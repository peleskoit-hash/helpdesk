from io import BytesIO
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

from fastapi import FastAPI
from fastapi.testclient import TestClient
from PIL import Image
import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool
from starlette.middleware.sessions import SessionMiddleware

from api.app.auth import get_current_user
from api.app.db import Base, get_db
from api.app.models import Client, ClientProfile, Company, TelegramContact, Ticket
from api.app.routers import ticket_client_avatars as router
from api.app.routers.tickets import _current_client_profile_out
from api.app.routers import tickets as tickets_router
from api.app.services import telegram_client_avatars as storage
from api.app.services import ticket_client_avatars as service


@pytest.mark.parametrize("thumbnail", [b"saved-thumbnail", None])
def test_ticket_card_and_client_profile_agree_on_telegram_avatar_presence(thumbnail):
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(engine)
    with Session(engine) as db:
        db.add_all([
            Company(id="company", name="Company"),
            Client(id="client", company_id="company", display_name="Client"),
            ClientProfile(id="profile", client_id="client"),
            Ticket(id="ticket", ticket_number=706, company_id="company", client_id="client",
                   subject="Test", status="closed"),
            TelegramContact(id="contact", company_id="company", client_id="client",
                            external_user_id="123", external_chat_id="123", avatar_thumbnail=thumbnail,
                            avatar_updated_at=datetime(2026, 10, 17, 12, 0)),
        ])
        db.commit()
        app = FastAPI()
        app.include_router(tickets_router.router)
        app.dependency_overrides[get_db] = lambda: db
        app.dependency_overrides[get_current_user] = lambda: SimpleNamespace(id="admin", role="admin")
        with TestClient(app) as client:
            card = client.get("/api/v1/tickets/706")
            profile = client.get("/api/v1/tickets/706/client-profile")
        assert card.status_code == profile.status_code == 200
        assert card.json()["client_profile"]["telegram_avatar_present"] == profile.json()["telegram_avatar_present"] == bool(thumbnail)
        expected_version = "2026-10-17T12:00:00" if thumbnail else None
        assert card.json()["client_profile"]["telegram_avatar_updated_at"] == profile.json()["telegram_avatar_updated_at"] == expected_version
    engine.dispose()


@pytest.fixture
def case(monkeypatch, tmp_path):
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(engine)
    directory = tmp_path / "telegram_client_avatars"
    monkeypatch.setattr(storage.settings, "telegram_client_avatar_storage_dir", str(directory))
    monkeypatch.setattr(service.settings, "telegram_bot_token", "client-test-token")
    with Session(engine) as db:
        db.add(Company(id="company", name="Company"))
        db.add(Client(id="client", company_id="company", display_name="Client"))
        db.add(ClientProfile(id="profile", client_id="client"))
        ticket = Ticket(id="ticket", ticket_number=1, company_id="company", client_id="client",
                        subject="Test", status="pending", assignee_id="agent")
        contact = TelegramContact(id="contact", company_id="company", client_id="client",
                                  external_user_id="123", external_chat_id="123")
        db.add_all([ticket, contact])
        db.commit()
        output = BytesIO()
        Image.new("RGB", (600, 400), "red").save(output, format="JPEG")
        content = output.getvalue()
        storage.save_avatar(db, contact.id, "photo-unique", content)
        app = FastAPI()
        app.add_middleware(SessionMiddleware, secret_key="test-secret")
        app.include_router(router.router)
        app.dependency_overrides[get_db] = lambda: db
        app.dependency_overrides[get_current_user] = lambda: SimpleNamespace(id="admin", role="admin")
        result = Mock(side_effect=lambda method, *args: {
            "total_count": 1, "photos": [[{"width": 100, "height": 100, "file_id": "small", "file_unique_id": "small"},
                                         {"width": 600, "height": 400, "file_id": "large", "file_unique_id": "photo-unique"}]]
        } if method == "getUserProfilePhotos" else {"file_path": "photos/photo.jpg"})
        download = Mock(return_value=content)
        monkeypatch.setattr(service, "_result", result)
        monkeypatch.setattr(service, "_download", download)
        yield SimpleNamespace(db=db, client=TestClient(app), app=app, directory=directory,
                              contact=contact, ticket=ticket, content=content, result=result, download=download)
    engine.dispose()


def _snapshot(case):
    case.db.refresh(case.contact)
    c = case.contact
    return c.avatar_thumbnail, c.avatar_file_unique_id, c.avatar_path, c.avatar_updated_at


def test_thumbnail_and_profile_presence_do_not_request_telegram(case):
    before = _snapshot(case)
    response = case.client.get("/api/v1/tickets/ticket/client-avatar/thumbnail")
    assert response.status_code == 200
    assert response.content == before[0]
    assert response.headers["content-type"] == "image/webp"
    assert response.headers["cache-control"] == "private, no-cache"
    assert response.headers["x-content-type-options"] == "nosniff"
    assert _current_client_profile_out(case.db.get(ClientProfile, "profile"), case.ticket, case.contact).telegram_avatar_present
    cached = case.client.get("/api/v1/tickets/1/client-avatar/thumbnail", headers={"If-None-Match": response.headers["etag"]})
    assert cached.status_code == 304
    case.result.assert_not_called()
    assert _snapshot(case) == before


@pytest.mark.parametrize("status", ["pending", "closed"])
def test_saved_full_photo_never_requests_telegram(case, status):
    case.ticket.status = status
    case.db.commit()
    response = case.client.get("/api/v1/tickets/ticket/client-avatar/full")
    assert response.status_code == 200
    assert response.content == case.content
    assert response.headers["content-type"] == "image/jpeg"
    assert response.headers["cache-control"] == "private, no-store"
    case.result.assert_not_called()
    case.download.assert_not_called()


@pytest.mark.parametrize("status", ["pending", "closed"])
@pytest.mark.parametrize("missing", ["no_path", "missing_file"])
def test_matching_temporary_photo_is_never_saved(case, status, missing):
    case.ticket.status = status
    case.db.commit()
    if missing == "no_path":
        storage.delete_full_avatar(case.db, case.contact.id)
    else:
        (case.directory / Path(case.contact.avatar_path).name).unlink()
    before = _snapshot(case)
    response = case.client.get("/api/v1/tickets/ticket/client-avatar/full")
    assert response.status_code == 200 and response.content == case.content
    assert [call.args[0] for call in case.result.call_args_list] == ["getUserProfilePhotos", "getFile"]
    assert case.result.call_args_list[1].args[1] == {"file_id": "large"}
    assert _snapshot(case) == before
    assert list(case.directory.iterdir()) == []


@pytest.mark.parametrize("failure", ["changed", "absent", "timeout", "invalid", "oversized"])
def test_temporary_unavailable_photo_preserves_original_thumbnail(case, failure, monkeypatch):
    storage.delete_full_avatar(case.db, case.contact.id)
    before = _snapshot(case)
    if failure == "changed":
        case.result.side_effect = lambda *args: {"total_count": 1, "photos": [[{
            "width": 600, "height": 400, "file_id": "new", "file_unique_id": "changed"}]]}
    elif failure == "absent":
        case.result.side_effect = lambda *args: {"total_count": 0, "photos": []}
    elif failure == "timeout":
        case.result.side_effect = TimeoutError("client-test-token private info")
    elif failure == "invalid":
        case.download.return_value = b"not an image"
    else:
        monkeypatch.setattr(service, "MAX_IMAGE_BYTES", 1)
        case.result.side_effect = lambda *args: {"total_count": 1, "photos": [[{
            "width": 600, "height": 400, "file_id": "large", "file_unique_id": "photo-unique", "file_size": 2}]]}
    response = case.client.get("/api/v1/tickets/ticket/client-avatar/full")
    assert response.status_code == 404
    assert response.json() == {"detail": "Изображение недоступно"}
    if failure in {"changed", "absent", "oversized"}:
        case.download.assert_not_called()
        assert case.result.call_count == 1
    assert _snapshot(case) == before
    assert list(case.directory.iterdir()) == []


@pytest.mark.parametrize("endpoint", ["thumbnail", "full"])
def test_no_thumbnail_returns_404_without_backfill(case, endpoint):
    case.contact.avatar_thumbnail = None
    case.db.commit()
    response = case.client.get(f"/api/v1/tickets/ticket/client-avatar/{endpoint}")
    assert response.status_code == 404
    case.result.assert_not_called()
    assert not _current_client_profile_out(case.db.get(ClientProfile, "profile"), case.ticket, case.contact).telegram_avatar_present


@pytest.mark.parametrize("endpoint", ["thumbnail", "full"])
@pytest.mark.parametrize("access", ["unauthorized", "forbidden", "missing_ticket"])
def test_endpoints_require_ticket_access_before_loading_any_photo(case, endpoint, access, monkeypatch):
    if access == "unauthorized":
        case.app.dependency_overrides.pop(get_current_user)
        expected = 401
    elif access == "forbidden":
        case.app.dependency_overrides[get_current_user] = lambda: SimpleNamespace(id="other", role="agent")
        expected = 403
    else:
        expected = 404
    protected = Mock(side_effect=AssertionError("Image accessed without ticket permission"))
    monkeypatch.setattr(router, "contact_for_ticket", protected)
    ticket = "missing" if access == "missing_ticket" else "ticket"
    response = case.client.get(f"/api/v1/tickets/{ticket}/client-avatar/{endpoint}")
    assert response.status_code == expected
    protected.assert_not_called()
    case.result.assert_not_called()


def test_cached_thumbnail_still_requires_ticket_access(case):
    response = case.client.get("/api/v1/tickets/ticket/client-avatar/thumbnail")
    case.app.dependency_overrides[get_current_user] = lambda: SimpleNamespace(id="other", role="agent")
    response = case.client.get("/api/v1/tickets/ticket/client-avatar/thumbnail",
                               headers={"If-None-Match": response.headers["etag"]})
    assert response.status_code == 403


def test_full_photo_cannot_read_employee_avatar_path(case):
    employee = case.directory.parent / "avatars" / ("a" * 32 + ".jpg")
    employee.parent.mkdir()
    employee.write_bytes(b"employee-private-image")
    case.contact.avatar_path = f"avatars/{employee.name}"
    case.db.commit()
    case.result.side_effect = lambda *args: {"total_count": 0, "photos": []}
    response = case.client.get("/api/v1/tickets/ticket/client-avatar/full")
    assert response.status_code == 404
    assert employee.read_bytes() == b"employee-private-image"
    assert "avatars/" not in response.text
