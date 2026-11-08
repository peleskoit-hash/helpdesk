from __future__ import annotations

from datetime import datetime
from types import SimpleNamespace
import uuid

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from api.app.db import Base
from api.app.models import (
    AutomaticMessageTemplate,
    Client,
    ClientProfile,
    Company,
    EmployeeNotificationPreference,
    MessageType,
    TelegramNotificationAccount,
    Ticket,
    TicketAttachment,
    TicketIntakeState,
    TicketMessage,
    TicketTaskKind,
    TicketStatus,
    TicketWorkflowType,
    User,
)
from api.app.routers.tickets import _assign_ticket_by_admin, move_to_special
from api.app.services import employee_telegram_notifications as notifications
from api.app.services import telegram_attachments as telegram_attachments_service
from api.app.services.telegram_employee_binding import REQUEST_STATUS_APPROVED, REQUEST_STATUS_PENDING
from api.app.services.ticket_numbering import ensure_ticket_number


def _session_factory():
    engine = create_engine(
        "sqlite://",
        future=True,
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    return sessionmaker(bind=engine, autoflush=False, autocommit=False)


def _session():
    return _session_factory()()


def _company(session) -> Company:
    company = Company(id=str(uuid.uuid4()), name="ACME", is_active=True)
    session.add(company)
    session.commit()
    return company


def _client(session, company_id: str) -> Client:
    client = Client(id=str(uuid.uuid4()), company_id=company_id, display_name="Client Display")
    profile = ClientProfile(
        id=str(uuid.uuid4()),
        client_id=client.id,
        contact_name="Ivan",
        last_name="Clientov",
        department="Support & <Ops>",
        is_verified=True,
    )
    session.add_all([client, profile])
    session.commit()
    return client


def _user(session, *, company_id: str, full_name: str, role: str = "agent", is_active: bool = True) -> User:
    now = datetime.utcnow()
    user = User(
        id=str(uuid.uuid4()),
        company_id=company_id,
        email=f"{uuid.uuid4().hex}@local.helpdesk",
        login=f"user-{uuid.uuid4().hex[:8]}",
        password_hash="hash",
        full_name=full_name,
        role=role,
        is_active=is_active,
        availability_status="online",
        created_at=now,
        updated_at=now,
    )
    session.add(user)
    session.commit()
    return user


def _ticket(
    session,
    *,
    company_id: str,
    client_id: str,
    assignee_id: str | None = None,
    ticket_number: int | None = 742,
    workflow_type: str = "ticket",
    task_kind: str = TicketTaskKind.NORMAL.value,
    parent_ticket_id: str | None = None,
) -> Ticket:
    now = datetime(2026, 8, 27, 9, 30)
    ticket = Ticket(
        id=str(uuid.uuid4()),
        ticket_number=ticket_number,
        company_id=company_id,
        client_id=client_id,
        assignee_id=assignee_id,
        subject="<b>unsafe</b> & test",
        workflow_type=workflow_type,
        intake_state=TicketIntakeState.READY.value,
        task_kind=task_kind,
        parent_ticket_id=parent_ticket_id,
        status=TicketStatus.NEW.value if assignee_id else TicketStatus.UNASSIGNED.value,
        created_at=now,
        updated_at=now,
    )
    session.add(ticket)
    session.commit()
    return ticket


def _message(
    session,
    *,
    ticket_id: str,
    body: str = "<b>hello</b> & test",
    message_type: str = "client",
    author_user_id: str | None = None,
    agent_visible: bool = True,
    replaced_message_id: str | None = None,
    telegram_message_id: str | None = None,
) -> TicketMessage:
    message = TicketMessage(
        id=str(uuid.uuid4()),
        ticket_id=ticket_id,
        message_type=message_type,
        author_user_id=author_user_id,
        body=body,
        agent_visible=agent_visible,
        telegram_message_id=telegram_message_id,
        replaced_message_id=replaced_message_id,
        created_at=datetime(2026, 8, 27, 10, 15),
    )
    session.add(message)
    session.commit()
    return message


def _attachment(
    session,
    *,
    ticket_id: str,
    message_id: str,
    source: str = "document",
    filename: str | None = "contract.pdf",
    media_group_id: str | None = None,
    telegram_message_id: str | None = None,
    created_at: datetime | None = None,
) -> TicketAttachment:
    attachment = TicketAttachment(
        id=str(uuid.uuid4()),
        ticket_id=ticket_id,
        message_id=message_id,
        source=source,
        telegram_file_id=f"file-{uuid.uuid4().hex[:8]}",
        telegram_file_unique_id=f"uniq-{uuid.uuid4().hex[:8]}",
        telegram_media_group_id=media_group_id,
        telegram_message_id=telegram_message_id,
        original_filename=filename,
        mime_type="application/octet-stream",
        file_size=123,
        local_path=f"/tmp/{uuid.uuid4().hex}",
        created_at=created_at or datetime(2026, 8, 27, 10, 15),
    )
    session.add(attachment)
    session.commit()
    return attachment


def _account(
    session,
    *,
    user_id: str | None,
    chat_id: str | None = "chat-1",
    username: str | None = "actual_recipient_username",
    request_status: str = REQUEST_STATUS_APPROVED,
    delivery_available: bool = True,
    is_blocked: bool = False,
) -> TelegramNotificationAccount:
    account = TelegramNotificationAccount(
        id=str(uuid.uuid4()),
        telegram_user_id=f"tg-{uuid.uuid4().hex[:8]}",
        telegram_chat_id=chat_id,
        telegram_username=username,
        telegram_display_name="TG",
        bound_user_id=user_id,
        request_status=request_status,
        delivery_available=delivery_available,
        is_blocked=is_blocked,
    )
    session.add(account)
    session.commit()
    return account


def _base(session, notification_type: str, body: str, *, enabled: bool = True, html_enabled: bool = True, preview: bool = False, silent: bool = False):
    row = AutomaticMessageTemplate(
        id=str(uuid.uuid4()),
        key=f"telegram.notification_bot.basic.{notification_type}",
        body=body,
        is_overridden=True,
        enabled=enabled,
        html_enabled=html_enabled,
        disable_link_preview=preview,
        send_silently=silent,
    )
    session.add(row)
    session.commit()
    return row


def _preference(session, user_id: str, **kwargs) -> EmployeeNotificationPreference:
    row = EmployeeNotificationPreference(
        id=str(uuid.uuid4()),
        user_id=user_id,
        use_base_new_assignment_settings=kwargs.get("use_base_new_assignment_settings", True),
        use_base_new_message_settings=kwargs.get("use_base_new_message_settings", True),
        personal_new_assignment_template=kwargs.get("personal_new_assignment_template"),
        personal_new_assignment_html_enabled=kwargs.get("personal_new_assignment_html_enabled", True),
        personal_new_assignment_disable_link_preview=kwargs.get("personal_new_assignment_disable_link_preview", False),
        personal_new_assignment_send_silently=kwargs.get("personal_new_assignment_send_silently", False),
        personal_new_message_template=kwargs.get("personal_new_message_template"),
        personal_new_message_html_enabled=kwargs.get("personal_new_message_html_enabled", True),
        personal_new_message_disable_link_preview=kwargs.get("personal_new_message_disable_link_preview", False),
        personal_new_message_send_silently=kwargs.get("personal_new_message_send_silently", False),
        receive_transfer_to_agent=kwargs.get("receive_transfer_to_agent", True),
        receive_transfer_from_agent=kwargs.get("receive_transfer_from_agent", True),
        receive_new_message=kwargs.get("receive_new_message", True),
    )
    session.add(row)
    session.commit()
    return row


def _assignment_event(user_id: str, kind: str = notifications.DELIVERY_KIND_NEW):
    return notifications.AssignmentNotificationEvent(
        kind=notifications.ASSIGNMENT_EVENT_NEW,
        old_assignee_id=None,
        new_assignee_id=user_id,
        deliveries=(notifications.AssignmentNotificationDelivery(kind=kind, user_id=user_id),),
    )


def test_global_disabled_and_non_business_roles_do_not_resolve_delivery():
    session = _session()
    company = _company(session)
    client = _client(session, company.id)
    agent = _user(session, company_id=company.id, full_name="Agent")
    admin = _user(session, company_id=company.id, full_name="Admin", role="admin")
    observer = _user(session, company_id=company.id, full_name="Observer", role="observer")
    ticket = _ticket(session, company_id=company.id, client_id=client.id)
    for user in (agent, admin, observer):
        _account(session, user_id=user.id, chat_id=f"chat-{user.role}")
    _base(session, "new_assignment", "disabled", enabled=False)

    assert notifications.resolve_assignment_telegram_deliveries(session, event=_assignment_event(agent.id), ticket=ticket) == ()

    _base(session, "transfer_to_agent", "to")
    assert notifications.resolve_assignment_telegram_deliveries(
        session,
        event=_assignment_event(admin.id, notifications.DELIVERY_KIND_TRANSFER_TO),
        ticket=ticket,
    ) == ()
    assert notifications.resolve_assignment_telegram_deliveries(
        session,
        event=_assignment_event(observer.id, notifications.DELIVERY_KIND_TRANSFER_TO),
        ticket=ticket,
    ) == ()


def test_agent_and_trainee_with_approved_binding_are_allowed_but_blocked_unavailable_pending_unbound_are_not():
    session = _session()
    company = _company(session)
    client = _client(session, company.id)
    ticket = _ticket(session, company_id=company.id, client_id=client.id)
    _base(session, "new_assignment", "hello")
    agent = _user(session, company_id=company.id, full_name="Agent")
    trainee = _user(session, company_id=company.id, full_name="Trainee", role="trainee")
    blocked = _user(session, company_id=company.id, full_name="Blocked")
    unavailable = _user(session, company_id=company.id, full_name="Unavailable")
    pending = _user(session, company_id=company.id, full_name="Pending")
    unbound = _user(session, company_id=company.id, full_name="Unbound")
    _account(session, user_id=agent.id, chat_id="chat-agent")
    _account(session, user_id=trainee.id, chat_id="chat-trainee")
    _account(session, user_id=blocked.id, is_blocked=True)
    _account(session, user_id=unavailable.id, delivery_available=False)
    _account(session, user_id=pending.id, request_status=REQUEST_STATUS_PENDING)
    _account(session, user_id=None, request_status=REQUEST_STATUS_PENDING)

    assert len(notifications.resolve_assignment_telegram_deliveries(session, event=_assignment_event(agent.id), ticket=ticket)) == 1
    assert len(notifications.resolve_assignment_telegram_deliveries(session, event=_assignment_event(trainee.id), ticket=ticket)) == 1
    for user in (blocked, unavailable, pending, unbound):
        assert notifications.resolve_assignment_telegram_deliveries(session, event=_assignment_event(user.id), ticket=ticket) == ()


def test_effective_settings_flags_tags_escaping_and_recipient_username(monkeypatch):
    monkeypatch.setattr(notifications.settings, "app_public_base_url", "https://desk.example.test/base/")
    session = _session()
    company = _company(session)
    client = _client(session, company.id)
    agent = _user(session, company_id=company.id, full_name="Agent")
    ticket = _ticket(session, company_id=company.id, client_id=client.id, assignee_id=agent.id)
    _account(session, user_id=agent.id, username="actual_recipient_username")
    _base(
        session,
        "new_assignment",
        "<b>[Номер заявки]</b> [Тип заявки] [Заголовок заявки] [Автор заявки/задачи] [Подразделение автора] [Дата создания] [Ссылка на заявку] [TG @username адресата]",
        preview=True,
        silent=True,
    )

    delivery = notifications.resolve_assignment_telegram_deliveries(session, event=_assignment_event(agent.id), ticket=ticket)[0]

    assert delivery.parse_mode == "HTML"
    assert delivery.disable_link_preview is True
    assert delivery.send_silently is True
    assert "742" in delivery.text
    assert "заявка" in delivery.text
    assert "&lt;b&gt;unsafe&lt;/b&gt; &amp; test" in delivery.text
    assert "Clientov Ivan" in delivery.text
    assert "Support &amp; &lt;Ops&gt;" in delivery.text
    assert "27.08.2026" in delivery.text
    assert "https://desk.example.test/base/tickets/742" in delivery.text
    assert ticket.id not in delivery.text
    assert "@actual_recipient_username" in delivery.text
    assert "[Номер заявки]" not in delivery.text
    assert "[Заголовок заявки]" not in delivery.text
    assert "[TG @username адресата]" not in delivery.text


def test_scheduling_uses_existing_ticket_number_allocator_before_runtime_delivery():
    session = _session()
    company = _company(session)
    client = _client(session, company.id)
    agent = _user(session, company_id=company.id, full_name="Agent")
    ticket = _ticket(session, company_id=company.id, client_id=client.id, assignee_id=agent.id)
    ticket.ticket_number = None
    session.commit()
    ensure_ticket_number(session, ticket)

    notifications.schedule_assignment_notification(
        session,
        old_assignee_id=None,
        new_assignee_id=agent.id,
        ticket=ticket,
    )

    assert ticket.ticket_number == 1
    assert session.info[notifications._PENDING_ASSIGNMENT_NOTIFICATIONS_KEY][0]["ticket_id"] == ticket.id


def test_assignment_scheduler_does_not_mask_type_errors(monkeypatch):
    session = _session()
    company = _company(session)
    client = _client(session, company.id)
    agent = _user(session, company_id=company.id, full_name="Agent")
    ticket = _ticket(session, company_id=company.id, client_id=client.id, assignee_id=agent.id)

    def broken_builder(**kwargs):
        raise TypeError("real production error")

    monkeypatch.setattr(notifications, "build_assignment_notification_event", broken_builder)

    with pytest.raises(TypeError, match="real production error"):
        notifications.schedule_assignment_notification(
            session,
            old_assignee_id=None,
            new_assignee_id=agent.id,
            ticket=ticket,
        )


def test_ticket_link_uses_empty_semantics_without_public_base_url(monkeypatch):
    monkeypatch.setattr(notifications.settings, "app_public_base_url", "")
    session = _session()
    company = _company(session)
    client = _client(session, company.id)
    agent = _user(session, company_id=company.id, full_name="Agent")
    ticket = _ticket(session, company_id=company.id, client_id=client.id, assignee_id=agent.id)
    _account(session, user_id=agent.id)
    _base(session, "new_assignment", "Link=[Ссылка на заявку]")

    delivery = notifications.resolve_assignment_telegram_deliveries(session, event=_assignment_event(agent.id), ticket=ticket)[0]

    assert delivery.text == "Link=не задано"


def test_ticket_link_uses_ticket_number_not_uuid(monkeypatch):
    monkeypatch.setattr(notifications.settings, "app_public_base_url", "https://desk.example.test")
    session = _session()
    company = _company(session)
    client = _client(session, company.id)
    agent = _user(session, company_id=company.id, full_name="Assigned Agent")
    ticket = _ticket(session, company_id=company.id, client_id=client.id, assignee_id=agent.id)
    ticket.ticket_number = 502
    _account(session, user_id=agent.id)
    _base(session, "new_assignment", "[Ссылка на заявку]")

    delivery = notifications.resolve_assignment_telegram_deliveries(session, event=_assignment_event(agent.id), ticket=ticket)[0]

    assert delivery.text == "https://desk.example.test/tickets/502"
    assert ticket.id not in delivery.text


def test_plain_text_dynamic_values_are_not_html_escaped(monkeypatch):
    monkeypatch.setattr(notifications.settings, "app_public_base_url", "https://desk.example.test")
    session = _session()
    company = _company(session)
    client = _client(session, company.id)
    agent = _user(session, company_id=company.id, full_name="Agent")
    ticket = _ticket(session, company_id=company.id, client_id=client.id, assignee_id=agent.id)
    _account(session, user_id=agent.id)
    _base(session, "new_assignment", "[Заголовок заявки] [Подразделение автора] [Ссылка на заявку]", html_enabled=False)

    delivery = notifications.resolve_assignment_telegram_deliveries(session, event=_assignment_event(agent.id), ticket=ticket)[0]

    assert delivery.parse_mode is None
    assert "<b>unsafe</b> & test" in delivery.text
    assert "Support & <Ops>" in delivery.text
    assert "https://desk.example.test/tickets/742" in delivery.text


def test_dynamic_tag_like_values_are_not_substituted_second_time():
    session = _session()
    company = _company(session)
    client = _client(session, company.id)
    agent = _user(session, company_id=company.id, full_name="Agent")
    ticket = _ticket(session, company_id=company.id, client_id=client.id, assignee_id=agent.id)
    ticket.subject = "[Автор сообщения] [TG @username адресата] [Мой TG @username]"
    session.commit()
    _account(session, user_id=agent.id, username="actual_user")
    _base(session, "new_assignment", "Title: [Заголовок заявки]")

    delivery = notifications.resolve_assignment_telegram_deliveries(session, event=_assignment_event(agent.id), ticket=ticket)[0]

    assert delivery.text == "Title: [Автор сообщения] [TG @username адресата] [Мой TG @username]"
    assert "@actual_user" not in delivery.text


def test_message_body_tag_like_values_are_not_substituted_second_time():
    session = _session()
    company = _company(session)
    client = _client(session, company.id)
    agent = _user(session, company_id=company.id, full_name="Agent")
    ticket = _ticket(session, company_id=company.id, client_id=client.id, assignee_id=agent.id)
    message = _message(session, ticket_id=ticket.id, body="[Заголовок заявки] [TG @username адресата]")
    _account(session, user_id=agent.id, username="actual_user")
    _base(session, "new_message", "Body: [Тело сообщения]")

    delivery = notifications.resolve_new_message_telegram_delivery(
        session,
        ticket_id=ticket.id,
        message_id=message.id,
        recipient_user_id=agent.id,
    )

    assert delivery is not None
    assert delivery.text == "Body: [Заголовок заявки] [TG @username адресата]"
    assert "@actual_user" not in delivery.text


def test_single_file_message_body_uses_attachment_filename_without_empty_placeholder():
    session = _session()
    company = _company(session)
    client = _client(session, company.id)
    agent = _user(session, company_id=company.id, full_name="Agent")
    ticket = _ticket(session, company_id=company.id, client_id=client.id, assignee_id=agent.id)
    message = _message(session, ticket_id=ticket.id, body="")
    _attachment(session, ticket_id=ticket.id, message_id=message.id, filename="contract.pdf")
    _account(session, user_id=agent.id)
    _base(session, "new_message", "[Тело сообщения]")

    delivery = notifications.resolve_new_message_telegram_delivery(
        session,
        ticket_id=ticket.id,
        message_id=message.id,
        recipient_user_id=agent.id,
    )

    assert delivery is not None
    assert delivery.text == "Файлы:\ncontract.pdf"
    assert "не задано" not in delivery.text


def test_text_and_file_message_body_uses_text_once_then_file_block():
    session = _session()
    company = _company(session)
    client = _client(session, company.id)
    agent = _user(session, company_id=company.id, full_name="Agent")
    ticket = _ticket(session, company_id=company.id, client_id=client.id, assignee_id=agent.id)
    message = _message(session, ticket_id=ticket.id, body="Посмотрите договор")
    _attachment(session, ticket_id=ticket.id, message_id=message.id, filename="contract.pdf")
    _account(session, user_id=agent.id)
    _base(session, "new_message", "[Тело сообщения]")

    delivery = notifications.resolve_new_message_telegram_delivery(
        session,
        ticket_id=ticket.id,
        message_id=message.id,
        recipient_user_id=agent.id,
    )

    assert delivery is not None
    assert delivery.text == "Посмотрите договор\n\nФайлы:\ncontract.pdf"


def test_message_body_text_limit_boundaries_199_200_201():
    session = _session()
    company = _company(session)
    client = _client(session, company.id)
    agent = _user(session, company_id=company.id, full_name="Agent")
    ticket = _ticket(session, company_id=company.id, client_id=client.id, assignee_id=agent.id)
    messages = [
        _message(session, ticket_id=ticket.id, body="a" * 199),
        _message(session, ticket_id=ticket.id, body="b" * 200),
        _message(session, ticket_id=ticket.id, body="c" * 201),
    ]
    _account(session, user_id=agent.id)
    _base(session, "new_message", "[Тело сообщения]")

    rendered = [
        notifications.resolve_new_message_telegram_delivery(
            session,
            ticket_id=ticket.id,
            message_id=message.id,
            recipient_user_id=agent.id,
        ).text
        for message in messages
    ]

    assert rendered[0] == "a" * 199
    assert rendered[1] == "b" * 200
    assert rendered[2] == ("c" * 200) + "…"


def test_long_text_and_file_keeps_full_file_block_outside_text_limit():
    session = _session()
    company = _company(session)
    client = _client(session, company.id)
    agent = _user(session, company_id=company.id, full_name="Agent")
    ticket = _ticket(session, company_id=company.id, client_id=client.id, assignee_id=agent.id)
    message = _message(session, ticket_id=ticket.id, body="x" * 201)
    _attachment(session, ticket_id=ticket.id, message_id=message.id, filename="contract.pdf")
    _account(session, user_id=agent.id)
    _base(session, "new_message", "[Тело сообщения]")

    delivery = notifications.resolve_new_message_telegram_delivery(
        session,
        ticket_id=ticket.id,
        message_id=message.id,
        recipient_user_id=agent.id,
    )

    assert delivery is not None
    assert delivery.text == ("x" * 200) + "…\n\nФайлы:\ncontract.pdf"


def test_attachment_message_body_uses_source_not_extension_for_photo_video_document():
    session = _session()
    company = _company(session)
    client = _client(session, company.id)
    agent = _user(session, company_id=company.id, full_name="Agent")
    ticket = _ticket(session, company_id=company.id, client_id=client.id, assignee_id=agent.id)
    photo = _message(session, ticket_id=ticket.id, body="")
    document_photo = _message(session, ticket_id=ticket.id, body="")
    _attachment(session, ticket_id=ticket.id, message_id=photo.id, source="photo", filename="photo.jpg")
    _attachment(session, ticket_id=ticket.id, message_id=document_photo.id, source="document", filename="image.jpg")
    _account(session, user_id=agent.id)
    _base(session, "new_message", "[Тело сообщения]")

    photo_delivery = notifications.resolve_new_message_telegram_delivery(
        session,
        ticket_id=ticket.id,
        message_id=photo.id,
        recipient_user_id=agent.id,
    )
    document_delivery = notifications.resolve_new_message_telegram_delivery(
        session,
        ticket_id=ticket.id,
        message_id=document_photo.id,
        recipient_user_id=agent.id,
    )

    assert photo_delivery is not None
    assert photo_delivery.text == "Файлы:\nФото"
    assert document_delivery is not None
    assert document_delivery.text == "Файлы:\nimage.jpg"


def test_real_store_path_persists_attachment_source_used_by_notification_labels(monkeypatch, tmp_path):
    monkeypatch.setattr(telegram_attachments_service.settings, "attachment_storage_dir", str(tmp_path))
    monkeypatch.setattr(
        telegram_attachments_service,
        "get_file",
        lambda file_id, **_kwargs: {"result": {"file_path": f"stored/{file_id}.bin", "file_unique_id": f"uniq-{file_id}"}},
    )
    session = _session()
    company = _company(session)
    client = _client(session, company.id)
    agent = _user(session, company_id=company.id, full_name="Agent")
    ticket = _ticket(session, company_id=company.id, client_id=client.id, assignee_id=agent.id)
    photo_message = _message(session, ticket_id=ticket.id, body="")
    video_message = _message(session, ticket_id=ticket.id, body="")
    document_message = _message(session, ticket_id=ticket.id, body="")
    photo_data = telegram_attachments_service._extract_attachment(
        {
            "photo": [
                {"file_id": "photo-small", "file_unique_id": "uniq-photo-small", "file_size": 10, "width": 10, "height": 10},
                {"file_id": "photo-main", "file_unique_id": "uniq-photo-main", "file_size": 100, "width": 100, "height": 100},
            ],
        }
    )
    video_data = telegram_attachments_service._extract_attachment(
        {
            "video": {
                "file_id": "video-main",
                "file_unique_id": "uniq-video-main",
                "file_size": 100,
                "mime_type": "video/mp4",
                "file_name": "clip.mp4",
            },
        }
    )
    document_data = telegram_attachments_service._extract_attachment(
        {
            "document": {
                "file_id": "document-main",
                "file_unique_id": "uniq-document-main",
                "file_size": 100,
                "mime_type": "image/jpeg",
                "file_name": "image.jpg",
            },
        }
    )

    photo_attachment = telegram_attachments_service._store_telegram_attachment(session, ticket, photo_message, photo_data, "chat-1")
    video_attachment = telegram_attachments_service._store_telegram_attachment(session, ticket, video_message, video_data, "chat-1")
    document_attachment = telegram_attachments_service._store_telegram_attachment(session, ticket, document_message, document_data, "chat-1")
    session.commit()
    _account(session, user_id=agent.id)
    _base(session, "new_message", "[Тело сообщения]")

    assert photo_attachment.source == "photo"
    assert video_attachment.source == "video"
    assert document_attachment.source == "document"
    assert document_attachment.original_filename == "image.jpg"
    photo_delivery = notifications.resolve_new_message_telegram_delivery(
        session,
        ticket_id=ticket.id,
        message_id=photo_message.id,
        recipient_user_id=agent.id,
    )
    video_delivery = notifications.resolve_new_message_telegram_delivery(
        session,
        ticket_id=ticket.id,
        message_id=video_message.id,
        recipient_user_id=agent.id,
    )
    document_delivery = notifications.resolve_new_message_telegram_delivery(
        session,
        ticket_id=ticket.id,
        message_id=document_message.id,
        recipient_user_id=agent.id,
    )
    assert photo_delivery is not None
    assert photo_delivery.text == "Файлы:\nФото"
    assert video_delivery is not None
    assert video_delivery.text == "Файлы:\nВидео"
    assert document_delivery is not None
    assert document_delivery.text == "Файлы:\nimage.jpg"


def test_media_group_body_combines_parts_in_source_order_and_deduplicates_caption():
    session = _session()
    company = _company(session)
    client = _client(session, company.id)
    agent = _user(session, company_id=company.id, full_name="Agent")
    ticket = _ticket(session, company_id=company.id, client_id=client.id, assignee_id=agent.id)
    messages = [
        _message(session, ticket_id=ticket.id, body="Посмотрите", telegram_message_id="101"),
        _message(session, ticket_id=ticket.id, body="", telegram_message_id="102"),
        _message(session, ticket_id=ticket.id, body="Посмотрите", telegram_message_id="103"),
        _message(session, ticket_id=ticket.id, body="", telegram_message_id="104"),
    ]
    _attachment(session, ticket_id=ticket.id, message_id=messages[2].id, source="photo", filename="photo.jpg", media_group_id="mg-1")
    _attachment(session, ticket_id=ticket.id, message_id=messages[0].id, source="photo", filename="photo.jpg", media_group_id="mg-1")
    _attachment(session, ticket_id=ticket.id, message_id=messages[1].id, source="photo", filename="photo.jpg", media_group_id="mg-1")
    _attachment(session, ticket_id=ticket.id, message_id=messages[3].id, source="video", filename="video.mp4", media_group_id="mg-1")
    _account(session, user_id=agent.id)
    _base(session, "new_message", "[Тело сообщения]")

    delivery = notifications.resolve_media_group_new_message_telegram_delivery(
        session,
        ticket_id=ticket.id,
        media_group_id="mg-1",
    )

    assert delivery is not None
    assert delivery.text == "Посмотрите\n\nФайлы:\nФото\nФото\nФото\nВидео"


def test_long_media_group_caption_is_truncated_but_file_block_is_complete():
    session = _session()
    company = _company(session)
    client = _client(session, company.id)
    agent = _user(session, company_id=company.id, full_name="Agent")
    ticket = _ticket(session, company_id=company.id, client_id=client.id, assignee_id=agent.id)
    first = _message(session, ticket_id=ticket.id, body="m" * 201, telegram_message_id="101")
    second = _message(session, ticket_id=ticket.id, body="", telegram_message_id="102")
    third = _message(session, ticket_id=ticket.id, body="", telegram_message_id="103")
    _attachment(session, ticket_id=ticket.id, message_id=first.id, source="photo", media_group_id="mg-long")
    _attachment(session, ticket_id=ticket.id, message_id=second.id, source="photo", media_group_id="mg-long")
    _attachment(session, ticket_id=ticket.id, message_id=third.id, source="video", media_group_id="mg-long")
    _account(session, user_id=agent.id)
    _base(session, "new_message", "[Тело сообщения]")

    delivery = notifications.resolve_media_group_new_message_telegram_delivery(
        session,
        ticket_id=ticket.id,
        media_group_id="mg-long",
    )

    assert delivery is not None
    assert delivery.text == ("m" * 200) + "…\n\nФайлы:\nФото\nФото\nВидео"


def test_message_body_html_escapes_composite_body_and_plain_mode_does_not():
    session = _session()
    company = _company(session)
    client = _client(session, company.id)
    agent = _user(session, company_id=company.id, full_name="Agent")
    ticket = _ticket(session, company_id=company.id, client_id=client.id, assignee_id=agent.id)
    html_message = _message(session, ticket_id=ticket.id, body="<b>caption</b> & test")
    plain_message = _message(session, ticket_id=ticket.id, body="<b>caption</b> & test")
    _attachment(session, ticket_id=ticket.id, message_id=html_message.id, filename="<contract>&.pdf")
    _attachment(session, ticket_id=ticket.id, message_id=plain_message.id, filename="<contract>&.pdf")
    _account(session, user_id=agent.id)
    _base(session, "new_message", "[Тело сообщения]", html_enabled=True)

    html_delivery = notifications.resolve_new_message_telegram_delivery(
        session,
        ticket_id=ticket.id,
        message_id=html_message.id,
        recipient_user_id=agent.id,
    )
    session.query(AutomaticMessageTemplate).delete()
    session.commit()
    _base(session, "new_message", "[Тело сообщения]", html_enabled=False)
    plain_delivery = notifications.resolve_new_message_telegram_delivery(
        session,
        ticket_id=ticket.id,
        message_id=plain_message.id,
        recipient_user_id=agent.id,
    )

    assert html_delivery is not None
    assert html_delivery.text == "&lt;b&gt;caption&lt;/b&gt; &amp; test\n\nФайлы:\n&lt;contract&gt;&amp;.pdf"
    assert plain_delivery is not None
    assert plain_delivery.text == "<b>caption</b> & test\n\nФайлы:\n<contract>&.pdf"


def test_message_body_limit_happens_before_html_escaping():
    session = _session()
    company = _company(session)
    client = _client(session, company.id)
    agent = _user(session, company_id=company.id, full_name="Agent")
    ticket = _ticket(session, company_id=company.id, client_id=client.id, assignee_id=agent.id)
    raw_text = "<" * 201
    message = _message(session, ticket_id=ticket.id, body=raw_text)
    _account(session, user_id=agent.id)
    _base(session, "new_message", "[Тело сообщения]", html_enabled=True)

    delivery = notifications.resolve_new_message_telegram_delivery(
        session,
        ticket_id=ticket.id,
        message_id=message.id,
        recipient_user_id=agent.id,
    )

    assert delivery is not None
    assert delivery.text == ("&lt;" * 200) + "…"


def test_message_body_limit_plain_mode_does_not_escape_entities():
    session = _session()
    company = _company(session)
    client = _client(session, company.id)
    agent = _user(session, company_id=company.id, full_name="Agent")
    ticket = _ticket(session, company_id=company.id, client_id=client.id, assignee_id=agent.id)
    raw_text = "&" * 201
    message = _message(session, ticket_id=ticket.id, body=raw_text)
    _account(session, user_id=agent.id)
    _base(session, "new_message", "[Тело сообщения]", html_enabled=False)

    delivery = notifications.resolve_new_message_telegram_delivery(
        session,
        ticket_id=ticket.id,
        message_id=message.id,
        recipient_user_id=agent.id,
    )

    assert delivery is not None
    assert delivery.text == ("&" * 200) + "…"


def test_scheduling_single_attachment_is_immediate_but_media_group_is_debounced():
    session = _session()
    company = _company(session)
    client = _client(session, company.id)
    agent = _user(session, company_id=company.id, full_name="Agent")
    ticket = _ticket(session, company_id=company.id, client_id=client.id, assignee_id=agent.id)
    single_message = _message(session, ticket_id=ticket.id, body="")
    group_message = _message(session, ticket_id=ticket.id, body="")
    single_attachment = _attachment(session, ticket_id=ticket.id, message_id=single_message.id, filename="contract.pdf")
    group_attachment = _attachment(session, ticket_id=ticket.id, message_id=group_message.id, filename="photo.jpg", media_group_id="mg-1")

    notifications.schedule_new_message_notification(session, ticket=ticket, message=single_message, attachment=single_attachment)
    notifications.schedule_new_message_notification(session, ticket=ticket, message=group_message, attachment=group_attachment)

    assert len(session.info[notifications._PENDING_MESSAGE_NOTIFICATIONS_KEY]) == 1
    assert session.info[notifications._PENDING_MESSAGE_NOTIFICATIONS_KEY][0]["message_id"] == single_message.id
    assert len(session.info[notifications._PENDING_MEDIA_GROUP_NOTIFICATIONS_KEY]) == 1
    assert session.info[notifications._PENDING_MEDIA_GROUP_NOTIFICATIONS_KEY][0]["media_group_id"] == "mg-1"


def test_text_only_message_schedules_one_immediate_notification():
    session = _session()
    company = _company(session)
    client = _client(session, company.id)
    agent = _user(session, company_id=company.id, full_name="Agent")
    ticket = _ticket(session, company_id=company.id, client_id=client.id, assignee_id=agent.id)
    message = _message(session, ticket_id=ticket.id, body="plain text")

    notifications.schedule_new_message_notification(session, ticket=ticket, message=message)

    assert [item["message_id"] for item in session.info[notifications._PENDING_MESSAGE_NOTIFICATIONS_KEY]] == [message.id]
    assert notifications._PENDING_MEDIA_GROUP_NOTIFICATIONS_KEY not in session.info


def test_regular_ticket_new_message_runtime_delivery_still_sends(monkeypatch):
    Session = _session_factory()
    session = Session()
    company = _company(session)
    client = _client(session, company.id)
    agent = _user(session, company_id=company.id, full_name="Agent")
    ticket = _ticket(session, company_id=company.id, client_id=client.id, assignee_id=agent.id, workflow_type=TicketWorkflowType.TICKET.value)
    message = TicketMessage(
        id=str(uuid.uuid4()),
        ticket_id=ticket.id,
        message_type=MessageType.CLIENT.value,
        author_user_id=None,
        body="client ping",
        agent_visible=True,
        created_at=datetime(2026, 8, 27, 10, 15),
    )
    session.add(message)
    session.flush()
    _account(session, user_id=agent.id, chat_id="chat-agent")
    _base(session, "new_message", "[Тело сообщения]")
    sent = []

    monkeypatch.setattr(notifications, "SessionLocal", Session)
    monkeypatch.setattr(notifications.settings, "employee_telegram_bot_token", "employee-token")
    monkeypatch.setattr(notifications, "send_message", lambda chat_id, text, **kwargs: sent.append((chat_id, text)) or {"ok": True})

    notifications.schedule_new_message_notification(session, ticket=ticket, message=message)
    session.commit()

    assert sent == [("chat-agent", "client ping")]


def test_regular_task_assignment_runtime_delivery_still_sends(monkeypatch):
    Session = _session_factory()
    session = Session()
    company = _company(session)
    client = _client(session, company.id)
    agent = _user(session, company_id=company.id, full_name="Task Agent")
    task = _ticket(session, company_id=company.id, client_id=client.id, assignee_id=agent.id, workflow_type=TicketWorkflowType.TASK.value)
    _account(session, user_id=agent.id, chat_id="chat-agent")
    _base(session, "new_assignment", "Assigned [Номер заявки]")
    sent = []

    monkeypatch.setattr(notifications, "SessionLocal", Session)
    monkeypatch.setattr(notifications.settings, "employee_telegram_bot_token", "employee-token")
    monkeypatch.setattr(notifications, "send_message", lambda chat_id, text, **kwargs: sent.append((chat_id, text)) or {"ok": True})

    notifications.schedule_assignment_notification(
        session,
        old_assignee_id=None,
        new_assignee_id=agent.id,
        ticket=task,
    )
    session.commit()

    assert sent == [("chat-agent", "Assigned 742")]


def test_two_standalone_attachment_messages_schedule_two_notifications():
    session = _session()
    company = _company(session)
    client = _client(session, company.id)
    agent = _user(session, company_id=company.id, full_name="Agent")
    ticket = _ticket(session, company_id=company.id, client_id=client.id, assignee_id=agent.id)
    first = _message(session, ticket_id=ticket.id, body="")
    second = _message(session, ticket_id=ticket.id, body="")
    first_attachment = _attachment(session, ticket_id=ticket.id, message_id=first.id, filename="first.pdf")
    second_attachment = _attachment(session, ticket_id=ticket.id, message_id=second.id, filename="second.pdf")

    notifications.schedule_new_message_notification(session, ticket=ticket, message=first, attachment=first_attachment)
    notifications.schedule_new_message_notification(session, ticket=ticket, message=second, attachment=second_attachment)

    assert [item["message_id"] for item in session.info[notifications._PENDING_MESSAGE_NOTIFICATIONS_KEY]] == [first.id, second.id]
    assert notifications._PENDING_MEDIA_GROUP_NOTIFICATIONS_KEY not in session.info


def test_group_parent_assignment_runtime_delivery_is_preserved(monkeypatch):
    Session = _session_factory()
    session = Session()
    company = _company(session)
    client = _client(session, company.id)
    agent = _user(session, company_id=company.id, full_name="Group Parent Agent")
    parent = _ticket(
        session,
        company_id=company.id,
        client_id=client.id,
        assignee_id=agent.id,
        workflow_type=TicketWorkflowType.TASK.value,
        task_kind=TicketTaskKind.GROUP_PARENT.value,
    )
    _account(session, user_id=agent.id, chat_id="chat-agent")
    _base(session, "new_assignment", "Parent [Номер заявки]")
    sent = []

    monkeypatch.setattr(notifications, "SessionLocal", Session)
    monkeypatch.setattr(notifications.settings, "employee_telegram_bot_token", "employee-token")
    monkeypatch.setattr(notifications, "send_message", lambda chat_id, text, **kwargs: sent.append((chat_id, text)) or {"ok": True})

    notifications.schedule_assignment_notification(
        session,
        old_assignee_id=None,
        new_assignee_id=agent.id,
        ticket=parent,
    )
    session.commit()

    assert sent == [("chat-agent", "Parent 742")]


def test_group_child_new_message_runtime_delivery_is_suppressed(monkeypatch):
    Session = _session_factory()
    session = Session()
    company = _company(session)
    client = _client(session, company.id)
    agent = _user(session, company_id=company.id, full_name="Child Agent")
    parent = _ticket(
        session,
        company_id=company.id,
        client_id=client.id,
        ticket_number=900,
        workflow_type=TicketWorkflowType.TASK.value,
        task_kind=TicketTaskKind.GROUP_PARENT.value,
    )
    child = _ticket(
        session,
        company_id=company.id,
        client_id=client.id,
        assignee_id=agent.id,
        ticket_number=901,
        workflow_type=TicketWorkflowType.TASK.value,
        task_kind=TicketTaskKind.GROUP_CHILD.value,
        parent_ticket_id=parent.id,
    )
    message = TicketMessage(
        id=str(uuid.uuid4()),
        ticket_id=child.id,
        message_type=MessageType.CLIENT.value,
        author_user_id=None,
        body="child client ping",
        agent_visible=True,
        created_at=datetime(2026, 8, 27, 10, 15),
    )
    session.add(message)
    session.flush()
    _account(session, user_id=agent.id, chat_id="chat-agent")
    _base(session, "new_message", "[Тело сообщения]")
    sent = []

    monkeypatch.setattr(notifications, "SessionLocal", Session)
    monkeypatch.setattr(notifications.settings, "employee_telegram_bot_token", "employee-token")
    monkeypatch.setattr(notifications, "send_message", lambda chat_id, text, **kwargs: sent.append((chat_id, text)) or {"ok": True})

    notifications.schedule_new_message_notification(session, ticket=child, message=message)
    session.commit()

    assert sent == []


def test_group_child_assignment_runtime_delivery_is_suppressed(monkeypatch):
    Session = _session_factory()
    session = Session()
    company = _company(session)
    client = _client(session, company.id)
    agent = _user(session, company_id=company.id, full_name="Child Agent")
    parent = _ticket(
        session,
        company_id=company.id,
        client_id=client.id,
        ticket_number=910,
        workflow_type=TicketWorkflowType.TASK.value,
        task_kind=TicketTaskKind.GROUP_PARENT.value,
    )
    child = _ticket(
        session,
        company_id=company.id,
        client_id=client.id,
        assignee_id=agent.id,
        ticket_number=911,
        workflow_type=TicketWorkflowType.TASK.value,
        task_kind=TicketTaskKind.GROUP_CHILD.value,
        parent_ticket_id=parent.id,
    )
    _account(session, user_id=agent.id, chat_id="chat-agent")
    _base(session, "new_assignment", "Child [Номер заявки]")
    sent = []

    monkeypatch.setattr(notifications, "SessionLocal", Session)
    monkeypatch.setattr(notifications.settings, "employee_telegram_bot_token", "employee-token")
    monkeypatch.setattr(notifications, "send_message", lambda chat_id, text, **kwargs: sent.append((chat_id, text)) or {"ok": True})

    notifications.schedule_assignment_notification(
        session,
        old_assignee_id=None,
        new_assignee_id=agent.id,
        ticket=child,
    )
    session.commit()

    assert sent == []


def test_group_child_transfer_runtime_delivery_is_preserved(monkeypatch):
    Session = _session_factory()
    session = Session()
    company = _company(session)
    client = _client(session, company.id)
    agent_a = _user(session, company_id=company.id, full_name="Agent A")
    agent_b = _user(session, company_id=company.id, full_name="Agent B")
    parent = _ticket(
        session,
        company_id=company.id,
        client_id=client.id,
        ticket_number=915,
        workflow_type=TicketWorkflowType.TASK.value,
        task_kind=TicketTaskKind.GROUP_PARENT.value,
    )
    child = _ticket(
        session,
        company_id=company.id,
        client_id=client.id,
        assignee_id=agent_b.id,
        ticket_number=916,
        workflow_type=TicketWorkflowType.TASK.value,
        task_kind=TicketTaskKind.GROUP_CHILD.value,
        parent_ticket_id=parent.id,
    )
    _account(session, user_id=agent_a.id, chat_id="chat-a")
    _account(session, user_id=agent_b.id, chat_id="chat-b")
    _base(session, "transfer_to_agent", "To [Предыдущий агент]")
    _base(session, "transfer_from_agent", "From [Куда передана заявка]")
    sent = []

    monkeypatch.setattr(notifications, "SessionLocal", Session)
    monkeypatch.setattr(notifications.settings, "employee_telegram_bot_token", "employee-token")
    monkeypatch.setattr(notifications, "send_message", lambda chat_id, text, **kwargs: sent.append((chat_id, text)) or {"ok": True})

    notifications.schedule_assignment_notification(
        session,
        old_assignee_id=agent_a.id,
        new_assignee_id=agent_b.id,
        ticket=child,
    )
    session.commit()

    assert sent == [
        ("chat-b", "To Agent A"),
        ("chat-a", "From Agent B"),
    ]


def test_multiple_group_child_messages_do_not_create_employee_spam(monkeypatch):
    Session = _session_factory()
    session = Session()
    company = _company(session)
    client = _client(session, company.id)
    agent = _user(session, company_id=company.id, full_name="Child Agent")
    parent = _ticket(
        session,
        company_id=company.id,
        client_id=client.id,
        ticket_number=920,
        workflow_type=TicketWorkflowType.TASK.value,
        task_kind=TicketTaskKind.GROUP_PARENT.value,
    )
    first_child = _ticket(
        session,
        company_id=company.id,
        client_id=client.id,
        assignee_id=agent.id,
        ticket_number=921,
        workflow_type=TicketWorkflowType.TASK.value,
        task_kind=TicketTaskKind.GROUP_CHILD.value,
        parent_ticket_id=parent.id,
    )
    second_child = _ticket(
        session,
        company_id=company.id,
        client_id=client.id,
        assignee_id=agent.id,
        ticket_number=922,
        workflow_type=TicketWorkflowType.TASK.value,
        task_kind=TicketTaskKind.GROUP_CHILD.value,
        parent_ticket_id=parent.id,
    )
    first_message = TicketMessage(
        id=str(uuid.uuid4()),
        ticket_id=first_child.id,
        message_type=MessageType.CLIENT.value,
        author_user_id=None,
        body="child one",
        agent_visible=True,
        created_at=datetime(2026, 8, 27, 10, 15),
    )
    second_message = TicketMessage(
        id=str(uuid.uuid4()),
        ticket_id=second_child.id,
        message_type=MessageType.CLIENT.value,
        author_user_id=None,
        body="child two",
        agent_visible=True,
        created_at=datetime(2026, 8, 27, 10, 16),
    )
    session.add_all([first_message, second_message])
    session.flush()
    _account(session, user_id=agent.id, chat_id="chat-agent")
    _base(session, "new_message", "[Тело сообщения]")
    sent = []

    monkeypatch.setattr(notifications, "SessionLocal", Session)
    monkeypatch.setattr(notifications.settings, "employee_telegram_bot_token", "employee-token")
    monkeypatch.setattr(notifications, "send_message", lambda chat_id, text, **kwargs: sent.append((chat_id, text)) or {"ok": True})

    notifications.schedule_new_message_notification(session, ticket=first_child, message=first_message)
    notifications.schedule_new_message_notification(session, ticket=second_child, message=second_message)
    session.commit()

    assert sent == []


def test_media_group_debounce_resets_timer_sends_once_and_cleans_registry(monkeypatch):
    class FakeTimer:
        instances = []

        def __init__(self, interval, function, args=(), kwargs=None):
            self.interval = interval
            self.function = function
            self.args = args
            self.kwargs = kwargs or {}
            self.daemon = False
            self.cancelled = False
            FakeTimer.instances.append(self)

        def start(self):
            return None

        def cancel(self):
            self.cancelled = True

        def fire(self):
            if not self.cancelled:
                self.function(*self.args, **self.kwargs)

        def force_fire(self):
            self.function(*self.args, **self.kwargs)

    Session = _session_factory()
    session = Session()
    company = _company(session)
    client = _client(session, company.id)
    agent = _user(session, company_id=company.id, full_name="Agent")
    ticket = _ticket(session, company_id=company.id, client_id=client.id, assignee_id=agent.id)
    first = _message(session, ticket_id=ticket.id, body="", telegram_message_id="1")
    second = _message(session, ticket_id=ticket.id, body="", telegram_message_id="2")
    _attachment(session, ticket_id=ticket.id, message_id=first.id, source="photo", media_group_id="mg-1")
    _attachment(session, ticket_id=ticket.id, message_id=second.id, source="video", media_group_id="mg-1")
    _account(session, user_id=agent.id, chat_id="chat-agent")
    _base(session, "new_message", "[Тело сообщения]")
    sent = []

    with notifications._MEDIA_GROUP_DEBOUNCE_LOCK:
        notifications._MEDIA_GROUP_DEBOUNCE.clear()
    monkeypatch.setattr(notifications, "_MEDIA_GROUP_TIMER_FACTORY", FakeTimer)
    monkeypatch.setattr(notifications, "SessionLocal", Session)
    monkeypatch.setattr(notifications, "send_message", lambda chat_id, text, **kwargs: sent.append((chat_id, text, kwargs)) or {"ok": True})
    monkeypatch.setattr(notifications.settings, "employee_telegram_bot_token", "employee-token")

    notifications.start_media_group_notification_debounce(ticket_id=ticket.id, media_group_id="mg-1")
    notifications.start_media_group_notification_debounce(ticket_id=ticket.id, media_group_id="mg-1")

    assert len(FakeTimer.instances) == 2
    assert [timer.interval for timer in FakeTimer.instances] == [10.0, 10.0]
    assert FakeTimer.instances[0].cancelled is True
    FakeTimer.instances[0].force_fire()
    assert sent == []
    FakeTimer.instances[1].fire()
    assert [(chat_id, text) for chat_id, text, _kwargs in sent] == [("chat-agent", "Файлы:\nФото\nВидео")]
    with notifications._MEDIA_GROUP_DEBOUNCE_LOCK:
        assert (ticket.id, "mg-1") not in notifications._MEDIA_GROUP_DEBOUNCE


def test_media_group_debounce_waits_for_late_parts_and_sends_one_complete_notification(monkeypatch):
    class FakeTimer:
        instances = []

        def __init__(self, interval, function, args=(), kwargs=None):
            self.interval = interval
            self.function = function
            self.args = args
            self.kwargs = kwargs or {}
            self.daemon = False
            self.cancelled = False
            FakeTimer.instances.append(self)

        def start(self):
            return None

        def cancel(self):
            self.cancelled = True

        def force_fire(self):
            self.function(*self.args, **self.kwargs)

    Session = _session_factory()
    session = Session()
    company = _company(session)
    client = _client(session, company.id)
    agent = _user(session, company_id=company.id, full_name="Agent")
    ticket = _ticket(session, company_id=company.id, client_id=client.id, assignee_id=agent.id)
    for index in range(7):
        message = _message(session, ticket_id=ticket.id, body="", telegram_message_id=str(100 + index))
        _attachment(session, ticket_id=ticket.id, message_id=message.id, filename=f"file-{index}.pdf", media_group_id="mg-late")
    _account(session, user_id=agent.id, chat_id="chat-agent")
    _base(session, "new_message", "[Тело сообщения]")
    sent = []

    with notifications._MEDIA_GROUP_DEBOUNCE_LOCK:
        notifications._MEDIA_GROUP_DEBOUNCE.clear()
    monkeypatch.setattr(notifications, "_MEDIA_GROUP_TIMER_FACTORY", FakeTimer)
    monkeypatch.setattr(notifications, "SessionLocal", Session)
    monkeypatch.setattr(notifications, "send_message", lambda chat_id, text, **kwargs: sent.append((chat_id, text, kwargs)) or {"ok": True})
    monkeypatch.setattr(notifications.settings, "employee_telegram_bot_token", "employee-token")

    notifications.start_media_group_notification_debounce(ticket_id=ticket.id, media_group_id="mg-late")
    for index in range(7, 10):
        message = _message(session, ticket_id=ticket.id, body="", telegram_message_id=str(100 + index))
        _attachment(session, ticket_id=ticket.id, message_id=message.id, filename=f"file-{index}.pdf", media_group_id="mg-late")
    session.commit()
    notifications.start_media_group_notification_debounce(ticket_id=ticket.id, media_group_id="mg-late")

    FakeTimer.instances[0].force_fire()
    assert sent == []
    FakeTimer.instances[1].force_fire()

    assert len(sent) == 1
    assert sent[0][0] == "chat-agent"
    assert sent[0][1].splitlines() == ["Файлы:"] + [f"file-{index}.pdf" for index in range(10)]


def test_media_group_of_ten_files_finalizes_immediately(monkeypatch):
    class FakeTimer:
        instances = []

        def __init__(self, interval, function, args=(), kwargs=None):
            self.interval = interval
            self.function = function
            self.args = args
            self.kwargs = kwargs or {}
            self.daemon = False
            self.cancelled = False
            FakeTimer.instances.append(self)

        def start(self):
            return None

        def cancel(self):
            self.cancelled = True

        def fire(self):
            if not self.cancelled:
                self.function(*self.args, **self.kwargs)

    Session = _session_factory()
    session = Session()
    company = _company(session)
    client = _client(session, company.id)
    agent = _user(session, company_id=company.id, full_name="Agent")
    ticket = _ticket(session, company_id=company.id, client_id=client.id, assignee_id=agent.id)
    for index in range(10):
        message = _message(session, ticket_id=ticket.id, body="", telegram_message_id=str(200 + index))
        _attachment(session, ticket_id=ticket.id, message_id=message.id, filename=f"ten-{index}.pdf", media_group_id="mg-10")
    _account(session, user_id=agent.id, chat_id="chat-agent")
    _base(session, "new_message", "[Тело сообщения]")
    sent = []

    with notifications._MEDIA_GROUP_DEBOUNCE_LOCK:
        notifications._MEDIA_GROUP_DEBOUNCE.clear()
    monkeypatch.setattr(notifications, "_MEDIA_GROUP_TIMER_FACTORY", FakeTimer)
    monkeypatch.setattr(notifications, "SessionLocal", Session)
    monkeypatch.setattr(notifications, "send_message", lambda chat_id, text, **kwargs: sent.append((chat_id, text)) or {"ok": True})
    monkeypatch.setattr(notifications.settings, "employee_telegram_bot_token", "employee-token")

    notifications.start_media_group_notification_debounce(ticket_id=ticket.id, media_group_id="mg-10")

    assert [timer.interval for timer in FakeTimer.instances] == [0.0]
    FakeTimer.instances[0].fire()
    assert sent == [("chat-agent", "Файлы:\n" + "\n".join(f"ten-{index}.pdf" for index in range(10)))]


def test_media_group_of_eight_files_finalizes_after_quiet_window(monkeypatch):
    class FakeTimer:
        instances = []

        def __init__(self, interval, function, args=(), kwargs=None):
            self.interval = interval
            self.function = function
            self.args = args
            self.kwargs = kwargs or {}
            self.daemon = False
            self.cancelled = False
            FakeTimer.instances.append(self)

        def start(self):
            return None

        def cancel(self):
            self.cancelled = True

        def fire(self):
            if not self.cancelled:
                self.function(*self.args, **self.kwargs)

    Session = _session_factory()
    session = Session()
    company = _company(session)
    client = _client(session, company.id)
    agent = _user(session, company_id=company.id, full_name="Agent")
    ticket = _ticket(session, company_id=company.id, client_id=client.id, assignee_id=agent.id)
    for index in range(8):
        message = _message(session, ticket_id=ticket.id, body="", telegram_message_id=str(300 + index))
        _attachment(session, ticket_id=ticket.id, message_id=message.id, filename=f"eight-{index}.pdf", media_group_id="mg-8")
    _account(session, user_id=agent.id, chat_id="chat-agent")
    _base(session, "new_message", "[Тело сообщения]")
    sent = []

    with notifications._MEDIA_GROUP_DEBOUNCE_LOCK:
        notifications._MEDIA_GROUP_DEBOUNCE.clear()
    monkeypatch.setattr(notifications, "_MEDIA_GROUP_TIMER_FACTORY", FakeTimer)
    monkeypatch.setattr(notifications, "SessionLocal", Session)
    monkeypatch.setattr(notifications, "send_message", lambda chat_id, text, **kwargs: sent.append((chat_id, text)) or {"ok": True})
    monkeypatch.setattr(notifications.settings, "employee_telegram_bot_token", "employee-token")

    notifications.start_media_group_notification_debounce(ticket_id=ticket.id, media_group_id="mg-8")

    assert [timer.interval for timer in FakeTimer.instances] == [10.0]
    assert sent == []
    FakeTimer.instances[0].fire()
    assert sent == [("chat-agent", "Файлы:\n" + "\n".join(f"eight-{index}.pdf" for index in range(8)))]


def test_media_group_stale_callback_returns_before_db_resolve(monkeypatch):
    class FakeTimer:
        instances = []

        def __init__(self, interval, function, args=(), kwargs=None):
            self.function = function
            self.args = args
            self.kwargs = kwargs or {}
            self.daemon = False
            self.cancelled = False
            FakeTimer.instances.append(self)

        def start(self):
            return None

        def cancel(self):
            self.cancelled = True

        def force_fire(self):
            self.function(*self.args, **self.kwargs)

    calls = {"resolve": 0, "send": 0}
    with notifications._MEDIA_GROUP_DEBOUNCE_LOCK:
        notifications._MEDIA_GROUP_DEBOUNCE.clear()
    monkeypatch.setattr(notifications, "_MEDIA_GROUP_TIMER_FACTORY", FakeTimer)
    monkeypatch.setattr(notifications, "resolve_media_group_new_message_telegram_delivery", lambda *_args, **_kwargs: calls.__setitem__("resolve", calls["resolve"] + 1))
    monkeypatch.setattr(notifications, "_send_delivery", lambda *_args, **_kwargs: calls.__setitem__("send", calls["send"] + 1))

    notifications.start_media_group_notification_debounce(ticket_id="ticket-1", media_group_id="mg-1")
    notifications.start_media_group_notification_debounce(ticket_id="ticket-1", media_group_id="mg-1")

    FakeTimer.instances[0].force_fire()

    assert calls == {"resolve": 0, "send": 0}


def test_media_group_generation_change_during_resolve_skips_send(monkeypatch):
    class FakeTimer:
        instances = []

        def __init__(self, interval, function, args=(), kwargs=None):
            self.function = function
            self.args = args
            self.kwargs = kwargs or {}
            self.daemon = False
            self.cancelled = False
            FakeTimer.instances.append(self)

        def start(self):
            return None

        def cancel(self):
            self.cancelled = True

    calls = {"send": 0}
    delivery = notifications.EmployeeTelegramDelivery(
        event_kind="new_message",
        delivery_kind="new_message",
        user_id="agent-1",
        telegram_chat_id="chat-1",
        text="ready",
        parse_mode=None,
        disable_link_preview=False,
        send_silently=False,
    )

    def resolve_and_reset(*_args, **_kwargs):
        notifications.start_media_group_notification_debounce(ticket_id="ticket-1", media_group_id="mg-1")
        return delivery

    with notifications._MEDIA_GROUP_DEBOUNCE_LOCK:
        notifications._MEDIA_GROUP_DEBOUNCE.clear()
    monkeypatch.setattr(notifications, "_MEDIA_GROUP_TIMER_FACTORY", FakeTimer)
    monkeypatch.setattr(notifications, "resolve_media_group_new_message_telegram_delivery", resolve_and_reset)
    monkeypatch.setattr(notifications, "_send_delivery", lambda *_args, **_kwargs: calls.__setitem__("send", calls["send"] + 1))

    notifications.start_media_group_notification_debounce(ticket_id="ticket-1", media_group_id="mg-1")
    FakeTimer.instances[0].function(*FakeTimer.instances[0].args)

    assert calls["send"] == 0
    with notifications._MEDIA_GROUP_DEBOUNCE_LOCK:
        assert notifications._MEDIA_GROUP_DEBOUNCE[("ticket-1", "mg-1")].generation == 2


def test_different_media_groups_send_separate_debounced_notifications(monkeypatch):
    class ImmediateTimer:
        def __init__(self, interval, function, args=(), kwargs=None):
            self.function = function
            self.args = args
            self.kwargs = kwargs or {}
            self.daemon = False

        def start(self):
            self.function(*self.args, **self.kwargs)

        def cancel(self):
            return None

    Session = _session_factory()
    session = Session()
    company = _company(session)
    client = _client(session, company.id)
    agent = _user(session, company_id=company.id, full_name="Agent")
    ticket = _ticket(session, company_id=company.id, client_id=client.id, assignee_id=agent.id)
    first = _message(session, ticket_id=ticket.id, body="", telegram_message_id="1")
    second = _message(session, ticket_id=ticket.id, body="", telegram_message_id="2")
    _attachment(session, ticket_id=ticket.id, message_id=first.id, filename="first.pdf", media_group_id="mg-1")
    _attachment(session, ticket_id=ticket.id, message_id=second.id, filename="second.pdf", media_group_id="mg-2")
    _account(session, user_id=agent.id, chat_id="chat-agent")
    _base(session, "new_message", "[Тело сообщения]")
    sent = []

    with notifications._MEDIA_GROUP_DEBOUNCE_LOCK:
        notifications._MEDIA_GROUP_DEBOUNCE.clear()
    monkeypatch.setattr(notifications, "_MEDIA_GROUP_TIMER_FACTORY", ImmediateTimer)
    monkeypatch.setattr(notifications, "SessionLocal", Session)
    monkeypatch.setattr(notifications, "send_message", lambda chat_id, text, **kwargs: sent.append(text) or {"ok": True})
    monkeypatch.setattr(notifications.settings, "employee_telegram_bot_token", "employee-token")

    notifications.start_media_group_notification_debounce(ticket_id=ticket.id, media_group_id="mg-1")
    notifications.start_media_group_notification_debounce(ticket_id=ticket.id, media_group_id="mg-2")

    assert sent == ["Файлы:\nfirst.pdf", "Файлы:\nsecond.pdf"]


def test_personal_assignment_and_message_inheritance_are_independent_and_use_personal_semantic_label():
    session = _session()
    company = _company(session)
    client = _client(session, company.id)
    agent = _user(session, company_id=company.id, full_name="Agent")
    ticket = _ticket(session, company_id=company.id, client_id=client.id, assignee_id=agent.id)
    message = _message(session, ticket_id=ticket.id)
    _account(session, user_id=agent.id, username="runtime_user")
    _base(session, "new_assignment", "Base A [TG @username адресата]")
    _base(session, "new_message", "Base M")
    _preference(
        session,
        agent.id,
        use_base_new_assignment_settings=False,
        personal_new_assignment_template="Personal A [Мой TG @username]",
        personal_new_assignment_html_enabled=False,
        personal_new_assignment_disable_link_preview=True,
        personal_new_assignment_send_silently=True,
        use_base_new_message_settings=False,
        personal_new_message_template="Personal M [Тело сообщения] [Мой TG @username]",
        personal_new_message_disable_link_preview=True,
    )

    assignment_delivery = notifications.resolve_assignment_telegram_deliveries(session, event=_assignment_event(agent.id), ticket=ticket)[0]
    message_delivery = notifications.resolve_new_message_telegram_delivery(
        session,
        ticket_id=ticket.id,
        message_id=message.id,
        recipient_user_id=agent.id,
    )

    assert assignment_delivery.text == "Personal A @runtime_user"
    assert assignment_delivery.parse_mode is None
    assert assignment_delivery.disable_link_preview is True
    assert assignment_delivery.send_silently is True
    assert message_delivery is not None
    assert message_delivery.text.startswith("Personal M &lt;b&gt;hello&lt;/b&gt; &amp; test @runtime_user")


def test_transfer_types_use_base_templates_but_personal_delivery_switches():
    session = _session()
    company = _company(session)
    client = _client(session, company.id)
    old_agent = _user(session, company_id=company.id, full_name="Old Agent")
    new_agent = _user(session, company_id=company.id, full_name="New Agent")
    ticket = _ticket(session, company_id=company.id, client_id=client.id, assignee_id=new_agent.id)
    _account(session, user_id=old_agent.id, chat_id="old")
    _account(session, user_id=new_agent.id, chat_id="new")
    _base(session, "transfer_to_agent", "To [Предыдущий агент]")
    _base(session, "transfer_from_agent", "From [Куда передана заявка]")
    _preference(session, new_agent.id, receive_transfer_to_agent=False)
    _preference(session, old_agent.id, receive_transfer_from_agent=True, personal_new_assignment_template="ignored")
    event = notifications.build_assignment_notification_event(old_assignee_id=old_agent.id, new_assignee_id=new_agent.id)

    deliveries = notifications.resolve_assignment_telegram_deliveries(session, event=event, ticket=ticket)

    assert [(item.user_id, item.text) for item in deliveries] == [(old_agent.id, "From New Agent")]


def test_new_message_tags_and_own_message_skip(monkeypatch):
    monkeypatch.setattr(notifications.settings, "app_public_base_url", "https://desk.example.test")
    session = _session()
    company = _company(session)
    client = _client(session, company.id)
    agent = _user(session, company_id=company.id, full_name="Assigned Agent")
    other = _user(session, company_id=company.id, full_name="Comment Author")
    ticket = _ticket(session, company_id=company.id, client_id=client.id, assignee_id=agent.id)
    comment = _message(session, ticket_id=ticket.id, message_type=MessageType.INTERNAL.value, author_user_id=other.id)
    own = _message(session, ticket_id=ticket.id, message_type=MessageType.AGENT.value, author_user_id=agent.id)
    _account(session, user_id=agent.id, username="agent_tg")
    _base(
        session,
        "new_message",
        "[Номер заявки] [Тип заявки] [Заголовок заявки] [Автор сообщения] [Вид сообщения] [Тело сообщения] [Дата сообщения] [Ссылка на заявку] [TG @username адресата]",
    )

    delivery = notifications.resolve_new_message_telegram_delivery(session, ticket_id=ticket.id, message_id=comment.id, recipient_user_id=agent.id)
    own_delivery = notifications.resolve_new_message_telegram_delivery(session, ticket_id=ticket.id, message_id=own.id, recipient_user_id=agent.id)

    assert delivery is not None
    assert "742 заявка" in delivery.text
    assert "Comment Author" in delivery.text
    assert "Комментарий" in delivery.text
    assert "&lt;b&gt;hello&lt;/b&gt; &amp; test" in delivery.text
    assert "27.08.2026" in delivery.text
    assert "@agent_tg" in delivery.text
    assert "https://desk.example.test/tickets/742" in delivery.text
    assert ticket.id not in delivery.text
    assert own_delivery is None


def test_hidden_internal_message_does_not_schedule_or_resolve_new_message():
    session = _session()
    company = _company(session)
    client = _client(session, company.id)
    agent = _user(session, company_id=company.id, full_name="Assigned Agent")
    other = _user(session, company_id=company.id, full_name="Comment Author")
    ticket = _ticket(session, company_id=company.id, client_id=client.id, assignee_id=agent.id)
    hidden = _message(
        session,
        ticket_id=ticket.id,
        message_type=MessageType.INTERNAL.value,
        author_user_id=other.id,
        agent_visible=False,
    )
    _account(session, user_id=agent.id)
    _base(session, "new_message", "[Тело сообщения]")

    notifications.schedule_new_message_notification(session, ticket=ticket, message=hidden)

    assert notifications._PENDING_MESSAGE_NOTIFICATIONS_KEY not in session.info
    assert notifications.resolve_new_message_telegram_delivery(
        session,
        ticket_id=ticket.id,
        message_id=hidden.id,
        recipient_user_id=agent.id,
    ) is None


def test_edited_client_message_does_not_schedule_or_resolve_new_message():
    session = _session()
    company = _company(session)
    client = _client(session, company.id)
    agent = _user(session, company_id=company.id, full_name="Assigned Agent")
    ticket = _ticket(session, company_id=company.id, client_id=client.id, assignee_id=agent.id)
    original = _message(session, ticket_id=ticket.id)
    edited = _message(session, ticket_id=ticket.id, body="edited", replaced_message_id=original.id)
    _account(session, user_id=agent.id)
    _base(session, "new_message", "[Тело сообщения]")

    notifications.schedule_new_message_notification(session, ticket=ticket, message=edited)

    assert notifications._PENDING_MESSAGE_NOTIFICATIONS_KEY not in session.info
    assert notifications.resolve_new_message_telegram_delivery(
        session,
        ticket_id=ticket.id,
        message_id=edited.id,
        recipient_user_id=agent.id,
    ) is None


def test_special_transfer_event_classification_and_labels():
    session = _session()
    company = _company(session)
    old_agent = _user(session, company_id=company.id, full_name="Agent A")
    new_agent = _user(session, company_id=company.id, full_name="Agent B")
    admin = _user(session, company_id=company.id, full_name="Admin User", role="admin")

    agent_to_agent = notifications.build_assignment_notification_event(old_assignee_id=old_agent.id, new_assignee_id=new_agent.id)
    to_special = notifications.build_assignment_notification_event(
        old_assignee_id=old_agent.id,
        new_assignee_id=admin.id,
        new_is_special=True,
        actor_user_id=admin.id,
    )
    from_special = notifications.build_assignment_notification_event(
        old_assignee_id=admin.id,
        new_assignee_id=new_agent.id,
        old_is_special=True,
        actor_user_id=admin.id,
    )
    internal = notifications.build_assignment_notification_event(
        old_assignee_id=admin.id,
        new_assignee_id=str(uuid.uuid4()),
        old_is_special=True,
        new_is_special=True,
        actor_user_id=admin.id,
    )

    assert [(d.kind, d.user_id) for d in agent_to_agent.deliveries] == [
        (notifications.DELIVERY_KIND_TRANSFER_TO, new_agent.id),
        (notifications.DELIVERY_KIND_TRANSFER_FROM, old_agent.id),
    ]
    assert [(d.kind, d.user_id) for d in to_special.deliveries] == [(notifications.DELIVERY_KIND_TRANSFER_FROM, old_agent.id)]
    assert [(d.kind, d.user_id) for d in from_special.deliveries] == [(notifications.DELIVERY_KIND_TRANSFER_TO, new_agent.id)]
    assert notifications.DELIVERY_KIND_NEW not in {d.kind for d in from_special.deliveries}
    assert internal is None


def test_special_transfers_send_no_duplicates_and_use_administrative_control_label(monkeypatch):
    Session = _session_factory()
    session = Session()
    company = _company(session)
    client = _client(session, company.id)
    agent_a = _user(session, company_id=company.id, full_name="Agent A")
    agent_b = _user(session, company_id=company.id, full_name="Agent B")
    admin = _user(session, company_id=company.id, full_name="Admin User", role="admin")
    _account(session, user_id=agent_a.id, chat_id="chat-a")
    _account(session, user_id=agent_b.id, chat_id="chat-b")
    _account(session, user_id=admin.id, chat_id="chat-admin")
    _base(session, "transfer_to_agent", "To [Предыдущий агент]")
    _base(session, "transfer_from_agent", "From [Куда передана заявка]")
    _base(session, "new_assignment", "New")
    ticket = _ticket(session, company_id=company.id, client_id=client.id, assignee_id=agent_a.id)
    sent = []

    monkeypatch.setattr(notifications, "SessionLocal", Session)
    monkeypatch.setattr(notifications.settings, "employee_telegram_bot_token", "employee-token")
    monkeypatch.setattr(notifications, "send_message", lambda chat_id, text, **kwargs: sent.append((chat_id, text, kwargs)) or {"ok": True})

    move_to_special(ticket.id, db=session, user=SimpleNamespace(id=admin.id, role="admin", is_real_admin=True))
    session.commit()
    assert sent == [("chat-a", "From Административный контроль", {"parse_mode": None, "bot_token": "employee-token", "disable_web_page_preview": False, "disable_notification": False})]

    sent.clear()
    _assign_ticket_by_admin(
        session,
        ticket,
        agent_b,
        SimpleNamespace(id=admin.id, role="admin", real_user_id=admin.id, real_role="admin", is_real_admin=True),
    )
    session.commit()

    assert sent == [("chat-b", "To Административный контроль", {"parse_mode": None, "bot_token": "employee-token", "disable_web_page_preview": False, "disable_notification": False})]


@pytest.fixture(autouse=True)
def _notification_work_hours(monkeypatch):
    # Existing delivery tests cover daytime behavior independently of the wall clock.
    from api.app.services import employee_notification_backlog as backlog

    original = backlog._moscow_now
    monkeypatch.setattr(backlog, "_moscow_now", lambda now=None: original(now or datetime(2026, 11, 9, 9)))
