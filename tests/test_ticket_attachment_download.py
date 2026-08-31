from __future__ import annotations

import asyncio
from io import BytesIO
from datetime import datetime
from types import SimpleNamespace
import uuid
from urllib.error import HTTPError

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


def test_download_local_document_uses_attachment_disposition_and_original_filename(tmp_path):
    session = _session()
    ticket, attachment = _seed_attachment(session, tmp_path, filename="report.pdf")

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


def test_download_large_telegram_file_uses_streaming_response_without_loading_body(monkeypatch, tmp_path):
    session = _session()
    ticket, attachment = _seed_attachment(
        session,
        tmp_path,
        filename="large.pdf",
        local_exists=False,
        telegram_file_id="file-id",
        telegram_file_path="documents/large.pdf",
        file_size=8_600_000,
    )
    opened = []

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
