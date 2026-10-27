from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..models import BotButtonSetting

SYSTEM_MENU_TYPE = "__system__"
FIRST_CONNECTION_BUTTON_KEY = "__system__|first_connection"
FIRST_CONNECTION_WORKFLOW_TYPE = "ticket"
FIRST_CONNECTION_TITLE = "\u041f\u0435\u0440\u0432\u043e\u0435 \u043f\u043e\u0434\u043a\u043b\u044e\u0447\u0435\u043d\u0438\u0435 \u043a \u0431\u043e\u0442\u0443"
REVIEW_SYSTEM_BUTTONS: tuple[dict[str, str], ...] = (
    {"key": "__system__|review|start", "text": "\u041e\u0441\u0442\u0430\u0432\u0438\u0442\u044c \u043e\u0442\u0437\u044b\u0432"},
    {"key": "__system__|review|rate_1", "text": "\u2b50"},
    {"key": "__system__|review|rate_2", "text": "\u2b50\u2b50"},
    {"key": "__system__|review|rate_3", "text": "\u2b50\u2b50\u2b50"},
    {"key": "__system__|review|rate_4", "text": "\u2b50\u2b50\u2b50\u2b50"},
    {"key": "__system__|review|rate_5", "text": "\u2b50\u2b50\u2b50\u2b50\u2b50"},
    {"key": "__system__|review|back", "text": "\u041d\u0430\u0437\u0430\u0434"},
    {"key": "__system__|review|finish", "text": "\u0417\u0430\u0432\u0435\u0440\u0448\u0438\u0442\u044c \u043e\u0442\u0437\u044b\u0432"},
)
FEEDBACK_SYSTEM_BUTTONS: tuple[dict[str, str], ...] = (
    {"key": "__system__|feedback|start", "text": "\u041f\u0440\u0435\u0434\u043b\u043e\u0436\u0435\u043d\u0438\u044f \u0438 \u0437\u0430\u043c\u0435\u0447\u0430\u043d\u0438\u044f"},
    {"key": "__system__|feedback|back", "text": "\u041d\u0430\u0437\u0430\u0434"},
)


@dataclass(frozen=True)
class FirstConnectionDefaults:
    subject: str
    client_text: str
    system_text: str


def is_system_menu_type(menu_type: str | None) -> bool:
    value = str(menu_type or "").strip()
    return bool(value) and value.startswith(SYSTEM_MENU_TYPE)


def first_connection_row(db: Session) -> BotButtonSetting | None:
    return db.execute(
        select(BotButtonSetting).where(BotButtonSetting.button_key == FIRST_CONNECTION_BUTTON_KEY)
    ).scalar_one_or_none()


def materialize_first_connection_row(db: Session, defaults: FirstConnectionDefaults) -> BotButtonSetting:
    row = first_connection_row(db)
    if row is not None:
        changed = False
        if not (row.menu_type or "").strip():
            row.menu_type = SYSTEM_MENU_TYPE
            changed = True
        if not (row.workflow_type or "").strip():
            row.workflow_type = FIRST_CONNECTION_WORKFLOW_TYPE
            changed = True
        if not (row.button_path or "").strip():
            row.button_path = FIRST_CONNECTION_TITLE
            changed = True
        if not (row.button_label or "").strip():
            row.button_label = FIRST_CONNECTION_TITLE
            changed = True
        if changed:
            row.updated_at = datetime.utcnow()
        return row

    now = datetime.utcnow()
    row = BotButtonSetting(
        id=str(uuid.uuid4()),
        stable_button_id=str(uuid.uuid4()),
        button_key=FIRST_CONNECTION_BUTTON_KEY,
        legacy_button_key=None,
        menu_type=SYSTEM_MENU_TYPE,
        workflow_type=FIRST_CONNECTION_WORKFLOW_TYPE,
        button_path=FIRST_CONNECTION_TITLE,
        button_label=FIRST_CONNECTION_TITLE,
        label_override=defaults.subject,
        label_overridden=False,
        currently_present=True,
        instruction_text=defaults.system_text,
        client_text=defaults.client_text,
        client_text_overridden=False,
        system_text=defaults.system_text,
        system_text_overridden=False,
        is_enabled=True,
        visibility_overridden=False,
        button_kind="system",
        parent_path="",
        is_archived=False,
        created_at=now,
        updated_at=now,
    )
    db.add(row)
    db.flush()
    return row


def first_connection_values(db: Session, defaults: FirstConnectionDefaults) -> dict[str, str]:
    row = first_connection_row(db)
    subject = (row.label_override or "").strip() if row is not None and row.label_overridden else ""
    system_text = (row.system_text or "").strip() if row is not None and row.system_text_overridden else ""
    return {
        "subject": subject or defaults.subject,
        "client_text": defaults.client_text,
        "system_text": system_text or defaults.system_text,
    }
