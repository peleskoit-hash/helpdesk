from __future__ import annotations

from datetime import datetime

from sqlalchemy.orm import Session

from ..config import settings
from ..models import Ticket, TicketIntakeState, TicketStatus, TicketWorkflowType
from ..services.bitrix_client import BitrixApiError, BitrixConfigError, extract_deal_id, get_deal_phone_lookup, validate_deal_id

BITRIX_LOOKUP_AUTOMATION_KEY = "bitrix_deal_phone_lookup"
BITRIX_DEAL_FORMAT_ERROR_TEXT = (
    "Не удалось распознать сделку.\n\n"
    "Отправьте одним сообщением номер сделки из 5–7 цифр или ссылку вида:\n"
    "https://crm.elitesochi.com/crm/deal/show/2450346/"
)
AUTOMATION_FALLBACK_CLIENT_TEXT = "Заявка создана и передана в работу."


def _is_phone_payload_allowed(phone_text: str | None) -> bool:
    value = (phone_text or "").strip()
    if not value:
        return False
    return all(ch.isdigit() or ch == "+" for ch in value)


def get_automation_registry() -> dict[str, dict]:
    bitrix_available = bool((settings.bitrix_webhook_base or "").strip())
    return {
        BITRIX_LOOKUP_AUTOMATION_KEY: {
            "key": BITRIX_LOOKUP_AUTOMATION_KEY,
            "title": "Расшифровка номера Bitrix",
            "description": "Ищет сделку/контакт/лид в Bitrix по номеру и возвращает клиенту найденный телефон.",
            "available": bitrix_available,
            "enabled": True,
            "handler": "_process_bitrix_deal_phone_lookup_task",
        }
    }


def _handoff_automation_task_to_agent(
    db: Session,
    ticket: Ticket,
    button,
    external_chat_id: str,
    notify_client: bool,
    *,
    append_button_system_text_fn,
    ready_ticket_for_distribution_fn,
    send_message_fn,
):
    if button is not None:
        append_button_system_text_fn(db, ticket, button)
    ready_ticket_for_distribution_fn(db, ticket, button)
    db.commit()
    if notify_client:
        send_message_fn(external_chat_id, AUTOMATION_FALLBACK_CLIENT_TEXT)
    return {
        "ok": True,
        "ticket_id": ticket.id,
        "workflow_type": TicketWorkflowType.TASK.value,
        "status": ticket.status,
        "external_chat_id": external_chat_id,
        "automation_handoff": True,
    }


def _process_bitrix_deal_phone_lookup_task(
    db: Session,
    ticket: Ticket,
    button,
    client_body: str,
    telegram_message_id: str | None,
    attachment_data: dict | None,
    external_chat_id: str,
    notify_client: bool,
    *,
    append_client_message_fn,
    store_telegram_attachment_fn,
    append_system_message_fn,
    handoff_automation_task_to_agent_fn,
    remove_client_close_button_fn,
    send_message_fn,
):
    deal_id = extract_deal_id(client_body)
    if not deal_id or not validate_deal_id(deal_id):
        db.commit()
        if notify_client:
            send_message_fn(external_chat_id, BITRIX_DEAL_FORMAT_ERROR_TEXT)
        return {
            "ok": True,
            "ticket_id": ticket.id,
            "workflow_type": TicketWorkflowType.TASK.value,
            "status": ticket.status,
            "external_chat_id": external_chat_id,
            "automation_collecting": True,
        }

    client_message = append_client_message_fn(db, ticket, client_body, telegram_message_id)
    if attachment_data is not None:
        store_telegram_attachment_fn(db, ticket, client_message, attachment_data, external_chat_id)

    try:
        lookup = get_deal_phone_lookup(deal_id)
    except BitrixConfigError:
        append_system_message_fn(
            db,
            ticket,
            "Автоматизация bitrix_deal_phone_lookup не выполнена: BITRIX_WEBHOOK_BASE не задан",
            agent_visible=True,
        )
        return handoff_automation_task_to_agent_fn(db, ticket, button, external_chat_id, notify_client)
    except BitrixApiError as exc:
        append_system_message_fn(
            db,
            ticket,
            f"Автоматизация bitrix_deal_phone_lookup не выполнена: {exc}",
            agent_visible=True,
        )
        return handoff_automation_task_to_agent_fn(db, ticket, button, external_chat_id, notify_client)
    except Exception as exc:
        append_system_message_fn(
            db,
            ticket,
            f"Автоматизация bitrix_deal_phone_lookup не выполнена: {type(exc).__name__}",
            agent_visible=True,
        )
        return handoff_automation_task_to_agent_fn(db, ticket, button, external_chat_id, notify_client)

    if not lookup.phone:
        append_system_message_fn(
            db,
            ticket,
            f"Телефон не найден через Bitrix lookup. DEAL_ID={lookup.deal_id}, CONTACT_ID={lookup.contact_id or 'n/a'}, LEAD_ID={lookup.lead_id or 'n/a'}",
            agent_visible=True,
        )
        return handoff_automation_task_to_agent_fn(db, ticket, button, external_chat_id, notify_client)
    if not _is_phone_payload_allowed(lookup.phone):
        append_system_message_fn(
            db,
            ticket,
            f"Bitrix lookup вернул недопустимый формат телефона: {lookup.phone!r}. Заявка передана сотруднику.",
            agent_visible=True,
        )
        return handoff_automation_task_to_agent_fn(db, ticket, button, external_chat_id, notify_client)

    append_system_message_fn(
        db,
        ticket,
        f"Bitrix lookup выполнен: DEAL_ID={lookup.deal_id}, CONTACT_ID={lookup.contact_id or 'n/a'}, LEAD_ID={lookup.lead_id or 'n/a'}, source={lookup.source or 'n/a'}",
        agent_visible=True,
    )
    ticket.intake_state = TicketIntakeState.READY.value
    ticket.status = TicketStatus.CLOSED.value
    ticket.closed_at = datetime.utcnow()
    ticket.is_frozen = False
    ticket.freeze_until = None
    if ticket.intake_state == TicketIntakeState.COLLECTING.value:
        ticket.intake_state = TicketIntakeState.CANCELLED.value
    ticket.updated_at = datetime.utcnow()
    remove_client_close_button_fn(db, ticket)
    db.commit()
    if notify_client:
        send_message_fn(external_chat_id, lookup.phone)
        send_message_fn(external_chat_id, "Для создания новой заявки нажмите /start")
    return {
        "ok": True,
        "ticket_id": ticket.id,
        "workflow_type": TicketWorkflowType.TASK.value,
        "status": ticket.status,
        "external_chat_id": external_chat_id,
        "automation_completed": True,
    }


def resolve_effective_automation_key(
    db: Session,
    ticket: Ticket,
    button,
    *,
    button_setting_for_ticket_fn,
) -> str | None:
    row = button_setting_for_ticket_fn(db, ticket)
    if row is not None and bool(getattr(row, "automation_overridden", False)):
        return (getattr(row, "automation_key", None) or "").strip() or None
    return (getattr(button, "automation_key", None) or "").strip() or None


def process_collecting_ticket_automation(
    db: Session,
    ticket: Ticket,
    button,
    client_body: str,
    telegram_message_id: str | None,
    attachment_data: dict | None,
    *,
    attachment_too_large: bool,
    external_chat_id: str,
    notify_client: bool,
    append_system_message_fn,
    append_client_message_fn,
    store_telegram_attachment_fn,
    remove_client_close_button_fn,
    send_message_fn,
    too_large_attachment_system_text_fn,
    send_too_large_attachment_client_messages_fn,
    button_setting_for_ticket_fn,
    append_button_system_text_fn,
    ready_ticket_for_distribution_fn,
) -> dict | None:
    if button is None:
        return None

    effective_automation_key = resolve_effective_automation_key(
        db,
        ticket,
        button,
        button_setting_for_ticket_fn=button_setting_for_ticket_fn,
    )
    if not effective_automation_key:
        return None

    if effective_automation_key == BITRIX_LOOKUP_AUTOMATION_KEY:
        if attachment_too_large:
            append_system_message_fn(
                db,
                ticket,
                too_large_attachment_system_text_fn(attachment_data),
                agent_visible=True,
            )
            send_too_large_attachment_client_messages_fn(
                db,
                ticket,
                external_chat_id,
                attachment_data,
                notify_client=notify_client,
            )
            attachment_data = None
        return _process_bitrix_deal_phone_lookup_task(
            db=db,
            ticket=ticket,
            button=button,
            client_body=client_body,
            telegram_message_id=telegram_message_id,
            attachment_data=attachment_data,
            external_chat_id=external_chat_id,
            notify_client=notify_client,
            append_client_message_fn=append_client_message_fn,
            store_telegram_attachment_fn=store_telegram_attachment_fn,
            append_system_message_fn=append_system_message_fn,
            handoff_automation_task_to_agent_fn=lambda _db, _ticket, _button, _chat_id, _notify: _handoff_automation_task_to_agent(
                _db,
                _ticket,
                _button,
                _chat_id,
                _notify,
                append_button_system_text_fn=append_button_system_text_fn,
                ready_ticket_for_distribution_fn=ready_ticket_for_distribution_fn,
                send_message_fn=send_message_fn,
            ),
            remove_client_close_button_fn=remove_client_close_button_fn,
            send_message_fn=send_message_fn,
        )

    append_system_message_fn(
        db,
        ticket,
        f"Автоматизация не найдена или недоступна: {effective_automation_key}. Заявка передана сотруднику.",
        agent_visible=True,
    )
    return None
