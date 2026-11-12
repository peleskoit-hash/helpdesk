from types import SimpleNamespace
from unittest.mock import Mock

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from api.app.routers import mass_notifications as notifications


SEND = "/api/v1/mass-notifications/send"
PUBLIC_ORIGIN = "https://desk.example.test"


@pytest.fixture
def broadcast(monkeypatch):
    monkeypatch.setattr(notifications.settings, "app_env", "prod")
    monkeypatch.setattr(notifications.settings, "app_public_base_url", PUBLIC_ORIGIN + ":443/desk")
    db = Mock()
    db.execute.return_value.all.return_value = [
        SimpleNamespace(external_chat_id="123", display_name="Client"),
    ]
    message, document, photo = Mock(), Mock(), Mock()
    monkeypatch.setattr(notifications, "send_message", message)
    monkeypatch.setattr(notifications, "send_document", document)
    monkeypatch.setattr(notifications, "send_photo", photo)
    app = FastAPI()
    app.include_router(notifications.router)
    app.dependency_overrides[notifications.get_db] = lambda: db
    app.dependency_overrides[notifications.get_current_user] = lambda: SimpleNamespace(role="admin")
    return SimpleNamespace(client=TestClient(app), db=db, message=message, document=document, photo=photo)


def test_public_origin_allows_json_behind_reverse_proxy(broadcast):
    response = broadcast.client.post(SEND, json={"text": "Notice"}, headers={"Origin": PUBLIC_ORIGIN})

    assert response.status_code == 200
    assert response.json() == {"ok": True, "total": 1, "sent": 1, "failed": 0, "failed_recipients": []}
    broadcast.message.assert_called_once_with("123", "Notice")


def test_public_origin_allows_multipart_and_files(broadcast):
    response = broadcast.client.post(
        SEND,
        data={"text": "Notice"},
        files=[
            ("files", ("notice.txt", b"Document contents", "text/plain")),
            ("files", ("notice.png", b"Image contents", "image/png")),
        ],
        headers={"Origin": PUBLIC_ORIGIN},
    )

    assert response.status_code == 200
    assert response.json()["sent"] == 1
    broadcast.message.assert_called_once_with("123", "Notice")
    broadcast.document.assert_called_once_with(
        chat_id="123", file_path=b"Document contents", filename="notice.txt", mime_type="text/plain",
    )
    broadcast.photo.assert_called_once_with(
        chat_id="123", content=b"Image contents", filename="notice.png", mime_type="image/png",
    )


@pytest.mark.parametrize("origin", [None, "null", "https://other.example.test", PUBLIC_ORIGIN + "/path"])
def test_rejects_untrusted_origin_before_sending(broadcast, origin):
    headers = {
        "X-Forwarded-Host": "other.example.test",
        "X-Forwarded-Proto": "https",
    }
    if origin is not None:
        headers["Origin"] = origin
    response = broadcast.client.post(SEND, json={"text": "Notice"}, headers=headers)

    assert response.status_code == 403
    assert response.json()["detail"]["error"]["code"] == "invalid_origin"
    broadcast.db.execute.assert_not_called()
    broadcast.message.assert_not_called()
    broadcast.document.assert_not_called()
    broadcast.photo.assert_not_called()


@pytest.mark.parametrize(("mode", "public_url", "host", "status"), [
    ("dev", None, "localhost:8000", 200),
    ("dev", None, "other.example.test", 403),
    ("dev", "invalid-address", "localhost:8000", 403),
    ("prod", None, "localhost:8000", 403),
])
def test_only_local_dev_can_use_request_origin(broadcast, monkeypatch, mode, public_url, host, status):
    monkeypatch.setattr(notifications.settings, "app_env", mode)
    monkeypatch.setattr(notifications.settings, "app_public_base_url", public_url)
    response = broadcast.client.post(
        SEND, json={"text": "Notice"}, headers={"Host": host, "Origin": f"http://{host}"},
    )

    assert response.status_code == status
    assert broadcast.message.call_count == (1 if status == 200 else 0)
