from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from api.app.models import AutomaticMessageTemplate
from api.app.services import telegram_intake_hours
from api.app.services.automatic_messages import (
    get_automatic_message_definition,
    get_effective_automatic_message_text,
    list_automatic_message_definitions,
    reset_automatic_message_override,
    upsert_automatic_message_override,
)
from api.app.services.telegram_intake_hours import is_telegram_intake_open


MOSCOW = ZoneInfo("Europe/Moscow")
TEMPLATES = [
    ("telegram.after_hours.creation_blocked.client", "Создание заявки или задачи в нерабочее время", 10),
    (
        "telegram.after_hours.automation_handoff_blocked.client",
        "Передача автоматического обращения сотруднику в нерабочее время",
        20,
    ),
]


@pytest.mark.parametrize("day", range(6))
@pytest.mark.parametrize("clock, expected", [("07:49:59", False), ("07:50:00", True), ("18:59:59", True), ("19:00:00", False)])
def test_monday_through_saturday_boundaries(day, clock, expected):
    at = datetime.fromisoformat(f"2026-11-02T{clock}").replace(tzinfo=MOSCOW) + timedelta(days=day)
    assert is_telegram_intake_open(at) is expected


@pytest.mark.parametrize("hour", [8, 12, 18])
def test_sunday_is_closed(hour):
    assert is_telegram_intake_open(datetime(2026, 11, 8, hour, tzinfo=MOSCOW)) is False


@pytest.mark.parametrize("at, expected", [
    ("2026-11-07T18:59:59", True),
    ("2026-11-07T23:59:59", False),
    ("2026-11-08T00:00:00", False),
    ("2026-11-08T23:59:59", False),
    ("2026-11-09T00:00:00", False),
    ("2026-11-09T07:49:59", False),
    ("2026-11-09T07:50:00", True),
])
def test_weekend_transition(at, expected):
    assert is_telegram_intake_open(datetime.fromisoformat(at).replace(tzinfo=MOSCOW)) is expected


@pytest.mark.parametrize("at, expected", [
    ("2026-11-02T04:49:59", False),
    ("2026-11-02T04:50:00", True),
    ("2026-11-02T15:59:59", True),
    ("2026-11-02T16:00:00", False),
    ("2026-11-07T22:00:00", False),
])
def test_utc_datetime(at, expected):
    assert is_telegram_intake_open(datetime.fromisoformat(at).replace(tzinfo=timezone.utc)) is expected


def test_naive_datetime_is_rejected():
    with pytest.raises(ValueError, match="timezone-aware"):
        is_telegram_intake_open(datetime(2026, 11, 2, 12))


def test_default_uses_current_moscow_time(monkeypatch):
    class FixedDatetime(datetime):
        @classmethod
        def now(cls, tz=None):
            assert tz == MOSCOW
            return cls(2026, 11, 2, 7, 50, tzinfo=tz)

    monkeypatch.setattr(telegram_intake_hours, "datetime", FixedDatetime)
    assert is_telegram_intake_open() is True


@pytest.mark.parametrize("key, title, sort_order", TEMPLATES)
def test_templates_are_registered_editable_and_resettable(key, title, sort_order):
    definition = get_automatic_message_definition(key)
    assert definition is not None
    assert definition in list_automatic_message_definitions()
    assert definition.title == title
    assert definition.category == "after_19"
    assert definition.category_title == "После 19:00"
    assert definition.sort_order == sort_order
    assert definition.runtime_enabled is True
    assert definition.description
    assert "Пн–Сб 08:00–19:00 МСК" in definition.default_text
    assert "воскресенье — выходной" in definition.default_text

    engine = create_engine("sqlite:///:memory:")
    AutomaticMessageTemplate.__table__.create(engine)
    with Session(engine) as db:
        assert get_effective_automatic_message_text(db, key) == definition.default_text
        upsert_automatic_message_override(db, key, "Изменённый текст")
        db.commit()
        assert get_effective_automatic_message_text(db, key) == "Изменённый текст"
        reset_automatic_message_override(db, key)
        db.commit()
        assert get_effective_automatic_message_text(db, key) == definition.default_text
    engine.dispose()
