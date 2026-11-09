from __future__ import annotations

import asyncio
from io import BytesIO
from datetime import datetime
from types import SimpleNamespace
import uuid
from urllib.error import HTTPError, URLError

import pytest
from fastapi import HTTPException
from fastapi.responses import FileResponse, StreamingResponse
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from api.app.db import Base
from api.app.models import MessageType, Ticket, TicketAttachment, TicketMessage, TicketStatus, TicketWorkflowType
from api.app.routers import tickets as tickets_router


def _session():
    engine = create_engine(
        "sqlite://",
        future=True,
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine, autoflush=False, autocommit=False)
    return Session()


def _seed_attachment(session, tmp_path, *, filename="report.pdf", local_exists=True, telegram_file_id=None, telegram_file_path=None, file_size=3):
    now = datetime.utcnow()
    ticket = Ticket(
        id=str(uuid.uuid4()),
        company_id=str(uuid.uuid4()),
        client_id=str(uuid.uuid4()),
        subject="Need help",
        status=TicketStatus.PENDING.value,
        workflow_type=TicketWorkflowType.TICKET.value,
        created_at=now,
        updated_at=now,
    )
    message = TicketMessage(
        id=str(uuid.uuid4()),
        ticket_id=ticket.id,
        message_type=MessageType.CLIENT.value,
        body="file",
        created_at=now,
    )
    path = tmp_path / filename
    if local_exists:
        path.write_bytes(b"pdf")
    attachment = TicketAttachment(
        id=str(uuid.uuid4()),
        ticket_id=ticket.id,
        message_id=message.id,
        source="document",
        telegram_file_id=telegram_file_id,
        telegram_file_path=telegram_file_path,
        original_filename=filename,
        mime_type="application/pdf",
        file_size=file_size,
        local_path=str(path),
        created_at=now,
    )
    session.add_all([ticket, message, attachment])
    session.commit()
    return ticket, attachment


def test_download_local_document_uses_attachment_disposition_and_original_filename(tmp_path, monkeypatch):
    session = _session()
    ticket, attachment = _seed_attachment(session, tmp_path, filename="report.pdf")

    monkeypatch.setattr(tickets_router, "get_file", lambda *args, **kwargs: pytest.fail("Local download must not call getFile"))
    monkeypatch.setattr(tickets_router, "urlopen", lambda *args, **kwargs: pytest.fail("Local download must not contact Telegram"))
    response = tickets_router.download_attachment(ticket.id, attachment.id, db=session, user=SimpleNamespace(id="admin-1", role="admin"))

    assert isinstance(response, FileResponse)
    assert response.headers["content-disposition"] == 'attachment; filename="report.pdf"; filename*=UTF-8\'\'report.pdf'


def test_download_cyrillic_filename_is_encoded_for_content_disposition(tmp_path):
    session = _session()
    ticket, attachment = _seed_attachment(session, tmp_path, filename="договор 1.pdf")

    response = tickets_router.download_attachment(ticket.id, attachment.id, db=session, user=SimpleNamespace(id="admin-1", role="admin"))

    disposition = response.headers["content-disposition"]
    assert disposition.startswith('attachment; filename="_ 1.pdf"; filename*=UTF-8\'\'')
    assert "%D0%B4%D0%BE%D0%B3%D0%BE%D0%B2%D0%BE%D1%80%201.pdf" in disposition


def test_download_missing_physical_file_without_telegram_metadata_returns_controlled_404(tmp_path):
    session = _session()
    ticket, attachment = _seed_attachment(session, tmp_path, local_exists=False)

    with pytest.raises(HTTPException) as exc:
        tickets_router.download_attachment(ticket.id, attachment.id, db=session, user=SimpleNamespace(id="admin-1", role="admin"))

    assert exc.value.status_code == 404
    assert exc.value.detail["error"]["code"] == "attachment_file_missing"


@pytest.mark.parametrize("channel", ["telegram", "web"])
def test_download_large_telegram_file_uses_streaming_response_without_loading_body(monkeypatch, tmp_path, channel):
    session = _session()
    ticket, attachment = _seed_attachment(
        session,
        tmp_path,
        filename="large.pdf",
        local_exists=False,
        telegram_file_id="file-id",
        telegram_file_path=None,
        file_size=8_600_000,
    )
    opened = []
    ticket.channel = channel
    session.commit()
    monkeypatch.setattr(tickets_router, "get_file", lambda file_id, **kwargs:
                        {"result": {"file_path": "documents/large.pdf"}})
    if channel == "web":
        monkeypatch.setattr(tickets_router, "_telegram_contact_for_client", lambda *args:
                            pytest.fail("WEB download must not look up the client's Telegram contact"))

    class _TelegramResponse:
        headers = {"Content-Length": "8600000"}

        def read(self, size):
            opened.append(size)
            return b""

        def close(self):
            opened.append("closed")

    monkeypatch.setattr(tickets_router.settings, "telegram_bot_token", "token")
    monkeypatch.setattr(tickets_router, "urlopen", lambda request, timeout: _TelegramResponse())

    response = tickets_router.download_attachment(ticket.id, attachment.id, db=session, user=SimpleNamespace(id="admin-1", role="admin"))

    assert isinstance(response, StreamingResponse)
    assert response.headers["content-length"] == "8600000"
    assert opened == []


@pytest.mark.parametrize(
    ("status_code", "expected_status", "expected_error"),
    [
        (404, 404, "attachment_file_missing"),
        (429, 502, "attachment_download_failed"),
        (500, 502, "attachment_download_failed"),
    ],
)
def test_download_telegram_http_errors_are_mapped_without_raw_500(monkeypatch, tmp_path, status_code, expected_status, expected_error):
    session = _session()
    ticket, attachment = _seed_attachment(
        session,
        tmp_path,
        local_exists=False,
        telegram_file_id="file-id",
        telegram_file_path="documents/report.pdf",
    )

    def _raise_http_error(_request, timeout):
        raise HTTPError(
            url="https://api.telegram.org/file/botSECRET/documents/report.pdf",
            code=status_code,
            msg="telegram error",
            hdrs={},
            fp=BytesIO(b'{"ok": false, "description": "secret body"}'),
        )

    monkeypatch.setattr(tickets_router.settings, "telegram_bot_token", "SECRET")
    monkeypatch.setattr(tickets_router, "urlopen", _raise_http_error)
    monkeypatch.setattr(tickets_router, "get_file", lambda *args, **kwargs: {"result": {"file_path": "documents/refreshed.pdf"}})

    with pytest.raises(HTTPException) as exc:
        tickets_router.download_attachment(ticket.id, attachment.id, db=session, user=SimpleNamespace(id="admin-1", role="admin"))

    assert exc.value.status_code == expected_status
    assert exc.value.detail["error"]["code"] == expected_error
    assert "SECRET" not in exc.value.detail["error"]["message"]
    assert "secret body" not in exc.value.detail["error"]["message"]


def test_download_telegram_stream_yields_chunks_and_closes_response(monkeypatch, tmp_path):
    session = _session()
    ticket, attachment = _seed_attachment(
        session,
        tmp_path,
        filename="stream.pdf",
        local_exists=False,
        telegram_file_id="file-id",
        telegram_file_path="documents/stream.pdf",
        file_size=9,
    )
    events = []

    class _TelegramResponse:
        headers = {"Content-Length": "9"}

        def __init__(self):
            self._chunks = [b"one", b"two", b"six", b""]

        def read(self, size):
            events.append(("read", size))
            return self._chunks.pop(0)

        def close(self):
            events.append(("close", None))

    async def _consume(response):
        chunks = []
        async for chunk in response.body_iterator:
            chunks.append(chunk)
        return chunks

    monkeypatch.setattr(tickets_router.settings, "telegram_bot_token", "token")
    monkeypatch.setattr(tickets_router, "urlopen", lambda request, timeout: _TelegramResponse())

    response = tickets_router.download_attachment(ticket.id, attachment.id, db=session, user=SimpleNamespace(id="admin-1", role="admin"))
    chunks = asyncio.run(_consume(response))

    assert chunks == [b"one", b"two", b"six"]
    assert events == [("read", 65536), ("read", 65536), ("read", 65536), ("read", 65536), ("close", None)]


@pytest.mark.parametrize("channel", ["telegram", "web"])
def test_stale_saved_path_refreshes_once_with_existing_bot_selection(monkeypatch, tmp_path, channel):
    from api.app.services import telegram_bot

    session = _session()
    ticket, attachment = _seed_attachment(session, tmp_path, filename="договор.pdf", local_exists=False,
        telegram_file_id="file-id", telegram_file_path="documents/old.pdf")
    ticket.channel = channel
    session.commit()
    urls, refreshes, bot_calls = [], [], []
    if channel == "telegram":
        monkeypatch.setattr(tickets_router, "_telegram_contact_for_client",
                            lambda *args: SimpleNamespace(external_chat_id="client-chat"))
    else:
        monkeypatch.setattr(tickets_router, "_telegram_contact_for_client",
                            lambda *args: pytest.fail("WEB must not look up Telegram contact"))
    def request(method, **kwargs):
        bot_calls.append((method, kwargs))
        return {"result": {"file_path": "documents/new.pdf"}}
    monkeypatch.setattr(telegram_bot, "telegram_request", request)
    def get_file(file_id, *, chat_id):
        refreshes.append((file_id, chat_id))
        return telegram_bot.get_file(file_id, chat_id=chat_id)
    monkeypatch.setattr(tickets_router, "get_file", get_file)
    body = BytesIO(b"fresh!")
    body.headers = {"Content-Length": "6"}
    def download(request, timeout):
        urls.append(request.full_url)
        if len(urls) == 1:
            raise HTTPError(request.full_url, 404, "stale", {}, BytesIO())
        return body
    monkeypatch.setattr(tickets_router.settings, "telegram_bot_token", "download-token")
    monkeypatch.setattr(tickets_router, "urlopen", download)
    response = tickets_router.download_attachment(ticket.id, attachment.id, db=session,
                                                  user=SimpleNamespace(id="admin", role="admin"))
    assert urls == ["https://api.telegram.org/file/botdownload-token/documents/old.pdf",
                    "https://api.telegram.org/file/botdownload-token/documents/new.pdf"]
    assert refreshes == [("file-id", "client-chat" if channel == "telegram" else None)]
    assert bot_calls == [("getFile", {"payload": {"file_id": "file-id"}})]
    assert attachment.telegram_file_path == "documents/new.pdf"
    assert response.headers["content-type"] == "application/pdf"
    assert response.headers["content-length"] == "6"
    assert response.headers["content-disposition"] == tickets_router._content_disposition_attachment("договор.pdf")
    async def consume():
        return b"".join([chunk async for chunk in response.body_iterator])
    assert asyncio.run(consume()) == b"fresh!"
    assert body.closed
    assert not (tmp_path / "договор.pdf").exists()


@pytest.mark.parametrize("failure,expected", [(404, 404), (500, 502), ("network", 502)])
def test_stale_path_download_retry_is_bounded_and_errors_are_mapped(monkeypatch, tmp_path, failure, expected):
    session = _session()
    ticket, attachment = _seed_attachment(session, tmp_path, local_exists=False,
        telegram_file_id="file-id", telegram_file_path="documents/old.pdf")
    opens, refreshes = [], []
    def download(request, timeout):
        opens.append(request.full_url)
        if len(opens) == 2 and failure == "network":
            raise URLError("connection failed")
        raise HTTPError(request.full_url, 404 if len(opens) == 1 else failure, "missing", {}, BytesIO())
    def refresh(*args, **kwargs):
        refreshes.append((args, kwargs))
        return {"result": {"file_path": "documents/new.pdf"}}
    monkeypatch.setattr(tickets_router.settings, "telegram_bot_token", "token")
    monkeypatch.setattr(tickets_router, "urlopen", download)
    monkeypatch.setattr(tickets_router, "get_file", refresh)
    with pytest.raises(HTTPException) as error:
        tickets_router.download_attachment(ticket.id, attachment.id, db=session, user=SimpleNamespace(id="admin", role="admin"))
    assert error.value.status_code == expected
    assert error.value.detail["error"]["code"] == ("attachment_file_missing" if expected == 404 else "attachment_download_failed")
    assert len(opens) == 2 and len(refreshes) == 1


@pytest.mark.parametrize("result", ["api_error", "empty_path"])
def test_stale_path_refresh_failure_is_controlled(monkeypatch, tmp_path, result):
    session = _session()
    ticket, attachment = _seed_attachment(session, tmp_path, local_exists=False,
        telegram_file_id="file-id", telegram_file_path="documents/old.pdf")
    opens = []
    def download(request, timeout):
        opens.append(request.full_url)
        raise HTTPError(request.full_url, 404, "missing", {}, BytesIO())
    def refresh(*args, **kwargs):
        if result == "api_error":
            raise tickets_router.TelegramApiError("File unavailable")
        return {"result": {}}
    monkeypatch.setattr(tickets_router.settings, "telegram_bot_token", "token")
    monkeypatch.setattr(tickets_router, "urlopen", download)
    monkeypatch.setattr(tickets_router, "get_file", refresh)
    with pytest.raises(HTTPException) as error:
        tickets_router.download_attachment(ticket.id, attachment.id, db=session, user=SimpleNamespace(id="admin", role="admin"))
    assert error.value.status_code == 404
    assert error.value.detail["error"]["code"] == "attachment_file_missing"
    assert len(opens) == 1
