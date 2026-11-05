from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from api.app.auth import get_current_user
from api.app.db import get_db
from api.app.routers import settings_bot_button_items as items, settings_bot_button_structure as structure
from test_db_only_blockers import db, button


@pytest.mark.parametrize("workflow,kind", [("ticket", "terminal"), ("submenu", "section"), ("group_task", "group_parent")])
def test_saved_label_structure_reopen_and_reset_preserve_original_and_identity(db, monkeypatch, workflow, kind):
    monkeypatch.setattr(items, "_list_available_button_files", lambda: [])
    monkeypatch.setattr(items, "get_automation_registry", lambda: {})
    row = button(db, "Original", workflow=workflow, kind=kind)
    db.commit()
    identity = (row.id, row.button_key, row.button_path, row.parent_path)
    app = FastAPI()
    app.include_router(items.router)
    app.include_router(structure.router)
    app.dependency_overrides[get_db] = lambda: db
    app.dependency_overrides[get_current_user] = lambda: SimpleNamespace(id="admin", role="admin")
    params = {"button_key": row.button_key, "menu_type": row.menu_type, "workflow_type": workflow,
              "button_path": row.button_path, "button_label": row.button_label}
    with TestClient(app) as http:
        for renamed in ["New label", "Second label", None]:
            response = http.put("/api/v1/settings/bot-buttons/item/instruction", params=params,
                                json={"label_overridden": renamed is not None, "label_override": renamed})
            assert response.status_code == 200, response.text
            db.expire_all()
            item = http.get("/api/v1/settings/bot-buttons/item", params={"button_key": row.button_key, "menu_type": row.menu_type})
            assert item.status_code == 200, item.text
            effective = renamed or "Original"
            assert item.json()["button_label_effective"] == effective
            tree = http.get(f"/api/v1/settings/bot-buttons/menu-types/{row.menu_type}/structure")
            assert tree.status_code == 200, tree.text
            node = tree.json()["tree_rows"][0]["buttons"][0]
            assert node["label"] == effective
            assert (node["button_id"], node["technical_key"], node["full_path"], node["parent_path"]) == identity
            assert row.button_label == "Original"
            # Existing clients pass the displayed (effective) label on subsequent saves/reset.
            params["button_label"] = effective


@pytest.mark.parametrize("layout_mode", ["none", "partial", "manual"])
@pytest.mark.parametrize("nested", [False, True])
def test_submenu_rename_preserves_order_paths_and_wrong_problem(db, monkeypatch, layout_mode, nested):
    from datetime import datetime
    from api.app.models import BotMenuLevelLayout, Ticket, Client, Company
    from api.app.routers import telegram, tickets
    from api.app.services import ticket_problem_flow as flow
    from api.app.services.button_config import build_level_layout_key
    from api.app.services.ticket_subject import build_ticket_subject_map, resolve_ticket_subject

    monkeypatch.setattr(items, "_list_available_button_files", lambda: [])
    monkeypatch.setattr(items, "get_automation_registry", lambda: {})
    sent = []
    monkeypatch.setattr(telegram, "send_message", lambda *a, **kw: sent.append(kw))
    outer = button(db, "Outer", workflow="submenu", kind="section",
                   label_overridden=True, label_override="Внешнее") if nested else None
    parent = outer.button_path if outer else ""
    before = button(db, "Before", parent=parent, updated_at=datetime(2026, 1, 1))
    folder = button(db, "Проблема с техникой", parent=parent, workflow="submenu", kind="section",
                    updated_at=datetime(2026, 1, 2), stable_button_id="stable-folder")
    after = button(db, "After", parent=parent, updated_at=datetime(2026, 1, 3))
    leaf = button(db, "Проблема с интернетом", parent=folder.button_path)
    current = button(db, "Current", parent=folder.button_path)
    ticket = Ticket(id="ticket", company_id="company", client_id="client", subject="Current",
                    workflow_type="ticket", task_kind="normal", status="new", is_frozen=False,
                    instruction_button_key=current.button_key)
    db.add(ticket)
    db.add_all([Company(id="company", name="Company"),
                Client(id="client", company_id="company", display_name="Client")])
    if layout_mode != "none":
        keys = [after.button_key, folder.button_key, before.button_key] if layout_mode == "manual" else [before.button_key]
        db.add(BotMenuLevelLayout(id="layout", menu_type=folder.menu_type,
               level_key=build_level_layout_key(folder.menu_type, parent),
               layout_json=[{"columns": 3 if layout_mode == "manual" else 1, "button_keys": keys}]))
    db.commit()
    identities = [(r.id, r.stable_button_id, r.button_key, r.button_path, r.parent_path) for r in [folder, leaf, current]]
    original_subject = ticket.subject
    level_key = build_level_layout_key(folder.menu_type, parent)
    original_rows = structure._rows_for_level_key(db, folder.menu_type, level_key)
    expected_keys = [[n["technical_key"] for n in r["buttons"]] for r in original_rows]
    expected_columns = [r.get("columns", 1) for r in original_rows]
    app = FastAPI()
    app.include_router(items.router)
    app.include_router(structure.router)
    app.include_router(tickets.router)
    actor = SimpleNamespace(id="admin", role="admin")
    app.dependency_overrides[get_db] = lambda: db
    app.dependency_overrides[get_current_user] = lambda: actor
    monkeypatch.setattr(tickets, "_get_ticket_or_404", lambda *a: ticket)
    notices = []
    monkeypatch.setattr(tickets, "_send_to_telegram_client", lambda *a: notices.append(a[-1]) or {"result": {"message_id": 5}})
    monkeypatch.setattr(tickets, "_remove_client_close_button", lambda *a: None)
    with TestClient(app) as http:
        for index, label in enumerate(["Техническая проблема", "Техническая проблема повторно"]):
            response = http.put("/api/v1/settings/bot-buttons/item/instruction",
                params={"button_key": folder.button_key, "menu_type": folder.menu_type,
                        "workflow_type": "submenu", "button_path": folder.button_path, "button_label": folder.button_label},
                json={"label_overridden": True, "label_override": label})
            assert response.status_code == 200, response.text
            db.expire_all()
            rows = structure._rows_for_level_key(db, folder.menu_type, level_key)
            assert [[n["technical_key"] for n in r["buttons"]] for r in rows] == expected_keys
            assert [r.get("columns", 1) for r in rows] == expected_columns
            telegram._send_problem_menu_with_visibility(db, "test", menu_name=folder.menu_type,
                                                       menu_key=outer.button_key if outer else None)
            assert [[n["callback_data"] for n in r] for r in sent[-1]["reply_markup"]["inline_keyboard"] if r[0]["callback_data"] != "back"][:len(expected_keys)] == [
                [f"menuid:{folder.id}" if key == folder.button_key else f"btnid:{before.id if key == before.button_key else after.id}" for key in r] for r in expected_keys]
            expected_path = ("Внешнее → " if nested else "") + label + " → Проблема с интернетом"
            new_ticket = SimpleNamespace(id="new", task_kind="normal", instruction_button_key=leaf.button_key,
                                         button_config_key=None, subject="stored")
            assert resolve_ticket_subject(db, new_ticket) == expected_path
            assert build_ticket_subject_map(db, [new_ticket])["new"] == expected_path
            created = Ticket(id=label, company_id="company", client_id="client", subject="Stored subject",
                             instruction_button_key=leaf.button_key, workflow_type="ticket", task_kind="normal",
                             status="new", is_frozen=False, ticket_number=123 + index, assignee_id="admin")
            db.add(created)
            db.commit()
            response = http.get("/api/v1/tickets/active", params={"assignee_ids": "admin"})
            assert response.status_code == 200, response.text
            assert next(item for item in response.json()["items"] if item["id"] == created.id)["subject"] == expected_path
            assert created.subject == "Stored subject"
            for role in ["agent", "admin"]:
                actor.role = role
                response = http.get("/api/v1/tickets/ticket/problem-menu")
                assert response.status_code == 200, response.text
                config = flow._db_button_config(db, folder.menu_type)
                assert flow.button_full_path_label(config, leaf.button_key) == expected_path
                tree = response.json()["rows"]
                if outer:
                    tree = tree[0]["buttons"][0]["children"]
                assert [[n["key"] for n in r["buttons"]] for r in tree] == expected_keys
                node = next(n for r in tree for n in r["buttons"] if n["key"] == folder.button_key)
                assert node["label"] == label
                assert [n["key"] for r in node["children"] for n in r["buttons"]] == [leaf.button_key]
                rejected = http.post("/api/v1/tickets/ticket/wrong-problem", json={"button_key": folder.button_key})
                assert rejected.status_code == 409
            actor.role = "admin"
        result = http.post("/api/v1/tickets/ticket/wrong-problem", json={"button_key": leaf.button_key})
        assert result.status_code == 200, result.text
        assert result.json()["label"] == expected_path
        assert ticket.wrong_problem_button_key == leaf.button_key
        assert expected_path in notices[-1]
    assert [(r.id, r.stable_button_id, r.button_key, r.button_path, r.parent_path) for r in [folder, leaf, current]] == identities
    assert ticket.subject == original_subject


@pytest.mark.parametrize("role", ["agent", "admin"])
def test_wrong_problem_excludes_matching_labels_across_submenus(db, monkeypatch, role):
    from api.app.models import Ticket
    from api.app.routers import tickets

    first = button(db, "First", workflow="submenu", kind="section")
    second = button(db, "Second", workflow="submenu", kind="section")
    current = button(db, "Current", parent=first.button_path)
    same_level = button(db, "Different key", parent=first.button_path,
                        label_overridden=True, label_override="Одинаковая проблема")
    other_level = button(db, "Одинаковая проблема", parent=second.button_path)
    first_choice = button(db, "Available first", parent=first.button_path)
    second_choice = button(db, "Available second", parent=second.button_path)
    assert len({current.button_key, same_level.button_key, other_level.button_key}) == 3
    ticket = Ticket(id="ticket", company_id="company", client_id="client", subject="Одинаковая проблема",
                    instruction_button_key=current.button_key, workflow_type="ticket", task_kind="normal",
                    status="new", is_frozen=False)
    db.add(ticket)
    db.commit()
    monkeypatch.setattr(tickets, "_get_ticket_or_404", lambda *args: ticket)
    app = FastAPI()
    app.include_router(tickets.router)
    app.dependency_overrides[get_db] = lambda: db
    app.dependency_overrides[get_current_user] = lambda: SimpleNamespace(id=role, role=role)
    with TestClient(app) as http:
        response = http.get("/api/v1/tickets/ticket/problem-menu")
    assert response.status_code == 200, response.text
    submenus = {node["key"]: node for row in response.json()["rows"] for node in row["buttons"]}
    assert set(submenus) == {first.button_key, second.button_key}
    for parent, available in [(first, first_choice), (second, second_choice)]:
        children = [node for row in submenus[parent.button_key]["children"] for node in row["buttons"]]
        assert [node["key"] for node in children] == [available.button_key]
