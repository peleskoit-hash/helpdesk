from __future__ import annotations

import asyncio

from fastapi import FastAPI

from api.app.routers import telegram as telegram_router
from api.app.services import telegram_polling


async def _asgi_post(app: FastAPI, path: str) -> tuple[int, str]:
    messages = []
    request_sent = False

    async def receive():
        nonlocal request_sent
        if request_sent:
            return {"type": "http.disconnect"}
        request_sent = True
        return {"type": "http.request", "body": b"{}", "more_body": False}

    async def send(message):
        messages.append(message)

    await app(
        {
            "type": "http",
            "asgi": {"version": "3.0"},
            "http_version": "1.1",
            "method": "POST",
            "scheme": "http",
            "path": path,
            "raw_path": path.encode("ascii"),
            "query_string": b"",
            "headers": [
                (b"host", b"testserver"),
                (b"content-type", b"application/json"),
                (b"content-length", b"2"),
            ],
            "client": ("testclient", 50000),
            "server": ("testserver", 80),
        },
        receive,
        send,
    )
    status = next(message["status"] for message in messages if message["type"] == "http.response.start")
    body = b"".join(message.get("body", b"") for message in messages if message["type"] == "http.response.body")
    return status, body.decode("utf-8")


def test_telegram_webhook_route_is_removed():
    app = FastAPI()
    app.include_router(telegram_router.router)

    registered_paths = {route.path for route in app.router.routes}

    assert "/api/v1/messenger/webhook/telegram" not in registered_paths

    status_code, _ = asyncio.run(_asgi_post(app, "/api/v1/messenger/webhook/telegram"))

    assert status_code == 404


class _SessionContext:
    def __init__(self, session):
        self._session = session

    def __enter__(self):
        return self._session

    def __exit__(self, exc_type, exc, tb):
        return False


def test_process_update_sync_forwards_update_to_process_telegram_update(monkeypatch):
    expected_session = object()
    update = {"update_id": 101, "message": {"text": "/start"}}
    captured_calls: list[tuple[object, dict, bool]] = []
    expected_result = {"status": "ok", "external_chat_id": "42"}

    def fake_session_local():
        return _SessionContext(expected_session)

    def fake_process_telegram_update(db, payload, notify_client=False):
        captured_calls.append((db, payload, notify_client))
        return expected_result

    monkeypatch.setattr(telegram_polling, "SessionLocal", fake_session_local)
    monkeypatch.setattr(telegram_router, "process_telegram_update", fake_process_telegram_update)

    result = telegram_polling._process_update_sync(update)

    assert captured_calls == [(expected_session, update, True)]
    assert result is expected_result
