"""Synthetic schedules, isolated SQLite and mocked Telegram; no live services."""
import calendar
import copy
from concurrent.futures import ThreadPoolExecutor
from datetime import date, datetime, timezone
from threading import Barrier
from types import SimpleNamespace

import pytest
from sqlalchemy import create_engine, event, select, text
from sqlalchemy.orm import sessionmaker

from api.app.models import (Company, User, TelegramNotificationAccount as Account,
    EmployeeNotificationPreference as Preference, AutomaticMessageTemplate,
    EmployeeScheduleBinding as Binding, EmployeeScheduleSnapshot as Snapshot,
    EmployeeNotificationPeriod as Period, EmployeeNotificationBacklog as Backlog)
from api.app.services import employee_notification_backlog as backlog
from api.app.services import employee_telegram_notifications as notifications
from api.app.services import employee_schedule_client


def local(value="2026-11-09T12:00:00"):
    return datetime.fromisoformat(value).replace(tzinfo=backlog.MOSCOW)


def utc(value):
    return value.astimezone(timezone.utc).replace(tzinfo=None)


@pytest.fixture
def env(tmp_path, monkeypatch):
    engine = create_engine(f"sqlite:///{tmp_path / 'routing.sqlite'}", connect_args={"timeout": 5})
    @event.listens_for(engine, "connect")
    def fk(conn, _): conn.execute("PRAGMA foreign_keys=ON")
    for model in (Company, User, Account, Preference, AutomaticMessageTemplate, Binding, Snapshot, Period, Backlog):
        model.__table__.create(engine)
    factory = sessionmaker(engine, autoflush=False)
    with factory.begin() as db:
        db.add(Company(id="c", name="Synthetic")); db.flush()
        for ident, role in (("a", "agent"), ("b", "admin")):
            db.add(User(id=ident, company_id="c", full_name="Synthetic " + ident, role=role, is_active=True))
        db.flush()
        for ident in ("a", "b"):
            db.add(Account(id=ident, bound_user_id=ident, telegram_user_id=ident, telegram_chat_id=ident,
                           request_status="approved", is_blocked=False, delivery_available=True))
    result = SimpleNamespace(engine=engine, factory=factory, now=local(), sent=[], deleted=[])
    actual_clock = backlog._moscow_now
    monkeypatch.setattr(backlog, "_moscow_now", lambda value=None: result.now if value is None else actual_clock(value))
    def send(chat, body, **kwargs):
        result.sent.append((chat, body, kwargs))
        return {"ok": True, "result": {"message_id": len(result.sent)}}
    monkeypatch.setattr(notifications, "SessionLocal", factory)
    monkeypatch.setattr(notifications.settings, "employee_telegram_bot_token", "synthetic-token")
    monkeypatch.setattr(notifications, "send_message", send)
    monkeypatch.setattr(backlog, "send_message", send)
    monkeypatch.setattr(backlog, "telegram_request", lambda *a, **kw: result.deleted.append((a, kw)))
    def forbidden(*args, **kwargs): raise AssertionError("Spark network access is forbidden")
    monkeypatch.setattr(employee_schedule_client, "fetch_month", forbidden)
    yield result
    engine.dispose()


def bind(env, user="a", enabled=True):
    with env.factory.begin() as db:
        db.add(Binding(user_id=user, external_employee_id="1" if user == "a" else "2", enabled=enabled,
                       external_full_name="Synthetic " + user, external_department_id="1",
                       external_department_name="Synthetic", connected_at=datetime(2000, 1, 1),
                       last_checked_at=datetime(2000, 1, 1)))


def publish(env, plan=None, months=("2026-11", "2026-12")):
    with env.factory.begin() as db:
        for month in months:
            year, number = map(int, month.split("-"))
            employees = []
            for ident in (1, 2):
                days = []
                for day in range(1, calendar.monthrange(year, number)[1] + 1):
                    value = date(year, number, day)
                    shift = "11:00-17:00" if plan is None else plan.get(value.isoformat(), "")
                    days.append({"date": value.isoformat(), "work_day": bool(shift), "shift": shift,
                                 "mark": "" if shift else "vacation", "duty": False,
                                 "is_weekend": value.weekday() >= 5})
                employees.append({"id": ident, "full_name": "Synthetic", "is_active": False,
                                  "default_shift": "08:00-19:00", "dismissed_from": None,
                                  "last_work_month": None, "days": days})
            db.add(Snapshot(month=month, revision=42, payload_hash="synthetic", fetched_at=datetime(2000, 1, 1),
                            payload_json={"version": 2, "month": month, "today": "2026-11-09",
                                          "departments": [{"id": 1, "name": "Synthetic", "employees": employees}]}))


def delivery(internal=False, body="<b>Synthetic</b>", *, user=None, kind="new_message"):
    fields = dict(user_id=user or ("b" if internal else "a"), telegram_chat_id=user or ("b" if internal else "a"),
                  text=body, parse_mode="HTML", disable_link_preview=False, send_silently=True)
    return (notifications.InternalTaskCompletionTelegramDelivery(**fields) if internal else
            notifications.EmployeeTelegramDelivery(event_kind=kind, delivery_kind=kind, **fields))


def push(env, internal=False, body="<b>Synthetic</b>"):
    (notifications._send_internal_task_completion_delivery if internal else notifications._send_delivery)(delivery(internal, body))


def rows(env, model):
    with env.factory() as db:
        return [dict(row) for row in db.execute(select(model.__table__).order_by(model.id)).mappings()]


def callback(env, period_id, answer="yes", message="101"):
    with env.factory() as db:
        return backlog.handle_backlog_callback(db, {"data": f"nb:{period_id}:{answer}", "from": {"id": "a"},
            "message": {"message_id": message, "chat": {"id": "a", "type": "private"}}}, now=env.now)


@pytest.mark.parametrize("internal", [False, True])
@pytest.mark.parametrize("at,sent,day", [
    ("2026-11-09T10:59:59", False, date(2026, 11, 9)),
    ("2026-11-09T11:00:00", True, None),
    ("2026-11-09T16:59:59", True, None),
    ("2026-11-09T17:00:00", False, date(2026, 11, 10)),
    ("2026-11-09T20:00:00", False, date(2026, 11, 10)),
])
def test_both_entrypoints_follow_half_open_shift(env, internal, at, sent, day):
    env.now = local(at); bind(env, "b" if internal else "a"); publish(env)
    push(env, internal)
    assert len(env.sent) == int(sent)
    periods, messages = rows(env, Period), rows(env, Backlog)
    if sent:
        assert not periods and not messages
        assert env.sent[0][2]["parse_mode"] == "HTML"
        assert env.sent[0][2]["disable_notification"] is True
    else:
        assert len(periods) == len(messages) == 1
        assert periods[0]["period_mode"] == "schedule" and periods[0]["work_date"] == day
        assert periods[0]["offer_at"] == datetime.combine(day, datetime.min.time()).replace(hour=8)
        assert periods[0]["expires_at"].hour == 14 and periods[0]["schedule_revision"] == 42
        assert messages[0]["created_at"] == utc(env.now) and messages[0]["send_state"] == "pending"


@pytest.mark.parametrize("internal", [False, True])
def test_confirmed_working_sunday_sends(env, internal):
    env.now = local("2026-11-08T12:00:00"); bind(env, "b" if internal else "a"); publish(env)
    push(env, internal)
    assert len(env.sent) == 1 and not rows(env, Period)


def test_weekend_notifications_wait_for_tuesday_and_preserve_order_parameters(env):
    bind(env); publish(env, {"2026-11-10": "11:00-17:00"})
    env.now = local("2026-11-07T20:00:00"); push(env, body="first")
    env.now = local("2026-11-08T12:00:00"); push(env, body="second")
    periods, messages = rows(env, Period), rows(env, Backlog)
    assert not env.sent and len(periods) == 1
    p = periods[0]
    assert p["work_date"] == date(2026, 11, 10) and p["window_state"] == "resolved"
    assert p["anchor_at"] == datetime(2026, 11, 7, 17)
    assert (p["offer_at"], p["expires_at"]) == (datetime(2026, 11, 10, 8), datetime(2026, 11, 10, 14))
    assert [m["text"] for m in messages] == ["first", "second"]
    assert [m["created_at"] for m in messages] == [datetime(2026, 11, 7, 17), datetime(2026, 11, 8, 9)]
    assert all(m["parse_mode"] == "HTML" and not m["disable_link_preview"] and m["send_silently"] for m in messages)


@pytest.mark.parametrize("kind", ["no_data", "missing_month", "missing_employee", "missing_date", "invalid_shift", "no_future_shift"])
def test_unknown_or_incomplete_calendar_accumulates_without_guessed_window(env, kind):
    bind(env); env.now = local("2026-11-09T09:00:00")
    if kind != "no_data":
        publish(env, {} if kind == "no_future_shift" else None, months=("2026-12",) if kind == "missing_month" else ("2026-11", "2026-12"))
        if kind in {"missing_employee", "missing_date", "invalid_shift"}:
            with env.factory.begin() as db:
                row = db.get(Snapshot, "2026-11"); value = copy.deepcopy(row.payload_json)
                worker = value["departments"][0]["employees"][0]
                if kind == "missing_employee": worker["id"] = 99
                elif kind == "missing_date": worker["days"].pop(8)
                else: worker["days"][8]["shift"] = "broken"
                row.payload_json = value
    push(env, body="first"); push(env, body="second")
    p = rows(env, Period)
    assert not env.sent and len(p) == 1 and p[0]["period_mode"] == "schedule"
    assert p[0]["window_state"] == "unresolved" and p[0]["work_date"] is None
    assert p[0]["offer_at"] is p[0]["expires_at"] is p[0]["schedule_revision"] is None
    assert [m["text"] for m in rows(env, Backlog)] == ["first", "second"]


def test_spark_known_date_occupied_by_legacy_uses_unresolved(env):
    bind(env); publish(env)
    env.now = local("2026-11-09T09:00:00")
    with env.factory.begin() as db:
        db.add(Period(id="legacy", user_id="a", work_date=date(2026, 11, 9), chat_id="a"))
    push(env)
    periods = rows(env, Period)
    legacy = next(p for p in periods if p["id"] == "legacy")
    spark = next(p for p in periods if p["id"] != "legacy")
    assert legacy["period_mode"] == "legacy" and legacy["work_date"] == date(2026, 11, 9)
    assert spark["period_mode"] == "schedule" and spark["work_date"] is None and spark["window_state"] == "unresolved"


@pytest.mark.parametrize("internal", [False, True])
@pytest.mark.parametrize("invalid", ["inactive", "missing_user", "unbound", "wrong_chat", "pending", "blocked", "unavailable", "personal_disabled"])
def test_invalid_recipient_is_suppressed_at_both_entrypoints(env, internal, invalid):
    user = "b" if internal else "a"
    bind(env, user); publish(env)
    with env.factory.begin() as db:
        account = db.get(Account, user)
        if invalid == "inactive": db.get(User, user).is_active = False
        elif invalid == "missing_user":
            account.bound_user_id = None; db.flush(); db.delete(db.get(User, user))
        elif invalid == "unbound": account.bound_user_id = None
        elif invalid == "wrong_chat": account.telegram_chat_id = "different"
        elif invalid == "pending": account.request_status = "pending"
        elif invalid == "blocked": account.is_blocked = True
        elif invalid == "unavailable": account.delivery_available = False
        else:
            db.add(Preference(id=user, user_id=user, receive_new_message=False, notify_internal_task_completion=False))
    push(env, internal)
    assert not env.sent and not rows(env, Backlog)


@pytest.mark.parametrize("kind,field", [(notifications.DELIVERY_KIND_TRANSFER_TO, "receive_transfer_to_agent"),
                                       (notifications.DELIVERY_KIND_TRANSFER_FROM, "receive_transfer_from_agent")])
def test_transfer_personal_switch_is_rechecked(env, kind, field):
    with env.factory.begin() as db:
        db.add(Preference(id="p", user_id="a", **{field: False}))
    notifications._send_delivery(delivery(kind=kind))
    assert not env.sent and not rows(env, Backlog)


def test_recheck_time_after_permission_checks_accumulates_at_shift_end(env, monkeypatch):
    bind(env); publish(env); env.now = local("2026-11-09T16:59:59")
    check = notifications._delivery_still_allowed
    def cross_boundary(*args, **kwargs):
        allowed = check(*args, **kwargs)
        env.now = local("2026-11-09T17:00:00")
        return allowed
    monkeypatch.setattr(notifications, "_delivery_still_allowed", cross_boundary)
    push(env)
    assert not env.sent and rows(env, Period)[0]["work_date"] == date(2026, 11, 10)
    assert rows(env, Backlog)[0]["created_at"] == datetime(2026, 11, 9, 14)


def test_check_failure_never_falls_back_to_send_or_leaks_details(env, monkeypatch, caplog):
    def fail(*args): raise RuntimeError("sensitive-text synthetic-token")
    monkeypatch.setattr(notifications, "_approved_account_for_user", fail)
    push(env)
    assert not env.sent and not rows(env, Backlog)
    assert "sensitive-text" not in caplog.text and "synthetic-token" not in caplog.text


@pytest.mark.parametrize("connected", [False, True])
@pytest.mark.parametrize("mode", ["legacy", "schedule"])
def test_legacy_worker_and_callback_protect_schedule_and_connected_legacy(env, connected, mode):
    if connected: bind(env)
    with env.factory.begin() as db:
        db.add(Period(id="p", user_id="a", chat_id="a", work_date=date(2026, 11, 9), period_mode=mode,
                      message_id="101", offered=False)); db.flush()
        db.add(Backlog(period_id="p", text="keep", disable_link_preview=True, send_silently=True))
    protected = connected or mode == "schedule"
    if protected:
        before = rows(env, Backlog)
        assert callback(env, "p")["ignored"]
        assert callback(env, "p", "no")["ignored"]
        backlog.run_employee_notification_backlog_once(session_factory=env.factory, now_utc=env.now)
        env.now = local("2026-11-09T20:00:00")
        backlog.run_employee_notification_backlog_once(session_factory=env.factory, now_utc=env.now)
        assert rows(env, Backlog) == before and len(rows(env, Period)) == 1
        assert not env.sent and not env.deleted
    else:
        assert callback(env, "p")["backlog"]
        assert not rows(env, Backlog) and env.sent[0][1] == "keep"


def _seed_schedule_conflict(env, merge):
    bind(env, enabled=False)
    with env.factory.begin() as db:
        if merge:
            db.add(Period(id="unresolved", user_id="a", chat_id="a", period_mode="schedule", window_state="unresolved",
                          anchor_at=datetime(2026, 11, 6, 9)))
            db.flush()
            db.add(Backlog(period_id="unresolved", text="older", parse_mode=None, disable_link_preview=True,
                           send_silently=False, created_at=datetime(2026, 11, 6, 9), send_state="unknown", send_attempt_id="old-attempt"))
            db.flush()
        db.add(Period(id="source", user_id="a", chat_id="a", period_mode="schedule", window_state="resolved",
                      work_date=date(2026, 11, 10), anchor_at=datetime(2026, 11, 7, 17),
                      offer_at=datetime(2026, 11, 10, 8), expires_at=datetime(2026, 11, 10, 14), schedule_revision=42))
        db.flush()
        db.add_all([Backlog(period_id="source", text=f"source-{n}", parse_mode="HTML", disable_link_preview=False,
                           send_silently=True, created_at=datetime(2026, 11, 7 + n, 17), send_state="pending") for n in (0, 1)])
    env.now = local("2026-11-09T20:00:00")


def _assert_legacy_conflict(env, merge):
    _seed_schedule_conflict(env, merge)
    before = rows(env, Backlog)
    push(env, body="new legacy")
    periods, after = rows(env, Period), rows(env, Backlog)
    assert len(periods) == 2 and len(after) == len(before) + 1
    unresolved = next(p for p in periods if p["period_mode"] == "schedule")
    legacy = next(p for p in periods if p["period_mode"] == "legacy")
    assert unresolved["id"] == ("unresolved" if merge else "source")
    assert unresolved["work_date"] is unresolved["offer_at"] is unresolved["expires_at"] is None
    assert unresolved["window_state"] == "unresolved"
    assert unresolved["anchor_at"] == datetime(2026, 11, 6, 9) if merge else unresolved["anchor_at"] == datetime(2026, 11, 7, 17)
    assert legacy["work_date"] == date(2026, 11, 10) and after[-1]["period_id"] == legacy["id"]
    for old, new in zip(before, after[:-1]):
        assert {k:v for k,v in old.items() if k != "period_id"} == {k:v for k,v in new.items() if k != "period_id"}
        assert new["period_id"] == unresolved["id"]
    assert after[-1]["text"] == "new legacy" and not env.sent
    # Legacy expiration clears only the new legacy period; Spark rows stay frozen.
    backlog.run_employee_notification_backlog_once(session_factory=env.factory, now_utc=local("2026-11-10T19:00:00"))
    assert [p["id"] for p in rows(env, Period)] == [unresolved["id"]]
    assert rows(env, Backlog) == after[:-1]


def test_legacy_date_conflict_moves_schedule_to_unresolved_without_loss(env):
    _assert_legacy_conflict(env, merge=False)


def test_legacy_date_conflict_merges_existing_unresolved_without_loss(env):
    _assert_legacy_conflict(env, merge=True)


@pytest.mark.parametrize("existing_anchor,source_anchor,expected", [
    (datetime(2026, 11, 6, 9), datetime(2026, 11, 7, 17), datetime(2026, 11, 6, 9)),
    (datetime(2026, 11, 7, 17), datetime(2026, 11, 6, 9), datetime(2026, 11, 6, 9)),
    (datetime(2026, 11, 6, 9), None, datetime(2026, 11, 6, 9)),
    (None, datetime(2026, 11, 7, 17), datetime(2026, 11, 7, 17)),
    (None, None, None),
])
def test_legacy_conflict_merge_preserves_known_anchor(env, existing_anchor, source_anchor, expected):
    _seed_schedule_conflict(env, merge=True)
    with env.factory.begin() as db:
        db.get(Period, "unresolved").anchor_at = existing_anchor
        db.get(Period, "source").anchor_at = source_anchor
    before = rows(env, Backlog)
    push(env, body="new legacy")
    periods, after = rows(env, Period), rows(env, Backlog)
    unresolved = next(p for p in periods if p["period_mode"] == "schedule")
    assert unresolved["id"] == "unresolved" and unresolved["anchor_at"] == expected
    assert unresolved["work_date"] is unresolved["offer_at"] is unresolved["expires_at"] is None
    assert len(periods) == 2 and len(after) == len(before) + 1
    for old, new in zip(before, after[:-1]):
        assert {k: v for k, v in old.items() if k != "period_id"} == {k: v for k, v in new.items() if k != "period_id"}
        assert new["period_id"] == "unresolved"
    legacy = next(p for p in periods if p["period_mode"] == "legacy")
    assert legacy["work_date"] == date(2026, 11, 10)
    assert after[-1]["period_id"] == legacy["id"] and after[-1]["text"] == "new legacy"
    assert not env.sent


@pytest.mark.parametrize("merge", [False, True])
def test_legacy_conflict_rolls_back_if_new_message_cannot_be_saved(env, merge):
    _seed_schedule_conflict(env, merge)
    before = rows(env, Period), rows(env, Backlog)
    def fail(conn, cursor, statement, params, context, many):
        if statement.startswith("INSERT INTO employee_notification_backlog"):
            raise RuntimeError("Synthetic storage failure")
    event.listen(env.engine, "before_cursor_execute", fail)
    try: push(env, body="new legacy")
    finally: event.remove(env.engine, "before_cursor_execute", fail)
    assert (rows(env, Period), rows(env, Backlog)) == before
    assert not env.sent


def test_concurrent_unknown_schedule_messages_share_one_unresolved_period(env):
    bind(env)
    barrier = Barrier(2)
    def append(body):
        barrier.wait(timeout=5)
        with env.factory() as db:
            result = backlog.route_delivery(db, delivery(body=body), now=env.now)
            db.commit()
            return result
    with ThreadPoolExecutor(2) as pool:
        a, b = pool.submit(append, "first"), pool.submit(append, "second")
        assert a.result(timeout=10) == b.result(timeout=10) == "accumulate"
    assert len(rows(env, Period)) == 1
    messages = rows(env, Backlog)
    assert len(messages) == 2 and {m["text"] for m in messages} == {"first", "second"}
    assert messages[0]["id"] < messages[1]["id"]
