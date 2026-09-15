from __future__ import annotations

from sqlalchemy import create_engine, inspect, text
from sqlalchemy.exc import IntegrityError

from api.app.models import InternalTaskDefinition, InternalTaskOccurrence
from api.app.services import schema_evolution


DEFINITION_COLUMNS = {
    "id",
    "company_id",
    "title",
    "description",
    "creator_admin_id",
    "responsible_admin_id",
    "display_author",
    "assignment_mode",
    "assignee_user_id",
    "notify_on_completion",
    "deadline_at_utc",
    "schedule_kind",
    "timezone_name",
    "scheduled_for_utc",
    "recurrence_mode",
    "recurrence_config",
    "cycle_start_at_utc",
    "cycle_end_mode",
    "cycle_end_at_utc",
    "cycle_max_occurrences",
    "state",
    "generation_not_before_utc",
    "created_at",
    "updated_at",
}

OCCURRENCE_COLUMNS = {
    "id",
    "definition_id",
    "occurrence_at_utc",
    "ticket_id",
    "title_snapshot",
    "description_snapshot",
    "creator_admin_id",
    "responsible_admin_id",
    "display_author",
    "effective_assignment_mode",
    "effective_assignee_user_id",
    "notify_on_completion",
    "deadline_at_utc",
    "materialized_at_utc",
    "created_at",
}


def _index_columns(engine, table_name: str) -> dict[str, tuple[bool, tuple[str, ...]]]:
    with engine.connect() as conn:
        result = {}
        for row in conn.execute(text(f"PRAGMA index_list({table_name})")).mappings().all():
            name = str(row["name"])
            result[name] = (
                bool(row["unique"]),
                tuple(
                    column["name"]
                    for column in conn.execute(text(f"PRAGMA index_info({name})")).mappings().all()
                ),
            )
        return result


def _insert_definition(conn, *, definition_id: str = "def-1", **overrides):
    values = {
        "id": definition_id,
        "company_id": "company-1",
        "title": "Internal task",
        "description": "Do the internal thing",
        "creator_admin_id": "admin-1",
        "responsible_admin_id": "admin-2",
        "display_author": True,
        "assignment_mode": "direct",
        "assignee_user_id": "agent-1",
        "notify_on_completion": True,
        "schedule_kind": "recurring",
        "timezone_name": "Europe/Moscow",
        "recurrence_mode": "daily",
        "cycle_end_mode": "count",
        "cycle_max_occurrences": 3,
        "state": "active",
        "created_at": "2026-09-15 10:00:00",
        "updated_at": "2026-09-15 10:00:00",
    }
    values.update(overrides)
    conn.execute(
        text(
            """
            INSERT INTO internal_task_definitions (
                id,
                company_id,
                title,
                description,
                creator_admin_id,
                responsible_admin_id,
                display_author,
                assignment_mode,
                assignee_user_id,
                notify_on_completion,
                schedule_kind,
                timezone_name,
                recurrence_mode,
                cycle_end_mode,
                cycle_max_occurrences,
                state,
                created_at,
                updated_at
            )
            VALUES (
                :id,
                :company_id,
                :title,
                :description,
                :creator_admin_id,
                :responsible_admin_id,
                :display_author,
                :assignment_mode,
                :assignee_user_id,
                :notify_on_completion,
                :schedule_kind,
                :timezone_name,
                :recurrence_mode,
                :cycle_end_mode,
                :cycle_max_occurrences,
                :state,
                :created_at,
                :updated_at
            )
            """
        ),
        values,
    )


def _insert_occurrence(conn, *, occurrence_id: str = "occ-1", ticket_id: str = "ticket-1", **overrides):
    values = {
        "id": occurrence_id,
        "definition_id": "def-1",
        "occurrence_at_utc": "2026-09-16 08:00:00",
        "ticket_id": ticket_id,
        "title_snapshot": "Internal task",
        "description_snapshot": "Do the internal thing",
        "creator_admin_id": "admin-1",
        "responsible_admin_id": "admin-2",
        "display_author": True,
        "effective_assignment_mode": "direct",
        "effective_assignee_user_id": "agent-1",
        "notify_on_completion": True,
        "materialized_at_utc": "2026-09-16 08:00:01",
        "created_at": "2026-09-16 08:00:01",
    }
    values.update(overrides)
    conn.execute(
        text(
            """
            INSERT INTO internal_task_occurrences (
                id,
                definition_id,
                occurrence_at_utc,
                ticket_id,
                title_snapshot,
                description_snapshot,
                creator_admin_id,
                responsible_admin_id,
                display_author,
                effective_assignment_mode,
                effective_assignee_user_id,
                notify_on_completion,
                materialized_at_utc,
                created_at
            )
            VALUES (
                :id,
                :definition_id,
                :occurrence_at_utc,
                :ticket_id,
                :title_snapshot,
                :description_snapshot,
                :creator_admin_id,
                :responsible_admin_id,
                :display_author,
                :effective_assignment_mode,
                :effective_assignee_user_id,
                :notify_on_completion,
                :materialized_at_utc,
                :created_at
            )
            """
        ),
        values,
    )


def test_internal_task_schema_evolution_creates_tables_columns_and_indexes(monkeypatch):
    engine = create_engine("sqlite:///:memory:", future=True)
    monkeypatch.setattr(schema_evolution, "engine", engine)

    schema_evolution.ensure_internal_task_tables()
    schema_evolution.ensure_internal_task_tables()

    inspector = inspect(engine)
    assert {"internal_task_definitions", "internal_task_occurrences"} <= set(inspector.get_table_names())
    assert set(InternalTaskDefinition.__table__.columns.keys()) == set(
        column["name"] for column in inspector.get_columns("internal_task_definitions")
    )
    assert set(InternalTaskOccurrence.__table__.columns.keys()) == set(
        column["name"] for column in inspector.get_columns("internal_task_occurrences")
    )
    assert set(InternalTaskDefinition.__table__.columns.keys()) == DEFINITION_COLUMNS
    assert set(InternalTaskOccurrence.__table__.columns.keys()) == OCCURRENCE_COLUMNS

    definition_indexes = _index_columns(engine, "internal_task_definitions")
    occurrence_indexes = _index_columns(engine, "internal_task_occurrences")
    assert definition_indexes["ix_internal_task_definitions_company_schedule_state"] == (
        False,
        ("company_id", "schedule_kind", "state"),
    )
    assert definition_indexes["ix_internal_task_definitions_due_once"] == (
        False,
        ("schedule_kind", "state", "scheduled_for_utc"),
    )
    assert definition_indexes["ix_internal_task_definitions_active_recurring"] == (
        False,
        ("schedule_kind", "state", "generation_not_before_utc"),
    )
    assert definition_indexes["ix_internal_task_definitions_creator"] == (
        False,
        ("company_id", "creator_admin_id"),
    )
    assert definition_indexes["ix_internal_task_definitions_responsible"] == (
        False,
        ("company_id", "responsible_admin_id"),
    )
    assert occurrence_indexes["ux_internal_task_occurrences_ticket_id"] == (True, ("ticket_id",))
    assert "ix_internal_task_occurrences_definition_time" not in occurrence_indexes

    unique_constraints = inspector.get_unique_constraints("internal_task_occurrences")
    assert any(
        tuple(constraint["column_names"]) == ("definition_id", "occurrence_at_utc")
        for constraint in unique_constraints
    )


def test_internal_task_occurrence_unique_definition_time_and_ticket_id(monkeypatch):
    engine = create_engine("sqlite:///:memory:", future=True)
    monkeypatch.setattr(schema_evolution, "engine", engine)
    schema_evolution.ensure_internal_task_tables()

    with engine.begin() as conn:
        _insert_definition(conn)
        _insert_occurrence(conn)

        try:
            _insert_occurrence(conn, occurrence_id="occ-duplicate-time", ticket_id="ticket-2")
        except IntegrityError:
            pass
        else:
            raise AssertionError("definition_id + occurrence_at_utc must be unique")

        try:
            _insert_occurrence(
                conn,
                occurrence_id="occ-duplicate-ticket",
                occurrence_at_utc="2026-09-17 08:00:00",
            )
        except IntegrityError:
            pass
        else:
            raise AssertionError("ticket_id must be unique")


def test_internal_task_schema_check_constraints_reject_invalid_values(monkeypatch):
    engine = create_engine("sqlite:///:memory:", future=True)
    monkeypatch.setattr(schema_evolution, "engine", engine)
    schema_evolution.ensure_internal_task_tables()

    invalid_definition_values = [
        {"assignment_mode": "unknown"},
        {"schedule_kind": "sometimes"},
        {"recurrence_mode": "yearly"},
        {"cycle_end_mode": "after_success"},
        {"state": "broken"},
        {"cycle_max_occurrences": 0},
    ]
    for index, overrides in enumerate(invalid_definition_values):
        with engine.begin() as conn:
            try:
                _insert_definition(conn, definition_id=f"bad-def-{index}", **overrides)
            except IntegrityError:
                continue
            raise AssertionError(f"definition CHECK constraint accepted {overrides}")

    with engine.begin() as conn:
        try:
            _insert_occurrence(conn, effective_assignment_mode="unknown")
        except IntegrityError:
            pass
        else:
            raise AssertionError("occurrence assignment mode CHECK constraint accepted an invalid value")
