from __future__ import annotations

import re
from datetime import datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..models import ClientCardFieldConfig, ClientCardRuleConfig, ClientProfile
from .client_card import _normalize_custom_field_value

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
        .order_by(ClientCardRuleConfig.sort_order.asc(), ClientCardRuleConfig.created_at.asc())
    ).scalars().all()


def _custom_field_keys(db: Session) -> set[str]:
    return set(
        db.execute(
            select(ClientCardFieldConfig.key).where(ClientCardFieldConfig.is_system.is_(False))
        ).scalars().all()
    )


def _field_configs(db: Session) -> dict[str, ClientCardFieldConfig]:
    return {field.key: field for field in db.execute(select(ClientCardFieldConfig)).scalars().all()}


def _profile_context(profile: ClientProfile, incoming: dict[str, Any], merged_custom_fields: dict[str, Any]) -> dict[str, Any]:
    context = {field: getattr(profile, field, None) for field in SYSTEM_PROFILE_FIELDS}
    context.update({key: value for key, value in incoming.items() if key in SYSTEM_PROFILE_FIELDS})
    context.update(merged_custom_fields or {})
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


def _save_action_dependencies(action: dict[str, Any]) -> set[str]:
    value_type = action.get("value_type")
    deps: set[str] = set()
    if value_type in {"field", "map"} and action.get("source_key"):
        deps.add(str(action["source_key"]))
    if value_type == "template":
        deps.update(_template_fields(action.get("value")))
    return deps


def _rule_save_actions(rule: ClientCardRuleConfig) -> list[dict[str, Any]]:
    return [
        action
        for action in (rule.actions_json or [])
        if action.get("action") == "set_value" and action.get("target_type", "field") == "field" and action.get("scope") == "save"
    ]


def _save_rule_dependencies(rule: ClientCardRuleConfig) -> set[str]:
    actions = _rule_save_actions(rule)
    deps: set[str] = set()
    if not actions:
        return deps
    if rule.condition_operator != "always" and rule.condition_field_key:
        deps.add(rule.condition_field_key)
    for action in actions:
        deps.update(_save_action_dependencies(action))
    return deps


def _save_rules_dependencies(rules: list[ClientCardRuleConfig]) -> set[str]:
    deps: set[str] = set()
    for rule in rules:
        deps.update(_save_rule_dependencies(rule))
    return deps


def client_card_derivation_should_run(db: Session, incoming: dict[str, Any], merged_custom_fields: dict[str, Any] | None = None) -> bool:
    rules = _active_save_rules(db)
    return _save_rules_should_run(rules, incoming)


def _save_rules_should_run(rules: list[ClientCardRuleConfig], incoming: dict[str, Any]) -> bool:
    touched = set(incoming.keys())
    if "custom_fields" in touched:
        touched.discard("custom_fields")
        if isinstance(incoming.get("custom_fields"), dict):
            touched.update(incoming["custom_fields"].keys())
    return bool(touched & _save_rules_dependencies(rules))


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
    return _TEMPLATE_FIELD_RE.sub(lambda match: _normalize(context.get(match.group(1))), str(template))


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


def _normalize_derived_value(
    target_key: str,
    value: Any,
    field_configs: dict[str, ClientCardFieldConfig],
    custom_keys: set[str],
) -> Any:
    if target_key in custom_keys:
        if value is None:
            return None
        return _normalize_custom_field_value(field_configs[target_key], value)
    return _normalize_system_value(target_key, field_configs.get(target_key), value)


def apply_client_card_save_derivations(
    db: Session,
    profile: ClientProfile,
    incoming: dict[str, Any],
    merged_custom_fields: dict[str, Any],
) -> None:
    rules = _active_save_rules(db)
    if not rules:
        return
    if not _save_rules_should_run(rules, incoming):
        return

    custom_keys = _custom_field_keys(db)
    field_configs = _field_configs(db)
    context = _profile_context(profile, incoming, merged_custom_fields)
    custom_fields = dict(merged_custom_fields or {})

    for rule in rules:
        actions = _rule_save_actions(rule)
        if not actions or not _rule_matches(context, rule):
            continue
        for action in actions:
            target_key = action.get("target_key")
            if not target_key:
                continue
            raw_value = _action_value(action, context)
            value = _normalize_derived_value(target_key, raw_value, field_configs, custom_keys)
            if target_key in SYSTEM_PROFILE_FIELDS:
                setattr(profile, target_key, value)
            elif target_key in custom_keys:
                if value is None:
                    custom_fields.pop(target_key, None)
                else:
                    custom_fields[target_key] = value
                profile.custom_fields = custom_fields
            context[target_key] = value

    profile.updated_at = datetime.utcnow()
