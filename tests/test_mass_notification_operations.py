from concurrent.futures import ThreadPoolExecutor
from threading import Barrier

import pytest
from sqlalchemy import create_engine, func, inspect, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import sessionmaker

from api.app.db import Base
from api.app.models import MassNotificationOperation as Operation, MassNotificationRecipient as Recipient
from api.app.services import mass_notification_operations as service


@pytest.fixture
def sessions(tmp_path):
    engine = create_engine(f"sqlite:///{(tmp_path / 'operations.db').as_posix()}")
    Base.metadata.create_all(engine, tables=[Operation.__table__, Recipient.__table__])
    yield sessionmaker(bind=engine)
    engine.dispose()


def create(db, **changes):
    values = {
        "requested_by_user_id": "admin",
        "idempotency_key": "request-1",
        "request_hash": "a" * 64,
        "text": "Notice",
        "filters": {"destination": "Support"},
        "attachments": [{"name": "one.pdf"}, {"name": "two.png"}],
        "recipients": [
            {"client_id": "client-1", "chat_id": "chat-1", "client_name": "Client"},
            {"client_id": "client-1", "chat_id": "chat-2", "client_name": "Client"},
        ],
    }
    return service.create_mass_notification_operation(db, **(values | changes))


def test_metadata_and_persisted_recipient_snapshot(sessions):
    schema = inspect(sessions.kw["bind"])
    assert set(schema.get_table_names()) == {"mass_notification_operations", "mass_notification_recipients"}
    assert schema.get_foreign_keys(Recipient.__tablename__)[0]["referred_table"] == Operation.__tablename__
    assert any(index["column_names"] == ["operation_id"] for index in schema.get_indexes(Recipient.__tablename__))
    with sessions() as db:
        operation, created = create(db)
        assert created
        operation_id = operation.id

    with sessions() as db:
        operation = db.get(Operation, operation_id)
        assert operation.status == operation.stage == "queued"
        assert operation.total_count == 2
        assert operation.filters_json == {"destination": "Support"}
        assert operation.attachments_json == [{"name": "one.pdf"}, {"name": "two.png"}]
        rows = db.scalars(select(Recipient).order_by(Recipient.chat_id)).all()
        assert [(row.operation_id, row.client_id, row.chat_id, row.client_name, row.status) for row in rows] == [
            (operation_id, "client-1", "chat-1", "Client", "pending"),
            (operation_id, "client-1", "chat-2", "Client", "pending"),
        ]
        expected = [
            {"key": "text", "kind": "text", "status": "pending", "telegram_message_id": None, "error": None},
            {"key": "file:0", "kind": "attachment", "attachment_index": 0, "status": "pending",
             "telegram_message_id": None, "error": None},
            {"key": "file:1", "kind": "attachment", "attachment_index": 1, "status": "pending",
             "telegram_message_id": None, "error": None},
        ]
        assert all(row.components_json == expected for row in rows)


def test_recipient_insert_failure_rolls_back_operation_and_snapshot(sessions):
    invalid_snapshot = [
        {"client_id": "client-1", "chat_id": "chat-1", "client_name": "Client"},
        {"client_id": "client-2", "chat_id": "chat-2", "client_name": None},
    ]
    with sessions() as db:
        with pytest.raises(IntegrityError):
            create(db, recipients=invalid_snapshot)
    with sessions() as db:
        assert db.scalar(select(func.count()).select_from(Operation)) == 0
        assert db.scalar(select(func.count()).select_from(Recipient)) == 0


def test_attachment_only_has_no_text_component(sessions):
    with sessions() as db:
        operation, _ = create(db, text="", attachments=[{"name": "only.pdf"}])
        operation_id = operation.id
    with sessions() as db:
        assert db.get(Operation, operation_id).text == ""
        for recipient in db.scalars(select(Recipient)):
            assert [component["key"] for component in recipient.components_json] == ["file:0"]


@pytest.mark.parametrize("changes", [
    {"text": " ", "attachments": []},
    {"attachments": [{}] * 11},
    {"recipients": []},
    {"idempotency_key": " "},
    {"requested_by_user_id": ""},
    {"request_hash": "z" * 64},
    {"request_hash": "abc"},
    {"recipients": [{"client_id": "client", "chat_id": " ", "client_name": "Client"}]},
    {"recipients": [
        {"client_id": "first", "chat_id": "chat", "client_name": "First"},
        {"client_id": "second", "chat_id": " chat ", "client_name": "Second"},
    ]},
])
def test_invalid_request_does_not_persist(sessions, changes):
    with sessions() as db:
        with pytest.raises(ValueError):
            create(db, **changes)
    with sessions() as db:
        assert db.scalar(select(func.count()).select_from(Operation)) == 0


def test_idempotent_replay_does_not_replace_snapshot(sessions):
    with sessions() as db:
        operation, _ = create(db)
        operation_id = operation.id
    with sessions() as db:
        replay, created = create(db, recipients=[
            {"client_id": "different", "chat_id": "chat-3", "client_name": "Different"},
        ])
        assert (replay.id, created, replay.total_count) == (operation_id, False, 2)
        assert set(db.scalars(select(Recipient.chat_id))) == {"chat-1", "chat-2"}


@pytest.mark.parametrize("changes", [{"request_hash": "b" * 64}, {"requested_by_user_id": "other-admin"}])
def test_reusing_key_for_another_request_conflicts(sessions, changes):
    with sessions() as db:
        create(db)
    with sessions() as db:
        with pytest.raises(service.MassNotificationOperationConflict):
            create(db, **changes)


def test_concurrent_creates_use_the_unique_constraint(sessions, monkeypatch):
    barrier = Barrier(2)
    find_operation = service._find_operation

    def find_with_race(db, key):
        operation = find_operation(db, key)
        if operation is None:
            barrier.wait(timeout=5)
        return operation

    monkeypatch.setattr(service, "_find_operation", find_with_race)

    def create_in_session():
        with sessions() as db:
            operation, created = create(db)
            return operation.id, created

    with ThreadPoolExecutor(max_workers=2) as pool:
        tasks = [pool.submit(create_in_session) for _ in range(2)]
        first, second = [task.result(timeout=10) for task in tasks]
    assert first[0] == second[0]
    assert sorted([first[1], second[1]]) == [False, True]
    with sessions() as db:
        assert db.scalar(select(func.count()).select_from(Operation)) == 1
        assert db.scalar(select(func.count()).select_from(Recipient)) == 2


def test_snapshot_chat_uniqueness_is_enforced_by_db(sessions):
    with sessions() as db:
        operation, _ = create(db)
        db.add(Recipient(
            id="duplicate", operation_id=operation.id, client_id="different",
            chat_id="chat-1", client_name="Duplicate", components_json=[],
        ))
        with pytest.raises(IntegrityError):
            db.commit()


def test_operation_claims_once_across_sessions(sessions):
    with sessions() as db:
        operation, _ = create(db)
        operation_id = operation.id
    with sessions() as first, sessions() as second:
        assert service.claim_mass_notification_operation(first, operation_id, "worker-1")
        assert not service.claim_mass_notification_operation(second, operation_id, "worker-2")
    with sessions() as db:
        claimed = db.get(Operation, operation_id)
        assert claimed.status == "running"
        assert claimed.worker_token == "worker-1"
        assert claimed.started_at == claimed.heartbeat_at
        assert claimed.started_at is not None


@pytest.fixture
def running(sessions):
    def prepare(attachments=()):
        with sessions() as db:
            operation, _ = create(db, attachments=list(attachments))
            operation_id = operation.id
            assert service.claim_mass_notification_operation(db, operation_id, "worker")
            recipient_id = db.scalar(select(Recipient.id).where(Recipient.chat_id == "chat-1"))
        return operation_id, recipient_id
    return prepare


def test_component_begin_is_durable_and_concurrent_begin_is_rejected(sessions, running):
    ids = running()
    barrier = Barrier(2)

    def begin():
        with sessions() as db:
            barrier.wait(timeout=5)
            return service.begin_mass_notification_component(db, *ids, "text", "worker")

    with ThreadPoolExecutor(max_workers=2) as pool:
        tasks = [pool.submit(begin) for _ in range(2)]
        assert sorted(task.result(timeout=10) for task in tasks) == [False, True]
    with sessions() as db:
        persisted = db.get(Recipient, ids[1])
        assert persisted.status == persisted.components_json[0]["status"] == "sending"
        assert persisted.components_json[0]["telegram_message_id"] is None


def test_positive_message_id_persists_and_cannot_be_overwritten(sessions, running):
    ids = running()
    with sessions() as db:
        assert not service.finish_mass_notification_component(db, *ids, "text", "worker", "sent", 42)
        assert service.begin_mass_notification_component(db, *ids, "text", "worker")
        for invalid in (None, 0, -1, True):
            with pytest.raises(ValueError, match="positive"):
                service.finish_mass_notification_component(db, *ids, "text", "worker", "sent", invalid)
        with pytest.raises(ValueError, match="finish"):
            service.finish_mass_notification_component(db, *ids, "text", "worker", "pending")
        assert service.finish_mass_notification_component(db, *ids, "text", "worker", "sent", 42)
    with sessions() as db:
        assert not service.finish_mass_notification_component(db, *ids, "text", "worker", "sent", 43)
        assert not service.begin_mass_notification_component(db, *ids, "text", "worker")
        recipient = db.get(Recipient, ids[1])
        assert recipient.status == "sent"
        assert recipient.components_json[0]["telegram_message_id"] == 42


@pytest.mark.parametrize(("text_status", "attachment_status", "recipient_status"), [
    ("sent", "failed", "partial"),
    ("failed", "unknown", "unknown"),
    ("failed", "failed", "failed"),
    ("sent", "sent", "sent"),
])
def test_aggregate_status_preserves_component_outcomes(sessions, running, text_status, attachment_status, recipient_status):
    ids = running([{"name": "one.pdf"}])
    for component, status in [("text", text_status), ("file:0", attachment_status)]:
        with sessions() as db:
            assert service.begin_mass_notification_component(db, *ids, component, "worker")
            message_id, error = (51, None) if status == "sent" else (None, f"{component} error")
            assert service.finish_mass_notification_component(db, *ids, component, "worker", status, message_id, error)
        with sessions() as db:
            assert db.get(Recipient, ids[1]).status == ("sending" if component == "text" else recipient_status)
            assert not service.begin_mass_notification_component(db, *ids, component, "worker")
    with sessions() as db:
        components = db.get(Recipient, ids[1]).components_json
        assert [component["status"] for component in components] == [text_status, attachment_status]
        assert [component["error"] for component in components] == [
            None if status == "sent" else f"{key} error"
            for key, status in [("text", text_status), ("file:0", attachment_status)]
        ]


def test_only_owner_can_begin_finish_and_skip(sessions, running):
    ids = running()
    with sessions() as db:
        assert not service.begin_mass_notification_component(db, *ids, "text", "other")
        assert not service.skip_mass_notification_recipient(db, *ids, "other", "reason")
        assert service.begin_mass_notification_component(db, *ids, "text", "worker")
        assert not service.finish_mass_notification_component(db, *ids, "text", "other", "sent", 42)
    with sessions() as db:
        recipient = db.get(Recipient, ids[1])
        assert recipient.status == recipient.components_json[0]["status"] == "sending"


def test_skip_only_before_any_component_starts(sessions, running):
    operation_id, recipient_id = running()
    with sessions() as db:
        other_id = db.scalar(select(Recipient.id).where(Recipient.chat_id == "chat-2"))
        assert service.begin_mass_notification_component(db, operation_id, recipient_id, "text", "worker")
        assert not service.skip_mass_notification_recipient(db, operation_id, recipient_id, "worker", "too late")
        assert service.skip_mass_notification_recipient(db, operation_id, other_id, "worker", "no longer eligible")
        assert not service.begin_mass_notification_component(db, operation_id, other_id, "text", "worker")
    with sessions() as db:
        skipped = db.get(Recipient, other_id)
        assert (skipped.status, skipped.error_message) == ("skipped", "no longer eligible")
        assert skipped.components_json[0]["status"] == "pending"
