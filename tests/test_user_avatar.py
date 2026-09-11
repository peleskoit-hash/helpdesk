from __future__ import annotations

from datetime import datetime
from pathlib import Path
from types import SimpleNamespace
import uuid

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from pydantic import ValidationError
from sqlalchemy import create_engine
from sqlalchemy.pool import StaticPool
from sqlalchemy.orm import sessionmaker

from api.app.auth import hash_password
from api.app.db import Base
from api.app.models import Company, User
from api.app.routers import users as users_router
from api.app.schemas import SettingsUserUpdate


JPEG_BYTES = b"\xff\xd8\xffavatar-jpeg"
PNG_BYTES = b"\x89PNG\r\n\x1a\navatar-png"
WEBP_BYTES = b"RIFF\x08\x00\x00\x00WEBPavatar-webp"


@pytest.fixture
def session():
    engine = create_engine(
        "sqlite://",
        future=True,
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine, autoflush=False, autocommit=False)
    db = Session()
    try:
        yield db
    finally:
        db.close()


@pytest.fixture
def avatar_storage(monkeypatch, tmp_path):
    storage_dir = tmp_path / "storage" / "avatars"
    monkeypatch.setattr("api.app.services.user_avatars.settings.avatar_storage_dir", str(storage_dir))
    return storage_dir


def _company(session) -> Company:
    company = Company(id=str(uuid.uuid4()), name="ACME", is_active=True, created_at=datetime.utcnow())
    session.add(company)
    session.commit()
    return company


def _user(
    session,
    *,
    company_id: str,
    full_name: str,
    login: str,
    role: str = "agent",
    avatar_path: str | None = None,
) -> User:
    now = datetime.utcnow()
    user = User(
        id=str(uuid.uuid4()),
        company_id=company_id,
        email=f"{login}@local.helpdesk",
        login=login,
        password_hash=hash_password("secret"),
        avatar_path=avatar_path,
        full_name=full_name,
        role=role,
        is_active=True,
        availability_status="online",
        created_at=now,
        updated_at=now,
    )
    session.add(user)
    session.commit()
    return user


def _current_user(user: User, *, real_admin: bool | None = None):
    is_admin = user.role == "admin" if real_admin is None else real_admin
    real_role = "admin" if is_admin else user.role
    return SimpleNamespace(
        id=user.id,
        role=user.role,
        real_user_id=user.id,
        real_role=real_role,
        is_real_admin=is_admin,
    )


def _client(session, current_user) -> TestClient:
    app = FastAPI()
    app.include_router(users_router.router)
    app.dependency_overrides[users_router.get_db] = lambda: session
    app.dependency_overrides[users_router.get_current_user] = lambda: current_user
    return TestClient(app)


def _stored_file(storage_dir: Path, relative_path: str) -> Path:
    return storage_dir.parent / relative_path


def test_upload_jpeg_avatar_for_self_stores_relative_path_and_file(session, avatar_storage):
    company = _company(session)
    current = _user(session, company_id=company.id, full_name="Ivan Petrov", login="ivan")

    response = _client(session, _current_user(current)).put(
        "/api/v1/users/me/avatar",
        files={"avatar": ("avatar.jpg", JPEG_BYTES, "image/jpeg")},
    )

    session.refresh(current)
    assert response.status_code == 200
    assert response.json() == {
        "ok": True,
        "avatar_present": True,
        "avatar_url": f"/api/v1/users/{current.id}/avatar",
    }
    assert current.avatar_path is not None
    assert current.avatar_path.startswith("avatars/")
    assert not Path(current.avatar_path).is_absolute()
    assert _stored_file(avatar_storage, current.avatar_path).read_bytes() == JPEG_BYTES


def test_upload_png_avatar_for_self(session, avatar_storage):
    company = _company(session)
    current = _user(session, company_id=company.id, full_name="Ivan Petrov", login="ivan")

    response = _client(session, _current_user(current)).put(
        "/api/v1/users/me/avatar",
        files={"avatar": ("avatar.png", PNG_BYTES, "image/png")},
    )

    session.refresh(current)
    assert response.status_code == 200
    assert current.avatar_path.endswith(".png")
    assert _stored_file(avatar_storage, current.avatar_path).read_bytes() == PNG_BYTES


def test_upload_webp_avatar_for_self(session, avatar_storage):
    company = _company(session)
    current = _user(session, company_id=company.id, full_name="Ivan Petrov", login="ivan")

    response = _client(session, _current_user(current)).put(
        "/api/v1/users/me/avatar",
        files={"avatar": ("avatar.webp", WEBP_BYTES, "image/webp")},
    )

    session.refresh(current)
    assert response.status_code == 200
    assert current.avatar_path.endswith(".webp")
    assert _stored_file(avatar_storage, current.avatar_path).read_bytes() == WEBP_BYTES


def test_upload_rejects_invalid_bytes_despite_png_content_type(session, avatar_storage):
    company = _company(session)
    current = _user(session, company_id=company.id, full_name="Ivan Petrov", login="ivan")

    response = _client(session, _current_user(current)).put(
        "/api/v1/users/me/avatar",
        files={"avatar": ("avatar.png", b"not-an-image", "image/png")},
    )

    session.refresh(current)
    assert response.status_code == 400
    assert response.json()["detail"]["error"]["code"] == "avatar_invalid_image"
    assert current.avatar_path is None
    assert not avatar_storage.exists()


def test_upload_rejects_mismatched_content_type_and_bytes(session, avatar_storage):
    company = _company(session)
    current = _user(session, company_id=company.id, full_name="Ivan Petrov", login="ivan")

    response = _client(session, _current_user(current)).put(
        "/api/v1/users/me/avatar",
        files={"avatar": ("avatar.png", JPEG_BYTES, "image/png")},
    )

    session.refresh(current)
    assert response.status_code == 400
    assert response.json()["detail"]["error"]["code"] == "avatar_invalid_image"
    assert current.avatar_path is None


def test_upload_rejects_unsupported_content_type(session, avatar_storage):
    company = _company(session)
    current = _user(session, company_id=company.id, full_name="Ivan Petrov", login="ivan")

    response = _client(session, _current_user(current)).put(
        "/api/v1/users/me/avatar",
        files={"avatar": ("avatar.gif", JPEG_BYTES, "image/gif")},
    )

    session.refresh(current)
    assert response.status_code == 400
    assert response.json()["detail"]["error"]["code"] == "avatar_unsupported_type"
    assert current.avatar_path is None


def test_upload_rejects_avatar_over_five_megabytes(session, avatar_storage):
    company = _company(session)
    current = _user(session, company_id=company.id, full_name="Ivan Petrov", login="ivan")
    oversized = b"\xff\xd8\xff" + b"x" * (5 * 1024 * 1024)

    response = _client(session, _current_user(current)).put(
        "/api/v1/users/me/avatar",
        files={"avatar": ("avatar.jpg", oversized, "image/jpeg")},
    )

    session.refresh(current)
    assert response.status_code == 413
    assert response.json()["detail"]["error"]["code"] == "avatar_too_large"
    assert current.avatar_path is None


def test_replace_avatar_deletes_old_file_after_successful_commit(session, avatar_storage):
    company = _company(session)
    current = _user(session, company_id=company.id, full_name="Ivan Petrov", login="ivan")
    client = _client(session, _current_user(current))
    first = client.put("/api/v1/users/me/avatar", files={"avatar": ("a.jpg", JPEG_BYTES, "image/jpeg")})
    session.refresh(current)
    old_path = current.avatar_path

    second = client.put("/api/v1/users/me/avatar", files={"avatar": ("b.png", PNG_BYTES, "image/png")})

    session.refresh(current)
    assert first.status_code == 200
    assert second.status_code == 200
    assert old_path != current.avatar_path
    assert not _stored_file(avatar_storage, old_path).exists()
    assert _stored_file(avatar_storage, current.avatar_path).read_bytes() == PNG_BYTES


def test_delete_avatar_clears_db_path_and_removes_file(session, avatar_storage):
    company = _company(session)
    current = _user(session, company_id=company.id, full_name="Ivan Petrov", login="ivan")
    client = _client(session, _current_user(current))
    client.put("/api/v1/users/me/avatar", files={"avatar": ("a.jpg", JPEG_BYTES, "image/jpeg")})
    session.refresh(current)
    old_path = current.avatar_path

    response = client.delete("/api/v1/users/me/avatar")

    session.refresh(current)
    assert response.status_code == 200
    assert response.json() == {"ok": True, "avatar_present": False, "avatar_url": None}
    assert current.avatar_path is None
    assert not _stored_file(avatar_storage, old_path).exists()


def test_delete_absent_avatar_is_idempotent_success(session, avatar_storage):
    company = _company(session)
    current = _user(session, company_id=company.id, full_name="Ivan Petrov", login="ivan")

    response = _client(session, _current_user(current)).delete("/api/v1/users/me/avatar")

    session.refresh(current)
    assert response.status_code == 200
    assert response.json() == {"ok": True, "avatar_present": False, "avatar_url": None}
    assert current.avatar_path is None


def test_get_own_avatar_returns_bytes_and_content_type(session, avatar_storage):
    company = _company(session)
    current = _user(session, company_id=company.id, full_name="Ivan Petrov", login="ivan")
    client = _client(session, _current_user(current))
    client.put("/api/v1/users/me/avatar", files={"avatar": ("a.jpg", JPEG_BYTES, "image/jpeg")})

    response = client.get(f"/api/v1/users/{current.id}/avatar")

    assert response.status_code == 200
    assert response.content == JPEG_BYTES
    assert response.headers["content-type"].startswith("image/jpeg")


def test_normal_user_cannot_get_other_user_avatar(session, avatar_storage):
    company = _company(session)
    current = _user(session, company_id=company.id, full_name="Ivan Petrov", login="ivan")
    other = _user(session, company_id=company.id, full_name="Other User", login="other")
    avatar_storage.mkdir(parents=True, exist_ok=True)
    other.avatar_path = "avatars/other.jpg"
    _stored_file(avatar_storage, other.avatar_path).write_bytes(JPEG_BYTES)
    session.commit()

    response = _client(session, _current_user(current)).get(f"/api/v1/users/{other.id}/avatar")

    assert response.status_code == 403
    assert response.json()["detail"]["error"]["code"] == "forbidden"


def test_real_admin_can_get_other_user_avatar(session, avatar_storage):
    company = _company(session)
    admin = _user(session, company_id=company.id, full_name="Admin User", login="admin", role="admin")
    other = _user(session, company_id=company.id, full_name="Other User", login="other")
    avatar_storage.mkdir(parents=True, exist_ok=True)
    other.avatar_path = "avatars/other.png"
    _stored_file(avatar_storage, other.avatar_path).write_bytes(PNG_BYTES)
    session.commit()
    admin_effective_agent = SimpleNamespace(
        id=admin.id,
        role="agent",
        real_user_id=admin.id,
        real_role="admin",
        is_real_admin=True,
    )

    response = _client(session, admin_effective_agent).get(f"/api/v1/users/{other.id}/avatar")

    assert response.status_code == 200
    assert response.content == PNG_BYTES
    assert response.headers["content-type"].startswith("image/png")


def test_get_missing_avatar_returns_avatar_not_found(session, avatar_storage):
    company = _company(session)
    current = _user(session, company_id=company.id, full_name="Ivan Petrov", login="ivan")
    client = _client(session, _current_user(current))

    absent_response = client.get(f"/api/v1/users/{current.id}/avatar")
    current.avatar_path = "avatars/missing.jpg"
    session.commit()
    missing_file_response = client.get(f"/api/v1/users/{current.id}/avatar")

    assert absent_response.status_code == 404
    assert absent_response.json()["detail"]["error"]["code"] == "avatar_not_found"
    assert missing_file_response.status_code == 404
    assert missing_file_response.json()["detail"]["error"]["code"] == "avatar_not_found"


def test_traversal_avatar_path_does_not_read_outside_avatar_storage(session, avatar_storage, tmp_path):
    company = _company(session)
    current = _user(session, company_id=company.id, full_name="Ivan Petrov", login="ivan")
    secret = tmp_path / "storage" / "secret.jpg"
    secret.parent.mkdir(parents=True, exist_ok=True)
    secret.write_bytes(JPEG_BYTES)
    current.avatar_path = "../secret.jpg"
    session.commit()

    response = _client(session, _current_user(current)).get(f"/api/v1/users/{current.id}/avatar")

    assert response.status_code == 404
    assert response.json()["detail"]["error"]["code"] == "avatar_not_found"
    assert response.content != JPEG_BYTES


def test_no_put_or_delete_routes_for_other_user_avatar(session, avatar_storage):
    company = _company(session)
    current = _user(session, company_id=company.id, full_name="Ivan Petrov", login="ivan")
    other = _user(session, company_id=company.id, full_name="Other User", login="other")
    client = _client(session, _current_user(current))

    put_response = client.put(
        f"/api/v1/users/{other.id}/avatar",
        files={"avatar": ("avatar.jpg", JPEG_BYTES, "image/jpeg")},
    )
    delete_response = client.delete(f"/api/v1/users/{other.id}/avatar")

    session.refresh(other)
    assert put_response.status_code == 405
    assert delete_response.status_code == 405
    assert other.avatar_path is None


def test_self_profile_avatar_url_is_null_or_user_avatar_endpoint(session, avatar_storage):
    company = _company(session)
    current = _user(session, company_id=company.id, full_name="Ivan Petrov", login="ivan")
    client = _client(session, _current_user(current))

    empty_response = client.get("/api/v1/users/me/profile")
    current.avatar_path = "avatars/ivan.webp"
    session.commit()
    avatar_response = client.get("/api/v1/users/me/profile")

    assert empty_response.status_code == 200
    assert empty_response.json()["avatar_url"] is None
    assert empty_response.json()["avatar_present"] is False
    assert avatar_response.status_code == 200
    assert avatar_response.json()["avatar_url"] == f"/api/v1/users/{current.id}/avatar"
    assert avatar_response.json()["avatar_present"] is True


def test_settings_user_update_still_forbids_avatar_path():
    with pytest.raises(ValidationError):
        SettingsUserUpdate(avatar_path="avatars/other.webp")
