"""Durable, chat-locked phone lookup through the existing Bitrix button."""
from datetime import datetime, timezone
from html import escape
import re
from urllib.error import HTTPError

from sqlalchemy import select

from ..models import TelegramContact, Ticket, TicketIntakeState, TicketStatus
from .automatic_messages import render_automatic_message_for_telegram, render_automatic_message_text
from .telegram_text_format import strip_telegram_html, prepare_telegram_html_message
from .bitrix_client import BitrixApiError, get_deal_phone_lookup
from .bitrix_deal_input import extract_deal_id
from .telegram_group_automation import chat_lock

KEY = "bitrix_deal_phone_lookup"
PREFIX = "bitrix_phone:"


def now():
    return datetime.now(timezone.utc).timestamp()


def waiting(ticket):
    return (ticket is not None and ticket.status != TicketStatus.CLOSED.value
            and ticket.intake_state == TicketIntakeState.COLLECTING.value
            and KEY in (ticket.quick_action_state_json or {}))


def state(ticket):
    return dict((ticket.quick_action_state_json or {}).get(KEY) or {})


def save(db, ticket, **changes):
    ticket.quick_action_state_json = {**(ticket.quick_action_state_json or {}), KEY: {**state(ticket), **changes}}
    db.commit()


def parse_lines(body):
    ids = []
    for index, line in enumerate(body.replace("\r\n", "\n").replace("\r", "\n").split("\n"), 1):
        line = line.strip()
        if not line:
            continue
        value = extract_deal_id(line)
        if value is None:
            return [], ("invalid_line", {"line_number": index})
        value = value.lstrip("0")
        if value not in ids:
            ids.append(value)
    if not ids:
        return [], ("empty_input", {})
    return ([], ("limit_exceeded", {})) if len(ids) > 30 else (ids, None)


def markup(ticket, *, support=False):
    buttons = []
    if support:
        buttons.append([{"text": "Связаться с сотрудником", "callback_data": PREFIX + "support:" + ticket.id}])
    buttons.append([{"text": "Закрыть обращение", "callback_data": PREFIX + "close:" + ticket.id}])
    return {"inline_keyboard": buttons}


def confirmed(response):
    message_id = (response.get("result") or {}).get("message_id") if isinstance(response, dict) else None
    return message_id if isinstance(response, dict) and response.get("ok") and type(message_id) is int and message_id > 0 else None


def rejected(exc):
    return isinstance(exc.__cause__, HTTPError) and exc.__cause__.code in {400, 404}


def deliver(db, ticket, stage, call):
    deliveries = dict(state(ticket).get("deliveries") or {})
    prior = deliveries.get(stage) or {}
    if prior.get("message_id"):
        return prior["message_id"]
    if prior.get("status") in {"sending", "uncertain"}:
        return None  # Telegram has no idempotency key: never repeat an ambiguous delivery.
    deliveries[stage] = {"status": "sending"}
    save(db, ticket, deliveries=deliveries)
    try:
        response = call()
        message_id = confirmed(response)
        status = "confirmed" if message_id else ("failed" if response.get("ok") is False else "uncertain")
    except Exception as exc:
        message_id, status = None, "failed" if rejected(exc) else "uncertain"
    deliveries[stage] = {"status": status, "message_id": message_id}
    save(db, ticket, deliveries=deliveries)
    return message_id


def send_template(db, ticket, chat, key, notify, callbacks, *, stage=None, terminal=False, **extra):
    if not notify:
        return None
    template_key = "telegram.automation.bitrix_deal_format_error_client" if key == "invalid" else f"telegram.bitrix_phone.{key}"
    text, mode = render_automatic_message_for_telegram(db, template_key, ticket=ticket, extra=extra)
    if key == "invalid" and extra["reason"] not in text:
        text += "\n\n" + escape(extra["reason"])
    mid = deliver(db, ticket, stage or key, lambda: callbacks["send"](
        chat, text, parse_mode=mode, reply_markup=None if terminal else markup(ticket, support=key != "checking" and state(ticket).get("phase") != "input")))
    if mid and not terminal:
        ticket.client_close_message_id = str(mid)
        save(db, ticket)
    return mid


def begin(db, ticket, chat, notify, callbacks):
    ticket.load_counted = False
    if waiting(ticket):
        return resume(db, ticket, chat, notify, callbacks)
    ticket.quick_action_state_json = {**(ticket.quick_action_state_json or {}), KEY: {"phase": "input", "deadline": now() + 600, "deliveries": {}}}
    db.commit()
    send_template(db, ticket, chat, "prompt", notify, callbacks)
    return {"ok": True, "ticket_id": ticket.id, "automation_collecting": True}


def reason_text(db, ticket, key, **extra):
    text, mode = render_automatic_message_for_telegram(db, f"telegram.bitrix_phone.{key}", ticket=ticket, extra=extra)
    return strip_telegram_html(text) if mode == "HTML" else text


def result_text(rows):
    return "\n".join(f"{row['id']} — {row.get('phone') or row['reason']}" for row in rows)


def wrap_result(db, ticket, rows, *, rich=False):
    incomplete = any(not row.get("phone") for row in rows)
    key = "result_partial" if incomplete else "result_full"
    template = render_automatic_message_text(db, f"telegram.bitrix_phone.{key}", ticket=ticket, escape_values_for_html=True)
    if "{{ответ}}" not in template:
        template += "\n\n{{ответ}}"
    answer = escape(result_text(rows))
    if rich:
        answer = "<table><tr><th>Номер сделки</th><th>Телефон (результат)</th></tr>" + "".join(
            "<tr><td>" + escape(row["id"]) + "</td><td>" + escape(row.get("phone") or row["reason"]) + "</td></tr>"
            for row in rows) + "</table>"
    explanation = ""
    if incomplete:
        note, note_mode = render_automatic_message_for_telegram(db, "telegram.bitrix_phone.partial_result", ticket=ticket)
        explanation = note if note_mode == "HTML" else escape(note)
        if rich:
            explanation = "<p>" + explanation + "</p>"
    values = {"ответ": answer, "пояснение": explanation}
    text = re.sub(r"\{\{(ответ|пояснение)\}\}", lambda match: values[match.group(1)], template)
    if rich:
        return text, None
    text, mode = prepare_telegram_html_message(text)
    return (text if mode == "HTML" else strip_telegram_html(text)), mode


def append_summary(db, ticket, callbacks, heading, *, handed_off=False):
    current = state(ticket)
    if current.get("summary_saved"):
        return
    rows = current.get("rows") or []
    ids = current.get("ids") or [row["id"] for row in rows]
    processed = {row["id"] for row in rows}
    found = {row["id"] for row in rows if row.get("phone")}
    unprocessed = [value for value in ids if value not in processed]
    parts = [heading, result_text(rows) or "Обработанных сделок нет."]
    if current.get("technical_error"):
        parts.append(current["technical_error"])
    elif current.get("inflight"):
        parts.append(f"Обработка сделки {current['inflight']} прервалась; результат не получен.")
    if unprocessed:
        parts.append("Необработанные ID: " + ", ".join(unprocessed))
    if handed_off:
        remaining = [value for value in ids if value not in found]
        parts.append("Сотруднику требуется помочь с оставшимися нерасшифрованными номерами: " + ", ".join(remaining)
                     if remaining else "Нерасшифрованных номеров нет. Сотруднику требуется помочь с доставкой результата.")
    callbacks["append_system"](db, ticket, "\n\n".join(parts), agent_visible=True)
    # Persist the marker in the caller's close/distribution transaction with the summary itself.
    ticket.quick_action_state_json = {**ticket.quick_action_state_json, KEY: {**current, "summary_saved": True}}


def send_results(db, ticket, chat, notify, callbacks):
    if not notify:
        return False
    rows = state(ticket)["rows"]
    incomplete = any(not row.get("phone") for row in rows)
    keyboard = markup(ticket, support=True) if incomplete else None
    text, parse_mode = wrap_result(db, ticket, rows)
    if len(rows) > 1 and len(text) < 24000 and state(ticket).get("result_format") != "text":
        def rich():
            html, _mode = wrap_result(db, ticket, rows, rich=True)
            return callbacks["send_rich"](chat, {"html": html}, reply_markup=keyboard)
        mid = deliver(db, ticket, "rich_result", rich)
        if mid:
            if keyboard is not None:
                ticket.client_close_message_id = str(mid)
                save(db, ticket)
            return True
        if state(ticket)["deliveries"]["rich_result"]["status"] != "failed":
            return False
        save(db, ticket, result_format="text")
    for index, offset in enumerate(range(0, len(text), 4000)):
        mid = deliver(db, ticket, f"text_result_{index}", lambda offset=offset: callbacks["send"](
            chat, text[offset:offset + 4000], parse_mode=parse_mode, reply_markup=keyboard))
        if not mid:
            return False
        if keyboard is not None:
            ticket.client_close_message_id = str(mid)
            save(db, ticket)
    return True


def finish(db, ticket, chat, key, notify, callbacks):
    if not waiting(ticket):
        return {"ok": True, "closed": ticket.status == TicketStatus.CLOSED.value}
    key = state(ticket).get("finish") or key
    save(db, ticket, finish=key)
    if key == "success" and not send_results(db, ticket, chat, notify, callbacks):
        if any(item.get("status") in {"uncertain", "sending"} for item in state(ticket).get("deliveries", {}).values()):
            save(db, ticket, finish=None, phase="results", deadline=now() + 600)
            send_template(db, ticket, chat, "delivery_pending", notify, callbacks)
        return pending(ticket)
    notice = send_template(db, ticket, chat, key, notify, callbacks, terminal=True)
    if not notice:
        if (state(ticket).get("deliveries", {}).get(key) or {}).get("status") in {"uncertain", "sending"}:
            save(db, ticket, finish=None, phase="results", deadline=now() + 600)
            send_template(db, ticket, chat, "delivery_pending", notify, callbacks)
        return pending(ticket)
    try:
        callbacks["remove_close"](db, ticket)
        callbacks["complete"](db, ticket, chat, False)
        append_summary(db, ticket, callbacks, f"Расшифровка номера Bitrix: {key}")
        db.commit()
    except Exception:
        db.rollback()
        db.refresh(ticket)
        return pending(ticket)
    return {"ok": True, "closed": True, "automation_completed": key == "success", "ticket_id": ticket.id}


def pending(ticket):
    return {"ok": True, "ticket_id": ticket.id, "closed": False, "automation_collecting": True}


def handoff(db, ticket, chat, notify, callbacks, *, technical=False):
    if not state(ticket).get("handed_off"):
        ticket.quick_action_state_json = {**ticket.quick_action_state_json, KEY: {**state(ticket), "handed_off": True, "technical": technical}}
        # The distribution and its durable marker must share one transaction.
        ticket.intake_state = TicketIntakeState.READY.value
        ticket.agent_visible = True
        ticket.load_counted = True
        callbacks["ready"](db, ticket, None)
        append_summary(db, ticket, callbacks, "Расшифровка Bitrix передана сотрудникам" +
                       (": техническая ошибка API" if technical else ": по просьбе клиента"), handed_off=True)
        db.commit()
    send_template(db, ticket, chat, "unavailable" if state(ticket).get("technical") else "handoff",
                  notify, callbacks, terminal=True)
    return {"ok": True, "ticket_id": ticket.id, "automation_handoff": True}


def resume(db, ticket, chat, notify, callbacks):
    current = state(ticket)
    if current.get("handed_off"):
        return handoff(db, ticket, chat, notify, callbacks)
    if current.get("inflight") or current.get("technical"):
        return handoff(db, ticket, chat, notify, callbacks, technical=True)
    if current.get("finish"):
        return finish(db, ticket, chat, current["finish"], notify, callbacks)
    if current.get("rows"):
        if send_results(db, ticket, chat, notify, callbacks):
            if current.get("phase") != "results":
                save(db, ticket, phase="results", deadline=now() + 600)
        else:
            # Offer a safe exit even when Telegram's result delivery is ambiguous.
            send_template(db, ticket, chat, "delivery_pending", notify, callbacks)
    return pending(ticket)


def incoming(db, ticket, body, message_id, attachment, chat, notify, callbacks):
    current = state(ticket)
    message = callbacks["append_client"](db, ticket, body, message_id)
    stored = callbacks["store_attachment"](db, ticket, message, attachment, chat) if attachment else None
    db.commit()  # Claim TelegramUpdate before any external I/O.
    if stored is not None:
        callbacks["archive"](stored.id)
    if (not current.get("finish") and not current.get("inflight") and not current.get("technical")
            and current["deadline"] <= now()):
        return finish(db, ticket, chat, "timeout_results" if current.get("rows") else "timeout_input", notify, callbacks)
    if current.get("rows") or current.get("finish") or current.get("inflight") or current.get("technical"):
        save(db, ticket, deadline=now() + 600)
        return resume(db, ticket, chat, notify, callbacks)
    save(db, ticket, deadline=now() + 600)
    ids, error = parse_lines(body)
    if error:
        return send_invalid(db, ticket, chat, notify, callbacks, reason_text(db, ticket, error[0], **error[1]), message_id)
    rows = []
    save(db, ticket, ids=ids, phase="processing", rows=rows)
    send_template(db, ticket, chat, "checking", notify, callbacks)
    for deal_id in ids:
        save(db, ticket, inflight=deal_id)
        try:
            lookup = get_deal_phone_lookup(deal_id)
            reason = reason_text(db, ticket, "not_found" if "deal_not_found" in lookup.errors else "phone_missing")
            rows.append({"id": deal_id, "phone": lookup.phone, "reason": None if lookup.phone else reason})
        except BitrixApiError as exc:
            if exc.http_status == 400 and exc.description == "Not found":
                rows.append({"id": deal_id, "phone": None, "reason": reason_text(db, ticket, "not_found")})
            else:
                save(db, ticket, technical=True, rows=rows, inflight=None,
                     technical_error=f"Сделка {deal_id}: техническая ошибка Bitrix ({type(exc).__name__})")
                return handoff(db, ticket, chat, notify, callbacks, technical=True)
        except Exception as exc:
            save(db, ticket, technical=True, rows=rows, inflight=None,
                 technical_error=f"Сделка {deal_id}: техническая ошибка Bitrix ({type(exc).__name__})")
            return handoff(db, ticket, chat, notify, callbacks, technical=True)
        save(db, ticket, rows=rows, inflight=None,
             finish="success" if len(rows) == len(ids) and all(row.get("phone") for row in rows) else None)
    if all(row.get("phone") for row in rows):
        return finish(db, ticket, chat, "success", notify, callbacks)
    return resume(db, ticket, chat, notify, callbacks)


def send_invalid(db, ticket, chat, notify, callbacks, error, message_id):
    send_template(db, ticket, chat, "invalid", notify, callbacks, stage=f"invalid_{message_id}", reason=error)
    return pending(ticket)


def callback(db, ticket, chat, data, message_id, notify, callbacks):
    action, _, ticket_id = data.removeprefix(PREFIX).partition(":")
    if (action not in {"close", "support"} or not waiting(ticket) or ticket.id != ticket_id
            or str(message_id) != ticket.client_close_message_id):
        db.commit()
        return {"ok": True, "closed": False, "ignored": "stale_bitrix_phone_button"}
    if not state(ticket).get("finish") and state(ticket)["deadline"] <= now():
        return finish(db, ticket, chat, "timeout_results" if state(ticket).get("rows") else "timeout_input", notify, callbacks)
    if action == "support":
        return handoff(db, ticket, chat, notify, callbacks)
    return finish(db, ticket, chat, "closed", notify, callbacks)


def cleanup(db, callbacks):
    rows = db.execute(select(Ticket, TelegramContact.external_chat_id).join(
        TelegramContact, (TelegramContact.client_id == Ticket.client_id) & (TelegramContact.company_id == Ticket.company_id),
    ).where(Ticket.status != TicketStatus.CLOSED.value, Ticket.quick_action_state_json.is_not(None))).all()
    closed = 0
    for ticket, chat in rows:
        if KEY not in (ticket.quick_action_state_json or {}):
            continue
        with chat_lock(db, chat, blocking=False) as acquired:
            if not acquired:
                continue
            db.refresh(ticket)
            current = state(ticket)
            if current.get("handed_off") or current.get("inflight") or current.get("technical") or current.get("finish"):
                closed += bool(resume(db, ticket, chat, True, callbacks).get("closed"))
            elif waiting(ticket) and current["deadline"] <= now():
                closed += bool(finish(db, ticket, chat, "timeout_results" if current.get("rows") else "timeout_input", True, callbacks).get("closed"))
    return closed
