from copy import deepcopy
from types import SimpleNamespace
import uuid

import pytest
from fastapi import HTTPException
from sqlalchemy import select

from api.app.models import BotButtonSetting, BotMenuLevelLayout, Client, Company, Ticket
from api.app.routers import settings_bot_button_items as items, settings_bot_button_structure as structure
from api.app.routers import telegram, tickets
from api.app.schemas import BotButtonInstructionUpdate
from api.app.services.button_config import resolve_button_row, build_level_layout_key
from api.app.services.ticket_subject import build_ticket_subject_map, resolve_ticket_subject
from api.app.services.ticket_list import apply_role_filter, apply_search_filter
from api.app.services import ticket_problem_flow as flow
from test_db_only_blockers import db, button, no_txt


ADMIN = SimpleNamespace(id="admin", role="admin")


def save(db, row, workflow, *, key=None, **payload):
    return items.save_bot_button_instruction(BotButtonInstructionUpdate(**payload),
        button_key=key or row.button_key, menu_type=row.menu_type, workflow_type=workflow,
        button_path=row.button_path, button_label=row.button_label, db=db, user=ADMIN)


def test_role_filter_preserves_call_center_access_after_button_key_change(db, no_txt):
    # Match PostgreSQL's Unicode lower() for Cyrillic menu names in SQLite.
    if db.get_bind().dialect.name == "sqlite":
        db.connection().connection.driver_connection.create_function(
            "lower", 1, lambda value: value.lower() if value is not None else None)
    call_center = button(db, "A", menu="Колл Центр", legacy_button_key="old-call-center-source")
    other_menu = button(db, "A", menu="Руководство", legacy_button_key="old-other-source")
    old_key = call_center.button_key
    db.add_all([Company(id="company", name="Company"),
                Client(id="client", company_id="company", display_name="Client")])
    links = {
        "call-center": (old_key, old_key),
        "legacy-config": (call_center.legacy_button_key, None),
        "legacy-instruction": (None, call_center.legacy_button_key),
        "other-menu": (other_menu.button_key, other_menu.legacy_button_key),
    }
    for ticket_id, (config_key, instruction_key) in links.items():
        db.add(Ticket(id=ticket_id, company_id="company", client_id="client", subject="Historical subject",
                      workflow_type="ticket", task_kind="normal", status="closed", agent_visible=True,
                      button_config_key=config_key, instruction_button_key=instruction_key))
    db.commit()
    users_and_expected = [
        (SimpleNamespace(id="observer", role="chiki_piki"),
         {"call-center", "legacy-config", "legacy-instruction"}),
        (SimpleNamespace(id="unassigned-agent", role="agent"), set()),
        (ADMIN, set(links)),
    ]

    # Both the old technical key and each historical legacy link retain access rules.
    for workflow in (None, "task", "ticket"):
        if workflow is not None:
            save(db, call_center, workflow)
            db.expire_all()
            assert call_center.button_key == f"Колл Центр|{workflow}|A"
        for user, expected in users_and_expected:
            assert set(db.execute(apply_role_filter(select(Ticket.id), user)).scalars()) == expected
        for ticket_id, historical_links in links.items():
            historic = db.get(Ticket, ticket_id)
            assert (historic.button_config_key, historic.instruction_button_key) == historical_links


@pytest.mark.parametrize("start,changes,is_db", [
    ("ticket", ["task", "ticket"], False),
    ("task", ["ticket", "auto"], False),
    ("task", ["ticket", "auto", "task", "ticket"], True),
])
def test_type_cycles_preserve_identity_layout_settings_and_history(db, no_txt, start, changes, is_db):
    row = button(db, "A", workflow=start, stable_button_id=str(uuid.uuid4()), is_db_created=is_db,
                 instruction_text="Instruction", client_text="Client", system_text="System",
                 label_override="Visible label", label_overridden=True,
                 files_json=["user.txt"], files_overridden=True, automation_key="send_files",
                 automation_overridden=True, conditional_files_json=[{"conditions": [], "files": ["condition.txt"]}],
                 conditional_files_overridden=True, confirmation_enabled=True, confirmation_text="Confirm",
                 button_style="success", freeze_on_create=True)
    row.button_label = "SearchLabel"
    if is_db:
        row.button_key = "menu-a|db|A"
    initial = row.button_key
    layout = BotMenuLevelLayout(id="layout", menu_type="menu-a", level_key=build_level_layout_key("menu-a"),
        layout_json=[{"columns": 3, "button_keys": ["left", initial, "right"]}])
    db.add_all([layout, Company(id="company", name="Company"),
                Client(id="client", company_id="company", display_name="Client")])
    db.commit()
    preserved = {column.name: deepcopy(getattr(row, column.name)) for column in row.__table__.columns
                 if column.name not in {"button_key", "workflow_type", "legacy_button_key", "updated_at"}}
    history = []
    for index, next_type in enumerate(changes):
        previous = row.button_key
        old = Ticket(id=f"old-{index}", company_id="company", client_id="client", subject="Historical subject",
                     workflow_type=row.workflow_type, task_kind="normal", status="closed",
                     button_config_key=previous, instruction_button_key=previous)
        db.add(old)
        db.commit()
        history.append((old.id, {column.name: deepcopy(getattr(old, column.name)) for column in old.__table__.columns}))
        # A stale browser tab must edit the same row, never insert a second setting.
        result = save(db, row, next_type, key=initial)
        db.expire_all()
        assert result["button_key"] == f"menu-a|{next_type}|A"
        assert row.button_key == result["button_key"]
        assert row.legacy_button_key == (initial if is_db else None)
        assert {field: getattr(row, field) for field in preserved} == preserved
        assert layout.layout_json == [{"columns": 3, "button_keys": ["left", row.button_key, "right"]}]
        assert db.query(BotButtonSetting).count() == 1
        assert items.get_bot_button_item(initial, db=db, user=ADMIN)["button_key"] == row.button_key
        for ticket_id, snapshot in history:
            historic = db.get(Ticket, ticket_id)
            assert {field: getattr(historic, field) for field in snapshot} == snapshot
            assert tickets._instruction_for_ticket(db, historic)[1] == "Instruction"
            assert telegram._button_setting_for_ticket(db, historic).id == row.id
            assert telegram._resolve_button_setting_row(db, historic).id == row.id
            assert telegram._resolve_effective_direct_files(db, historic, None) == ["user.txt"]
            assert resolve_ticket_subject(db, historic) == "A"
            assert build_ticket_subject_map(db, [historic])[historic.id] == "A"
            assert flow.get_problem_menu_for_ticket(db, historic,
                get_or_create_client_profile_fn=lambda *args: pytest.fail("Saved menu must resolve"))["rows"] == []
            assert telegram._button_from_callback(db, f"btn:{snapshot['button_config_key']}", "menu-a").key == row.button_key
        matched = db.execute(apply_search_filter(select(Ticket.id), "SearchLabel", db)).scalars().all()
        assert set(matched) == {ticket_id for ticket_id, _ in history}
        assert telegram._button_from_callback(db, f"btnid:{row.id}", "menu-a").key == row.button_key
    # History can still read archived settings, but Telegram must reject the callback.
    row.is_archived = True
    db.commit()
    assert tickets._instruction_for_ticket(db, db.get(Ticket, history[0][0]))[1] == "Instruction"
    assert telegram._button_from_callback(db, f"btn:{initial}", "menu-a") is None


def test_db_key_is_not_renamed_without_type_change(db, no_txt):
    row = button(db, "A", is_db_created=True, button_key="menu-a|db|A")
    db.commit()
    save(db, row, "ticket")
    assert row.button_key == "menu-a|db|A" and row.legacy_button_key is None


def test_occupied_db_legacy_requires_decision_without_any_changes(db, no_txt):
    row = button(db, "A", is_db_created=True, button_key="menu-a|db|A", legacy_button_key="older-custom-key")
    db.commit()
    with pytest.raises(HTTPException) as error:
        save(db, row, "task")
    assert error.value.detail["error"]["code"] == "button_legacy_key_occupied"
    db.rollback()
    assert row.button_key == "menu-a|db|A" and row.legacy_button_key == "older-custom-key"
    assert row.workflow_type == "ticket"


@pytest.mark.parametrize("archived", [False, True])
@pytest.mark.parametrize("collision", ["key", "alias"])
def test_key_and_alias_collisions_do_not_mutate_settings(db, no_txt, archived, collision):
    row = button(db, "A")
    other = button(db, "Other", is_archived=archived)
    if collision == "key":
        other.button_key = "menu-a|task|A"
    else:
        other.legacy_button_key = "menu-a|auto|A"
    db.commit()
    with pytest.raises(HTTPException) as error:
        save(db, row, "task")
    assert error.value.status_code == 409
    db.rollback()
    assert row.button_key == "menu-a|ticket|A" and row.workflow_type == "ticket"
    assert db.query(BotButtonSetting).count() == 2
    ambiguous = "menu-a|task|A" if collision == "key" else "menu-a|auto|A"
    assert resolve_button_row(db, ambiguous) is None


@pytest.mark.parametrize("workflow", ["ticket", "task", "auto"])
def test_new_buttons_use_workflow_keys_and_cannot_duplicate_the_path(db, workflow):
    result = structure.create_bot_button_item({"menu_type": "menu-a", "label": "A", "button_kind": workflow,
                                              "workflow_type": workflow}, db=db, user=ADMIN)
    row = db.query(BotButtonSetting).one()
    assert result["button_key"] == row.button_key == f"menu-a|{workflow}|A"
    identity = row.id, row.stable_button_id
    save(db, row, "auto" if workflow != "auto" else "ticket")
    with pytest.raises(HTTPException) as error:
        structure.create_bot_button_item({"menu_type": "menu-a", "label": "A", "button_kind": workflow,
                                          "workflow_type": workflow}, db=db, user=ADMIN)
    assert error.value.status_code == 409
    db.rollback()
    assert db.query(BotButtonSetting).count() == 1
    assert (row.id, row.stable_button_id) == identity


@pytest.mark.parametrize("legacy", [None, "menu-a|task|A"])
def test_db_original_alias_survives_compatible_legacy_slot(db, legacy):
    row = button(db, "A", is_db_created=True, button_key="menu-a|db|A", legacy_button_key=legacy)
    db.commit()
    save(db, row, "auto")
    assert row.legacy_button_key == "menu-a|db|A"
    assert resolve_button_row(db, "menu-a|db|A").id == row.id
    assert resolve_button_row(db, "menu-a|task|A").id == row.id


def test_russian_button_path_keeps_backslash_and_rebind_to_own_alias_is_noop(db):
    from api.app.routers.settings import rebind_bot_button_setting
    row = button(db, r"Выдать чип\брелок", workflow="task", menu="Руководство")
    db.commit()
    original = row.button_key
    result = save(db, row, "ticket")
    assert result["button_key"] == r"Руководство|ticket|Выдать чип\брелок"
    rebound = rebind_bot_button_setting(original, row.button_key, row.menu_type, row.workflow_type,
                                        row.button_path, row.button_label, db=db, user=ADMIN)
    assert rebound["moved"] is False
    unused_alias = row.button_key.replace("|ticket|", "|auto|")
    assert rebind_bot_button_setting(original, unused_alias, row.menu_type, row.workflow_type,
        row.button_path, row.button_label, db=db, user=ADMIN)["moved"] is False
    assert db.query(BotButtonSetting).count() == 1


def test_longer_key_that_does_not_fit_ticket_references_requires_migration(db):
    # auto -> ticket can exceed the existing VARCHAR(100) ticket link limit.
    name = "A" * (100 - len("menu-a|auto|"))
    row = button(db, name, workflow="auto")
    db.commit()
    old_key = row.button_key
    with pytest.raises(HTTPException) as error:
        save(db, row, "ticket")
    assert error.value.detail["error"]["code"] == "button_key_requires_migration"
    db.rollback()
    assert row.button_key == old_key and row.workflow_type == "auto"



def test_unknown_key_format_is_rejected_without_mutation(db):
    row = button(db, "A", button_key="custom-key")
    db.commit()
    with pytest.raises(HTTPException) as error:
        save(db, row, "task")
    assert error.value.detail["error"]["code"] == "button_key_requires_decision"
    db.rollback()
    assert row.button_key == "custom-key" and row.workflow_type == "ticket"


def test_restore_by_create_changes_type_without_losing_db_alias(db):
    row = button(db, "A", is_db_created=True, button_key="menu-a|db|A", is_archived=True,
                 currently_present=False, instruction_text="Keep")
    db.commit()
    identity = row.id
    result = structure.create_bot_button_item({"menu_type": "menu-a", "label": "A", "button_kind": "task",
                                              "workflow_type": "task"}, db=db, user=ADMIN)
    assert result["button_key"] == "menu-a|task|A"
    assert row.id == identity and row.legacy_button_key == "menu-a|db|A"
    assert row.instruction_text == "Keep" and db.query(BotButtonSetting).count() == 1


@pytest.mark.parametrize("old_kind", ["ticket", "db"])
def test_instruction_endpoint_returns_new_key_and_accepts_old_links(db, old_kind):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from api.app.auth import get_current_user
    from api.app.db import get_db
    row = button(db, "A", button_key=f"menu-a|{old_kind}|A", is_db_created=old_kind == "db",
                 stable_button_id=str(uuid.uuid4()), instruction_text="Keep")
    db.commit()
    old_key = row.button_key
    identity = row.id, row.stable_button_id
    app = FastAPI()
    app.include_router(items.router)
    app.dependency_overrides[get_db] = lambda: db
    app.dependency_overrides[get_current_user] = lambda: ADMIN
    with TestClient(app) as http:
        result = http.put("/api/v1/settings/bot-buttons/item/instruction",
            params={"button_key": old_key, "menu_type": "menu-a", "workflow_type": "task", "button_path": "A", "button_label": "A"},
            json={})
        assert result.status_code == 200, result.text
        assert result.json()["button_key"] == "menu-a|task|A"
        item = http.get("/api/v1/settings/bot-buttons/item", params={"button_key": old_key})
        assert item.status_code == 200
        assert item.json()["button_key"] == "menu-a|task|A"
        assert item.json()["instruction_text"] == "Keep"
    assert (row.id, row.stable_button_id) == identity
