from __future__ import annotations

from datetime import datetime
from types import SimpleNamespace
import uuid

import pytest
from fastapi import HTTPException
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker

from api.app.db import Base
from api.app.models import Client, ClientProfile, ClientCardBlockConfig, ClientCardFieldConfig, ClientCardRuleConfig, Company, Ticket, TicketStatus, User
from api.app.routers import settings_client_card, tickets
from api.app.schemas import ClientCardBlockUpdate, ClientCardFieldCreate, ClientCardFieldUpdate, ClientCardMovePayload, ClientCardRuleAction, ClientCardRuleCreate, ClientCardRuleUpdate, ClientProfileUpdate
from api.app.services import seed_data


def _session():
    engine = create_engine("sqlite:///:memory:", future=True)
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine, autoflush=False, autocommit=False)
    return Session()


def _admin():
    return SimpleNamespace(id="admin-1", role="admin")


def _block(key: str, title: str, sort_order: int) -> ClientCardBlockConfig:
    now = datetime.utcnow()
    return ClientCardBlockConfig(
        id=str(uuid.uuid4()),
        key=key,
        title=title,
        color_scheme="gray",
        sort_order=sort_order,
        is_active=True,
        created_at=now,
        updated_at=now,
    )


def _field(key: str, label: str, block_key: str, sort_order: int, *, is_system: bool) -> ClientCardFieldConfig:
    now = datetime.utcnow()
    return ClientCardFieldConfig(
        id=str(uuid.uuid4()),
        key=key,
        label=label,
        field_type="text",
        block_key=block_key,
        sort_order=sort_order,
        is_visible=True,
        is_visible_in_editor=not is_system,
        is_editable=not is_system,
        is_required=False,
        is_system=is_system,
        created_at=now,
        updated_at=now,
    )


def test_cannot_delete_system_block():
    session = _session()
    system_block = _block("tg", "Telegram", 10)
    session.add(system_block)
    session.commit()

    with pytest.raises(HTTPException) as exc:
        settings_client_card.delete_client_card_block(system_block.id, db=session, user=_admin())

    assert exc.value.status_code == 403
    assert session.get(ClientCardBlockConfig, system_block.id) is not None


def test_delete_custom_block_removes_custom_fields():
    session = _session()
    system_block = _block("tg", "Telegram", 10)
    custom_block = _block("custom_block_123", "123", 20)
    custom_field = _field("custom_note", "Note", custom_block.key, 10, is_system=False)
    session.add_all([system_block, custom_block, custom_field])
    session.commit()

    payload = settings_client_card.delete_client_card_block(custom_block.id, db=session, user=_admin())

    assert [block.key for block in payload.blocks] == ["tg"]
    assert session.get(ClientCardBlockConfig, custom_block.id) is None
    assert session.get(ClientCardFieldConfig, custom_field.id) is None


def test_cannot_delete_system_field():
    session = _session()
    block = _block("tg", "Telegram", 10)
    system_field = _field("telegram_username", "@username", block.key, 10, is_system=True)
    session.add_all([block, system_field])
    session.commit()

    with pytest.raises(HTTPException) as exc:
        settings_client_card.delete_client_card_field(system_field.id, db=session, user=_admin())

    assert exc.value.status_code == 403
    assert session.get(ClientCardFieldConfig, system_field.id) is not None


def test_delete_custom_field_keeps_profile_data_contract_to_config_only():
    session = _session()
    block = _block("custom_block_123", "123", 10)
    custom_field = _field("custom_note", "Note", block.key, 10, is_system=False)
    session.add_all([block, custom_field])
    session.commit()

    payload = settings_client_card.delete_client_card_field(custom_field.id, db=session, user=_admin())

    assert payload.blocks[0].fields == []
    assert session.get(ClientCardFieldConfig, custom_field.id) is None


def test_patch_custom_field_with_same_block_key_passes():
    session = _session()
    block = _block("custom_block_123", "123", 10)
    custom_field = _field("custom_note", "Note", block.key, 10, is_system=False)
    session.add_all([block, custom_field])
    session.commit()

    result = settings_client_card.update_client_card_field(
        custom_field.id,
        ClientCardFieldUpdate(label="Updated", block_key=block.key),
        db=session,
        user=_admin(),
    )

    assert result.label == "Updated"
    assert result.block_key == block.key
    assert session.get(ClientCardFieldConfig, custom_field.id).block_key == block.key


def test_patch_custom_field_with_other_block_key_returns_400_and_keeps_block():
    session = _session()
    source_block = _block("custom_block_123", "123", 10)
    target_block = _block("custom_block_other", "Other", 20)
    custom_field = _field("custom_note", "Note", source_block.key, 10, is_system=False)
    session.add_all([source_block, target_block, custom_field])
    session.commit()

    with pytest.raises(HTTPException) as exc:
        settings_client_card.update_client_card_field(
            custom_field.id,
            ClientCardFieldUpdate(block_key=target_block.key),
            db=session,
            user=_admin(),
        )

    assert exc.value.status_code == 400
    assert exc.value.detail["error"]["code"] == "field_block_change_not_supported"
    assert session.get(ClientCardFieldConfig, custom_field.id).block_key == source_block.key


def test_patch_system_field_with_other_block_key_returns_400_and_keeps_block():
    session = _session()
    source_block = _block("tg", "Telegram", 10)
    target_block = _block("profile", "Profile", 20)
    system_field = _field("telegram_username", "@username", source_block.key, 10, is_system=True)
    session.add_all([source_block, target_block, system_field])
    session.commit()

    with pytest.raises(HTTPException) as exc:
        settings_client_card.update_client_card_field(
            system_field.id,
            ClientCardFieldUpdate(block_key=target_block.key),
            db=session,
            user=_admin(),
        )

    assert exc.value.status_code == 400
    assert exc.value.detail["error"]["code"] == "field_block_change_not_supported"
    assert session.get(ClientCardFieldConfig, system_field.id).block_key == source_block.key


def test_patch_existing_field_ignores_extra_key_payload():
    session = _session()
    block = _block("custom_block_123", "123", 10)
    custom_field = _field("custom_note", "Note", block.key, 10, is_system=False)
    session.add_all([block, custom_field])
    session.commit()

    result = settings_client_card.update_client_card_field(
        custom_field.id,
        ClientCardFieldUpdate(label="Updated", key="custom_other"),
        db=session,
        user=_admin(),
    )

    assert result.key == "custom_note"
    assert result.label == "Updated"
    assert session.get(ClientCardFieldConfig, custom_field.id).key == "custom_note"


def test_patch_block_accepts_named_color():
    session = _session()
    block = _block("custom_block_123", "123", 10)
    session.add(block)
    session.commit()

    result = settings_client_card.update_client_card_block(
        block.id,
        ClientCardBlockUpdate(color_scheme="blue"),
        db=session,
        user=_admin(),
    )

    assert result.color_scheme == "blue"
    assert session.get(ClientCardBlockConfig, block.id).color_scheme == "blue"


def test_block_visibility_flags_serialize_and_patch_independently():
    session = _session()
    block = _block("custom_block_123", "123", 10)
    session.add(block)
    session.commit()

    result = settings_client_card.update_client_card_block(
        block.id,
        ClientCardBlockUpdate(show_in_card=False, show_in_editor=True, show_title=False),
        db=session,
        user=_admin(),
    )
    payload = settings_client_card.get_client_card_config(db=session, user=_admin())

    assert result.show_in_card is False
    assert result.show_in_editor is True
    assert result.show_title is False
    assert payload.blocks[0].show_in_card is False
    assert payload.blocks[0].show_in_editor is True
    assert payload.blocks[0].show_title is False


def test_patch_block_accepts_hex_color_and_serializes_it():
    session = _session()
    block = _block("custom_block_123", "123", 10)
    session.add(block)
    session.commit()

    result = settings_client_card.update_client_card_block(
        block.id,
        ClientCardBlockUpdate(color_scheme="#DBEAFE"),
        db=session,
        user=_admin(),
    )
    payload = settings_client_card.get_client_card_config(db=session, user=_admin())

    assert result.color_scheme == "#dbeafe"
    assert payload.blocks[0].color_scheme == "#dbeafe"


def test_patch_block_rejects_invalid_color():
    session = _session()
    block = _block("custom_block_123", "123", 10)
    session.add(block)
    session.commit()

    with pytest.raises(HTTPException) as exc:
        settings_client_card.update_client_card_block(
            block.id,
            ClientCardBlockUpdate(color_scheme="url(javascript:bad)"),
            db=session,
            user=_admin(),
        )

    assert exc.value.status_code == 422
    assert exc.value.detail["error"]["code"] == "color_scheme_invalid"
    assert session.get(ClientCardBlockConfig, block.id).color_scheme == "gray"


def test_create_field_still_accepts_selected_block_key():
    session = _session()
    block = _block("custom_block_123", "123", 10)
    session.add(block)
    session.commit()

    result = settings_client_card.create_client_card_field(
        ClientCardFieldCreate(label="Note", field_type="text", block_key=block.key),
        db=session,
        user=_admin(),
    )

    assert result.block_key == block.key
    assert result.key.startswith("custom_note")


def test_patch_field_updates_card_and_editor_visibility_independently():
    session = _session()
    block = _block("custom_block_123", "123", 10)
    custom_field = _field("custom_note", "Note", block.key, 10, is_system=False)
    session.add_all([block, custom_field])
    session.commit()

    result = settings_client_card.update_client_card_field(
        custom_field.id,
        ClientCardFieldUpdate(is_visible=False, is_visible_in_editor=True),
        db=session,
        user=_admin(),
    )
    payload = settings_client_card.get_client_card_config(db=session, user=_admin())

    assert result.is_visible is False
    assert result.is_visible_in_editor is True
    assert payload.blocks[0].fields[0].is_visible is False
    assert payload.blocks[0].fields[0].is_visible_in_editor is True


def test_patch_system_field_can_change_editor_visibility_but_not_editable():
    session = _session()
    block = _block("tg", "Telegram", 10)
    system_field = _field("telegram_username", "@username", block.key, 10, is_system=True)
    session.add_all([block, system_field])
    session.commit()

    result = settings_client_card.update_client_card_field(
        system_field.id,
        ClientCardFieldUpdate(is_visible_in_editor=True),
        db=session,
        user=_admin(),
    )

    assert result.is_visible_in_editor is True
    with pytest.raises(HTTPException) as exc:
        settings_client_card.update_client_card_field(
            system_field.id,
            ClientCardFieldUpdate(is_editable=True),
            db=session,
            user=_admin(),
        )

    assert exc.value.status_code == 403
    assert exc.value.detail["error"]["code"] == "readonly_system_field_editable"
    assert session.get(ClientCardFieldConfig, system_field.id).is_editable is False


def test_create_field_sets_editor_visibility_and_selected_block_key():
    session = _session()
    block = _block("custom_block_123", "123", 10)
    session.add(block)
    session.commit()

    result = settings_client_card.create_client_card_field(
        ClientCardFieldCreate(label="Hidden Note", field_type="text", block_key=block.key, is_visible=False, is_visible_in_editor=True),
        db=session,
        user=_admin(),
    )

    assert result.block_key == block.key
    assert result.is_visible is False
    assert result.is_visible_in_editor is True


def test_create_rule_equals_show():
    session = _session()
    block = _block("department", "Department", 10)
    condition = _field("location_type", "Location", block.key, 10, is_system=True)
    target = _field("anydesk", "AnyDesk", block.key, 20, is_system=True)
    session.add_all([block, condition, target])
    session.commit()

    result = settings_client_card.create_client_card_rule(
        ClientCardRuleCreate(
            name="Show AnyDesk",
            condition_field_key="location_type",
            condition_operator="equals",
            condition_value="Remote",
            actions=[ClientCardRuleAction(target_key="anydesk", action="show", scope="editor")],
        ),
        db=session,
        user=_admin(),
    )

    assert result.name == "Show AnyDesk"
    assert result.actions[0].target_key == "anydesk"


def test_ticket_profile_custom_fields_patch_preserves_omitted_and_clears_explicit_null():
    session = _session()
    now = datetime.utcnow()
    company = Company(id="company-1", name="Company")
    user = User(id="admin-1", company_id=company.id, full_name="Admin", role="admin")
    client = Client(id="client-1", company_id=company.id, display_name="Client")
    ticket = Ticket(
        id="ticket-1",
        company_id=company.id,
        client_id=client.id,
        subject="Subject",
        status=TicketStatus.NEW.value,
        created_at=now,
        updated_at=now,
    )
    profile = ClientProfile(
        id="profile-1",
        client_id=client.id,
        custom_fields={"custom_a": "old-a", "custom_b": "old-b", "custom_hidden": "keep"},
        created_at=now,
        updated_at=now,
    )
    block = _block("custom_block_1", "Custom", 10)
    field_a = _field("custom_a", "A", block.key, 10, is_system=False)
    field_b = _field("custom_b", "B", block.key, 20, is_system=False)
    hidden = _field("custom_hidden", "Hidden", block.key, 30, is_system=False)
    session.add_all([company, user, client, ticket, profile, block, field_a, field_b, hidden])
    session.commit()

    tickets.update_client_profile(
        ticket.id,
        ClientProfileUpdate(custom_fields={"custom_a": "new-a"}),
        db=session,
        user=_admin(),
    )
    session.refresh(profile)
    assert profile.custom_fields == {"custom_a": "new-a", "custom_b": "old-b", "custom_hidden": "keep"}

    tickets.update_client_profile(
        ticket.id,
        ClientProfileUpdate(custom_fields={}),
        db=session,
        user=_admin(),
    )
    session.refresh(profile)
    assert profile.custom_fields == {"custom_a": "new-a", "custom_b": "old-b", "custom_hidden": "keep"}

    tickets.update_client_profile(
        ticket.id,
        ClientProfileUpdate(custom_fields={"custom_a": None}),
        db=session,
        user=_admin(),
    )
    session.refresh(profile)
    assert profile.custom_fields == {"custom_b": "old-b", "custom_hidden": "keep"}


def test_ticket_profile_patch_preserves_omitted_system_fields_and_clears_explicit_null():
    session = _session()
    now = datetime.utcnow()
    company = Company(id="company-1", name="Company")
    user = User(id="admin-1", company_id=company.id, full_name="Admin", role="admin")
    client = Client(id="client-1", company_id=company.id, display_name="Client")
    ticket = Ticket(
        id="ticket-1",
        company_id=company.id,
        client_id=client.id,
        subject="Subject",
        status=TicketStatus.NEW.value,
        created_at=now,
        updated_at=now,
    )
    profile = ClientProfile(
        id="profile-1",
        client_id=client.id,
        contact_name="Old",
        email="client@example.test",
        phone="+70000000000",
        verification_status="manual",
        employee_type="Employee",
        external_user_id="telegram-1",
        created_at=now,
        updated_at=now,
    )
    session.add_all([company, user, client, ticket, profile])
    session.commit()

    tickets.update_client_profile(
        ticket.id,
        ClientProfileUpdate(contact_name="New"),
        db=session,
        user=_admin(),
    )
    session.refresh(profile)

    assert profile.contact_name == "New"
    assert profile.email == "client@example.test"
    assert profile.phone == "+70000000000"
    assert profile.verification_status == "manual"
    assert profile.employee_type == "Employee"
    assert profile.external_user_id == "telegram-1"

    tickets.update_client_profile(
        ticket.id,
        ClientProfileUpdate(email=None),
        db=session,
        user=_admin(),
    )
    session.refresh(profile)

    assert profile.email is None
    assert profile.phone == "+70000000000"


def test_create_rule_rejects_unknown_condition_field():
    session = _session()
    block = _block("department", "Department", 10)
    target = _field("anydesk", "AnyDesk", block.key, 20, is_system=True)
    session.add_all([block, target])
    session.commit()

    with pytest.raises(HTTPException) as exc:
        settings_client_card.create_client_card_rule(
            ClientCardRuleCreate(
                name="Bad",
                condition_field_key="missing",
                condition_operator="equals",
                condition_value="x",
                actions=[ClientCardRuleAction(target_key="anydesk", action="show", scope="editor")],
            ),
            db=session,
            user=_admin(),
        )

    assert exc.value.status_code == 422
    assert exc.value.detail["error"]["code"] == "rule_condition_field_unknown"


def test_create_rule_rejects_unknown_target_field():
    session = _session()
    block = _block("department", "Department", 10)
    condition = _field("location_type", "Location", block.key, 10, is_system=True)
    session.add_all([block, condition])
    session.commit()

    with pytest.raises(HTTPException) as exc:
        settings_client_card.create_client_card_rule(
            ClientCardRuleCreate(
                name="Bad",
                condition_field_key="location_type",
                condition_operator="equals",
                condition_value="x",
                actions=[ClientCardRuleAction(target_key="missing", action="show", scope="editor")],
            ),
            db=session,
            user=_admin(),
        )

    assert exc.value.status_code == 422
    assert exc.value.detail["error"]["code"] == "rule_target_field_unknown"


def test_create_rule_rejects_invalid_action_operator_scope():
    with pytest.raises(Exception):
        ClientCardRuleCreate(
            name="Bad",
            condition_field_key="location_type",
            condition_operator="contains",
            condition_value="x",
            actions=[ClientCardRuleAction(target_key="anydesk", action="show", scope="editor")],
        )
    with pytest.raises(Exception):
        ClientCardRuleAction(target_key="anydesk", action="flash", scope="editor")
    with pytest.raises(Exception):
        ClientCardRuleAction(target_key="anydesk", action="show", scope="modal")


def test_block_rule_show_hide_allowed_but_readonly_rejected():
    session = _session()
    block = _block("custom_block_1", "Custom", 10)
    condition = _field("location_type", "Location", block.key, 10, is_system=True)
    session.add_all([block, condition])
    session.commit()

    result = settings_client_card.create_client_card_rule(
        ClientCardRuleCreate(
            name="Block rule",
            condition_field_key="location_type",
            condition_operator="equals",
            condition_value="Remote",
            actions=[ClientCardRuleAction(target_type="block", target_key=block.key, action="hide", scope="both")],
        ),
        db=session,
        user=_admin(),
    )

    assert result.actions[0].target_type == "block"
    assert result.actions[0].action == "hide"

    with pytest.raises(HTTPException) as exc:
        settings_client_card.create_client_card_rule(
            ClientCardRuleCreate(
                name="Bad block rule",
                condition_field_key="location_type",
                condition_operator="equals",
                condition_value="Remote",
                actions=[ClientCardRuleAction(target_type="block", target_key=block.key, action="readonly", scope="editor")],
            ),
            db=session,
            user=_admin(),
        )
    assert exc.value.status_code == 422
    assert exc.value.detail["error"]["code"] == "rule_block_action_invalid"


def test_field_editor_only_actions_reject_card_and_both_scope():
    session = _session()
    block = _block("custom_block_1", "Custom", 10)
    condition = _field("location_type", "Location", block.key, 10, is_system=True)
    target = _field("custom_note", "Note", block.key, 20, is_system=False)
    session.add_all([block, condition, target])
    session.commit()

    for scope in ["card", "both"]:
        with pytest.raises(HTTPException) as exc:
            settings_client_card.create_client_card_rule(
                ClientCardRuleCreate(
                    name=f"Bad {scope}",
                    condition_field_key="location_type",
                    condition_operator="equals",
                    condition_value="Remote",
                    actions=[ClientCardRuleAction(target_key=target.key, action="required", scope=scope)],
                ),
                db=session,
                user=_admin(),
            )
        assert exc.value.status_code == 422
        assert exc.value.detail["error"]["code"] == "rule_field_action_scope_invalid"


def test_editor_rule_rejects_field_without_runtime_editor_control():
    session = _session()
    block = _block("tg", "Telegram", 10)
    condition = _field("location_type", "Location", block.key, 10, is_system=True)
    unsupported = _field("telegram_username", "Telegram", block.key, 20, is_system=True)
    session.add_all([block, condition, unsupported])
    session.commit()

    with pytest.raises(HTTPException) as exc:
        settings_client_card.create_client_card_rule(
            ClientCardRuleCreate(
                name="Unsupported",
                condition_field_key=condition.key,
                condition_operator="equals",
                condition_value="Remote",
                actions=[ClientCardRuleAction(target_key=unsupported.key, action="show", scope="both")],
            ),
            db=session,
            user=_admin(),
        )

    assert exc.value.status_code == 422
    assert exc.value.detail["error"]["code"] == "rule_target_editor_unsupported"


def test_rule_multiple_actions_are_preserved():
    session = _session()
    block = _block("custom_block_1", "Custom", 10)
    condition = _field("location_type", "Location", block.key, 10, is_system=True)
    target_a = _field("custom_a", "A", block.key, 20, is_system=False)
    target_b = _field("custom_b", "B", block.key, 30, is_system=False)
    session.add_all([block, condition, target_a, target_b])
    session.commit()

    result = settings_client_card.create_client_card_rule(
        ClientCardRuleCreate(
            name="Multi",
            condition_field_key=condition.key,
            condition_operator="equals",
            condition_value="Remote",
            actions=[
                ClientCardRuleAction(target_key=target_a.key, action="show", scope="both"),
                ClientCardRuleAction(target_key=target_b.key, action="required", scope="editor"),
            ],
        ),
        db=session,
        user=_admin(),
    )

    assert [action.target_key for action in result.actions] == [target_a.key, target_b.key]
    assert [action.action for action in result.actions] == ["show", "required"]


def test_rule_condition_value_null_and_omitted_patch_contract():
    session = _session()
    block = _block("custom_block_1", "Custom", 10)
    condition = _field("location_type", "Location", block.key, 10, is_system=True)
    target = _field("custom_note", "Note", block.key, 20, is_system=False)
    session.add_all([block, condition, target])
    session.commit()

    rule = settings_client_card.create_client_card_rule(
        ClientCardRuleCreate(
            name="Rule",
            condition_field_key="location_type",
            condition_operator="equals",
            condition_value="Remote",
            actions=[ClientCardRuleAction(target_key=target.key, action="show", scope="editor")],
        ),
        db=session,
        user=_admin(),
    )

    updated = settings_client_card.update_client_card_rule(
        rule.id,
        ClientCardRuleUpdate(condition_operator="is_empty", condition_value=None),
        db=session,
        user=_admin(),
    )
    assert updated.condition_value is None

    updated = settings_client_card.update_client_card_rule(
        rule.id,
        ClientCardRuleUpdate(name="Renamed"),
        db=session,
        user=_admin(),
    )
    assert updated.condition_operator == "is_empty"
    assert updated.condition_value is None

    updated = settings_client_card.update_client_card_rule(
        rule.id,
        ClientCardRuleUpdate(condition_operator="equals", condition_value="Office"),
        db=session,
        user=_admin(),
    )
    assert updated.condition_value == "Office"
    updated = settings_client_card.update_client_card_rule(
        rule.id,
        ClientCardRuleUpdate(sort_order=22),
        db=session,
        user=_admin(),
    )
    assert updated.condition_value == "Office"


def test_get_config_returns_rules_and_patch_toggles_rule():
    session = _session()
    block = _block("department", "Department", 10)
    condition = _field("location_type", "Location", block.key, 10, is_system=True)
    target = _field("anydesk", "AnyDesk", block.key, 20, is_system=True)
    rule = ClientCardRuleConfig(
        id=str(uuid.uuid4()),
        name="Show AnyDesk",
        is_active=True,
        is_system=False,
        sort_order=10,
        condition_field_key="location_type",
        condition_operator="equals",
        condition_value="Remote",
        actions_json=[{"target_type": "field", "target_key": "anydesk", "action": "show", "scope": "editor"}],
        created_at=datetime.utcnow(),
        updated_at=datetime.utcnow(),
    )
    session.add_all([block, condition, target, rule])
    session.commit()

    payload = settings_client_card.get_client_card_config(db=session, user=_admin())
    updated = settings_client_card.update_client_card_rule(rule.id, ClientCardRuleUpdate(is_active=False), db=session, user=_admin())

    assert payload.rules[0].name == "Show AnyDesk"
    assert updated.is_active is False


def test_delete_custom_rule_and_forbid_system_rule_delete():
    session = _session()
    custom_rule = ClientCardRuleConfig(
        id=str(uuid.uuid4()),
        name="Custom",
        is_active=True,
        is_system=False,
        sort_order=10,
        condition_field_key="a",
        condition_operator="is_not_empty",
        actions_json=[{"target_type": "field", "target_key": "a", "action": "show", "scope": "editor"}],
        created_at=datetime.utcnow(),
        updated_at=datetime.utcnow(),
    )
    system_rule = ClientCardRuleConfig(
        id=str(uuid.uuid4()),
        name="System",
        is_active=True,
        is_system=True,
        sort_order=20,
        condition_field_key="a",
        condition_operator="is_not_empty",
        actions_json=[{"target_type": "field", "target_key": "a", "action": "show", "scope": "editor"}],
        created_at=datetime.utcnow(),
        updated_at=datetime.utcnow(),
    )
    session.add_all([custom_rule, system_rule])
    session.commit()

    settings_client_card.delete_client_card_rule(custom_rule.id, db=session, user=_admin())
    with pytest.raises(HTTPException) as exc:
        settings_client_card.delete_client_card_rule(system_rule.id, db=session, user=_admin())

    assert session.get(ClientCardRuleConfig, custom_rule.id) is None
    assert exc.value.status_code == 403


def test_move_rule_recalculates_sort_order_with_step_ten():
    session = _session()
    rules = []
    for index, name in enumerate(["A", "B", "C"], start=1):
      rules.append(
          ClientCardRuleConfig(
              id=str(uuid.uuid4()),
              name=name,
              is_active=True,
              is_system=False,
              sort_order=index * 10,
              condition_field_key="x",
              condition_operator="is_not_empty",
              actions_json=[{"target_type": "field", "target_key": "x", "action": "show", "scope": "editor"}],
              created_at=datetime.utcnow(),
              updated_at=datetime.utcnow(),
          )
      )
    session.add_all(rules)
    session.commit()

    payload = settings_client_card.move_client_card_rule(
        rules[2].id,
        ClientCardMovePayload(direction="up"),
        db=session,
        user=_admin(),
    )

    assert [rule.name for rule in payload.rules] == ["A", "C", "B"]
    assert [rule.sort_order for rule in payload.rules] == [10, 20, 30]


def test_delete_field_or_block_used_by_rule_is_blocked():
    session = _session()
    block = _block("custom_block_1", "Custom", 10)
    condition = _field("custom_condition", "Condition", block.key, 10, is_system=False)
    target = _field("custom_target", "Target", block.key, 20, is_system=False)
    session.add_all([block, condition, target])
    session.commit()
    rule = settings_client_card.create_client_card_rule(
        ClientCardRuleCreate(
            name="Rule",
            condition_field_key=condition.key,
            condition_operator="equals",
            condition_value="yes",
            actions=[ClientCardRuleAction(target_key=target.key, action="show", scope="editor")],
        ),
        db=session,
        user=_admin(),
    )

    with pytest.raises(HTTPException) as exc:
        settings_client_card.delete_client_card_field(condition.id, db=session, user=_admin())
    assert exc.value.status_code == 409
    assert exc.value.detail["error"]["code"] == "rule_condition_in_use"
    assert exc.value.detail["error"]["details"]["rules"][0]["id"] == rule.id

    with pytest.raises(HTTPException) as exc:
        settings_client_card.delete_client_card_field(target.id, db=session, user=_admin())
    assert exc.value.status_code == 409
    assert exc.value.detail["error"]["code"] == "rule_target_in_use"

    with pytest.raises(HTTPException) as exc:
        settings_client_card.delete_client_card_block(block.id, db=session, user=_admin())
    assert exc.value.status_code == 409


def test_delete_block_used_by_block_rule_is_blocked():
    session = _session()
    block = _block("custom_block_1", "Custom", 10)
    condition = _field("custom_condition", "Condition", block.key, 10, is_system=False)
    session.add_all([block, condition])
    session.commit()
    rule = settings_client_card.create_client_card_rule(
        ClientCardRuleCreate(
            name="Block rule",
            condition_field_key=condition.key,
            condition_operator="equals",
            condition_value="yes",
            actions=[ClientCardRuleAction(target_type="block", target_key=block.key, action="hide", scope="editor")],
        ),
        db=session,
        user=_admin(),
    )

    with pytest.raises(HTTPException) as exc:
        settings_client_card.delete_client_card_block(block.id, db=session, user=_admin())
    assert exc.value.status_code == 409
    assert exc.value.detail["error"]["code"] == "rule_target_in_use"
    assert exc.value.detail["error"]["details"]["rules"][0]["id"] == rule.id


def test_seed_creates_default_anydesk_rule_without_duplicates(monkeypatch):
    engine = create_engine("sqlite:///:memory:", future=True)
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine, autoflush=False, autocommit=False)
    monkeypatch.setattr(seed_data, "SessionLocal", Session)

    seed_data.ensure_client_card_defaults()
    seed_data.ensure_client_card_defaults()

    with Session() as session:
        rules = session.execute(select(ClientCardRuleConfig).where(ClientCardRuleConfig.condition_field_key == "location_type")).scalars().all()

    assert len(rules) == 1
    assert rules[0].technical_key == "show_anydesk_for_remote"
    assert rules[0].condition_value == "\u0423\u0434\u0430\u043b\u0451\u043d\u043d\u044b\u0439"
    assert rules[0].actions_json[0]["target_key"] == "anydesk"


def test_seed_system_rule_technical_key_prevents_duplicate_after_rename(monkeypatch):
    engine = create_engine("sqlite:///:memory:", future=True)
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine, autoflush=False, autocommit=False)
    monkeypatch.setattr(seed_data, "SessionLocal", Session)

    seed_data.ensure_client_card_defaults()
    with Session() as session:
        rule = session.execute(
            select(ClientCardRuleConfig).where(ClientCardRuleConfig.technical_key == "show_anydesk_for_remote")
        ).scalar_one()
        rule.name = "User renamed AnyDesk rule"
        session.commit()

    seed_data.ensure_client_card_defaults()

    with Session() as session:
        rules = session.execute(
            select(ClientCardRuleConfig).where(ClientCardRuleConfig.technical_key == "show_anydesk_for_remote")
        ).scalars().all()
    assert len(rules) == 1
    assert rules[0].name == "User renamed AnyDesk rule"


def test_move_block_recalculates_sort_order_with_step_ten():
    session = _session()
    first = _block("tg", "Telegram", 11)
    second = _block("profile", "Profile", 20)
    third = _block("custom_block_123", "123", 1000)
    session.add_all([first, second, third])
    session.commit()

    payload = settings_client_card.move_client_card_block(
        third.id,
        ClientCardMovePayload(direction="up"),
        db=session,
        user=_admin(),
    )

    assert [block.key for block in payload.blocks] == ["tg", "custom_block_123", "profile"]
    orders = session.execute(select(ClientCardBlockConfig.sort_order).order_by(ClientCardBlockConfig.sort_order)).scalars().all()
    assert orders == [10, 20, 30]


def test_move_field_recalculates_sort_order_inside_block_only():
    session = _session()
    block = _block("custom_block_123", "123", 10)
    first = _field("custom_a", "A", block.key, 11, is_system=False)
    second = _field("custom_b", "B", block.key, 20, is_system=False)
    outside = _field("custom_c", "C", "other", 99, is_system=False)
    session.add_all([block, first, second, outside])
    session.commit()

    payload = settings_client_card.move_client_card_field(
        second.id,
        ClientCardMovePayload(direction="up"),
        db=session,
        user=_admin(),
    )

    fields = payload.blocks[0].fields
    assert [field.key for field in fields] == ["custom_b", "custom_a"]
    same_block_orders = session.execute(
        select(ClientCardFieldConfig.sort_order)
        .where(ClientCardFieldConfig.block_key == block.key)
        .order_by(ClientCardFieldConfig.sort_order)
    ).scalars().all()
    assert same_block_orders == [10, 20]
    assert session.get(ClientCardFieldConfig, outside.id).sort_order == 99
