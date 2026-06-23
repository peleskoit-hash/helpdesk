from __future__ import annotations

import uuid
from datetime import datetime

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker

from api.app.db import Base
from api.app.models import BotButtonSetting
from api.app.services import bot_button_materialization as materialization
from api.app.services.button_config import ButtonConfig, ButtonSpec, MenuSpec, TerminalButtonRef


def _session():
    engine = create_engine("sqlite:///:memory:", future=True)
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine, autoflush=False, autocommit=False)
    return Session()


def _seed_row(session, **overrides):
    now = datetime.utcnow()
    payload = {
        "id": str(uuid.uuid4()),
        "stable_button_id": str(uuid.uuid4()),
        "button_key": "menu-a|ticket|A",
        "legacy_button_key": None,
        "menu_type": "menu-a",
        "workflow_type": "ticket",
        "button_path": "A",
        "button_label": "A",
        "currently_present": True,
        "is_db_created": False,
        "button_kind": "terminal",
        "parent_path": "",
        "is_archived": False,
        "created_at": now,
        "updated_at": now,
    }
    payload.update(overrides)
    row = BotButtonSetting(
        **payload,
    )
    session.add(row)
    session.commit()
    return row


def _patch_sources(monkeypatch, *, txt_button: ButtonSpec):
    config = ButtonConfig(
        menus={"root": MenuSpec(key="root", title="menu-a", rows=((txt_button.key,),))},
        buttons={txt_button.key: txt_button},
        root_menu_key="root",
    )
    ref = TerminalButtonRef(
        source_button_key=txt_button.key,
        label=txt_button.label,
        workflow_type=txt_button.kind,
        full_path=txt_button.label,
        technical_key="menu-a|ticket|A" if txt_button.kind == "ticket" else f"menu-a|{txt_button.kind}|{txt_button.label}",
        instruction_text=txt_button.instruction_text,
    )
    monkeypatch.setattr(materialization, "available_menu_names", lambda: ("menu-a",))
    monkeypatch.setattr(materialization, "load_button_config", lambda menu_name: config)
    monkeypatch.setattr(materialization, "list_terminal_buttons", lambda menu_name: (ref,))


def _patch_group_child_sources(monkeypatch, *, duplicate_match: bool = False):
    child = ButtonSpec(key="child-a", label="Child", kind="task", system_text="sys")
    parent = ButtonSpec(key="parent-a", label="Parent", kind="group_task", subtasks=(child,))
    buttons = {parent.key: parent}
    menus = {"root": MenuSpec(key="root", title="menu-a", rows=((parent.key,),))}
    if duplicate_match:
        dup_parent = ButtonSpec(key="parent-b", label="Parent", kind="group_task", subtasks=(child,))
        buttons[dup_parent.key] = dup_parent
        menus["dup"] = MenuSpec(key="dup", title="dup", rows=((dup_parent.key,),))
        menus["root"] = MenuSpec(key="root", title="menu-a", rows=((parent.key,), (dup_parent.key,)))
    config = ButtonConfig(menus=menus, buttons=buttons, root_menu_key="root")
    ref = TerminalButtonRef(
        source_button_key="missing-child-key",
        label="Child",
        workflow_type="group_task_child",
        full_path="Parent/Child",
        technical_key="menu-a|group_task_child|Parent/Child",
        instruction_text=None,
    )
    monkeypatch.setattr(materialization, "available_menu_names", lambda: ("menu-a",))
    monkeypatch.setattr(materialization, "load_button_config", lambda menu_name: config)
    monkeypatch.setattr(materialization, "list_terminal_buttons", lambda menu_name: (ref,))


def _patch_submenu_sources(monkeypatch, *, duplicate_match: bool = False, submenu_text: str | None = "Подменю текст"):
    child_a = ButtonSpec(key="child-a", label="Leaf A", kind="ticket", text="Child text")
    submenu_a = ButtonSpec(key="submenu-a", label="Submenu", kind="submenu", submenu_key="submenu-key-a")
    buttons = {
        submenu_a.key: submenu_a,
        child_a.key: child_a,
    }
    menus = {
        "submenu-key-a": MenuSpec(key="submenu-key-a", title="Submenu", text=submenu_text, rows=((child_a.key,),)),
        "root": MenuSpec(key="root", title="menu-a", rows=((submenu_a.key,),)),
    }
    if duplicate_match:
        child_b = ButtonSpec(key="child-b", label="Leaf B", kind="ticket", text="Other child")
        submenu_b = ButtonSpec(key="submenu-b", label="Submenu", kind="submenu", submenu_key="submenu-key-b")
        buttons[submenu_b.key] = submenu_b
        buttons[child_b.key] = child_b
        menus["submenu-key-b"] = MenuSpec(key="submenu-key-b", title="Submenu", text="Dup text", rows=((child_b.key,),))
        menus["root"] = MenuSpec(key="root", title="menu-a", rows=((submenu_a.key,), (submenu_b.key,)))
    config = ButtonConfig(menus=menus, buttons=buttons, root_menu_key="root")
    refs = (
        TerminalButtonRef(
            source_button_key=child_a.key,
            label=child_a.label,
            workflow_type="ticket",
            full_path="Submenu/Leaf A",
            technical_key="menu-a|ticket|Submenu/Leaf A",
            instruction_text=None,
        ),
    )
    monkeypatch.setattr(materialization, "available_menu_names", lambda: ("menu-a",))
    monkeypatch.setattr(materialization, "load_button_config", lambda menu_name: config)
    monkeypatch.setattr(materialization, "list_terminal_buttons", lambda menu_name: refs)


@pytest.fixture
def txt_button():
    return ButtonSpec(
        key="txt-a",
        label="A",
        kind="ticket",
        text="Клиентский текст",
        confirmation_text="Подтвердите",
        system_text="Системный текст",
        automation_key="bitrix_deal_phone_lookup",
        file_path="folder/file.txt",
        file_rules=(
            {"field": "employee_type", "equals": "Стажёр", "file": "cond/one.txt"},
            {"field": "location_type", "equals": "Удалённый", "file": "cond/two.txt"},
        ),
    )


def test_txt_value_materializes_when_override_missing(monkeypatch, txt_button):
    session = _session()
    _patch_sources(monkeypatch, txt_button=txt_button)
    _seed_row(session, client_text=None, client_text_overridden=False)

    summary = materialization.materialize_bot_button_settings(session, apply=True)
    row = session.execute(select(BotButtonSetting).where(BotButtonSetting.button_key == "menu-a|ticket|A")).scalar_one()

    assert summary.rows_updated == 1
    assert row.client_text == "Клиентский текст"
    assert row.client_text_overridden is True


def test_existing_db_override_is_not_overwritten(monkeypatch, txt_button):
    session = _session()
    _patch_sources(monkeypatch, txt_button=txt_button)
    _seed_row(session, client_text="DB текст", client_text_overridden=True)

    summary = materialization.materialize_bot_button_settings(session, apply=True)
    row = session.execute(select(BotButtonSetting).where(BotButtonSetting.button_key == "menu-a|ticket|A")).scalar_one()

    assert row.client_text == "DB текст"
    assert row.client_text_overridden is True
    assert summary.skipped_overrides >= 1


def test_explicit_empty_override_is_preserved(monkeypatch, txt_button):
    session = _session()
    _patch_sources(monkeypatch, txt_button=txt_button)
    _seed_row(session, client_text=None, client_text_overridden=True)

    materialization.materialize_bot_button_settings(session, apply=True)
    row = session.execute(select(BotButtonSetting).where(BotButtonSetting.button_key == "menu-a|ticket|A")).scalar_one()

    assert row.client_text is None
    assert row.client_text_overridden is True


def test_empty_txt_values_materialize(monkeypatch):
    session = _session()
    button = ButtonSpec(key="txt-a", label="A", kind="ticket")
    _patch_sources(monkeypatch, txt_button=button)

    summary = materialization.materialize_bot_button_settings(session, apply=True)
    row = session.execute(select(BotButtonSetting).where(BotButtonSetting.button_key == "menu-a|ticket|A")).scalar_one()

    assert summary.rows_created == 1
    assert row.client_text is None
    assert row.client_text_overridden is True
    assert row.files_json == []
    assert row.files_overridden is True
    assert row.conditional_files_json == []
    assert row.conditional_files_overridden is True
    assert row.confirmation_text is None
    assert row.confirmation_enabled is False
    assert row.confirmation_overridden is True


def test_files_and_conditional_rules_keep_structure(monkeypatch, txt_button):
    session = _session()
    _patch_sources(monkeypatch, txt_button=txt_button)

    materialization.materialize_bot_button_settings(session, apply=True)
    row = session.execute(select(BotButtonSetting).where(BotButtonSetting.button_key == "menu-a|ticket|A")).scalar_one()

    assert row.files_json == ["folder/file.txt"]
    assert row.conditional_files_json == [
        {"conditions": [{"field": "employee_type", "operator": "eq", "value": "Стажёр"}], "files": ["cond/one.txt"]},
        {"conditions": [{"field": "location_type", "operator": "eq", "value": "Удалённый"}], "files": ["cond/two.txt"]},
    ]


def test_db_created_button_is_skipped(monkeypatch, txt_button):
    session = _session()
    _patch_sources(monkeypatch, txt_button=txt_button)
    _seed_row(session, is_db_created=True, client_text=None, client_text_overridden=False)

    summary = materialization.materialize_bot_button_settings(session, apply=True)
    row = session.execute(select(BotButtonSetting).where(BotButtonSetting.button_key == "menu-a|ticket|A")).scalar_one()

    assert summary.skipped_db_created_or_archived == 1
    assert row.client_text is None
    assert row.client_text_overridden is False


def test_second_run_is_idempotent(monkeypatch, txt_button):
    session = _session()
    _patch_sources(monkeypatch, txt_button=txt_button)

    first = materialization.materialize_bot_button_settings(session, apply=True)
    second = materialization.materialize_bot_button_settings(session, apply=True)

    assert first.rows_created == 1
    assert second.rows_created == 0
    assert second.rows_updated == 0
    assert sum(second.field_changes.values()) == 0


def test_apply_rolls_back_on_error(monkeypatch, txt_button):
    session = _session()
    _patch_sources(monkeypatch, txt_button=txt_button)
    _seed_row(session, client_text=None, client_text_overridden=False)

    original = materialization._set_field_if_needed
    calls = {"count": 0}

    def broken(*args, **kwargs):
        calls["count"] += 1
        if calls["count"] == 2:
            raise RuntimeError("boom")
        return original(*args, **kwargs)

    monkeypatch.setattr(materialization, "_set_field_if_needed", broken)

    with pytest.raises(RuntimeError):
        materialization.materialize_bot_button_settings(session, apply=True)

    session.rollback()
    row = session.execute(select(BotButtonSetting).where(BotButtonSetting.button_key == "menu-a|ticket|A")).scalar_one()
    assert row.client_text is None
    assert row.client_text_overridden is False


def test_group_task_child_maps_without_source_button_key(monkeypatch):
    session = _session()
    _patch_group_child_sources(monkeypatch, duplicate_match=False)

    summary = materialization.materialize_bot_button_settings(session, apply=False)

    assert summary.skipped_ambiguous == 0
    assert len(summary.warning_details) == 0
    assert summary.buttons_found == 2


def test_group_task_child_ambiguous_mapping_warns(monkeypatch):
    session = _session()
    _patch_group_child_sources(monkeypatch, duplicate_match=True)

    summary = materialization.materialize_bot_button_settings(session, apply=False)

    child_warnings = [item for item in summary.warning_details if item.get("child_button_key")]
    assert summary.skipped_ambiguous >= 1
    assert len(child_warnings) == 1
    assert "ambiguous" in child_warnings[0]["reason"]


def test_apply_is_blocked_when_warnings_exist(monkeypatch):
    session = _session()
    _patch_group_child_sources(monkeypatch, duplicate_match=True)

    summary = materialization.materialize_bot_button_settings(session, apply=True)

    assert summary.apply_blocked is True
    assert summary.rows_created == 0
    assert summary.rows_updated == 0
    assert session.execute(select(BotButtonSetting)).scalars().all() == []


def test_submenu_text_materializes_into_client_text(monkeypatch):
    session = _session()
    _patch_submenu_sources(monkeypatch, submenu_text="Текст подменю")
    _seed_row(
        session,
        button_key="menu-a|submenu|Submenu",
        workflow_type="submenu",
        button_kind="section",
        button_path="Submenu",
        button_label="Submenu",
        client_text=None,
        client_text_overridden=False,
    )

    summary = materialization.materialize_bot_button_settings(session, apply=True)
    row = session.execute(select(BotButtonSetting).where(BotButtonSetting.button_key == "menu-a|submenu|Submenu")).scalar_one()

    assert row.client_text == "Текст подменю"
    assert row.client_text_overridden is True
    assert summary.submenus_found == 1
    assert summary.submenu_text_changes == 1


def test_submenu_db_override_is_not_overwritten(monkeypatch):
    session = _session()
    _patch_submenu_sources(monkeypatch, submenu_text="TXT подменю")
    _seed_row(
        session,
        button_key="menu-a|submenu|Submenu",
        workflow_type="submenu",
        button_kind="section",
        button_path="Submenu",
        button_label="Submenu",
        client_text="DB подменю",
        client_text_overridden=True,
    )

    summary = materialization.materialize_bot_button_settings(session, apply=True)
    row = session.execute(select(BotButtonSetting).where(BotButtonSetting.button_key == "menu-a|submenu|Submenu")).scalar_one()

    assert row.client_text == "DB подменю"
    assert summary.submenu_skipped_overrides == 1


def test_empty_submenu_text_materializes_as_empty_override(monkeypatch):
    session = _session()
    _patch_submenu_sources(monkeypatch, submenu_text=None)

    summary = materialization.materialize_bot_button_settings(session, apply=True)
    row = session.execute(select(BotButtonSetting).where(BotButtonSetting.button_key == "menu-a|submenu|Submenu")).scalar_one()

    assert row.client_text is None
    assert row.client_text_overridden is True
    assert summary.submenu_rows_to_create == 1
    assert summary.submenu_text_changes == 1


def test_db_created_submenu_is_skipped(monkeypatch):
    session = _session()
    _patch_submenu_sources(monkeypatch, submenu_text="TXT подменю")
    _seed_row(
        session,
        button_key="menu-a|submenu|Submenu",
        workflow_type="submenu",
        button_kind="section",
        button_path="Submenu",
        button_label="Submenu",
        is_db_created=True,
        client_text=None,
        client_text_overridden=False,
    )

    summary = materialization.materialize_bot_button_settings(session, apply=True)
    row = session.execute(select(BotButtonSetting).where(BotButtonSetting.button_key == "menu-a|submenu|Submenu")).scalar_one()

    assert row.client_text is None
    assert row.client_text_overridden is False
    assert summary.submenu_skipped_db_created == 1


def test_ambiguous_submenu_mapping_blocks_apply(monkeypatch):
    session = _session()
    _patch_submenu_sources(monkeypatch, duplicate_match=True)

    summary = materialization.materialize_bot_button_settings(session, apply=True)

    assert summary.apply_blocked is True
    assert summary.submenu_skipped_ambiguous == 1
    assert session.execute(select(BotButtonSetting)).scalars().all() == []


def test_submenu_second_run_is_idempotent(monkeypatch):
    session = _session()
    _patch_submenu_sources(monkeypatch, submenu_text="TXT подменю")

    first = materialization.materialize_bot_button_settings(session, apply=True)
    second = materialization.materialize_bot_button_settings(session, apply=True)

    assert first.submenu_text_changes == 1
    assert second.submenu_text_changes == 0
    assert second.rows_updated == 0
