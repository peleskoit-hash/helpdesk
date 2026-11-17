import asyncio
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from pathlib import Path
from threading import Barrier
from types import SimpleNamespace
from unittest.mock import Mock
from uuid import uuid4

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, event, inspect, select
from sqlalchemy.orm import sessionmaker

from api.app.db import Base
from api.app.models import MassNotificationOperation as Operation, MassNotificationRecipient as Recipient
from api.app.routers import mass_notifications as router
from api.app.services import mass_notification_delivery as delivery
from api.app.services import mass_notification_staging as staging
from api.app.services import telegram_bot
from api.app.services.mass_notification_operations import (
    begin_mass_notification_component, claim_mass_notification_operation, finish_mass_notification_component,
)
from test_mass_notification_delivery import env as delivery_env


NOW = datetime(2026, 11, 17, 12, tzinfo=timezone.utc)
OLD = NOW - timedelta(minutes=15)
FRESH = OLD + timedelta(microseconds=1)
JOURNAL = "/api/v1/mass-notifications/operations"


@pytest.fixture
def recovery(tmp_path, monkeypatch):
    engine = create_engine(f"sqlite:///{(tmp_path / 'recovery.db').as_posix()}")
    Base.metadata.create_all(engine, tables=[Operation.__table__, Recipient.__table__])
    sessions = sessionmaker(engine)
    monkeypatch.setattr(delivery.tempfile, "gettempdir", lambda: str(tmp_path))
    prohibited = Mock(side_effect=AssertionError("reconciliation must not call Telegram or start delivery"))
    for module, names in [
        (delivery, ("send_message", "copy_message", "stage_mass_notification_file", "run_mass_notification_operation")),
        (staging, ("send_photo", "send_document")),
        (telegram_bot, ("send_message", "copy_message", "send_photo", "send_document")),
    ]:
        for name in names:
            monkeypatch.setattr(module, name, prohibited)

    def create(status="running", *, owner="admin", attachments=None, recipients=(), **fields):
        operation_id = str(uuid4())
        with sessions.begin() as db:
            db.add(Operation(**({
                "id": operation_id, "requested_by_user_id": owner, "idempotency_key": operation_id,
                "request_hash": "a" * 64, "text": "Notice", "filters_json": {"destination": "Support"},
                "attachments_json": attachments or [], "total_count": len(recipients), "status": status,
                "worker_token": "worker" if status == "running" else None,
                "created_at": OLD - timedelta(hours=1),
            } | fields)))
            for index, (recipient_status, components) in enumerate(recipients):
                db.add(Recipient(
                    id=f"{operation_id}:{index}", operation_id=operation_id,
                    client_id=f"client-{index}", chat_id=f"chat-{index}", client_name=f"Recipient {index}",
                    status=recipient_status, components_json=components, error_message="Previous recipient error",
                ))
        folder = tmp_path / "es-desk-mass-notifications" / operation_id
        folder.mkdir(parents=True)
        (folder / "local-file").write_bytes(b"only this operation")
        return operation_id, folder

    def reconcile(operation_id=None):
        with sessions() as db:
            return delivery.reconcile_stale_mass_notification_operations(db, operation_id, now=NOW)

    def read(operation_id):
        with sessions() as db:
            return db.get(Operation, operation_id), db.scalars(
                select(Recipient).where(Recipient.operation_id == operation_id).order_by(Recipient.id)
            ).all()

    yield SimpleNamespace(sessions=sessions, engine=engine, create=create, reconcile=reconcile, read=read)
    prohibited.assert_not_called()
    engine.dispose()


def components(*statuses):
    return [
        {
            "key": "text" if index == 0 else f"file:{index - 1}",
            "kind": "text" if index == 0 else "attachment",
            "status": status,
            "telegram_message_id": index + 100 if status == "sent" else None,
            "error": f"previous {status}" if status in {"failed", "unknown"} else None,
        }
        for index, status in enumerate(statuses)
    ]


@pytest.mark.parametrize(("status", "times", "interrupted"), [
    ("queued", {"created_at": FRESH}, False),
    ("queued", {"created_at": OLD}, True),
    ("running", {"started_at": FRESH}, False),
    ("running", {"started_at": OLD}, True),
    ("running", {"heartbeat_at": FRESH, "started_at": OLD}, False),
    ("running", {"heartbeat_at": OLD, "started_at": OLD}, True),
    ("running", {"worker_token": None}, True),
    ("completed", {}, False),
    ("failed", {}, False),
    ("interrupted", {}, False),
])
def test_stale_threshold_fallbacks_and_terminal_states(recovery, status, times, interrupted):
    operation_id, folder = recovery.create(status, **times)
    assert recovery.reconcile() == ([operation_id] if interrupted else [])
    operation, _ = recovery.read(operation_id)
    assert operation.status == ("interrupted" if interrupted else status)
    if interrupted:
        assert operation.stage == "interrupted"
        assert operation.error_message == delivery.INTERRUPTED_OPERATION_ERROR
        assert operation.finished_at.replace(tzinfo=timezone.utc) == NOW
    assert folder.exists() is not interrupted
    assert recovery.reconcile() == []


def test_preserves_confirmed_uploads_and_recalculates_components(recovery):
    attachments = [
        {"status": status, "staging_chat_id": "stage" if status == "ready" else None,
         "staging_message_id": 45 if status == "ready" else None, "error": f"old {status}"}
        for status in ("pending", "uploading", "ready", "failed", "unknown")
    ]
    original = [
        ("sending", components("sent", "sending", "pending")),
        ("sending", components("sent", "sending")),
        ("sending", components("failed", "unknown")),
        ("failed", components("failed", "failed")),
        ("sent", components("sent", "sent")),
        ("skipped", components("pending")),
        ("pending", components("pending")),
        ("partial", components("sent", "failed")),
    ]
    operation_id, folder = recovery.create(
        attachments=attachments, recipients=original, heartbeat_at=OLD,
    )
    _, fresh_folder = recovery.create("running", heartbeat_at=FRESH)
    assert recovery.reconcile() == [operation_id]
    operation, rows = recovery.read(operation_id)
    assert operation.attachments_json == [
        dict(item, status="unknown", error=delivery.INTERRUPTED_ATTACHMENT_ERROR) if item["status"] == "uploading" else item
        for item in attachments
    ]
    assert operation.attachments_json[2]["staging_message_id"] == 45
    assert (operation.worker_token, operation.text, operation.filters_json, operation.total_count) == (
        "worker", "Notice", {"destination": "Support"}, len(original),
    )
    assert [row.status for row in rows] == ["pending", "partial", "unknown", "failed", "sent", "skipped", "pending", "partial"]
    assert [row.components_json for row in rows] == [
        [
            dict(component, status="unknown", error=delivery.INTERRUPTED_COMPONENT_ERROR)
            if component["status"] == "sending" else component
            for component in source
        ]
        for _, source in original
    ]
    assert all(row.error_message == "Previous recipient error" for row in rows)
    assert not folder.exists()
    assert (fresh_folder / "local-file").exists()


def test_two_reconcilers_can_only_win_once(recovery, monkeypatch):
    operation_id, _ = recovery.create()
    barrier = Barrier(2)
    candidates = delivery._stale_operation_candidates
    cleanup = Mock(wraps=delivery._clean_operation_directory)
    monkeypatch.setattr(delivery, "_clean_operation_directory", cleanup)

    def synchronized_read(db, operation_id, cutoff):
        snapshot = candidates(db, operation_id, cutoff)
        barrier.wait(timeout=5)
        return snapshot

    monkeypatch.setattr(delivery, "_stale_operation_candidates", synchronized_read)
    with ThreadPoolExecutor(max_workers=2) as workers:
        jobs = [workers.submit(recovery.reconcile, operation_id) for _ in range(2)]
        results = [job.result(timeout=10) for job in jobs]
    assert sorted(results) == [[], [operation_id]]
    cleanup.assert_called_once_with(operation_id)


@pytest.mark.parametrize(("field", "new_value"), [
    ("heartbeat_at", FRESH), ("heartbeat_at", OLD - timedelta(seconds=1)),
    ("worker_token", "other-worker"), ("status", "completed"),
])
def test_concurrent_change_after_snapshot_prevents_interrupt(recovery, monkeypatch, field, new_value):
    operation_id, folder = recovery.create(heartbeat_at=OLD)
    candidates = delivery._stale_operation_candidates

    def read_then_change(db, operation_id, cutoff):
        snapshot = candidates(db, operation_id, cutoff)
        assert len(snapshot) == 1
        with recovery.sessions.begin() as other:
            setattr(other.get(Operation, operation_id), field, new_value)
        return snapshot

    monkeypatch.setattr(delivery, "_stale_operation_candidates", read_then_change)
    assert recovery.reconcile(operation_id) == []
    assert recovery.read(operation_id)[0].status == ("completed" if field == "status" else "running")
    assert folder.exists()


@pytest.mark.parametrize("failure", ["corrupt-status", "missing-message-id", "unconfirmed-message-id", "commit", "recipient-update"])
def test_failure_rolls_back_whole_operation_and_keeps_files(recovery, failure, caplog):
    bad_component = components("sending")
    if failure == "corrupt-status":
        bad_component[0]["status"] = "invented"
    if failure == "missing-message-id":
        bad_component[0]["status"] = "sent"
    if failure == "unconfirmed-message-id":
        bad_component[0]["telegram_message_id"] = 99
    recipients = [("sending", components("sending")), ("sending", bad_component)]
    original_attachment = {"status": "uploading", "error": None}
    operation_id, folder = recovery.create(recipients=recipients, attachments=[original_attachment])

    with recovery.sessions() as db:
        if failure == "commit":
            def fail_commit(_session):
                raise RuntimeError("synthetic commit failure")
            event.listen(db, "before_commit", fail_commit, once=True)
        elif failure == "recipient-update":
            def fail_recipient(_mapper, _connection, recipient):
                if recipient.id.endswith(":1"):
                    raise RuntimeError("synthetic recipient update failure")
            event.listen(Recipient, "before_update", fail_recipient)
        try:
            assert delivery.reconcile_stale_mass_notification_operations(db, operation_id, NOW) == []
        finally:
            if failure == "recipient-update":
                event.remove(Recipient, "before_update", fail_recipient)
    operation, rows = recovery.read(operation_id)
    assert (operation.status, operation.stage, operation.finished_at) == ("running", "queued", None)
    assert operation.attachments_json == [original_attachment]
    assert [(row.status, row.components_json) for row in rows] == recipients
    assert (folder / "local-file").exists()
    assert "Could not interrupt mass notification operation" in caplog.text


def test_commit_is_once_per_operation_and_cleanup_follows_it(recovery, monkeypatch):
    operation_id, folder = recovery.create(recipients=[("sending", components("sending"))])
    commits = []
    cleanup = delivery._clean_operation_directory

    def verify_then_clean(operation_id):
        operation, rows = recovery.read(operation_id)
        assert commits == ["committed"]
        assert operation.status == "interrupted"
        assert rows[0].components_json[0]["status"] == "unknown"
        cleanup(operation_id)

    monkeypatch.setattr(delivery, "_clean_operation_directory", verify_then_clean)
    with recovery.sessions() as db:
        event.listen(db, "after_commit", lambda _: commits.append("committed"))
        assert delivery.reconcile_stale_mass_notification_operations(db, operation_id, NOW) == [operation_id]
        assert not claim_mass_notification_operation(db, operation_id, "new-token")
        assert not begin_mass_notification_component(db, operation_id, f"{operation_id}:0", "text", "worker")
        assert not finish_mass_notification_component(db, operation_id, f"{operation_id}:0", "text", "worker", "sent", 99)
    assert not folder.exists()


def test_cleanup_refuses_symlink_and_leaves_target_intact(recovery, tmp_path, monkeypatch, caplog):
    operation_id, folder = recovery.create()
    target = tmp_path / "other-data"
    folder.rename(target)
    try:
        folder.symlink_to(target, target_is_directory=True)
    except OSError as exc:
        if getattr(exc, "winerror", None) != 1314:
            raise
        # Exercise the same guard on Windows workers without symlink permission.
        is_symlink = Path.is_symlink
        monkeypatch.setattr(Path, "is_symlink", lambda path: path == folder or is_symlink(path))
    assert recovery.reconcile(operation_id) == [operation_id]
    assert (target / "local-file").read_bytes() == b"only this operation"
    assert "Cannot clean mass notification directory" in caplog.text


def test_cleanup_failure_is_logged_without_rollback(recovery, monkeypatch, caplog):
    operation_id, folder = recovery.create(recipients=[("sending", components("sending"))])
    with monkeypatch.context() as patch:
        patch.setattr(delivery.shutil, "rmtree", Mock(side_effect=OSError("synthetic cleanup failure")))
        assert recovery.reconcile(operation_id) == [operation_id]
    operation, rows = recovery.read(operation_id)
    assert operation.status == "interrupted"
    assert rows[0].status == "unknown"
    assert folder.exists()
    assert "Cannot clean mass notification directory" in caplog.text


def test_recovery_while_telegram_is_in_flight_stops_existing_worker(delivery_env):
    env = delivery_env
    operation_id, folder, _ = env.create(chat_count=2, files=[("notice.pdf", "application/pdf")])

    def recover_during_send(*args, **kwargs):
        with env.sessions.begin() as db:
            db.get(Operation, operation_id).heartbeat_at = OLD
        with env.sessions() as db:
            assert delivery.reconcile_stale_mass_notification_operations(db, operation_id, NOW) == [operation_id]
        return {"ok": True, "result": {"message_id": 123}}

    env.mocks.send.side_effect = recover_during_send
    delivery.run_mass_notification_operation(operation_id)
    delivery.run_mass_notification_operation(operation_id)
    operation, rows = env.read(operation_id)
    env.mocks.send.assert_called_once()
    env.mocks.document.assert_called_once()
    env.mocks.photo.assert_not_called()
    env.mocks.copy.assert_not_called()
    assert operation.status == "interrupted"
    assert operation.attachments_json[0]["staging_message_id"] == 202
    assert all(row.status == "pending" for row in rows)
    outcomes = [component for row in rows for component in row.components_json]
    assert [item["status"] for item in outcomes].count("unknown") == 1
    assert all(item["telegram_message_id"] is None for item in outcomes)
    assert not folder.exists()


@pytest.mark.parametrize("endpoint", ["latest", "id"])
def test_journal_reconciles_only_owned_stale_operation_and_reads_committed_state(recovery, monkeypatch, endpoint):
    operation_id, _ = recovery.create(recipients=[
        ("sending", components("sent", "sending", "pending")),
        ("sent", components("sent")),
    ])
    fresh_id, fresh_folder = recovery.create("running", created_at=OLD - timedelta(days=1), heartbeat_at=FRESH)
    other_id, other_folder = recovery.create(owner="other-admin")
    original_reconcile = delivery.reconcile_stale_mass_notification_operations
    calls = []

    def frozen_reconcile(db, operation_id):
        calls.append(operation_id)
        return original_reconcile(db, operation_id, NOW)

    monkeypatch.setattr(router, "reconcile_stale_mass_notification_operations", frozen_reconcile)
    app = FastAPI()
    app.include_router(router.router)

    def get_db():
        with recovery.sessions() as db:
            yield db

    app.dependency_overrides[router.get_db] = get_db
    app.dependency_overrides[router.get_current_user] = lambda: SimpleNamespace(id="admin", role="admin")
    with TestClient(app) as client:
        assert client.get(f"{JOURNAL}/{other_id}").status_code == 404
        current = client.get(f"{JOURNAL}/{fresh_id}").json()["operation"]
        result = client.get(f"{JOURNAL}/{'latest' if endpoint == 'latest' else operation_id}").json()["operation"]
    assert current["status"] == "running"
    assert result["operation_id"] == operation_id
    assert result["status"] == "interrupted"
    assert result["error_message"] == delivery.INTERRUPTED_OPERATION_ERROR
    assert (result["processed_count"], result["total_count"], result["pending_count"]) == (1, 2, 1)
    assert result["recipients"][0]["components"][1]["error"] == delivery.INTERRUPTED_COMPONENT_ERROR
    assert result["recipients"][0]["components"][0]["status"] == "sent"
    assert result["recipients"][1]["status"] == "sent"
    assert calls == [fresh_id, operation_id]
    assert recovery.read(other_id)[0].status == "running"
    assert other_folder.exists() and fresh_folder.exists()


def test_startup_reconciles_after_schema_creation_before_runtime_services(tmp_path, monkeypatch):
    from api.app import main
    from api.app.routers import telegram

    engine = create_engine(f"sqlite:///{(tmp_path / 'startup.db').as_posix()}")
    sessions = sessionmaker(engine)
    monkeypatch.setattr(main, "engine", engine)
    monkeypatch.setattr(main, "SessionLocal", sessions)
    monkeypatch.setattr(type(main.settings), "validate_runtime_guards", lambda self: None)
    for name in (
        *(name for name in vars(main) if name.startswith("ensure_")),
        "migrate_legacy_bitrix_passwords", "migrate_legacy_bot_button_pdf_saved_files",
        "cleanup_old_integration_logs_on_startup",
    ):
        monkeypatch.setattr(main, name, lambda *args: None)
    monkeypatch.setattr(telegram, "recover_incomplete_group_automations", lambda db: None)
    called = []

    def reconcile(db):
        assert inspect(db.get_bind()).has_table(Operation.__tablename__)
        assert inspect(db.get_bind()).has_table(Recipient.__tablename__)
        assert delivery.reconcile_stale_mass_notification_operations(db, now=NOW) == []
        called.append("recovery")

    class StopBeforeTelegram(Exception):
        pass

    def stop():
        assert called == ["recovery"]
        raise StopBeforeTelegram

    monkeypatch.setattr(main, "reconcile_stale_mass_notification_operations", reconcile)
    monkeypatch.setattr(main.settings, "telegram_bot_token", "test-token")
    monkeypatch.setattr(main, "set_bot_commands", stop)
    with pytest.raises(StopBeforeTelegram):
        asyncio.run(main.on_startup())
    engine.dispose()
