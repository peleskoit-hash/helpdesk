from __future__ import annotations

from sqlalchemy import create_engine, inspect, text

from api.app.services import schema_evolution


def test_employee_notification_preferences_schema_evolution_preserves_existing_users(monkeypatch):
    engine = create_engine("sqlite:///:memory:", future=True)
    monkeypatch.setattr(schema_evolution, "engine", engine)
    with engine.begin() as conn:
        conn.execute(
            text(
                """
                CREATE TABLE users (
                    id VARCHAR(36) PRIMARY KEY,
                    company_id VARCHAR(36) NOT NULL,
                    email VARCHAR(255),
                    login VARCHAR(100),
                    password_hash VARCHAR(255),
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
                INSERT INTO users (id, company_id, email, login, password_hash, full_name, role, is_active)
                VALUES ('u1', 'c1', NULL, 'u1', 'hash', 'User One', 'agent', TRUE)
                """
            )
        )

    schema_evolution.ensure_employee_notification_preferences_table()
    schema_evolution.ensure_employee_notification_preferences_table()

    inspector = inspect(engine)
    assert "employee_notification_preferences" in inspector.get_table_names()
    inspected_columns = inspector.get_columns("employee_notification_preferences")
    columns = {column["name"] for column in inspected_columns}
    assert {
        "user_id",
        "use_base_new_assignment_settings",
        "use_base_new_message_settings",
        "personal_new_assignment_template",
        "personal_new_message_template",
        "receive_transfer_to_agent",
        "receive_transfer_from_agent",
        "receive_new_message",
        "notify_internal_task_completion",
    }.issubset(columns)
    internal_task_completion_column = next(
        column for column in inspected_columns if column["name"] == "notify_internal_task_completion"
    )
    assert internal_task_completion_column["nullable"] is False
    assert str(internal_task_completion_column["default"]).upper() in {"TRUE", "1"}
    with engine.connect() as conn:
        existing_user = conn.execute(text("SELECT full_name FROM users WHERE id = 'u1'")).scalar_one()
        conn.execute(
            text(
                """
                INSERT INTO employee_notification_preferences (
                    id,
                    user_id,
                    use_base_new_assignment_settings,
                    use_base_new_message_settings,
                    receive_transfer_to_agent,
                    receive_transfer_from_agent,
                    receive_new_message
                )
                VALUES ('pref-1', 'u1', TRUE, TRUE, TRUE, TRUE, TRUE)
                """
            )
        )
        pref_count = conn.execute(text("SELECT COUNT(*) FROM employee_notification_preferences")).scalar_one()
        internal_task_completion = conn.execute(
            text("SELECT notify_internal_task_completion FROM employee_notification_preferences WHERE id = 'pref-1'")
        ).scalar_one()

    assert existing_user == "User One"
    assert pref_count == 1
    assert bool(internal_task_completion) is True


def test_employee_notification_preferences_schema_evolution_migrates_old_global_inheritance(monkeypatch):
    engine = create_engine("sqlite:///:memory:", future=True)
    monkeypatch.setattr(schema_evolution, "engine", engine)
    with engine.begin() as conn:
        conn.execute(
            text(
                """
                CREATE TABLE employee_notification_preferences (
                    id VARCHAR(36) PRIMARY KEY,
                    user_id VARCHAR(36) UNIQUE NOT NULL,
                    use_base_settings BOOLEAN,
                    personal_new_assignment_template TEXT,
                    personal_new_message_template TEXT,
                    receive_transfer_to_agent BOOLEAN,
                    receive_transfer_from_agent BOOLEAN,
                    receive_new_message BOOLEAN
                )
                """
            )
        )
        conn.execute(
            text(
                """
                INSERT INTO employee_notification_preferences (
                    id,
                    user_id,
                    use_base_settings,
                    personal_new_assignment_template,
                    personal_new_message_template,
                    receive_transfer_to_agent,
                    receive_transfer_from_agent,
                    receive_new_message
                )
                VALUES
                    ('pref-true', 'u-true', TRUE, 'A true', 'M true', TRUE, TRUE, TRUE),
                    ('pref-false', 'u-false', FALSE, 'A false', 'M false', FALSE, FALSE, FALSE)
                """
            )
        )

    schema_evolution.ensure_employee_notification_preferences_table()
    schema_evolution.ensure_employee_notification_preferences_table()

    with engine.connect() as conn:
        rows = conn.execute(
            text(
                """
                SELECT
                    user_id,
                    use_base_new_assignment_settings,
                    use_base_new_message_settings,
                    personal_new_assignment_template,
                    personal_new_message_template,
                    notify_internal_task_completion
                FROM employee_notification_preferences
                ORDER BY user_id
                """
            )
        ).mappings().all()

    assert rows == [
        {
            "user_id": "u-false",
            "use_base_new_assignment_settings": False,
            "use_base_new_message_settings": False,
            "personal_new_assignment_template": "A false",
            "personal_new_message_template": "M false",
            "notify_internal_task_completion": True,
        },
        {
            "user_id": "u-true",
            "use_base_new_assignment_settings": True,
            "use_base_new_message_settings": True,
            "personal_new_assignment_template": "A true",
            "personal_new_message_template": "M true",
            "notify_internal_task_completion": True,
        },
    ]
