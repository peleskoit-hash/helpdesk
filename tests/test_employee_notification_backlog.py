from datetime import datetime
from types import SimpleNamespace

import pytest
from sqlalchemy import create_engine, func, inspect, select
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from api.app.db import Base
from api.app.models import (
    Company, EmployeeNotificationBacklog, EmployeeNotificationPeriod,
    TelegramNotificationAccount, User,
)
from api.app.services import employee_notification_backlog as backlog
from api.app.services import employee_telegram_bot as bot
from api.app.services import employee_telegram_notifications as notifications
from api.app.services import schema_evolution


def local(day, hour, minute=0, second=0):
    return datetime(2026, 11, day, hour, minute, second, tzinfo=backlog.MOSCOW)


@pytest.fixture
def env(monkeypatch):
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(engine)
    factory = sessionmaker(engine, autoflush=False)
    with factory() as db:
        db.add(Company(id="c", name="Company"))
        for user_id in ("a", "b"):
            db.add(User(id=user_id, company_id="c", login=user_id, password_hash="hash",
                        full_name=user_id, role="admin" if user_id == "b" else "agent"))
            db.add(TelegramNotificationAccount(
                id=user_id, bound_user_id=user_id, telegram_user_id=user_id,
                telegram_chat_id=user_id, request_status="approved"))
        db.commit()
    sent, deleted = [], []
    def send(chat_id, text, **kwargs):
        sent.append((chat_id, text, kwargs))
        return {"ok": True, "result": {"message_id": len(sent)}}
    def request(method, payload, **kwargs):
        deleted.append((method, payload, kwargs))
        return {"ok": True}
    monkeypatch.setattr(backlog.settings, "employee_telegram_bot_token", "employee-test-token")
    monkeypatch.setattr(backlog, "send_message", send)
    monkeypatch.setattr(backlog, "telegram_request", request)
    monkeypatch.setattr(notifications, "send_message", send)
    monkeypatch.setattr(notifications, "SessionLocal", factory)
    monkeypatch.setattr(bot, "answer_callback_query", lambda *args, **kwargs: {"ok": True})
    return SimpleNamespace(factory=factory, engine=engine, sent=sent, deleted=deleted, send=send)


def delivery(user="a", text="<b>original</b>"):
    return notifications.EmployeeTelegramDelivery(
        event_kind="new_message", delivery_kind="new_message", user_id=user,
        telegram_chat_id=user, text=text, parse_mode="HTML", disable_link_preview=False, send_silently=True)


def enqueue(env, count, *, user="a", now=None):
    for index in range(count):
        assert backlog.accumulate_delivery(delivery(user, str(index)), session_factory=env.factory,
                                           now=now or local(7, 19))


def count(env, model=EmployeeNotificationBacklog):
    with env.factory() as db:
        return db.scalar(select(func.count()).select_from(model))


def period(env, user="a"):
    with env.factory() as db:
        return db.scalar(select(EmployeeNotificationPeriod).where(EmployeeNotificationPeriod.user_id == user))


def tick(env, now=None):
    backlog.run_employee_notification_backlog_once(session_factory=env.factory, now_utc=now or local(9, 8))


def callback(env, *, user="a", answer="yes", now=None, message_id=None, period_id=None, sender=None):
    current = period(env, user)
    query = {"id": "cb", "data": f"nb:{period_id or current.id}:{answer}",
             "from": {"id": sender or user},
             "message": {"message_id": message_id or current.message_id,
                         "chat": {"id": user, "type": "private"}}}
    with env.factory() as db:
        return backlog.handle_backlog_callback(db, query, now=now or local(9, 8))


@pytest.mark.parametrize("now,working", [
    (local(9, 7, 59, 59), False), (local(9, 8), True), (local(9, 18, 59, 59), True),
    (local(9, 19), False), (local(7, 12), True), (local(8, 12), False),
    (datetime(2026, 11, 9, 5), True),  # naive input is UTC
])
def test_schedule(now, working):
    assert backlog.is_notification_work_time(now) is working


@pytest.mark.parametrize("internal", [False, True])
def test_daytime_sends_original_parameters_without_accumulation(env, monkeypatch, internal):
    monkeypatch.setattr(backlog, "_moscow_now", lambda now=None: local(9, 12))
    send = notifications._send_internal_task_completion_delivery if internal else notifications._send_delivery
    send(delivery())
    assert count(env) == 0
    assert env.sent == [("a", "<b>original</b>", {
        "parse_mode": "HTML", "bot_token": "employee-test-token",
        "disable_web_page_preview": False, "disable_notification": True})]


@pytest.mark.parametrize("now,target", [(local(7, 19), 9), (local(8, 12), 9), (local(9, 19), 10)])
def test_evening_and_sunday_accumulate(env, monkeypatch, now, target):
    monkeypatch.setattr(backlog, "_moscow_now", lambda value=None: now)
    notifications._send_delivery(delivery())
    notifications._send_internal_task_completion_delivery(delivery("b"))
    assert not env.sent
    assert count(env) == 2
    assert period(env).work_date.day == target


def test_weekend_preserves_backlog_and_monday_offer_has_no_duplicates(env):
    enqueue(env, 2)
    tick(env, local(8, 8))
    tick(env, local(8, 19))
    assert not env.sent and count(env) == 2
    tick(env)
    tick(env)
    assert len(env.sent) == 1
    assert env.sent[0][1] == "В нерабочее время у Вас накопилось 2 уведомлений. Хотите их прочитать?"
    assert [button["text"] for button in env.sent[0][2]["reply_markup"]["inline_keyboard"][0]] == ["Да", "Нет"]


@pytest.mark.parametrize("total,batches", [(3, [3]), (5, [5]), (12, [5, 5, 2])])
def test_batches_order_current_prompt_deletion_and_no_final_question(env, total, batches):
    enqueue(env, total)
    tick(env)
    remaining = total
    for size in batches:
        old = period(env).message_id
        assert callback(env)["backlog"]
        remaining -= size
        assert count(env) == remaining
        assert env.deleted[-1] == ("deleteMessage", {"chat_id": "a", "message_id": int(old)},
                                    {"bot_token": "employee-test-token"})
        assert callback(env, message_id=old).get("ignored")
        if remaining:
            assert env.sent[-1][1] == f"Выгрузил 5 уведомлений. Осталось {remaining}. Показать следующие (максимум 5 за раз)?"
            assert period(env).message_id != old
    notices = [(text, kwargs) for _, text, kwargs in env.sent if "reply_markup" not in kwargs]
    assert [text for text, _ in notices] == list(map(str, range(total)))
    assert all(kwargs["parse_mode"] == "HTML" and kwargs["disable_notification"] for _, kwargs in notices)
    assert period(env).message_id is None
    before = len(env.sent)
    tick(env)
    assert len(env.sent) == before


def test_no_clears_only_recipient_period(env):
    enqueue(env, 3)
    enqueue(env, 2, user="b")
    tick(env)
    callback(env, answer="no")
    assert count(env) == 2
    assert period(env).message_id is None
    assert period(env, "b").message_id is not None
    callback(env, user="b")
    assert count(env) == 0


def test_1900_expires_old_period_and_keeps_new_evening(env):
    enqueue(env, 12)
    tick(env)
    callback(env)
    active = period(env).message_id
    enqueue(env, 2, now=local(9, 19))
    tick(env, local(9, 19))
    assert count(env) == 2
    assert count(env, EmployeeNotificationPeriod) == 1
    assert period(env).work_date.day == 10
    assert env.deleted[-1][1]["message_id"] == int(active)
    tick(env, local(10, 8))
    assert "2 уведомлений" in env.sent[-1][1]


def test_expiration_after_scheduler_downtime_does_not_carry_old_unread(env):
    enqueue(env, 3)
    tick(env)
    enqueue(env, 2, now=local(9, 20))
    tick(env, local(10, 8))
    assert count(env) == 2
    assert count(env, EmployeeNotificationPeriod) == 1


@pytest.mark.parametrize("mismatch", ["message", "period", "recipient", "after_hours"])
def test_invalid_callbacks_do_not_touch_backlog(env, mismatch):
    enqueue(env, 3)
    tick(env)
    args = {"message": {"message_id": "999"}, "period": {"period_id": "unknown"},
            "recipient": {"sender": "b"}, "after_hours": {"now": local(9, 19)}}[mismatch]
    assert callback(env, **args).get("ignored")
    assert count(env) == 3 and not env.deleted


def test_delete_failure_still_invalidates_buttons(env, monkeypatch):
    enqueue(env, 6)
    tick(env)
    old = period(env).message_id
    def fail(*args, **kwargs):
        raise RuntimeError("Telegram refused deletion")
    monkeypatch.setattr(backlog, "telegram_request", fail)
    callback(env)
    assert count(env) == 1
    assert callback(env, message_id=old).get("ignored")
    callback(env, answer="no")
    assert count(env) == 0


def test_failed_delivery_retains_unsent_items_in_order(env, monkeypatch):
    enqueue(env, 5)
    tick(env)
    def send(chat, text, **kwargs):
        if text == "2":
            raise RuntimeError("send failed")
        return env.send(chat, text, **kwargs)
    monkeypatch.setattr(backlog, "send_message", send)
    callback(env)
    assert count(env) == 3
    assert "Выгрузил 2 уведомлений. Осталось 3." in env.sent[-1][1]
    monkeypatch.setattr(backlog, "send_message", env.send)
    callback(env)
    assert count(env) == 0


def test_bot_routes_backlog_callback(env, monkeypatch):
    enqueue(env, 3)
    tick(env)
    monkeypatch.setattr(backlog, "_moscow_now", lambda now=None: local(9, 8))
    current = period(env)
    with env.factory() as db:
        result = bot.process_employee_telegram_update(db, {"callback_query": {
            "id": "cb", "data": f"nb:{current.id}:no", "from": {"id": "a"},
            "message": {"message_id": current.message_id, "chat": {"id": "a", "type": "private"}},
        }})
    assert result["backlog"] and count(env) == 0


def test_explicit_schema_migration_is_idempotent(env, monkeypatch):
    with env.engine.begin() as conn:
        EmployeeNotificationBacklog.__table__.drop(conn)
        EmployeeNotificationPeriod.__table__.drop(conn)
    monkeypatch.setattr(schema_evolution, "engine", env.engine)
    schema_evolution.ensure_employee_notification_backlog_tables()
    enqueue(env, 1)
    schema_evolution.ensure_employee_notification_backlog_tables()
    assert count(env) == 1
    assert "employee_notification_periods" in inspect(env.engine).get_table_names()


@pytest.mark.parametrize("now", [local(8, 12), local(9, 20)])
def test_start_and_admin_actions_work_outside_hours_without_touching_backlog(env, monkeypatch, now):
    monkeypatch.setattr(backlog, "_moscow_now", lambda value=None: now)
    monkeypatch.setattr(bot, "send_message", env.send)
    monkeypatch.setattr(bot.settings, "telegram_notification_bot_owner_id", "owner")
    with env.factory() as db:
        db.get(TelegramNotificationAccount, "b").is_notification_bot_admin = True
        db.commit()
    enqueue(env, 3, now=now)
    with env.factory() as db:
        result = bot.process_employee_telegram_update(db, {"message": {
            "from": {"id": "b"}, "chat": {"id": "b", "type": "private"}, "text": "/start",
        }})
        assert result["action"] == "menu_shown"
        result = bot.process_employee_telegram_update(db, {"callback_query": {
            "id": "cb", "from": {"id": "b"}, "data": "n:m",
            "message": {"message_id": 100, "chat": {"id": "b", "type": "private"}},
        }})
        assert result["ok"]
    assert env.sent and count(env) == 3


def test_clearing_period_preserves_client_ticket_and_message(env):
    from api.app.models import Client, Ticket, TicketMessage

    with env.factory() as db:
        db.add(Client(id="client", company_id="c", display_name="Client"))
        db.add(Ticket(id="ticket", company_id="c", client_id="client", subject="Keep ticket", status="new"))
        db.add(TicketMessage(id="message", ticket_id="ticket", message_type="client", body="Keep history"))
        db.commit()
    enqueue(env, 3)
    tick(env)
    callback(env, answer="no")
    enqueue(env, 2, now=local(9, 20))
    tick(env, local(10, 19))
    with env.factory() as db:
        assert db.get(Ticket, "ticket").subject == "Keep ticket"
        assert db.get(TicketMessage, "message").body == "Keep history"
        assert db.get(Client, "client").display_name == "Client"
    assert count(env) == 0


def test_empty_backlog_sends_nothing(env):
    tick(env)
    assert not env.sent
