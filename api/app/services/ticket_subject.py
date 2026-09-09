from __future__ import annotations

import re

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..models import BotButtonSetting, Ticket, TicketTaskKind


EMOJI_RE = re.compile(
    "["
    "\U0001F300-\U0001F5FF"
    "\U0001F600-\U0001F64F"
    "\U0001F680-\U0001F6FF"
    "\U0001F700-\U0001F77F"
    "\U0001F780-\U0001F7FF"
    "\U0001F800-\U0001F8FF"
    "\U0001F900-\U0001F9FF"
    "\U0001FA00-\U0001FA6F"
    "\U0001FA70-\U0001FAFF"
    "\u2600-\u26FF"
    "\u2700-\u27BF"
    "]+",
    flags=re.UNICODE,
)


def _normalize_path(path: str) -> str:
    value = (path or "").strip()
    if not value:
        return ""
    value = value.replace("/", " → ")
    value = re.sub(r"\s*→\s*", " → ", value)
    value = re.sub(r"\s{2,}", " ", value).strip()
    return value


def _display_without_emoji(text: str) -> str:
    clean = EMOJI_RE.sub("", text or "")
    clean = re.sub(r"\s{2,}", " ", clean).strip()
    return clean


def _path_from_technical_key(key: str) -> str:
    raw = (key or "").strip()
    if not raw:
        return ""
    if "|" in raw:
        parts = raw.split("|", 2)
        if len(parts) == 3:
            return _normalize_path(parts[2])
    return ""


def resolve_ticket_subject(db: Session, ticket: Ticket) -> str:
    if (ticket.task_kind or TicketTaskKind.NORMAL.value) in {
        TicketTaskKind.GROUP_PARENT.value,
        TicketTaskKind.GROUP_CHILD.value,
    }:
        return _display_without_emoji(ticket.subject)

    key_candidates = []
    if (ticket.instruction_button_key or "").strip():
        key_candidates.append(ticket.instruction_button_key.strip())
    if (ticket.button_config_key or "").strip():
        key_candidates.append(ticket.button_config_key.strip())

    if key_candidates:
        row = db.execute(
            select(BotButtonSetting).where(BotButtonSetting.button_key.in_(key_candidates))
        ).scalars().first()
        if row is not None:
            path = _normalize_path(row.button_path or "")
            if path:
                return _display_without_emoji(path)

    for key in key_candidates:
        path = _path_from_technical_key(key)
        if path:
            return _display_without_emoji(path)

    return _display_without_emoji(ticket.subject)


def build_ticket_subject_map(db: Session, tickets: list[Ticket]) -> dict[str, str]:
    if not tickets:
        return {}

    key_by_ticket: dict[str, list[str]] = {}
    all_keys: set[str] = set()
    for ticket in tickets:
        keys: list[str] = []
        instruction_key = (ticket.instruction_button_key or "").strip()
        button_key = (ticket.button_config_key or "").strip()
        if instruction_key:
            keys.append(instruction_key)
            all_keys.add(instruction_key)
        if button_key and button_key not in keys:
            keys.append(button_key)
            all_keys.add(button_key)
        key_by_ticket[ticket.id] = keys

    rows_by_key: dict[str, BotButtonSetting] = {}
    if all_keys:
        rows = db.execute(
            select(BotButtonSetting).where(BotButtonSetting.button_key.in_(all_keys))
        ).scalars().all()
        rows_by_key = {row.button_key: row for row in rows}

    subject_map: dict[str, str] = {}
    for ticket in tickets:
        if (ticket.task_kind or TicketTaskKind.NORMAL.value) in {
            TicketTaskKind.GROUP_PARENT.value,
            TicketTaskKind.GROUP_CHILD.value,
        }:
            subject_map[ticket.id] = _display_without_emoji(ticket.subject)
            continue

        resolved = ""
        for key in key_by_ticket.get(ticket.id, []):
            row = rows_by_key.get(key)
            if row is not None:
                resolved = _normalize_path(row.button_path or "")
                if resolved:
                    break
        if not resolved:
            for key in key_by_ticket.get(ticket.id, []):
                resolved = _path_from_technical_key(key)
                if resolved:
                    break
        subject_map[ticket.id] = _display_without_emoji(resolved or ticket.subject)

    return subject_map
