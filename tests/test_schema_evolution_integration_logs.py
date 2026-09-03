from sqlalchemy import create_engine, text

from api.app.db import Base
from api.app.services import schema_evolution


EXPECTED_COLUMNS = {
    "id",
    "created_at",
    "source",
    "event_type",
    "result",
    "code",
    "description",
    "deal_id",
    "task_id",
    "bitrix_id",
    "call_time",
    "message_text",
    "telegram_message_id",
}


def _integration_logs_contract(engine):
    with engine.connect() as conn:
        columns = {
            row["name"]: row
            for row in conn.execute(text("PRAGMA table_info(integration_logs)")).mappings().all()
        }
        foreign_keys = conn.execute(text("PRAGMA foreign_key_list(integration_logs)")).mappings().all()
        indexes = {}
        for row in conn.execute(text("PRAGMA index_list(integration_logs)")).mappings().all():
            name = row["name"]
            if str(name).startswith("sqlite_autoindex_"):
                continue
            indexes[name] = tuple(
                column["name"]
                for column in conn.execute(text(f"PRAGMA index_info({name})")).mappings().all()
            )
    return columns, foreign_keys, indexes


def test_ensure_integration_logs_table_is_idempotent_and_matches_orm_contract(monkeypatch):
    metadata_engine = create_engine("sqlite:///:memory:", future=True)
    Base.metadata.create_all(metadata_engine)
    metadata_columns, metadata_foreign_keys, metadata_indexes = _integration_logs_contract(metadata_engine)

    engine = create_engine("sqlite:///:memory:", future=True)
    monkeypatch.setattr(schema_evolution, "engine", engine)

    schema_evolution.ensure_integration_logs_table()
    schema_evolution.ensure_integration_logs_table()
    columns, foreign_keys, indexes = _integration_logs_contract(engine)

    assert set(columns) == EXPECTED_COLUMNS
    assert set(metadata_columns) == EXPECTED_COLUMNS
    assert foreign_keys == []
    assert metadata_foreign_keys == []
    assert indexes == {
        "ix_integration_logs_created_at": ("created_at",),
        "ix_integration_logs_task_dedup": ("source", "event_type", "result", "deal_id", "created_at"),
    }
    assert metadata_indexes == indexes
