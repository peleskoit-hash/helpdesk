from __future__ import annotations

from datetime import datetime
from types import SimpleNamespace
import uuid

import pytest
from fastapi import HTTPException
from sqlalchemy import create_engine, event, select
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from api.app.db import Base
from api.app.models import Client, ClientProfile, ClientCardBlockConfig, ClientCardFieldConfig, ClientCardRuleConfig, ClientCardSourceConfig, Company, Ticket, TicketStatus, User
from api.app.routers import management_users, settings_client_card, tickets
from api.app.schemas import ClientCardBlockUpdate, ClientCardFieldCreate, ClientCardFieldUpdate, ClientCardMovePayload, ClientCardRuleAction, ClientCardRuleCreate, ClientCardRuleUpdate, ClientCardSourceCreate, ClientCardSourceUpdate, ClientProfileUpdate
from api.app.services import client_card_sources
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


def _client_card_save_fixture(session):
    now = datetime.utcnow()
    company = Company(id=str(uuid.uuid4()), name="Company")
    user = User(id="admin-1", company_id=company.id, full_name="Admin", role="admin")
    client = Client(id=str(uuid.uuid4()), company_id=company.id, display_name="Client")
    ticket = Ticket(
        id=str(uuid.uuid4()),
        company_id=company.id,
        client_id=client.id,
        subject="Subject",
        status=TicketStatus.NEW.value,
        created_at=now,
        updated_at=now,
    )
    profile = ClientProfile(id=str(uuid.uuid4()), client_id=client.id, custom_fields={}, created_at=now, updated_at=now)
    block = _block("department", "Department", 10)
    fields = [
        _field("contact_name", "Name", block.key, 10, is_system=True),
        _field("destination", "Destination", block.key, 20, is_system=True),
        _field("sand_owner", "Sand", block.key, 30, is_system=True),
        _field("sales_group", "Sales", block.key, 40, is_system=True),
        _field("department", "Department", block.key, 50, is_system=True),
        _field("first_department", "First", block.key, 60, is_system=True),
        _field("group_name", "Group", block.key, 70, is_system=True),
        _field("location_type", "Location", block.key, 80, is_system=True),
        _field("anydesk", "AnyDesk", block.key, 90, is_system=True),
    ]
    custom_block = _block("custom_block_1", "Custom", 20)
    custom_note = _field("custom_note", "Note", custom_block.key, 10, is_system=False)
    session.add_all([company, user, client, ticket, profile, block, custom_block, custom_note, *fields])
    session.commit()
    return client, ticket, profile


def _save_rule(name, order, operator, condition_key, condition_value, actions):
    return ClientCardRuleConfig(
        id=str(uuid.uuid4()),
        name=name,
        is_active=True,
        is_system=False,
        sort_order=order,
        condition_field_key=condition_key or "",
        condition_operator=operator,
        condition_value=condition_value,
        actions_json=actions,
        created_at=datetime.utcnow(),
        updated_at=datetime.utcnow(),
    )


def _source_config(**overrides):
    now = datetime.utcnow()
    data = {
        "id": str(uuid.uuid4()),
        "technical_key": "lookup",
        "name": "Lookup",
        "kind": "http",
        "is_active": True,
        "method": "GET",
        "url_template": "https://api.example.com/users/{{internal_number}}",
        "query_template_json": None,
        "headers_template_json": None,
        "body_template_json": None,
        "timeout_ms": 1000,
        "mapping_json": [{"response_path": "data.name", "target_key": "contact_name"}],
        "on_error": "ignore",
        "created_at": now,
        "updated_at": now,
    }
    data.update(overrides)
    return ClientCardSourceConfig(**data)


def test_runtime_client_card_view_config_exposes_only_safe_source_summary():
    session = _session()
    now = datetime.utcnow()
    company = Company(id=str(uuid.uuid4()), name="Company")
    user = User(id="admin-1", company_id=company.id, full_name="Admin", role="admin")
    client = Client(id=str(uuid.uuid4()), company_id=company.id, display_name="Client")
    ticket = Ticket(
        id=str(uuid.uuid4()),
        company_id=company.id,
        client_id=client.id,
        subject="Subject",
        status=TicketStatus.NEW.value,
        created_at=now,
        updated_at=now,
    )
    source = _source_config(
        technical_key="lookup",
        name="Lookup",
        url_template="https://api.example.com/users/{{internal_number}}",
        query_template_json={"q": "{{contact_name}}"},
        headers_template_json={"Authorization": "{{secret:TOKEN}}"},
        body_template_json={"name": "{{contact_name}}"},
        mapping_json=[{"response_path": "data.name", "target_key": "contact_name"}],
    )
    session.add_all([company, user, client, ticket, source])
    session.commit()

    runtime_payload = tickets.get_client_card_view_config(ticket.id, db=session, user=_admin())
    source_payload = runtime_payload.sources[0].model_dump()

    assert source_payload == {
        "id": source.id,
        "name": "Lookup",
        "technical_key": "lookup",
        "is_active": True,
    }


def test_create_rule_accepts_always_set_value_action_schema():
    session = _session()
    block = _block("department", "Department", 10)
    destination = _field("destination", "Destination", block.key, 10, is_system=True)
    department = _field("department", "Department", block.key, 20, is_system=True)
    session.add_all([block, destination, department])
    session.commit()

    result = settings_client_card.create_client_card_rule(
        ClientCardRuleCreate(
            name="Derive",
            condition_operator="always",
            actions=[
                ClientCardRuleAction(
                    target_key="department",
                    action="set_value",
                    scope="save",
                    value_type="field",
                    source_key="destination",
                )
            ],
        ),
        db=session,
        user=_admin(),
    )

    assert result.condition_field_key is None
    assert result.actions[0].action == "set_value"
    assert result.actions[0].scope == "save"
    assert result.actions[0].value_type == "field"


def test_save_rule_rejects_backend_unsupported_fields():
    session = _session()
    block = _block("tg", "Telegram", 10)
    telegram = _field("telegram_username", "@username", block.key, 10, is_system=True)
    department = _field("department", "Department", block.key, 20, is_system=True)
    destination = _field("destination", "Destination", block.key, 30, is_system=True)
    session.add_all([block, telegram, department, destination])
    session.commit()

    cases = [
        (
            ClientCardRuleCreate(
                name="Bad target",
                condition_operator="always",
                actions=[ClientCardRuleAction(target_key="telegram_username", action="set_value", scope="save", value_type="literal", value="x")],
            ),
            "rule_save_target_unsupported",
        ),
        (
            ClientCardRuleCreate(
                name="Bad source",
                condition_operator="always",
                actions=[ClientCardRuleAction(target_key="department", action="set_value", scope="save", value_type="field", source_key="telegram_username")],
            ),
            "rule_save_source_unsupported",
        ),
        (
            ClientCardRuleCreate(
                name="Bad condition",
                condition_field_key="telegram_username",
                condition_operator="equals",
                condition_value="x",
                actions=[ClientCardRuleAction(target_key="department", action="set_value", scope="save", value_type="field", source_key="destination")],
            ),
            "rule_save_condition_field_unsupported",
        ),
        (
            ClientCardRuleCreate(
                name="Bad template",
                condition_operator="always",
                actions=[ClientCardRuleAction(target_key="department", action="set_value", scope="save", value_type="template", value="{{telegram_username}}")],
            ),
            "rule_save_template_ref_unsupported",
        ),
    ]
    for payload, code in cases:
        with pytest.raises(HTTPException) as exc:
            settings_client_card.create_client_card_rule(payload, db=session, user=_admin())
        assert exc.value.status_code == 422
        assert exc.value.detail["error"]["code"] == code


def test_save_rule_rejects_dependency_free_always_literal_and_null_actions():
    session = _session()
    block = _block("department", "Department", 10)
    department = _field("department", "Department", block.key, 10, is_system=True)
    first_department = _field("first_department", "First", block.key, 20, is_system=True)
    session.add_all([block, department, first_department])
    session.commit()

    for payload in [
        ClientCardRuleCreate(
            name="Literal only",
            condition_operator="always",
            actions=[ClientCardRuleAction(target_key="department", action="set_value", scope="save", value_type="literal", value="x")],
        ),
        ClientCardRuleCreate(
            name="Null only",
            condition_operator="always",
            actions=[ClientCardRuleAction(target_key="first_department", action="set_value", scope="save", value_type="null")],
        ),
    ]:
        with pytest.raises(HTTPException) as exc:
            settings_client_card.create_client_card_rule(payload, db=session, user=_admin())
        assert exc.value.status_code == 422
        assert exc.value.detail["error"]["code"] == "rule_save_trigger_required"


def test_lookup_source_rule_schema_storage_and_source_refs():
    session = _session()
    block = _block("profile", "Profile", 10)
    session.add_all([
        block,
        _field("internal_number", "Number", block.key, 10, is_system=True),
        _field("contact_name", "Name", block.key, 20, is_system=True),
    ])
    session.commit()
    source = settings_client_card.create_client_card_source(
        ClientCardSourceCreate(
            name="Lookup",
            url_template="https://api.example.com/users/{{internal_number}}",
            mapping_json=[{"response_path": "data.name", "target_key": "contact_name"}],
        ),
        db=session,
        user=_admin(),
    )

    result = settings_client_card.create_client_card_rule(
        ClientCardRuleCreate(
            name="Lookup rule",
            condition_operator="always",
            actions=[ClientCardRuleAction(action="lookup_source", scope="save", source_id=source.id)],
        ),
        db=session,
        user=_admin(),
    )

    stored = session.get(ClientCardRuleConfig, result.id).actions_json[0]
    assert stored == {"action": "lookup_source", "scope": "save", "source_id": source.id}
    with pytest.raises(HTTPException) as exc:
        settings_client_card.delete_client_card_source(source.id, db=session, user=_admin())
    assert exc.value.status_code == 409
    assert exc.value.detail["error"]["details"]["rules"][0]["id"] == result.id


def test_lookup_source_rule_rejects_missing_or_static_dependency_free_source():
    session = _session()
    block = _block("profile", "Profile", 10)
    session.add_all([block, _field("contact_name", "Name", block.key, 10, is_system=True)])
    session.commit()

    with pytest.raises(HTTPException) as missing_exc:
        settings_client_card.create_client_card_rule(
            ClientCardRuleCreate(
                name="Missing source",
                condition_operator="always",
                actions=[ClientCardRuleAction(action="lookup_source", scope="save", source_id=str(uuid.uuid4()))],
            ),
            db=session,
            user=_admin(),
        )
    assert missing_exc.value.detail["error"]["code"] == "rule_source_unknown"

    source = settings_client_card.create_client_card_source(
        ClientCardSourceCreate(
            name="Static",
            url_template="https://api.example.com/users",
            mapping_json=[{"response_path": "data.name", "target_key": "contact_name"}],
        ),
        db=session,
        user=_admin(),
    )
    with pytest.raises(HTTPException) as dep_exc:
        settings_client_card.create_client_card_rule(
            ClientCardRuleCreate(
                name="Static source",
                condition_operator="always",
                actions=[ClientCardRuleAction(action="lookup_source", scope="save", source_id=source.id)],
            ),
            db=session,
            user=_admin(),
        )
    assert dep_exc.value.detail["error"]["code"] == "rule_save_trigger_required"


def test_source_patch_cannot_make_lookup_source_rule_dependency_free():
    session = _session()
    block = _block("profile", "Profile", 10)
    session.add_all([
        block,
        _field("internal_number", "Number", block.key, 10, is_system=True),
        _field("contact_name", "Name", block.key, 20, is_system=True),
    ])
    session.commit()
    source = settings_client_card.create_client_card_source(
        ClientCardSourceCreate(
            name="Lookup",
            url_template="https://api.example.com/users/{{internal_number}}",
            mapping_json=[{"response_path": "data.name", "target_key": "contact_name"}],
        ),
        db=session,
        user=_admin(),
    )
    settings_client_card.create_client_card_rule(
        ClientCardRuleCreate(
            name="Lookup rule",
            condition_operator="always",
            actions=[ClientCardRuleAction(action="lookup_source", scope="save", source_id=source.id)],
        ),
        db=session,
        user=_admin(),
    )

    with pytest.raises(HTTPException) as exc:
        settings_client_card.update_client_card_source(
            source.id,
            ClientCardSourceUpdate(url_template="https://api.example.com/users"),
            db=session,
            user=_admin(),
        )

    assert exc.value.status_code == 422
    assert exc.value.detail["error"]["code"] == "rule_save_trigger_required"
    assert session.get(ClientCardSourceConfig, source.id).url_template.endswith("{{internal_number}}")


def test_save_derivation_value_types_and_ordered_override():
    session = _session()
    _, ticket, profile = _client_card_save_fixture(session)
    session.add_all(
        [
            _save_rule(
                "fallback",
                10,
                "always",
                None,
                None,
                [
                    {"target_type": "field", "target_key": "department", "action": "set_value", "scope": "save", "value_type": "field", "source_key": "destination"},
                    {"target_type": "field", "target_key": "group_name", "action": "set_value", "scope": "save", "value_type": "template", "value": "{{destination}} / {{location_type}}"},
                    {"target_type": "field", "target_key": "anydesk", "action": "set_value", "scope": "save", "value_type": "null"},
                    {"target_type": "field", "target_key": "custom_note", "action": "set_value", "scope": "save", "value_type": "literal", "value": "from-rule"},
                ],
            ),
            _save_rule(
                "override",
                20,
                "equals",
                "destination",
                "Sales",
                [
                    {"target_type": "field", "target_key": "department", "action": "set_value", "scope": "save", "value_type": "literal", "value": "Sales Department"},
                    {"target_type": "field", "target_key": "first_department", "action": "set_value", "scope": "save", "value_type": "map", "source_key": "sales_group", "map": {"A": "First", "B": None}},
                ],
            ),
        ]
    )
    profile.anydesk = "old-anydesk"
    session.commit()

    tickets.update_client_profile(
        ticket.id,
        ClientProfileUpdate(destination="Sales", sales_group="A", location_type="Remote"),
        db=session,
        user=_admin(),
    )
    session.refresh(profile)

    assert profile.department == "Sales Department"
    assert profile.first_department == "First"
    assert profile.group_name == "Sales / Remote"
    assert profile.anydesk is None
    assert profile.custom_fields == {"custom_note": "from-rule"}


def test_save_derivation_normalizes_custom_select_and_system_boolean():
    session = _session()
    _, ticket, profile = _client_card_save_fixture(session)
    custom_block = session.execute(select(ClientCardBlockConfig).where(ClientCardBlockConfig.key == "custom_block_1")).scalar_one()
    custom_select = _field("custom_status", "Status", custom_block.key, 20, is_system=False)
    custom_select.field_type = "select"
    custom_select.options_json = ["ok"]
    session.add_all(
        [
            custom_select,
            _save_rule(
                "normalize",
                10,
                "is_not_empty",
                "destination",
                None,
                [
                    {"target_type": "field", "target_key": "custom_status", "action": "set_value", "scope": "save", "value_type": "literal", "value": "ok"},
                    {"target_type": "field", "target_key": "is_verified", "action": "set_value", "scope": "save", "value_type": "literal", "value": "true"},
                ],
            ),
        ]
    )
    session.commit()

    tickets.update_client_profile(ticket.id, ClientProfileUpdate(destination="Support"), db=session, user=_admin())
    session.refresh(profile)

    assert profile.custom_fields == {"custom_status": "ok"}
    assert profile.is_verified is True


def test_save_derivation_rejects_invalid_custom_select_value():
    session = _session()
    _, ticket, _ = _client_card_save_fixture(session)
    custom_block = session.execute(select(ClientCardBlockConfig).where(ClientCardBlockConfig.key == "custom_block_1")).scalar_one()
    custom_select = _field("custom_status", "Status", custom_block.key, 20, is_system=False)
    custom_select.field_type = "select"
    custom_select.options_json = ["ok"]
    session.add_all(
        [
            custom_select,
            _save_rule(
                "normalize",
                10,
                "is_not_empty",
                "destination",
                None,
                [{"target_type": "field", "target_key": "custom_status", "action": "set_value", "scope": "save", "value_type": "literal", "value": "bad"}],
            ),
        ]
    )
    session.commit()

    with pytest.raises(HTTPException) as exc:
        tickets.update_client_profile(ticket.id, ClientProfileUpdate(destination="Support"), db=session, user=_admin())

    assert exc.value.status_code == 422
    assert exc.value.detail["error"]["code"] == "invalid_custom_field_value"


def test_save_derivation_boolean_condition_map_and_template_match_js_normalize():
    session = _session()
    _, ticket, profile = _client_card_save_fixture(session)
    session.add(
        _save_rule(
            "bool",
            10,
            "equals",
            "is_verified",
            "false",
            [
                {"target_type": "field", "target_key": "department", "action": "set_value", "scope": "save", "value_type": "map", "source_key": "is_verified", "map": {"false": "not verified"}},
                {"target_type": "field", "target_key": "group_name", "action": "set_value", "scope": "save", "value_type": "template", "value": "verified={{is_verified}}"},
            ],
        )
    )
    session.commit()

    tickets.update_client_profile(ticket.id, ClientProfileUpdate(is_verified=False), db=session, user=_admin())
    session.refresh(profile)

    assert profile.department == "not verified"
    assert profile.group_name == "verified=false"


def test_save_derivation_uses_current_profile_plus_incoming_partial_payload():
    session = _session()
    _, ticket, profile = _client_card_save_fixture(session)
    profile.destination = "\u041f\u0435\u0441\u043e\u043a"
    profile.department = "old"
    session.add(
        _save_rule(
            "sand",
            10,
            "equals",
            "destination",
            "\u041f\u0435\u0441\u043e\u043a",
            [
                {"target_type": "field", "target_key": "department", "action": "set_value", "scope": "save", "value_type": "literal", "value": "\u0421\u0442\u0430\u0436\u0438\u0440\u043e\u0432\u043a\u0430"},
                {"target_type": "field", "target_key": "group_name", "action": "set_value", "scope": "save", "value_type": "field", "source_key": "sand_owner"},
            ],
        )
    )
    session.commit()

    tickets.update_client_profile(ticket.id, ClientProfileUpdate(sand_owner="Owner"), db=session, user=_admin())
    session.refresh(profile)

    assert profile.department == "\u0421\u0442\u0430\u0436\u0438\u0440\u043e\u0432\u043a\u0430"
    assert profile.group_name == "Owner"


def test_contact_name_only_patch_does_not_run_unrelated_derivation():
    session = _session()
    _, ticket, profile = _client_card_save_fixture(session)
    profile.destination = "Support"
    profile.department = "Existing"
    session.add(
        _save_rule(
            "fallback",
            10,
            "always",
            None,
            None,
            [{"target_type": "field", "target_key": "department", "action": "set_value", "scope": "save", "value_type": "field", "source_key": "destination"}],
        )
    )
    session.commit()

    tickets.update_client_profile(ticket.id, ClientProfileUpdate(contact_name="Anna"), db=session, user=_admin())
    session.refresh(profile)

    assert profile.contact_name == "Anna"
    assert profile.department == "Existing"


def test_management_user_profile_uses_common_save_derivation():
    session = _session()
    client, _, profile = _client_card_save_fixture(session)
    session.add(
        _save_rule(
            "fallback",
            10,
            "always",
            None,
            None,
            [{"target_type": "field", "target_key": "department", "action": "set_value", "scope": "save", "value_type": "field", "source_key": "destination"}],
        )
    )
    session.commit()

    management_users.update_management_user_profile(
        client.id,
        ClientProfileUpdate(destination="Support"),
        db=session,
        user=_admin(),
    )
    session.refresh(profile)

    assert profile.department == "Support"


def test_lookup_source_per_rule_trigger_action_order_mapping_and_later_override(monkeypatch):
    session = _session()
    _, ticket, profile = _client_card_save_fixture(session)
    source = _source_config(mapping_json=[
        {"response_path": "data.name", "target_key": "contact_name"},
        {"response_path": "data.department", "target_key": "department"},
    ])
    unrelated = _source_config(
        id=str(uuid.uuid4()),
        technical_key="other",
        name="Other",
        url_template="https://api.example.com/other/{{sales_group}}",
    )
    session.add_all([
        source,
        unrelated,
        _save_rule(
            "lookup",
            10,
            "always",
            None,
            None,
            [
                {"action": "lookup_source", "scope": "save", "source_id": source.id},
                {"target_type": "field", "target_key": "group_name", "action": "set_value", "scope": "save", "value_type": "template", "value": "{{contact_name}} / {{department}}"},
            ],
        ),
        _save_rule(
            "unrelated",
            20,
            "always",
            None,
            None,
            [{"action": "lookup_source", "scope": "save", "source_id": unrelated.id}],
        ),
        _save_rule(
            "later override",
            30,
            "equals",
            "department",
            "From source",
            [{"target_type": "field", "target_key": "department", "action": "set_value", "scope": "save", "value_type": "literal", "value": "Final"}],
        ),
    ])
    session.commit()
    calls = []

    def fake_execute(src, context):
        calls.append(src.id)
        return SimpleNamespace(http_status=200, payload={"data": {"name": "Anna", "department": "From source"}}, duration_ms=1)

    monkeypatch.setattr(client_card_sources, "execute_source", fake_execute)

    tickets.update_client_profile(ticket.id, ClientProfileUpdate(internal_number="42"), db=session, user=_admin())
    session.refresh(profile)

    assert calls == [source.id]
    assert profile.contact_name == "Anna"
    assert profile.group_name == "Anna / From source"
    assert profile.department == "Final"


def test_lookup_source_missing_null_inactive_and_error_policies(monkeypatch):
    session = _session()
    _, ticket, profile = _client_card_save_fixture(session)
    missing_source = _source_config(mapping_json=[
        {"response_path": "data.missing", "target_key": "last_name"},
        {"response_path": "data.name", "target_key": "contact_name"},
        {"response_path": "data.clear", "target_key": "anydesk"},
    ])
    inactive_source = _source_config(
        id=str(uuid.uuid4()),
        technical_key="inactive",
        name="Inactive",
        is_active=False,
        mapping_json=[{"response_path": "data.name", "target_key": "department"}],
    )
    ignore_source = _source_config(
        id=str(uuid.uuid4()),
        technical_key="ignore",
        name="Ignore",
        url_template="https://api.example.com/ignore/{{internal_number}}",
        mapping_json=[{"response_path": "data.name", "target_key": "department"}],
        on_error="ignore",
    )
    session.add_all([
        missing_source,
        inactive_source,
        ignore_source,
        _save_rule(
            "sources",
            10,
            "always",
            None,
            None,
            [
                {"action": "lookup_source", "scope": "save", "source_id": missing_source.id},
                {"action": "lookup_source", "scope": "save", "source_id": inactive_source.id},
                {"action": "lookup_source", "scope": "save", "source_id": ignore_source.id},
            ],
        ),
    ])
    profile.last_name = "Keep"
    profile.anydesk = "old"
    session.commit()

    def fake_execute(src, context):
        if src.id == ignore_source.id:
            raise client_card_sources.ClientCardSourceHttpError("Source HTTP request failed", status=502)
        return SimpleNamespace(http_status=200, payload={"data": {"name": "Anna", "clear": None}}, duration_ms=1)

    monkeypatch.setattr(client_card_sources, "execute_source", fake_execute)

    tickets.update_client_profile(ticket.id, ClientProfileUpdate(internal_number="42"), db=session, user=_admin())
    session.refresh(profile)

    assert profile.last_name == "Keep"
    assert profile.contact_name == "Anna"
    assert profile.anydesk is None
    assert profile.department is None


def test_lookup_source_ignore_discards_partial_mapping_delta_and_later_triggers(monkeypatch):
    session = _session()
    _, ticket, profile = _client_card_save_fixture(session)
    custom_block = session.execute(select(ClientCardBlockConfig).where(ClientCardBlockConfig.key == "custom_block_1")).scalar_one()
    custom_select = _field("custom_status", "Status", custom_block.key, 20, is_system=False)
    custom_select.field_type = "select"
    custom_select.options_json = ["ok"]
    source = _source_config(
        mapping_json=[
            {"response_path": "data.name", "target_key": "contact_name"},
            {"response_path": "data.status", "target_key": "custom_status"},
        ],
        on_error="ignore",
    )
    session.add_all([
        custom_select,
        source,
        _save_rule(
            "lookup",
            10,
            "always",
            None,
            None,
            [{"action": "lookup_source", "scope": "save", "source_id": source.id}],
        ),
        _save_rule(
            "later",
            20,
            "equals",
            "contact_name",
            "Anna",
            [{"target_type": "field", "target_key": "department", "action": "set_value", "scope": "save", "value_type": "literal", "value": "Triggered"}],
        ),
    ])
    profile.contact_name = "Before"
    session.commit()

    monkeypatch.setattr(
        client_card_sources,
        "execute_source",
        lambda _source, _context: SimpleNamespace(http_status=200, payload={"data": {"name": "Anna", "status": "bad"}}, duration_ms=1),
    )

    tickets.update_client_profile(ticket.id, ClientProfileUpdate(internal_number="42"), db=session, user=_admin())
    session.refresh(profile)

    assert profile.contact_name == "Before"
    assert profile.custom_fields == {}
    assert profile.department is None


def test_lookup_source_block_save_aborts_without_profile_mutation(monkeypatch):
    session = _session()
    _, ticket, profile = _client_card_save_fixture(session)
    source = _source_config(on_error="block_save")
    session.add_all([
        source,
        _save_rule(
            "block",
            10,
            "always",
            None,
            None,
            [{"action": "lookup_source", "scope": "save", "source_id": source.id}],
        ),
    ])
    profile.contact_name = "Before"
    session.commit()

    monkeypatch.setattr(
        client_card_sources,
        "execute_source",
        lambda _source, _context: (_ for _ in ()).throw(client_card_sources.ClientCardSourceHttpError("boom", status=502)),
    )

    with pytest.raises(HTTPException) as exc:
        tickets.update_client_profile(ticket.id, ClientProfileUpdate(internal_number="42", contact_name="Incoming"), db=session, user=_admin())

    assert exc.value.status_code == 502
    session.refresh(profile)
    assert profile.contact_name == "Before"
    assert profile.internal_number is None


def test_lookup_source_http_runs_outside_db_transaction_and_contact_name_side_effect(monkeypatch):
    session = _session()
    client, ticket, profile = _client_card_save_fixture(session)
    source = _source_config()
    session.add_all([
        source,
        _save_rule(
            "lookup",
            10,
            "always",
            None,
            None,
            [{"action": "lookup_source", "scope": "save", "source_id": source.id}],
        ),
    ])
    session.commit()
    transaction_states = []

    def fake_execute(_source, _context):
        transaction_states.append(session.in_transaction())
        return SimpleNamespace(http_status=200, payload={"data": {"name": "Mapped Name"}}, duration_ms=1)

    monkeypatch.setattr(client_card_sources, "execute_source", fake_execute)

    tickets.update_client_profile(ticket.id, ClientProfileUpdate(internal_number="42"), db=session, user=_admin())
    session.refresh(profile)
    session.refresh(client)

    assert transaction_states == [False]
    assert profile.contact_name == "Mapped Name"
    assert client.display_name == "Mapped Name"


def test_lookup_source_runtime_error_is_not_swallowed_by_ignore(monkeypatch):
    session = _session()
    _, ticket, profile = _client_card_save_fixture(session)
    source = _source_config(on_error="ignore")
    session.add_all([
        source,
        _save_rule(
            "lookup",
            10,
            "always",
            None,
            None,
            [{"action": "lookup_source", "scope": "save", "source_id": source.id}],
        ),
    ])
    profile.contact_name = "Before"
    session.commit()

    monkeypatch.setattr(client_card_sources, "execute_source", lambda _source, _context: (_ for _ in ()).throw(RuntimeError("programming error")))

    with pytest.raises(RuntimeError):
        tickets.update_client_profile(ticket.id, ClientProfileUpdate(internal_number="42"), db=session, user=_admin())

    session.refresh(profile)
    assert profile.contact_name == "Before"
    assert profile.internal_number is None


def test_lookup_source_missing_profile_block_save_leaves_no_profile_row(monkeypatch):
    session = _session()
    client, ticket, profile = _client_card_save_fixture(session)
    session.delete(profile)
    source = _source_config(on_error="block_save")
    session.add(_save_rule("lookup", 10, "always", None, None, [{"action": "lookup_source", "scope": "save", "source_id": source.id}]))
    session.add(source)
    session.commit()

    monkeypatch.setattr(
        client_card_sources,
        "execute_source",
        lambda _source, _context: (_ for _ in ()).throw(client_card_sources.ClientCardSourceHttpError("boom", status=502)),
    )

    with pytest.raises(HTTPException) as exc:
        tickets.update_client_profile(ticket.id, ClientProfileUpdate(internal_number="42"), db=session, user=_admin())

    assert exc.value.status_code == 502
    assert session.execute(select(ClientProfile).where(ClientProfile.client_id == client.id)).scalar_one_or_none() is None


def test_lookup_source_phase_b_uses_plain_snapshots_and_no_sql_between_sources(monkeypatch):
    session = _session()
    _, ticket, profile = _client_card_save_fixture(session)
    custom_block = session.execute(select(ClientCardBlockConfig).where(ClientCardBlockConfig.key == "custom_block_1")).scalar_one()
    custom_select = _field("custom_status", "Status", custom_block.key, 20, is_system=False)
    custom_select.field_type = "select"
    custom_select.options_json = ["ok"]
    first_source = _source_config(
        mapping_json=[{"response_path": "data.status", "target_key": "custom_status"}],
    )
    second_source = _source_config(
        id=str(uuid.uuid4()),
        technical_key="second",
        name="Second",
        url_template="https://api.example.com/second/{{custom_status}}",
        mapping_json=[{"response_path": "data.name", "target_key": "contact_name"}],
    )
    session.add_all([
        custom_select,
        first_source,
        second_source,
        _save_rule(
            "lookup",
            10,
            "always",
            None,
            None,
            [
                {"action": "lookup_source", "scope": "save", "source_id": first_source.id},
                {"action": "lookup_source", "scope": "save", "source_id": second_source.id},
            ],
        ),
    ])
    session.commit()
    first_source_id = first_source.id
    second_source_id = second_source.id
    phase_b = {"active": False, "sql": 0}

    def count_phase_b_sql(*_args):
        if phase_b["active"]:
            phase_b["sql"] += 1

    event.listen(session.bind, "before_cursor_execute", count_phase_b_sql)

    def fake_execute(source, context):
        assert session.in_transaction() is False
        if source.id == first_source_id:
            phase_b["active"] = True
            return SimpleNamespace(http_status=200, payload={"data": {"status": "ok"}}, duration_ms=1)
        assert context["custom_status"] == "ok"
        assert phase_b["sql"] == 0
        phase_b["active"] = False
        return SimpleNamespace(http_status=200, payload={"data": {"name": "Anna"}}, duration_ms=1)

    monkeypatch.setattr(client_card_sources, "execute_source", fake_execute)
    try:
        tickets.update_client_profile(ticket.id, ClientProfileUpdate(internal_number="42"), db=session, user=_admin())
    finally:
        event.remove(session.bind, "before_cursor_execute", count_phase_b_sql)
    session.refresh(profile)

    assert profile.custom_fields == {"custom_status": "ok"}
    assert profile.contact_name == "Anna"


def test_lookup_source_profile_changed_during_preflight_returns_409_without_overwrite(monkeypatch):
    session = _session()
    _, ticket, profile = _client_card_save_fixture(session)
    source = _source_config()
    session.add_all([
        source,
        _save_rule(
            "lookup",
            10,
            "always",
            None,
            None,
            [{"action": "lookup_source", "scope": "save", "source_id": source.id}],
        ),
    ])
    profile.contact_name = "Before"
    session.commit()

    def fake_execute(_source, _context):
        profile.contact_name = "Concurrent"
        profile.updated_at = datetime.utcnow()
        session.commit()
        return SimpleNamespace(http_status=200, payload={"data": {"name": "Source wins?"}}, duration_ms=1)

    monkeypatch.setattr(client_card_sources, "execute_source", fake_execute)

    with pytest.raises(HTTPException) as exc:
        tickets.update_client_profile(ticket.id, ClientProfileUpdate(internal_number="42"), db=session, user=_admin())

    assert exc.value.status_code == 409
    assert exc.value.detail["error"]["code"] == "client_profile_changed_retry"
    session.refresh(profile)
    assert profile.contact_name == "Concurrent"
    assert profile.internal_number is None


def test_lookup_source_profile_changed_in_other_session_returns_409_without_overwrite(monkeypatch):
    engine = create_engine(
        "sqlite://",
        future=True,
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine, autoflush=False, autocommit=False)
    session = Session()
    other_session = Session()
    _, ticket, profile = _client_card_save_fixture(session)
    source = _source_config()
    session.add_all([
        source,
        _save_rule(
            "lookup",
            10,
            "always",
            None,
            None,
            [{"action": "lookup_source", "scope": "save", "source_id": source.id}],
        ),
    ])
    profile.contact_name = "Before"
    session.commit()

    def fake_execute(_source, _context):
        concurrent = other_session.execute(select(ClientProfile).where(ClientProfile.id == profile.id)).scalar_one()
        concurrent.contact_name = "Concurrent"
        concurrent.updated_at = datetime.utcnow()
        other_session.commit()
        return SimpleNamespace(http_status=200, payload={"data": {"name": "Source wins?"}}, duration_ms=1)

    monkeypatch.setattr(client_card_sources, "execute_source", fake_execute)

    with pytest.raises(HTTPException) as exc:
        tickets.update_client_profile(ticket.id, ClientProfileUpdate(internal_number="42"), db=session, user=_admin())

    assert exc.value.status_code == 409
    assert exc.value.detail["error"]["code"] == "client_profile_changed_retry"
    session.rollback()
    session.refresh(profile)
    assert profile.contact_name == "Concurrent"
    assert profile.internal_number is None


def test_lookup_source_missing_profile_concurrent_create_returns_409_without_duplicate(monkeypatch):
    engine = create_engine(
        "sqlite://",
        future=True,
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine, autoflush=False, autocommit=False)
    session = Session()
    other_session = Session()
    client, ticket, profile = _client_card_save_fixture(session)
    session.delete(profile)
    source = _source_config()
    session.add_all([
        source,
        _save_rule(
            "lookup",
            10,
            "always",
            None,
            None,
            [{"action": "lookup_source", "scope": "save", "source_id": source.id}],
        ),
    ])
    session.commit()

    def fake_execute(_source, _context):
        concurrent = ClientProfile(
            id=str(uuid.uuid4()),
            client_id=client.id,
            contact_name="Concurrent",
            internal_number="concurrent-number",
            custom_fields={},
            created_at=datetime.utcnow(),
            updated_at=datetime.utcnow(),
        )
        other_session.add(concurrent)
        other_session.commit()
        return SimpleNamespace(http_status=200, payload={"data": {"name": "Source wins?"}}, duration_ms=1)

    monkeypatch.setattr(client_card_sources, "execute_source", fake_execute)

    with pytest.raises(HTTPException) as exc:
        tickets.update_client_profile(ticket.id, ClientProfileUpdate(internal_number="42"), db=session, user=_admin())

    assert exc.value.status_code == 409
    assert exc.value.detail["error"]["code"] == "client_profile_changed_retry"
    session.rollback()
    profiles = session.execute(select(ClientProfile).where(ClientProfile.client_id == client.id)).scalars().all()
    assert len(profiles) == 1
    assert profiles[0].contact_name == "Concurrent"
    assert profiles[0].internal_number == "concurrent-number"


def test_seeded_department_meta_rules_match_legacy_behavior(monkeypatch):
    engine = create_engine("sqlite:///:memory:", future=True)
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine, autoflush=False, autocommit=False)
    monkeypatch.setattr(seed_data, "SessionLocal", Session)
    seed_data.ensure_client_card_defaults()

    with Session() as session:
        now = datetime.utcnow()
        company = Company(id=str(uuid.uuid4()), name="Company")
        user = User(id="admin-1", company_id=company.id, full_name="Admin", role="admin")
        client = Client(id=str(uuid.uuid4()), company_id=company.id, display_name="Client")
        ticket = Ticket(
            id=str(uuid.uuid4()),
            company_id=company.id,
            client_id=client.id,
            subject="Subject",
            status=TicketStatus.NEW.value,
            created_at=now,
            updated_at=now,
        )
        profile = ClientProfile(id=str(uuid.uuid4()), client_id=client.id, custom_fields={}, created_at=now, updated_at=now)
        session.add_all([company, user, client, ticket, profile])
        session.commit()
        tickets.update_client_profile(ticket.id, ClientProfileUpdate(destination="\u041f\u0435\u0441\u043e\u043a", sand_owner="Owner"), db=session, user=_admin())
        session.refresh(profile)
        assert profile.department == "\u0421\u0442\u0430\u0436\u0438\u0440\u043e\u0432\u043a\u0430"
        assert profile.first_department is None
        assert profile.group_name == "Owner"

        tickets.update_client_profile(
            ticket.id,
            ClientProfileUpdate(destination="\u041e\u0442\u0434\u0435\u043b \u041f\u0440\u043e\u0434\u0430\u0436", sales_group="\u0433\u0440\u0443\u043f\u043f\u0430 \u0428\u0430\u0431\u0443\u0440\u043e\u0432\u0430"),
            db=session,
            user=_admin(),
        )
        session.refresh(profile)
        assert profile.department == "\u041e\u0442\u0434\u0435\u043b \u043f\u0440\u043e\u0434\u0430\u0436"
        assert profile.first_department == "\u041f\u0435\u0440\u0432\u044b\u0439 \u0434\u0435\u043f\u0430\u0440\u0442\u0430\u043c\u0435\u043d\u0442"
        assert profile.group_name == "\u0433\u0440\u0443\u043f\u043f\u0430 \u0428\u0430\u0431\u0443\u0440\u043e\u0432\u0430"

        tickets.update_client_profile(ticket.id, ClientProfileUpdate(destination="\u041a\u043e\u043b\u043b \u0426\u0435\u043d\u0442\u0440"), db=session, user=_admin())
        session.refresh(profile)
        assert profile.department == "\u041a\u043e\u043b\u043b \u0426\u0435\u043d\u0442\u0440"
        assert profile.first_department is None
        assert profile.group_name is None


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
