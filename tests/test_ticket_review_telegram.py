from __future__ import annotations

import uuid
from datetime import datetime
from types import SimpleNamespace

from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker

from api.app.db import Base
from api.app.models import AutomaticMessageTemplate, BotButtonSetting, Client, ClientProfile, Company, MessageType, TelegramContact, Ticket, TicketAttachment, TicketFeedback, TicketFeedbackInput, TicketFeedbackState, TicketIntakeState, TicketMessage, TicketReview, TicketReviewCycle, TicketReviewCycleState, TicketStatus, TicketTaskKind, TicketWorkflowType, User
from api.app.routers import telegram as telegram_router
from api.app.services.automatic_messages import get_automatic_message_definition
from api.app.services.ticket_lifecycle import close_ticket_by_actor
from api.app.services.telegram_post_close import _now_utc, attributable_review_agent_id_for_actor, cleanup_review_flow_for_ticket_reopen, feedback_back_callback_data, feedback_input_markup, feedback_start_callback_data, post_close_actions_markup, review_back_callback_data, review_finish_callback_data, review_finish_markup, review_rate_callback_data, review_rating_markup, review_start_callback_data
from api.app.services.ticket_reviews import create_review_cycle, save_review_caption, save_review_rating, set_review_current_message_id, start_review_rating_cycle


def _session():
    engine = create_engine("sqlite:///:memory:", future=True)
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine, autoflush=False, autocommit=False)
    return Session()


def _seed_base(session, *, assignee_role: str = "agent", assignee: bool = True):
    company = Company(id=str(uuid.uuid4()), name="ACME", created_at=datetime.utcnow())
    client = Client(id=str(uuid.uuid4()), company_id=company.id, display_name="Client")
    contact = TelegramContact(
        id=str(uuid.uuid4()),
        company_id=company.id,
        client_id=client.id,
        external_user_id="100500",
        external_chat_id="100500",
    )
    agent = User(id=str(uuid.uuid4()), company_id=company.id, full_name="Agent", role=assignee_role)
    admin = User(id=str(uuid.uuid4()), company_id=company.id, full_name="Admin", role="admin")
    session.add_all([company, client, contact, agent, admin])
    session.commit()
    return company, client, contact, (agent if assignee else None), admin


def _seed_ticket(
    session,
    company,
    client,
    agent=None,
    *,
    workflow_type=TicketWorkflowType.TICKET.value,
    with_agent_message: bool = True,
):
    ticket = Ticket(
        id=str(uuid.uuid4()),
        company_id=company.id,
        client_id=client.id,
        assignee_id=agent.id if agent is not None else None,
        subject="Need help",
        workflow_type=workflow_type,
        task_kind=TicketTaskKind.NORMAL.value,
        status=TicketStatus.PENDING.value,
        created_at=datetime.utcnow(),
        updated_at=datetime.utcnow(),
    )
    session.add(ticket)
    message = None
    if with_agent_message:
        message = TicketMessage(
            id=str(uuid.uuid4()),
            ticket_id=ticket.id,
            message_type=MessageType.AGENT.value,
            body="Done",
            author_user_id=agent.id if agent is not None else None,
            created_at=datetime.utcnow(),
        )
        session.add(message)
    if workflow_type == TicketWorkflowType.TASK.value:
        session.add(
            TicketMessage(
                id=str(uuid.uuid4()),
                ticket_id=ticket.id,
                message_type=MessageType.CLIENT.value,
                body="Task text",
                created_at=datetime.utcnow(),
            )
        )
    session.commit()
    return ticket, message


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


def _close_ticket(
    session,
    ticket,
    actor,
    agent_message,
    *,
    send_result=None,
):
    sent = []

    def _send(_db, _ticket, text, **kwargs):
        sent.append((text, kwargs))
        return send_result

    close_ticket_by_actor(
        session,
        ticket,
        actor,
        force_without_agent_reply_for_task=actor.role == "admin",
        is_admin_fn=lambda user: user.role == "admin",
        get_or_create_client_profile_fn=lambda *_: None,
        last_agent_message_fn=lambda *_: agent_message,
        first_client_message_fn=lambda db, closed_ticket: db.execute(
            select(TicketMessage)
            .where(TicketMessage.ticket_id == closed_ticket.id, TicketMessage.message_type == MessageType.CLIENT.value)
            .limit(1)
        ).scalar_one_or_none(),
        send_to_telegram_client_fn=_send,
        actor_display_name_fn=lambda *_: actor.role,
        add_message_fn=_add_message,
        message_attachments_fn=lambda *_: [],
        send_attachment_to_telegram_client_fn=lambda *_args, **_kwargs: None,
        onsite_close_reminder_text="onsite",
        ticket_closed_client_text="closed",
    )
    return sent


def _cycles(session):
    return session.execute(select(TicketReviewCycle)).scalars().all()


def _feedbacks(session):
    return session.execute(select(TicketFeedback).order_by(TicketFeedback.created_at)).scalars().all()


def _markup_labels(markup):
    return [button["text"] for row in (markup or {}).get("inline_keyboard") or [] for button in row]


def _seed_system_button(session, *, key: str, default_text: str, text: str | None = None, style: str | None = None):
    row = BotButtonSetting(
        id=str(uuid.uuid4()),
        stable_button_id=str(uuid.uuid4()),
        button_key=key,
        menu_type="__system__",
        workflow_type="system",
        button_path=default_text,
        button_label=default_text,
        label_override=text,
        label_overridden=text is not None,
        button_style=style,
        currently_present=True,
        is_db_created=True,
        button_kind="system",
        parent_path="",
        is_archived=False,
        created_at=datetime.utcnow(),
        updated_at=datetime.utcnow(),
    )
    session.add(row)
    session.commit()
    return row


def test_employee_close_offers_review_on_employee_and_same_close_message():
    session = _session()
    company, client, _contact, agent, _admin = _seed_base(session)
    ticket, agent_message = _seed_ticket(session, company, client, agent)
    sent = _close_ticket(
        session,
        ticket,
        SimpleNamespace(id=agent.id, role="agent"),
        agent_message,
        send_result={"ok": True, "result": {"message_id": 77}},
    )

    cycle = _cycles(session)[0]
    assert cycle.agent_id == agent.id
    assert cycle.state == TicketReviewCycleState.OFFERED.value
    assert cycle.review_current_message_id == "77"
    assert sent[0][0] == "closed"
    assert sent[0][1]["required"] is False
    assert sent[0][1]["remove_keyboard"] is False
    assert _markup_labels(sent[0][1]["reply_markup"]) == ["Оставить отзыв", "Предложения и замечания"]
    assert sent[0][1]["reply_markup"]["inline_keyboard"][0][0]["callback_data"] == review_start_callback_data(cycle.id)
    assert sent[0][1]["reply_markup"]["inline_keyboard"][1][0]["callback_data"] == feedback_start_callback_data(ticket.id)


def test_post_close_actions_default_markup_has_no_button_style():
    session = _session()
    company, client, contact, agent, _admin = _seed_base(session)
    ticket, _message = _seed_ticket(session, company, client, agent)
    ticket.status = TicketStatus.CLOSED.value
    cycle = create_review_cycle(session, ticket_id=ticket.id, agent_id=agent.id, client_id=client.id)
    session.commit()

    markup = post_close_actions_markup(session, ticket=ticket, contact=contact, review_cycle=cycle)

    assert _markup_labels(markup) == ["Оставить отзыв", "Предложения и замечания"]
    assert "style" not in markup["inline_keyboard"][0][0]
    assert "style" not in markup["inline_keyboard"][1][0]


def test_post_close_actions_apply_custom_review_and_feedback_buttons_in_all_combinations():
    session = _session()
    company, client, contact, agent, _admin = _seed_base(session)
    ticket, _message = _seed_ticket(session, company, client, agent)
    ticket.status = TicketStatus.CLOSED.value
    cycle = create_review_cycle(session, ticket_id=ticket.id, agent_id=agent.id, client_id=client.id)
    _seed_system_button(session, key="__system__|review|start", default_text="Оставить отзыв", text="Оценить", style="primary")
    _seed_system_button(session, key="__system__|feedback|start", default_text="Предложения и замечания", text="Предложить", style="success")
    session.commit()

    both = post_close_actions_markup(session, ticket=ticket, contact=contact, review_cycle=cycle)
    assert both["inline_keyboard"][0][0] == {"text": "Оценить", "callback_data": review_start_callback_data(cycle.id), "style": "primary"}
    assert both["inline_keyboard"][1][0] == {"text": "Предложить", "callback_data": feedback_start_callback_data(ticket.id), "style": "success"}

    session.add(
        TicketFeedback(
            id=str(uuid.uuid4()),
            ticket_id=ticket.id,
            client_id=client.id,
            client_name_snapshot="Client",
            ticket_title_snapshot=ticket.subject,
            text="done",
            submitted_at=datetime.utcnow(),
            created_at=datetime.utcnow(),
        )
    )
    session.commit()
    only_review = post_close_actions_markup(session, ticket=ticket, contact=contact, review_cycle=cycle)
    assert only_review["inline_keyboard"] == [[{"text": "Оценить", "callback_data": review_start_callback_data(cycle.id), "style": "primary"}]]

    session.query(TicketFeedback).delete()
    cycle.state = TicketReviewCycleState.COMPLETED.value
    session.commit()
    only_feedback = post_close_actions_markup(session, ticket=ticket, contact=contact, review_cycle=cycle)
    assert only_feedback["inline_keyboard"] == [[{"text": "Предложить", "callback_data": feedback_start_callback_data(ticket.id), "style": "success"}]]


def test_review_rating_back_and_finish_markup_apply_custom_text_style_and_keep_callbacks():
    session = _session()
    company, client, _contact, agent, _admin = _seed_base(session)
    ticket, _message = _seed_ticket(session, company, client, agent)
    cycle = create_review_cycle(session, ticket_id=ticket.id, agent_id=agent.id, client_id=client.id)
    _seed_system_button(session, key="__system__|review|rate_3", default_text="⭐⭐⭐", text="Три", style="success")
    _seed_system_button(session, key="__system__|review|back", default_text="Назад", text="К оценкам", style="danger")
    _seed_system_button(session, key="__system__|review|finish", default_text="Завершить отзыв", text="Готово", style="primary")

    rating_markup = review_rating_markup(cycle, session)
    buttons = [row[0] for row in rating_markup["inline_keyboard"]]
    assert [button["callback_data"] for button in buttons] == [
        review_rate_callback_data(cycle.id, 1),
        review_rate_callback_data(cycle.id, 2),
        review_rate_callback_data(cycle.id, 3),
        review_rate_callback_data(cycle.id, 4),
        review_rate_callback_data(cycle.id, 5),
        review_back_callback_data(cycle.id),
    ]
    assert buttons[2] == {"text": "Три", "callback_data": review_rate_callback_data(cycle.id, 3), "style": "success"}
    assert buttons[-1] == {"text": "К оценкам", "callback_data": review_back_callback_data(cycle.id), "style": "danger"}

    finish_button = review_finish_markup(cycle, session)["inline_keyboard"][0][0]
    assert finish_button == {"text": "Готово", "callback_data": review_finish_callback_data(cycle.id), "style": "primary"}


def test_feedback_back_markup_applies_custom_text_style_and_keeps_callback():
    session = _session()
    input_state = TicketFeedbackInput(id="input-1", ticket_id="ticket-1", client_id="client-1", state=TicketFeedbackState.AWAITING_TEXT.value)
    _seed_system_button(session, key="__system__|feedback|back", default_text="Назад", text="Вернуться", style="danger")

    button = feedback_input_markup(input_state, session)["inline_keyboard"][0][0]

    assert button == {"text": "Вернуться", "callback_data": feedback_back_callback_data(input_state.id), "style": "danger"}


def test_admin_close_offers_review_on_current_assignee_not_admin():
    session = _session()
    company, client, _contact, agent, admin = _seed_base(session)
    ticket, agent_message = _seed_ticket(session, company, client, agent)

    sent = _close_ticket(
        session,
        ticket,
        SimpleNamespace(id=admin.id, role="admin"),
        agent_message,
        send_result={"ok": True, "result": {"message_id": 78}},
    )

    assert _cycles(session)[0].agent_id == agent.id
    assert len(sent) == 1
    assert sent[0][1]["reply_markup"]["inline_keyboard"][0][0]["text"] == "Оставить отзыв"


def test_admin_close_without_attributable_employee_has_no_review_offer():
    session = _session()
    company, client, _contact, _agent, admin = _seed_base(session, assignee=False)
    ticket, agent_message = _seed_ticket(session, company, client, None)

    sent = _close_ticket(
        session,
        ticket,
        SimpleNamespace(id=admin.id, role="admin"),
        agent_message,
        send_result={"ok": True, "result": {"message_id": 79}},
    )

    assert _cycles(session) == []
    assert len(sent) == 1
    assert sent[0][1]["remove_keyboard"] is False
    assert _markup_labels(sent[0][1]["reply_markup"]) == ["Предложения и замечания"]


def test_non_ordinary_workflows_do_not_offer_review():
    for workflow_type in [TicketWorkflowType.TASK.value, TicketWorkflowType.AUTO.value]:
        session = _session()
        company, client, _contact, agent, _admin = _seed_base(session)
        ticket, agent_message = _seed_ticket(session, company, client, agent, workflow_type=workflow_type)

        sent = _close_ticket(
            session,
            ticket,
            SimpleNamespace(id=agent.id, role="agent"),
            agent_message,
            send_result={"ok": True, "result": {"message_id": 80}},
        )

        assert _cycles(session) == []
        for _text, kwargs in sent:
            labels = _markup_labels(kwargs.get("reply_markup"))
            assert "Оставить отзыв" not in labels
            if workflow_type == TicketWorkflowType.TASK.value:
                assert labels == ["Предложения и замечания"]
            else:
                assert labels == []


def test_close_delivery_failures_do_not_leave_active_review_offer():
    for response in [None, {"ok": True, "result": {}}]:
        session = _session()
        company, client, _contact, agent, _admin = _seed_base(session)
        ticket, agent_message = _seed_ticket(session, company, client, agent)

        _close_ticket(
            session,
            ticket,
            SimpleNamespace(id=agent.id, role="agent"),
            agent_message,
            send_result=response,
        )

        assert ticket.status == TicketStatus.CLOSED.value
        cycle = _cycles(session)[0]
        assert cycle.state == TicketReviewCycleState.CANCELLED.value
        assert cycle.review_current_message_id is None


def test_review_enabled_close_without_message_id_cancels_cycle():
    session = _session()
    company, client, _contact, agent, _admin = _seed_base(session)
    ticket, agent_message = _seed_ticket(session, company, client, agent)

    sent = _close_ticket(
        session,
        ticket,
        SimpleNamespace(id=agent.id, role="agent"),
        agent_message,
        send_result={"ok": True, "result": {}},
    )
    cycle = _cycles(session)[0]

    assert len(sent) == 1
    assert sent[0][1]["reply_markup"]["inline_keyboard"][0][0]["text"] == "Оставить отзыв"
    assert cycle.state == TicketReviewCycleState.CANCELLED.value
    assert cycle.review_current_message_id is None


def _callback_payload(*, update_id: int, contact, data: str, message_id: int = 77, first_name: str = "Client"):
    return {
        "update_id": update_id,
        "callback_query": {
            "id": f"cb-{update_id}",
            "from": {"id": int(contact.external_user_id), "first_name": first_name},
            "data": data,
            "message": {
                "message_id": message_id,
                "chat": {"id": int(contact.external_chat_id), "type": "private"},
            },
        },
    }


def test_client_self_close_offers_review_on_current_assignee(monkeypatch):
    session = _session()
    company, client, contact, agent, _admin = _seed_base(session)
    ticket, _message = _seed_ticket(session, company, client, agent, with_agent_message=False)
    sent = []

    def _send(*args, **kwargs):
        sent.append((args, kwargs))
        return {"ok": True, "result": {"message_id": 90}}

    monkeypatch.setattr(telegram_router, "send_message", _send)
    monkeypatch.setattr(telegram_router, "delete_message", lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("close must not delete a temporary close message")))
    monkeypatch.setattr(telegram_router, "answer_callback_query", lambda *args, **kwargs: {"ok": True})
    monkeypatch.setattr(telegram_router, "_safe_remove_inline_keyboard", lambda *args, **kwargs: None)

    result = telegram_router.process_telegram_update(
        session,
        _callback_payload(update_id=2001, contact=contact, data="client_close", message_id=12),
        notify_client=True,
    )

    cycle = _cycles(session)[0]
    assert result["closed"] is True
    assert cycle.agent_id == agent.id
    assert cycle.review_current_message_id == "90"
    assert len(sent) == 1
    assert sent[0][1]["remove_keyboard"] is False
    assert _markup_labels(sent[0][1]["reply_markup"]) == ["Оставить отзыв", "Предложения и замечания"]


def test_client_self_close_without_assignee_has_no_review(monkeypatch):
    session = _session()
    company, client, contact, _agent, _admin = _seed_base(session, assignee=False)
    _seed_ticket(session, company, client, None, with_agent_message=False)
    sent = []

    monkeypatch.setattr(telegram_router, "send_message", lambda *args, **kwargs: sent.append((args, kwargs)) or {"ok": True, "result": {"message_id": 91}})
    monkeypatch.setattr(telegram_router, "delete_message", lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("close must not delete a temporary close message")))
    monkeypatch.setattr(telegram_router, "answer_callback_query", lambda *args, **kwargs: {"ok": True})
    monkeypatch.setattr(telegram_router, "_safe_remove_inline_keyboard", lambda *args, **kwargs: None)

    telegram_router.process_telegram_update(
        session,
        _callback_payload(update_id=2002, contact=contact, data="client_close", message_id=12),
        notify_client=True,
    )

    assert _cycles(session) == []
    assert len(sent) == 1
    assert sent[0][0][1] == "Ваше обращение закрыто. Если Вам снова потребуется помощь технической поддержки, нажмите /start."
    assert sent[0][1]["remove_keyboard"] is True
    assert sent[0][1]["reply_markup"] is None


def test_client_self_close_without_assignee_uses_unassigned_override(monkeypatch):
    session = _session()
    company, client, contact, _agent, _admin = _seed_base(session, assignee=False)
    _seed_ticket(session, company, client, None, with_agent_message=False)
    session.add(
        AutomaticMessageTemplate(
            id="unassigned-close-override",
            key="telegram.ticket.close.unassigned.client",
            body="Закрыто до назначения: {{start_command}}",
            is_overridden=True,
            html_enabled=True,
        )
    )
    session.commit()
    sent = []

    monkeypatch.setattr(telegram_router, "send_message", lambda *args, **kwargs: sent.append((args, kwargs)) or {"ok": True, "result": {"message_id": 92}})
    monkeypatch.setattr(telegram_router, "answer_callback_query", lambda *args, **kwargs: {"ok": True})
    monkeypatch.setattr(telegram_router, "_safe_remove_inline_keyboard", lambda *args, **kwargs: None)

    telegram_router.process_telegram_update(
        session,
        _callback_payload(update_id=2003, contact=contact, data="client_close", message_id=12),
        notify_client=True,
    )

    assert _cycles(session) == []
    assert sent[0][0][1] == "Закрыто до назначения: /start"
    assert sent[0][1]["reply_markup"] is None


def test_client_self_close_with_assignee_renders_html_and_keeps_post_close_actions(monkeypatch):
    session = _session()
    company, client, contact, agent, _admin = _seed_base(session)
    _seed_ticket(session, company, client, agent, with_agent_message=False)
    session.add(
        AutomaticMessageTemplate(
            id="assigned-close-html",
            key="telegram.ticket.close.client",
            body="<code>текст</code>",
            is_overridden=True,
            html_enabled=True,
        )
    )
    session.commit()
    sent = []

    monkeypatch.setattr(telegram_router, "send_message", lambda *args, **kwargs: sent.append((args, kwargs)) or {"ok": True, "result": {"message_id": 93}})
    monkeypatch.setattr(telegram_router, "answer_callback_query", lambda *args, **kwargs: {"ok": True})
    monkeypatch.setattr(telegram_router, "_safe_remove_inline_keyboard", lambda *args, **kwargs: None)

    telegram_router.process_telegram_update(
        session,
        _callback_payload(update_id=2004, contact=contact, data="client_close", message_id=12),
        notify_client=True,
    )

    assert sent[0][0][1] == "<code>текст</code>"
    assert sent[0][1]["parse_mode"] == "HTML"
    assert _markup_labels(sent[0][1]["reply_markup"]) == ["Оставить отзыв", "Предложения и замечания"]


def test_close_command_without_assignee_uses_unassigned_message_and_no_actions(monkeypatch):
    session = _session()
    company, client, contact, _agent, _admin = _seed_base(session, assignee=False)
    ticket, _message = _seed_ticket(session, company, client, None, with_agent_message=False)
    sent = []

    monkeypatch.setattr(telegram_router, "send_message", lambda *args, **kwargs: sent.append((args, kwargs)) or {"ok": True, "result": {"message_id": 94}})
    monkeypatch.setattr(telegram_router, "remove_inline_keyboard", lambda *args, **kwargs: None)

    result = telegram_router.process_telegram_update(
        session,
        _message_payload(update_id=2005, contact=contact, text="/close"),
        notify_client=True,
    )

    session.refresh(ticket)
    assert result["closed"] is True
    assert ticket.status == TicketStatus.CLOSED.value
    assert _cycles(session) == []
    assert sent[0][0][1] == "Ваше обращение закрыто. Если Вам снова потребуется помощь технической поддержки, нажмите /start."
    assert sent[0][1]["remove_keyboard"] is True
    assert sent[0][1]["reply_markup"] is None


def test_close_command_with_assignee_keeps_regular_post_close_actions(monkeypatch):
    session = _session()
    company, client, contact, agent, _admin = _seed_base(session)
    ticket, _message = _seed_ticket(session, company, client, agent, with_agent_message=False)
    sent = []

    monkeypatch.setattr(telegram_router, "send_message", lambda *args, **kwargs: sent.append((args, kwargs)) or {"ok": True, "result": {"message_id": 95}})
    monkeypatch.setattr(telegram_router, "remove_inline_keyboard", lambda *args, **kwargs: None)

    result = telegram_router.process_telegram_update(
        session,
        _message_payload(update_id=2006, contact=contact, text="/close"),
        notify_client=True,
    )

    session.refresh(ticket)
    cycle = _cycles(session)[0]
    assert result["closed"] is True
    assert ticket.status == TicketStatus.CLOSED.value
    assert cycle.agent_id == agent.id
    assert cycle.review_current_message_id == "95"
    assert sent[0][0][1] == telegram_router.TICKET_CLOSED_CLIENT_TEXT
    assert sent[0][1]["remove_keyboard"] is False
    assert _markup_labels(sent[0][1]["reply_markup"]) == ["Оставить отзыв", "Предложения и замечания"]


def _closed_review_cycle(session):
    company, client, contact, agent, _admin = _seed_base(session)
    ticket, _message = _seed_ticket(session, company, client, agent)
    ticket.status = TicketStatus.CLOSED.value
    cycle = create_review_cycle(session, ticket_id=ticket.id, agent_id=agent.id, client_id=client.id)
    set_review_current_message_id(session, cycle=cycle, message_id="77")
    session.commit()
    return contact, ticket, cycle


def test_valid_review_start_deletes_exact_close_message_and_sends_rating_prompt(monkeypatch):
    session = _session()
    contact, _ticket, cycle = _closed_review_cycle(session)
    deleted = []
    sent = []
    monkeypatch.setattr(telegram_router, "answer_callback_query", lambda *args, **kwargs: {"ok": True})
    monkeypatch.setattr(telegram_router, "delete_message", lambda chat_id, message_id: deleted.append((chat_id, message_id)) or {"ok": True})
    monkeypatch.setattr(telegram_router, "send_message", lambda *args, **kwargs: sent.append((args, kwargs)) or {"ok": True, "result": {"message_id": 88}})

    result = telegram_router.process_telegram_update(
        session,
        _callback_payload(update_id=3001, contact=contact, data=review_start_callback_data(cycle.id), message_id=77),
        notify_client=True,
    )
    session.refresh(cycle)

    assert result["review"] == "awaiting_rating"
    assert cycle.state == TicketReviewCycleState.AWAITING_RATING.value
    assert cycle.review_current_message_id == "88"
    assert deleted == [(contact.external_chat_id, 77)]
    assert sent[0][1]["reply_markup"]["inline_keyboard"][-1][0]["text"] == "Назад"


def test_stale_review_callbacks_do_not_change_data(monkeypatch):
    session = _session()
    contact, _ticket, cycle = _closed_review_cycle(session)
    cycle.state = TicketReviewCycleState.CANCELLED.value
    cycle.cancelled_at = datetime.utcnow()
    session.commit()
    removed = []
    monkeypatch.setattr(telegram_router, "answer_callback_query", lambda *args, **kwargs: {"ok": True})
    monkeypatch.setattr(telegram_router, "_safe_remove_inline_keyboard", lambda chat_id, message_id: removed.append((chat_id, message_id)))

    result = telegram_router.process_telegram_update(
        session,
        _callback_payload(update_id=3002, contact=contact, data=review_start_callback_data(cycle.id), message_id=77),
        notify_client=True,
    )
    session.refresh(cycle)

    assert result["review"] == "stale"
    assert cycle.state == TicketReviewCycleState.CANCELLED.value
    assert removed == [(contact.external_chat_id, 77)]


def test_review_callback_from_another_client_and_reopened_ticket_are_stale(monkeypatch):
    session = _session()
    contact, ticket, cycle = _closed_review_cycle(session)
    other_client = Client(id=str(uuid.uuid4()), company_id=ticket.company_id, display_name="Other")
    other_contact = TelegramContact(
        id=str(uuid.uuid4()),
        company_id=ticket.company_id,
        client_id=other_client.id,
        external_user_id="200500",
        external_chat_id="200500",
    )
    session.add_all([other_client, other_contact])
    session.commit()
    monkeypatch.setattr(telegram_router, "answer_callback_query", lambda *args, **kwargs: {"ok": True})
    monkeypatch.setattr(telegram_router, "_safe_remove_inline_keyboard", lambda *args, **kwargs: None)

    result = telegram_router.process_telegram_update(
        session,
        _callback_payload(update_id=3003, contact=other_contact, data=review_start_callback_data(cycle.id), message_id=77),
        notify_client=True,
    )
    assert result["review"] == "stale"

    ticket.status = TicketStatus.NEW.value
    session.commit()
    result = telegram_router.process_telegram_update(
        session,
        _callback_payload(update_id=3004, contact=contact, data=review_start_callback_data(cycle.id), message_id=77),
        notify_client=True,
    )
    session.refresh(cycle)
    assert result["review"] == "stale"
    assert cycle.state == TicketReviewCycleState.OFFERED.value


def test_valid_review_back_returns_to_offered_and_sends_new_post_close_screen(monkeypatch):
    session = _session()
    contact, _ticket, cycle = _closed_review_cycle(session)
    start_review_rating_cycle(session, cycle=cycle)
    set_review_current_message_id(session, cycle=cycle, message_id="88")
    session.commit()
    deleted = []
    sent = []
    monkeypatch.setattr(telegram_router, "answer_callback_query", lambda *args, **kwargs: {"ok": True})
    monkeypatch.setattr(telegram_router, "delete_message", lambda chat_id, message_id: deleted.append((chat_id, message_id)) or {"ok": True})
    monkeypatch.setattr(telegram_router, "send_message", lambda *args, **kwargs: sent.append((args, kwargs)) or {"ok": True, "result": {"message_id": 99}})
    monkeypatch.setattr(telegram_router, "edit_message_reply_markup", lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("back must not edit markup")))

    result = telegram_router.process_telegram_update(
        session,
        _callback_payload(update_id=3005, contact=contact, data=review_back_callback_data(cycle.id), message_id=88),
        notify_client=True,
    )
    session.refresh(cycle)

    assert result["review"] == "offered"
    assert cycle.state == TicketReviewCycleState.OFFERED.value
    assert cycle.review_current_message_id == "99"
    assert deleted == [(contact.external_chat_id, 88)]
    assert sent[0][0][1] == telegram_router.TICKET_CLOSED_CLIENT_TEXT
    assert sent[0][1]["remove_keyboard"] is False
    assert _markup_labels(sent[0][1]["reply_markup"]) == ["Оставить отзыв", "Предложения и замечания"]
    assert session.execute(select(TicketReview)).scalars().all() == []


def _awaiting_rating_cycle(session):
    contact, ticket, cycle = _closed_review_cycle(session)
    start_review_rating_cycle(session, cycle=cycle)
    set_review_current_message_id(session, cycle=cycle, message_id="88")
    session.commit()
    return contact, ticket, cycle


def _awaiting_text_cycle(session, *, review_text: str | None = None, caption_state: bool = False):
    contact, ticket, cycle = _awaiting_rating_cycle(session)
    review = save_review_rating(
        session,
        cycle=cycle,
        rating=4,
        agent_name_snapshot="Agent",
        client_name_snapshot="Client",
        ticket_subject_snapshot="Need help",
        rated_at=datetime(2026, 1, 1, 12, 0, 0),
    )
    set_review_current_message_id(session, cycle=cycle, message_id="99")
    if caption_state:
        save_review_caption(session, cycle=cycle, caption=review_text or "caption")
    elif review_text is not None:
        review.review_text = review_text
    session.commit()
    return contact, ticket, cycle, review


def _reviews(session):
    return session.execute(select(TicketReview).order_by(TicketReview.created_at)).scalars().all()


def _message_payload(*, update_id: int, contact, text: str | None = None, caption: str | None = None, attachment_kind: str | None = None):
    message = {
        "message_id": update_id + 1000,
        "from": {"id": int(contact.external_user_id), "first_name": "Client"},
        "chat": {"id": int(contact.external_chat_id), "type": "private"},
    }
    if text is not None:
        message["text"] = text
    if caption is not None:
        message["caption"] = caption
    if attachment_kind == "document":
        message["document"] = {"file_id": f"file-{update_id}", "file_unique_id": f"unique-{update_id}", "file_name": "doc.txt"}
    elif attachment_kind == "photo":
        message["photo"] = [{"file_id": f"photo-{update_id}", "file_unique_id": f"photo-unique-{update_id}", "file_size": 10}]
    return {"update_id": update_id, "message": message}


def test_actor_without_db_user_is_not_review_agent():
    session = _session()
    company, client, _contact, agent, _admin = _seed_base(session)
    ticket, _message = _seed_ticket(session, company, client, agent)

    assert attributable_review_agent_id_for_actor(session, ticket, SimpleNamespace(id=str(uuid.uuid4()), role="agent")) is None


def test_valid_rating_saves_review_snapshots_deletes_stars_and_sends_text_prompt(monkeypatch):
    session = _session()
    contact, _ticket, cycle = _awaiting_rating_cycle(session)
    deleted = []
    sent = []
    monkeypatch.setattr(telegram_router, "answer_callback_query", lambda *args, **kwargs: {"ok": True})
    monkeypatch.setattr(telegram_router, "delete_message", lambda chat_id, message_id: deleted.append((chat_id, message_id)) or {"ok": True})
    monkeypatch.setattr(telegram_router, "send_message", lambda *args, **kwargs: sent.append((args, kwargs)) or {"ok": True, "result": {"message_id": 101}})

    before = datetime.utcnow()
    result = telegram_router.process_telegram_update(
        session,
        _callback_payload(update_id=4001, contact=contact, data=review_rate_callback_data(cycle.id, 5), message_id=88),
        notify_client=True,
    )
    after = datetime.utcnow()
    review = _reviews(session)[0]
    session.refresh(cycle)

    assert result["review"] == "awaiting_text"
    assert review.rating == 5
    assert before <= review.rated_at <= after
    assert review.agent_name_snapshot == "Agent"
    assert review.client_name_snapshot == "Client"
    assert review.ticket_subject_snapshot == "Need help"
    assert review.cycle_id == cycle.id
    assert review.version_number == 1
    assert review.previous_version_id is None
    assert review.is_current is True
    assert cycle.review_id == review.id
    assert cycle.state == TicketReviewCycleState.AWAITING_TEXT.value
    assert cycle.review_current_message_id == "101"
    assert deleted == [(contact.external_chat_id, 88)]
    definition = get_automatic_message_definition("telegram.review.text_prompt")
    assert definition is not None
    assert sent[0][0][1] == definition.default_text


def test_rating_boundaries_and_stale_callbacks_do_not_save(monkeypatch):
    for data_factory, message_id, mutate in [
        (lambda cycle: review_rate_callback_data(cycle.id, 0), 88, lambda *_: None),
        (lambda cycle: review_rate_callback_data(cycle.id, 6), 88, lambda *_: None),
        (lambda cycle: review_rate_callback_data(cycle.id, 5), 77, lambda *_: None),
        (lambda cycle: review_rate_callback_data(cycle.id, 5), 88, lambda _session, cycle: setattr(cycle, "state", TicketReviewCycleState.CANCELLED.value)),
        (lambda cycle: review_rate_callback_data(cycle.id, 5), 88, lambda _session, cycle: setattr(cycle, "state", TicketReviewCycleState.COMPLETED.value)),
        (
            lambda cycle: review_rate_callback_data(cycle.id, 5),
            88,
            lambda _session, cycle: create_review_cycle(_session, ticket_id=cycle.ticket_id, agent_id=cycle.agent_id, client_id=cycle.client_id),
        ),
    ]:
        session = _session()
        contact, _ticket, cycle = _awaiting_rating_cycle(session)
        mutate(session, cycle)
        session.commit()
        monkeypatch.setattr(telegram_router, "answer_callback_query", lambda *args, **kwargs: {"ok": True})
        monkeypatch.setattr(telegram_router, "_safe_remove_inline_keyboard", lambda *args, **kwargs: None)

        result = telegram_router.process_telegram_update(
            session,
            _callback_payload(update_id=int(uuid.uuid4().int % 1000000), contact=contact, data=data_factory(cycle), message_id=message_id),
            notify_client=True,
        )

        assert result["review"] == "stale"
        assert _reviews(session) == []


def test_repeated_old_rating_callback_does_not_create_second_review(monkeypatch):
    session = _session()
    contact, _ticket, cycle = _awaiting_rating_cycle(session)
    monkeypatch.setattr(telegram_router, "answer_callback_query", lambda *args, **kwargs: {"ok": True})
    monkeypatch.setattr(telegram_router, "delete_message", lambda *args, **kwargs: {"ok": True})
    monkeypatch.setattr(telegram_router, "send_message", lambda *args, **kwargs: {"ok": True, "result": {"message_id": 201}})
    monkeypatch.setattr(telegram_router, "_safe_remove_inline_keyboard", lambda *args, **kwargs: None)

    telegram_router.process_telegram_update(
        session,
        _callback_payload(update_id=4101, contact=contact, data=review_rate_callback_data(cycle.id, 5), message_id=88),
        notify_client=True,
    )
    result = telegram_router.process_telegram_update(
        session,
        _callback_payload(update_id=4102, contact=contact, data=review_rate_callback_data(cycle.id, 5), message_id=88),
        notify_client=True,
    )

    assert result["review"] == "stale"
    assert len(_reviews(session)) == 1


def test_start_send_exception_keeps_cycle_consistent(monkeypatch):
    session = _session()
    contact, _ticket, cycle = _closed_review_cycle(session)
    deleted = []
    monkeypatch.setattr(telegram_router, "answer_callback_query", lambda *args, **kwargs: {"ok": True})
    monkeypatch.setattr(telegram_router, "delete_message", lambda chat_id, message_id: deleted.append((chat_id, message_id)))
    monkeypatch.setattr(telegram_router, "send_message", lambda *args, **kwargs: (_ for _ in ()).throw(RuntimeError("telegram down")))

    result = telegram_router.process_telegram_update(
        session,
        _callback_payload(update_id=4103, contact=contact, data=review_start_callback_data(cycle.id), message_id=77),
        notify_client=True,
    )
    session.refresh(cycle)

    assert result["review"] == "rating_prompt_failed"
    assert cycle.state == TicketReviewCycleState.OFFERED.value
    assert cycle.review_current_message_id == "77"
    assert deleted == []


def test_back_send_exception_keeps_rating_prompt_current(monkeypatch):
    session = _session()
    contact, _ticket, cycle = _awaiting_rating_cycle(session)
    deleted = []
    monkeypatch.setattr(telegram_router, "answer_callback_query", lambda *args, **kwargs: {"ok": True})
    monkeypatch.setattr(telegram_router, "delete_message", lambda chat_id, message_id: deleted.append((chat_id, message_id)))
    monkeypatch.setattr(telegram_router, "send_message", lambda *args, **kwargs: (_ for _ in ()).throw(RuntimeError("telegram down")))

    result = telegram_router.process_telegram_update(
        session,
        _callback_payload(update_id=4104, contact=contact, data=review_back_callback_data(cycle.id), message_id=88),
        notify_client=True,
    )
    session.refresh(cycle)

    assert result["review"] == "post_close_failed"
    assert cycle.state == TicketReviewCycleState.AWAITING_RATING.value
    assert cycle.review_current_message_id == "88"
    assert deleted == []


def test_rate_callback_from_another_client_does_not_save(monkeypatch):
    session = _session()
    contact, ticket, cycle = _awaiting_rating_cycle(session)
    other_client = Client(id=str(uuid.uuid4()), company_id=ticket.company_id, display_name="Other")
    other_contact = TelegramContact(
        id=str(uuid.uuid4()),
        company_id=ticket.company_id,
        client_id=other_client.id,
        external_user_id="300500",
        external_chat_id="300500",
    )
    session.add_all([other_client, other_contact])
    session.commit()
    monkeypatch.setattr(telegram_router, "answer_callback_query", lambda *args, **kwargs: {"ok": True})
    monkeypatch.setattr(telegram_router, "_safe_remove_inline_keyboard", lambda *args, **kwargs: None)

    result = telegram_router.process_telegram_update(
        session,
        _callback_payload(update_id=4002, contact=other_contact, data=review_rate_callback_data(cycle.id, 5), message_id=88),
        notify_client=True,
    )

    assert result["review"] == "stale"
    assert _reviews(session) == []


def test_text_prompt_failure_keeps_rating_and_safely_completes(monkeypatch):
    session = _session()
    contact, _ticket, cycle = _awaiting_rating_cycle(session)
    deleted = []
    monkeypatch.setattr(telegram_router, "answer_callback_query", lambda *args, **kwargs: {"ok": True})
    monkeypatch.setattr(telegram_router, "delete_message", lambda chat_id, message_id: deleted.append((chat_id, message_id)))
    monkeypatch.setattr(telegram_router, "send_message", lambda *args, **kwargs: (_ for _ in ()).throw(RuntimeError("telegram down")))

    result = telegram_router.process_telegram_update(
        session,
        _callback_payload(update_id=4003, contact=contact, data=review_rate_callback_data(cycle.id, 1), message_id=88),
        notify_client=True,
    )
    review = _reviews(session)[0]
    session.refresh(cycle)

    assert result["review"] == "completed_without_text_prompt"
    assert review.rating == 1
    assert review.review_text is None
    assert cycle.state == TicketReviewCycleState.COMPLETED.value
    assert cycle.review_current_message_id is None
    assert deleted == [(contact.external_chat_id, 88)]


def test_review_text_preserves_newlines_completes_and_keeps_thank_you_id(monkeypatch):
    session = _session()
    contact, _ticket, cycle, review = _awaiting_text_cycle(session)
    deleted = []
    monkeypatch.setattr(telegram_router, "delete_message", lambda chat_id, message_id: deleted.append((chat_id, message_id)) or {"ok": True})
    monkeypatch.setattr(telegram_router, "send_message", lambda *args, **kwargs: {"ok": True, "result": {"message_id": 111}})

    result = telegram_router.process_telegram_update(
        session,
        _message_payload(update_id=5001, contact=contact, text="line 1\nline  2"),
        notify_client=True,
    )
    session.refresh(cycle)
    session.refresh(review)

    assert result["review"] == "completed"
    assert review.review_text == "line 1\nline  2"
    assert review.rated_at == datetime(2026, 1, 1, 12, 0, 0)
    assert review.text_updated_at is not None
    assert cycle.state == TicketReviewCycleState.COMPLETED.value
    assert cycle.review_current_message_id == "111"
    assert deleted == [(contact.external_chat_id, 99)]

    normal = telegram_router.process_telegram_update(
        session,
        _message_payload(update_id=5002, contact=contact, text="normal after complete"),
        notify_client=False,
    )
    assert normal["ignored"] == "problem_type_required"


def test_review_text_thank_you_failure_completes_and_clears_current(monkeypatch):
    session = _session()
    contact, _ticket, cycle, review = _awaiting_text_cycle(session)
    monkeypatch.setattr(telegram_router, "delete_message", lambda *args, **kwargs: {"ok": True})
    monkeypatch.setattr(telegram_router, "send_message", lambda *args, **kwargs: (_ for _ in ()).throw(RuntimeError("telegram down")))

    result = telegram_router.process_telegram_update(
        session,
        _message_payload(update_id=5003, contact=contact, text="text"),
        notify_client=True,
    )
    session.refresh(cycle)
    session.refresh(review)

    assert result["review"] == "completed"
    assert review.review_text == "text"
    assert cycle.state == TicketReviewCycleState.COMPLETED.value
    assert cycle.review_current_message_id is None


def test_attachment_without_caption_is_not_saved_as_ticket_attachment_and_keeps_review_active(monkeypatch):
    session = _session()
    contact, _ticket, cycle, review = _awaiting_text_cycle(session)
    deleted = []
    monkeypatch.setattr(telegram_router, "delete_message", lambda chat_id, message_id: deleted.append((chat_id, message_id)) or {"ok": True})
    monkeypatch.setattr(telegram_router, "send_message", lambda *args, **kwargs: {"ok": True, "result": {"message_id": 112}})

    result = telegram_router.process_telegram_update(
        session,
        _message_payload(update_id=5004, contact=contact, attachment_kind="document"),
        notify_client=True,
    )
    session.refresh(cycle)
    session.refresh(review)

    assert result["review"] == "attachment_without_caption"
    assert cycle.state == TicketReviewCycleState.AWAITING_TEXT.value
    assert cycle.review_current_message_id == "112"
    assert review.review_text is None
    assert session.execute(select(TicketAttachment)).scalars().all() == []
    assert deleted == [(contact.external_chat_id, 99)]


def test_attachment_caption_replaces_text_and_finish_completes(monkeypatch):
    session = _session()
    contact, _ticket, cycle, review = _awaiting_text_cycle(session)
    deleted = []
    sent = []
    monkeypatch.setattr(telegram_router, "delete_message", lambda chat_id, message_id: deleted.append((chat_id, message_id)) or {"ok": True})
    monkeypatch.setattr(telegram_router, "send_message", lambda *args, **kwargs: sent.append((args, kwargs)) or {"ok": True, "result": {"message_id": 113 + len(sent)}})
    monkeypatch.setattr(telegram_router, "answer_callback_query", lambda *args, **kwargs: {"ok": True})

    first = telegram_router.process_telegram_update(
        session,
        _message_payload(update_id=5005, contact=contact, caption="caption one", attachment_kind="photo"),
        notify_client=True,
    )
    session.refresh(cycle)
    session.refresh(review)
    assert first["review"] == "caption_saved"
    assert cycle.state == TicketReviewCycleState.AWAITING_TEXT_AFTER_CAPTION.value
    assert review.review_text == "caption one"
    assert sent[-1][1]["reply_markup"]["inline_keyboard"][0][0]["text"] == "Завершить отзыв"

    second = telegram_router.process_telegram_update(
        session,
        _message_payload(update_id=5006, contact=contact, caption="caption two", attachment_kind="document"),
        notify_client=True,
    )
    session.refresh(review)
    assert second["review"] == "caption_saved"
    assert review.review_text == "caption two"

    no_caption = telegram_router.process_telegram_update(
        session,
        _message_payload(update_id=5007, contact=contact, attachment_kind="document"),
        notify_client=True,
    )
    session.refresh(cycle)
    session.refresh(review)
    assert no_caption["review"] == "attachment_without_caption"
    assert cycle.state == TicketReviewCycleState.AWAITING_TEXT_AFTER_CAPTION.value
    assert review.review_text == "caption two"
    assert sent[-1][1]["reply_markup"]["inline_keyboard"][0][0]["text"] == "Завершить отзыв"

    finish_message_id = int(cycle.review_current_message_id)
    finished = telegram_router.process_telegram_update(
        session,
        _callback_payload(update_id=5008, contact=contact, data=review_finish_callback_data(cycle.id), message_id=finish_message_id),
        notify_client=True,
    )
    session.refresh(cycle)
    session.refresh(review)
    assert finished["review"] == "completed"
    assert review.review_text == "caption two"
    assert cycle.state == TicketReviewCycleState.COMPLETED.value
    assert cycle.review_current_message_id is not None
    assert session.execute(select(TicketAttachment)).scalars().all() == []


def test_text_after_caption_replaces_caption_and_completes(monkeypatch):
    session = _session()
    contact, _ticket, cycle, review = _awaiting_text_cycle(session, review_text="caption", caption_state=True)
    monkeypatch.setattr(telegram_router, "delete_message", lambda *args, **kwargs: {"ok": True})
    monkeypatch.setattr(telegram_router, "send_message", lambda *args, **kwargs: {"ok": True, "result": {"message_id": 120}})

    telegram_router.process_telegram_update(
        session,
        _message_payload(update_id=5010, contact=contact, text="updated review text"),
        notify_client=True,
    )
    session.refresh(cycle)
    session.refresh(review)

    assert review.review_text == "updated review text"
    assert cycle.state == TicketReviewCycleState.COMPLETED.value
    assert len(_reviews(session)) == 1


def test_start_command_cleans_review_states_and_continues_start_flow(monkeypatch):
    scenarios = [
        (TicketReviewCycleState.OFFERED.value, False, "cancelled"),
        (TicketReviewCycleState.AWAITING_RATING.value, False, "cancelled"),
        (TicketReviewCycleState.AWAITING_TEXT.value, True, "completed"),
        (TicketReviewCycleState.AWAITING_TEXT_AFTER_CAPTION.value, True, "completed"),
        (TicketReviewCycleState.COMPLETED.value, True, "completed"),
    ]
    for idx, (state, has_review, expected_state) in enumerate(scenarios, start=1):
        session = _session()
        contact, ticket, cycle = _closed_review_cycle(session)
        cycle.state = state
        cycle.review_current_message_id = "130"
        review = None
        if has_review:
            if state == TicketReviewCycleState.COMPLETED.value:
                cycle.state = TicketReviewCycleState.AWAITING_RATING.value
            review = save_review_rating(
                session,
                cycle=cycle,
                rating=5,
                agent_name_snapshot="Agent",
                client_name_snapshot="Client",
                ticket_subject_snapshot="Need help",
            )
            if state == TicketReviewCycleState.AWAITING_TEXT_AFTER_CAPTION.value:
                save_review_caption(session, cycle=cycle, caption="caption")
            if state == TicketReviewCycleState.COMPLETED.value:
                cycle.state = TicketReviewCycleState.COMPLETED.value
                cycle.review_current_message_id = "130"
        session.commit()
        deleted = []
        sent = []
        monkeypatch.setattr(telegram_router, "delete_message", lambda chat_id, message_id: deleted.append((chat_id, message_id)) or {"ok": True})
        monkeypatch.setattr(telegram_router, "send_problem_menu", lambda *args, **kwargs: sent.append(("menu", args, kwargs)) or {"ok": True, "result": {"message_id": 140}})
        monkeypatch.setattr(telegram_router, "send_message", lambda *args, **kwargs: sent.append(("message", args, kwargs)) or {"ok": True, "result": {"message_id": 141}})

        result = telegram_router.process_telegram_update(
            session,
            _message_payload(update_id=6000 + idx, contact=contact, text="/start"),
            notify_client=True,
        )
        session.refresh(cycle)

        assert result["command"] == "start"
        assert deleted == [(contact.external_chat_id, 130)]
        assert cycle.review_current_message_id is None
        assert cycle.state == expected_state
        if review is not None:
            session.refresh(review)
            assert review.rating == 5


def test_restore_cleans_review_before_restore_notification(monkeypatch):
    session = _session()
    contact, ticket, cycle, review = _awaiting_text_cycle(session, review_text="caption", caption_state=True)
    deleted = []
    sent = []
    user = SimpleNamespace(id=ticket.closed_by_user_id or ticket.assignee_id, role="admin")
    ticket.closed_by_user_id = user.id
    session.commit()
    monkeypatch.setattr(telegram_router, "send_message", lambda *args, **kwargs: {"ok": True, "result": {"message_id": 1}})

    from api.app.routers import tickets as tickets_router

    monkeypatch.setattr(tickets_router, "delete_message", lambda chat_id, message_id: deleted.append((chat_id, message_id)))
    monkeypatch.setattr(tickets_router, "_send_to_telegram_client", lambda db, t, text, **kwargs: sent.append((t.id, text, kwargs)) or {"ok": True, "result": {"message_id": 150}})

    result = tickets_router.restore(ticket.id, db=session, user=user)
    session.refresh(cycle)
    session.refresh(review)

    assert result == {"ok": True}
    assert deleted == [(contact.external_chat_id, 99)]
    assert sent and "вернули в работу" in sent[0][1]
    assert cycle.state == TicketReviewCycleState.CANCELLED.value
    assert cycle.review_current_message_id is None
    assert review.review_text == "caption"


def test_reopen_cleanup_handles_offered_rating_after_rating_and_completed_states():
    for state in [
        TicketReviewCycleState.OFFERED.value,
        TicketReviewCycleState.AWAITING_RATING.value,
        TicketReviewCycleState.AWAITING_TEXT.value,
        TicketReviewCycleState.AWAITING_TEXT_AFTER_CAPTION.value,
        TicketReviewCycleState.COMPLETED.value,
    ]:
        session = _session()
        contact, ticket, cycle = _closed_review_cycle(session)
        review = None
        if state in {
            TicketReviewCycleState.AWAITING_TEXT.value,
            TicketReviewCycleState.AWAITING_TEXT_AFTER_CAPTION.value,
            TicketReviewCycleState.COMPLETED.value,
        }:
            cycle.state = TicketReviewCycleState.AWAITING_RATING.value
            review = save_review_rating(
                session,
                cycle=cycle,
                rating=4,
                agent_name_snapshot="Agent",
                client_name_snapshot="Client",
                ticket_subject_snapshot="Need help",
            )
            if state == TicketReviewCycleState.AWAITING_TEXT_AFTER_CAPTION.value:
                save_review_caption(session, cycle=cycle, caption="caption")
            if state == TicketReviewCycleState.COMPLETED.value:
                cycle.state = TicketReviewCycleState.COMPLETED.value
                cycle.completed_at = datetime.utcnow()
        else:
            cycle.state = state
        cycle.review_current_message_id = "160"
        session.commit()
        deleted = []

        cleanup_review_flow_for_ticket_reopen(
            session,
            ticket=ticket,
            contact=contact,
            delete_message_fn=lambda chat_id, message_id: deleted.append((chat_id, message_id)),
        )
        session.commit()
        session.refresh(cycle)

        assert deleted == [(contact.external_chat_id, 160)]
        assert cycle.review_current_message_id is None
        if state == TicketReviewCycleState.COMPLETED.value:
            assert cycle.state == TicketReviewCycleState.COMPLETED.value
        else:
            assert cycle.state == TicketReviewCycleState.CANCELLED.value
        if review is not None:
            session.refresh(review)
            assert review.rating == 4


def test_reopen_cleanup_without_telegram_contact_cancels_active_cycle_and_keeps_review_data():
    session = _session()
    contact, ticket, cycle, review = _awaiting_text_cycle(session, review_text="saved", caption_state=True)
    session.delete(contact)
    session.commit()
    deleted = []

    cleanup_review_flow_for_ticket_reopen(
        session,
        ticket=ticket,
        contact=None,
        delete_message_fn=lambda chat_id, message_id: deleted.append((chat_id, message_id)),
    )
    session.commit()
    session.refresh(cycle)
    session.refresh(review)

    assert deleted == []
    assert cycle.state == TicketReviewCycleState.CANCELLED.value
    assert cycle.review_current_message_id is None
    assert review.rating == 4
    assert review.review_text == "saved"


def test_reopen_cleanup_without_telegram_contact_clears_completed_current_message_id():
    session = _session()
    contact, ticket, cycle, review = _awaiting_text_cycle(session)
    cycle.state = TicketReviewCycleState.COMPLETED.value
    cycle.completed_at = datetime.utcnow()
    cycle.review_current_message_id = "170"
    session.delete(contact)
    session.commit()
    deleted = []

    cleanup_review_flow_for_ticket_reopen(
        session,
        ticket=ticket,
        contact=None,
        delete_message_fn=lambda chat_id, message_id: deleted.append((chat_id, message_id)),
    )
    session.commit()
    session.refresh(cycle)
    session.refresh(review)

    assert deleted == []
    assert cycle.state == TicketReviewCycleState.COMPLETED.value
    assert cycle.review_current_message_id is None
    assert review.rating == 4


def test_review_telegram_now_is_utc_aware():
    value = _now_utc()

    assert value.tzinfo is not None
    assert value.utcoffset().total_seconds() == 0


def test_client_name_snapshot_prefers_client_profile_full_name(monkeypatch):
    session = _session()
    contact, _ticket, cycle = _awaiting_rating_cycle(session)
    client = session.get(Client, contact.client_id)
    client.display_name = "Иван Петров"
    contact.external_display_name = "ivan777"
    session.add(
        ClientProfile(
            id=str(uuid.uuid4()),
            client_id=contact.client_id,
            last_name="Турчинский",
            contact_name="Дмитрий",
        )
    )
    session.commit()
    monkeypatch.setattr(telegram_router, "answer_callback_query", lambda *args, **kwargs: {"ok": True})
    monkeypatch.setattr(telegram_router, "delete_message", lambda *args, **kwargs: {"ok": True})
    monkeypatch.setattr(telegram_router, "send_message", lambda *args, **kwargs: {"ok": True, "result": {"message_id": 180}})

    telegram_router.process_telegram_update(
        session,
        _callback_payload(update_id=7001, contact=contact, data=review_rate_callback_data(cycle.id, 5), message_id=88),
        notify_client=True,
    )

    assert _reviews(session)[0].client_name_snapshot == "Турчинский Дмитрий"


def test_client_name_snapshot_falls_back_to_es_desk_client_display_name_without_profile(monkeypatch):
    session = _session()
    contact, _ticket, cycle = _awaiting_rating_cycle(session)
    client = session.get(Client, contact.client_id)
    client.display_name = "Иван Петров"
    contact.external_display_name = "ivan777"
    session.commit()
    monkeypatch.setattr(telegram_router, "answer_callback_query", lambda *args, **kwargs: {"ok": True})
    monkeypatch.setattr(telegram_router, "delete_message", lambda *args, **kwargs: {"ok": True})
    monkeypatch.setattr(telegram_router, "send_message", lambda *args, **kwargs: {"ok": True, "result": {"message_id": 182}})

    telegram_router.process_telegram_update(
        session,
        _callback_payload(update_id=7003, contact=contact, data=review_rate_callback_data(cycle.id, 5), message_id=88),
        notify_client=True,
    )

    assert _reviews(session)[0].client_name_snapshot == "Иван Петров"


def test_client_name_snapshot_falls_back_to_telegram_display_name(monkeypatch):
    session = _session()
    contact, _ticket, cycle = _awaiting_rating_cycle(session)
    client = session.get(Client, contact.client_id)
    client.display_name = "   "
    contact.external_display_name = "ivan777"
    session.commit()
    monkeypatch.setattr(telegram_router, "answer_callback_query", lambda *args, **kwargs: {"ok": True})
    monkeypatch.setattr(telegram_router, "delete_message", lambda *args, **kwargs: {"ok": True})
    monkeypatch.setattr(telegram_router, "send_message", lambda *args, **kwargs: {"ok": True, "result": {"message_id": 181}})

    telegram_router.process_telegram_update(
        session,
        _callback_payload(update_id=7002, contact=contact, data=review_rate_callback_data(cycle.id, 5), message_id=88, first_name="ivan777"),
        notify_client=True,
    )

    assert _reviews(session)[0].client_name_snapshot == "ivan777"


def test_back_does_not_call_edit_message_reply_markup(monkeypatch):
    session = _session()
    contact, _ticket, cycle = _awaiting_rating_cycle(session)
    deleted = []
    monkeypatch.setattr(telegram_router, "answer_callback_query", lambda *args, **kwargs: {"ok": True})
    monkeypatch.setattr(telegram_router, "send_message", lambda *args, **kwargs: {"ok": True, "result": {"message_id": 99}})
    monkeypatch.setattr(telegram_router, "edit_message_reply_markup", lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("back must not edit markup")))
    monkeypatch.setattr(telegram_router, "delete_message", lambda chat_id, message_id: deleted.append((chat_id, message_id)))

    result = telegram_router.process_telegram_update(
        session,
        _callback_payload(update_id=7003, contact=contact, data=review_back_callback_data(cycle.id), message_id=88),
        notify_client=True,
    )
    session.refresh(cycle)

    assert result["review"] == "offered"
    assert deleted == [(contact.external_chat_id, 88)]
    assert cycle.state == TicketReviewCycleState.OFFERED.value
    assert cycle.review_current_message_id == "99"


def test_first_caption_prompt_failure_completes_without_losing_caption_or_rating(monkeypatch):
    session = _session()
    contact, _ticket, cycle, review = _awaiting_text_cycle(session)
    deleted = []
    monkeypatch.setattr(telegram_router, "delete_message", lambda chat_id, message_id: deleted.append((chat_id, message_id)))
    monkeypatch.setattr(telegram_router, "send_message", lambda *args, **kwargs: (_ for _ in ()).throw(RuntimeError("telegram down")))

    result = telegram_router.process_telegram_update(
        session,
        _message_payload(update_id=7004, contact=contact, caption="caption saved", attachment_kind="document"),
        notify_client=True,
    )
    session.refresh(cycle)
    session.refresh(review)

    assert result["review"] == "caption_saved"
    assert review.rating == 4
    assert review.review_text == "caption saved"
    assert cycle.state == TicketReviewCycleState.COMPLETED.value
    assert cycle.review_current_message_id is None
    assert deleted == [(contact.external_chat_id, 99)]
    assert session.execute(select(TicketAttachment)).scalars().all() == []


def test_repeated_caption_prompt_failure_uses_same_completion_failsafe(monkeypatch):
    session = _session()
    contact, _ticket, cycle, review = _awaiting_text_cycle(session, review_text="first caption", caption_state=True)
    deleted = []
    monkeypatch.setattr(telegram_router, "delete_message", lambda chat_id, message_id: deleted.append((chat_id, message_id)))
    monkeypatch.setattr(telegram_router, "send_message", lambda *args, **kwargs: (_ for _ in ()).throw(RuntimeError("telegram down")))

    telegram_router.process_telegram_update(
        session,
        _message_payload(update_id=7005, contact=contact, caption="second caption", attachment_kind="photo"),
        notify_client=True,
    )
    session.refresh(cycle)
    session.refresh(review)

    assert review.review_text == "second caption"
    assert cycle.state == TicketReviewCycleState.COMPLETED.value
    assert cycle.review_current_message_id is None
    assert deleted == [(contact.external_chat_id, 99)]


def test_feedback_saves_once_and_repeated_start_is_stale(monkeypatch):
    session = _session()
    contact, ticket, _cycle = _closed_review_cycle(session)
    sent = []
    monkeypatch.setattr(telegram_router, "answer_callback_query", lambda *args, **kwargs: {"ok": True})
    monkeypatch.setattr(telegram_router, "delete_message", lambda *args, **kwargs: {"ok": True})
    monkeypatch.setattr(telegram_router, "send_message", lambda *args, **kwargs: sent.append((args, kwargs)) or {"ok": True, "result": {"message_id": 190 + len(sent)}})

    started = telegram_router.process_telegram_update(
        session,
        _callback_payload(update_id=8001, contact=contact, data=feedback_start_callback_data(ticket.id), message_id=77),
        notify_client=True,
    )
    completed = telegram_router.process_telegram_update(
        session,
        _message_payload(update_id=8002, contact=contact, text="Сделайте удобнее"),
        notify_client=True,
    )
    repeated = telegram_router.process_telegram_update(
        session,
        _callback_payload(update_id=8003, contact=contact, data=feedback_start_callback_data(ticket.id), message_id=77),
        notify_client=True,
    )

    assert started["feedback"] == "awaiting_text"
    assert completed["feedback"] == "completed"
    assert repeated["feedback"] == "stale"
    assert len(_feedbacks(session)) == 1
    assert _feedbacks(session)[0].text == "Сделайте удобнее"


def test_duplicate_feedback_conflict_does_not_rollback_outer_transaction(monkeypatch):
    from api.app.services import telegram_post_close

    session = _session()
    contact, ticket, _cycle = _closed_review_cycle(session)
    first = telegram_post_close.save_ticket_feedback(session, ticket=ticket, contact=contact, text="Первый")
    session.commit()

    ticket.subject = "Changed before duplicate"
    monkeypatch.setattr(telegram_post_close, "_feedback_exists", lambda *_args, **_kwargs: False)
    duplicate = telegram_post_close.save_ticket_feedback(session, ticket=ticket, contact=contact, text="Второй")
    session.flush()

    assert duplicate.id == first.id
    assert ticket.subject == "Changed before duplicate"
    assert len(_feedbacks(session)) == 1


def test_reopen_and_second_close_does_not_offer_second_feedback(monkeypatch):
    session = _session()
    contact, ticket, _cycle = _closed_review_cycle(session)
    session.add(
        TicketFeedback(
            id=str(uuid.uuid4()),
            ticket_id=ticket.id,
            client_id=contact.client_id,
            client_name_snapshot="Client",
            ticket_title_snapshot="Need help",
            text="Уже отправлено",
            submitted_at=datetime.utcnow(),
            created_at=datetime.utcnow(),
            updated_at=datetime.utcnow(),
        )
    )
    ticket.status = TicketStatus.PENDING.value
    agent = session.get(User, ticket.assignee_id)
    agent_message = TicketMessage(
        id=str(uuid.uuid4()),
        ticket_id=ticket.id,
        message_type=MessageType.AGENT.value,
        body="Повторный ответ",
        author_user_id=agent.id,
        created_at=datetime.utcnow(),
    )
    session.add(agent_message)
    session.commit()

    sent = _close_ticket(
        session,
        ticket,
        SimpleNamespace(id=agent.id, role="agent"),
        agent_message,
        send_result={"ok": True, "result": {"message_id": 195}},
    )

    labels = _markup_labels(sent[0][1]["reply_markup"])
    assert "Предложения и замечания" not in labels
    assert len(_feedbacks(session)) == 1


def test_feedback_back_cancels_only_feedback_input_and_returns_actions(monkeypatch):
    session = _session()
    contact, ticket, cycle = _closed_review_cycle(session)
    sent = []
    monkeypatch.setattr(telegram_router, "answer_callback_query", lambda *args, **kwargs: {"ok": True})
    monkeypatch.setattr(telegram_router, "delete_message", lambda *args, **kwargs: {"ok": True})
    monkeypatch.setattr(telegram_router, "send_message", lambda *args, **kwargs: sent.append((args, kwargs)) or {"ok": True, "result": {"message_id": 200 + len(sent)}})

    telegram_router.process_telegram_update(
        session,
        _callback_payload(update_id=8011, contact=contact, data=feedback_start_callback_data(ticket.id), message_id=77),
        notify_client=True,
    )
    input_state = session.execute(select(TicketFeedbackInput)).scalar_one()
    result = telegram_router.process_telegram_update(
        session,
        _callback_payload(update_id=8012, contact=contact, data=feedback_back_callback_data(input_state.id), message_id=int(input_state.current_message_id)),
        notify_client=True,
    )
    session.refresh(input_state)
    session.refresh(cycle)

    assert result["feedback"] == "cancelled"
    assert input_state.state == TicketFeedbackState.CANCELLED.value
    assert _feedbacks(session) == []
    assert cycle.state == TicketReviewCycleState.OFFERED.value
    assert sent[-1][0][1] == telegram_router.TICKET_CLOSED_CLIENT_TEXT
    assert _markup_labels(sent[-1][1]["reply_markup"]) == ["Оставить отзыв", "Предложения и замечания"]


def test_start_command_cleans_feedback_input_without_saving(monkeypatch):
    session = _session()
    contact, ticket, _cycle = _closed_review_cycle(session)
    input_state = TicketFeedbackInput(
        id=str(uuid.uuid4()),
        ticket_id=ticket.id,
        client_id=contact.client_id,
        state=TicketFeedbackState.AWAITING_TEXT.value,
        current_message_id="222",
        created_at=datetime.utcnow(),
        updated_at=datetime.utcnow(),
    )
    session.add(input_state)
    session.commit()
    monkeypatch.setattr(telegram_router, "delete_message", lambda *args, **kwargs: {"ok": True})
    monkeypatch.setattr(telegram_router, "send_problem_menu", lambda *args, **kwargs: {"ok": True, "result": {"message_id": 223}})
    monkeypatch.setattr(telegram_router, "send_message", lambda *args, **kwargs: {"ok": True, "result": {"message_id": 224}})

    result = telegram_router.process_telegram_update(
        session,
        _message_payload(update_id=8021, contact=contact, text="/start"),
        notify_client=True,
    )
    session.refresh(input_state)

    assert result["command"] == "start"
    assert input_state.state == TicketFeedbackState.CANCELLED.value
    assert input_state.current_message_id is None
    assert _feedbacks(session) == []


def test_review_completion_offers_feedback_and_feedback_completion_can_offer_review(monkeypatch):
    session = _session()
    contact, ticket, cycle, review = _awaiting_text_cycle(session)
    sent = []
    monkeypatch.setattr(telegram_router, "delete_message", lambda *args, **kwargs: {"ok": True})
    monkeypatch.setattr(telegram_router, "send_message", lambda *args, **kwargs: sent.append((args, kwargs)) or {"ok": True, "result": {"message_id": 230 + len(sent)}})

    telegram_router.process_telegram_update(
        session,
        _message_payload(update_id=8031, contact=contact, text="Отзыв"),
        notify_client=True,
    )
    assert _markup_labels(sent[-1][1]["reply_markup"]) == ["Предложения и замечания"]
    session.refresh(review)
    assert review.review_text == "Отзыв"

    session2 = _session()
    contact2, ticket2, _cycle2 = _closed_review_cycle(session2)
    sent2 = []
    monkeypatch.setattr(telegram_router, "answer_callback_query", lambda *args, **kwargs: {"ok": True})
    monkeypatch.setattr(telegram_router, "send_message", lambda *args, **kwargs: sent2.append((args, kwargs)) or {"ok": True, "result": {"message_id": 240 + len(sent2)}})
    telegram_router.process_telegram_update(
        session2,
        _callback_payload(update_id=8032, contact=contact2, data=feedback_start_callback_data(ticket2.id), message_id=77),
        notify_client=True,
    )
    telegram_router.process_telegram_update(
        session2,
        _message_payload(update_id=8033, contact=contact2, text="Предложение"),
        notify_client=True,
    )
    assert _markup_labels(sent2[-1][1]["reply_markup"]) == ["Оставить отзыв"]


def test_feedback_thank_you_review_action_becomes_current_and_starts_review(monkeypatch):
    session = _session()
    contact, ticket, cycle = _closed_review_cycle(session)
    sent = []
    monkeypatch.setattr(telegram_router, "answer_callback_query", lambda *args, **kwargs: {"ok": True})
    monkeypatch.setattr(telegram_router, "delete_message", lambda *args, **kwargs: {"ok": True})
    monkeypatch.setattr(telegram_router, "send_message", lambda *args, **kwargs: sent.append((args, kwargs)) or {"ok": True, "result": {"message_id": 240 + len(sent)}})

    telegram_router.process_telegram_update(
        session,
        _callback_payload(update_id=8034, contact=contact, data=feedback_start_callback_data(ticket.id), message_id=77),
        notify_client=True,
    )
    input_state = session.execute(select(TicketFeedbackInput)).scalar_one()
    assert input_state.current_message_id == "241"

    feedback_result = telegram_router.process_telegram_update(
        session,
        _message_payload(update_id=8035, contact=contact, text="Предложение"),
        notify_client=True,
    )
    session.refresh(cycle)

    assert feedback_result["feedback"] == "completed"
    assert _markup_labels(sent[-1][1]["reply_markup"]) == ["Оставить отзыв"]
    assert cycle.state == TicketReviewCycleState.OFFERED.value
    assert cycle.review_current_message_id == "242"

    review_result = telegram_router.process_telegram_update(
        session,
        _callback_payload(
            update_id=8036,
            contact=contact,
            data=review_start_callback_data(cycle.id),
            message_id=int(cycle.review_current_message_id),
        ),
        notify_client=True,
    )
    session.refresh(cycle)

    assert review_result["review"] == "awaiting_rating"
    assert cycle.state == TicketReviewCycleState.AWAITING_RATING.value
    assert cycle.review_current_message_id == "243"


def test_back_keeps_completed_other_action_and_shows_remaining_buttons(monkeypatch):
    session = _session()
    contact, ticket, cycle = _closed_review_cycle(session)
    session.add(
        TicketFeedback(
            id=str(uuid.uuid4()),
            ticket_id=ticket.id,
            client_id=contact.client_id,
            client_name_snapshot="Client",
            ticket_title_snapshot="Need help",
            text="Already sent",
            submitted_at=datetime.utcnow(),
            created_at=datetime.utcnow(),
            updated_at=datetime.utcnow(),
        )
    )
    start_review_rating_cycle(session, cycle=cycle)
    set_review_current_message_id(session, cycle=cycle, message_id="88")
    session.commit()

    sent = []
    monkeypatch.setattr(telegram_router, "answer_callback_query", lambda *args, **kwargs: {"ok": True})
    monkeypatch.setattr(telegram_router, "delete_message", lambda *args, **kwargs: {"ok": True})
    monkeypatch.setattr(telegram_router, "send_message", lambda *args, **kwargs: sent.append((args, kwargs)) or {"ok": True, "result": {"message_id": 290}})

    result = telegram_router.process_telegram_update(
        session,
        _callback_payload(update_id=8037, contact=contact, data=review_back_callback_data(cycle.id), message_id=88),
        notify_client=True,
    )
    session.refresh(cycle)

    assert result["review"] == "offered"
    assert sent[0][0][1] == telegram_router.TICKET_CLOSED_CLIENT_TEXT
    assert _markup_labels(sent[0][1]["reply_markup"]) == ["Оставить отзыв"]
    assert cycle.state == TicketReviewCycleState.OFFERED.value
    assert cycle.review_current_message_id == "290"
    assert len(_feedbacks(session)) == 1


def test_task_feedback_back_returns_task_completion_message_without_review(monkeypatch):
    session = _session()
    company, client, contact, agent, _admin = _seed_base(session)
    task, _message = _seed_ticket(session, company, client, agent, workflow_type=TicketWorkflowType.TASK.value)
    task.status = TicketStatus.CLOSED.value
    session.commit()

    sent = []
    monkeypatch.setattr(telegram_router, "answer_callback_query", lambda *args, **kwargs: {"ok": True})
    monkeypatch.setattr(telegram_router, "delete_message", lambda *args, **kwargs: {"ok": True})
    monkeypatch.setattr(telegram_router, "send_message", lambda *args, **kwargs: sent.append((args, kwargs)) or {"ok": True, "result": {"message_id": 300 + len(sent)}})

    started = telegram_router.process_telegram_update(
        session,
        _callback_payload(update_id=8038, contact=contact, data=feedback_start_callback_data(task.id), message_id=300),
        notify_client=True,
    )
    input_state = session.execute(select(TicketFeedbackInput)).scalar_one()
    result = telegram_router.process_telegram_update(
        session,
        _callback_payload(update_id=8039, contact=contact, data=feedback_back_callback_data(input_state.id), message_id=int(input_state.current_message_id)),
        notify_client=True,
    )
    session.refresh(input_state)

    assert started["feedback"] == "awaiting_text"
    assert result["feedback"] == "cancelled"
    assert input_state.state == TicketFeedbackState.CANCELLED.value
    assert 'По Вашей задаче "Need help"' in sent[-1][0][1]
    assert "Получен ответ:" in sent[-1][0][1]
    assert "Done" in sent[-1][0][1]
    assert _markup_labels(sent[-1][1]["reply_markup"]) == ["Предложения и замечания"]
    assert _cycles(session) == []
    assert _feedbacks(session) == []


def test_no_post_close_buttons_after_both_actions_and_task_feedback_has_no_review(monkeypatch):
    session = _session()
    contact, ticket, cycle, _review = _awaiting_text_cycle(session)
    sent = []
    monkeypatch.setattr(telegram_router, "answer_callback_query", lambda *args, **kwargs: {"ok": True})
    monkeypatch.setattr(telegram_router, "delete_message", lambda *args, **kwargs: {"ok": True})
    monkeypatch.setattr(telegram_router, "send_message", lambda *args, **kwargs: sent.append((args, kwargs)) or {"ok": True, "result": {"message_id": 250 + len(sent)}})
    telegram_router.process_telegram_update(session, _message_payload(update_id=8041, contact=contact, text="Отзыв"), notify_client=True)
    telegram_router.process_telegram_update(session, _callback_payload(update_id=8042, contact=contact, data=feedback_start_callback_data(ticket.id), message_id=251), notify_client=True)
    telegram_router.process_telegram_update(session, _message_payload(update_id=8043, contact=contact, text="Предложение"), notify_client=True)
    session.refresh(cycle)
    assert cycle.state == TicketReviewCycleState.COMPLETED.value
    assert sent[-1][1]["reply_markup"] is None

    session2 = _session()
    company, client, contact2, agent, _admin = _seed_base(session2)
    task, _message = _seed_ticket(session2, company, client, agent, workflow_type=TicketWorkflowType.TASK.value)
    task.status = TicketStatus.CLOSED.value
    session2.commit()
    sent2 = []
    monkeypatch.setattr(telegram_router, "send_message", lambda *args, **kwargs: sent2.append((args, kwargs)) or {"ok": True, "result": {"message_id": 260 + len(sent2)}})
    telegram_router.process_telegram_update(session2, _callback_payload(update_id=8044, contact=contact2, data=feedback_start_callback_data(task.id), message_id=260), notify_client=True)
    telegram_router.process_telegram_update(session2, _message_payload(update_id=8045, contact=contact2, text="Task feedback"), notify_client=True)
    assert sent2[-1][1]["reply_markup"] is None


def test_feedback_media_during_input_is_not_saved(monkeypatch):
    session = _session()
    contact, ticket, _cycle = _closed_review_cycle(session)
    input_state = TicketFeedbackInput(
        id=str(uuid.uuid4()),
        ticket_id=ticket.id,
        client_id=contact.client_id,
        state=TicketFeedbackState.AWAITING_TEXT.value,
        current_message_id="270",
        created_at=datetime.utcnow(),
        updated_at=datetime.utcnow(),
    )
    session.add(input_state)
    session.commit()
    monkeypatch.setattr(telegram_router, "delete_message", lambda *args, **kwargs: {"ok": True})
    monkeypatch.setattr(telegram_router, "send_message", lambda *args, **kwargs: {"ok": True, "result": {"message_id": 271}})

    result = telegram_router.process_telegram_update(
        session,
        _message_payload(update_id=8051, contact=contact, attachment_kind="document"),
        notify_client=True,
    )
    session.refresh(input_state)

    assert result["feedback"] == "text_required"
    assert input_state.state == TicketFeedbackState.AWAITING_TEXT.value
    assert _feedbacks(session) == []
