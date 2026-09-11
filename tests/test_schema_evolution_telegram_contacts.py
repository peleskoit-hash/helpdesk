from __future__ import annotations

import uuid
from datetime import datetime

import pytest
from sqlalchemy import create_engine, text

from sqlalchemy.exc import IntegrityError

from api.app.models import TelegramContact, TelegramNotificationAccount, TicketAttachment, TicketAttachmentPreload, User
from api.app.services import schema_evolution


def _create_legacy_telegram_contacts_table(engine) -> None:
    with engine.begin() as conn:
        conn.execute(
            text(
                """
                CREATE TABLE companies (
                    id VARCHAR(36) PRIMARY KEY,
                    name VARCHAR(200),
                    is_active BOOLEAN,
                    created_at TIMESTAMP
                )
                """
            )
        )
        conn.execute(
            text(
                """
                CREATE TABLE clients (
                    id VARCHAR(36) PRIMARY KEY,
                    company_id VARCHAR(36),
                    display_name VARCHAR(200)
                )
                """
            )
        )
        conn.execute(
            text(
                """
                CREATE TABLE telegram_contacts (
                    id VARCHAR(36) PRIMARY KEY,
                    company_id VARCHAR(36) NOT NULL,
                    client_id VARCHAR(36) NOT NULL,
                    external_user_id VARCHAR(100) NOT NULL,
                    external_chat_id VARCHAR(100) NOT NULL UNIQUE,
                    created_at TIMESTAMP,
                    updated_at TIMESTAMP
                )
                """
            )
        )


def _create_legacy_users_table(engine) -> None:
    with engine.begin() as conn:
        conn.execute(
            text(
                """
                CREATE TABLE users (
                    id VARCHAR(36) PRIMARY KEY,
                    company_id VARCHAR(36) NOT NULL,
                    email VARCHAR(255),
                    full_name VARCHAR(200) NOT NULL,
                    role VARCHAR(20) NOT NULL,
                    is_active BOOLEAN
                )
                """
            )
        )
        conn.execute(
            text(
                """
                INSERT INTO users (
                    id,
                    company_id,
                    email,
                    full_name,
                    role,
                    is_active
                ) VALUES (
                    'user-1',
                    'company-1',
                    'user-1@example.test',
                    'User One',
                    'agent',
                    TRUE
                )
                """
            )
        )


def _create_minimal_users_table(engine) -> None:
    with engine.begin() as conn:
        conn.execute(
            text(
                """
                CREATE TABLE users (
                    id VARCHAR(36) NOT NULL PRIMARY KEY
                )
                """
            )
        )
        conn.execute(text("INSERT INTO users (id) VALUES ('user-1')"))


def _telegram_notification_columns(engine) -> dict[str, dict]:
    with engine.connect() as conn:
        rows = conn.execute(text("PRAGMA table_info(telegram_notification_accounts)")).mappings().all()
    return {
        row["name"]: {
            "type": str(row["type"]),
            "notnull": int(row["notnull"]),
            "default": row["dflt_value"],
            "pk": int(row["pk"]),
        }
        for row in rows
    }


def _telegram_notification_indexes(engine) -> dict[str, tuple[str, ...]]:
    with engine.connect() as conn:
        rows = conn.execute(text("PRAGMA index_list(telegram_notification_accounts)")).mappings().all()
        indexes = {}
        for row in rows:
            if not row["unique"]:
                continue
            columns = conn.execute(text(f"PRAGMA index_info({row['name']})")).mappings().all()
            indexes[row["name"]] = tuple(column["name"] for column in columns)
    return indexes


def _telegram_notification_foreign_keys(engine) -> list[dict]:
    with engine.connect() as conn:
        return conn.execute(text("PRAGMA foreign_key_list(telegram_notification_accounts)")).mappings().all()


def _insert_contact(
    engine,
    *,
    company_id: str,
    client_id: str,
    external_user_id: str,
    external_chat_id: str,
) -> None:
    with engine.begin() as conn:
        now = datetime.utcnow()
        conn.execute(
            text(
                """
                INSERT INTO telegram_contacts (
                    id,
                    company_id,
                    client_id,
                    external_user_id,
                    external_chat_id,
                    created_at,
                    updated_at
                ) VALUES (
                    :id,
                    :company_id,
                    :client_id,
                    :external_user_id,
                    :external_chat_id,
                    :created_at,
                    :updated_at
                )
                """
            ),
            {
                "id": str(uuid.uuid4()),
                "company_id": company_id,
                "client_id": client_id,
                "external_user_id": external_user_id,
                "external_chat_id": external_chat_id,
                "created_at": now,
                "updated_at": now,
            },
        )


def _unique_indexes(engine) -> dict[str, tuple[str, ...]]:
    with engine.connect() as conn:
        rows = conn.execute(text("PRAGMA index_list(telegram_contacts)")).mappings().all()
        indexes = {}
        for row in rows:
            if not row["unique"]:
                continue
            columns = conn.execute(text(f"PRAGMA index_info({row['name']})")).mappings().all()
            indexes[row["name"]] = tuple(column["name"] for column in columns)
        return indexes


def _preload_columns(engine) -> dict[str, str]:
    with engine.connect() as conn:
        rows = conn.execute(text("PRAGMA table_info(ticket_attachment_preloads)")).mappings().all()
    return {row["name"]: str(row["type"]) for row in rows}


def _preload_indexes(engine) -> dict[str, tuple[str, ...]]:
    with engine.connect() as conn:
        rows = conn.execute(text("PRAGMA index_list(ticket_attachment_preloads)")).mappings().all()
        indexes = {}
        for row in rows:
            columns = conn.execute(text(f"PRAGMA index_info({row['name']})")).mappings().all()
            indexes[row["name"]] = tuple(column["name"] for column in columns)
    return indexes


def test_clean_existing_db_gets_company_user_uniqueness(monkeypatch):
    engine = create_engine("sqlite:///:memory:", future=True)
    _create_legacy_telegram_contacts_table(engine)
    _insert_contact(engine, company_id="company-a", client_id="client-a", external_user_id="123", external_chat_id="chat-1")
    monkeypatch.setattr(schema_evolution, "engine", engine)

    schema_evolution.ensure_telegram_contact_company_user_unique()

    indexes = _unique_indexes(engine)
    assert schema_evolution.TELEGRAM_CONTACT_COMPANY_USER_UNIQUE_NAME in indexes
    assert indexes[schema_evolution.TELEGRAM_CONTACT_COMPANY_USER_UNIQUE_NAME] == ("company_id", "external_user_id")
    assert any(columns == ("external_chat_id",) for columns in indexes.values())


def test_second_run_is_idempotent(monkeypatch):
    engine = create_engine("sqlite:///:memory:", future=True)
    _create_legacy_telegram_contacts_table(engine)
    _insert_contact(engine, company_id="company-a", client_id="client-a", external_user_id="123", external_chat_id="chat-1")
    monkeypatch.setattr(schema_evolution, "engine", engine)

    schema_evolution.ensure_telegram_contact_company_user_unique()
    indexes_after_first_run = _unique_indexes(engine)

    schema_evolution.ensure_telegram_contact_company_user_unique()
    indexes_after_second_run = _unique_indexes(engine)

    assert indexes_after_second_run == indexes_after_first_run
    assert list(indexes_after_second_run.values()).count(("company_id", "external_user_id")) == 1


def test_duplicate_same_company_fails_before_ddl_and_keeps_rows(monkeypatch):
    engine = create_engine("sqlite:///:memory:", future=True)
    _create_legacy_telegram_contacts_table(engine)
    _insert_contact(engine, company_id="company-a", client_id="client-a", external_user_id="123", external_chat_id="chat-1")
    _insert_contact(engine, company_id="company-a", client_id="client-b", external_user_id="123", external_chat_id="chat-2")
    monkeypatch.setattr(schema_evolution, "engine", engine)

    with pytest.raises(RuntimeError, match="duplicate Telegram identity groups"):
        schema_evolution.ensure_telegram_contact_company_user_unique()

    indexes = _unique_indexes(engine)
    assert schema_evolution.TELEGRAM_CONTACT_COMPANY_USER_UNIQUE_NAME not in indexes
    with engine.connect() as conn:
        row_count = conn.execute(text("SELECT COUNT(*) FROM telegram_contacts")).scalar_one()
    assert row_count == 2


def test_same_user_in_different_companies_is_allowed(monkeypatch):
    engine = create_engine("sqlite:///:memory:", future=True)
    _create_legacy_telegram_contacts_table(engine)
    _insert_contact(engine, company_id="company-a", client_id="client-a", external_user_id="123", external_chat_id="chat-1")
    _insert_contact(engine, company_id="company-b", client_id="client-b", external_user_id="123", external_chat_id="chat-2")
    monkeypatch.setattr(schema_evolution, "engine", engine)

    schema_evolution.ensure_telegram_contact_company_user_unique()

    indexes = _unique_indexes(engine)
    assert indexes[schema_evolution.TELEGRAM_CONTACT_COMPANY_USER_UNIQUE_NAME] == ("company_id", "external_user_id")


def test_telegram_contact_model_metadata_contains_composite_unique():
    unique_constraints = {
        tuple(column.name for column in constraint.columns): constraint.name
        for constraint in TelegramContact.__table__.constraints
        if getattr(constraint, "columns", None) is not None and constraint.__class__.__name__ == "UniqueConstraint"
    }

    assert unique_constraints[("company_id", "external_user_id")] == "uq_telegram_contacts_company_external_user"
    assert TelegramContact.__table__.c.external_chat_id.unique is True


def test_fresh_schema_remains_idempotent_for_existing_invariant(monkeypatch):
    engine = create_engine("sqlite:///:memory:", future=True)
    from api.app.db import Base
    Base.metadata.create_all(engine)
    monkeypatch.setattr(schema_evolution, "engine", engine)

    schema_evolution.ensure_telegram_contact_company_user_unique()

    indexes = _unique_indexes(engine)
    assert any(columns == ("company_id", "external_user_id") for columns in indexes.values())
    assert any(columns == ("external_chat_id",) for columns in indexes.values())


def test_ticket_attachment_preload_table_creation_is_idempotent(monkeypatch):
    engine = create_engine("sqlite:///:memory:", future=True)
    monkeypatch.setattr(schema_evolution, "engine", engine)

    schema_evolution.ensure_ticket_attachment_preload_table()
    columns_after_first_run = _preload_columns(engine)
    indexes_after_first_run = _preload_indexes(engine)

    schema_evolution.ensure_ticket_attachment_preload_table()
    columns_after_second_run = _preload_columns(engine)
    indexes_after_second_run = _preload_indexes(engine)

    expected_columns = {
        "id",
        "ticket_id",
        "user_id",
        "original_filename",
        "mime_type",
        "file_size",
        "local_path",
        "telegram_file_id",
        "telegram_file_unique_id",
        "archive_group_id",
        "status",
        "final_attachment_id",
        "reply_to_message_id",
        "prefer_caption",
        "telegram_staging_info_message_id",
        "telegram_staging_file_message_id",
        "created_at",
    }

    assert set(columns_after_first_run) == expected_columns
    assert columns_after_second_run == columns_after_first_run
    assert indexes_after_first_run["ix_ticket_attachment_preloads_ticket_user"] == ("ticket_id", "user_id")
    assert indexes_after_second_run == indexes_after_first_run


def test_ticket_attachment_preload_model_metadata_matches_expected_columns():
    actual_columns = set(TicketAttachmentPreload.__table__.columns.keys())

    assert actual_columns == {
        "id",
        "ticket_id",
        "user_id",
        "original_filename",
        "mime_type",
        "file_size",
        "local_path",
        "telegram_file_id",
        "telegram_file_unique_id",
        "archive_group_id",
        "status",
        "final_attachment_id",
        "reply_to_message_id",
        "prefer_caption",
        "telegram_staging_info_message_id",
        "telegram_staging_file_message_id",
        "created_at",
    }


def test_ticket_attachment_table_creation_is_idempotent(monkeypatch):
    engine = create_engine("sqlite:///:memory:", future=True)
    monkeypatch.setattr(schema_evolution, "engine", engine)

    schema_evolution.ensure_ticket_attachment_table()
    schema_evolution.ensure_ticket_attachment_table()

    with engine.connect() as conn:
        columns = {
            row["name"]
            for row in conn.execute(text("PRAGMA table_info(ticket_attachments)")).mappings().all()
        }

    assert "telegram_archive_message_id" in columns
    assert "telegram_archive_info_message_id" in columns
    assert "telegram_media_group_id" in columns


def test_ticket_attachment_model_metadata_matches_expected_columns():
    actual_columns = set(TicketAttachment.__table__.columns.keys())

    assert actual_columns == {
        "id",
        "ticket_id",
        "message_id",
        "source",
        "telegram_file_id",
        "telegram_file_unique_id",
        "telegram_file_path",
        "telegram_delivery_status",
        "telegram_message_id",
        "telegram_archive_message_id",
        "telegram_archive_info_message_id",
        "telegram_media_group_id",
        "original_filename",
        "mime_type",
        "file_size",
        "local_path",
        "created_at",
    }


def test_user_send_by_enter_column_is_added_idempotently_for_existing_schema(monkeypatch):
    engine = create_engine("sqlite:///:memory:", future=True)
    _create_legacy_users_table(engine)
    monkeypatch.setattr(schema_evolution, "engine", engine)

    schema_evolution.ensure_user_columns()
    schema_evolution.ensure_user_columns()

    with engine.connect() as conn:
        columns = {
            row["name"]: row
            for row in conn.execute(text("PRAGMA table_info(users)")).mappings().all()
        }
        send_by_enter = conn.execute(text("SELECT send_by_enter FROM users WHERE id = 'user-1'")).scalar_one()

    assert "send_by_enter" in columns
    assert columns["send_by_enter"]["notnull"] == 1
    assert send_by_enter in (0, False)


def test_user_avatar_path_column_is_added_idempotently_and_keeps_rows(monkeypatch):
    engine = create_engine("sqlite:///:memory:", future=True)
    _create_legacy_users_table(engine)
    monkeypatch.setattr(schema_evolution, "engine", engine)

    schema_evolution.ensure_user_columns()
    schema_evolution.ensure_user_columns()

    with engine.connect() as conn:
        columns = {
            row["name"]: row
            for row in conn.execute(text("PRAGMA table_info(users)")).mappings().all()
        }
        row = conn.execute(text("SELECT id, full_name, avatar_path FROM users WHERE id = 'user-1'")).mappings().one()

    assert "avatar_path" in columns
    assert columns["avatar_path"]["notnull"] == 0
    assert row["id"] == "user-1"
    assert row["full_name"] == "User One"
    assert row["avatar_path"] is None


def test_user_model_metadata_contains_send_by_enter_column():
    assert "send_by_enter" in User.__table__.columns.keys()


def test_user_model_metadata_contains_avatar_path_column():
    assert "avatar_path" in User.__table__.columns.keys()


def test_ticket_columns_schema_evolution_does_not_backfill_completion_from_closed_by(monkeypatch):
    engine = create_engine("sqlite:///:memory:", future=True)
    monkeypatch.setattr(schema_evolution, "engine", engine)
    with engine.begin() as conn:
        conn.execute(
            text(
                """
                CREATE TABLE tickets (
                    id VARCHAR(36) PRIMARY KEY,
                    assignee_id VARCHAR(36),
                    closed_by_user_id VARCHAR(36),
                    status VARCHAR(20),
                    subject VARCHAR(255),
                    created_at TIMESTAMP,
                    updated_at TIMESTAMP,
                    closed_at TIMESTAMP
                )
                """
            )
        )
        conn.execute(
            text(
                """
                INSERT INTO tickets (
                    id, assignee_id, closed_by_user_id, status, subject, created_at, updated_at, closed_at
                ) VALUES (
                    'ticket-1', 'agent-a', 'former-admin', 'closed', 'Closed ticket',
                    '2026-01-01 10:00:00', '2026-01-02 10:00:00', '2026-01-02 10:00:00'
                )
                """
            )
        )

    schema_evolution.ensure_ticket_columns()
    schema_evolution.ensure_ticket_columns()

    with engine.connect() as conn:
        row = conn.execute(
            text(
                """
                SELECT completed_by_user_id, completed_counted_at
                FROM tickets
                WHERE id = 'ticket-1'
                """
            )
        ).mappings().one()

    assert row["completed_by_user_id"] is None
    assert row["completed_counted_at"] is None


def test_telegram_notification_accounts_table_creation_is_idempotent(monkeypatch):
    engine = create_engine("sqlite:///:memory:", future=True)
    _create_minimal_users_table(engine)
    monkeypatch.setattr(schema_evolution, "engine", engine)

    schema_evolution.ensure_telegram_notification_accounts_table()
    columns_after_first_run = _telegram_notification_columns(engine)
    indexes_after_first_run = _telegram_notification_indexes(engine)
    foreign_keys_after_first_run = _telegram_notification_foreign_keys(engine)

    schema_evolution.ensure_telegram_notification_accounts_table()
    columns_after_second_run = _telegram_notification_columns(engine)
    indexes_after_second_run = _telegram_notification_indexes(engine)
    foreign_keys_after_second_run = _telegram_notification_foreign_keys(engine)

    assert columns_after_second_run == columns_after_first_run
    assert indexes_after_second_run == indexes_after_first_run
    assert foreign_keys_after_second_run == foreign_keys_after_first_run
    assert columns_after_second_run["id"]["type"] == "VARCHAR(36)"
    assert columns_after_second_run["id"]["notnull"] == 1
    assert columns_after_second_run["id"]["pk"] == 1
    assert columns_after_second_run["telegram_user_id"]["type"] == "VARCHAR(100)"
    assert columns_after_second_run["telegram_user_id"]["notnull"] == 1
    assert columns_after_second_run["request_status"]["notnull"] == 1
    assert columns_after_second_run["request_status"]["default"] == "'pending'"
    assert columns_after_second_run["delivery_available"]["notnull"] == 1
    assert columns_after_second_run["delivery_available"]["default"] in ("TRUE", "1")
    assert columns_after_second_run["is_blocked"]["notnull"] == 1
    assert columns_after_second_run["is_blocked"]["default"] in ("FALSE", "0")
    assert columns_after_second_run["is_notification_bot_admin"]["notnull"] == 1
    assert columns_after_second_run["is_notification_bot_admin"]["default"] in ("FALSE", "0")
    assert columns_after_second_run["created_at"]["notnull"] == 1
    assert columns_after_second_run["updated_at"]["notnull"] == 1
    assert any(columns == ("telegram_user_id",) for columns in indexes_after_second_run.values())
    assert any(columns == ("bound_user_id",) for columns in indexes_after_second_run.values())
    assert len(foreign_keys_after_second_run) == 1
    assert foreign_keys_after_second_run[0]["from"] == "bound_user_id"
    assert foreign_keys_after_second_run[0]["table"] == "users"
    assert foreign_keys_after_second_run[0]["to"] == "id"


def test_telegram_notification_account_unique_telegram_user_id(monkeypatch):
    engine = create_engine("sqlite:///:memory:", future=True)
    _create_minimal_users_table(engine)
    monkeypatch.setattr(schema_evolution, "engine", engine)
    schema_evolution.ensure_telegram_notification_accounts_table()

    with engine.begin() as conn:
        conn.execute(
            text(
                """
                INSERT INTO telegram_notification_accounts (
                    id, telegram_user_id, request_status, delivery_available, created_at, updated_at
                ) VALUES (
                    'acc-1', 'tg-1', 'pending', TRUE, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP
                )
                """
            )
        )

    with pytest.raises(IntegrityError):
        with engine.begin() as conn:
            conn.execute(
                text(
                    """
                    INSERT INTO telegram_notification_accounts (
                        id, telegram_user_id, request_status, delivery_available, created_at, updated_at
                    ) VALUES (
                        'acc-2', 'tg-1', 'pending', TRUE, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP
                    )
                    """
                )
            )


def test_telegram_notification_account_bound_user_id_is_one_to_one_when_not_null(monkeypatch):
    engine = create_engine("sqlite:///:memory:", future=True)
    _create_minimal_users_table(engine)
    monkeypatch.setattr(schema_evolution, "engine", engine)
    schema_evolution.ensure_telegram_notification_accounts_table()

    with engine.begin() as conn:
        conn.execute(
            text(
                """
                INSERT INTO telegram_notification_accounts (
                    id, telegram_user_id, bound_user_id, request_status, delivery_available, created_at, updated_at
                ) VALUES (
                    'acc-1', 'tg-1', 'user-1', 'pending', TRUE, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP
                )
                """
            )
        )

    with pytest.raises(IntegrityError):
        with engine.begin() as conn:
            conn.execute(
                text(
                    """
                    INSERT INTO telegram_notification_accounts (
                        id, telegram_user_id, bound_user_id, request_status, delivery_available, created_at, updated_at
                    ) VALUES (
                        'acc-2', 'tg-2', 'user-1', 'pending', TRUE, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP
                    )
                    """
                )
            )

    with engine.begin() as conn:
        conn.execute(
            text(
                """
                INSERT INTO telegram_notification_accounts (
                    id, telegram_user_id, bound_user_id, request_status, delivery_available, created_at, updated_at
                ) VALUES (
                    'acc-3', 'tg-3', NULL, 'pending', TRUE, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP
                )
                """
            )
        )
        conn.execute(
            text(
                """
                INSERT INTO telegram_notification_accounts (
                    id, telegram_user_id, bound_user_id, request_status, delivery_available, created_at, updated_at
                ) VALUES (
                    'acc-4', 'tg-4', NULL, 'pending', TRUE, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP
                )
                """
            )
        )

    with engine.connect() as conn:
        null_bound_count = conn.execute(
            text(
                """
                SELECT COUNT(*)
                FROM telegram_notification_accounts
                WHERE bound_user_id IS NULL
                """
            )
        ).scalar_one()

    assert null_bound_count == 2


def test_telegram_notification_account_model_metadata_matches_expected_columns():
    assert set(TelegramNotificationAccount.__table__.columns.keys()) == {
        "id",
        "telegram_user_id",
        "telegram_chat_id",
        "telegram_username",
        "telegram_display_name",
        "bound_user_id",
        "request_status",
        "delivery_available",
        "is_blocked",
        "is_notification_bot_admin",
        "created_at",
        "updated_at",
    }


@pytest.mark.parametrize(
    ("row", "expected"),
    [
        (
            {
                "is_unique": True,
                "predicate_sql": None,
                "expression_sql": None,
                "key_arity": 2,
                "key_columns": ["company_id", "external_user_id"],
            },
            True,
        ),
        (
            {
                "is_unique": True,
                "predicate_sql": None,
                "expression_sql": None,
                "key_arity": 2,
                "key_columns": ["company_id", "external_user_id"],
                "constraint_name": "some_constraint",
            },
            True,
        ),
        (
            {
                "is_unique": False,
                "predicate_sql": None,
                "expression_sql": None,
                "key_arity": 2,
                "key_columns": ["company_id", "external_user_id"],
            },
            False,
        ),
        (
            {
                "is_unique": True,
                "predicate_sql": "(company_id IS NOT NULL)",
                "expression_sql": None,
                "key_arity": 2,
                "key_columns": ["company_id", "external_user_id"],
            },
            False,
        ),
        (
            {
                "is_unique": True,
                "predicate_sql": None,
                "expression_sql": "(lower((external_user_id)::text))",
                "key_arity": 2,
                "key_columns": ["company_id", None],
            },
            False,
        ),
        (
            {
                "is_unique": True,
                "predicate_sql": None,
                "expression_sql": None,
                "key_arity": 3,
                "key_columns": ["company_id", "external_user_id", "client_id"],
            },
            False,
        ),
    ],
)
def test_postgres_equivalent_unique_invariant_detection(row, expected):
    result = schema_evolution._postgres_is_equivalent_unique_invariant(
        row,
        ("company_id", "external_user_id"),
    )

    assert result is expected
