from __future__ import annotations

from datetime import datetime
from pathlib import Path
import subprocess
import sys
import uuid

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker

from api.app.db import Base
from api.app.models import (
    Client,
    ClientCardBlockConfig,
    ClientCardFieldConfig,
    ClientCardRuleConfig,
    ClientCardSourceConfig,
    ClientProfile,
    Company,
    Ticket,
    User,
)
from api.app.services import seed_data
from api.app.services.call_center_employee_resolver import SYSTEM_SOURCE_KEY
from api.app.services.client_card_config_transfer import (
    ClientCardTransferError,
    export_client_card_config,
    import_client_card_config,
)


def _session_factory():
    engine = create_engine("sqlite:///:memory:", future=True)
    Base.metadata.create_all(engine)
    return sessionmaker(bind=engine, autoflush=False, autocommit=False)


def _session():
    return _session_factory()()


def _block(key: str = "profile", **overrides) -> ClientCardBlockConfig:
    now = datetime.utcnow()
    data = {
        "id": str(uuid.uuid4()),
        "key": key,
        "title": key.title(),
        "color_scheme": "gray",
        "sort_order": 10,
        "is_active": True,
        "show_in_card": True,
        "show_in_editor": True,
        "show_title": True,
        "created_at": now,
        "updated_at": now,
    }
    data.update(overrides)
    return ClientCardBlockConfig(**data)


def _field(key: str, block_key: str = "profile", *, is_system: bool = True, **overrides) -> ClientCardFieldConfig:
    now = datetime.utcnow()
    data = {
        "id": str(uuid.uuid4()),
        "key": key,
        "label": key,
        "field_type": "text",
        "block_key": block_key,
        "sort_order": 10,
        "is_visible": True,
        "is_visible_in_editor": True,
        "is_editable": not is_system,
        "is_required": False,
        "is_system": is_system,
        "options_json": None,
        "created_at": now,
        "updated_at": now,
    }
    data.update(overrides)
    return ClientCardFieldConfig(**data)


def _source(**overrides) -> ClientCardSourceConfig:
    now = datetime.utcnow()
    data = {
        "id": str(uuid.uuid4()),
        "technical_key": "employee_lookup",
        "name": "Employee lookup",
        "kind": "http",
        "is_active": True,
        "method": "GET",
        "url_template": "https://api.example.com/users/{{internal_number}}",
        "query_template_json": {"q": "{{contact_name}}"},
        "headers_template_json": {"Authorization": "Bearer {{secret:API_KEY}}"},
        "body_template_json": None,
        "timeout_ms": 1000,
        "mapping_json": [{"response_path": "data.name", "target_key": "contact_name"}],
        "on_error": "ignore",
        "created_at": now,
        "updated_at": now,
    }
    data.update(overrides)
    return ClientCardSourceConfig(**data)


def _rule(**overrides) -> ClientCardRuleConfig:
    now = datetime.utcnow()
    data = {
        "id": str(uuid.uuid4()),
        "technical_key": "lookup_on_internal_number",
        "name": "Lookup on internal number",
        "is_active": True,
        "is_system": False,
        "sort_order": 10,
        "condition_field_key": "internal_number",
        "condition_operator": "is_not_empty",
        "condition_value": None,
        "actions_json": [{"action": "lookup_source", "scope": "save", "source_id": "source-id"}],
        "created_at": now,
        "updated_at": now,
    }
    data.update(overrides)
    return ClientCardRuleConfig(**data)


def _seed_config(session):
    block = _block()
    fields = [_field("contact_name"), _field("internal_number"), _field("custom_note", is_system=False)]
    source = _source()
    rule = _rule(actions_json=[{"action": "lookup_source", "scope": "save", "source_id": source.id}])
    session.add_all([block, *fields, source, rule])
    session.commit()
    return block, fields, source, rule


def _counts(session) -> dict[str, int]:
    return {
        "blocks": len(session.execute(select(ClientCardBlockConfig)).scalars().all()),
        "fields": len(session.execute(select(ClientCardFieldConfig)).scalars().all()),
        "rules": len(session.execute(select(ClientCardRuleConfig)).scalars().all()),
        "sources": len(session.execute(select(ClientCardSourceConfig)).scalars().all()),
        "tickets": len(session.execute(select(Ticket)).scalars().all()),
        "clients": len(session.execute(select(Client)).scalars().all()),
        "users": len(session.execute(select(User)).scalars().all()),
        "profiles": len(session.execute(select(ClientProfile)).scalars().all()),
    }


def test_container_layout_cli_help_imports_service():
    api_root = Path(__file__).resolve().parents[1] / "api"

    result = subprocess.run(
        [sys.executable, "tools/client_card_config_transfer.py", "--help"],
        cwd=api_root,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        timeout=10,
        check=False,
    )

    assert result.returncode == 0
    assert "Export/import client-card configuration safely." in result.stdout
    assert "No module named" not in result.stderr


def test_export_does_not_contain_secret_values_and_marks_local_sources_inactive():
    session = _session()
    session.add_all(
        [
            _block(),
            _field("contact_name"),
            _field("internal_number"),
            _source(
                technical_key="local_lookup",
                url_template="http://localhost:8080/mock/{{internal_number}}",
                headers_template_json={
                    "Authorization": "Bearer plain-secret",
                    "X-Safe": "{{secret:API_KEY}}",
                },
            ),
        ]
    )
    session.commit()

    data = export_client_card_config(session)
    raw = str(data)

    assert "plain-secret" not in raw
    assert "{{secret:API_KEY}}" in raw
    assert data["sources"][0]["dev_only"] is True
    assert data["sources"][0]["is_active"] is False


def test_export_recursively_redacts_secret_like_values_and_url_query_without_reencoding_templates():
    session = _session()
    session.add_all(
        [
            _block(),
            _field("contact_name"),
            _field("internal_number"),
            _source(
                technical_key="nested_secret_lookup",
                url_template="https://lookup.example.com/users/{{internal_number}}?token=plain-token&visible={{contact_name}}",
                query_template_json={"nested": {"api_key": "plain-query-secret", "visible": "{{contact_name}}"}},
                headers_template_json={"Authorization": "Bearer {{secret:API_KEY}}", "Nested": {"password": "plain-header-secret"}},
                body_template_json={"auth": {"token": "plain-body-secret"}, "visible": "{{internal_number}}"},
            ),
        ]
    )
    session.commit()

    data = export_client_card_config(session)
    raw = str(data)
    source = data["sources"][0]

    assert "plain-token" not in raw
    assert "plain-query-secret" not in raw
    assert "plain-header-secret" not in raw
    assert "plain-body-secret" not in raw
    assert "{{secret:API_KEY}}" in raw
    assert "visible={{contact_name}}" in source["url_template"]
    assert "token=" not in source["url_template"]


def test_export_rejects_url_credentials_without_leaking_values():
    session = _session()
    session.add_all([_block(), _field("contact_name"), _source(url_template="https://user:pass@example.com")])
    session.commit()

    with pytest.raises(ClientCardTransferError) as exc:
        export_client_card_config(session)

    assert "user" not in str(exc.value)
    assert "pass" not in str(exc.value)


def test_import_into_empty_database_restores_dependencies_with_new_ids():
    dev = _session()
    _seed_config(dev)
    exported = export_client_card_config(dev)

    prod = _session()
    result = import_client_card_config(prod, exported, apply=True)

    assert result["conflicts"] == []
    source = prod.execute(select(ClientCardSourceConfig).where(ClientCardSourceConfig.technical_key == "employee_lookup")).scalar_one()
    rule = prod.execute(select(ClientCardRuleConfig).where(ClientCardRuleConfig.technical_key == "lookup_on_internal_number")).scalar_one()
    assert rule.actions_json == [{"action": "lookup_source", "scope": "save", "source_id": source.id}]
    assert source.id != exported["sources"][0].get("id")


def test_import_over_seed_records_updates_without_duplicate_system_entities(monkeypatch):
    Session = _session_factory()
    monkeypatch.setattr(seed_data, "SessionLocal", Session)
    seed_data.ensure_client_card_defaults()

    with Session() as seeded:
        seeded_counts = _counts(seeded)
        exported = export_client_card_config(seeded)
        exported["blocks"][0]["title"] = "Telegram Updated"
        result = import_client_card_config(seeded, exported, apply=True)
        after_counts = _counts(seeded)

    assert result["conflicts"] == []
    assert after_counts["blocks"] == seeded_counts["blocks"]
    assert after_counts["fields"] == seeded_counts["fields"]
    assert after_counts["rules"] == seeded_counts["rules"]


@pytest.mark.parametrize(
    "is_system, options, expected",
    [
        (True, None, ["Руководство"]),
        (True, ["Руководство", "Продажи"], ["Руководство"]),
        (True, ["Продажи"], None),
        (True, [], None),
        (False, None, None),
    ],
)
def test_import_select_admin_only_values_with_dry_run_and_repeat(is_system, options, expected):
    dev = _session()
    dev.add_all([
        _block(),
        _field("destination", is_system=is_system, field_type="select",
               options_json=options, admin_only_values_json=["Руководство"]),
    ])
    dev.commit()
    exported = export_client_card_config(dev)
    prod = _session()
    prod.add_all([
        _block(),
        _field("destination", is_system=is_system, field_type="select", options_json=options),
    ])
    prod.commit()

    dry_run = import_client_card_config(prod, exported)
    field = prod.execute(select(ClientCardFieldConfig)).scalar_one()
    assert dry_run["dry_run"] is True
    assert dry_run["applied"] is False
    assert dry_run["conflicts"] == []
    assert field.admin_only_values_json is None

    first = import_client_card_config(prod, exported, apply=True)
    assert first["applied"] is True
    assert first["conflicts"] == []
    assert field.options_json == options
    assert field.admin_only_values_json == expected

    second = import_client_card_config(prod, exported, apply=True)
    assert second["applied"] is True
    assert second["conflicts"] == []
    assert second["created"] == {}
    assert second["updated"] == {}
    assert field.admin_only_values_json == expected


def test_repeated_import_is_idempotent():
    dev = _session()
    _seed_config(dev)
    exported = export_client_card_config(dev)
    prod = _session()

    first = import_client_card_config(prod, exported, apply=True)
    second = import_client_card_config(prod, exported, apply=True)

    assert first["created"]["client_card_block_configs"] == 1
    assert "client_card_block_configs" not in second["created"]
    assert second["conflicts"] == []
    assert _counts(prod)["blocks"] == 1
    assert _counts(prod)["fields"] == 3
    assert _counts(prod)["sources"] == 1
    assert _counts(prod)["rules"] == 1


def test_system_lookup_action_round_trips_without_exporting_runtime_source():
    dev = _session()
    block = _block()
    fields = [_field("contact_name"), _field("internal_number")]
    system_source = _source(
        technical_key=SYSTEM_SOURCE_KEY,
        name="System",
        kind="system",
        method="POST",
        url_template="system://call-center-employee-resolver",
        query_template_json=None,
        headers_template_json=None,
        mapping_json=[{"response_path": "data.first_name", "target_key": "contact_name"}],
    )
    rule = _rule(
        technical_key="mixed_system_rule",
        actions_json=[
            {"action": "lookup_source", "scope": "save", "source_id": system_source.id},
            {"action": "show", "scope": "editor", "target_type": "field", "target_key": "contact_name"},
        ],
    )
    dev.add_all([block, *fields, system_source, rule])
    dev.commit()

    exported = export_client_card_config(dev)
    assert exported["sources"] == []
    assert exported["rules"][0]["actions"] == [
        {"action": "lookup_source", "scope": "save", "source_key": SYSTEM_SOURCE_KEY},
        {"action": "show", "scope": "editor", "target_type": "field", "target_key": "contact_name"},
    ]
    assert SYSTEM_SOURCE_KEY not in json_dumps(exported["sources"])

    prod = _session()
    first = import_client_card_config(prod, exported, apply=True)
    second = import_client_card_config(prod, exported, apply=True)
    imported_source = prod.execute(select(ClientCardSourceConfig).where(ClientCardSourceConfig.technical_key == SYSTEM_SOURCE_KEY)).scalar_one()
    imported_rule = prod.execute(select(ClientCardRuleConfig).where(ClientCardRuleConfig.technical_key == "mixed_system_rule")).scalar_one()

    assert first["conflicts"] == []
    assert second["conflicts"] == []
    assert imported_rule.actions_json == [
        {"action": "lookup_source", "scope": "save", "source_id": imported_source.id},
        {"action": "show", "scope": "editor", "target_type": "field", "target_key": "contact_name"},
    ]
    assert len(prod.execute(select(ClientCardSourceConfig).where(ClientCardSourceConfig.technical_key == SYSTEM_SOURCE_KEY)).scalars().all()) == 1


def test_system_source_import_upgrade_keeps_existing_id_and_user_mappings():
    prod = _session()
    existing = _source(
        technical_key=SYSTEM_SOURCE_KEY,
        name="System",
        kind="system",
        method="POST",
        url_template="system://call-center-employee-resolver",
        query_template_json=None,
        headers_template_json=None,
        body_template_json=None,
        mapping_json=[
            {"response_path": "data.bitrix_id", "target_key": "bitrix_id"},
            {"response_path": "data.full_name", "target_key": "custom_note"},
        ],
    )
    prod.add_all([_block(), _field("contact_name"), _field("internal_number"), _field("custom_note", is_system=False), existing])
    prod.commit()
    existing_id = existing.id

    payload = {
        "format_version": 1,
        "scope": "client_card_config",
        "blocks": [],
        "fields": [],
        "sources": [],
        "rules": [
            {
                "rule_key": "uses_system_source",
                "technical_key": "uses_system_source",
                "name": "Uses system source",
                "is_active": True,
                "sort_order": 10,
                "condition_field_key": "internal_number",
                "condition_operator": "is_not_empty",
                "condition_value": None,
                "actions": [{"action": "lookup_source", "scope": "save", "source_key": SYSTEM_SOURCE_KEY}],
            }
        ],
    }

    first = import_client_card_config(prod, payload, apply=True)
    second = import_client_card_config(prod, payload, apply=True)
    source = prod.execute(select(ClientCardSourceConfig).where(ClientCardSourceConfig.technical_key == SYSTEM_SOURCE_KEY)).scalar_one()

    assert first["conflicts"] == []
    assert second["conflicts"] == []
    assert source.id == existing_id
    mapping_by_target = {item["target_key"]: item["response_path"] for item in source.mapping_json}
    assert mapping_by_target["bitrix_id"] == "data.bitrix_id"
    assert mapping_by_target["contact_name"] == "data.first_name"
    assert mapping_by_target["last_name"] == "data.last_name"
    assert mapping_by_target["external_user_id"] == "data.id"
    assert mapping_by_target["bitrix_login"] == "data.login"
    assert mapping_by_target["custom_note"] == "data.full_name"
    assert len(mapping_by_target) == len(source.mapping_json)


def test_export_order_is_deterministic_for_same_config():
    session = _session()
    _seed_config(session)

    first = export_client_card_config(session)
    second = export_client_card_config(session)

    assert first == second


def test_round_trip_export_import_export_is_byte_and_structure_equal_for_dev_only_source():
    dev = _session()
    source = _source(
        technical_key="dev_lookup",
        is_active=True,
        url_template="http://localhost:8080/mock/{{internal_number}}",
        created_at=datetime(2026, 8, 1, 10, 0, 3),
        updated_at=datetime(2026, 8, 1, 10, 0, 3),
    )
    rule = _rule(
        technical_key=None,
        name="Lookup dev employee",
        condition_field_key="internal_number",
        condition_operator="is_not_empty",
        condition_value=None,
        actions_json=[{"action": "lookup_source", "scope": "save", "source_id": "dev-source-id"}],
        created_at=datetime(2026, 8, 1, 10, 0, 4),
        updated_at=datetime(2026, 8, 1, 10, 0, 4),
    )
    dev.add_all(
        [
            _block("profile", created_at=datetime(2026, 8, 1, 10, 0, 0), updated_at=datetime(2026, 8, 1, 10, 0, 0)),
            _field("contact_name", created_at=datetime(2026, 8, 1, 10, 0, 1), updated_at=datetime(2026, 8, 1, 10, 0, 1)),
            _field("internal_number", created_at=datetime(2026, 8, 1, 10, 0, 2), updated_at=datetime(2026, 8, 1, 10, 0, 2)),
            source,
            rule,
        ]
    )
    rule.actions_json = [{"action": "lookup_source", "scope": "save", "source_id": source.id}]
    dev.commit()

    exported_before = export_client_card_config(dev)
    raw_before = json_dumps(exported_before)

    copied = _session()
    first_import = import_client_card_config(copied, exported_before, apply=True)
    exported_after = export_client_card_config(copied)
    raw_after = json_dumps(exported_after)
    second_import = import_client_card_config(copied, exported_after, apply=True)

    assert first_import["conflicts"] == []
    assert first_import["updated"] == {}
    assert exported_before == exported_after
    assert raw_before == raw_after
    assert second_import["created"] == {}
    assert second_import["updated"] == {}
    assert second_import["conflicts"] == []


def test_field_show_when_false_round_trips_through_export_import():
    dev = _session()
    dev.add_all(
        [
            _block("profile"),
            _field("custom_flag", is_system=False, field_type="checkbox", show_when_false=False),
        ]
    )
    dev.commit()

    exported = export_client_card_config(dev)
    prod = _session()
    result = import_client_card_config(prod, exported, apply=True)
    copied = prod.execute(select(ClientCardFieldConfig).where(ClientCardFieldConfig.key == "custom_flag")).scalar_one()

    assert exported["fields"][0]["show_when_false"] is False
    assert result["conflicts"] == []
    assert copied.show_when_false is False


def json_dumps(value):
    import json

    return json.dumps(value, ensure_ascii=False, indent=2) + "\n"


def test_numeric_ids_can_differ_between_dev_and_prod():
    dev = _session()
    _seed_config(dev)
    exported = export_client_card_config(dev)
    dev_ids = {
        dev.execute(select(ClientCardSourceConfig)).scalar_one().id,
        dev.execute(select(ClientCardRuleConfig)).scalar_one().id,
    }

    prod = _session()
    import_client_card_config(prod, exported, apply=True)
    prod_ids = {
        prod.execute(select(ClientCardSourceConfig)).scalar_one().id,
        prod.execute(select(ClientCardRuleConfig)).scalar_one().id,
    }

    assert dev_ids.isdisjoint(prod_ids)


def test_rules_after_import_reference_correct_fields():
    dev = _session()
    block = _block()
    fields = [_field("destination"), _field("custom_note", is_system=False)]
    rule = _rule(
        technical_key=None,
        name="Show custom note",
        condition_field_key="destination",
        condition_operator="equals",
        condition_value="Support",
        actions_json=[{"target_type": "field", "target_key": "custom_note", "action": "show", "scope": "editor"}],
    )
    dev.add_all([block, *fields, rule])
    dev.commit()
    exported = export_client_card_config(dev)

    prod = _session()
    import_client_card_config(prod, exported, apply=True)
    imported = prod.execute(select(ClientCardRuleConfig)).scalar_one()

    assert imported.condition_field_key == "destination"
    assert imported.actions_json[0]["target_key"] == "custom_note"
    assert imported.technical_key is None


def test_sources_and_mappings_keep_links():
    dev = _session()
    _seed_config(dev)
    exported = export_client_card_config(dev)

    prod = _session()
    import_client_card_config(prod, exported, apply=True)
    source = prod.execute(select(ClientCardSourceConfig)).scalar_one()

    assert source.mapping_json == [{"response_path": "data.name", "target_key": "contact_name"}]
    assert source.query_template_json == {"q": "{{contact_name}}"}


def test_source_order_is_preserved_for_new_imports():
    dev = _session()
    dev.add_all(
        [
            _block(),
            _field("contact_name"),
            _source(technical_key="first_lookup", url_template="https://first.example.com", created_at=datetime(2026, 1, 1)),
            _source(technical_key="second_lookup", url_template="https://second.example.com", created_at=datetime(2026, 1, 2)),
        ]
    )
    dev.commit()
    exported = export_client_card_config(dev)

    prod = _session()
    import_client_card_config(prod, exported, apply=True)
    ordered = prod.execute(
        select(ClientCardSourceConfig).order_by(ClientCardSourceConfig.created_at.asc(), ClientCardSourceConfig.technical_key.asc())
    ).scalars().all()

    assert [item.technical_key for item in ordered] == ["first_lookup", "second_lookup"]


def test_import_error_in_middle_rolls_back_whole_transaction():
    session = _session()
    data = {
        "format_version": 1,
        "scope": "client_card_config",
        "created_at": "2026-08-03T00:00:00+00:00",
        "stats": {},
        "blocks": [{"key": "profile", "title": "Profile", "color_scheme": "gray", "sort_order": 10, "is_active": True}],
        "fields": [{"key": "contact_name", "label": "Name", "field_type": "text", "block_key": "profile", "sort_order": 10, "is_visible": True, "is_visible_in_editor": True, "is_editable": True, "is_required": False, "is_system": True}],
        "sources": [{"source_key": "bad", "technical_key": "bad", "name": "Bad", "kind": "http", "is_active": True, "method": "GET", "url_template": "https://user:pass@example.com", "mapping_json": [], "timeout_ms": 1000, "on_error": "ignore"}],
        "rules": [],
    }

    with pytest.raises(Exception):
        import_client_card_config(session, data, apply=True)
    session.rollback()

    assert _counts(session)["blocks"] == 0
    assert _counts(session)["fields"] == 0


def test_apply_with_conflict_rolls_back_all_prior_changes():
    dev = _session()
    _seed_config(dev)
    exported = export_client_card_config(dev)
    exported["fields"].append(dict(exported["fields"][0]))

    prod = _session()
    result = import_client_card_config(prod, exported, apply=True)

    assert result["applied"] is False
    assert result["conflicts"]
    assert _counts(prod)["blocks"] == 0
    assert _counts(prod)["fields"] == 0


def test_dangerous_existing_field_mismatch_is_conflict_and_not_silent_update():
    dev = _session()
    _seed_config(dev)
    exported = export_client_card_config(dev)
    prod = _session()
    prod.add_all([_block(), _field("contact_name", is_system=False)])
    prod.commit()

    result = import_client_card_config(prod, exported, apply=True)
    field = prod.execute(select(ClientCardFieldConfig).where(ClientCardFieldConfig.key == "contact_name")).scalar_one()

    assert result["applied"] is False
    assert result["conflicts"][0]["reason"] == "is_system mismatch"
    assert field.is_system is False


def test_dry_run_does_not_change_database():
    dev = _session()
    _seed_config(dev)
    exported = export_client_card_config(dev)
    prod = _session()

    result = import_client_card_config(prod, exported)

    assert result["dry_run"] is True
    assert _counts(prod)["blocks"] == 0
    assert _counts(prod)["fields"] == 0
    assert _counts(prod)["sources"] == 0
    assert _counts(prod)["rules"] == 0


def test_import_keeps_dev_only_sources_inactive_for_local_hosts():
    dev = _session()
    dev.add_all(
        [
            _block(),
            _field("contact_name"),
            _source(technical_key="localhost_lookup", url_template="http://localhost:8080/mock", is_active=True),
            _source(technical_key="loopback_lookup", url_template="http://127.0.0.1:5000/mock", is_active=True),
            _source(technical_key="ipv6_lookup", url_template="http://[::1]:5000/mock", is_active=True),
            _source(technical_key="docker_lookup", url_template="http://host.docker.internal:9000/mock", is_active=True),
        ]
    )
    dev.commit()
    exported = export_client_card_config(dev)

    prod = _session()
    import_client_card_config(prod, exported, apply=True)
    imported = prod.execute(select(ClientCardSourceConfig)).scalars().all()

    assert all(source.is_active is False for source in imported)
    assert {source.technical_key for source in imported} == {
        "localhost_lookup",
        "loopback_lookup",
        "ipv6_lookup",
        "docker_lookup",
    }


def test_protected_runtime_tables_and_snapshot_are_not_touched():
    dev = _session()
    _seed_config(dev)
    exported = export_client_card_config(dev)
    prod = _session()
    company = Company(id=str(uuid.uuid4()), name="Company")
    user = User(id=str(uuid.uuid4()), company_id=company.id, full_name="User", role="admin")
    client = Client(id=str(uuid.uuid4()), company_id=company.id, display_name="Client")
    profile = ClientProfile(id=str(uuid.uuid4()), client_id=client.id, bitrix_password="keep-secret", custom_fields={})
    ticket = Ticket(
        id=str(uuid.uuid4()),
        company_id=company.id,
        client_id=client.id,
        subject="Subject",
        call_center_profile_snapshot_json={"custom_fields": {"custom_note": "keep"}},
    )
    prod.add_all([company, user, client, profile, ticket])
    prod.commit()
    before = _counts(prod)

    import_client_card_config(prod, exported, apply=True)
    prod.refresh(profile)
    prod.refresh(ticket)
    after = _counts(prod)

    assert after["tickets"] == before["tickets"]
    assert after["clients"] == before["clients"]
    assert after["users"] == before["users"]
    assert after["profiles"] == before["profiles"]
    assert profile.bitrix_password == "keep-secret"
    assert ticket.call_center_profile_snapshot_json == {"custom_fields": {"custom_note": "keep"}}
