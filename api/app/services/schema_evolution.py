from __future__ import annotations

from sqlalchemy import text

from ..db import engine


def ensure_client_profile_columns() -> None:
    required_columns = {
        "last_name": "VARCHAR(200)",
        "is_verified": "BOOLEAN",
        "destination": "VARCHAR(200)",
        "sand_owner": "VARCHAR(200)",
        "sales_group": "VARCHAR(200)",
        "location_type": "VARCHAR(100)",
        "anydesk": "VARCHAR(100)",
        "bitrix_password": "VARCHAR(255)",
        "bitrix_id": "VARCHAR(100)",
        "custom_fields": "JSONB",
    }

    with engine.begin() as conn:
        dialect = conn.dialect.name
        existing: set[str] = set()

        if dialect == "sqlite":
            rows = conn.execute(text("PRAGMA table_info(client_profiles)")).mappings().all()
            existing = {row["name"] for row in rows}
        else:
            rows = conn.execute(
                text(
                    """
                    SELECT column_name
                    FROM information_schema.columns
                    WHERE table_name = 'client_profiles'
                    """
                )
            ).fetchall()
            existing = {row[0] for row in rows}

        if dialect == "sqlite":
            required_columns["custom_fields"] = "TEXT"
        for column_name, column_type in required_columns.items():
            if column_name in existing:
                continue
            conn.execute(text(f"ALTER TABLE client_profiles ADD COLUMN {column_name} {column_type}"))
        conn.execute(text("UPDATE client_profiles SET is_verified = FALSE WHERE is_verified IS NULL"))
        if dialect == "sqlite":
            conn.execute(text("UPDATE client_profiles SET custom_fields = '{}' WHERE custom_fields IS NULL"))
        else:
            conn.execute(text("UPDATE client_profiles SET custom_fields = '{}'::jsonb WHERE custom_fields IS NULL"))


def ensure_client_card_field_config_columns() -> None:
    field_required_columns = {
        "is_required": "BOOLEAN",
        "is_visible_in_editor": "BOOLEAN",
        "options_json": "JSONB",
    }
    block_required_columns = {
        "show_in_card": "BOOLEAN",
        "show_in_editor": "BOOLEAN",
        "show_title": "BOOLEAN",
    }
    with engine.begin() as conn:
        dialect = conn.dialect.name
        if dialect == "sqlite":
            rows = conn.execute(text("PRAGMA table_info(client_card_field_configs)")).mappings().all()
            field_existing = {row["name"] for row in rows}
            rows = conn.execute(text("PRAGMA table_info(client_card_block_configs)")).mappings().all()
            block_existing = {row["name"] for row in rows}
            field_required_columns["options_json"] = "TEXT"
        else:
            rows = conn.execute(
                text(
                    """
                    SELECT column_name
                    FROM information_schema.columns
                    WHERE table_name = 'client_card_field_configs'
                    """
                )
            ).fetchall()
            field_existing = {row[0] for row in rows}
            rows = conn.execute(
                text(
                    """
                    SELECT column_name
                    FROM information_schema.columns
                    WHERE table_name = 'client_card_block_configs'
                    """
                )
            ).fetchall()
            block_existing = {row[0] for row in rows}
        for column_name, column_type in field_required_columns.items():
            if column_name not in field_existing:
                conn.execute(text(f"ALTER TABLE client_card_field_configs ADD COLUMN {column_name} {column_type}"))
        for column_name, column_type in block_required_columns.items():
            if column_name not in block_existing:
                conn.execute(text(f"ALTER TABLE client_card_block_configs ADD COLUMN {column_name} {column_type}"))
        conn.execute(text("UPDATE client_card_field_configs SET is_required = FALSE WHERE is_required IS NULL"))
        conn.execute(
            text(
                """
                UPDATE client_card_field_configs
                SET is_visible_in_editor = CASE WHEN is_system = FALSE THEN TRUE ELSE FALSE END
                WHERE is_visible_in_editor IS NULL
                """
            )
        )
        conn.execute(text("UPDATE client_card_block_configs SET show_in_card = TRUE WHERE show_in_card IS NULL"))
        conn.execute(text("UPDATE client_card_block_configs SET show_in_editor = TRUE WHERE show_in_editor IS NULL"))
        conn.execute(text("UPDATE client_card_block_configs SET show_title = TRUE WHERE show_title IS NULL"))
        if dialect == "sqlite":
            conn.execute(text("UPDATE client_card_field_configs SET options_json = NULL WHERE options_json = ''"))

        actions_type = "TEXT" if dialect == "sqlite" else "JSONB"
        conn.execute(
            text(
                f"""
                CREATE TABLE IF NOT EXISTS client_card_rule_configs (
                    id VARCHAR(36) PRIMARY KEY,
                    technical_key VARCHAR(100) UNIQUE,
                    name VARCHAR(200) NOT NULL,
                    is_active BOOLEAN,
                    is_system BOOLEAN,
                    sort_order INTEGER,
                    condition_field_key VARCHAR(100) NOT NULL,
                    condition_operator VARCHAR(40) NOT NULL,
                    condition_value VARCHAR(500),
                    actions_json {actions_type},
                    created_at TIMESTAMP,
                    updated_at TIMESTAMP
                )
                """
            )
        )
        if dialect == "sqlite":
            rows = conn.execute(text("PRAGMA table_info(client_card_rule_configs)")).mappings().all()
            rule_existing = {row["name"] for row in rows}
        else:
            rows = conn.execute(
                text(
                    """
                    SELECT column_name
                    FROM information_schema.columns
                    WHERE table_name = 'client_card_rule_configs'
                    """
                )
            ).fetchall()
            rule_existing = {row[0] for row in rows}
        rule_required_columns = {
            "technical_key": "VARCHAR(100)",
            "is_system": "BOOLEAN",
            "sort_order": "INTEGER",
            "condition_value": "VARCHAR(500)",
            "actions_json": actions_type,
        }
        for column_name, column_type in rule_required_columns.items():
            if column_name not in rule_existing:
                conn.execute(text(f"ALTER TABLE client_card_rule_configs ADD COLUMN {column_name} {column_type}"))
        conn.execute(
            text(
                "CREATE UNIQUE INDEX IF NOT EXISTS ux_client_card_rule_configs_technical_key "
                "ON client_card_rule_configs (technical_key)"
            )
        )
        conn.execute(text("UPDATE client_card_rule_configs SET is_active = TRUE WHERE is_active IS NULL"))
        conn.execute(text("UPDATE client_card_rule_configs SET is_system = FALSE WHERE is_system IS NULL"))
        conn.execute(text("UPDATE client_card_rule_configs SET sort_order = 1000 WHERE sort_order IS NULL"))

        source_json_type = "TEXT" if dialect == "sqlite" else "JSONB"
        conn.execute(
            text(
                f"""
                CREATE TABLE IF NOT EXISTS client_card_source_configs (
                    id VARCHAR(36) PRIMARY KEY,
                    technical_key VARCHAR(100) UNIQUE,
                    name VARCHAR(200) NOT NULL,
                    kind VARCHAR(40) NOT NULL,
                    is_active BOOLEAN,
                    method VARCHAR(10) NOT NULL,
                    url_template VARCHAR(2000) NOT NULL,
                    query_template_json {source_json_type},
                    headers_template_json {source_json_type},
                    body_template_json {source_json_type},
                    timeout_ms INTEGER,
                    mapping_json {source_json_type},
                    on_error VARCHAR(40),
                    created_at TIMESTAMP,
                    updated_at TIMESTAMP
                )
                """
            )
        )
        if dialect == "sqlite":
            source_rows = conn.execute(text("PRAGMA table_info(client_card_source_configs)")).mappings().all()
            source_existing = {row["name"] for row in source_rows}
        else:
            source_rows = conn.execute(
                text(
                    """
                    SELECT column_name
                    FROM information_schema.columns
                    WHERE table_name = 'client_card_source_configs'
                    """
                )
            ).fetchall()
            source_existing = {row[0] for row in source_rows}
        source_required_columns = {
            "technical_key": "VARCHAR(100)",
            "kind": "VARCHAR(40)",
            "is_active": "BOOLEAN",
            "method": "VARCHAR(10)",
            "url_template": "VARCHAR(2000)",
            "query_template_json": source_json_type,
            "headers_template_json": source_json_type,
            "body_template_json": source_json_type,
            "timeout_ms": "INTEGER",
            "mapping_json": source_json_type,
            "on_error": "VARCHAR(40)",
        }
        for column_name, column_type in source_required_columns.items():
            if column_name not in source_existing:
                conn.execute(text(f"ALTER TABLE client_card_source_configs ADD COLUMN {column_name} {column_type}"))
        conn.execute(
            text(
                "CREATE UNIQUE INDEX IF NOT EXISTS ux_client_card_source_configs_technical_key "
                "ON client_card_source_configs (technical_key)"
            )
        )
        conn.execute(text("UPDATE client_card_source_configs SET kind = 'http' WHERE kind IS NULL"))
        conn.execute(text("UPDATE client_card_source_configs SET is_active = TRUE WHERE is_active IS NULL"))
        conn.execute(text("UPDATE client_card_source_configs SET method = 'GET' WHERE method IS NULL"))
        conn.execute(text("UPDATE client_card_source_configs SET timeout_ms = 3000 WHERE timeout_ms IS NULL"))
        conn.execute(text("UPDATE client_card_source_configs SET on_error = 'ignore' WHERE on_error IS NULL"))


def ensure_call_center_employee_resolver_state_table() -> None:
    with engine.begin() as conn:
        dialect = conn.dialect.name
        json_type = "TEXT" if dialect == "sqlite" else "JSONB"
        conn.execute(
            text(
                f"""
                CREATE TABLE IF NOT EXISTS call_center_employee_resolver_state (
                    key VARCHAR(100) PRIMARY KEY,
                    snapshot_json {json_type},
                    metadata_json {json_type},
                    fetched_at TIMESTAMP,
                    last_attempt_at TIMESTAMP,
                    last_success_at TIMESTAMP,
                    updated_at TIMESTAMP
                )
                """
            )
        )


def ensure_telegram_contact_columns() -> None:
    required_columns = {
        "external_username": "VARCHAR(100)",
        "external_display_name": "VARCHAR(200)",
        "is_blocked": "BOOLEAN",
        "active_menu_message_id": "VARCHAR(100)",
        "menu_message_ids_json": "JSONB",
    }

    with engine.begin() as conn:
        dialect = conn.dialect.name
        existing: set[str] = set()

        if dialect == "sqlite":
            rows = conn.execute(text("PRAGMA table_info(telegram_contacts)")).mappings().all()
            existing = {row["name"] for row in rows}
            required_columns["menu_message_ids_json"] = "TEXT"
        else:
            rows = conn.execute(
                text(
                    """
                    SELECT column_name
                    FROM information_schema.columns
                    WHERE table_name = 'telegram_contacts'
                    """
                )
            ).fetchall()
            existing = {row[0] for row in rows}

        for column_name, column_type in required_columns.items():
            if column_name in existing:
                continue
            conn.execute(text(f"ALTER TABLE telegram_contacts ADD COLUMN {column_name} {column_type}"))
        conn.execute(text("UPDATE telegram_contacts SET is_blocked = FALSE WHERE is_blocked IS NULL"))
        if dialect == "sqlite":
            conn.execute(text("UPDATE telegram_contacts SET menu_message_ids_json = '[]' WHERE menu_message_ids_json IS NULL"))
        else:
            conn.execute(text("UPDATE telegram_contacts SET menu_message_ids_json = '[]'::jsonb WHERE menu_message_ids_json IS NULL"))
            conn.execute(text("ALTER TABLE telegram_contacts ALTER COLUMN is_blocked SET DEFAULT FALSE"))
            conn.execute(text("ALTER TABLE telegram_contacts ALTER COLUMN is_blocked SET NOT NULL"))


def ensure_user_columns() -> None:
    required_columns = {
        "availability_status": "VARCHAR(40)",
        "legacy_ticket_count": "INTEGER",
        "last_assigned_at": "TIMESTAMP",
        "login": "VARCHAR(100)",
        "password_hash": "VARCHAR(255)",
        "created_at": "TIMESTAMP",
        "updated_at": "TIMESTAMP",
    }

    with engine.begin() as conn:
        dialect = conn.dialect.name
        existing: set[str] = set()

        if dialect == "sqlite":
            rows = conn.execute(text("PRAGMA table_info(users)")).mappings().all()
            existing = {row["name"] for row in rows}
        else:
            rows = conn.execute(
                text(
                    """
                    SELECT column_name
                    FROM information_schema.columns
                    WHERE table_name = 'users'
                    """
                )
            ).fetchall()
            existing = {row[0] for row in rows}

        for column_name, column_type in required_columns.items():
            if column_name not in existing:
                conn.execute(text(f"ALTER TABLE users ADD COLUMN {column_name} {column_type}"))
        conn.execute(text("UPDATE users SET availability_status = 'online' WHERE availability_status IS NULL"))
        conn.execute(text("UPDATE users SET legacy_ticket_count = 0 WHERE legacy_ticket_count IS NULL"))
        conn.execute(text("UPDATE users SET login = email WHERE login IS NULL"))
        conn.execute(text("UPDATE users SET created_at = CURRENT_TIMESTAMP WHERE created_at IS NULL"))
        conn.execute(text("UPDATE users SET updated_at = CURRENT_TIMESTAMP WHERE updated_at IS NULL"))
        if dialect != "sqlite":
            conn.execute(text("ALTER TABLE users ALTER COLUMN email DROP NOT NULL"))
        if dialect != "sqlite":
            conn.execute(text("CREATE UNIQUE INDEX IF NOT EXISTS ix_users_login_unique ON users(login) WHERE login IS NOT NULL"))


def ensure_ticket_columns() -> None:
    required_columns = {
        "ticket_number": "INTEGER",
        "workflow_type": "VARCHAR(20)",
        "intake_state": "VARCHAR(20)",
        "client_close_message_id": "VARCHAR(100)",
        "menu_message_id": "VARCHAR(100)",
        "is_special": "BOOLEAN",
        "special_assignee_id": "VARCHAR(36)",
        "special_at": "TIMESTAMP",
        "auto_close_disabled": "BOOLEAN",
        "auto_close_started_at": "TIMESTAMP",
        "auto_close_due_at": "TIMESTAMP",
        "auto_closed_at": "TIMESTAMP",
        "task_kind": "VARCHAR(30)",
        "parent_ticket_id": "VARCHAR(36)",
        "agent_visible": "BOOLEAN",
        "load_counted": "BOOLEAN",
        "button_config_key": "VARCHAR(100)",
        "instruction_button_key": "VARCHAR(400)",
        "instruction_title": "VARCHAR(255)",
        "instruction_text": "TEXT",
        "wrong_problem_hint": "TEXT",
        "wrong_problem_used_at": "TIMESTAMP",
        "wrong_problem_button_key": "VARCHAR(100)",
        "wrong_problem_label": "VARCHAR(255)",
        "onsite_triggered": "BOOLEAN",
        "closed_by_user_id": "VARCHAR(36)",
        "completed_by_user_id": "VARCHAR(36)",
        "completed_counted_at": "TIMESTAMP",
        "call_center_employee_lookup_at": "TIMESTAMP",
        "call_center_internal_number": "VARCHAR(100)",
        "call_center_employee_first_name": "VARCHAR(200)",
        "call_center_employee_last_name": "VARCHAR(200)",
        "call_center_employee_id": "VARCHAR(100)",
        "call_center_employee_bitrix_login": "VARCHAR(255)",
        "call_center_profile_snapshot_json": "JSON",
    }

    with engine.begin() as conn:
        dialect = conn.dialect.name
        existing: set[str] = set()

        if dialect == "sqlite":
            rows = conn.execute(text("PRAGMA table_info(tickets)")).mappings().all()
            existing = {row["name"] for row in rows}
        else:
            rows = conn.execute(
                text(
                    """
                    SELECT column_name
                    FROM information_schema.columns
                    WHERE table_name = 'tickets'
                    """
                )
            ).fetchall()
            existing = {row[0] for row in rows}

        for column_name, column_type in required_columns.items():
            if column_name not in existing:
                conn.execute(text(f"ALTER TABLE tickets ADD COLUMN {column_name} {column_type}"))
        conn.execute(text("UPDATE tickets SET workflow_type = 'ticket' WHERE workflow_type IS NULL"))
        conn.execute(text("UPDATE tickets SET intake_state = 'ready' WHERE intake_state IS NULL"))
        conn.execute(text("UPDATE tickets SET task_kind = 'normal' WHERE task_kind IS NULL"))
        conn.execute(text("UPDATE tickets SET agent_visible = TRUE WHERE agent_visible IS NULL"))
        conn.execute(text("UPDATE tickets SET load_counted = TRUE WHERE load_counted IS NULL"))
        conn.execute(text("UPDATE tickets SET is_special = FALSE WHERE is_special IS NULL"))
        conn.execute(text("UPDATE tickets SET onsite_triggered = FALSE WHERE onsite_triggered IS NULL"))
        conn.execute(text("UPDATE tickets SET auto_close_disabled = FALSE WHERE auto_close_disabled IS NULL"))
        conn.execute(
            text(
                """
                UPDATE tickets
                SET completed_by_user_id = closed_by_user_id,
                    completed_counted_at = COALESCE(closed_at, updated_at, created_at)
                WHERE completed_by_user_id IS NULL
                  AND completed_counted_at IS NULL
                  AND status = 'closed'
                  AND closed_by_user_id IS NOT NULL
                  AND EXISTS (
                      SELECT 1
                      FROM users
                      WHERE users.id = tickets.closed_by_user_id
                        AND users.role IN ('agent', 'trainee')
                  )
                """
            )
        )
        if "ticket_number" not in existing:
            if dialect == "sqlite":
                conn.execute(
                    text(
                        """
                        WITH numbered AS (
                            SELECT id, ROW_NUMBER() OVER (ORDER BY created_at, id) AS rn
                            FROM tickets
                        )
                        UPDATE tickets
                        SET ticket_number = (SELECT rn FROM numbered WHERE numbered.id = tickets.id)
                        WHERE ticket_number IS NULL
                        """
                    )
                )
                conn.execute(text("CREATE UNIQUE INDEX IF NOT EXISTS ix_tickets_ticket_number_unique ON tickets(ticket_number)"))
            else:
                conn.execute(
                    text(
                        """
                        WITH numbered AS (
                            SELECT id, ROW_NUMBER() OVER (ORDER BY created_at, id) AS rn
                            FROM tickets
                        )
                        UPDATE tickets
                        SET ticket_number = numbered.rn
                        FROM numbered
                        WHERE tickets.id = numbered.id AND tickets.ticket_number IS NULL
                        """
                    )
                )
                conn.execute(text("CREATE UNIQUE INDEX IF NOT EXISTS ix_tickets_ticket_number_unique ON tickets(ticket_number) WHERE ticket_number IS NOT NULL"))


def ensure_bot_button_settings_table() -> None:
    with engine.begin() as conn:
        dialect = conn.dialect.name
        if dialect == "sqlite":
            conn.execute(
                text(
                    """
                    CREATE TABLE IF NOT EXISTS bot_button_settings (
                        id VARCHAR(36) PRIMARY KEY,
                        stable_button_id VARCHAR(36) UNIQUE,
                        button_key VARCHAR(500) UNIQUE NOT NULL,
                        legacy_button_key VARCHAR(500),
                        menu_type VARCHAR(200) NOT NULL,
                        workflow_type VARCHAR(50) NOT NULL,
                        button_path VARCHAR(1000) NOT NULL,
                        button_label VARCHAR(255) NOT NULL,
                        label_override VARCHAR(255),
                        label_overridden BOOLEAN,
                        currently_present BOOLEAN,
                        instruction_text TEXT,
                        client_text TEXT,
                        client_text_overridden BOOLEAN,
                        system_text TEXT,
                        system_text_overridden BOOLEAN,
                        fixed_assignee_enabled BOOLEAN,
                        fixed_assignee_user_id VARCHAR(36),
                        freeze_on_create BOOLEAN,
                        confirmation_enabled BOOLEAN,
                        confirmation_text TEXT,
                        confirmation_overridden BOOLEAN,
                        files_overridden BOOLEAN,
                        files_json TEXT,
                        conditional_files_overridden BOOLEAN,
                        conditional_files_json TEXT,
                        automation_key VARCHAR(120),
                        automation_overridden BOOLEAN,
                        is_enabled BOOLEAN,
                        visibility_overridden BOOLEAN,
                        is_db_created BOOLEAN,
                        button_kind VARCHAR(30),
                        parent_path VARCHAR(1000),
                        is_archived BOOLEAN,
                        deleted_at TIMESTAMP,
                        deleted_by_user_id VARCHAR(36),
                        created_at TIMESTAMP,
                        updated_at TIMESTAMP
                    )
                    """
                )
            )
            rows = conn.execute(text("PRAGMA table_info(bot_button_settings)")).mappings().all()
            existing = {row["name"] for row in rows}
            if "system_text" not in existing:
                conn.execute(text("ALTER TABLE bot_button_settings ADD COLUMN system_text TEXT"))
            if "stable_button_id" not in existing:
                conn.execute(text("ALTER TABLE bot_button_settings ADD COLUMN stable_button_id VARCHAR(36)"))
            if "legacy_button_key" not in existing:
                conn.execute(text("ALTER TABLE bot_button_settings ADD COLUMN legacy_button_key VARCHAR(500)"))
            if "currently_present" not in existing:
                conn.execute(text("ALTER TABLE bot_button_settings ADD COLUMN currently_present BOOLEAN"))
            if "label_override" not in existing:
                conn.execute(text("ALTER TABLE bot_button_settings ADD COLUMN label_override VARCHAR(255)"))
            if "label_overridden" not in existing:
                conn.execute(text("ALTER TABLE bot_button_settings ADD COLUMN label_overridden BOOLEAN"))
            if "client_text" not in existing:
                conn.execute(text("ALTER TABLE bot_button_settings ADD COLUMN client_text TEXT"))
            if "client_text_overridden" not in existing:
                conn.execute(text("ALTER TABLE bot_button_settings ADD COLUMN client_text_overridden BOOLEAN"))
            if "system_text_overridden" not in existing:
                conn.execute(text("ALTER TABLE bot_button_settings ADD COLUMN system_text_overridden BOOLEAN"))
            if "files_overridden" not in existing:
                conn.execute(text("ALTER TABLE bot_button_settings ADD COLUMN files_overridden BOOLEAN"))
            if "files_json" not in existing:
                conn.execute(text("ALTER TABLE bot_button_settings ADD COLUMN files_json TEXT"))
            if "conditional_files_overridden" not in existing:
                conn.execute(text("ALTER TABLE bot_button_settings ADD COLUMN conditional_files_overridden BOOLEAN"))
            if "conditional_files_json" not in existing:
                conn.execute(text("ALTER TABLE bot_button_settings ADD COLUMN conditional_files_json TEXT"))
            if "is_enabled" not in existing:
                conn.execute(text("ALTER TABLE bot_button_settings ADD COLUMN is_enabled BOOLEAN"))
            if "automation_key" not in existing:
                conn.execute(text("ALTER TABLE bot_button_settings ADD COLUMN automation_key VARCHAR(120)"))
            if "automation_overridden" not in existing:
                conn.execute(text("ALTER TABLE bot_button_settings ADD COLUMN automation_overridden BOOLEAN"))
            if "visibility_overridden" not in existing:
                conn.execute(text("ALTER TABLE bot_button_settings ADD COLUMN visibility_overridden BOOLEAN"))
            if "is_db_created" not in existing:
                conn.execute(text("ALTER TABLE bot_button_settings ADD COLUMN is_db_created BOOLEAN"))
            if "button_kind" not in existing:
                conn.execute(text("ALTER TABLE bot_button_settings ADD COLUMN button_kind VARCHAR(30)"))
            if "parent_path" not in existing:
                conn.execute(text("ALTER TABLE bot_button_settings ADD COLUMN parent_path VARCHAR(1000)"))
            if "is_archived" not in existing:
                conn.execute(text("ALTER TABLE bot_button_settings ADD COLUMN is_archived BOOLEAN"))
            if "deleted_at" not in existing:
                conn.execute(text("ALTER TABLE bot_button_settings ADD COLUMN deleted_at TIMESTAMP"))
            if "deleted_by_user_id" not in existing:
                conn.execute(text("ALTER TABLE bot_button_settings ADD COLUMN deleted_by_user_id VARCHAR(36)"))
            conn.execute(text("UPDATE bot_button_settings SET system_text_overridden = FALSE WHERE system_text_overridden IS NULL"))
            conn.execute(text("UPDATE bot_button_settings SET files_overridden = FALSE WHERE files_overridden IS NULL"))
            conn.execute(text("UPDATE bot_button_settings SET conditional_files_overridden = FALSE WHERE conditional_files_overridden IS NULL"))
            conn.execute(text("UPDATE bot_button_settings SET automation_overridden = FALSE WHERE automation_overridden IS NULL"))
            conn.execute(text("UPDATE bot_button_settings SET is_enabled = TRUE WHERE is_enabled IS NULL"))
            conn.execute(text("UPDATE bot_button_settings SET visibility_overridden = FALSE WHERE visibility_overridden IS NULL"))
            conn.execute(text("UPDATE bot_button_settings SET is_db_created = FALSE WHERE is_db_created IS NULL"))
            conn.execute(text("UPDATE bot_button_settings SET button_kind = 'terminal' WHERE button_kind IS NULL"))
            conn.execute(text("UPDATE bot_button_settings SET parent_path = '' WHERE parent_path IS NULL"))
            conn.execute(text("UPDATE bot_button_settings SET currently_present = TRUE WHERE currently_present IS NULL"))
            conn.execute(text("UPDATE bot_button_settings SET is_archived = FALSE WHERE is_archived IS NULL"))
            conn.execute(text("UPDATE bot_button_settings SET label_overridden = FALSE WHERE label_overridden IS NULL"))
            conn.execute(text("UPDATE bot_button_settings SET client_text_overridden = FALSE WHERE client_text_overridden IS NULL"))
            conn.execute(text("UPDATE bot_button_settings SET stable_button_id = id WHERE stable_button_id IS NULL"))
            return

        conn.execute(
            text(
                """
                CREATE TABLE IF NOT EXISTS bot_button_settings (
                    id VARCHAR(36) PRIMARY KEY,
                    stable_button_id VARCHAR(36) UNIQUE,
                    button_key VARCHAR(500) UNIQUE NOT NULL,
                    legacy_button_key VARCHAR(500),
                    menu_type VARCHAR(200) NOT NULL,
                    workflow_type VARCHAR(50) NOT NULL,
                    button_path VARCHAR(1000) NOT NULL,
                    button_label VARCHAR(255) NOT NULL,
                    label_override VARCHAR(255),
                    label_overridden BOOLEAN,
                    currently_present BOOLEAN,
                    instruction_text TEXT,
                    client_text TEXT,
                    client_text_overridden BOOLEAN,
                    system_text TEXT,
                    system_text_overridden BOOLEAN,
                    fixed_assignee_enabled BOOLEAN,
                    fixed_assignee_user_id VARCHAR(36),
                    freeze_on_create BOOLEAN,
                    confirmation_enabled BOOLEAN,
                    confirmation_text TEXT,
                    confirmation_overridden BOOLEAN,
                    files_overridden BOOLEAN,
                    files_json JSONB,
                    conditional_files_overridden BOOLEAN,
                    conditional_files_json JSONB,
                    automation_key VARCHAR(120),
                    automation_overridden BOOLEAN,
                    is_enabled BOOLEAN,
                    visibility_overridden BOOLEAN,
                    is_db_created BOOLEAN,
                    button_kind VARCHAR(30),
                    parent_path VARCHAR(1000),
                    is_archived BOOLEAN,
                    deleted_at TIMESTAMP,
                    deleted_by_user_id VARCHAR(36),
                    created_at TIMESTAMP,
                    updated_at TIMESTAMP
                )
                """
            )
        )
        rows = conn.execute(
            text(
                """
                SELECT column_name
                FROM information_schema.columns
                WHERE table_name = 'bot_button_settings'
                """
            )
        ).fetchall()
        existing = {row[0] for row in rows}
        if "system_text" not in existing:
            conn.execute(text("ALTER TABLE bot_button_settings ADD COLUMN system_text TEXT"))
        if "stable_button_id" not in existing:
            conn.execute(text("ALTER TABLE bot_button_settings ADD COLUMN stable_button_id VARCHAR(36)"))
        if "legacy_button_key" not in existing:
            conn.execute(text("ALTER TABLE bot_button_settings ADD COLUMN legacy_button_key VARCHAR(500)"))
        if "currently_present" not in existing:
            conn.execute(text("ALTER TABLE bot_button_settings ADD COLUMN currently_present BOOLEAN"))
        if "label_override" not in existing:
            conn.execute(text("ALTER TABLE bot_button_settings ADD COLUMN label_override VARCHAR(255)"))
        if "label_overridden" not in existing:
            conn.execute(text("ALTER TABLE bot_button_settings ADD COLUMN label_overridden BOOLEAN"))
        if "client_text" not in existing:
            conn.execute(text("ALTER TABLE bot_button_settings ADD COLUMN client_text TEXT"))
        if "client_text_overridden" not in existing:
            conn.execute(text("ALTER TABLE bot_button_settings ADD COLUMN client_text_overridden BOOLEAN"))
        if "system_text_overridden" not in existing:
            conn.execute(text("ALTER TABLE bot_button_settings ADD COLUMN system_text_overridden BOOLEAN"))
        if "fixed_assignee_enabled" not in existing:
            conn.execute(text("ALTER TABLE bot_button_settings ADD COLUMN fixed_assignee_enabled BOOLEAN"))
        if "fixed_assignee_user_id" not in existing:
            conn.execute(text("ALTER TABLE bot_button_settings ADD COLUMN fixed_assignee_user_id VARCHAR(36)"))
        if "freeze_on_create" not in existing:
            conn.execute(text("ALTER TABLE bot_button_settings ADD COLUMN freeze_on_create BOOLEAN"))
        if "confirmation_enabled" not in existing:
            conn.execute(text("ALTER TABLE bot_button_settings ADD COLUMN confirmation_enabled BOOLEAN"))
        if "confirmation_text" not in existing:
            conn.execute(text("ALTER TABLE bot_button_settings ADD COLUMN confirmation_text TEXT"))
        if "confirmation_overridden" not in existing:
            conn.execute(text("ALTER TABLE bot_button_settings ADD COLUMN confirmation_overridden BOOLEAN"))
        if "files_overridden" not in existing:
            conn.execute(text("ALTER TABLE bot_button_settings ADD COLUMN files_overridden BOOLEAN"))
        if "files_json" not in existing:
            conn.execute(text("ALTER TABLE bot_button_settings ADD COLUMN files_json JSONB"))
        if "conditional_files_overridden" not in existing:
            conn.execute(text("ALTER TABLE bot_button_settings ADD COLUMN conditional_files_overridden BOOLEAN"))
        if "conditional_files_json" not in existing:
            conn.execute(text("ALTER TABLE bot_button_settings ADD COLUMN conditional_files_json JSONB"))
        if "is_enabled" not in existing:
            conn.execute(text("ALTER TABLE bot_button_settings ADD COLUMN is_enabled BOOLEAN"))
        if "automation_key" not in existing:
            conn.execute(text("ALTER TABLE bot_button_settings ADD COLUMN automation_key VARCHAR(120)"))
        if "automation_overridden" not in existing:
            conn.execute(text("ALTER TABLE bot_button_settings ADD COLUMN automation_overridden BOOLEAN"))
        if "visibility_overridden" not in existing:
            conn.execute(text("ALTER TABLE bot_button_settings ADD COLUMN visibility_overridden BOOLEAN"))
        if "is_db_created" not in existing:
            conn.execute(text("ALTER TABLE bot_button_settings ADD COLUMN is_db_created BOOLEAN"))
        if "button_kind" not in existing:
            conn.execute(text("ALTER TABLE bot_button_settings ADD COLUMN button_kind VARCHAR(30)"))
        if "parent_path" not in existing:
            conn.execute(text("ALTER TABLE bot_button_settings ADD COLUMN parent_path VARCHAR(1000)"))
        if "is_archived" not in existing:
            conn.execute(text("ALTER TABLE bot_button_settings ADD COLUMN is_archived BOOLEAN"))
        if "deleted_at" not in existing:
            conn.execute(text("ALTER TABLE bot_button_settings ADD COLUMN deleted_at TIMESTAMP"))
        if "deleted_by_user_id" not in existing:
            conn.execute(text("ALTER TABLE bot_button_settings ADD COLUMN deleted_by_user_id VARCHAR(36)"))
        conn.execute(text("UPDATE bot_button_settings SET system_text_overridden = FALSE WHERE system_text_overridden IS NULL"))
        conn.execute(text("UPDATE bot_button_settings SET fixed_assignee_enabled = FALSE WHERE fixed_assignee_enabled IS NULL"))
        conn.execute(text("UPDATE bot_button_settings SET freeze_on_create = FALSE WHERE freeze_on_create IS NULL"))
        conn.execute(text("UPDATE bot_button_settings SET confirmation_enabled = FALSE WHERE confirmation_enabled IS NULL"))
        conn.execute(text("UPDATE bot_button_settings SET confirmation_overridden = FALSE WHERE confirmation_overridden IS NULL"))
        conn.execute(text("UPDATE bot_button_settings SET files_overridden = FALSE WHERE files_overridden IS NULL"))
        conn.execute(text("UPDATE bot_button_settings SET conditional_files_overridden = FALSE WHERE conditional_files_overridden IS NULL"))
        conn.execute(text("UPDATE bot_button_settings SET automation_overridden = FALSE WHERE automation_overridden IS NULL"))
        conn.execute(text("UPDATE bot_button_settings SET is_enabled = TRUE WHERE is_enabled IS NULL"))
        conn.execute(text("UPDATE bot_button_settings SET visibility_overridden = FALSE WHERE visibility_overridden IS NULL"))
        conn.execute(text("UPDATE bot_button_settings SET is_db_created = FALSE WHERE is_db_created IS NULL"))
        conn.execute(text("UPDATE bot_button_settings SET button_kind = 'terminal' WHERE button_kind IS NULL"))
        conn.execute(text("UPDATE bot_button_settings SET parent_path = '' WHERE parent_path IS NULL"))
        conn.execute(text("UPDATE bot_button_settings SET currently_present = TRUE WHERE currently_present IS NULL"))
        conn.execute(text("UPDATE bot_button_settings SET is_archived = FALSE WHERE is_archived IS NULL"))
        conn.execute(text("UPDATE bot_button_settings SET label_overridden = FALSE WHERE label_overridden IS NULL"))
        conn.execute(text("UPDATE bot_button_settings SET client_text_overridden = FALSE WHERE client_text_overridden IS NULL"))
        conn.execute(text("UPDATE bot_button_settings SET stable_button_id = id WHERE stable_button_id IS NULL"))


def ensure_bot_menu_level_layouts_table() -> None:
    with engine.begin() as conn:
        dialect = conn.dialect.name
        if dialect == "sqlite":
            conn.execute(
                text(
                    """
                    CREATE TABLE IF NOT EXISTS bot_menu_level_layouts (
                        id VARCHAR(36) PRIMARY KEY,
                        menu_type VARCHAR(200) NOT NULL,
                        level_key VARCHAR(600) NOT NULL UNIQUE,
                        layout_json TEXT,
                        created_at TIMESTAMP,
                        updated_at TIMESTAMP
                    )
                    """
                )
            )
            conn.execute(text("CREATE INDEX IF NOT EXISTS ix_bot_menu_level_layouts_menu_type ON bot_menu_level_layouts(menu_type)"))
            return

        conn.execute(
            text(
                """
                CREATE TABLE IF NOT EXISTS bot_menu_level_layouts (
                    id VARCHAR(36) PRIMARY KEY,
                    menu_type VARCHAR(200) NOT NULL,
                    level_key VARCHAR(600) NOT NULL UNIQUE,
                    layout_json JSONB,
                    created_at TIMESTAMP,
                    updated_at TIMESTAMP
                )
                """
            )
        )
        conn.execute(text("CREATE INDEX IF NOT EXISTS ix_bot_menu_level_layouts_menu_type ON bot_menu_level_layouts(menu_type)"))


def ensure_bot_menu_profiles_tables() -> None:
    with engine.begin() as conn:
        dialect = conn.dialect.name
        timestamp_type = "TIMESTAMP"
        bool_type = "BOOLEAN"
        conn.execute(
            text(
                f"""
                CREATE TABLE IF NOT EXISTS bot_menu_profiles (
                    id VARCHAR(36) PRIMARY KEY,
                    menu_type VARCHAR(200) UNIQUE NOT NULL,
                    display_name VARCHAR(200) NOT NULL,
                    is_fallback {bool_type},
                    is_active {bool_type},
                    created_at {timestamp_type},
                    updated_at {timestamp_type}
                )
                """
            )
        )
        conn.execute(
            text(
                f"""
                CREATE TABLE IF NOT EXISTS bot_menu_destination_bindings (
                    id VARCHAR(36) PRIMARY KEY,
                    destination_value VARCHAR(200) UNIQUE NOT NULL,
                    menu_type VARCHAR(200) NOT NULL,
                    created_at {timestamp_type},
                    updated_at {timestamp_type}
                )
                """
            )
        )
        conn.execute(text("CREATE UNIQUE INDEX IF NOT EXISTS ix_bot_menu_profiles_menu_type ON bot_menu_profiles(menu_type)"))
        conn.execute(text("CREATE UNIQUE INDEX IF NOT EXISTS ix_bot_menu_destination_bindings_destination ON bot_menu_destination_bindings(destination_value)"))
        conn.execute(text("CREATE INDEX IF NOT EXISTS ix_bot_menu_destination_bindings_menu_type ON bot_menu_destination_bindings(menu_type)"))
        conn.execute(text("UPDATE bot_menu_profiles SET is_fallback = FALSE WHERE is_fallback IS NULL"))
        conn.execute(text("UPDATE bot_menu_profiles SET is_active = TRUE WHERE is_active IS NULL"))


def ensure_automatic_message_templates_table() -> None:
    with engine.begin() as conn:
        timestamp_type = "TIMESTAMP"
        bool_type = "BOOLEAN"
        conn.execute(
            text(
                f"""
                CREATE TABLE IF NOT EXISTS automatic_message_templates (
                    id VARCHAR(36) PRIMARY KEY,
                    key VARCHAR(150) UNIQUE NOT NULL,
                    body TEXT,
                    is_overridden {bool_type},
                    html_enabled {bool_type},
                    created_at {timestamp_type},
                    updated_at {timestamp_type}
                )
                """
            )
        )
        conn.execute(text("CREATE UNIQUE INDEX IF NOT EXISTS ix_automatic_message_templates_key ON automatic_message_templates(key)"))
        conn.execute(text("UPDATE automatic_message_templates SET is_overridden = FALSE WHERE is_overridden IS NULL"))
        conn.execute(text("UPDATE automatic_message_templates SET html_enabled = TRUE WHERE html_enabled IS NULL"))


def ensure_ticket_message_columns() -> None:
    required_columns = {
        "agent_visible": "BOOLEAN",
        "telegram_message_id": "VARCHAR(100)",
        "message_status": "VARCHAR(20)",
        "status_note": "VARCHAR(255)",
        "replaced_message_id": "VARCHAR(36)",
    }

    with engine.begin() as conn:
        dialect = conn.dialect.name
        existing: set[str] = set()

        if dialect == "sqlite":
            rows = conn.execute(text("PRAGMA table_info(ticket_messages)")).mappings().all()
            existing = {row["name"] for row in rows}
        else:
            rows = conn.execute(
                text(
                    """
                    SELECT column_name
                    FROM information_schema.columns
                    WHERE table_name = 'ticket_messages'
                    """
                )
            ).fetchall()
            existing = {row[0] for row in rows}

        for column_name, column_type in required_columns.items():
            if column_name not in existing:
                conn.execute(text(f"ALTER TABLE ticket_messages ADD COLUMN {column_name} {column_type}"))
        conn.execute(text("UPDATE ticket_messages SET agent_visible = TRUE WHERE message_type != 'system' AND agent_visible IS NULL"))
        conn.execute(text("UPDATE ticket_messages SET agent_visible = FALSE WHERE message_type = 'system' AND agent_visible IS NULL"))
        conn.execute(text("UPDATE ticket_messages SET message_status = 'normal' WHERE message_status IS NULL"))


def ensure_ticket_attachment_table() -> None:
    with engine.begin() as conn:
        dialect = conn.dialect.name
        if dialect == "sqlite":
            conn.execute(
                text(
                    """
                    CREATE TABLE IF NOT EXISTS ticket_attachments (
                        id VARCHAR(36) PRIMARY KEY,
                        ticket_id VARCHAR(36) NOT NULL,
                        message_id VARCHAR(36) NOT NULL,
                        source VARCHAR(40) NOT NULL,
                        telegram_file_id VARCHAR(255),
                        telegram_file_unique_id VARCHAR(255),
                        telegram_file_path VARCHAR(500),
                        telegram_message_id VARCHAR(100),
                        original_filename VARCHAR(255),
                        mime_type VARCHAR(255),
                        file_size INTEGER,
                        local_path VARCHAR(1000) NOT NULL,
                        created_at TIMESTAMP
                    )
                    """
                )
            )
            rows = conn.execute(text("PRAGMA table_info(ticket_attachments)")).mappings().all()
            existing = {row["name"] for row in rows}
            if "telegram_message_id" not in existing:
                conn.execute(text("ALTER TABLE ticket_attachments ADD COLUMN telegram_message_id VARCHAR(100)"))
            return

        conn.execute(
            text(
                """
                CREATE TABLE IF NOT EXISTS ticket_attachments (
                    id VARCHAR(36) PRIMARY KEY,
                    ticket_id VARCHAR(36) NOT NULL REFERENCES tickets(id),
                    message_id VARCHAR(36) NOT NULL REFERENCES ticket_messages(id),
                    source VARCHAR(40) NOT NULL,
                    telegram_file_id VARCHAR(255),
                    telegram_file_unique_id VARCHAR(255),
                    telegram_file_path VARCHAR(500),
                    telegram_message_id VARCHAR(100),
                    original_filename VARCHAR(255),
                    mime_type VARCHAR(255),
                    file_size INTEGER,
                    local_path VARCHAR(1000) NOT NULL,
                    created_at TIMESTAMP
                )
                """
            )
        )
        rows = conn.execute(
            text(
                """
                SELECT column_name
                FROM information_schema.columns
                WHERE table_name = 'ticket_attachments'
                """
            )
        ).fetchall()
        existing = {row[0] for row in rows}
        if "telegram_message_id" not in existing:
            conn.execute(text("ALTER TABLE ticket_attachments ADD COLUMN telegram_message_id VARCHAR(100)"))
