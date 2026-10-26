from copy import deepcopy
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
import uuid

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm.attributes import set_committed_value

from api.app.auth import get_current_user
from api.app.db import get_db
from api.app.models import BotButtonSetting, BotMenuLevelLayout, Client, Company, Ticket
from api.app.routers import settings_bot_button_structure as structure, telegram
from api.app.services.button_config import build_level_layout_key, button_key_aliases, button_key_matches, resolve_button_row
from api.app.services.ticket_subject import resolve_ticket_subject, build_ticket_subject_map
from test_db_only_blockers import db, button, no_txt
from test_button_key_transitions import save


ADMIN = SimpleNamespace(id="admin", role="admin")


@pytest.fixture
def http(db):
    app = FastAPI()
    app.include_router(structure.router)
    app.dependency_overrides[get_db] = lambda: db
    app.dependency_overrides[get_current_user] = lambda: ADMIN
    with TestClient(app) as client:
        yield client


def layout(db, parent, rows):
    value = BotMenuLevelLayout(id=str(uuid.uuid4()), menu_type="menu-a",
        level_key=build_level_layout_key("menu-a", parent),
        layout_json=[{"columns": len(row), "button_keys": [item.button_key for item in row]} for row in rows])
    db.add(value)
    return value


def layouts(db):
    return {row.level_key: deepcopy(row.layout_json) for row in db.query(BotMenuLevelLayout)}


def move(http, row, parent, **position):
    return http.post("/api/v1/settings/bot-buttons/item/move", json={
        "button_id": row.id, "menu_type": row.menu_type, "parent_path": parent, **position})


def folders(db):
    return [button(db, name, kind="section", workflow="submenu") for name in ("A", "B")]


@pytest.mark.parametrize("parent", ["", "A"])
@pytest.mark.parametrize("date_field", ["updated_at", "created_at"])
def test_structure_sorts_mixed_datetimes_in_utc_without_mutating_dates(db, http, parent, date_field):
    if parent:
        button(db, parent, kind="section", workflow="submenu")
    dates = {
        "First": datetime(2026, 10, 26, 8, tzinfo=timezone(timedelta(hours=3))),
        "Zulu": datetime(2026, 10, 26, 1, tzinfo=timezone(timedelta(hours=-5))),
        "Alpha": datetime(2026, 10, 26, 6),
        "Last": datetime(2026, 10, 26, 8, tzinfo=timezone.utc),
    }
    rows = [button(db, name, parent=parent, created_at=date, updated_at=date) for name, date in dates.items()]
    db.commit()
    statement = select(BotButtonSetting.id, BotButtonSetting.updated_at, BotButtonSetting.created_at).order_by(BotButtonSetting.id)
    stored_dates = db.connection().execute(statement).all()
    # Preserve driver-style aware values in the identity map; SQLite drops tzinfo on reload.
    for row in rows:
        db.refresh(row)
        set_committed_value(row, date_field, dates[row.button_label])
        if date_field == "created_at":
            set_committed_value(row, "updated_at", None)
    in_memory_dates = [(row.updated_at, row.created_at) for row in rows]
    response = http.get("/api/v1/settings/bot-buttons/menu-types/menu-a/structure")
    assert response.status_code == 200, response.text
    tree = response.json()["tree_rows"]
    if parent:
        tree = tree[0]["buttons"][0]["children_rows"]
    assert [node["label"] for item in tree for node in item["buttons"]] == ["First", "Alpha", "Zulu", "Last"]
    assert [(row.updated_at, row.created_at) for row in rows] == in_memory_dates
    assert db.connection().execute(statement).all() == stored_dates
    assert not db.dirty


def test_structure_exposes_database_identity_at_every_depth_without_writes(db, http):
    a = button(db, "Internal A", kind="section", workflow="submenu", id=str(uuid.uuid4()),
               label_override="Visible / A", label_overridden=True)
    nested = button(db, "Internal nested", parent=a.button_path, kind="section", workflow="submenu",
                    id=str(uuid.uuid4()), label_override="Visible nested", label_overridden=True)
    rows = [a, nested]
    for index, parent in enumerate(("", a.button_path, nested.button_path)):
        rows.append(button(db, f"Leaf {index}", parent=parent, workflow=("ticket", "task", "auto")[index],
                           id=str(uuid.uuid4()), stable_button_id=str(uuid.uuid4()), button_style="success"))
    removed = button(db, "Removed", parent=nested.button_path, id=str(uuid.uuid4()), is_archived=True)
    group = button(db, "Group", kind="group_parent", workflow="group_task", id=str(uuid.uuid4()))
    group_child = button(db, "Child", parent=group.button_path, workflow="group_task_child", id=str(uuid.uuid4()))
    rows += [removed, group, group_child]
    db.commit()
    snapshot = {row.id: {column.name: deepcopy(getattr(row, column.name)) for column in row.__table__.columns} for row in rows}
    response = http.get("/api/v1/settings/bot-buttons/menu-types/menu-a/structure")
    assert response.status_code == 200, response.text
    payload = response.json()
    nodes = {}

    def walk(tree):
        for layout_row in tree:
            for node in layout_row["buttons"]:
                if node.get("technical_key"):
                    nodes[node["technical_key"]] = node
                walk(node.get("children_rows", []))

    walk(payload["tree_rows"])
    flat = [*payload["tickets"], *payload["tasks"], *payload["autos"], *payload["removed_items"], *payload["group_tasks"]]
    flat += [child for parent in payload["group_tasks"] for child in parent["children"]]
    for row in rows:
        node = nodes[row.button_key]
        assert node["button_id"] == row.id
        assert str(uuid.UUID(node["button_id"])) == row.id
        assert node["parent_path"] == row.parent_path
        assert node["full_path"] == row.button_path
        if row.stable_button_id:
            assert node["button_id"] != row.stable_button_id
        if row.button_kind == "terminal" or row is group:
            item = next(item for item in flat if item["technical_key"] == row.button_key)
            assert (item["button_id"], item["parent_path"]) == (row.id, row.parent_path)
    assert nodes[a.button_key]["label"] == "Visible / A"
    assert nodes[a.button_key]["full_path"] == "Internal A"
    assert {row.id: {name: getattr(row, name) for name in snapshot[row.id]} for row in rows} == snapshot


@pytest.mark.parametrize("source,target", [("", "A"), ("A", ""), ("A", "B")])
def test_move_uses_identity_and_destination_from_structure_response(db, http, source, target):
    parents = folders(db)
    for parent in parents:
        parent.label_override, parent.label_overridden = f"Visible {parent.button_path}", True
    row = button(db, "Internal", parent=source, id=str(uuid.uuid4()), stable_button_id=str(uuid.uuid4()),
                 label_override="Display / unrelated", label_overridden=True, button_style="danger", instruction_text="Keep")
    db.commit()
    response = http.get("/api/v1/settings/bot-buttons/menu-types/menu-a/structure").json()
    leaves = response["tickets"]
    leaf = next(item for item in leaves if item["button_id"] == row.id)
    top = [node for item in response["tree_rows"] for node in item["buttons"]]
    parent = next((item for item in parents if item.button_path == target), None)
    parent_path = next(node["full_path"] for node in top if node["button_id"] == parent.id) if parent else ""
    before = {column.name: deepcopy(getattr(row, column.name)) for column in row.__table__.columns
              if column.name not in {"button_path", "parent_path", "updated_at"}}
    moved = http.post("/api/v1/settings/bot-buttons/item/move", json={
        "button_id": leaf["button_id"], "menu_type": "menu-a", "parent_path": parent_path,
        "new_row": True, "target_index": 0})
    assert moved.status_code == 200, moved.text
    actual = next(item for item in moved.json()["structure"]["tickets"] if item["button_id"] == leaf["button_id"])
    assert actual["parent_path"] == target
    assert actual["full_path"] == (f"{target}/Internal" if target else "Internal")
    assert {name: getattr(row, name) for name in before} == before


@pytest.mark.parametrize("source,target", [("", "A"), ("A", ""), ("A", "B")])
def test_moves_preserve_every_setting_order_and_uuid_callbacks(db, http, no_txt, monkeypatch, source, target):
    parents = folders(db)
    left = button(db, "Left", parent=source)
    row = button(db, "Move", parent=source, stable_button_id=str(uuid.uuid4()), button_style="success",
        instruction_text="Keep", client_text="Client", system_text="System", files_json=["file.txt"],
        files_overridden=True, conditional_files_json=[{"conditions": [], "files": ["file.txt"]}],
        conditional_files_overridden=True, automation_key="send_files", automation_overridden=True,
        confirmation_enabled=True, confirmation_text="Confirm", fixed_assignee_enabled=True,
        fixed_assignee_user_id="agent", label_overridden=True, label_override="Visible / label",
        suppress_client_answer=True, freeze_on_create=True)
    right = button(db, "Right", parent=source)
    first = button(db, "First", parent=target)
    last = button(db, "Last", parent=target)
    source_rows, target_rows = [[left, row, right]], [[first, last]]
    if not source:
        source_rows += [[parents[1]], [parents[0]]]
    elif not target:
        target_rows += [[parents[1]], [parents[0]]]
    src = layout(db, source, source_rows)
    dst = layout(db, target, target_rows)
    db.commit()
    settings = {c.name: deepcopy(getattr(row, c.name)) for c in row.__table__.columns
                if c.name not in {"parent_path", "button_path", "updated_at"}}
    before_target = deepcopy(dst.layout_json)
    aliases = button_key_aliases(row)
    result = move(http, row, target, target_row=0, target_index=1, new_row=False)
    assert result.status_code == 200, result.text
    db.expire_all()
    assert {name: getattr(row, name) for name in settings} == settings
    assert row.parent_path == target and row.button_path == (f"{target}/Move" if target else "Move")
    assert src.layout_json[0] == {"columns": 2, "button_keys": [left.button_key, right.button_key]}
    expected_tail = [[p.button_key] for p in reversed(parents)] if not source else []
    assert [item["button_keys"] for item in src.layout_json[1:]] == expected_tail
    before_target[0] = {"columns": 3, "button_keys": [first.button_key, row.button_key, last.button_key]}
    assert dst.layout_json == before_target
    assert db.query(BotButtonSetting).count() == 7
    assert button_key_aliases(row) == aliases
    assert telegram._button_from_callback(db, f"btnid:{row.id}", "menu-a").key == row.button_key
    for parent in parents:
        assert telegram._menu_key_from_callback(db, f"menuid:{parent.id}", "menu-a") == parent.button_key
    sent = []
    monkeypatch.setattr(telegram, "send_message", lambda *args, **kwargs: sent.append(kwargs) or {})
    parent = next((item for item in parents if item.button_path == target), None)
    telegram._send_problem_menu_db_only(db, "chat", menu_name="menu-a", menu_key=parent.button_key if parent else None)
    rendered = [item for line in sent[0]["reply_markup"]["inline_keyboard"] for item in line]
    assert next(item for item in rendered if item["callback_data"] == f"btnid:{row.id}")["style"] == "success"


@pytest.mark.parametrize("problem", ["collision", "missing", "terminal", "group", "archived", "cycle", "too_wide", "position", "self", "cross_menu"])
def test_invalid_moves_roll_back_every_record(db, http, problem):
    folders(db)
    row = button(db, "Move")
    a = db.query(BotButtonSetting).filter_by(button_path="A").one()
    target = "A"
    position = {}
    if problem == "collision":
        button(db, "Move", parent="A", is_archived=True)
    elif problem == "missing":
        target = "Missing"
    elif problem == "terminal":
        a.button_kind, a.workflow_type = "terminal", "ticket"
    elif problem == "group":
        a.button_kind, a.workflow_type = "group_parent", "group_task"
    elif problem == "archived":
        a.is_archived = True
    elif problem == "cycle":
        a.parent_path = "A"
    elif problem == "self":
        target = row.button_path
    elif problem == "cross_menu":
        button(db, "Foreign", menu="menu-b", kind="section", workflow="submenu")
        target = "Foreign"
    elif problem == "too_wide":
        children = [button(db, name, parent="A") for name in ("One", "Two", "Three")]
        layout(db, "A", [children])
        position = {"target_row": 0, "new_row": False}
    elif problem == "position":
        position = {"target_row": 99}
    layout(db, "", [[row]])
    db.commit()
    before = layouts(db)
    records = {item.id: {c.name: deepcopy(getattr(item, c.name)) for c in item.__table__.columns}
               for item in db.query(BotButtonSetting)}
    result = move(http, row, target, **position)
    assert result.status_code in {409, 422}, result.text
    db.expire_all()
    assert layouts(db) == before
    assert {item.id: {c.name: getattr(item, c.name) for c in item.__table__.columns}
            for item in db.query(BotButtonSetting)} == records


@pytest.mark.parametrize("kind,workflow", [("section", "submenu"), ("group_parent", "group_task"), ("terminal", "group_task_child")])
def test_nonordinary_buttons_cannot_move(db, http, kind, workflow):
    folders(db)
    row = button(db, "Move", kind=kind, workflow=workflow)
    db.commit()
    assert move(http, row, "A").status_code == 422
    assert row.parent_path == ""


@pytest.mark.parametrize("db_key", [False, True])
def test_repeated_moves_workflow_history_search_and_editing(db, http, no_txt, db_key):
    folders(db)
    row = button(db, "Move", instruction_text="Keep", button_style="danger")
    if db_key:
        row.button_key = "menu-a|db|Move"
    original = row.button_key
    db.add(Company(id="company", name="Company"))
    db.flush()
    db.add(Client(id="client", company_id="company", display_name="Client"))
    db.flush()
    ticket = Ticket(id="historic", company_id="company", client_id="client", subject="Stored subject",
                    workflow_type="ticket", task_kind="normal", status="closed",
                    button_config_key="menu-a|auto|Move", instruction_button_key=original)
    db.add(ticket)
    db.commit()
    history = {c.name: deepcopy(getattr(ticket, c.name)) for c in ticket.__table__.columns}
    assert resolve_ticket_subject(db, ticket) == "Move"
    for parent in ("A", "B", "", "A"):
        assert move(http, row, parent).status_code == 200
        assert row.button_key == original
    assert resolve_ticket_subject(db, ticket) == "A → Move"
    assert build_ticket_subject_map(db, [ticket])[ticket.id] == "A → Move"
    # This records existing display behavior; the stored history is untouched.
    assert {name: getattr(ticket, name) for name in history} == history
    for workflow in ("task", "auto", "ticket"):
        save(db, row, workflow, key=original)
        assert row.button_key == f"menu-a|{workflow}|Move"
        assert resolve_button_row(db, original).id == row.id
        assert resolve_button_row(db, "menu-a|auto|Move").id == row.id
        assert db.execute(select(Ticket.id).join(BotButtonSetting, button_key_matches(Ticket.button_config_key))).scalar_one() == ticket.id
        assert telegram._button_from_callback(db, f"btnid:{row.id}", "menu-a").key == row.button_key
    save(db, row, "ticket", key=original, instruction_text="Edited")
    assert row.instruction_text == "Edited"
    assert move(http, row, "B").status_code == 200
    assert row.button_key == "menu-a|ticket|Move"


def test_old_layout_api_and_implicit_order_survive_move(db, http):
    a, b = folders(db)
    row = button(db, "Move")
    left = button(db, "Left")
    first = button(db, "First", parent="A")
    db.commit()
    root = build_level_layout_key("menu-a")
    result = http.put("/api/v1/settings/bot-buttons/layout", params={"menu_type": "menu-a", "level_key": root},
                     json={"rows": [[left.button_key, row.button_key], [b.button_key], [a.button_key]]})
    assert result.status_code == 200, result.text
    assert move(http, row, "A").status_code == 200
    assert layouts(db)[root] == [{"columns": 1, "button_keys": [left.button_key]},
        {"columns": 1, "button_keys": [b.button_key]}, {"columns": 1, "button_keys": [a.button_key]}]
    sub = build_level_layout_key("menu-a", "A")
    assert layouts(db)[sub] == [{"columns": 1, "button_keys": [first.button_key]}, {"columns": 1, "button_keys": [row.button_key]}]
    result = http.put("/api/v1/settings/bot-buttons/layout", params={"menu_type": "menu-a", "level_key": sub},
                     json={"rows": [[row.button_key, first.button_key]]})
    assert result.status_code == 200
    before = layouts(db)
    result = http.put("/api/v1/settings/bot-buttons/layout", params={"menu_type": "menu-a", "level_key": root},
                     json={"rows": [[row.button_key]]})
    assert result.status_code == 422
    db.rollback()
    assert layouts(db) == before


def test_commit_failure_rolls_back_paths_and_both_layouts(db, http, monkeypatch):
    from sqlalchemy.exc import IntegrityError
    folders(db)
    row = button(db, "Move")
    layout(db, "", [[row]])
    db.commit()
    before = layouts(db)

    def fail_commit():
        raise IntegrityError("forced conflict after flush", {}, Exception("conflict"))

    monkeypatch.setattr(db, "commit", fail_commit)
    result = move(http, row, "A")
    assert result.status_code == 409, result.text
    db.expire_all()
    assert row.parent_path == "" and row.button_path == "Move"
    assert layouts(db) == before


def test_stale_editor_cannot_restore_old_path_after_move(db, http, no_txt):
    from fastapi import HTTPException
    from api.app.routers import settings_bot_button_items as items
    from api.app.schemas import BotButtonInstructionUpdate
    folders(db)
    row = button(db, "Move", instruction_text="Keep")
    db.commit()
    assert move(http, row, "A").status_code == 200
    with pytest.raises(HTTPException) as error:
        items.save_bot_button_instruction(BotButtonInstructionUpdate(instruction_text="Stale"),
            button_key=row.button_key, menu_type=row.menu_type, workflow_type=row.workflow_type,
            button_path="Move", button_label=row.button_label, db=db, user=ADMIN)
    assert error.value.status_code == 409
    db.rollback()
    assert row.button_path == "A/Move" and row.parent_path == "A" and row.instruction_text == "Keep"
