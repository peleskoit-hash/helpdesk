"""One deal lookup per client attempt; never hands an automation to staff."""
from datetime import datetime, timezone
import re

from sqlalchemy import select

from ..config import settings
from ..models import TelegramContact, Ticket, TicketIntakeState, TicketStatus
from .automatic_messages import render_automatic_message_for_telegram
from .bitrix_client import BitrixApiError, BitrixConfigError, bitrix_get
from .telegram_bot import client_close_markup
from .telegram_group_automation import chat_lock

KEY = "client_origin"


def now():
    return datetime.now(timezone.utc).timestamp()


def waiting(ticket):
    return (ticket is not None and ticket.status != TicketStatus.CLOSED.value
            and ticket.intake_state == TicketIntakeState.COLLECTING.value
            and isinstance((ticket.quick_action_state_json or {}).get(KEY), dict))


def touch(ticket):
    state = dict(ticket.quick_action_state_json or {})
    state[KEY] = {**state.get(KEY, {}), "deadline": now() + 600}
    ticket.quick_action_state_json = state


def send(db, ticket, chat, key, notify, callbacks, *, terminal=False, **extra):
    if not notify:
        return
    text, mode = render_automatic_message_for_telegram(db, f"telegram.client_origin.{key}", ticket=ticket, extra=extra)
    response = callbacks["send"](chat, text, parse_mode=mode,
                                 reply_markup=None if terminal else client_close_markup())
    if not terminal:
        callbacks["store_close"](ticket, response)
        state = dict(ticket.quick_action_state_json or {})
        origin = dict(state[KEY])
        ids = list(origin.get("close_message_ids", []))
        if ticket.client_close_message_id and ticket.client_close_message_id not in ids:
            ids.append(ticket.client_close_message_id)
        state[KEY] = {**origin, "close_message_ids": ids}
        ticket.quick_action_state_json = state
        db.commit()
    return response


def owns_close_button(db, contact, message_id):
    if contact is None or message_id is None:
        return False
    states = db.scalars(select(Ticket.quick_action_state_json).where(
        Ticket.client_id == contact.client_id, Ticket.company_id == contact.company_id,
        Ticket.quick_action_state_json.is_not(None),
    ))
    return any(str(message_id) in (state or {}).get(KEY, {}).get("close_message_ids", []) for state in states)


def save_delivery(db, ticket, delivery):
    state = dict(ticket.quick_action_state_json)
    state[KEY] = {**state[KEY], "finish": dict(delivery)}
    ids = list(state[KEY].get("close_message_ids", []))
    if ticket.client_close_message_id and ticket.client_close_message_id not in ids:
        ids.append(ticket.client_close_message_id)
    state[KEY]["close_message_ids"] = ids
    ticket.quick_action_state_json = state
    db.commit()


def begin(db, ticket, chat, notify, callbacks):
    ticket.load_counted = False
    touch(ticket)
    db.commit()
    send(db, ticket, chat, "prompt", notify, callbacks)
    return {"ok": True, "ticket_id": ticket.id, "automation_collecting": True}


def finish(db, ticket, chat, key, notify, callbacks, *, mark=None):
    if not waiting(ticket):
        return {"ok": True, "closed": False}
    delivery = dict(ticket.quick_action_state_json[KEY].get("finish") or {})
    if not delivery:
        delivery = {"key": key, "mark": mark}
    key = delivery["key"]
    # Commit the update claim before Telegram, retaining progress across a failed final message.
    save_delivery(db, ticket, delivery)
    stages = (["result"] if delivery.get("mark") is not None else []) + [key]
    if not notify and any(not delivery.get(stage + "_message_id") for stage in stages):
        return {"ok": True, "ticket_id": ticket.id, "closed": False, "automation_delivery_pending": True}
    try:
        if notify:
            for stage in stages:
                if delivery.get(stage + "_message_id"):
                    continue
                response = send(db, ticket, chat, stage, True, callbacks, terminal=True, client_origin=delivery.get("mark"))
                message_id = (response.get("result") or {}).get("message_id")
                if not response.get("ok") or type(message_id) is not int or message_id <= 0:
                    raise ValueError("Telegram did not confirm message_id")
                delivery[stage + "_message_id"] = message_id
                save_delivery(db, ticket, delivery)
        callbacks["remove_close"](db, ticket)
        callbacks["complete"](db, ticket, chat, False)
        callbacks["append_system"](db, ticket, f"Откуда клиент: {key}", agent_visible=True)
        db.commit()
    except Exception:
        db.rollback()
        db.refresh(ticket)
        return {"ok": True, "ticket_id": ticket.id, "closed": ticket.status == TicketStatus.CLOSED.value,
                "automation_delivery_pending": ticket.status != TicketStatus.CLOSED.value}
    return {"ok": True, "ticket_id": ticket.id, "closed": True}


def lookup(deal_id):
    base = (settings.bitrix_deal_mark_webhook_base or "").strip()
    if not base:
        raise BitrixConfigError("Deal mark webhook is not configured")
    payload = bitrix_get("crm.deal.get.json", {"ID": deal_id}, webhook_base=base)
    result = payload.get("result")
    if result is None or result == {}:
        return "not_found", None
    if not isinstance(result, dict):
        raise BitrixApiError("Unexpected deal result")
    mark = result.get("UF_CRM_MARK")
    if mark is None or mark == "":
        return "empty", None
    if not isinstance(mark, str):
        raise BitrixApiError("Unexpected UF_CRM_MARK type")
    return ("success", mark) if mark.strip() else ("empty", None)


def extract_deal_id(body):
    match = re.fullmatch(r"([0-9]+)|https://crm\.elitesochi\.com/crm/deal/(?:show|edit|details)/([0-9]+)/", body)
    value = (match.group(1) or match.group(2)) if match else None
    return value if value and value.strip("0") else None


def incoming(db, ticket, body, message_id, attachment, chat, notify, callbacks):
    if ticket.quick_action_state_json[KEY]["deadline"] <= now():
        return finish(db, ticket, chat, "timeout", notify, callbacks)
    touch(ticket)
    message = callbacks["append_client"](db, ticket, body, message_id)
    stored = callbacks["store_attachment"](db, ticket, message, attachment, chat) if attachment else None
    # Commit the existing TelegramUpdate claim before contacting Bitrix.
    db.commit()
    if stored is not None:
        callbacks["archive"](stored.id)
    delivery = ticket.quick_action_state_json[KEY].get("finish")
    if delivery:
        return finish(db, ticket, chat, delivery["key"], notify, callbacks, mark=delivery.get("mark"))
    value = extract_deal_id(body)
    if value is None:
        send(db, ticket, chat, "invalid", notify, callbacks)
        return {"ok": True, "automation_collecting": True, "ticket_id": ticket.id}
    send(db, ticket, chat, "checking", notify, callbacks, deal_id=value)
    try:
        outcome, mark = lookup(value)
    except BitrixApiError as exc:
        outcome, mark = ("not_found" if exc.http_status == 400 and exc.description == "Not found" else "error"), None
    except Exception:
        outcome, mark = "error", None
    if outcome == "success":
        return finish(db, ticket, chat, "success", notify, callbacks, mark=mark)
    send(db, ticket, chat, outcome, notify, callbacks)
    return {"ok": True, "automation_collecting": True, "ticket_id": ticket.id}


def cleanup(db, callbacks):
    rows = db.execute(select(Ticket, TelegramContact.external_chat_id).join(
        TelegramContact, (TelegramContact.client_id == Ticket.client_id) & (TelegramContact.company_id == Ticket.company_id),
    ).where(Ticket.status != TicketStatus.CLOSED.value, Ticket.intake_state == TicketIntakeState.COLLECTING.value)).all()
    closed = 0
    for ticket, chat in rows:
        if not waiting(ticket):
            continue
        with chat_lock(db, chat, blocking=False) as acquired:
            if not acquired:
                continue
            db.refresh(ticket)
            if waiting(ticket) and ticket.quick_action_state_json[KEY]["deadline"] <= now():
                closed += finish(db, ticket, chat, "timeout", True, callbacks)["closed"]
    return closed
