"""Isolated SQLite processing checks; Spark and Telegram calls are mocked."""
import copy
from datetime import date, datetime, timedelta

import pytest
from sqlalchemy import select

from test_employee_notification_schedule_routing import (
    env, bind, publish, rows, local, utc, Period, Backlog, Binding, Snapshot, User, Account,
)
from api.app.services import employee_notification_backlog as backlog


def seed(env, ident="p", times=("2026-11-07T20:00:00",), day=None, mode="schedule", prompt=None):
    with env.factory.begin() as db:
        db.add(Period(id=ident, user_id="a", chat_id="a", period_mode=mode, work_date=day,
                      window_state="resolved" if day and mode == "schedule" else "unresolved" if mode == "schedule" else "legacy",
                      anchor_at=utc(local(times[0])) if times[0] else None,
                      message_id=prompt, offered=prompt is not None,
                      offer_state="offered" if prompt else "idle"))
        db.flush()
        for index, value in enumerate(times):
            db.add(Backlog(period_id=ident, text=f"{ident}-{index}", parse_mode="HTML",
                          disable_link_preview=True, send_silently=True,
                          created_at=utc(local(value)) if value else None, send_state="pending"))


def run(env, at="2026-11-09T12:00:00"):
    backlog.run_employee_notification_backlog_once(session_factory=env.factory, now_utc=local(at))


def change(env, plan=None, missing=False):
    with env.factory.begin() as db:
        for snapshot in db.scalars(select(Snapshot)):
            db.delete(snapshot)
    if not missing:
        publish(env, plan)


def assert_parameters(items):
    assert all(m["parse_mode"] == "HTML" and m["disable_link_preview"] and m["send_silently"]
               and m["send_state"] == "pending" for m in items)


def test_weekend_accumulations_resolve_to_tuesday_and_repeat_without_changes(env):
    bind(env); publish(env, {"2026-11-10": "11:00-17:00"})
    seed(env, times=("2026-11-07T20:00:00", "2026-11-08T12:00:00"))
    before = rows(env, Backlog)
    run(env)
    period = rows(env, Period)[0]
    assert period["work_date"] == date(2026, 11, 10)
    assert period["offer_at"] == datetime(2026, 11, 10, 8)
    assert period["expires_at"] == datetime(2026, 11, 10, 14)
    assert period["schedule_revision"] == 42 and period["window_state"] == "resolved"
    state = rows(env, Period), rows(env, Backlog)
    run(env)
    assert (rows(env, Period), rows(env, Backlog)) == state
    assert rows(env, Backlog) == before and not env.sent


def test_shift_move_cancellation_and_unknown_invalidate_old_offer(env):
    bind(env); publish(env)
    seed(env, times=("2026-11-09T09:00:00",), day=date(2026, 11, 9))
    run(env)
    with env.factory.begin() as db:
        p = db.get(Period, "p")
        p.message_id, p.offered, p.offer_state = "111", True, "offered"
    change(env, {"2026-11-09": "11:00-20:00", "2026-11-10": "11:00-17:00"})
    run(env, "2026-11-09T18:00:00")
    p = rows(env, Period)[0]
    assert p["expires_at"] == datetime(2026, 11, 9, 17)
    assert p["message_id"] is None and p["offer_state"] == "invalidated" and env.deleted
    change(env, {"2026-11-10": "11:00-17:00"})
    run(env, "2026-11-09T21:00:00")
    assert rows(env, Period)[0]["work_date"] == date(2026, 11, 10)
    change(env, missing=True)
    run(env, "2026-11-11T20:00:00")
    p = rows(env, Period)[0]
    assert p["window_state"] == "unresolved" and p["work_date"] is p["expires_at"] is None
    assert len(rows(env, Backlog)) == 1 and not env.sent


def test_split_unresolved_and_expire_only_confirmed_previous_window(env):
    bind(env); publish(env)
    seed(env, times=("2026-11-09T09:00:00", "2026-11-09T18:00:00", "2026-11-10T18:00:00"))
    before = rows(env, Backlog)
    run(env, "2026-11-09T17:00:00")
    periods = {p["id"]: p for p in rows(env, Period)}
    items = rows(env, Backlog)
    assert [m["id"] for m in items] == [m["id"] for m in before[1:]]
    assert {periods[m["period_id"]]["work_date"] for m in items} == {date(2026, 11, 10), date(2026, 11, 11)}
    assert_parameters(items)
    run(env, "2026-11-10T17:00:00")
    assert [m["id"] for m in rows(env, Backlog)] == [before[2]["id"]]


def test_merge_matching_dates_preserves_message_identity_and_parameters(env):
    bind(env); publish(env, {"2026-11-10": "11:00-17:00"})
    seed(env, "first", ("2026-11-07T12:00:00",), date(2026, 11, 9), prompt="111")
    seed(env, "second", ("2026-11-08T12:00:00",), date(2026, 11, 10), prompt="222")
    before = rows(env, Backlog)
    run(env)
    assert len(rows(env, Period)) == 1
    p = rows(env, Period)[0]
    assert p["work_date"] == date(2026, 11, 10) and p["anchor_at"] == datetime(2026, 11, 7, 9)
    after = rows(env, Backlog)
    assert [{k:v for k,v in m.items() if k != "period_id"} for m in after] == [
        {k:v for k,v in m.items() if k != "period_id"} for m in before]
    assert len(env.deleted) == 2 and not env.sent


@pytest.mark.parametrize("age,remaining", [(29, 1), (30, 0)])
def test_retention_exact_age_without_schedule_or_token(env, monkeypatch, age, remaining):
    bind(env)
    monkeypatch.setattr(backlog.settings, "employee_telegram_bot_token", "")
    stamp = local("2026-11-30T12:00:00") - timedelta(days=age)
    seed(env, times=(stamp.isoformat(),))
    run(env, "2026-11-30T12:00:00")
    assert len(rows(env, Backlog)) == remaining and len(rows(env, Period)) == remaining
    assert not env.sent and not env.deleted


@pytest.mark.parametrize("mode", ["schedule", "legacy"])
def test_retention_mixed_null_inactive_blocked_and_no_token(env, monkeypatch, mode):
    bind(env)
    seed(env, times=("2026-10-31T12:00:00", "2026-11-01T12:00:01", None),
         day=date(2026, 12, 1) if mode == "legacy" else None, mode=mode, prompt="111")
    with env.factory.begin() as db:
        db.get(User, "a").is_active = False
        db.get(Account, "a").is_blocked = True
    monkeypatch.setattr(backlog.settings, "employee_telegram_bot_token", "")
    before = rows(env, Backlog)
    run(env, "2026-12-01T12:00:00")
    assert [m["id"] for m in rows(env, Backlog)] == [before[1]["id"], before[2]["id"]]
    assert rows(env, Backlog)[1]["created_at"] is None
    assert rows(env, Period)[0]["message_id"] is None
    state = rows(env, Period), rows(env, Backlog)
    run(env, "2026-12-01T12:00:00")
    assert (rows(env, Period), rows(env, Backlog)) == state
    assert not env.sent and not env.deleted


def test_null_time_stays_unresolved_without_fabricating_timestamp(env):
    bind(env); publish(env)
    seed(env, times=(None,), day=date(2026, 11, 9))
    run(env, "2026-12-10T20:00:00")
    assert rows(env, Backlog)[0]["created_at"] is None
    assert rows(env, Period)[0]["window_state"] == "unresolved"


def test_legacy_collision_stays_unresolved_and_disabled_binding_keeps_schedule(env):
    bind(env); publish(env)
    seed(env, "legacy", (None,), date(2026, 11, 10), mode="legacy")
    seed(env, times=("2026-11-09T20:00:00",))
    run(env)
    assert next(p for p in rows(env, Period) if p["period_mode"] == "schedule")["work_date"] is None
    with env.factory.begin() as db:
        db.get(Binding, "a").enabled = False
    run(env, "2026-11-10T20:00:00")
    assert len(rows(env, Period)) == 1 and rows(env, Period)[0]["period_mode"] == "schedule"
    assert rows(env, Backlog)[0]["text"] == "p-0"


def test_legacy_offer_and_expiry_unchanged(env):
    seed(env, times=("2026-11-08T12:00:00",), day=date(2026, 11, 9), mode="legacy")
    run(env, "2026-11-09T08:00:00")
    assert len(env.sent) == 1 and rows(env, Period)[0]["offered"]
    run(env, "2026-11-09T19:00:00")
    assert not rows(env, Period) and not rows(env, Backlog)


def test_maintenance_rolls_back_redistribution_on_storage_failure(env, monkeypatch):
    bind(env); publish(env)
    seed(env, times=("2026-11-09T09:00:00", "2026-11-09T18:00:00"))
    before = rows(env, Period), rows(env, Backlog)
    original = backlog._process_schedule_periods
    def fail(*args):
        original(*args)
        raise RuntimeError("Synthetic transaction failure")
    monkeypatch.setattr(backlog, "_process_schedule_periods", fail)
    with pytest.raises(RuntimeError):
        run(env)
    assert (rows(env, Period), rows(env, Backlog)) == before
    assert not env.sent and not env.deleted


def test_date_swap_and_existing_unresolved_keep_unique_constraints(env):
    bind(env); publish(env)
    seed(env, "first", ("2026-11-10T09:00:00",), date(2026, 11, 9))
    seed(env, "second", ("2026-11-09T09:00:00",), date(2026, 11, 10))
    seed(env, "unknown", (None,))
    before = rows(env, Backlog)
    run(env)
    periods = {p["id"]: p for p in rows(env, Period)}
    messages = rows(env, Backlog)
    assert len(periods) == len(messages) == 3
    assert [periods[m["period_id"]]["work_date"] for m in messages] == [date(2026, 11, 10), date(2026, 11, 9), None]
    assert [m["id"] for m in messages] == [m["id"] for m in before]


def test_incomplete_calendar_cannot_skip_gap_or_use_stale_expiry(env):
    bind(env); publish(env, {"2026-11-10": "11:00-17:00"})
    seed(env, times=("2026-11-07T12:00:00", "2026-11-10T09:00:00"), day=date(2026, 11, 8))
    with env.factory.begin() as db:
        period = db.get(Period, "p")
        period.expires_at = datetime(2026, 11, 8, 14)
        snapshot = db.get(Snapshot, "2026-11")
        payload = copy.deepcopy(snapshot.payload_json)
        for employee in payload["departments"][0]["employees"]:
            employee["days"] = [d for d in employee["days"] if d["date"] != "2026-11-08"]
        snapshot.payload_json = payload
    run(env, "2026-11-10T17:00:00")
    # The resolver rejects the incomplete monthly calendar, including later dates.
    assert [m["text"] for m in rows(env, Backlog)] == ["p-0", "p-1"]
    assert rows(env, Period)[0]["window_state"] == "unresolved"
    assert rows(env, Period)[0]["expires_at"] is None


def test_shortened_shift_expires_on_new_confirmed_end(env):
    bind(env); publish(env)
    seed(env, times=("2026-11-09T09:00:00",))
    run(env)
    change(env, {"2026-11-09": "11:00-13:00"})
    run(env, "2026-11-09T13:00:00")
    assert not rows(env, Backlog) and not rows(env, Period) and not env.sent


def test_retention_commits_even_if_telegram_prompt_deletion_fails(env, monkeypatch):
    seed(env, times=("2026-11-01T12:00:00",), prompt="111")
    def fail(*args, **kwargs):
        raise RuntimeError("Synthetic Telegram failure")
    monkeypatch.setattr(backlog, "telegram_request", fail)
    run(env, "2026-12-01T12:00:00")
    assert not rows(env, Backlog) and not rows(env, Period)
    run(env, "2026-12-01T12:00:00")
    assert not env.sent


def confirmed_then_deactivate(env, prompt=False):
    bind(env); publish(env)
    seed(env, times=("2026-11-09T09:00:00",))
    run(env, "2026-11-09T09:00:00")
    confirmed = rows(env, Period)[0]
    with env.factory.begin() as db:
        db.get(User, "a").is_active = False
        if prompt:
            p = db.get(Period, confirmed["id"])
            p.message_id, p.offered, p.offer_state = "111", True, "offered"
    return confirmed


def test_inactive_keeps_confirmed_window_and_expires_without_delivery(env):
    confirmed = confirmed_then_deactivate(env)
    run(env, "2026-11-09T10:00:00")
    assert rows(env, Period)[0] == confirmed
    run(env, "2026-11-09T17:00:00")
    assert not rows(env, Period) and not rows(env, Backlog) and not env.sent


def test_inactive_unknown_window_retained_until_thirty_days(env):
    bind(env)
    seed(env, times=("2026-11-01T09:00:00",))
    with env.factory.begin() as db:
        db.get(User, "a").is_active = False
    run(env, "2026-11-30T09:00:00")
    assert len(rows(env, Backlog)) == 1
    assert rows(env, Period)[0]["expires_at"] is None
    run(env, "2026-12-01T09:00:00")
    assert not rows(env, Period) and not rows(env, Backlog) and not env.sent


def test_reactivated_recalculates_moved_shift_before_old_expiry(env):
    confirmed_then_deactivate(env)
    run(env, "2026-11-09T10:00:00")
    change(env, {"2026-11-09": "11:00-20:00"})
    with env.factory.begin() as db:
        db.get(Snapshot, "2026-11").revision = 43
        db.get(User, "a").is_active = True
    run(env, "2026-11-09T18:00:00")
    p = rows(env, Period)[0]
    assert p["expires_at"] == datetime(2026, 11, 9, 17) and p["schedule_revision"] == 43
    assert len(rows(env, Backlog)) == 1 and not env.sent
    run(env, "2026-11-09T20:00:00")
    assert not rows(env, Backlog)


def test_deactivation_invalidates_active_button_but_keeps_window(env):
    confirmed = confirmed_then_deactivate(env, prompt=True)
    run(env, "2026-11-09T10:00:00")
    p = rows(env, Period)[0]
    for field in ("work_date", "offer_at", "expires_at", "schedule_revision"):
        assert p[field] == confirmed[field]
    assert p["message_id"] is None and not p["offered"] and p["offer_state"] == "invalidated"
    assert p["offer_generation"] == confirmed["offer_generation"] + 1
    state = rows(env, Period), rows(env, Backlog)
    run(env, "2026-11-09T10:00:00")
    assert (rows(env, Period), rows(env, Backlog)) == state
    assert len(env.deleted) == 1 and not env.sent


@pytest.mark.parametrize("reason", ["changed_revision", "missing_snapshot", "unconfirmed_revision"])
def test_inactive_unconfirmed_deadline_does_not_expire(env, reason):
    confirmed = confirmed_then_deactivate(env)
    with env.factory.begin() as db:
        if reason == "changed_revision":
            db.get(Snapshot, "2026-11").revision = 43
        elif reason == "missing_snapshot":
            db.delete(db.get(Snapshot, "2026-11"))
        else:
            db.get(Period, confirmed["id"]).schedule_revision = None
    run(env, "2026-11-10T20:00:00")
    assert len(rows(env, Backlog)) == 1 and not env.sent
    assert rows(env, Period)[0]["expires_at"] == confirmed["expires_at"]
