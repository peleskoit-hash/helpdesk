import asyncio
from types import SimpleNamespace
import uuid

import pytest
from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, inspect, select, text

from api.app.auth import get_current_user
from api.app.db import get_db
from api.app.models import MessageType, Ticket, TicketAttachment, TicketAttachmentPreload, TicketMessage
from api.app.routers import tickets
from api.app.services import schema_evolution, telegram_outbound_delivery
from api.app.services.ticket_messages import reply_to_ticket
from test_ticket_messages_outbound import (
    _FormData, _JsonRequest, _MultipartRequest, _save_upload_attachment_factory,
    _seed_ticket, _session_bundle, _upload,
)


@pytest.fixture
def case(tmp_path, monkeypatch):
    engine, _, db = _session_bundle()
    ticket = _seed_ticket(db)
    ticket.assignee_id = "agent-1"
    db.commit()
    calls = []
    scheduled = []

    def send(*args, **kwargs):
        calls.append(kwargs)
        return {"result": {"message_id": 901}}

    def claim(session, preload_id, ticket_id, user_id):
        preload = session.get(TicketAttachmentPreload, preload_id)
        assert (preload.ticket_id, preload.user_id) == (ticket_id, user_id)
        return preload

    monkeypatch.setattr(telegram_outbound_delivery, "_send_staging_document_from_path",
                        lambda **kwargs: ("file-id", "unique-id", "archive-id"))
    kwargs = dict(db=db, ticket=ticket, user=SimpleNamespace(id="agent-1", role="agent"),
        send_to_telegram_client=send,
        telegram_message_id_from_response=lambda response: str(response["result"]["message_id"]),
        save_upload_attachment=_save_upload_attachment_factory(tmp_path),
        send_attachment_to_telegram_client=lambda *args, **kwargs: None,
        claim_attachment_preload=claim,
        schedule_attachment_delivery=lambda **kwargs: scheduled.append(kwargs),
        unfreeze_ticket_on_message=lambda *args: None,
        remove_client_close_button=lambda *args: None,
        refresh_ticket_auto_close=lambda *args: None)
    try:
        yield SimpleNamespace(db=db, ticket=ticket, calls=calls, scheduled=scheduled, kwargs=kwargs)
    finally:
        db.close()
        engine.dispose()


def source_message(case, *, kind="client", ticket=None, telegram_id="701"):
    message = TicketMessage(id=str(uuid.uuid4()), ticket_id=(ticket or case.ticket).id,
        message_type=kind, body="Quoted original", telegram_message_id=telegram_id)
    case.db.add(message)
    case.db.commit()
    return message


@pytest.mark.parametrize("channel", ["telegram", "web"])
@pytest.mark.parametrize("workflow", ["ticket", "task"])
@pytest.mark.parametrize("transport", ["text", "raw", "ready", "uploading"])
def test_quote_persists_in_all_reply_branches(case, tmp_path, channel, workflow, transport):
    case.ticket.channel = channel
    case.ticket.workflow_type = workflow
    source = source_message(case)
    source_id = source.id
    if transport == "text":
        request = _JsonRequest({"text": "Answer", "quote_message_id": source_id})
    else:
        files, preload_ids = [], []
        if transport == "raw":
            files = [_upload("answer.pdf", b"pdf")]
        else:
            path = tmp_path / "answer.pdf"
            path.write_bytes(b"pdf")
            preload = TicketAttachmentPreload(id=str(uuid.uuid4()), ticket_id=case.ticket.id,
                user_id="agent-1", status=transport, local_path=str(path), original_filename="answer.pdf",
                telegram_file_id="file-id" if transport == "ready" else None)
            case.db.add(preload)
            case.db.commit()
            preload_ids = [preload.id]
        request = _MultipartRequest(_FormData(text="Answer", files=files,
            quote_message_id=source_id, preloaded_attachment_ids=preload_ids))
    assert asyncio.run(reply_to_ticket(request=request, **case.kwargs)) == {"ok": True}
    case.db.expunge_all()
    reply = case.db.execute(select(TicketMessage).where(TicketMessage.message_type == "agent")).scalar_one()
    assert reply.quote_message_id == source_id
    assert reply.quote_message_id != "701"
    assert case.db.get(TicketMessage, source_id).quote_message_id is None
    assert case.db.query(TicketAttachment).filter_by(message_id=reply.id).count() == (transport != "text")
    if channel == "telegram" and workflow == "ticket" and transport == "text":
        assert case.calls == [{"reply_to_message_id": "701"}]
    else:
        assert case.calls == []
    for delivery in case.scheduled:
        assert delivery["reply_to_message_id"] == "701"
    if workflow == "ticket" and transport in {"ready", "uploading"}:
        assert case.db.get(TicketAttachmentPreload, preload_ids[0]).reply_to_message_id == "701"


@pytest.mark.parametrize("transport", ["text", "raw"])
@pytest.mark.parametrize("quote_kind", ["none", "missing", "other_ticket", "internal", "local_agent"])
def test_quote_validation_and_null_compatibility(case, transport, quote_kind):
    quote_id = None
    if quote_kind == "missing":
        quote_id = str(uuid.uuid4())
    elif quote_kind == "other_ticket":
        other_ticket = Ticket(id=str(uuid.uuid4()), company_id=case.ticket.company_id,
                              client_id=case.ticket.client_id, subject="Other ticket")
        case.db.add(other_ticket)
        case.db.commit()
        quote_id = source_message(case, ticket=other_ticket).id
    elif quote_kind in {"internal", "local_agent"}:
        quote_id = source_message(case, kind="internal" if quote_kind == "internal" else "agent",
                                  telegram_id=None).id
    request = (_JsonRequest({"text": "Answer", "quote_message_id": quote_id}) if transport == "text"
               else _MultipartRequest(_FormData(text="Answer", files=[_upload("answer.pdf", b"pdf")],
                                                quote_message_id=quote_id)))
    if quote_kind == "internal":
        with pytest.raises(HTTPException) as exc:
            asyncio.run(reply_to_ticket(request=request, **case.kwargs))
        assert exc.value.detail["error"]["code"] == "internal_quote_forbidden"
        assert case.calls == case.scheduled == []
        assert case.db.query(TicketMessage).count() == 1
        assert case.db.query(TicketAttachment).count() == 0
        return
    assert asyncio.run(reply_to_ticket(request=request, **case.kwargs)) == {"ok": True}
    case.db.expunge_all()
    reply = case.db.execute(select(TicketMessage).where(TicketMessage.body == "Answer")).scalar_one()
    assert reply.quote_message_id == (quote_id if quote_kind == "local_agent" else None)
    if transport == "text":
        assert case.calls == [{"reply_to_message_id": None}]
    for delivery in case.scheduled:
        assert delivery["reply_to_message_id"] is None


def test_quote_serializes_in_card_api_and_comments_remain_unlinked(case):
    source = source_message(case)
    source_id = source.id
    assert asyncio.run(reply_to_ticket(request=_JsonRequest({"text": "Answer", "quote_message_id": source_id}),
                                      **case.kwargs)) == {"ok": True}
    assert asyncio.run(reply_to_ticket(request=_JsonRequest({"text": "Private comment", "quote_message_id": source_id}),
        message_type=MessageType.INTERNAL, **case.kwargs)) == {"ok": True}
    assert len(case.calls) == 1
    case.db.expunge_all()
    app = FastAPI()
    app.include_router(tickets.router)
    app.dependency_overrides[get_db] = lambda: case.db
    app.dependency_overrides[get_current_user] = lambda: SimpleNamespace(id="admin", role="admin")
    with TestClient(app) as client:
        response = client.get(f"/api/v1/tickets/{case.ticket.id}")
    assert response.status_code == 200, response.text
    messages = {message["body"]: message for message in response.json()["messages"]}
    assert messages["Answer"]["quote_message_id"] == source_id
    assert messages["Quoted original"]["quote_message_id"] is None
    assert messages["Private comment"]["quote_message_id"] is None


def test_schema_evolution_adds_nullable_quote_without_rewriting_history(monkeypatch):
    engine = create_engine("sqlite:///:memory:")
    monkeypatch.setattr(schema_evolution, "engine", engine)
    try:
        with engine.begin() as conn:
            conn.execute(text("CREATE TABLE ticket_messages (id VARCHAR(36) PRIMARY KEY, message_type VARCHAR(20), body TEXT)"))
            conn.execute(text("INSERT INTO ticket_messages (id, message_type, body) VALUES ('old', 'client', 'Original')"))
        schema_evolution.ensure_ticket_message_columns()
        with engine.begin() as conn:
            row = conn.execute(text("SELECT quote_message_id, body FROM ticket_messages WHERE id = 'old'")).one()
            assert tuple(row) == (None, "Original")
            conn.execute(text("UPDATE ticket_messages SET quote_message_id = 'quoted' WHERE id = 'old'"))
        schema_evolution.ensure_ticket_message_columns()
        column = next(column for column in inspect(engine).get_columns("ticket_messages") if column["name"] == "quote_message_id")
        assert column["nullable"] is True
        assert column["type"].length == 36
        with engine.connect() as conn:
            assert conn.execute(text("SELECT quote_message_id FROM ticket_messages WHERE id = 'old'")).scalar_one() == "quoted"
    finally:
        engine.dispose()
