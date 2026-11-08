import sqlite3
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta
from threading import Event
from types import SimpleNamespace
from unittest.mock import Mock

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker

from api.app.models import (
    BotButtonSetting, Company, TelegramAutomationGroup, Ticket, TicketAttachment,
    TicketIntakeState, TicketMessage,
)
from api.app.routers import telegram
from api.app.services import telegram_attachments, telegram_group_automation as automation
from test_telegram_group_registry import case, register
from test_telegram_client_blocking import _seed_contact, _seed_ticket


@pytest.fixture
def flow(case, monkeypatch, tmp_path):
    monkeypatch.setattr(automation, "is_telegram_intake_open", lambda: True)
    monkeypatch.setattr(telegram, "is_telegram_intake_open", lambda: True)
    from api.app.services import telegram_menu_flow, telegram_automation
    monkeypatch.setattr(telegram_automation, "is_telegram_intake_open", lambda: True)
    monkeypatch.setattr(telegram_menu_flow, "delete_message", Mock())
    monkeypatch.setattr(telegram_menu_flow, "remove_inline_keyboard", Mock())
    group_id = register(case)["group_id"]
    company = case.db.get(Company, case.db.get(TelegramAutomationGroup, group_id).company_id)
    client, _, contact = _seed_contact(case.db, company=company)
    ticket = _seed_ticket(case.db, company_id=company.id, client_id=client.id,
                          workflow_type="auto", intake_state="collecting", status="unassigned")
    button = BotButtonSetting(id="copy-button", button_key="Menu|auto|Request", menu_type="Menu",
                              workflow_type="auto", button_path="Request", button_label="Request",
                              automation_key="telegram_group", automation_overridden=True,
                              telegram_group_id=group_id, currently_present=True,
                              client_text="Пришлите одно сообщение", client_text_overridden=True)
    contact.active_menu_message_id = ticket.menu_message_id = "10"
    contact.menu_message_ids_json = [10]
    case.db.add(button)
    case.db.commit()
    copied = Mock(return_value={"ok": True, "result": {"message_id": 12345}})
    sent = Mock(return_value={"ok": True, "result": {"message_id": 500}})
    archive = Mock()
    monkeypatch.setattr(automation, "copy_message", copied)
    monkeypatch.setattr(telegram, "send_message", sent)
    monkeypatch.setattr(telegram, "answer_callback_query", Mock())
    monkeypatch.setattr(telegram, "remove_inline_keyboard", Mock())
    monkeypatch.setattr(telegram, "_safe_remove_inline_keyboard", Mock())
    monkeypatch.setattr(telegram, "_menu_name_for_contact", lambda *args: "Menu")
    monkeypatch.setattr(telegram, "choose_online_agent", lambda *args: None)
    monkeypatch.setattr(telegram, "schedule_new_message_notification", Mock())
    monkeypatch.setattr(telegram, "schedule_avatar_refresh", Mock())
    monkeypatch.setattr(telegram, "start_background_archive_inbound_attachment", archive)
    monkeypatch.setattr(telegram_attachments, "get_file", Mock(return_value={"result": {"file_path": "files/photo.jpg"}}))
    monkeypatch.setattr(telegram_attachments.settings, "attachment_storage_dir", str(tmp_path))
    case.ticket, case.button, case.contact = ticket, button, contact
    case.copied, case.sent, case.archive = copied, sent, archive
    return case


def select_button(flow, workflow="auto", *, confirmation=False):
    flow.button.workflow_type = workflow
    flow.button.button_key = f"Menu|{workflow}|Request"
    flow.ticket.workflow_type = workflow
    flow.db.commit()
    mid = 10
    if confirmation:
        flow.button.confirmation_enabled = True
        flow.button.confirmation_text = "Confirm"
        flow.db.commit()
        assert telegram.process_telegram_update(flow.db, {
            "update_id": 9, "callback_query": {"id": "select-prompt", "data": f"btn:{flow.button.button_key}",
                "from": {"id": int(flow.contact.external_user_id)},
                "message": {"message_id": 10, "chat": {"id": int(flow.contact.external_chat_id), "type": "private"}}},
        }, notify_client=True)["awaiting_confirmation"]
        mid = int(flow.contact.active_menu_message_id)
    data = f"confirm:yes:btn:{flow.button.button_key}" if confirmation else f"btn:{flow.button.button_key}"
    return telegram.process_telegram_update(flow.db, {
        "update_id": 10, "callback_query": {"id": "select", "data": data,
            "from": {"id": int(flow.contact.external_user_id)},
            "message": {"message_id": mid, "reply_markup": telegram._confirmation_markup(f"btn:{flow.button.button_key}") if confirmation else None, "chat": {"id": int(flow.contact.external_chat_id), "type": "private"}}},
    }, notify_client=True)


def incoming(flow, kind="text", *, update_id=11):
    message = {"message_id": update_id, "chat": {"id": int(flow.contact.external_chat_id), "type": "private"},
               "from": {"id": int(flow.contact.external_user_id), "first_name": "Ivan", "last_name": "Petrov"}}
    if kind == "text":
        message["text"] = "  Исходный текст клиента  "
    else:
        message["document"] = {"file_id": "file-1", "file_unique_id": "unique-1", "file_name": "original.pdf",
                               "mime_type": "application/pdf", "file_size": 1024}
        if kind == "caption":
            message["caption"] = "Исходная подпись"
    return {"update_id": update_id, "message": message}


@pytest.mark.parametrize("workflow", ["ticket", "task", "auto"])
@pytest.mark.parametrize("confirmation", [False, True])
def test_selection_waits_for_one_message_without_distribution_or_timeout(flow, workflow, confirmation):
    result = select_button(flow, workflow, confirmation=confirmation)
    assert result["group_waiting"] is True
    flow.db.refresh(flow.ticket)
    assert flow.ticket.assignee_id is None and flow.ticket.intake_state == "collecting"
    flow.copied.assert_not_called()
    assert flow.sent.call_args.args[1] == "Пришлите одно сообщение"
    flow.ticket.updated_at = datetime.utcnow() - timedelta(days=1)
    flow.db.commit()
    assert telegram.cleanup_expired_task_intents(flow.db) == 0
    result = telegram.process_telegram_update(flow.db, incoming(flow), notify_client=True)
    assert result["automation_completed"] is True
    assert flow.ticket.status == "closed" and flow.ticket.assignee_id is None


@pytest.mark.parametrize("kind", ["text", "caption", "file"])
def test_original_is_copied_once_history_archive_and_group_settings_preserved(flow, kind):
    group = flow.db.get(TelegramAutomationGroup, flow.button.telegram_group_id)
    snapshot = {col.name: getattr(group, col.name) for col in group.__table__.columns}
    select_button(flow)
    update = incoming(flow, kind)
    result = telegram.process_telegram_update(flow.db, update, notify_client=True)
    assert result["copied_message_id"] == 12345
    flow.copied.assert_called_once_with(group.chat_id, flow.contact.external_chat_id, "11")
    assert telegram.process_telegram_update(flow.db, update, notify_client=True) == {"ok": True, "duplicate": True}
    flow.db.expire_all()
    source = flow.db.scalars(select(TicketMessage).where(TicketMessage.message_type == "client")).one()
    assert source.telegram_message_id == "11"
    assert source.body == update["message"].get("text", update["message"].get("caption", ""))
    attachments = flow.db.scalars(select(TicketAttachment)).all()
    if kind == "text":
        assert attachments == []
        flow.archive.assert_not_called()
    else:
        assert len(attachments) == 1 and attachments[0].message_id == source.id
        assert attachments[0].telegram_file_id == "file-1" and attachments[0].original_filename == "original.pdf"
        flow.archive.assert_called_once_with(attachments[0].id)
    assert flow.ticket.status == "closed" and flow.ticket.intake_state == "ready"
    assert {col: getattr(group, col) for col in snapshot} == snapshot
    following = telegram.process_telegram_update(flow.db, incoming(flow, update_id=12), notify_client=False)
    assert following == {"ok": True, "ignored": "problem_type_required"}
    flow.copied.assert_called_once()
    assert len(flow.db.scalars(select(TicketMessage).where(TicketMessage.message_type == "client")).all()) == 1


@pytest.mark.parametrize("response", [
    {"ok": False}, {"ok": True}, {"ok": True, "result": {}},
    {"ok": True, "result": {"message_id": 0}}, {"ok": True, "result": {"message_id": -1}},
    {"ok": True, "result": {"message_id": True}}, {"ok": True, "result": {"message_id": "123"}},
])
def test_only_confirmed_positive_integer_message_id_allows_closing(flow, response):
    select_button(flow)
    flow.copied.return_value = response
    update = incoming(flow, "caption")
    result = telegram.process_telegram_update(flow.db, update)
    assert result["automation_handoff"] is True
    assert flow.ticket.status != "closed" and flow.ticket.closed_at is None
    assert flow.ticket.intake_state == "ready" and flow.ticket.workflow_type == "ticket"
    assert flow.db.scalars(select(TicketMessage).where(TicketMessage.message_type == "client")).one().body == "Исходная подпись"
    assert len(flow.db.scalars(select(TicketAttachment)).all()) == 1
    telegram.process_telegram_update(flow.db, update)
    flow.copied.assert_called_once()


def test_timeout_uses_handoff_without_retry_and_preserves_source(flow):
    select_button(flow)
    flow.copied.side_effect = TimeoutError("ambiguous result")
    result = telegram.process_telegram_update(flow.db, incoming(flow))
    assert result["automation_handoff"] is True and flow.ticket.status != "closed"
    assert any("не подтверждено" in m.body for m in flow.db.scalars(select(TicketMessage)))
    telegram.process_telegram_update(flow.db, incoming(flow))
    flow.copied.assert_called_once()


@pytest.mark.parametrize("recovery", ["duplicate", "new_update", "startup"])
@pytest.mark.parametrize("crash_after_ack", [False, True])
def test_incomplete_copy_is_recovered_without_resending_or_losing_ticket(flow, monkeypatch, recovery, crash_after_ack):
    select_button(flow)
    complete = telegram._complete_auto_ticket
    if crash_after_ack:
        monkeypatch.setattr(telegram, "_complete_auto_ticket", Mock(side_effect=SystemExit("process stopped")))
    else:
        flow.copied.side_effect = SystemExit("process stopped")
    update = incoming(flow, "caption")
    with pytest.raises(SystemExit):
        telegram.process_telegram_update(flow.db, update)
    flow.db.rollback()  # Simulate process/session termination after the durable claim.
    flow.db.refresh(flow.ticket)
    assert flow.ticket.intake_state == TicketIntakeState.TELEGRAM_GROUP_COPYING.value
    if crash_after_ack:
        receipt = flow.ticket.quick_action_state_json["telegram_group_delivery"]
        assert receipt == {"confirmed": True, "message_id": 12345,
                           "chat_id": "-100", "group_id": flow.button.telegram_group_id}
        monkeypatch.setattr(telegram, "_complete_auto_ticket", complete)
    if recovery == "startup":
        with flow.factory() as restarted_db:
            assert telegram.recover_incomplete_group_automations(restarted_db) == 1
            assert telegram.recover_incomplete_group_automations(restarted_db) == 0
    else:
        telegram.process_telegram_update(flow.db, update if recovery == "duplicate" else incoming(flow, update_id=12))
    flow.db.expire_all()
    assert (flow.ticket.status == "closed") is crash_after_ack
    assert flow.ticket.intake_state == "ready"
    assert flow.ticket.workflow_type == ("auto" if crash_after_ack else "ticket")
    assert flow.ticket.assignee_id is None
    assert flow.db.get(Ticket, flow.ticket.id) is not None
    flow.copied.assert_called_once()
    assert flow.db.scalar(select(TicketMessage).where(TicketMessage.telegram_message_id == "11")).body == "Исходная подпись"
    assert len(flow.db.scalars(select(TicketAttachment)).all()) == 1


@pytest.mark.parametrize("status", ["unavailable", "wrong_environment", "archive", "wrong_bot"])
def test_group_access_failure_never_copies_and_keeps_request_open(flow, status):
    select_button(flow)
    group = flow.db.get(TelegramAutomationGroup, flow.button.telegram_group_id)
    if status == "unavailable":
        group.status = "unavailable"
    elif status == "wrong_environment":
        group.environment = "prod"
    elif status == "archive":
        group.chat_id = "-999"
    else:
        group.bot_id = "other-bot"
    flow.db.commit()
    result = telegram.process_telegram_update(flow.db, incoming(flow))
    assert result["automation_handoff"] is True and flow.ticket.status != "closed"
    flow.copied.assert_not_called()


@pytest.mark.parametrize("same_update", [False, True])
def test_parallel_updates_copy_only_the_first_message_and_recovery_skips_inflight(flow, tmp_path, same_update):
    select_button(flow)
    first, second = incoming(flow), incoming(flow, update_id=11 if same_update else 12)
    path = tmp_path / "parallel.db"
    with sqlite3.connect(path) as target:
        flow.db.connection().connection.driver_connection.backup(target)
    engine = create_engine(f"sqlite:///{path}", connect_args={"check_same_thread": False})
    factory = sessionmaker(bind=engine, autoflush=False)
    entered, release = Event(), Event()
    def copy(*args, **kwargs):
        entered.set()
        assert release.wait(10)
        return {"ok": True, "result": {"message_id": 99}}
    flow.copied.side_effect = copy
    def process(update):
        with factory() as db:
            return telegram.process_telegram_update(db, update)
    try:
        with ThreadPoolExecutor(max_workers=2) as workers:
            a = workers.submit(process, first)
            assert entered.wait(10)
            b = workers.submit(process, second)
            try:
                with factory() as db:
                    assert telegram.recover_incomplete_group_automations(db) == 0
            finally:
                release.set()
            assert a.result(timeout=10)["automation_completed"] is True
            second_result = b.result(timeout=10)
            if same_update:
                assert second_result == {"ok": True, "duplicate": True}
            else:
                assert second_result == {"ok": True, "ignored": "problem_type_required"}
        flow.copied.assert_called_once()
        with factory() as db:
            sources = db.scalars(select(TicketMessage).where(TicketMessage.message_type == "client")).all()
            assert len(sources) == 1
    finally:
        engine.dispose()


@pytest.mark.parametrize("chat_type", ["group", "supergroup"])
def test_group_messages_do_not_enter_client_automation(flow, chat_type):
    select_button(flow)
    update = incoming(flow)
    update["message"]["chat"] = {"id": -100, "type": chat_type}
    assert telegram.process_telegram_update(flow.db, update)["ignored"] == "non_private_chat"
    flow.copied.assert_not_called()
    assert not flow.db.scalars(select(TicketMessage).where(TicketMessage.message_type == "client")).all()


def test_parallel_button_callbacks_start_waiting_only_once(flow, tmp_path):
    update = {"update_id": 10, "callback_query": {
        "id": "select", "data": f"btnid:{flow.button.id}",
        "from": {"id": int(flow.contact.external_user_id)},
        "message": {"message_id": 10, "chat": {"id": int(flow.contact.external_chat_id), "type": "private"}},
    }}
    path = tmp_path / "parallel-callback.db"
    with sqlite3.connect(path) as target:
        flow.db.connection().connection.driver_connection.backup(target)
    engine = create_engine(f"sqlite:///{path}", connect_args={"check_same_thread": False})
    factory = sessionmaker(bind=engine, autoflush=False)
    entered, release = Event(), Event()
    def send(*args, **kwargs):
        entered.set()
        assert release.wait(10)
        return {"ok": True, "result": {"message_id": 500}}
    flow.sent.side_effect = send
    def process():
        with factory() as db:
            return telegram.process_telegram_update(db, update, notify_client=True)
    try:
        with ThreadPoolExecutor(max_workers=2) as workers:
            first = workers.submit(process)
            assert entered.wait(10)
            second = workers.submit(process)
            release.set()
            assert first.result(timeout=10)["group_waiting"] is True
            assert second.result(timeout=10) == {"ok": True, "duplicate": True}
        flow.sent.assert_called_once()
        flow.copied.assert_not_called()
        with factory() as db:
            assert len(db.scalars(select(Ticket)).all()) == 1
            assert db.get(Ticket, flow.ticket.id).intake_state == "collecting"
    finally:
        engine.dispose()


@pytest.mark.parametrize("raise_inside", [False, True])
def test_postgres_lock_stays_on_business_connection_across_commits_and_is_released(raise_inside):
    class Connection:
        held = False
        def __enter__(self): return self
        def __exit__(self, *args): assert not self.held
        def execute(self, sql, params): self.held = "pg_advisory_unlock" not in str(sql)
        def commit(self): pass
        def invalidate(self): self.held = False
    control = Connection()
    engine = SimpleNamespace(dialect=SimpleNamespace(name="postgresql"), connect=lambda: control)
    db = SimpleNamespace(get_bind=lambda: engine, bind=engine, rollback=Mock())
    try:
        with automation.chat_lock(db, "100500") as acquired:
            assert acquired and db.bind is control
            control.commit()  # Durable source/update claim must not release the session lock.
            assert control.held
            control.commit()  # Closing/handoff uses the same pinned connection.
            if raise_inside:
                raise RuntimeError("process operation failed")
    except RuntimeError:
        assert raise_inside
    assert db.bind is engine and not control.held


def test_large_attachment_preserves_source_and_uses_fallback_without_copy(flow):
    select_button(flow)
    update = incoming(flow, "caption")
    update["message"]["document"]["file_size"] = 10 ** 10
    result = telegram.process_telegram_update(flow.db, update)
    assert result["automation_handoff"] is True and flow.ticket.status != "closed"
    flow.copied.assert_not_called()
    assert flow.db.scalar(select(TicketMessage).where(TicketMessage.message_type == "client")).body == "Исходная подпись"


def test_client_notification_failure_after_confirmed_copy_does_not_reopen_or_resend(flow):
    select_button(flow)
    flow.sent.side_effect = RuntimeError("notification unavailable")
    result = telegram.process_telegram_update(flow.db, incoming(flow), notify_client=True)
    assert result["automation_completed"] is True and flow.ticket.status == "closed"
    assert telegram.process_telegram_update(flow.db, incoming(flow)) == {"ok": True, "duplicate": True}
    flow.copied.assert_called_once()


@pytest.mark.parametrize("failure", ["append_system", "remove_close", "complete", "commit"])
def test_confirmed_delivery_survives_closing_failure_and_restart(flow, monkeypatch, failure):
    select_button(flow)
    flow.ticket.quick_action_state_json = {"greeting": True}
    flow.db.commit()
    callbacks = telegram._group_automation_callbacks()
    commit = flow.db.commit
    if failure == "commit":
        def failed_commit():
            if flow.ticket.status == "closed":
                raise RuntimeError("closing commit failed")
            commit()
        monkeypatch.setattr(flow.db, "commit", failed_commit)
    else:
        monkeypatch.setattr(telegram, "_group_automation_callbacks", lambda: {
            **callbacks, failure: Mock(side_effect=RuntimeError("closing failed")),
        })
    update = incoming(flow, "caption")
    assert telegram.process_telegram_update(flow.db, update)["automation_closing_pending"] is True
    flow.db.rollback()
    flow.db.refresh(flow.ticket)
    assert flow.ticket.status != "closed" and flow.ticket.workflow_type == "auto"
    assert flow.ticket.intake_state == TicketIntakeState.TELEGRAM_GROUP_COPYING.value
    assert len(flow.ticket.intake_state) <= Ticket.__table__.c.intake_state.type.length
    receipt = flow.ticket.quick_action_state_json["telegram_group_delivery"]
    assert receipt == {"confirmed": True, "message_id": 12345,
                       "chat_id": "-100", "group_id": flow.button.telegram_group_id}
    assert flow.ticket.quick_action_state_json["greeting"] is True
    monkeypatch.setattr(flow.db, "commit", commit)
    monkeypatch.setattr(telegram, "_group_automation_callbacks", lambda: callbacks)
    # Recovery reads only the persisted receipt, even if the button's destination changed.
    flow.button.telegram_group_id = None
    flow.db.commit()
    with flow.factory() as restarted_db:
        assert telegram.recover_incomplete_group_automations(restarted_db) == 1
        assert telegram.recover_incomplete_group_automations(restarted_db) == 0
        recovered = restarted_db.get(Ticket, flow.ticket.id)
        assert recovered.status == "closed" and recovered.assignee_id is None
        assert recovered.quick_action_state_json["telegram_group_delivery"] == receipt
        messages = restarted_db.scalars(select(TicketMessage)).all()
        assert sum(m.body == "Автоматическая заявка обработана системой" for m in messages) == 1
        assert sum(m.body.startswith("Сообщение скопировано") for m in messages) == 1
        assert not any("передана сотрудникам" in m.body for m in messages)
        assert len(restarted_db.scalars(select(TicketAttachment)).all()) == 1
        assert restarted_db.scalar(select(TicketMessage).where(TicketMessage.telegram_message_id == "11")).body == "Исходная подпись"
        assert telegram.process_telegram_update(restarted_db, update) == {"ok": True, "duplicate": True}
    flow.copied.assert_called_once()


@pytest.mark.parametrize("persisted", [False, True])
@pytest.mark.parametrize("interrupted", [False, True])
def test_receipt_commit_failure_or_interruption_never_resends(flow, monkeypatch, persisted, interrupted):
    select_button(flow)
    commit = flow.db.commit
    attempted = False
    def commit_receipt():
        nonlocal attempted
        if not attempted and (flow.ticket.quick_action_state_json or {}).get("telegram_group_delivery"):
            attempted = True
            if persisted:
                commit()
            raise SystemExit("process stopped") if interrupted else RuntimeError("receipt commit failed")
        commit()
    monkeypatch.setattr(flow.db, "commit", commit_receipt)
    update = incoming(flow, "caption")
    if interrupted:
        with pytest.raises(SystemExit):
            telegram.process_telegram_update(flow.db, update)
        flow.db.rollback()
    else:
        result = telegram.process_telegram_update(flow.db, update)
        assert result["automation_completed" if persisted else "automation_handoff"] is True
    monkeypatch.setattr(flow.db, "commit", commit)
    with flow.factory() as restarted_db:
        assert telegram.recover_incomplete_group_automations(restarted_db) == int(interrupted)
        assert telegram.recover_incomplete_group_automations(restarted_db) == 0
        ticket = restarted_db.get(Ticket, flow.ticket.id)
        assert (ticket.status == "closed") is persisted
        assert ticket.workflow_type == ("auto" if persisted else "ticket")
        assert bool((ticket.quick_action_state_json or {}).get("telegram_group_delivery")) is persisted
        assert len(restarted_db.scalars(select(TicketAttachment)).all()) == 1
        assert telegram.process_telegram_update(restarted_db, update) == {"ok": True, "duplicate": True}
    flow.copied.assert_called_once()


def test_lost_closing_commit_acknowledgement_does_not_close_twice(flow, monkeypatch):
    select_button(flow)
    commit = flow.db.commit
    def commit_closed():
        closing = flow.ticket.status == "closed"
        commit()
        if closing:
            raise RuntimeError("closing commit acknowledgement lost")
    monkeypatch.setattr(flow.db, "commit", commit_closed)
    update = incoming(flow)
    assert telegram.process_telegram_update(flow.db, update)["automation_completed"] is True
    assert telegram.recover_incomplete_group_automations(flow.db) == 0
    assert telegram.process_telegram_update(flow.db, update) == {"ok": True, "duplicate": True}
    assert flow.ticket.status == "closed" and flow.ticket.assignee_id is None
    messages = flow.db.scalars(select(TicketMessage)).all()
    assert sum(m.body == "Автоматическая заявка обработана системой" for m in messages) == 1
    flow.copied.assert_called_once()
