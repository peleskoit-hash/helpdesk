"""Durable, chat-locked phone lookup through the existing Bitrix button."""
from datetime import datetime, timezone
from html import escape
import re
from urllib.error import HTTPError

from sqlalchemy import or_, select

from ..models import TelegramContact, Ticket, TicketIntakeState, TicketStatus, TicketWorkflowType
from .automatic_messages import render_automatic_message_for_telegram, render_automatic_message_text
from .telegram_text_format import strip_telegram_html, prepare_telegram_html_message
from .bitrix_client import BitrixApiError, get_deal_phone_lookup
from .bitrix_deal_input import extract_deal_id
from .button_automation_types import get_button_automation_meta
from .telegram_group_automation import chat_lock
from . import bitrix_phone_deals

KEY = "bitrix_deal_phone_lookup"
PHONE_DEALS_KEY = "bitrix_phone_deal_lookup"
KEYS = {KEY, PHONE_DEALS_KEY}
PREFIX = "bitrix_phone:"
PHONE_DEALS_PREFIX = "bitrix_phone_deals:"


def automation_key(ticket):
    return PHONE_DEALS_KEY if ticket is not None and PHONE_DEALS_KEY in (ticket.quick_action_state_json or {}) else KEY


def template_prefix(ticket, *, data=""):
    return "telegram.bitrix_phone_deals" if automation_key(ticket) == PHONE_DEALS_KEY or data.startswith(PHONE_DEALS_PREFIX) else "telegram.bitrix_phone"


def row_complete(row):
    return row["complete"] if "complete" in row else bool(row.get("phone"))


def row_value(row):
    return row.get("phone") or row.get("responsible") or row.get("reason") or ("ID " + row["responsible_id"] if row.get("responsible_id") else "")


def now():
    return datetime.now(timezone.utc).timestamp()


def waiting(ticket):
    return (ticket is not None and ticket.status != TicketStatus.CLOSED.value
            and ticket.channel == "telegram"
            and ticket.intake_state == TicketIntakeState.COLLECTING.value
            and bool(KEYS.intersection(ticket.quick_action_state_json or {})))


def state(ticket):
    return dict((ticket.quick_action_state_json or {}).get(automation_key(ticket)) or {})


def save(db, ticket, **changes):
    ticket.quick_action_state_json = {**(ticket.quick_action_state_json or {}), automation_key(ticket): {**state(ticket), **changes}}
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


def markup(ticket, *, support=False, repeat=False):
    prefix = PHONE_DEALS_PREFIX if automation_key(ticket) == PHONE_DEALS_KEY else PREFIX
    buttons = []
    if repeat:
        label = "Найти другую сделку" if automation_key(ticket) == PHONE_DEALS_KEY else "Расшифр. другой номер"
        buttons.append([{"text": label, "callback_data": prefix + "repeat:" + ticket.id}])
    if support:
        buttons.append([{"text": "Связаться с сотрудником", "callback_data": prefix + "support:" + ticket.id}])
    buttons.append([{"text": "Закрыть обращение", "callback_data": prefix + "close:" + ticket.id}])
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
    template_key = "telegram.automation.bitrix_deal_format_error_client" if key == "invalid" and automation_key(ticket) == KEY else f"{template_prefix(ticket)}.{key}"
    text, mode = render_automatic_message_for_telegram(db, template_key, ticket=ticket, extra=extra)
    if key == "invalid" and extra["reason"] not in text:
        text += "\n\n" + escape(extra["reason"])
    mid = deliver(db, ticket, stage or key, lambda: callbacks["send"](
        chat, text, parse_mode=mode, reply_markup=None if terminal else markup(ticket, support=key != "checking" and state(ticket).get("phase") != "input")))
    if mid and not terminal:
        ticket.client_close_message_id = str(mid)
        save(db, ticket)
    return mid


def begin(db, ticket, chat, notify, callbacks, *, key=KEY):
    ticket.load_counted = False
    if waiting(ticket):
        return resume(db, ticket, chat, notify, callbacks)
    ticket.quick_action_state_json = {**(ticket.quick_action_state_json or {}), key: {"phase": "input", "deadline": now() + 600, "deliveries": {}}}
    db.commit()
    send_template(db, ticket, chat, "prompt", notify, callbacks)
    return {"ok": True, "ticket_id": ticket.id, "automation_collecting": True}


def reason_text(db, ticket, key, **extra):
    text, mode = render_automatic_message_for_telegram(db, f"{template_prefix(ticket)}.{key}", ticket=ticket, extra=extra)
    return strip_telegram_html(text) if mode == "HTML" else text


def result_text(rows):
    return "\n".join((f"{row['id']} — " if row["id"] else "") + row_value(row) for row in rows)


def wrap_result(db, ticket, rows, *, rich=False):
    incomplete = any(not row_complete(row) for row in rows)
    phone_deals = automation_key(ticket) == PHONE_DEALS_KEY
    key = "result_partial" if incomplete else "result_full"
    template = render_automatic_message_text(db, f"{template_prefix(ticket)}.{key}", ticket=ticket, escape_values_for_html=True)
    if "{{ответ}}" not in template:
        template += "\n\n{{ответ}}"
    answer = escape(result_text(rows))
    if phone_deals:
        answer = "\n".join((f'<a href="https://crm.elitesochi.com/crm/deal/show/{row["id"]}/">{row["id"]}</a> — ' if row["id"] else "") + escape(row_value(row)) for row in rows)
    if rich:
        answer = ("<table><tr><th>Сделка</th><th>Ответственный</th></tr>" if phone_deals else "<table><tr><th>Номер сделки</th><th>Телефон (результат)</th></tr>") + "".join(
            "<tr><td>" + (f'<a href="https://crm.elitesochi.com/crm/deal/show/{row["id"]}/">{row["id"]}</a>' if phone_deals and row["id"] else escape(row["id"])) + "</td><td>" + escape(row_value(row)) + "</td></tr>"
            for row in rows) + "</table>"
    explanation = ""
    if incomplete:
        note, note_mode = render_automatic_message_for_telegram(db, f"{template_prefix(ticket)}.partial_result", ticket=ticket)
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
    if automation_key(ticket) == PHONE_DEALS_KEY:
        heading = "Поиск сделки по номеру телефона" + (": техническая ошибка Bitrix, передано сотрудникам" if handed_off and current.get("technical") else ": передано сотрудникам по просьбе клиента" if handed_off else ": " + (current.get("finish") or "закрыто"))
        parts = [heading, "Телефон: " + current.get("phone_input", "не указан"), result_text(current.get("rows") or [])]
        if current.get("technical_error") or current.get("inflight"):
            parts.append("Поиск не завершён: " + (current.get("technical_error") or "обработка прервана"))
        if handed_off:
            parts.append("Сотруднику требуется помочь с поиском недостающих сделок, ответственных или доставкой результата.")
        callbacks["append_system"](db, ticket, "\n\n".join(filter(None, parts)), agent_visible=True)
        ticket.quick_action_state_json = {**ticket.quick_action_state_json, automation_key(ticket): {**current, "summary_saved": True}}
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
    ticket.quick_action_state_json = {**ticket.quick_action_state_json, automation_key(ticket): {**current, "summary_saved": True}}


def send_results(db, ticket, chat, notify, callbacks):
    if not notify:
        return False
    rows = state(ticket)["rows"]
    incomplete = any(not row_complete(row) for row in rows)
    keyboard = markup(ticket, support=incomplete, repeat=not incomplete)
    text, parse_mode = wrap_result(db, ticket, rows)
    if (len(rows) > 1 or automation_key(ticket) == PHONE_DEALS_KEY and rows[0]["id"]) and len(text) < 24000 and state(ticket).get("result_format") != "text":
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
    chunks = [text[offset:offset + 4000] for offset in range(0, len(text), 4000)]
    if automation_key(ticket) == PHONE_DEALS_KEY and len(text) > 4000:
        # Keep links intact when a large table needs several ordinary Telegram messages.
        text = re.sub(r'<a href="([^"]+)">(.*?)</a>', lambda match: match[2] + " " + match[1], text)
        text, parse_mode = strip_telegram_html(text), None
        chunks, chunk = [], ""
        for line in text.splitlines(keepends=True):
            if len(chunk) + len(line) > 4000 and chunk:
                chunks.append(chunk)
                chunk = ""
            while len(line) > 4000:
                chunks.append(line[:4000])
                line = line[4000:]
            chunk += line
        if chunk:
            chunks.append(chunk)
    for index, chunk in enumerate(chunks):
        mid = deliver(db, ticket, f"text_result_{index}", lambda chunk=chunk: callbacks["send"](
            chat, chunk, parse_mode=parse_mode, reply_markup=keyboard))
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
    if key == "success":
        if state(ticket).get("phase") != "success":
            save(db, ticket, finish=None, phase="success", deadline=now() + 600)
        else:
            save(db, ticket, finish=None)
        try:
            append_summary(db, ticket, callbacks, "Расшифровка номера Bitrix: success")
            db.commit()
        except Exception:
            db.rollback()
            db.refresh(ticket)
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
        ticket.quick_action_state_json = {**ticket.quick_action_state_json, automation_key(ticket): {**state(ticket), "handed_off": True, "technical": technical}}
        # The distribution and its durable marker must share one transaction.
        ticket.intake_state = TicketIntakeState.READY.value
        ticket.agent_visible = True
        ticket.load_counted = True
        if ticket.workflow_type == TicketWorkflowType.AUTO.value:
            ticket.workflow_type = TicketWorkflowType.TASK.value
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
        return lookup_failed(db, ticket, chat, notify, callbacks, current.get("technical_error") or "Обработка поиска прервана")
    if current.get("finish"):
        return finish(db, ticket, chat, current["finish"], notify, callbacks)
    if current.get("phase") == "success":
        return finish(db, ticket, chat, "success", notify, callbacks)
    if automation_key(ticket) == PHONE_DEALS_KEY and current.get("phase") == "retry_input":
        send_template(db, ticket, chat, "not_found", notify, callbacks, stage=f"not_found_{current['search_message_id']}")
        return pending(ticket)
    if current.get("rows"):
        if send_results(db, ticket, chat, notify, callbacks):
            if current.get("phase") != "results":
                save(db, ticket, phase="results", deadline=now() + 600)
        else:
            # Offer a safe exit even when Telegram's result delivery is ambiguous.
            send_template(db, ticket, chat, "delivery_pending", notify, callbacks)
    return pending(ticket)


def lookup_failed(db, ticket, chat, notify, callbacks, error):
    rows = state(ticket).get("rows") or []
    if automation_key(ticket) == PHONE_DEALS_KEY:
        prepare_deal_rows(db, ticket, rows)
    rows = [*rows, {"id": "", "responsible": reason_text(db, ticket, "error"), "complete": False}]
    save(db, ticket, rows=rows, inflight=None, technical=False, technical_error=error,
         phase="results", deadline=now() + 600, finish=None)
    return resume(db, ticket, chat, notify, callbacks)


def prepare_deal_rows(db, ticket, rows):
    for row in rows:
        if not row.get("responsible"):
            row["responsible"] = reason_text(db, ticket, "responsible_id", assigned_by_id=row["responsible_id"]) if row["responsible_id"] else reason_text(db, ticket, "responsible_missing")


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
    if (automation_key(ticket) == KEY and current.get("phase") == "results" and current.get("rows")
            and not any(current.get(key) for key in ("finish", "inflight", "technical", "technical_error"))
            and any(not row_complete(row) for row in current["rows"])
            and not any(item.get("status") in {"sending", "uncertain"}
                        for stage, item in current.get("deliveries", {}).items()
                        if stage != "checking" and not stage.startswith("invalid_"))):
        append_summary(db, ticket, callbacks, "Расшифровка номера Bitrix: partial_result")
        ticket.client_close_message_id = None
        ticket.quick_action_state_json = {**ticket.quick_action_state_json,
            KEY: {"phase": "retry_input", "deadline": now() + 600, "deliveries": {}}}
        db.commit()
        current = state(ticket)
    if current.get("rows") or current.get("finish") or current.get("inflight") or current.get("technical"):
        save(db, ticket, deadline=now() + 600)
        return resume(db, ticket, chat, notify, callbacks)
    save(db, ticket, deadline=now() + 600)
    if automation_key(ticket) == PHONE_DEALS_KEY:
        phone_input = bitrix_phone_deals.normalize_phone(body)
        if phone_input is None:
            return send_invalid(db, ticket, chat, notify, callbacks, reason_text(db, ticket, "invalid_reason"), message_id)
        save(db, ticket, phone_input=phone_input, phase="processing", rows=[], inflight=phone_input, search_message_id=message_id)
        send_template(db, ticket, chat, "checking", notify, callbacks, stage=f"checking_{message_id}")
        try:
            rows = bitrix_phone_deals.lookup(phone_input, lambda rows: save(db, ticket, rows=[dict(row) for row in rows]))
        except Exception as exc:
            return lookup_failed(db, ticket, chat, notify, callbacks, f"Техническая ошибка Bitrix ({type(exc).__name__})")
        prepare_deal_rows(db, ticket, rows)
        if not rows:
            save(db, ticket, rows=[], inflight=None, phase="retry_input", deadline=now() + 600)
            return resume(db, ticket, chat, notify, callbacks)
        success = all(row_complete(row) for row in rows)
        save(db, ticket, rows=rows, inflight=None, finish="success" if success else None)
        return finish(db, ticket, chat, "success", notify, callbacks) if success else resume(db, ticket, chat, notify, callbacks)
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
                return resume(db, ticket, chat, notify, callbacks)
        except Exception as exc:
            save(db, ticket, technical=True, rows=rows, inflight=None,
                 technical_error=f"Сделка {deal_id}: техническая ошибка Bitrix ({type(exc).__name__})")
            return resume(db, ticket, chat, notify, callbacks)
        save(db, ticket, rows=rows, inflight=None,
             finish="success" if len(rows) == len(ids) and all(row.get("phone") for row in rows) else None)
    if all(row.get("phone") for row in rows):
        return finish(db, ticket, chat, "success", notify, callbacks)
    return resume(db, ticket, chat, notify, callbacks)


def send_invalid(db, ticket, chat, notify, callbacks, error, message_id):
    send_template(db, ticket, chat, "invalid", notify, callbacks, stage=f"invalid_{message_id}", reason=error)
    return pending(ticket)


def callback(db, ticket, chat, data, message_id, notify, callbacks):
    action, _, ticket_id = data.partition(":")[2].partition(":")
    expected_prefix = PHONE_DEALS_PREFIX if automation_key(ticket) == PHONE_DEALS_KEY else PREFIX
    if (action not in {"close", "support", "repeat"} or not waiting(ticket) or ticket.id != ticket_id
            or not data.startswith(expected_prefix)
            or str(message_id) != ticket.client_close_message_id):
        db.commit()
        return {"ok": True, "closed": False, "ignored": "stale_bitrix_phone_button"}
    if not state(ticket).get("finish") and state(ticket)["deadline"] <= now():
        return finish(db, ticket, chat, "timeout_results" if state(ticket).get("rows") else "timeout_input", notify, callbacks)
    if action == "repeat":
        if state(ticket).get("phase") != "success" or state(ticket).get("finish"):
            db.commit()
            return {"ok": True, "ignored": "stale_bitrix_phone_button"}
        finish(db, ticket, chat, "success", notify, callbacks)
        if not waiting(ticket) or not state(ticket).get("summary_saved"):
            return pending(ticket)
        title = get_button_automation_meta(automation_key(ticket))["title"]
        callbacks["append_system"](db, ticket, f"Повторный запуск автоматизации: {title}", agent_visible=True)
        ticket.client_close_message_id = None
        ticket.quick_action_state_json = {**ticket.quick_action_state_json,
            automation_key(ticket): {"phase": "input", "deadline": now() + 600, "deliveries": {}}}
        db.commit()
        send_template(db, ticket, chat, "prompt", notify, callbacks)
        return pending(ticket)
    if action == "support":
        return handoff(db, ticket, chat, notify, callbacks)
    return finish(db, ticket, chat, "closed", notify, callbacks)


def cleanup(db, callbacks):
    rows = db.execute(select(Ticket, TelegramContact.external_chat_id).join(
        TelegramContact, (TelegramContact.client_id == Ticket.client_id) & (TelegramContact.company_id == Ticket.company_id),
    ).where(Ticket.status != TicketStatus.CLOSED.value, Ticket.quick_action_state_json.is_not(None),
            or_(Ticket.intake_state.is_(None), Ticket.intake_state != TicketIntakeState.COLLECTING.value, Ticket.channel == "telegram"))).all()
    closed = 0
    for ticket, chat in rows:
        if not KEYS.intersection(ticket.quick_action_state_json or {}):
            continue
        with chat_lock(db, chat, blocking=False) as acquired:
            if not acquired:
                continue
            db.refresh(ticket)
            current = state(ticket)
            if (current.get("handed_off") or current.get("inflight") or current.get("technical") or current.get("finish")
                    or current.get("phase") == "success" and not current.get("summary_saved")):
                closed += bool(resume(db, ticket, chat, True, callbacks).get("closed"))
            elif waiting(ticket) and current["deadline"] <= now():
                closed += bool(finish(db, ticket, chat, "timeout_results" if current.get("rows") else "timeout_input", True, callbacks).get("closed"))
    return closed
