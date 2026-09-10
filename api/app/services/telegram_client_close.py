from __future__ import annotations

import json
import uuid
from datetime import datetime

from sqlalchemy import and_, select
from sqlalchemy.orm import Session

from ..models import (
    MessageType,
    TelegramContact,
    TelegramUpdate,
    Ticket,
    TicketIntakeState,
    TicketMessage,
    TicketStatus,
    TicketWorkflowType,
)
from .automatic_messages import render_automatic_message_for_telegram
from .telegram_post_close import (
    attributable_review_agent_id_for_client_close,
    finalize_review_offer_close_message,
    post_close_actions_markup,
    prepare_review_offer_cycle,
)
from .telegram_text_format import prepare_telegram_html_message, strip_telegram_html


CLIENT_CLOSE_MESSAGE_KEY = "telegram.ticket.close.client"
UNASSIGNED_CLIENT_CLOSE_MESSAGE_KEY = "telegram.ticket.close.unassigned.client"
UNASSIGNED_CLIENT_CLOSE_FALLBACK_TEXT = (
    "Ваше обращение закрыто. Если Вам снова потребуется помощь технической поддержки, нажмите /start."
)


def store_client_close_button(ticket: Ticket, response: dict, *, response_message_id_fn) -> None:
    message_id = response_message_id_fn(response)
    if message_id is not None:
        ticket.client_close_message_id = message_id


def remove_client_close_button(
    db: Session,
    ticket: Ticket,
    *,
    remove_inline_keyboard_fn,
) -> None:
    if not ticket.client_close_message_id:
        return
    contact = db.execute(select(TelegramContact).where(TelegramContact.client_id == ticket.client_id)).scalar_one_or_none()
    if contact is None:
        ticket.client_close_message_id = None
        return
    try:
        remove_inline_keyboard_fn(contact.external_chat_id, int(ticket.client_close_message_id))
    except Exception:
        pass
    ticket.client_close_message_id = None


def has_agent_message(db: Session, ticket: Ticket) -> bool:
    return db.execute(
        select(TicketMessage.id)
        .where(and_(TicketMessage.ticket_id == ticket.id, TicketMessage.message_type == MessageType.AGENT.value))
        .limit(1)
    ).first() is not None


def client_can_close_ticket(db: Session, ticket: Ticket) -> bool:
    if ticket.status == TicketStatus.CLOSED.value:
        return False
    if ticket.workflow_type == TicketWorkflowType.TASK.value:
        return ticket.assignee_id is None
    if ticket.workflow_type == TicketWorkflowType.AUTO.value:
        return ticket.assignee_id is None
    return not has_agent_message(db, ticket)


def is_unassigned_client_close_ticket(ticket: Ticket) -> bool:
    workflow_type = ticket.workflow_type or TicketWorkflowType.TICKET.value
    return workflow_type == TicketWorkflowType.TICKET.value and ticket.assignee_id is None


def _prepare_close_text_for_telegram(text: str) -> tuple[str, str | None]:
    try:
        return prepare_telegram_html_message(text)
    except Exception:
        return strip_telegram_html(text), None


def _render_client_close_message_for_telegram(
    db: Session,
    *,
    ticket: Ticket,
    contact: TelegramContact,
    fallback: str,
    key: str | None,
) -> tuple[str, str | None]:
    if key is not None:
        try:
            return render_automatic_message_for_telegram(db, key, fallback, ticket=ticket, contact=contact)
        except Exception:
            return _prepare_close_text_for_telegram(fallback)
    return _prepare_close_text_for_telegram(fallback)


def send_client_close_notification(
    db: Session,
    *,
    ticket: Ticket,
    contact: TelegramContact,
    external_chat_id: str,
    send_message_fn,
    ticket_closed_client_text: str,
    ticket_closed_client_message_key: str | None = None,
) -> tuple[dict | None, object | None]:
    if is_unassigned_client_close_ticket(ticket):
        text, parse_mode = _render_client_close_message_for_telegram(
            db,
            ticket=ticket,
            contact=contact,
            fallback=UNASSIGNED_CLIENT_CLOSE_FALLBACK_TEXT,
            key=UNASSIGNED_CLIENT_CLOSE_MESSAGE_KEY,
        )
        return (
            send_message_fn(
                external_chat_id,
                text,
                parse_mode=parse_mode,
                remove_keyboard=True,
                reply_markup=None,
            ),
            None,
        )

    review_agent_id = attributable_review_agent_id_for_client_close(db, ticket)
    review_cycle = prepare_review_offer_cycle(db, ticket=ticket, agent_id=review_agent_id)
    reply_markup = post_close_actions_markup(db, ticket=ticket, contact=contact, review_cycle=review_cycle)
    text, parse_mode = _render_client_close_message_for_telegram(
        db,
        ticket=ticket,
        contact=contact,
        fallback=ticket_closed_client_text,
        key=ticket_closed_client_message_key,
    )
    close_response = send_message_fn(
        external_chat_id,
        text,
        parse_mode=parse_mode,
        remove_keyboard=reply_markup is None,
        reply_markup=reply_markup,
    )
    if review_cycle is not None:
        finalize_review_offer_close_message(db, cycle=review_cycle, close_response=close_response)
    return close_response, review_cycle


def close_active_ticket_for_contact(
    db: Session,
    contact,
    *,
    active_ticket_for_client_fn,
    collecting_ticket_for_client_fn,
    remove_menu_keyboard_fn,
    remove_client_close_button_fn,
    append_system_message_fn,
    fallback_message_id: int | None = None,
) -> Ticket | None:
    ticket = active_ticket_for_client_fn(db, contact.company_id, contact.client_id) or collecting_ticket_for_client_fn(
        db,
        contact.company_id,
        contact.client_id,
    )
    if ticket is None:
        return None

    ticket.status = TicketStatus.CLOSED.value
    ticket.closed_at = datetime.utcnow()
    ticket.is_frozen = False
    ticket.freeze_until = None
    if ticket.intake_state == TicketIntakeState.COLLECTING.value:
        ticket.intake_state = TicketIntakeState.CANCELLED.value
    ticket.updated_at = datetime.utcnow()
    remove_menu_keyboard_fn(
        db,
        ticket,
        fallback_chat_id=contact.external_chat_id,
        fallback_message_id=fallback_message_id,
    )
    remove_client_close_button_fn(db, ticket)
    append_system_message_fn(db, ticket, "Обращение закрыто клиентом через /close")
    return ticket


def process_client_close_callback(
    db: Session,
    payload: dict,
    callback: dict,
    notify_client: bool,
    *,
    first_company_fn,
    get_or_create_contact_from_user_fn,
    collecting_ticket_for_client_fn,
    active_ticket_for_client_fn,
    client_can_close_ticket_fn,
    remove_menu_keyboard_fn,
    append_system_message_fn,
    answer_callback_query_fn,
    safe_remove_inline_keyboard_fn,
    send_message_fn,
    ticket_closed_client_text: str,
    ticket_closed_client_message_key: str | None = None,
) -> dict:
    callback_id = str(callback.get("id", ""))
    message = callback.get("message") or {}
    chat = message.get("chat") or {}
    from_user = callback.get("from") or {}
    external_chat_id = str(chat.get("id", ""))
    message_id = message.get("message_id")

    db.add(
        TelegramUpdate(
            id=str(uuid.uuid4()),
            update_id=str(payload.get("update_id")),
            external_chat_id=external_chat_id or None,
            payload_json=json.dumps(payload, ensure_ascii=False),
            created_at=datetime.utcnow(),
        )
    )

    company = first_company_fn(db)
    if company is None or not external_chat_id:
        db.commit()
        return {"ok": True, "ignored": "invalid_client_close"}

    contact = get_or_create_contact_from_user_fn(db, company.id, external_chat_id, from_user)
    ticket = collecting_ticket_for_client_fn(db, contact.company_id, contact.client_id) or active_ticket_for_client_fn(
        db,
        contact.company_id,
        contact.client_id,
    )
    if ticket is None:
        db.commit()
        if notify_client and callback_id:
            answer_callback_query_fn(callback_id, "Активное обращение не найдено")
        return {"ok": True, "closed": False}

    if not client_can_close_ticket_fn(db, ticket):
        db.commit()
        if notify_client and callback_id:
            answer_callback_query_fn(callback_id, "Это обращение уже нельзя закрыть этой кнопкой")
        return {"ok": True, "closed": False, "ticket_id": ticket.id}

    ticket.status = TicketStatus.CLOSED.value
    ticket.closed_at = datetime.utcnow()
    ticket.is_frozen = False
    ticket.freeze_until = None
    ticket.intake_state = (
        TicketIntakeState.CANCELLED.value
        if ticket.intake_state == TicketIntakeState.COLLECTING.value
        else ticket.intake_state
    )
    ticket.client_close_message_id = None
    remove_menu_keyboard_fn(
        db,
        ticket,
        fallback_chat_id=external_chat_id,
        fallback_message_id=message_id if isinstance(message_id, int) else None,
    )
    append_system_message_fn(db, ticket, "Обращение закрыто клиентом через кнопку")
    db.commit()

    if notify_client:
        if callback_id:
            answer_callback_query_fn(callback_id, "Обращение закрыто")
        if isinstance(message_id, int):
            safe_remove_inline_keyboard_fn(external_chat_id, message_id)
        _close_response, review_cycle = send_client_close_notification(
            db,
            ticket=ticket,
            contact=contact,
            external_chat_id=external_chat_id,
            send_message_fn=send_message_fn,
            ticket_closed_client_text=ticket_closed_client_text,
            ticket_closed_client_message_key=ticket_closed_client_message_key,
        )
        if review_cycle is not None:
            db.commit()

    return {"ok": True, "closed": True, "ticket_id": ticket.id}
