from __future__ import annotations

import uuid
from datetime import datetime, timedelta
from types import SimpleNamespace

from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from api.app.db import Base
from api.app.models import (
    Client,
    Company,
    EmployeeNotificationPreference,
    InternalTaskAssignmentMode,
    InternalTaskDefinition,
    InternalTaskDefinitionState,
    InternalTaskOccurrence,
    InternalTaskScheduleKind,
    MessageType,
    TelegramNotificationAccount,
    Ticket,
    TicketAttachment,
    TicketMessage,
    TicketStatus,
    TicketTaskKind,
    TicketWorkflowType,
    User,
)
from api.app.services import employee_telegram_notifications as notifications
from api.app.services.automatic_messages import upsert_notification_bot_basic_template
from api.app.services.ticket_lifecycle import close_ticket_by_actor


def _session_factory():
    engine = create_engine(
        "sqlite://",
        future=True,
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    return sessionmaker(bind=engine, autoflush=False, autocommit=False)


def _user(session, company_id: str, role: str, *, is_active: bool = True) -> User:
    user = User(
        id=str(uuid.uuid4()),
        company_id=company_id,
        email=None,
        login=str(uuid.uuid4()),
        password_hash="hash",
        full_name=f"{role}-{uuid.uuid4().hex[:6]}",
        role=role,
        is_active=is_active,
        created_at=datetime.utcnow(),
        updated_at=datetime.utcnow(),
    )
    session.add(user)
    return user


def _base_entities(
    session,
    *,
    notify_on_completion: bool = True,
    pref_enabled: bool = True,
    responsible_missing: bool = False,
    use_base_internal_task_completion_settings: bool | None = None,
):
    now = datetime.utcnow()
    company = Company(id=str(uuid.uuid4()), name="ACME", is_active=True, created_at=now)
    client = Client(id=str(uuid.uuid4()), company_id=company.id, display_name="Система")
    session.add_all([company, client])
    creator = _user(session, company.id, "admin")
    responsible = _user(session, company.id, "admin")
    agent = _user(session, company.id, "agent")
    definition = InternalTaskDefinition(
        id=str(uuid.uuid4()),
        company_id=company.id,
        title="Definition title",
        description="Description",
        creator_admin_id=creator.id,
        responsible_admin_id=responsible.id,
        display_author=True,
        assignment_mode=InternalTaskAssignmentMode.DIRECT.value,
        assignee_user_id=agent.id,
        notify_on_completion=notify_on_completion,
        schedule_kind=InternalTaskScheduleKind.IMMEDIATE.value,
        timezone_name="Europe/Moscow",
        state=InternalTaskDefinitionState.FINISHED.value,
        created_at=now,
        updated_at=now,
    )
    ticket = Ticket(
        id=str(uuid.uuid4()),
        company_id=company.id,
        client_id=client.id,
        assignee_id=agent.id,
        subject="Current subject should not win",
        status=TicketStatus.PENDING.value,
        workflow_type=TicketWorkflowType.TASK.value,
        task_kind=TicketTaskKind.NORMAL.value,
        created_at=now,
        updated_at=now,
    )
    occurrence = InternalTaskOccurrence(
        id=str(uuid.uuid4()),
        definition_id=definition.id,
        occurrence_at_utc=now,
        ticket_id=ticket.id,
        title_snapshot="Snapshot title",
        description_snapshot="Description",
        creator_admin_id=creator.id,
        responsible_admin_id="missing-responsible" if responsible_missing else responsible.id,
        display_author=True,
        effective_assignment_mode=InternalTaskAssignmentMode.DIRECT.value,
        effective_assignee_user_id=agent.id,
        notify_on_completion=notify_on_completion,
        materialized_at_utc=now,
        created_at=now,
    )
    preference = EmployeeNotificationPreference(
        id=str(uuid.uuid4()),
        user_id=responsible.id,
        use_base_new_assignment_settings=False,
        use_base_internal_task_completion_settings=pref_enabled
        if use_base_internal_task_completion_settings is None
        else use_base_internal_task_completion_settings,
        notify_internal_task_completion=pref_enabled,
    )
    account = TelegramNotificationAccount(
        id=str(uuid.uuid4()),
        telegram_user_id=str(uuid.uuid4()),
        telegram_chat_id="chat-responsible",
        bound_user_id=responsible.id,
        request_status="approved",
        delivery_available=True,
        is_blocked=False,
        is_notification_bot_admin=False,
    )
    session.add_all([definition, ticket, occurrence, preference, account])
    _message(session, ticket, MessageType.CLIENT.value, "Initial internal task", None, created_at=now)
    session.commit()
    return SimpleNamespace(company=company, responsible=responsible, agent=agent, ticket=ticket, occurrence=occurrence)


def _ordinary_task(session):
    now = datetime.utcnow()
    company = Company(id=str(uuid.uuid4()), name="ACME", is_active=True, created_at=now)
    client = Client(id=str(uuid.uuid4()), company_id=company.id, display_name="Client")
    session.add_all([company, client])
    agent = _user(session, company.id, "agent")
    ticket = Ticket(
        id=str(uuid.uuid4()),
        company_id=company.id,
        client_id=client.id,
        assignee_id=agent.id,
        subject="Ordinary task",
        status=TicketStatus.PENDING.value,
        workflow_type=TicketWorkflowType.TASK.value,
        task_kind=TicketTaskKind.NORMAL.value,
        created_at=now,
        updated_at=now,
    )
    session.add(ticket)
    _message(session, ticket, MessageType.CLIENT.value, "Client text", None, created_at=now)
    session.commit()
    return SimpleNamespace(agent=agent, ticket=ticket)


def _message(session, ticket: Ticket, message_type: str, body: str, author_user_id: str | None, *, created_at: datetime | None = None):
    message = TicketMessage(
        id=str(uuid.uuid4()),
        ticket_id=ticket.id,
        message_type=message_type,
        body=body,
        author_user_id=author_user_id,
        agent_visible=True,
        message_status="normal",
        created_at=created_at or datetime.utcnow(),
    )
    session.add(message)
    session.flush()
    return message


def _attachment(session, ticket: Ticket, message, *, filename: str = "result.pdf", created_at: datetime | None = None):
    attachment = TicketAttachment(
        id=str(uuid.uuid4()),
        ticket_id=ticket.id,
        message_id=message.id,
        source="helpdesk",
        original_filename=filename,
        mime_type="application/pdf",
        local_path=f"/tmp/{filename}",
        created_at=created_at or datetime.utcnow(),
    )
    session.add(attachment)
    session.flush()
    return attachment


def _last_agent_message(session, ticket: Ticket):
    return session.execute(
        select(TicketMessage)
        .where(TicketMessage.ticket_id == ticket.id, TicketMessage.message_type == MessageType.AGENT.value)
        .order_by(TicketMessage.created_at.desc())
        .limit(1)
    ).scalar_one_or_none()


def _first_client_message(session, ticket: Ticket):
    return session.execute(
        select(TicketMessage)
        .where(TicketMessage.ticket_id == ticket.id, TicketMessage.message_type == MessageType.CLIENT.value)
        .order_by(TicketMessage.created_at.asc())
        .limit(1)
    ).scalar_one_or_none()


def _message_attachments(session, message_id: str):
    return session.execute(
        select(TicketAttachment).where(TicketAttachment.message_id == message_id).order_by(TicketAttachment.created_at.asc())
    ).scalars().all()


def _add_message(session, ticket, message_type, body, author_user_id, **kwargs):
    return _message(
        session,
        ticket,
        message_type.value if hasattr(message_type, "value") else message_type,
        body,
        author_user_id,
    )


def _close(session, ticket: Ticket, actor: User, *, force: bool = False, is_admin: bool = False, client_texts: list[str] | None = None):
    captured_client_texts = client_texts if client_texts is not None else []
    result = close_ticket_by_actor(
        session,
        ticket,
        SimpleNamespace(id=actor.id, role=actor.role),
        force_without_agent_reply_for_task=force,
        is_admin_fn=lambda _actor: is_admin,
        get_or_create_client_profile_fn=lambda *_: None,
        last_agent_message_fn=_last_agent_message,
        first_client_message_fn=_first_client_message,
        send_to_telegram_client_fn=lambda _db, _ticket, text, **_kwargs: captured_client_texts.append(text) or {"ok": True},
        actor_display_name_fn=lambda *_: actor.full_name,
        add_message_fn=_add_message,
        message_attachments_fn=_message_attachments,
        send_attachment_to_telegram_client_fn=lambda *_args, **_kwargs: {"ok": True},
        onsite_close_reminder_text="onsite",
        ticket_closed_client_text="closed",
    )
    session.commit()
    return result


def _capture_delivery(monkeypatch, Session):
    sent_texts: list[str] = []
    sent_files: list[str] = []
    monkeypatch.setattr(notifications, "SessionLocal", Session)
    monkeypatch.setattr(notifications.settings, "employee_telegram_bot_token", "employee-token")
    monkeypatch.setattr(notifications.settings, "app_public_base_url", "https://desk.example.test")
    monkeypatch.setattr(notifications, "send_message", lambda _chat_id, text, **_kwargs: sent_texts.append(text) or {"ok": True})
    monkeypatch.setattr(
        notifications,
        "send_document",
        lambda _chat_id, file_path, **_kwargs: sent_files.append(str(file_path)) or {"ok": True},
        raising=False,
    )
    monkeypatch.setattr(
        notifications,
        "send_document_by_file_id",
        lambda _chat_id, file_id, **_kwargs: sent_files.append(str(file_id)) or {"ok": True},
        raising=False,
    )
    return sent_texts, sent_files


def test_intermediate_text_and_file_do_not_notify_responsible_admin(monkeypatch):
    Session = _session_factory()
    session = Session()
    data = _base_entities(session)
    sent_texts, sent_files = _capture_delivery(monkeypatch, Session)

    intermediate = _message(session, data.ticket, MessageType.AGENT.value, "Intermediate", data.agent.id)
    session.commit()
    _attachment(session, data.ticket, intermediate)
    session.commit()

    assert sent_texts == []
    assert sent_files == []


def test_internal_task_completion_sends_snapshot_title_and_final_text_once(monkeypatch):
    Session = _session_factory()
    session = Session()
    data = _base_entities(session)
    sent_texts, sent_files = _capture_delivery(monkeypatch, Session)
    now = datetime.utcnow()
    _message(session, data.ticket, MessageType.AGENT.value, "Intermediate text", data.agent.id, created_at=now)
    _message(session, data.ticket, MessageType.AGENT.value, "Final text", data.agent.id, created_at=now + timedelta(seconds=1))

    assert _close(session, data.ticket, data.agent) == "closed"

    assert data.ticket.status == TicketStatus.CLOSED.value
    assert len(sent_texts) == 1
    assert f"{data.agent.full_name} выполнил задачу" in sent_texts[0]
    assert f'<a href="https://desk.example.test/tickets/{data.ticket.ticket_number}">Snapshot title</a>' in sent_texts[0]
    assert "Final text" in sent_texts[0]
    assert "Intermediate text" not in sent_texts[0]
    assert sent_files == []


def test_internal_task_completion_sends_file_only_result(monkeypatch):
    Session = _session_factory()
    session = Session()
    data = _base_entities(session)
    sent_texts, sent_files = _capture_delivery(monkeypatch, Session)
    final = _message(session, data.ticket, MessageType.AGENT.value, "", data.agent.id)
    _attachment(session, data.ticket, final, filename="final.pdf")

    _close(session, data.ticket, data.agent)

    assert len(sent_texts) == 1
    assert "Результат:\nФайлы:\nfinal.pdf" in sent_texts[0]
    assert sent_files == []


def test_internal_task_completion_sends_text_and_only_final_file(monkeypatch):
    Session = _session_factory()
    session = Session()
    data = _base_entities(session)
    sent_texts, sent_files = _capture_delivery(monkeypatch, Session)
    old = _message(session, data.ticket, MessageType.AGENT.value, "Old", data.agent.id, created_at=datetime.utcnow())
    _attachment(session, data.ticket, old, filename="old.pdf")
    final = _message(session, data.ticket, MessageType.AGENT.value, "Final with file", data.agent.id, created_at=datetime.utcnow() + timedelta(seconds=1))
    _attachment(session, data.ticket, final, filename="final.pdf")

    _close(session, data.ticket, data.agent)

    assert len(sent_texts) == 1
    assert "Final with file" in sent_texts[0]
    assert "Файлы:\nfinal.pdf" in sent_texts[0]
    assert "Old" not in sent_texts[0]
    assert "old.pdf" not in sent_texts[0]
    assert sent_files == []


def test_internal_task_completion_ignores_comment_attachment_before_agent_result(monkeypatch):
    Session = _session_factory()
    session = Session()
    data = _base_entities(session)
    sent_texts, sent_files = _capture_delivery(monkeypatch, Session)
    now = datetime.utcnow()
    comment = _message(session, data.ticket, MessageType.INTERNAL.value, "Internal context", data.agent.id, created_at=now)
    _attachment(session, data.ticket, comment, filename="internal.pdf")
    final = _message(session, data.ticket, MessageType.AGENT.value, "Final with file", data.agent.id, created_at=now + timedelta(seconds=1))
    _attachment(session, data.ticket, final, filename="final.pdf")

    _close(session, data.ticket, data.agent)

    assert len(sent_texts) == 1
    assert "Final with file" in sent_texts[0]
    assert "Файлы:\nfinal.pdf" in sent_texts[0]
    assert "Internal context" not in sent_texts[0]
    assert "internal.pdf" not in sent_texts[0]
    assert sent_files == []


def test_internal_task_completion_respects_occurrence_flag_and_user_preference(monkeypatch):
    for notify_on_completion, pref_enabled in [(False, True), (True, False)]:
        Session = _session_factory()
        session = Session()
        data = _base_entities(session, notify_on_completion=notify_on_completion, pref_enabled=pref_enabled)
        sent_texts, sent_files = _capture_delivery(monkeypatch, Session)
        _message(session, data.ticket, MessageType.AGENT.value, "Final", data.agent.id)

        _close(session, data.ticket, data.agent)

        assert sent_texts == []
        assert sent_files == []


def test_internal_task_completion_personal_preference_on_sends(monkeypatch):
    Session = _session_factory()
    session = Session()
    data = _base_entities(session, pref_enabled=True)
    sent_texts, sent_files = _capture_delivery(monkeypatch, Session)
    _message(session, data.ticket, MessageType.AGENT.value, "Final", data.agent.id)

    _close(session, data.ticket, data.agent)

    assert len(sent_texts) == 1
    assert "Final" in sent_texts[0]
    assert sent_files == []


def test_internal_task_completion_personal_template_is_used(monkeypatch):
    Session = _session_factory()
    session = Session()
    data = _base_entities(session, pref_enabled=True, use_base_internal_task_completion_settings=False)
    pref = session.execute(
        select(EmployeeNotificationPreference).where(EmployeeNotificationPreference.user_id == data.responsible.id)
    ).scalar_one()
    pref.personal_internal_task_completion_template = "PERSONAL [Автор сообщения] => [Задача] => [Результат]"
    pref.personal_internal_task_completion_html_enabled = False
    pref.personal_internal_task_completion_disable_link_preview = True
    pref.personal_internal_task_completion_send_silently = True
    session.commit()
    sent_texts, _sent_files = _capture_delivery(monkeypatch, Session)
    _message(session, data.ticket, MessageType.AGENT.value, "Personal final", data.agent.id)

    _close(session, data.ticket, data.agent)

    assert sent_texts == [f"PERSONAL {data.agent.full_name} => Snapshot title => Personal final"]


def test_internal_task_completion_base_mode_uses_basic_template_enabled_flag(monkeypatch):
    for enabled, expected_count in [(False, 0), (True, 1)]:
        Session = _session_factory()
        session = Session()
        data = _base_entities(session, pref_enabled=False)
        pref = session.execute(
            select(EmployeeNotificationPreference).where(EmployeeNotificationPreference.user_id == data.responsible.id)
        ).scalar_one()
        pref.use_base_internal_task_completion_settings = True
        pref.notify_internal_task_completion = True
        upsert_notification_bot_basic_template(
            session,
            "internal_task_completion",
            enabled=enabled,
            template="CUSTOM [Задача] => [Результат]" if enabled else "",
        )
        session.commit()
        sent_texts, _sent_files = _capture_delivery(monkeypatch, Session)
        _message(session, data.ticket, MessageType.AGENT.value, "Final from template", data.agent.id)

        _close(session, data.ticket, data.agent)

        assert len(sent_texts) == expected_count
        if enabled:
            assert sent_texts[0] == "CUSTOM Snapshot title => Final from template"


def test_internal_task_completion_delivery_gate_wins_over_enabled_base_template(monkeypatch):
    Session = _session_factory()
    session = Session()
    data = _base_entities(session, pref_enabled=False)
    pref = session.execute(
        select(EmployeeNotificationPreference).where(EmployeeNotificationPreference.user_id == data.responsible.id)
    ).scalar_one()
    pref.use_base_internal_task_completion_settings = True
    pref.notify_internal_task_completion = False
    upsert_notification_bot_basic_template(
        session,
        "internal_task_completion",
        enabled=True,
        template="CUSTOM [Задача] => [Результат]",
    )
    session.commit()
    sent_texts, sent_files = _capture_delivery(monkeypatch, Session)
    _message(session, data.ticket, MessageType.AGENT.value, "Final from disabled delivery", data.agent.id)

    _close(session, data.ticket, data.agent)

    assert sent_texts == []
    assert sent_files == []


def test_internal_task_completion_defaults_to_enabled_when_preference_row_missing(monkeypatch):
    Session = _session_factory()
    session = Session()
    data = _base_entities(session)
    session.delete(session.execute(select(EmployeeNotificationPreference).where(EmployeeNotificationPreference.user_id == data.responsible.id)).scalar_one())
    session.commit()
    sent_texts, _sent_files = _capture_delivery(monkeypatch, Session)
    _message(session, data.ticket, MessageType.AGENT.value, "Final", data.agent.id)

    _close(session, data.ticket, data.agent)

    assert len(sent_texts) == 1


def test_internal_task_completion_skips_missing_responsible_without_breaking_close(monkeypatch):
    Session = _session_factory()
    session = Session()
    data = _base_entities(session, responsible_missing=True)
    sent_texts, _sent_files = _capture_delivery(monkeypatch, Session)
    _message(session, data.ticket, MessageType.AGENT.value, "Final", data.agent.id)

    _close(session, data.ticket, data.agent)

    assert data.ticket.status == TicketStatus.CLOSED.value
    assert sent_texts == []


def test_internal_task_completion_unavailable_delivery_does_not_break_close(monkeypatch):
    Session = _session_factory()
    session = Session()
    data = _base_entities(session)
    account = session.execute(
        select(TelegramNotificationAccount).where(TelegramNotificationAccount.bound_user_id == data.responsible.id)
    ).scalar_one()
    account.delivery_available = False
    session.commit()
    sent_texts, sent_files = _capture_delivery(monkeypatch, Session)
    _message(session, data.ticket, MessageType.AGENT.value, "Final", data.agent.id)

    _close(session, data.ticket, data.agent)

    assert data.ticket.status == TicketStatus.CLOSED.value
    assert sent_texts == []
    assert sent_files == []


def test_admin_forced_internal_task_close_without_employee_result_does_not_notify(monkeypatch):
    Session = _session_factory()
    session = Session()
    data = _base_entities(session)
    sent_texts, sent_files = _capture_delivery(monkeypatch, Session)

    _close(session, data.ticket, data.responsible, force=True, is_admin=True)

    assert data.ticket.status == TicketStatus.CLOSED.value
    assert sent_texts == []
    assert sent_files == []


def test_admin_normal_internal_task_close_notifies_with_agent_result_author(monkeypatch):
    Session = _session_factory()
    session = Session()
    data = _base_entities(session)
    sent_texts, sent_files = _capture_delivery(monkeypatch, Session)
    _message(session, data.ticket, MessageType.AGENT.value, "Final by employee", data.agent.id)

    assert _close(session, data.ticket, data.responsible, force=False, is_admin=True) == "closed"

    messages = [
        message.body
        for message in session.execute(
            select(TicketMessage)
            .where(TicketMessage.ticket_id == data.ticket.id, TicketMessage.message_type == MessageType.SYSTEM.value)
            .order_by(TicketMessage.created_at.asc())
        ).scalars()
    ]
    assert data.ticket.status == TicketStatus.CLOSED.value
    assert data.ticket.closed_by_user_id == data.responsible.id
    assert data.ticket.completed_by_user_id == data.agent.id
    assert f"{data.responsible.full_name} закрыл задачу" in messages
    assert all("принудительно завершена администратором" not in message for message in messages)
    assert len(sent_texts) == 1
    assert f"{data.agent.full_name} выполнил задачу" in sent_texts[0]
    assert "Final by employee" in sent_texts[0]
    assert data.responsible.full_name not in sent_texts[0]
    assert sent_files == []


def test_ordinary_task_close_does_not_schedule_internal_completion_notification(monkeypatch):
    Session = _session_factory()
    session = Session()
    data = _ordinary_task(session)
    sent_texts, sent_files = _capture_delivery(monkeypatch, Session)
    client_texts: list[str] = []
    _message(session, data.ticket, MessageType.AGENT.value, "Ordinary final", data.agent.id)

    _close(session, data.ticket, data.agent, client_texts=client_texts)

    assert data.ticket.status == TicketStatus.CLOSED.value
    assert client_texts
    assert sent_texts == []
    assert sent_files == []


def test_internal_task_completion_notification_failure_does_not_break_close(monkeypatch):
    Session = _session_factory()
    session = Session()
    data = _base_entities(session)
    monkeypatch.setattr(notifications, "SessionLocal", Session)
    monkeypatch.setattr(notifications.settings, "employee_telegram_bot_token", "employee-token")
    monkeypatch.setattr(notifications, "send_message", lambda *_args, **_kwargs: (_ for _ in ()).throw(RuntimeError("telegram failed")))
    _message(session, data.ticket, MessageType.AGENT.value, "Final", data.agent.id)

    _close(session, data.ticket, data.agent)

    assert data.ticket.status == TicketStatus.CLOSED.value
