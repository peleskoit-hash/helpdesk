from copy import deepcopy
from types import SimpleNamespace
import uuid

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, inspect, select, text
from sqlalchemy.exc import DBAPIError

from api.app.auth import get_current_user
from api.app.db import get_db
from api.app.models import BotButtonSetting
from api.app.routers import settings_bot_button_items as items, settings_bot_button_structure as structure
from api.app.routers import telegram
from api.app.services import schema_evolution
from test_db_only_blockers import db, button, no_txt


KINDS = [("ticket", "terminal"), ("task", "terminal"), ("auto", "terminal"),
         ("submenu", "section"), ("group_task", "group_parent"),
         ("group_task", "section"), ("group_task_child", "terminal")]


@pytest.mark.parametrize("workflow,kind", KINDS)
def test_generated_callbacks_for_every_button_kind(db, monkeypatch, workflow, kind):
    row = button(db, "Parent", workflow=workflow, kind=kind)
    if workflow == "submenu":
        button(db, "Child", parent="Parent")
    sent = []
    monkeypatch.setattr(telegram, "send_message", lambda *args, **kwargs: sent.append(kwargs))
    telegram._send_problem_menu_with_visibility(db, "456", menu_name="menu-a")
    callback = sent[-1]["reply_markup"]["inline_keyboard"][0][0]["callback_data"]
    if workflow == "submenu":
        assert callback == f"menuid:{row.id}"
        assert telegram._menu_key_from_callback(db, callback, "menu-a") == row.button_key
    else:
        assert callback == f"btnid:{row.id}"
        resolved = telegram._button_from_callback(db, callback, "menu-a")
        assert resolved.kind == ("task" if workflow == "group_task_child" else workflow)


@pytest.mark.parametrize("workflow,kind", KINDS)
def test_all_button_kinds_archive_and_restore_without_origin(db, no_txt, workflow, kind):
    row = button(db, "Parent", workflow=workflow, kind=kind, stable_button_id=str(uuid.uuid4()),
                 legacy_button_key="previous-key", instruction_text="Keep instruction",
                 fixed_assignee_enabled=True, fixed_assignee_user_id="agent", files_json=["keep.pdf"],
                 conditional_files_json=[{"files": ["conditional.pdf"]}], client_text="Keep text")
    child = button(db, "Child", parent="Parent", workflow="group_task_child") if kind in {"section", "group_parent"} else None
    db.commit()
    changed = {"currently_present", "is_archived", "deleted_at", "deleted_by_user_id", "updated_at"}
    preserved = {column.name: deepcopy(getattr(row, column.name)) for column in row.__table__.columns
                 if column.name not in changed}
    app = FastAPI()
    app.include_router(items.router)
    app.include_router(structure.router)
    app.dependency_overrides[get_db] = lambda: db
    app.dependency_overrides[get_current_user] = lambda: SimpleNamespace(id="admin", role="admin")
    with TestClient(app) as http:
        response = http.delete("/api/v1/settings/bot-buttons/item", params={"button_key": row.button_key})
        assert response.status_code == 200, response.text
        db.refresh(row)
        assert row.is_archived and not row.currently_present
        if child:
            db.refresh(child)
            assert child.is_archived and not child.currently_present
        response = http.post("/api/v1/settings/bot-buttons/item/restore", params={"button_key": "previous-key"})
        assert response.status_code == 200, response.text
        db.refresh(row)
        assert row.currently_present and not row.is_archived
        if child:
            db.refresh(child)
            assert child.currently_present and not child.is_archived
        detail = http.get("/api/v1/settings/bot-buttons/item", params={"button_key": row.button_key})
        assert detail.status_code == 200
        tree = http.get("/api/v1/settings/bot-buttons/menu-types/menu-a/structure")
        assert tree.status_code == 200
        assert "is_db_created" not in detail.json()
        assert "is_db_created" not in tree.text
    assert {field: getattr(row, field) for field in preserved} == preserved


def test_hard_delete_of_archived_button_preserves_other_button_settings(db):
    archived = button(db, "Archived", is_archived=True, currently_present=False)
    active = button(db, "Active", stable_button_id=str(uuid.uuid4()), files_json=["keep.pdf"],
                    legacy_button_key="previous-active", instruction_text="Keep instruction")
    db.commit()
    preserved = {column.name: deepcopy(getattr(active, column.name)) for column in active.__table__.columns}
    result = items.delete_bot_button_setting(archived.button_key, hard=True, db=db,
                                            user=SimpleNamespace(id="admin", role="admin"))
    assert result == {"ok": True, "deleted": True, "hard": True}
    assert db.query(BotButtonSetting).one().id == active.id
    db.refresh(active)
    assert {field: getattr(active, field) for field in preserved} == preserved


def test_same_leaf_names_in_different_branches_have_no_txt_hash_collision(db, no_txt):
    for root in ("Left", "Right"):
        button(db, root, workflow="submenu", kind="section")
        button(db, "Shared", parent=root, workflow="submenu", kind="section")
        structure.create_bot_button_item({"menu_type": "menu-a", "parent_path": f"{root}/Shared",
            "label": "Leaf", "button_kind": "ticket", "workflow_type": "ticket"},
            db=db, user=SimpleNamespace(id="admin", role="admin"))
    assert db.query(BotButtonSetting).filter_by(button_label="Leaf").count() == 2


def test_origin_column_migration_preserves_rows_constraints_and_repeated_startup(db, monkeypatch):
    for index in range(48):
        workflow, kind = KINDS[index % len(KINDS)]
        button(db, f"Button {index}", workflow=workflow, kind=kind, stable_button_id=str(uuid.uuid4()),
               legacy_button_key=f"previous-{index}", parent="Folder", is_archived=index % 2 == 0,
               currently_present=index % 2 != 0, fixed_assignee_user_id="agent",
               files_json=[f"file-{index}.pdf"], conditional_files_json=[{"files": ["conditional.pdf"]}],
               instruction_text="Keep instruction", client_text="Keep text", label_override="Keep label")
    db.commit()
    engine = db.get_bind()
    monkeypatch.setattr(schema_evolution, "engine", engine)
    table = BotButtonSetting.__table__
    with engine.begin() as conn:
        conn.execute(text("ALTER TABLE bot_button_settings ADD COLUMN is_db_created BOOLEAN DEFAULT FALSE"))
        conn.execute(text("UPDATE bot_button_settings SET is_db_created = TRUE WHERE button_label = 'Button 47'"))
        conn.execute(text("CREATE INDEX button_label_test_idx ON bot_button_settings(button_label)"))
        conn.execute(text("CREATE TABLE button_refs (button_id VARCHAR(36) REFERENCES bot_button_settings(id))"))
        conn.execute(text("INSERT INTO button_refs SELECT id FROM bot_button_settings"))
        assert conn.scalar(text("SELECT count(*) FROM bot_button_settings WHERE is_db_created = FALSE")) == 47
        before = conn.execute(select(table).order_by(table.c.id)).all()
        indexes = inspect(conn).get_indexes(table.name)
        unique = inspect(conn).get_unique_constraints(table.name)
        foreign_keys = inspect(conn).get_foreign_keys("button_refs")
        tables = inspect(conn).get_table_names()
        schema_evolution.drop_bot_button_origin_column(conn)
        schema_evolution.drop_bot_button_origin_column(conn)
    schema_evolution.ensure_bot_button_settings_table()
    schema_evolution.ensure_bot_button_settings_table()
    with engine.connect() as conn:
        assert "is_db_created" not in {col["name"] for col in inspect(conn).get_columns(table.name)}
        assert conn.execute(select(table).order_by(table.c.id)).all() == before
        assert inspect(conn).get_indexes(table.name) == indexes
        assert inspect(conn).get_unique_constraints(table.name) == unique
        assert inspect(conn).get_foreign_keys("button_refs") == foreign_keys
        assert inspect(conn).get_table_names() == tables
        assert conn.scalar(text("SELECT count(*) FROM button_refs JOIN bot_button_settings ON button_id = id")) == 48
        assert conn.execute(text("PRAGMA foreign_key_check")).all() == []


def test_origin_column_drop_does_not_cascade_over_unexpected_dependencies(db):
    row = button(db, "Keep")
    db.commit()
    engine = db.get_bind()
    with engine.begin() as conn:
        conn.execute(text("ALTER TABLE bot_button_settings ADD COLUMN is_db_created BOOLEAN"))
        conn.execute(text("CREATE INDEX unexpected_origin_dependency ON bot_button_settings(is_db_created)"))
    with pytest.raises(DBAPIError), engine.begin() as conn:
        schema_evolution.drop_bot_button_origin_column(conn)
    with engine.connect() as conn:
        assert "is_db_created" in {col["name"] for col in inspect(conn).get_columns("bot_button_settings")}
        assert conn.scalar(select(BotButtonSetting.id)) == row.id


def test_fresh_and_missing_schema_need_no_origin_flag(monkeypatch):
    engine = create_engine("sqlite://")
    monkeypatch.setattr(schema_evolution, "engine", engine)
    try:
        with engine.begin() as conn:
            schema_evolution.drop_bot_button_origin_column(conn)
        schema_evolution.ensure_bot_button_settings_table()
        schema_evolution.ensure_bot_button_settings_table()
        assert "is_db_created" not in {col["name"] for col in inspect(engine).get_columns("bot_button_settings")}
        assert "is_db_created" not in BotButtonSetting.__table__.columns
    finally:
        engine.dispose()
