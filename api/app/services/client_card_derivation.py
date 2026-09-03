from __future__ import annotations

import re
import uuid
from dataclasses import dataclass
from datetime import datetime
from types import SimpleNamespace
from typing import Any

from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..models import Client, ClientCardFieldConfig, ClientCardRuleConfig, ClientCardSourceConfig, ClientProfile
from .client_card import _normalize_custom_field_value
from .encryption import encrypt_secret

SYSTEM_PROFILE_FIELDS = {
    "contact_name",
    "last_name",
    "email",
    "phone",
    "verification_status",
    "is_verified",
    "destination",
    "sand_owner",
    "sales_group",
    "department",
    "first_department",
    "group_name",
    "employee_type",
    "location_type",
    "anydesk",
    "internal_number",
    "external_user_id",
    "bitrix_login",
    "bitrix_password",
    "bitrix_id",
}

_TEMPLATE_FIELD_RE = re.compile(r"\{\{([a-zA-Z0-9_]+)\}\}")


def _active_save_rules(db: Session) -> list[ClientCardRuleConfig]:
    return db.execute(
        select(ClientCardRuleConfig)
        .where(ClientCardRuleConfig.is_active.is_(True))
        .order_by(ClientCardRuleConfig.sort_order.asc(), ClientCardRuleConfig.id.asc())
    ).scalars().all()


def _custom_field_keys(db: Session) -> set[str]:
    return set(
        db.execute(
            select(ClientCardFieldConfig.key).where(ClientCardFieldConfig.is_system.is_(False))
        ).scalars().all()
    )


def strip_system_profile_keys_from_custom_fields(custom_fields: dict[str, Any] | None) -> dict[str, Any]:
    return {
        key: value
        for key, value in dict(custom_fields or {}).items()
        if key not in SYSTEM_PROFILE_FIELDS
    }


def _field_configs(db: Session) -> dict[str, Any]:
    return {
        field.key: SimpleNamespace(
            key=field.key,
            label=field.label,
            field_type=field.field_type,
            is_editable=bool(field.is_editable),
            is_required=bool(field.is_required),
            is_system=bool(field.is_system),
            options_json=list(field.options_json or []),
        )
        for field in db.execute(select(ClientCardFieldConfig)).scalars().all()
    }


def _active_sources(db: Session) -> dict[str, ClientCardSourceConfig]:
    return {source.id: source for source in db.execute(select(ClientCardSourceConfig)).scalars().all()}


def _profile_context(profile: ClientProfile, incoming: dict[str, Any], merged_custom_fields: dict[str, Any]) -> dict[str, Any]:
    context = {field: getattr(profile, field, None) for field in SYSTEM_PROFILE_FIELDS}
    context.update({key: value for key, value in incoming.items() if key in SYSTEM_PROFILE_FIELDS})
    context.update(strip_system_profile_keys_from_custom_fields(merged_custom_fields))
    return context


def _normalize(value: Any) -> str:
    if value is None:
        return ""
    if value is True:
        return "true"
    if value is False:
        return "false"
    return str(value).strip()


def _template_fields(template: str | None) -> set[str]:
    if not template:
        return set()
    return set(_TEMPLATE_FIELD_RE.findall(str(template)))


def _save_action_dependencies(action: dict[str, Any], sources_by_id: dict[str, Any] | None = None) -> set[str]:
    value_type = action.get("value_type")
    deps: set[str] = set()
    if action.get("action") == "lookup_source":
        source = (sources_by_id or {}).get(action.get("source_id") or "")
        if source is not None:
            from .client_card_sources import source_template_field_refs

            deps.update(source_template_field_refs(source))
        return deps
    if action.get("action") == "set_value" and value_type in {"field", "map"} and action.get("source_key"):
        deps.add(str(action["source_key"]))
    if action.get("action") == "set_value" and value_type == "template":
        deps.update(_template_fields(action.get("value")))
    return deps


def _rule_save_actions(rule: ClientCardRuleConfig) -> list[dict[str, Any]]:
    return [
        action
        for action in (rule.actions_json or [])
        if (
            action.get("action") == "set_value"
            and action.get("target_type", "field") == "field"
            and action.get("scope") == "save"
        )
        or (action.get("action") == "lookup_source" and action.get("scope") == "save")
    ]


def _save_rule_dependencies(rule: ClientCardRuleConfig, sources_by_id: dict[str, Any] | None = None) -> set[str]:
    actions = _rule_save_actions(rule)
    deps: set[str] = set()
    if not actions:
        return deps
    if rule.condition_operator != "always" and rule.condition_field_key:
        deps.add(rule.condition_field_key)
    for action in actions:
        deps.update(_save_action_dependencies(action, sources_by_id=sources_by_id))
    return deps


def _save_rules_dependencies(rules: list[ClientCardRuleConfig], sources_by_id: dict[str, Any] | None = None) -> set[str]:
    deps: set[str] = set()
    for rule in rules:
        deps.update(_save_rule_dependencies(rule, sources_by_id=sources_by_id))
    return deps


def client_card_derivation_should_run(db: Session, incoming: dict[str, Any], merged_custom_fields: dict[str, Any] | None = None) -> bool:
    rules = _active_save_rules(db)
    return _save_rules_should_run(rules, incoming, sources_by_id=_active_sources(db))


def _incoming_changed_keys(incoming: dict[str, Any]) -> set[str]:
    touched = set(incoming.keys())
    if "custom_fields" in touched:
        touched.discard("custom_fields")
        if isinstance(incoming.get("custom_fields"), dict):
            touched.update(incoming["custom_fields"].keys())
    return touched


def _save_rules_should_run(rules: list[ClientCardRuleConfig], incoming: dict[str, Any], sources_by_id: dict[str, Any] | None = None) -> bool:
    return bool(_incoming_changed_keys(incoming) & _save_rules_dependencies(rules, sources_by_id=sources_by_id))


def _rule_matches(context: dict[str, Any], rule: ClientCardRuleConfig) -> bool:
    operator = rule.condition_operator
    if operator == "always":
        return True
    current = _normalize(context.get(rule.condition_field_key or ""))
    expected = _normalize(rule.condition_value)
    if operator == "equals":
        return current == expected
    if operator == "not_equals":
        return current != expected
    if operator == "is_empty":
        return current == ""
    if operator == "is_not_empty":
        return current != ""
    return False


def _render_template(template: str | None, context: dict[str, Any]) -> str | None:
    if template is None:
        return None
    rendered = _TEMPLATE_FIELD_RE.sub(lambda match: _normalize(context.get(match.group(1))), str(template))
    return re.sub(r"\s+", " ", rendered).strip()


def _action_value(action: dict[str, Any], context: dict[str, Any]) -> Any:
    value_type = action.get("value_type")
    if value_type == "literal":
        return action.get("value")
    if value_type == "field":
        return context.get(action.get("source_key") or "")
    if value_type == "template":
        return _render_template(action.get("value"), context)
    if value_type == "map":
        source_value = _normalize(context.get(action.get("source_key") or ""))
        mapping = action.get("map") or {}
        return mapping.get(source_value)
    if value_type == "null":
        return None
    return None


def _normalize_system_value(target_key: str, field: ClientCardFieldConfig | None, value: Any) -> Any:
    if target_key == "is_verified":
        if value is None:
            return False
        if isinstance(value, str):
            return value.strip().lower() in {"1", "true", "yes", "on", "\u0434\u0430"}
        return bool(value)
    if value is None:
        return None
    field_type = (field.field_type if field else "text") or "text"
    if field_type == "checkbox":
        if isinstance(value, str):
            return value.strip().lower() in {"1", "true", "yes", "on", "\u0434\u0430"}
        return bool(value)
    return str(value).strip() or None


def normalize_client_card_derived_value(
    target_key: str,
    value: Any,
    field_configs: dict[str, ClientCardFieldConfig],
    custom_keys: set[str],
) -> Any:
    if target_key in SYSTEM_PROFILE_FIELDS:
        return _normalize_system_value(target_key, field_configs.get(target_key), value)
    if target_key in custom_keys:
        if value is None:
            return None
        return _normalize_custom_field_value(field_configs[target_key], value)
    return _normalize_system_value(target_key, field_configs.get(target_key), value)


def _normalize_derived_value(
    target_key: str,
    value: Any,
    field_configs: dict[str, ClientCardFieldConfig],
    custom_keys: set[str],
) -> Any:
    return normalize_client_card_derived_value(target_key, value, field_configs, custom_keys)


@dataclass(frozen=True)
class SavePipelineResult:
    profile: ClientProfile
    changed_keys: set[str]


@dataclass(frozen=True)
class ManualSourceRunResult:
    profile: ClientProfile
    changed_keys: set[str]


def _profile_snapshot(profile: ClientProfile | None) -> dict[str, Any]:
    if profile is None:
        data = {field: None for field in SYSTEM_PROFILE_FIELDS}
        data["custom_fields"] = {}
        return data
    data = {field: getattr(profile, field, None) for field in SYSTEM_PROFILE_FIELDS}
    data["custom_fields"] = strip_system_profile_keys_from_custom_fields(profile.custom_fields)
    return data


def _profile_field_value_for_storage(field: str, value: Any) -> Any:
    if field == "bitrix_password":
        return encrypt_secret(value)
    return bool(value) if field == "is_verified" else value


def _copy_rule(rule: ClientCardRuleConfig) -> SimpleNamespace:
    return SimpleNamespace(
        id=rule.id,
        name=rule.name,
        technical_key=rule.technical_key,
        is_active=bool(rule.is_active),
        sort_order=rule.sort_order,
        condition_field_key=rule.condition_field_key,
        condition_operator=rule.condition_operator,
        condition_value=rule.condition_value,
        actions_json=[dict(action) for action in (rule.actions_json or [])],
        created_at=rule.created_at,
    )


def _copy_source(source: ClientCardSourceConfig) -> SimpleNamespace:
    return SimpleNamespace(
        id=source.id,
        technical_key=source.technical_key,
        name=source.name,
        kind=source.kind,
        is_active=bool(source.is_active),
        method=source.method,
        url_template=source.url_template,
        query_template_json=source.query_template_json,
        headers_template_json=source.headers_template_json,
        body_template_json=source.body_template_json,
        timeout_ms=source.timeout_ms,
        mapping_json=[dict(item) for item in (source.mapping_json or [])],
        on_error=source.on_error,
    )


def _locked_profile(db: Session, profile_id: str) -> ClientProfile | None:
    return db.execute(
        select(ClientProfile)
        .where(ClientProfile.id == profile_id)
        .with_for_update()
        .execution_options(populate_existing=True)
    ).scalar_one_or_none()


def _locked_profile_by_client_id(db: Session, client_id: str) -> ClientProfile | None:
    return db.execute(
        select(ClientProfile)
        .where(ClientProfile.client_id == client_id)
        .with_for_update()
        .execution_options(populate_existing=True)
    ).scalar_one_or_none()


def _locked_client(db: Session, client_id: str) -> Client | None:
    return db.execute(
        select(Client)
        .where(Client.id == client_id)
        .with_for_update()
        .execution_options(populate_existing=True)
    ).scalar_one_or_none()


def _retry_conflict() -> HTTPException:
    return HTTPException(
        status_code=409,
        detail={"error": {"code": "client_profile_changed_retry", "message": "Client profile changed during source lookup; retry save", "details": {}}},
    )


def _safe_source_error(source: Any) -> HTTPException:
    details = {"source": {"id": source.id, "name": source.name, "technical_key": source.technical_key}}
    return HTTPException(
        status_code=502,
        detail={"error": {"code": "client_card_source_failed", "message": "Client-card source lookup failed", "details": details}},
    )


def _missing_source_template_value(field_key: str, field_configs: dict[str, Any]) -> HTTPException:
    label = getattr(field_configs.get(field_key), "label", None) or field_key
    return HTTPException(
        status_code=422,
        detail={
            "error": {
                "code": "source_template_value_required",
                "message": f"\u041d\u0435 \u0437\u0430\u043f\u043e\u043b\u043d\u0435\u043d\u043e \u043f\u043e\u043b\u0435 \u201c{label}\u201d",
                "details": {"field_key": field_key, "field_label": label},
            }
        },
    )


def _apply_value_to_maps(
    target_key: str,
    value: Any,
    *,
    staged: dict[str, Any],
    custom_fields: dict[str, Any],
    context: dict[str, Any],
    field_configs: dict[str, ClientCardFieldConfig],
    custom_keys: set[str],
    changed_keys: set[str],
) -> None:
    normalized = _normalize_derived_value(target_key, value, field_configs, custom_keys)
    old_value = context.get(target_key)
    if target_key not in SYSTEM_PROFILE_FIELDS and target_key in custom_keys:
        if normalized is None:
            custom_fields.pop(target_key, None)
        else:
            custom_fields[target_key] = normalized
    staged[target_key] = normalized
    context[target_key] = normalized
    if old_value != normalized:
        changed_keys.add(target_key)


def _prepare_value_delta(
    target_key: str,
    value: Any,
    *,
    context: dict[str, Any],
    field_configs: dict[str, Any],
    custom_keys: set[str],
) -> tuple[str, Any, bool]:
    normalized = _normalize_derived_value(target_key, value, field_configs, custom_keys)
    return target_key, normalized, context.get(target_key) != normalized


def _merge_value_delta(
    delta: list[tuple[str, Any, bool]],
    *,
    staged: dict[str, Any],
    custom_fields: dict[str, Any],
    context: dict[str, Any],
    custom_keys: set[str],
    changed_keys: set[str],
) -> None:
    for target_key, normalized, changed in delta:
        if target_key not in SYSTEM_PROFILE_FIELDS and target_key in custom_keys:
            if normalized is None:
                custom_fields.pop(target_key, None)
            else:
                custom_fields[target_key] = normalized
        staged[target_key] = normalized
        context[target_key] = normalized
        if changed:
            changed_keys.add(target_key)


def _call_center_system_source_extra_delta(
    source: Any,
    payload: Any,
    *,
    resolved_targets: set[str],
    context: dict[str, Any],
    field_configs: dict[str, Any],
    custom_keys: set[str],
) -> list[tuple[str, Any, bool]]:
    if getattr(source, "technical_key", None) != "call_center_employee_by_internal_number":
        return []
    if "custom_fio" in resolved_targets or "custom_fio" not in custom_keys:
        return []
    data = payload.get("data") if isinstance(payload, dict) else None
    if not isinstance(data, dict):
        return []
    resolution_status = str(data.get("resolution_status") or "").strip()
    if resolution_status not in {"not_found_in_successful_response", "ambiguous_internal_number"}:
        return []
    return [
        _prepare_value_delta(
            "custom_fio",
            None,
            context=context,
            field_configs=field_configs,
            custom_keys=custom_keys,
        )
    ]


def _apply_profile_patch(profile: ClientProfile, data: dict[str, Any], custom_fields: dict[str, Any]) -> None:
    for field in SYSTEM_PROFILE_FIELDS:
        if field in data:
            setattr(profile, field, _profile_field_value_for_storage(field, data[field]))
    if "custom_fields" in data:
        profile.custom_fields = strip_system_profile_keys_from_custom_fields(custom_fields)


def apply_client_card_profile_save_pipeline(
    db: Session,
    profile: ClientProfile | None,
    incoming: dict[str, Any],
    merged_custom_fields: dict[str, Any],
    *,
    client_id: str | None = None,
) -> SavePipelineResult:
    rules = [_copy_rule(rule) for rule in _active_save_rules(db)]
    sources_by_id = {source.id: _copy_source(source) for source in db.execute(select(ClientCardSourceConfig)).scalars().all()}
    custom_keys = _custom_field_keys(db)
    field_configs = _field_configs(db)
    updated_at_snapshot = profile.updated_at if profile is not None else None
    profile_id = profile.id if profile is not None else None
    snapshot_client_id = profile.client_id if profile is not None else client_id
    if not snapshot_client_id:
        raise ValueError("client_id is required when profile is missing")
    changed_keys = _incoming_changed_keys(incoming)
    base = _profile_snapshot(profile)
    context = {field: base.get(field) for field in SYSTEM_PROFILE_FIELDS}
    context.update({key: value for key, value in incoming.items() if key in SYSTEM_PROFILE_FIELDS})
    context.update(strip_system_profile_keys_from_custom_fields(merged_custom_fields))
    custom_fields = strip_system_profile_keys_from_custom_fields(merged_custom_fields)
    staged: dict[str, Any] = {}
    resolved_targets: set[str] = set()

    if db.in_transaction():
        if db.new or db.dirty or db.deleted:
            raise RuntimeError("client-card save pipeline requires a clean read phase")
        db.rollback()

    for rule in rules:
        actions = _rule_save_actions(rule)
        if not actions:
            continue
        deps = _save_rule_dependencies(rule, sources_by_id=sources_by_id)
        if not deps or not (changed_keys & deps):
            continue
        if not _rule_matches(context, rule):
            continue
        for action in actions:
            if action.get("action") == "set_value":
                target_key = action.get("target_key")
                if not target_key or target_key in resolved_targets:
                    continue
                raw_value = _action_value(action, context)
                _apply_value_to_maps(
                    target_key,
                    raw_value,
                    staged=staged,
                    custom_fields=custom_fields,
                    context=context,
                    field_configs=field_configs,
                    custom_keys=custom_keys,
                    changed_keys=changed_keys,
                )
                resolved_targets.add(target_key)
                continue
            if action.get("action") != "lookup_source":
                continue
            source = sources_by_id.get(action.get("source_id") or "")
            if source is None or not source.is_active:
                continue
            try:
                from .client_card_sources import MISSING, execute_source, read_response_path
                from .client_card_sources import ClientCardSourceError

                result = execute_source(source, context)
                source_delta: list[tuple[str, Any, bool]] = []
                for item in source.mapping_json or []:
                    target_key = item.get("target_key")
                    if target_key in resolved_targets:
                        continue
                    value = read_response_path(result.payload, item.get("response_path"))
                    if value is MISSING or not target_key:
                        continue
                    source_delta.append(_prepare_value_delta(
                        target_key,
                        value,
                        context=context,
                        field_configs=field_configs,
                        custom_keys=custom_keys,
                    ))
                source_delta.extend(
                    _call_center_system_source_extra_delta(
                        source,
                        result.payload,
                        resolved_targets=resolved_targets | {target_key for target_key, _, _ in source_delta},
                        context=context,
                        field_configs=field_configs,
                        custom_keys=custom_keys,
                    )
                )
                _merge_value_delta(
                    source_delta,
                    staged=staged,
                    custom_fields=custom_fields,
                    context=context,
                    custom_keys=custom_keys,
                    changed_keys=changed_keys,
                )
                resolved_targets.update(target_key for target_key, _, _ in source_delta)
            except (ClientCardSourceError, HTTPException):
                if source.on_error == "block_save":
                    raise _safe_source_error(source)
                continue

    if profile_id:
        profile = _locked_profile(db, profile_id)
        if profile is None or profile.updated_at != updated_at_snapshot:
            raise _retry_conflict()
    else:
        if _locked_client(db, snapshot_client_id) is None:
            raise _retry_conflict()
        existing = _locked_profile_by_client_id(db, snapshot_client_id)
        if existing is not None:
            raise _retry_conflict()
        now = datetime.utcnow()
        profile = ClientProfile(
            id=str(uuid.uuid4()),
            client_id=snapshot_client_id,
            custom_fields={},
            created_at=now,
            updated_at=now,
        )
        db.add(profile)

    _apply_profile_patch(profile, incoming, custom_fields)
    for target_key, value in staged.items():
        if target_key in SYSTEM_PROFILE_FIELDS:
            setattr(profile, target_key, _profile_field_value_for_storage(target_key, value))
        elif target_key in custom_keys:
            current = dict(profile.custom_fields or {})
            if value is None:
                current.pop(target_key, None)
            else:
                current[target_key] = value
            profile.custom_fields = current
    profile.custom_fields = strip_system_profile_keys_from_custom_fields(profile.custom_fields)
    profile.updated_at = datetime.utcnow()
    return SavePipelineResult(profile=profile, changed_keys=changed_keys)


def apply_client_card_profile_local_save_rules(
    db: Session,
    profile: ClientProfile,
    incoming: dict[str, Any],
    merged_custom_fields: dict[str, Any],
) -> SavePipelineResult:
    rules = [_copy_rule(rule) for rule in _active_save_rules(db)]
    custom_keys = _custom_field_keys(db)
    field_configs = _field_configs(db)
    changed_keys = _incoming_changed_keys(incoming)
    base = _profile_snapshot(profile)
    context = {field: base.get(field) for field in SYSTEM_PROFILE_FIELDS}
    context.update({key: value for key, value in incoming.items() if key in SYSTEM_PROFILE_FIELDS})
    context.update(strip_system_profile_keys_from_custom_fields(merged_custom_fields))
    custom_fields = strip_system_profile_keys_from_custom_fields(merged_custom_fields)
    staged: dict[str, Any] = {}
    resolved_targets: set[str] = set()

    for rule in rules:
        actions = [
            action
            for action in _rule_save_actions(rule)
            if action.get("action") == "set_value"
        ]
        if not actions:
            continue
        deps: set[str] = set()
        if rule.condition_operator != "always" and rule.condition_field_key:
            deps.add(rule.condition_field_key)
        for action in actions:
            deps.update(_save_action_dependencies(action))
        if not deps or not (changed_keys & deps):
            continue
        if not _rule_matches(context, rule):
            continue
        for action in actions:
            target_key = action.get("target_key")
            if not target_key or target_key in resolved_targets:
                continue
            raw_value = _action_value(action, context)
            _apply_value_to_maps(
                target_key,
                raw_value,
                staged=staged,
                custom_fields=custom_fields,
                context=context,
                field_configs=field_configs,
                custom_keys=custom_keys,
                changed_keys=changed_keys,
            )
            resolved_targets.add(target_key)

    _apply_profile_patch(profile, incoming, custom_fields)
    for target_key, value in staged.items():
        if target_key in SYSTEM_PROFILE_FIELDS:
            setattr(profile, target_key, _profile_field_value_for_storage(target_key, value))
        elif target_key in custom_keys:
            current = dict(profile.custom_fields or {})
            if value is None:
                current.pop(target_key, None)
            else:
                current[target_key] = value
            profile.custom_fields = current
    profile.custom_fields = strip_system_profile_keys_from_custom_fields(profile.custom_fields)
    profile.updated_at = datetime.utcnow()
    return SavePipelineResult(profile=profile, changed_keys=changed_keys)


def apply_client_card_source_manual_run(
    db: Session,
    *,
    client_id: str,
    source_id: str,
) -> ManualSourceRunResult:
    source_model = db.get(ClientCardSourceConfig, source_id)
    if source_model is None:
        raise HTTPException(
            status_code=404,
            detail={"error": {"code": "source_not_found", "message": "Source not found", "details": {}}},
        )
    if not source_model.is_active:
        raise HTTPException(
            status_code=409,
            detail={"error": {"code": "source_inactive", "message": "Source is inactive", "details": {}}},
        )
    if db.get(Client, client_id) is None:
        raise HTTPException(
            status_code=404,
            detail={"error": {"code": "client_not_found", "message": "Client not found", "details": {}}},
        )

    source = _copy_source(source_model)
    profile = db.execute(select(ClientProfile).where(ClientProfile.client_id == client_id)).scalar_one_or_none()
    updated_at_snapshot = profile.updated_at if profile is not None else None
    profile_id = profile.id if profile is not None else None
    base = _profile_snapshot(profile)
    context = {field: base.get(field) for field in SYSTEM_PROFILE_FIELDS}
    context.update(base.get("custom_fields") or {})
    field_configs = _field_configs(db)

    from .client_card_sources import source_template_field_refs

    for field_key in sorted(source_template_field_refs(source)):
        if _normalize(context.get(field_key)) == "":
            raise _missing_source_template_value(field_key, field_configs)

    if db.in_transaction():
        if db.new or db.dirty or db.deleted:
            raise RuntimeError("client-card source manual run requires a clean read phase")
        db.rollback()

    try:
        from .client_card_sources import ClientCardSourceError, MISSING, execute_source, read_response_path

        result = execute_source(source, context)
    except ClientCardSourceError:
        raise _safe_source_error(source)

    custom_keys = _custom_field_keys(db)
    source_delta: list[tuple[str, Any, bool]] = []
    for item in source.mapping_json or []:
        target_key = item.get("target_key")
        value = read_response_path(result.payload, item.get("response_path"))
        if value is MISSING or not target_key:
            continue
        source_delta.append(
            _prepare_value_delta(
                target_key,
                value,
                context=context,
                field_configs=field_configs,
                custom_keys=custom_keys,
            )
        )
    source_delta.extend(
        _call_center_system_source_extra_delta(
            source,
            result.payload,
            resolved_targets={target_key for target_key, _, _ in source_delta},
            context=context,
            field_configs=field_configs,
            custom_keys=custom_keys,
        )
    )

    if profile_id:
        profile = _locked_profile(db, profile_id)
        if profile is None or profile.updated_at != updated_at_snapshot:
            raise _retry_conflict()
    else:
        if _locked_client(db, client_id) is None:
            raise _retry_conflict()
        existing = _locked_profile_by_client_id(db, client_id)
        if existing is not None:
            raise _retry_conflict()
        now = datetime.utcnow()
        profile = ClientProfile(
            id=str(uuid.uuid4()),
            client_id=client_id,
            custom_fields={},
            created_at=now,
            updated_at=now,
        )
        db.add(profile)

    changed_keys: set[str] = set()
    staged: dict[str, Any] = {}
    custom_fields = strip_system_profile_keys_from_custom_fields(profile.custom_fields)
    _merge_value_delta(
        source_delta,
        staged=staged,
        custom_fields=custom_fields,
        context=context,
        custom_keys=custom_keys,
        changed_keys=changed_keys,
    )
    for target_key, value in staged.items():
        if target_key in SYSTEM_PROFILE_FIELDS:
            setattr(profile, target_key, _profile_field_value_for_storage(target_key, value))
        elif target_key in custom_keys:
            profile.custom_fields = dict(custom_fields)
    profile.custom_fields = strip_system_profile_keys_from_custom_fields(custom_fields)
    if staged:
        result = apply_client_card_profile_local_save_rules(db, profile, staged, dict(profile.custom_fields or {}))
        changed_keys.update(result.changed_keys)
    profile.updated_at = datetime.utcnow()
    return ManualSourceRunResult(profile=profile, changed_keys=changed_keys)


def apply_client_card_save_derivations(
    db: Session,
    profile: ClientProfile,
    incoming: dict[str, Any],
    merged_custom_fields: dict[str, Any],
) -> None:
    apply_client_card_profile_save_pipeline(db, profile, incoming, merged_custom_fields)
