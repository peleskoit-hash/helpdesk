from __future__ import annotations

from datetime import datetime
from types import SimpleNamespace
import uuid

import pytest
from fastapi import HTTPException
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker

from api.app.db import Base
from api.app.models import ClientCardBlockConfig, ClientCardFieldConfig
from api.app.routers import settings_client_card
from api.app.schemas import ClientCardBlockUpdate, ClientCardFieldCreate, ClientCardFieldUpdate, ClientCardMovePayload


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
