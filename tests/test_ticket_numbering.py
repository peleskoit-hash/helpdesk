from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

from api.app.services.automatic_messages import NOTIFICATION_BOT_RECIPIENT_TG_USERNAME_TAG
from api.app.services.ticket_numbering import (
    TICKET_NUMBER_ALLOCATION_ADVISORY_LOCK_KEY,
    ensure_ticket_number,
)


class _ScalarResult:
    def __init__(self, value):
        self.value = value

    def scalar_one(self):
        return self.value


class _FakeSession:
    def __init__(self, *, dialect_name: str, max_number: int | None = None):
        self._bind = SimpleNamespace(dialect=SimpleNamespace(name=dialect_name))
        self.max_number = max_number
        self.executed: list[tuple[str, dict | None]] = []
        self.flush_count = 0

    def get_bind(self):
        return self._bind

    def execute(self, statement, params=None):
        self.executed.append((str(statement), params))
        if "pg_advisory_xact_lock" in str(statement):
            return _ScalarResult(None)
        return _ScalarResult(self.max_number)

    def flush(self):
        self.flush_count += 1


def test_postgresql_path_takes_advisory_lock_before_reading_max():
    db = _FakeSession(dialect_name="postgresql", max_number=41)
    ticket = SimpleNamespace(ticket_number=None)

    assert ensure_ticket_number(db, ticket) == 42

    assert len(db.executed) == 2
    lock_sql, lock_params = db.executed[0]
    max_sql, _ = db.executed[1]
    assert "pg_advisory_xact_lock" in lock_sql
    assert lock_params == {"lock_key": TICKET_NUMBER_ALLOCATION_ADVISORY_LOCK_KEY}
    assert "max(tickets.ticket_number)" in max_sql
    assert ticket.ticket_number == 42
    assert db.flush_count == 1


def test_sqlite_path_does_not_execute_postgresql_specific_sql():
    db = _FakeSession(dialect_name="sqlite", max_number=7)
    ticket = SimpleNamespace(ticket_number=None)

    assert ensure_ticket_number(db, ticket) == 8

    assert len(db.executed) == 1
    assert "pg_advisory_xact_lock" not in db.executed[0][0]
    assert ticket.ticket_number == 8


def test_existing_ticket_number_does_not_allocate_or_lock():
    db = _FakeSession(dialect_name="postgresql", max_number=99)
    ticket = SimpleNamespace(ticket_number=123)

    assert ensure_ticket_number(db, ticket) == 123

    assert db.executed == []
    assert db.flush_count == 0


def test_ticket_number_allocation_returns_max_plus_one():
    db = _FakeSession(dialect_name="sqlite", max_number=None)
    first_ticket = SimpleNamespace(ticket_number=None)
    assert ensure_ticket_number(db, first_ticket) == 1

    db = _FakeSession(dialect_name="sqlite", max_number=100)
    next_ticket = SimpleNamespace(ticket_number=None)
    assert ensure_ticket_number(db, next_ticket) == 101


def test_employee_notification_runtime_does_not_duplicate_numbering_or_tg_registry():
    source = Path("api/app/services/employee_telegram_notifications.py").read_text(encoding="utf-8")
    tg_tag = NOTIFICATION_BOT_RECIPIENT_TG_USERNAME_TAG

    assert "func.max(Ticket.ticket_number)" not in source
    assert "select(func.max(Ticket.ticket_number))" not in source
    assert tg_tag.admin_label not in source
    assert tg_tag.personal_label not in source
