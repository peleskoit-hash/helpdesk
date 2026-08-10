from __future__ import annotations

import uuid
from datetime import datetime

import pytest
from sqlalchemy import create_engine, text

from api.app.db import Base
from api.app.models import TelegramContact
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
    Base.metadata.create_all(engine)
    monkeypatch.setattr(schema_evolution, "engine", engine)

    schema_evolution.ensure_telegram_contact_company_user_unique()

    indexes = _unique_indexes(engine)
    assert any(columns == ("company_id", "external_user_id") for columns in indexes.values())
    assert any(columns == ("external_chat_id",) for columns in indexes.values())


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
