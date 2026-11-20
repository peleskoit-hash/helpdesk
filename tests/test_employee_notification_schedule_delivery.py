"""Schedule offers and durable per-message delivery, without live services."""
import pytest

from test_employee_notification_schedule_routing import (
    env, bind, publish, rows, local, Period, Backlog, Binding, Snapshot, User, Account,
)
from test_employee_notification_schedule_processing import seed, run
from api.app.services import employee_notification_backlog as backlog


def prepare(env, count=7):
    bind(env); publish(env)
    seed(env, times=("2026-11-09T09:00:00",) * count)
    env.now = local("2026-11-09T12:00:00")
    run(env)
    return button(env)


def button(env, answer="yes"):
    p = next(p for p in rows(env, Period) if p["message_id"] is not None)
    return {"data": f"nb:{p['id']}:{p['offer_generation']}:{answer}", "from": {"id": "a"},
            "message": {"message_id": p["message_id"], "chat": {"id": "a", "type": "private"}}}


def click(env, callback):
    with env.factory() as db:
        return backlog.handle_backlog_callback(db, callback)


def test_offer_only_in_shift_once_with_generation(env):
    bind(env); publish(env)
    seed(env, times=("2026-11-09T09:00:00",))
    run(env, "2026-11-09T10:59:59")
    assert not env.sent
    run(env, "2026-11-09T11:00:00")
    p = rows(env, Period)[0]
    assert len(env.sent) == 1 and p["offer_state"] == "offered"
    assert env.sent[0][2]["reply_markup"]["inline_keyboard"][0][0]["callback_data"] == f"nb:{p['id']}:{p['offer_generation']}:yes"
    run(env, "2026-11-09T12:00:00")
    assert len(env.sent) == 1


def test_batches_preserve_order_parameters_and_reject_repeated_button(env):
    old = prepare(env, 7)
    assert click(env, old)["backlog"]
    assert [s[1] for s in env.sent[1:6]] == [f"p-{n}" for n in range(5)]
    assert len(rows(env, Backlog)) == 2
    for _, _, params in env.sent[1:6]:
        assert params["parse_mode"] == "HTML" and params["disable_web_page_preview"] and params["disable_notification"]
        assert params["retry_only_parse_errors"] is True
    assert click(env, old)["ignored"]
    assert len(env.sent) == 7  # Initial offer, five messages, next offer.
    assert click(env, button(env))["backlog"]
    assert [s[1] for s in env.sent[-2:]] == ["p-5", "p-6"]
    assert not rows(env, Backlog)
    run(env)
    assert not rows(env, Period)


def test_no_clears_only_selected_period_and_replay_ignored(env):
    prepare(env, 2)
    seed(env, "other", ("2026-11-09T20:00:00",), day=local("2026-11-10T12:00:00").date())
    no = button(env, "no")
    assert click(env, no)["backlog"] and click(env, no)["ignored"]
    assert [m["text"] for m in rows(env, Backlog)] == ["other-0"]
    assert len(env.sent) == 1


@pytest.mark.parametrize("field", ["generation", "sender", "chat", "message", "legacy_format", "revision"])
def test_stale_or_wrong_callback_never_sends_or_deletes(env, field):
    cb = prepare(env)
    before = rows(env, Backlog)
    if field == "generation": cb["data"] = "nb:p:999:yes"
    elif field == "sender": cb["from"]["id"] = "other"
    elif field == "chat": cb["message"]["chat"]["id"] = "other"
    elif field == "message": cb["message"]["message_id"] = "999"
    elif field == "legacy_format": cb["data"] = "nb:p:yes"
    else:
        with env.factory.begin() as db: db.get(Snapshot, "2026-11").revision += 1
    assert click(env, cb)["ignored"]
    assert rows(env, Backlog) == before and len(env.sent) == 1 and not env.deleted


@pytest.mark.parametrize("reason", ["end", "blocked", "inactive", "disabled", "revision", "unbound", "unavailable"])
def test_each_message_rechecks_delivery_right(env, monkeypatch, reason):
    cb = prepare(env)
    original = backlog.send_message
    def send(*args, **kwargs):
        result = original(*args, **kwargs)
        if args[1] == "p-0":
            if reason == "end": env.now = local("2026-11-09T17:00:00")
            else:
                # Same transaction mutation simulates a change observed on the
                # next lock acquisition, without SQLite's cross-writer lock.
                changes[0] = True
        return result
    changes = [False]
    original_lock = backlog._locked_schedule_period
    def lock(db, ident):
        p = original_lock(db, ident)
        if changes[0]:
            changes[0] = False
            if reason == "blocked": db.get(Account, "a").is_blocked = True
            elif reason == "inactive": db.get(User, "a").is_active = False
            elif reason == "disabled": db.get(Binding, "a").enabled = False
            elif reason == "revision": db.get(Snapshot, "2026-11").revision += 1
            elif reason == "unbound": db.get(Account, "a").bound_user_id = None
            elif reason == "unavailable": db.get(Account, "a").delivery_available = False
            db.flush()
        return p
    monkeypatch.setattr(backlog, "send_message", send)
    monkeypatch.setattr(backlog, "_locked_schedule_period", lock)
    assert click(env, cb)["backlog"]
    assert [s[1] for s in env.sent[1:]] == ["p-0"]
    assert len(rows(env, Backlog)) == 6


def test_ambiguous_send_is_durable_and_never_blindly_retried(env, monkeypatch):
    cb = prepare(env, 3)
    original = backlog.send_message
    def send(*args, **kwargs):
        if args[1] == "p-1": raise TimeoutError("Synthetic uncertain response")
        return original(*args, **kwargs)
    monkeypatch.setattr(backlog, "send_message", send)
    assert click(env, cb)["backlog"]
    items = rows(env, Backlog)
    assert [m["text"] for m in items] == ["p-1", "p-2"]
    assert items[0]["send_state"] == "unknown" and items[0]["send_attempt_id"]
    assert items[1]["send_state"] == "pending"
    assert click(env, cb)["ignored"]
    run(env)
    assert len(env.sent) == 2 and rows(env, Backlog) == items


def test_interrupted_sending_attempt_is_not_reissued(env):
    prepare(env, 2)
    with env.factory.begin() as db:
        item = db.get(Backlog, rows(env, Backlog)[0]["id"])
        item.send_state, item.send_attempt_id = "sending", "interrupted"
    assert click(env, button(env))["backlog"]
    run(env)
    assert len(env.sent) == 1 and len(rows(env, Backlog)) == 2


def test_ambiguous_offer_is_not_duplicated(env, monkeypatch):
    bind(env); publish(env)
    seed(env, times=("2026-11-09T09:00:00",))
    calls = []
    def fail(*args, **kwargs):
        calls.append(args)
        raise TimeoutError("Synthetic uncertain offer")
    monkeypatch.setattr(backlog, "send_message", fail)
    run(env); run(env)
    assert len(calls) == 1 and rows(env, Period)[0]["message_id"] is None


def test_recalculation_replaces_generation_and_rejects_old_button(env):
    old = prepare(env, 2)
    with env.factory.begin() as db:
        db.get(Snapshot, "2026-11").revision += 1
    run(env)
    assert len(env.sent) == 2 and len(env.deleted) == 1
    assert click(env, old)["ignored"]
    assert len(rows(env, Backlog)) == 2
    assert click(env, button(env))["backlog"]
    assert not rows(env, Backlog)


def test_attempt_is_committed_before_network_and_success_not_repeated(env, monkeypatch):
    cb = prepare(env, 1)
    original = backlog.send_message
    observed = []
    def send(*args, **kwargs):
        item = rows(env, Backlog)[0]  # Independent session sees durable intent.
        observed.append((item["send_state"], item["send_attempt_id"]))
        return original(*args, **kwargs)
    monkeypatch.setattr(backlog, "send_message", send)
    assert click(env, cb)["backlog"]
    assert observed[0][0] == "sending" and observed[0][1]
    assert not rows(env, Backlog)
    assert click(env, cb)["ignored"]
    run(env)
    assert len(env.sent) == 2


@pytest.mark.parametrize("reason", ["unknown", "inactive", "blocked", "unbound", "unavailable", "pending", "disabled", "no_token"])
def test_worker_never_offers_without_current_delivery_right(env, monkeypatch, reason):
    bind(env)
    if reason != "unknown": publish(env)
    seed(env, times=("2026-11-09T09:00:00",))
    with env.factory.begin() as db:
        if reason == "inactive": db.get(User, "a").is_active = False
        elif reason == "blocked": db.get(Account, "a").is_blocked = True
        elif reason == "unbound": db.get(Account, "a").bound_user_id = None
        elif reason == "unavailable": db.get(Account, "a").delivery_available = False
        elif reason == "pending": db.get(Account, "a").request_status = "pending"
        elif reason == "disabled": db.get(Binding, "a").enabled = False
    if reason == "no_token": monkeypatch.setattr(backlog.settings, "employee_telegram_bot_token", "")
    run(env)
    assert not env.sent and len(rows(env, Backlog)) == 1


def test_confirmed_rejection_can_be_retried_but_does_not_drop_message(env, monkeypatch):
    cb = prepare(env, 1)
    original = backlog.send_message
    def send(*args, **kwargs):
        if args[1] == "p-0": return {"ok": False}
        return original(*args, **kwargs)
    monkeypatch.setattr(backlog, "send_message", send)
    click(env, cb)
    assert rows(env, Backlog)[0]["send_state"] == "pending"
    monkeypatch.setattr(backlog, "send_message", original)
    assert click(env, button(env))["backlog"]
    assert not rows(env, Backlog)


def test_html_sender_does_not_fallback_after_uncertain_transport_error(env, monkeypatch):
    from api.app.services import telegram_bot
    cb = prepare(env, 1)
    calls = []
    def fail(*args, **kwargs):
        calls.append((args, kwargs))
        raise telegram_bot.TelegramApiError("Synthetic timeout")
    monkeypatch.setattr(telegram_bot, "telegram_request", fail)
    monkeypatch.setattr(telegram_bot, "is_test_chat_id", lambda chat: False)
    monkeypatch.setattr(backlog, "send_message", telegram_bot.send_message)
    click(env, cb)
    assert len(calls) == 1
    assert rows(env, Backlog)[0]["send_state"] == "unknown"
