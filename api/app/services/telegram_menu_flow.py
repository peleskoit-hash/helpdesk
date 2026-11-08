from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..models import TelegramContact, Ticket
from .telegram_bot import delete_message, remove_inline_keyboard


def _response_message_id(response: dict) -> str | None:
    message_id = (response.get("result") or {}).get("message_id")
    return str(message_id) if message_id is not None else None


def _store_menu_message_id(ticket: Ticket, response: dict):
    message_id = _response_message_id(response)
    if message_id is not None:
        ticket.menu_message_id = message_id


def _remove_menu_keyboard(db: Session, ticket: Ticket, fallback_chat_id: str | None = None, fallback_message_id: int | None = None):
    contact_query = select(TelegramContact).where(TelegramContact.client_id == ticket.client_id)
    if fallback_chat_id is not None:
        contact_query = contact_query.where(TelegramContact.external_chat_id == fallback_chat_id)
    contact = db.execute(contact_query).scalar_one_or_none()
    chat_id = fallback_chat_id
    if chat_id is None:
        chat_id = contact.external_chat_id if contact is not None else None
    message_id_str = ticket.menu_message_id
    # A fallback may be a user's /close message: only remove tracked menus.
    message_id = int(message_id_str) if message_id_str and message_id_str.isdigit() else None
    if message_id is None and contact is not None and str(fallback_message_id) == contact.active_menu_message_id:
        message_id = fallback_message_id
    if message_id is None:
        return
    if contact is not None and contact.active_menu_message_id == str(message_id):
        contact.active_menu_message_id = None
    cleared = bool(chat_id) and _safe_delete_message(chat_id, message_id)
    if cleared:
        ticket.menu_message_id = None
    if contact is not None:
        ids = list(contact.menu_message_ids_json or [])
        ids = [mid for mid in ids if str(mid) != str(message_id)]
        if not cleared:
            ids.append(message_id)
        contact.menu_message_ids_json = ids[-20:]


def _safe_remove_inline_keyboard(chat_id: str, message_id: int | None):
    if message_id is None:
        return
    try:
        remove_inline_keyboard(chat_id, message_id)
    except Exception:
        pass


def _safe_delete_message(chat_id: str, message_id: int | None) -> bool:
    if message_id is None:
        return False
    try:
        delete_message(chat_id, message_id)
        return True
    except Exception as exc:
        if str(exc).strip() == "Bad Request: message to delete not found":
            return True
        _safe_remove_inline_keyboard(chat_id, message_id)
        return False


def _confirm_callback_data(button_ref: str, decision: str) -> str:
    return f"confirm:{decision}:{button_ref}"


def _parse_confirm_callback_data(data: str) -> tuple[str, str] | None:
    prefix = "confirm:"
    if not data.startswith(prefix):
        return None
    payload = data.removeprefix(prefix)
    parts = payload.split(":", 1)
    if len(parts) != 2:
        return None
    decision, button_ref = parts
    if decision not in {"yes", "no"}:
        legacy_parts = payload.rsplit(":", 1)
        if len(legacy_parts) != 2:
            return None
        button_ref, decision = legacy_parts
        if decision not in {"yes", "no"}:
            return None
    return button_ref, decision


def _confirmation_markup(button_ref: str) -> dict:
    return {
        "inline_keyboard": [[
            {"text": "Да", "callback_data": _confirm_callback_data(button_ref, "yes")},
            {"text": "Нет", "callback_data": _confirm_callback_data(button_ref, "no")},
        ]]
    }
