from __future__ import annotations

from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker

from api.app.models import AutomaticMessageTemplate
from api.app.services import schema_evolution
from api.app.services.automatic_messages import upsert_notification_bot_basic_template


def test_automatic_message_templates_schema_evolution_preserves_existing_rows(monkeypatch):
    engine = create_engine("sqlite:///:memory:", future=True)
    monkeypatch.setattr(schema_evolution, "engine", engine)

    with engine.begin() as conn:
        conn.execute(
            text(
                """
                CREATE TABLE automatic_message_templates (
                    id VARCHAR(36) PRIMARY KEY,
                    key VARCHAR(150) UNIQUE NOT NULL,
                    body TEXT,
                    is_overridden BOOLEAN,
                    html_enabled BOOLEAN,
                    created_at TIMESTAMP,
                    updated_at TIMESTAMP
                )
                """
            )
        )
        conn.execute(
            text(
                """
                INSERT INTO automatic_message_templates
                    (id, key, body, is_overridden, html_enabled, created_at, updated_at)
                VALUES
                    ('row-1', 'telegram.ticket.close.client', 'Старый текст', 1, 0, '2026-01-01', '2026-01-02')
                """
            )
        )

    schema_evolution.ensure_automatic_message_templates_table()

    with engine.connect() as conn:
        row = conn.execute(
            text(
                """
                SELECT body, is_overridden, html_enabled, enabled, disable_link_preview, send_silently
                FROM automatic_message_templates
                WHERE key = 'telegram.ticket.close.client'
                """
            )
        ).mappings().one()
        assert row["body"] == "Старый текст"
        assert bool(row["is_overridden"]) is True
        assert bool(row["html_enabled"]) is False
        assert bool(row["enabled"]) is True
        assert bool(row["disable_link_preview"]) is False
        assert bool(row["send_silently"]) is False

    Session = sessionmaker(bind=engine, autoflush=False, autocommit=False)
    session = Session()
    upsert_notification_bot_basic_template(
        session,
        "new_assignment",
        enabled=True,
        template="Новый текст",
        disable_link_preview=True,
        send_silently=True,
    )
    session.commit()

    saved = session.query(AutomaticMessageTemplate).filter_by(key="telegram.notification_bot.basic.new_assignment").one()
    assert saved.body == "Новый текст"
    assert saved.enabled is True
    assert saved.disable_link_preview is True
    assert saved.send_silently is True
