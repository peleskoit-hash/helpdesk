from __future__ import annotations

from sqlalchemy.orm import Session

from ..config import settings
from ..models import Ticket, TicketIntakeState, TicketStatus, TicketWorkflowType
from .automatic_messages import render_automatic_message_for_telegram
from .button_automation_types import (
    AUTOMATION_TYPE_BITRIX_DEAL_PHONE_LOOKUP,
    AUTOMATION_TYPE_SEND_FILES,
    get_button_automation_meta,
    is_builtin_button_automation_key,
)

BITRIX_LOOKUP_AUTOMATION_KEY = AUTOMATION_TYPE_BITRIX_DEAL_PHONE_LOOKUP
AUTOMATION_FALLBACK_CLIENT_TEXT = "Заявка создана и передана в работу."


def get_automation_registry() -> dict[str, dict]:
    bitrix_available = bool((settings.bitrix_webhook_base or "").strip())
    return {
        "client_origin": {
            "available": bool((settings.bitrix_deal_mark_webhook_base or "").strip()),
            "enabled": True,
        },
        BITRIX_LOOKUP_AUTOMATION_KEY: {
            "key": BITRIX_LOOKUP_AUTOMATION_KEY,
            "title": "Расшифровка номера Bitrix",
            "description": "Ищет сделку/контакт/лид в Bitrix по номеру и возвращает клиенту найденный телефон.",
            "available": bitrix_available,
            "enabled": True,
            "handler": "telegram_bitrix_phone",
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
        text, parse_mode = render_automatic_message_for_telegram(
            db,
            "telegram.automation.fallback_created",
            AUTOMATION_FALLBACK_CLIENT_TEXT,
            ticket=ticket,
            extra={"reason": "automation_handoff"},
        )
        send_message_fn(external_chat_id, text, parse_mode=parse_mode)
    return {
        "ok": True,
        "ticket_id": ticket.id,
        "workflow_type": TicketWorkflowType.TASK.value,
        "status": ticket.status,
        "external_chat_id": external_chat_id,
        "automation_handoff": True,
    }


def resolve_effective_automation_key(
    db: Session,
    ticket: Ticket,
    button,
    *,
    button_setting_for_ticket_fn,
) -> str | None:
    row = button_setting_for_ticket_fn(db, ticket)
    if row is None:
        return None
    return (getattr(row, "automation_key", None) or "").strip() or None


def unsupported_automation_message(automation_key: str | None, registry: dict[str, dict] | None = None) -> str:
    meta = get_button_automation_meta(automation_key, registry or get_automation_registry()) or {}
    return (
        f"Автоматизация не найдена или недоступна: "
        f"{meta.get('key') or automation_key}. Заявка передана сотруднику."
    )


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
    if effective_automation_key == AUTOMATION_TYPE_SEND_FILES:
        return None
    if effective_automation_key in {"telegram_group", BITRIX_LOOKUP_AUTOMATION_KEY}:
        return None  # Handled before the ordinary intake and its timeouts in telegram.py.
    if is_builtin_button_automation_key(effective_automation_key):
        append_system_message_fn(
            db,
            ticket,
            unsupported_automation_message(effective_automation_key),
            agent_visible=True,
        )
        return None

    append_system_message_fn(
        db,
        ticket,
        unsupported_automation_message(effective_automation_key),
        agent_visible=True,
    )
    return None
