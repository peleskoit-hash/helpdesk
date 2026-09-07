from __future__ import annotations

import asyncio
import uuid
from datetime import datetime, timedelta
from types import SimpleNamespace

from sqlalchemy import and_, desc, or_, select, create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from api.app.db import Base
from api.app.models import AutomaticMessageTemplate, Client, Company, MessageType, TelegramContact, Ticket, TicketAttachment, TicketAttachmentPreload, TicketMessage, TicketStatus, TicketTaskKind, TicketWorkflowType
from api.app.services.ticket_lifecycle import close_ticket_by_actor, finalize_group_parent_if_ready, notify_group_parent_completion_before_delete
from api.app.services.telegram_outbound_delivery import claim_attachment_preload
from api.app.services.telegram_text_format import prepare_telegram_html_message
from api.app.services.ticket_messages import reply_to_ticket


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


def _add_message(db, ticket, message_type, body, author_user_id, **kwargs):
    db.add(
        TicketMessage(
            id=str(uuid.uuid4()),
            ticket_id=ticket.id,
            message_type=message_type.value if hasattr(message_type, "value") else message_type,
            body=body,
            author_user_id=author_user_id,
            agent_visible=kwargs.get("agent_visible", True),
            created_at=datetime.utcnow(),
        )
    )


def _seed_ticket(session, **overrides) -> Ticket:
    now = datetime.utcnow()
    payload = {
        "id": str(uuid.uuid4()),
        "company_id": str(uuid.uuid4()),
        "client_id": str(uuid.uuid4()),
        "subject": "Need help",
        "status": TicketStatus.PENDING.value,
        "workflow_type": TicketWorkflowType.TICKET.value,
        "auto_close_started_at": now,
        "auto_close_due_at": now,
        "created_at": now,
        "updated_at": now,
    }
    payload.update(overrides)
    ticket = Ticket(**payload)
    session.add(ticket)
    session.commit()
    return ticket


def _seed_contact_for_ticket(session, ticket: Ticket) -> TelegramContact:
    company = Company(id=ticket.company_id, name="ACME", created_at=datetime.utcnow())
    client = Client(id=ticket.client_id, company_id=ticket.company_id, display_name="Client")
    contact = TelegramContact(
        id=str(uuid.uuid4()),
        company_id=ticket.company_id,
        client_id=ticket.client_id,
        external_user_id=str(len(session.query(TelegramContact).all()) + 1000),
        external_chat_id=str(len(session.query(TelegramContact).all()) + 1000),
    )
    session.merge(company)
    session.merge(client)
    session.add(contact)
    session.commit()
    return contact


def _markup_labels(markup):
    return [button["text"] for row in (markup or {}).get("inline_keyboard") or [] for button in row]


class _PreloadForm(dict):
    def __init__(self, *, text: str, preload_ids: list[str]):
        super().__init__()
        self["text"] = text
        self._preload_ids = preload_ids

    def getlist(self, key: str):
        if key == "preloaded_attachment_id":
            return list(self._preload_ids)
        if key == "files":
            return []
        return []


class _PreloadRequest:
    headers = {"content-type": "multipart/form-data; boundary=test"}

    def __init__(self, *, text: str, preload_ids: list[str]):
        self._form = _PreloadForm(text=text, preload_ids=preload_ids)

    async def form(self):
        return self._form


def _message_attachments(db, message_id):
    return db.execute(select(TicketAttachment).where(TicketAttachment.message_id == message_id)).scalars().all()


def test_manual_ticket_close_clears_auto_close_timer():
    session = _session()
    ticket = _seed_ticket(session)
    agent_message = TicketMessage(
        id=str(uuid.uuid4()),
        ticket_id=ticket.id,
        message_type=MessageType.AGENT.value,
        body="Done",
        author_user_id="agent-1",
        agent_visible=True,
        created_at=datetime.utcnow(),
    )
    session.add(agent_message)
    session.commit()

    result = close_ticket_by_actor(
        session,
        ticket,
        SimpleNamespace(id="agent-1", role="agent"),
        force_without_agent_reply_for_task=False,
        is_admin_fn=lambda actor: False,
        get_or_create_client_profile_fn=lambda *_: None,
        last_agent_message_fn=lambda *_: agent_message,
        first_client_message_fn=lambda *_: None,
        send_to_telegram_client_fn=lambda *_args, **_kwargs: None,
        actor_display_name_fn=lambda *_: "Agent",
        add_message_fn=_add_message,
        message_attachments_fn=lambda *_: [],
        send_attachment_to_telegram_client_fn=lambda *_args, **_kwargs: None,
        onsite_close_reminder_text="onsite",
        ticket_closed_client_text="closed",
    )
    session.flush()
    session.refresh(ticket)

    assert result == "closed"
    assert ticket.status == TicketStatus.CLOSED.value
    assert ticket.auto_close_started_at is None
    assert ticket.auto_close_due_at is None
    assert ticket.auto_closed_at is None


def test_admin_ticket_force_close_without_agent_reply_clears_auto_close_timer():
    session = _session()
    ticket = _seed_ticket(session)

    result = close_ticket_by_actor(
        session,
        ticket,
        SimpleNamespace(id="admin-1", role="admin"),
        force_without_agent_reply_for_task=True,
        is_admin_fn=lambda actor: True,
        get_or_create_client_profile_fn=lambda *_: None,
        last_agent_message_fn=lambda *_: None,
        first_client_message_fn=lambda *_: None,
        send_to_telegram_client_fn=lambda *_args, **_kwargs: None,
        actor_display_name_fn=lambda *_: "Admin",
        add_message_fn=_add_message,
        message_attachments_fn=lambda *_: [],
        send_attachment_to_telegram_client_fn=lambda *_args, **_kwargs: None,
        onsite_close_reminder_text="onsite",
        ticket_closed_client_text="closed",
    )
    session.flush()
    session.refresh(ticket)

    assert result == "closed"
    assert ticket.status == TicketStatus.CLOSED.value
    assert ticket.auto_close_started_at is None
    assert ticket.auto_close_due_at is None
    assert ticket.auto_closed_at is None


def _last_active_agent_message(db, ticket):
    return db.execute(
        select(TicketMessage)
        .where(
            and_(
                TicketMessage.ticket_id == ticket.id,
                TicketMessage.message_type == MessageType.AGENT.value,
                or_(TicketMessage.message_status.is_(None), TicketMessage.message_status != "deleted"),
            )
        )
        .order_by(desc(TicketMessage.created_at))
        .limit(1)
    ).scalar_one_or_none()


def test_task_close_sends_last_active_text_with_file_list_and_attachment(tmp_path):
    session = _session()
    ticket = _seed_ticket(session, workflow_type=TicketWorkflowType.TASK.value, task_kind=TicketTaskKind.NORMAL.value)
    client_message = TicketMessage(
        id=str(uuid.uuid4()),
        ticket_id=ticket.id,
        message_type=MessageType.CLIENT.value,
        body="Нужен договор",
        agent_visible=True,
        created_at=datetime.utcnow(),
    )
    older_answer = TicketMessage(
        id=str(uuid.uuid4()),
        ticket_id=ticket.id,
        message_type=MessageType.AGENT.value,
        body="Проверьте, пожалуйста, договор.",
        author_user_id="agent-1",
        agent_visible=True,
        message_status="normal",
        created_at=datetime.utcnow(),
    )
    deleted_newer_answer = TicketMessage(
        id=str(uuid.uuid4()),
        ticket_id=ticket.id,
        message_type=MessageType.AGENT.value,
        body="Неактуальный ответ",
        author_user_id="agent-1",
        agent_visible=True,
        message_status="deleted",
        created_at=datetime.utcnow(),
    )
    contract_path = tmp_path / "contract.pdf"
    contract_path.write_bytes(b"pdf")
    attachment = TicketAttachment(
        id=str(uuid.uuid4()),
        ticket_id=ticket.id,
        message_id=older_answer.id,
        source="helpdesk",
        original_filename="contract.pdf",
        mime_type="application/pdf",
        file_size=3,
        local_path=str(contract_path),
        created_at=datetime.utcnow(),
    )
    session.add_all([client_message, older_answer, deleted_newer_answer, attachment])
    session.commit()

    sent_texts = []
    sent_attachments = []
    result = close_ticket_by_actor(
        session,
        ticket,
        SimpleNamespace(id="agent-1", role="agent"),
        force_without_agent_reply_for_task=False,
        is_admin_fn=lambda actor: False,
        get_or_create_client_profile_fn=lambda *_: None,
        last_agent_message_fn=_last_active_agent_message,
        first_client_message_fn=lambda *_: client_message,
        send_to_telegram_client_fn=lambda _db, _ticket, text, **_kwargs: sent_texts.append(text) or {"ok": True},
        actor_display_name_fn=lambda *_: "Agent",
        add_message_fn=_add_message,
        message_attachments_fn=lambda db, message_id: db.execute(
            select(TicketAttachment).where(TicketAttachment.message_id == message_id)
        ).scalars().all(),
        send_attachment_to_telegram_client_fn=lambda _db, _ticket, item, **_kwargs: sent_attachments.append(item.original_filename),
        onsite_close_reminder_text="onsite",
        ticket_closed_client_text="closed",
    )

    assert result == "closed"
    assert sent_attachments == ["contract.pdf"]
    assert "Проверьте, пожалуйста, договор." in sent_texts[0]
    assert "Файлы:\ncontract.pdf" in sent_texts[0]
    assert "Неактуальный ответ" not in sent_texts[0]


def test_task_close_treats_null_message_status_as_active():
    session = _session()
    ticket = _seed_ticket(session, workflow_type=TicketWorkflowType.TASK.value, task_kind=TicketTaskKind.NORMAL.value)
    client_message = TicketMessage(id=str(uuid.uuid4()), ticket_id=ticket.id, message_type=MessageType.CLIENT.value, body="Вопрос", created_at=datetime.utcnow())
    legacy_agent_message = TicketMessage(
        id=str(uuid.uuid4()),
        ticket_id=ticket.id,
        message_type=MessageType.AGENT.value,
        body="Legacy answer",
        author_user_id="agent-1",
        message_status=None,
        created_at=datetime.utcnow(),
    )
    deleted_newer_answer = TicketMessage(
        id=str(uuid.uuid4()),
        ticket_id=ticket.id,
        message_type=MessageType.AGENT.value,
        body="Deleted answer",
        author_user_id="agent-1",
        message_status="deleted",
        created_at=datetime.utcnow() + timedelta(seconds=1),
    )
    session.add_all([client_message, legacy_agent_message, deleted_newer_answer])
    session.commit()

    sent_texts = []
    close_ticket_by_actor(
        session,
        ticket,
        SimpleNamespace(id="agent-1", role="agent"),
        force_without_agent_reply_for_task=False,
        is_admin_fn=lambda actor: False,
        get_or_create_client_profile_fn=lambda *_: None,
        last_agent_message_fn=_last_active_agent_message,
        first_client_message_fn=lambda *_: client_message,
        send_to_telegram_client_fn=lambda _db, _ticket, text, **_kwargs: sent_texts.append(text) or {"ok": True},
        actor_display_name_fn=lambda *_: "Agent",
        add_message_fn=_add_message,
        message_attachments_fn=lambda *_: [],
        send_attachment_to_telegram_client_fn=lambda *_args, **_kwargs: None,
        onsite_close_reminder_text="onsite",
        ticket_closed_client_text="closed",
    )

    assert "Legacy answer" in sent_texts[0]
    assert "Deleted answer" not in sent_texts[0]


def test_task_preload_claim_real_flow_labels_image_video_and_document_by_persisted_metadata(tmp_path):
    session = _session()
    ticket = _seed_ticket(session, workflow_type=TicketWorkflowType.TASK.value, task_kind=TicketTaskKind.NORMAL.value)
    client_message = TicketMessage(id=str(uuid.uuid4()), ticket_id=ticket.id, message_type=MessageType.CLIENT.value, body="Вопрос", created_at=datetime.utcnow())
    session.add(client_message)
    preloads = []
    for filename, mime_type, file_id in [
        ("image.jpg", "image/jpeg", "file-image"),
        ("clip.mp4", "video/mp4", "file-video"),
        ("contract.pdf", "application/pdf", "file-pdf"),
    ]:
        path = tmp_path / filename
        path.write_bytes(b"payload")
        preloads.append(
            TicketAttachmentPreload(
                id=str(uuid.uuid4()),
                ticket_id=ticket.id,
                user_id="agent-1",
                original_filename=filename,
                mime_type=mime_type,
                file_size=7,
                local_path=str(path),
                telegram_file_id=file_id,
                status="ready",
                created_at=datetime.utcnow(),
            )
        )
    session.add_all(preloads)
    session.commit()

    async def run_reply():
        return await reply_to_ticket(
            request=_PreloadRequest(text="Ответ с файлами", preload_ids=[preload.id for preload in preloads]),
            db=session,
            user=SimpleNamespace(id="agent-1", role="agent"),
            ticket=ticket,
            send_to_telegram_client=lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("task reply must not send text")),
            telegram_message_id_from_response=lambda response: None,
            save_upload_attachment=lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("raw upload should not be used")),
            send_attachment_to_telegram_client=lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("task reply must not send file")),
            claim_attachment_preload=lambda db, preload_id, expected_ticket_id, expected_user_id: claim_attachment_preload(
                db,
                preload_id=preload_id,
                ticket_id=expected_ticket_id,
                user_id=expected_user_id,
            ),
            schedule_attachment_delivery=lambda **_kwargs: (_ for _ in ()).throw(AssertionError("task reply must not schedule delivery")),
            unfreeze_ticket_on_message=lambda *_args, **_kwargs: None,
            remove_client_close_button=lambda *_args, **_kwargs: None,
            refresh_ticket_auto_close=lambda *_args, **_kwargs: None,
        )

    assert asyncio.run(run_reply()) == {"ok": True}
    agent_message = _last_active_agent_message(session, ticket)
    attachments = _message_attachments(session, agent_message.id)

    assert [attachment.source for attachment in attachments] == ["helpdesk", "helpdesk", "helpdesk"]
    assert [attachment.mime_type for attachment in attachments] == ["image/jpeg", "video/mp4", "application/pdf"]

    sent_texts = []
    sent_attachments = []
    close_ticket_by_actor(
        session,
        ticket,
        SimpleNamespace(id="agent-1", role="agent"),
        force_without_agent_reply_for_task=False,
        is_admin_fn=lambda actor: False,
        get_or_create_client_profile_fn=lambda *_: None,
        last_agent_message_fn=_last_active_agent_message,
        first_client_message_fn=lambda *_: client_message,
        send_to_telegram_client_fn=lambda _db, _ticket, text, **_kwargs: sent_texts.append(text) or {"ok": True},
        actor_display_name_fn=lambda *_: "Agent",
        add_message_fn=_add_message,
        message_attachments_fn=_message_attachments,
        send_attachment_to_telegram_client_fn=lambda _db, _ticket, item, **_kwargs: sent_attachments.append(item.original_filename),
        onsite_close_reminder_text="onsite",
        ticket_closed_client_text="closed",
    )

    assert sent_attachments == ["image.jpg", "clip.mp4", "contract.pdf"]
    assert "Файлы:\nФото\nВидео\ncontract.pdf" in sent_texts[0]


def test_task_close_sends_text_and_all_four_file_names_from_single_agent_message(tmp_path):
    session = _session()
    ticket = _seed_ticket(session, workflow_type=TicketWorkflowType.TASK.value, task_kind=TicketTaskKind.NORMAL.value)
    client_message = TicketMessage(id=str(uuid.uuid4()), ticket_id=ticket.id, message_type=MessageType.CLIENT.value, body="Вопрос", created_at=datetime.utcnow())
    agent_message = TicketMessage(
        id=str(uuid.uuid4()),
        ticket_id=ticket.id,
        message_type=MessageType.AGENT.value,
        body="выввыв",
        author_user_id="agent-1",
        created_at=datetime.utcnow(),
    )
    session.add_all([client_message, agent_message])
    filenames = [
        "MISSION_CHECKLIST_DRAFT_v21.txt",
        "run_e2e_trace.log",
        "run_v6_integrated.py",
        "task_job.json",
    ]
    attachments = []
    for filename in filenames:
        path = tmp_path / filename
        path.write_bytes(b"file")
        attachments.append(
            TicketAttachment(
                id=str(uuid.uuid4()),
                ticket_id=ticket.id,
                message_id=agent_message.id,
                source="helpdesk",
                original_filename=filename,
                mime_type="text/plain",
                local_path=str(path),
                created_at=datetime.utcnow(),
            )
        )
    session.add_all(attachments)
    session.commit()

    sent_texts = []
    sent_attachments = []
    close_ticket_by_actor(
        session,
        ticket,
        SimpleNamespace(id="agent-1", role="agent"),
        force_without_agent_reply_for_task=False,
        is_admin_fn=lambda actor: False,
        get_or_create_client_profile_fn=lambda *_: None,
        last_agent_message_fn=lambda *_: agent_message,
        first_client_message_fn=lambda *_: client_message,
        send_to_telegram_client_fn=lambda _db, _ticket, text, **_kwargs: sent_texts.append(text) or {"ok": True},
        actor_display_name_fn=lambda *_: "Agent",
        add_message_fn=_add_message,
        message_attachments_fn=_message_attachments,
        send_attachment_to_telegram_client_fn=lambda _db, _ticket, item, **_kwargs: sent_attachments.append(item.original_filename),
        onsite_close_reminder_text="onsite",
        ticket_closed_client_text="closed",
    )

    assert sent_texts[0].endswith(
        "выввыв\n\nФайлы:\nMISSION_CHECKLIST_DRAFT_v21.txt\nrun_e2e_trace.log\nrun_v6_integrated.py\ntask_job.json"
    )
    assert sent_attachments == filenames


def test_task_completion_agent_answer_escapes_dynamic_text_and_filenames_for_telegram_html(tmp_path):
    session = _session()
    ticket = _seed_ticket(session, workflow_type=TicketWorkflowType.TASK.value, task_kind=TicketTaskKind.NORMAL.value)
    client_message = TicketMessage(id=str(uuid.uuid4()), ticket_id=ticket.id, message_type=MessageType.CLIENT.value, body="<client>&", created_at=datetime.utcnow())
    agent_message = TicketMessage(
        id=str(uuid.uuid4()),
        ticket_id=ticket.id,
        message_type=MessageType.AGENT.value,
        body='Проверьте <b>это</b> & "то"',
        author_user_id="agent-1",
        created_at=datetime.utcnow(),
    )
    file_path = tmp_path / "contract.pdf"
    file_path.write_bytes(b"pdf")
    attachment = TicketAttachment(
        id=str(uuid.uuid4()),
        ticket_id=ticket.id,
        message_id=agent_message.id,
        source="helpdesk",
        original_filename='<contract>&".pdf',
        mime_type="application/pdf",
        local_path=str(file_path),
        created_at=datetime.utcnow(),
    )
    template = AutomaticMessageTemplate(
        id=str(uuid.uuid4()),
        key="telegram.task.completion.with_answer",
        body="<b>{{agent_answer}}</b>",
        is_overridden=True,
        html_enabled=True,
        enabled=True,
    )
    session.add_all([client_message, agent_message, attachment, template])
    session.commit()

    prepared = []
    close_ticket_by_actor(
        session,
        ticket,
        SimpleNamespace(id="agent-1", role="agent"),
        force_without_agent_reply_for_task=False,
        is_admin_fn=lambda actor: False,
        get_or_create_client_profile_fn=lambda *_: None,
        last_agent_message_fn=lambda *_: agent_message,
        first_client_message_fn=lambda *_: client_message,
        send_to_telegram_client_fn=lambda _db, _ticket, text, **_kwargs: prepared.append(prepare_telegram_html_message(text)) or {"ok": True},
        actor_display_name_fn=lambda *_: "Agent",
        add_message_fn=_add_message,
        message_attachments_fn=_message_attachments,
        send_attachment_to_telegram_client_fn=lambda *_args, **_kwargs: None,
        onsite_close_reminder_text="onsite",
        ticket_closed_client_text="closed",
    )

    payload_text, parse_mode = prepared[0]
    assert parse_mode == "HTML"
    assert payload_text.startswith("<b>")
    assert "&lt;b&gt;это&lt;/b&gt; &amp; \"то\"" in payload_text
    assert '&lt;contract&gt;&amp;".pdf' in payload_text


def test_task_text_only_completion_keeps_agent_answer_text_only():
    session = _session()
    ticket = _seed_ticket(session, workflow_type=TicketWorkflowType.TASK.value, task_kind=TicketTaskKind.NORMAL.value)
    client_message = TicketMessage(id=str(uuid.uuid4()), ticket_id=ticket.id, message_type=MessageType.CLIENT.value, body="Вопрос", created_at=datetime.utcnow())
    agent_message = TicketMessage(id=str(uuid.uuid4()), ticket_id=ticket.id, message_type=MessageType.AGENT.value, body="Ответ", author_user_id="agent-1", created_at=datetime.utcnow())
    session.add_all([client_message, agent_message])
    session.commit()

    sent_texts = []
    close_ticket_by_actor(
        session,
        ticket,
        SimpleNamespace(id="agent-1", role="agent"),
        force_without_agent_reply_for_task=False,
        is_admin_fn=lambda actor: False,
        get_or_create_client_profile_fn=lambda *_: None,
        last_agent_message_fn=lambda *_: agent_message,
        first_client_message_fn=lambda *_: client_message,
        send_to_telegram_client_fn=lambda _db, _ticket, text, **_kwargs: sent_texts.append(text) or {"ok": True},
        actor_display_name_fn=lambda *_: "Agent",
        add_message_fn=_add_message,
        message_attachments_fn=lambda *_: [],
        send_attachment_to_telegram_client_fn=lambda *_args, **_kwargs: None,
        onsite_close_reminder_text="onsite",
        ticket_closed_client_text="closed",
    )

    assert "Получен ответ:\nОтвет" in sent_texts[0]
    assert "Файлы:" not in sent_texts[0]


def test_task_text_with_photo_and_video_lists_human_labels(tmp_path):
    session = _session()
    ticket = _seed_ticket(session, workflow_type=TicketWorkflowType.TASK.value, task_kind=TicketTaskKind.NORMAL.value)
    client_message = TicketMessage(id=str(uuid.uuid4()), ticket_id=ticket.id, message_type=MessageType.CLIENT.value, body="Вопрос", created_at=datetime.utcnow())
    agent_message = TicketMessage(id=str(uuid.uuid4()), ticket_id=ticket.id, message_type=MessageType.AGENT.value, body="Ответ", author_user_id="agent-1", created_at=datetime.utcnow())
    photo_path = tmp_path / "photo.jpg"
    video_path = tmp_path / "video.mp4"
    photo_path.write_bytes(b"jpg")
    video_path.write_bytes(b"mp4")
    attachment_created_at = datetime.utcnow()
    session.add_all([
        client_message,
        agent_message,
        TicketAttachment(id=str(uuid.uuid4()), ticket_id=ticket.id, message_id=agent_message.id, source="photo", original_filename="photo.jpg", mime_type="image/jpeg", local_path=str(photo_path), created_at=attachment_created_at),
        TicketAttachment(id=str(uuid.uuid4()), ticket_id=ticket.id, message_id=agent_message.id, source="video", original_filename="video.mp4", mime_type="video/mp4", local_path=str(video_path), created_at=attachment_created_at + timedelta(seconds=1)),
    ])
    session.commit()

    sent_texts = []
    close_ticket_by_actor(
        session,
        ticket,
        SimpleNamespace(id="agent-1", role="agent"),
        force_without_agent_reply_for_task=False,
        is_admin_fn=lambda actor: False,
        get_or_create_client_profile_fn=lambda *_: None,
        last_agent_message_fn=lambda *_: agent_message,
        first_client_message_fn=lambda *_: client_message,
        send_to_telegram_client_fn=lambda _db, _ticket, text, **_kwargs: sent_texts.append(text) or {"ok": True},
        actor_display_name_fn=lambda *_: "Agent",
        add_message_fn=_add_message,
        message_attachments_fn=lambda db, message_id: db.execute(select(TicketAttachment).where(TicketAttachment.message_id == message_id)).scalars().all(),
        send_attachment_to_telegram_client_fn=lambda *_args, **_kwargs: None,
        onsite_close_reminder_text="onsite",
        ticket_closed_client_text="closed",
    )

    assert "Файлы:\nФото\nВидео" in sent_texts[0]


def test_task_files_only_completion_keeps_files_only_branch(tmp_path):
    session = _session()
    ticket = _seed_ticket(session, workflow_type=TicketWorkflowType.TASK.value, task_kind=TicketTaskKind.NORMAL.value)
    client_message = TicketMessage(id=str(uuid.uuid4()), ticket_id=ticket.id, message_type=MessageType.CLIENT.value, body="Вопрос", created_at=datetime.utcnow())
    agent_message = TicketMessage(id=str(uuid.uuid4()), ticket_id=ticket.id, message_type=MessageType.AGENT.value, body="", author_user_id="agent-1", created_at=datetime.utcnow())
    file_path = tmp_path / "contract.pdf"
    file_path.write_bytes(b"pdf")
    session.add_all([
        client_message,
        agent_message,
        TicketAttachment(id=str(uuid.uuid4()), ticket_id=ticket.id, message_id=agent_message.id, source="helpdesk", original_filename="contract.pdf", mime_type="application/pdf", local_path=str(file_path), created_at=datetime.utcnow()),
    ])
    session.commit()

    sent_texts = []
    close_ticket_by_actor(
        session,
        ticket,
        SimpleNamespace(id="agent-1", role="agent"),
        force_without_agent_reply_for_task=False,
        is_admin_fn=lambda actor: False,
        get_or_create_client_profile_fn=lambda *_: None,
        last_agent_message_fn=lambda *_: agent_message,
        first_client_message_fn=lambda *_: client_message,
        send_to_telegram_client_fn=lambda _db, _ticket, text, **_kwargs: sent_texts.append(text) or {"ok": True},
        actor_display_name_fn=lambda *_: "Agent",
        add_message_fn=_add_message,
        message_attachments_fn=lambda db, message_id: db.execute(select(TicketAttachment).where(TicketAttachment.message_id == message_id)).scalars().all(),
        send_attachment_to_telegram_client_fn=lambda *_args, **_kwargs: None,
        onsite_close_reminder_text="onsite",
        ticket_closed_client_text="closed",
    )

    assert "Получены файлы от сотрудника:" in sent_texts[0]
    assert "{{agent_answer}}" not in sent_texts[0]
    assert "Файлы:\ncontract.pdf" not in sent_texts[0]


def test_all_task_close_paths_send_feedback_action_to_client():
    session = _session()

    normal = _seed_ticket(session, workflow_type=TicketWorkflowType.TASK.value, task_kind=TicketTaskKind.NORMAL.value)
    _seed_contact_for_ticket(session, normal)
    normal_client_message = TicketMessage(id=str(uuid.uuid4()), ticket_id=normal.id, message_type=MessageType.CLIENT.value, body="Вопрос", created_at=datetime.utcnow())
    normal_agent_message = TicketMessage(id=str(uuid.uuid4()), ticket_id=normal.id, message_type=MessageType.AGENT.value, body="Ответ", author_user_id="agent-1", created_at=datetime.utcnow())
    session.add_all([normal_client_message, normal_agent_message])
    session.commit()

    without_answer = _seed_ticket(session, workflow_type=TicketWorkflowType.TASK.value, task_kind=TicketTaskKind.NORMAL.value)
    _seed_contact_for_ticket(session, without_answer)
    without_answer_client_message = TicketMessage(id=str(uuid.uuid4()), ticket_id=without_answer.id, message_type=MessageType.CLIENT.value, body="Нет данных", created_at=datetime.utcnow())
    session.add(without_answer_client_message)
    session.commit()

    parent_direct = _seed_ticket(session, workflow_type=TicketWorkflowType.TASK.value, task_kind=TicketTaskKind.GROUP_PARENT.value)
    _seed_contact_for_ticket(session, parent_direct)
    parent_direct_client_message = TicketMessage(id=str(uuid.uuid4()), ticket_id=parent_direct.id, message_type=MessageType.CLIENT.value, body="Группа", created_at=datetime.utcnow())
    parent_direct_agent_message = TicketMessage(id=str(uuid.uuid4()), ticket_id=parent_direct.id, message_type=MessageType.AGENT.value, body="Итог", author_user_id="agent-1", created_at=datetime.utcnow())
    session.add_all([parent_direct_client_message, parent_direct_agent_message])
    session.commit()

    parent_auto = _seed_ticket(session, workflow_type=TicketWorkflowType.TASK.value, task_kind=TicketTaskKind.GROUP_PARENT.value)
    _seed_contact_for_ticket(session, parent_auto)
    parent_auto_client_message = TicketMessage(id=str(uuid.uuid4()), ticket_id=parent_auto.id, message_type=MessageType.CLIENT.value, body="Автогруппа", created_at=datetime.utcnow())
    session.add(parent_auto_client_message)
    session.commit()

    parent_delete = _seed_ticket(session, workflow_type=TicketWorkflowType.TASK.value, task_kind=TicketTaskKind.GROUP_PARENT.value, status=TicketStatus.CLOSED.value)
    _seed_contact_for_ticket(session, parent_delete)
    parent_delete_client_message = TicketMessage(id=str(uuid.uuid4()), ticket_id=parent_delete.id, message_type=MessageType.CLIENT.value, body="Перед удалением", created_at=datetime.utcnow())
    session.add(parent_delete_client_message)
    session.commit()

    sent = {}

    def _send(_db, ticket, text, **kwargs):
        sent.setdefault(ticket.id, []).append(kwargs.get("reply_markup"))
        return {"ok": True, "result": {"message_id": len(sent) + 300}}

    close_kwargs = dict(
        force_without_agent_reply_for_task=False,
        is_admin_fn=lambda actor: False,
        get_or_create_client_profile_fn=lambda *_: None,
        last_agent_message_fn=lambda db, ticket: normal_agent_message if ticket.id == normal.id else parent_direct_agent_message,
        first_client_message_fn=lambda db, ticket: {
            normal.id: normal_client_message,
            without_answer.id: without_answer_client_message,
            parent_direct.id: parent_direct_client_message,
            parent_auto.id: parent_auto_client_message,
            parent_delete.id: parent_delete_client_message,
        }.get(ticket.id),
        send_to_telegram_client_fn=_send,
        actor_display_name_fn=lambda *_: "Agent",
        add_message_fn=_add_message,
        message_attachments_fn=lambda *_: [],
        send_attachment_to_telegram_client_fn=lambda *_args, **_kwargs: None,
        onsite_close_reminder_text="onsite",
        ticket_closed_client_text="closed",
    )
    close_ticket_by_actor(session, normal, SimpleNamespace(id="agent-1", role="agent"), **close_kwargs)
    close_ticket_by_actor(
        session,
        without_answer,
        SimpleNamespace(id="admin-1", role="admin"),
        **{**close_kwargs, "force_without_agent_reply_for_task": True, "is_admin_fn": lambda actor: True, "last_agent_message_fn": lambda *_: None},
    )
    close_ticket_by_actor(session, parent_direct, SimpleNamespace(id="agent-1", role="agent"), **close_kwargs)
    finalize_group_parent_if_ready(
        session,
        parent_auto,
        closed_by_user_id="agent-1",
        first_client_message_fn=close_kwargs["first_client_message_fn"],
        send_to_telegram_client_fn=_send,
        send_attachment_to_telegram_client_fn=lambda *_args, **_kwargs: None,
        add_message_fn=_add_message,
    )
    notify_group_parent_completion_before_delete(
        session,
        parent_delete,
        first_client_message_fn=close_kwargs["first_client_message_fn"],
        send_to_telegram_client_fn=_send,
        send_attachment_to_telegram_client_fn=lambda *_args, **_kwargs: None,
    )

    for ticket in [normal, without_answer, parent_direct, parent_auto, parent_delete]:
        assert sent[ticket.id]
        assert _markup_labels(sent[ticket.id][0]) == ["Предложения и замечания"]
