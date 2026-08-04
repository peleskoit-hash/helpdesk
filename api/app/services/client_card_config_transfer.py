from __future__ import annotations

import argparse
import copy
import hashlib
import json
import re
import sys
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any
from urllib.parse import unquote_plus, urlparse, urlunparse

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..db import SessionLocal
from ..models import (
    ClientCardBlockConfig,
    ClientCardFieldConfig,
    ClientCardRuleConfig,
    ClientCardSourceConfig,
)
from .client_card_sources import (
    DEV_LOCAL_HTTP_HOSTS,
    ensure_system_source_mapping_json,
    parse_template_refs,
    source_context_field_keys,
    validate_mapping,
    validate_source_payload,
    validate_template_tree,
    validate_url_template_static,
)
from .call_center_employee_resolver import SYSTEM_SOURCE_KEY as CALL_CENTER_EMPLOYEE_SOURCE_KEY


FORMAT_VERSION = 1
SCOPE = "client_card_config"
SORTED_JSON = {"ensure_ascii": False, "sort_keys": True, "separators": (",", ":")}
SECRET_KEY_RE = re.compile(r"(authorization|auth|token|secret|password|passwd|api[_-]?key|cookie)", re.IGNORECASE)
SECRET_TEMPLATE_RE = re.compile(r"\{\{secret:[A-Z0-9_]+\}\}")
MOCK_HOST_RE = re.compile(r"(^|\.)mock(s)?(\.|$)|(^|\.)example\.(com|org|net)$", re.IGNORECASE)
SYSTEM_BLOCK_KEYS = {"tg", "profile", "department", "work", "verification"}
class ClientCardTransferError(RuntimeError):
    pass


@dataclass
class TransferStats:
    created: dict[str, int] = field(default_factory=dict)
    updated: dict[str, int] = field(default_factory=dict)
    skipped: dict[str, int] = field(default_factory=dict)
    conflicts: list[dict[str, str]] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    def inc(self, bucket: str, table: str) -> None:
        target = getattr(self, bucket)
        target[table] = target.get(table, 0) + 1

    def as_dict(self) -> dict[str, Any]:
        return {
            "created": dict(sorted(self.created.items())),
            "updated": dict(sorted(self.updated.items())),
            "skipped": dict(sorted(self.skipped.items())),
            "conflicts": self.conflicts,
            "warnings": self.warnings,
        }


def _now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def _datetime_iso(value: datetime | None) -> str | None:
    if value is None:
        return None
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.replace(microsecond=0).isoformat()


def _parse_export_created_at(value: Any) -> datetime | None:
    if not isinstance(value, str) or not value.strip():
        return None
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError:
        return None
    if parsed.tzinfo is not None:
        parsed = parsed.astimezone(timezone.utc).replace(tzinfo=None)
    return parsed


def _canonical(value: Any) -> str:
    return json.dumps(value, **SORTED_JSON)


def _hash_key(prefix: str, payload: Any) -> str:
    digest = hashlib.sha256(_canonical(payload).encode("utf-8")).hexdigest()[:20]
    return f"{prefix}_{digest}"


def _source_stable_key(source: ClientCardSourceConfig, payload: dict[str, Any] | None = None) -> str:
    if source.technical_key:
        return source.technical_key
    data = payload or _source_payload(source)
    return _hash_key("custom_source", {key: data.get(key) for key in sorted(data) if key not in {"source_key", "technical_key"}})


def _rule_stable_key(rule: ClientCardRuleConfig, *, sources_by_id: dict[str, str]) -> str:
    if rule.technical_key:
        return rule.technical_key
    actions = _actions_to_export(rule.actions_json or [], sources_by_id=sources_by_id)
    return _hash_key(
        "custom_rule",
        {
            "name": rule.name,
            "condition_field_key": rule.condition_field_key or "",
            "condition_operator": rule.condition_operator,
            "condition_value": rule.condition_value,
            "actions": actions,
        },
    )


def _is_secret_key(key: str) -> bool:
    return bool(SECRET_KEY_RE.search(str(key)))


def _sanitize_json_templates(value: Any, *, in_secret_key: bool = False, warnings: list[str] | None = None, path: str = "") -> Any:
    if isinstance(value, dict):
        sanitized: dict[str, Any] = {}
        for key, item in value.items():
            key_text = str(key)
            secretish = in_secret_key or _is_secret_key(key_text)
            child_path = f"{path}.{key_text}" if path else key_text
            sanitized_item = _sanitize_json_templates(item, in_secret_key=secretish, warnings=warnings, path=child_path)
            if sanitized_item is _REDACTED:
                if warnings is not None:
                    warnings.append(f"redacted secret-like source template value at {child_path}")
                continue
            sanitized[key_text] = sanitized_item
        return sanitized
    if isinstance(value, list):
        result = []
        for index, item in enumerate(value):
            sanitized_item = _sanitize_json_templates(item, in_secret_key=in_secret_key, warnings=warnings, path=f"{path}[{index}]")
            if sanitized_item is not _REDACTED:
                result.append(sanitized_item)
        return result
    if isinstance(value, str):
        if in_secret_key and not SECRET_TEMPLATE_RE.search(value):
            return _REDACTED
        return value
    if in_secret_key and value is not None:
        return _REDACTED
    return value


def _sanitize_url_template(url_template: str | None, *, warnings: list[str] | None = None) -> str | None:
    if not url_template:
        return url_template
    parsed = urlparse(url_template)
    if parsed.username is not None or parsed.password is not None:
        raise ClientCardTransferError("Source URL contains credentials and cannot be exported")
    query_items = []
    redacted = False
    for raw_part in parsed.query.split("&") if parsed.query else []:
        raw_key, separator, raw_value = raw_part.partition("=")
        key = unquote_plus(raw_key)
        value = unquote_plus(raw_value) if separator else ""
        if _is_secret_key(key) and not SECRET_TEMPLATE_RE.fullmatch(value.strip()):
            redacted = True
            continue
        query_items.append(raw_part)
    if redacted and warnings is not None:
        warnings.append("redacted secret-like source URL query parameter")
    return urlunparse(parsed._replace(query="&".join(query_items)))


class _Redacted:
    pass


_REDACTED = _Redacted()


def _is_dev_only_url(url_template: str | None) -> bool:
    parsed = urlparse(url_template or "")
    host = (parsed.hostname or "").strip().lower().rstrip(".")
    if not host:
        return False
    return host in DEV_LOCAL_HTTP_HOSTS or host.endswith(".localhost") or bool(MOCK_HOST_RE.search(host))


def _source_payload(source: ClientCardSourceConfig, *, warnings: list[str] | None = None) -> dict[str, Any]:
    url_template = _sanitize_url_template(source.url_template, warnings=warnings)
    dev_only = _is_dev_only_url(url_template)
    headers = _sanitize_json_templates(source.headers_template_json, warnings=warnings, path="headers_template_json")
    query = _sanitize_json_templates(source.query_template_json, warnings=warnings, path="query_template_json")
    body = _sanitize_json_templates(source.body_template_json, warnings=warnings, path="body_template_json")
    return {
        "source_key": source.technical_key or "",
        "technical_key": source.technical_key,
        "name": source.name,
        "kind": source.kind,
        "is_active": bool(source.is_active) and not dev_only,
        "dev_only": dev_only,
        "method": source.method,
        "url_template": url_template,
        "query_template_json": query,
        "headers_template_json": headers,
        "body_template_json": body,
        "timeout_ms": source.timeout_ms,
        "mapping_json": copy.deepcopy(source.mapping_json or []),
        "on_error": source.on_error,
    }


def _actions_to_export(
    actions: list[dict[str, Any]],
    *,
    sources_by_id: dict[str, str],
    warnings: list[str] | None = None,
    rule_name: str = "",
) -> list[dict[str, Any]]:
    exported: list[dict[str, Any]] = []
    for raw in actions or []:
        action = copy.deepcopy(raw)
        if action.get("action") == "lookup_source":
            source_id = action.pop("source_id", None)
            source_key = sources_by_id.get(source_id or "")
            if not source_key:
                raise ClientCardTransferError(f"Rule action references unknown source_id: {source_id}")
            action["source_key"] = source_key
        exported.append(action)
    return exported


def _actions_to_import(actions: list[dict[str, Any]], *, sources_by_key: dict[str, ClientCardSourceConfig]) -> list[dict[str, Any]]:
    imported: list[dict[str, Any]] = []
    for raw in actions or []:
        action = copy.deepcopy(raw)
        if action.get("action") == "lookup_source":
            source_key = action.pop("source_key", None)
            source = sources_by_key.get(source_key or "")
            if source is None:
                raise ClientCardTransferError(f"Rule action references unknown source_key: {source_key}")
            action["source_id"] = source.id
        imported.append(action)
    return imported


def export_client_card_config(db: Session) -> dict[str, Any]:
    warnings: list[str] = []
    blocks = db.execute(
        select(ClientCardBlockConfig).order_by(ClientCardBlockConfig.sort_order.asc(), ClientCardBlockConfig.key.asc())
    ).scalars().all()
    fields = db.execute(
        select(ClientCardFieldConfig).order_by(ClientCardFieldConfig.block_key.asc(), ClientCardFieldConfig.sort_order.asc(), ClientCardFieldConfig.key.asc())
    ).scalars().all()
    sources = db.execute(
        select(ClientCardSourceConfig).order_by(ClientCardSourceConfig.created_at.asc(), ClientCardSourceConfig.technical_key.asc(), ClientCardSourceConfig.id.asc())
    ).scalars().all()
    source_payloads = []
    sources_by_id: dict[str, str] = {}
    for index, source in enumerate(sources, start=1):
        payload = _source_payload(source, warnings=warnings)
        payload["source_key"] = _source_stable_key(source, payload)
        payload["order"] = index
        sources_by_id[source.id] = payload["source_key"]
        if source.technical_key == CALL_CENTER_EMPLOYEE_SOURCE_KEY:
            continue
        source_payloads.append(payload)

    rules = db.execute(select(ClientCardRuleConfig)).scalars().all()
    rule_payloads = []
    for rule in rules:
        actions = _actions_to_export(
            rule.actions_json or [],
            sources_by_id=sources_by_id,
            warnings=warnings,
            rule_name=rule.technical_key or rule.name or rule.id,
        )
        rule_key = rule.technical_key or _hash_key(
            "custom_rule",
            {
                "name": rule.name,
                "condition_field_key": rule.condition_field_key or "",
                "condition_operator": rule.condition_operator,
                "condition_value": rule.condition_value,
                "actions": actions,
            },
        )
        rule_payloads.append(
            {
                "rule_key": rule_key,
                "technical_key": rule.technical_key,
                "name": rule.name,
                "is_active": bool(rule.is_active),
                "is_system": bool(rule.is_system),
                "sort_order": rule.sort_order,
                "condition_field_key": rule.condition_field_key or "",
                "condition_operator": rule.condition_operator,
                "condition_value": rule.condition_value,
                "actions": actions,
            }
        )
    rule_payloads.sort(key=lambda item: (item["sort_order"], item["technical_key"] or item["rule_key"]))

    config_timestamps = [
        value
        for value in [
            *(block.created_at for block in blocks),
            *(item.created_at for item in fields),
            *(item.created_at for item in sources),
            *(item.created_at for item in rules),
        ]
        if value is not None
    ]
    data = {
        "format_version": FORMAT_VERSION,
        "scope": SCOPE,
        "created_at": _datetime_iso(min(config_timestamps)) or _now_iso(),
        "stats": {
            "blocks": len(blocks),
            "fields": len(fields),
            "rules": len(rule_payloads),
            "sources": len(source_payloads),
            "dev_only_sources_marked_inactive": sum(1 for item in source_payloads if item["dev_only"]),
        },
        "blocks": [
            {
                "key": block.key,
                "title": block.title,
                "color_scheme": block.color_scheme,
                "sort_order": block.sort_order,
                "is_active": bool(block.is_active),
                "show_in_card": block.show_in_card is not False,
                "show_in_editor": block.show_in_editor is not False,
                "show_title": block.show_title is not False,
            }
            for block in blocks
        ],
        "fields": [
            {
                "key": item.key,
                "label": item.label,
                "field_type": item.field_type,
                "block_key": item.block_key,
                "sort_order": item.sort_order,
                "is_visible": bool(item.is_visible),
                "is_visible_in_editor": item.is_visible_in_editor is not False,
                "is_editable": bool(item.is_editable),
                "is_required": bool(item.is_required),
                "is_system": bool(item.is_system),
                "options_json": copy.deepcopy(item.options_json),
            }
            for item in fields
        ],
        "sources": source_payloads,
        "rules": rule_payloads,
        "warnings": sorted(set(warnings)),
    }
    _assert_export_is_safe(data)
    return data


def _assert_export_is_safe(data: dict[str, Any]) -> None:
    raw = _canonical(data)
    forbidden_keys = [
        "ticket_messages",
        "tickets",
        "clients",
        "client_profiles",
        "users",
        "telegram_updates",
        "call_center_profile_snapshot_json",
        "password_hash",
    ]
    for key in forbidden_keys:
        if f'"{key}"' in raw:
            raise ClientCardTransferError(f"Forbidden table/data key in export: {key}")


def _rule_uses_unexported_system_source(rule: ClientCardRuleConfig, *, sources_by_id: dict[str, str]) -> bool:
    for action in rule.actions_json or []:
        if action.get("action") == "lookup_source" and sources_by_id.get(action.get("source_id") or "") == CALL_CENTER_EMPLOYEE_SOURCE_KEY:
            return True
    return False


def _validate_payload(data: dict[str, Any]) -> None:
    if data.get("format_version") != FORMAT_VERSION:
        raise ClientCardTransferError("Unsupported client-card export format_version")
    if data.get("scope") != SCOPE:
        raise ClientCardTransferError("Unsupported export scope")
    for section in ("blocks", "fields", "rules", "sources"):
        if not isinstance(data.get(section), list):
            raise ClientCardTransferError(f"Export section must be a list: {section}")


def _payload_references_system_source(data: dict[str, Any]) -> bool:
    for item in data.get("sources") or []:
        if (item.get("source_key") or item.get("technical_key")) == CALL_CENTER_EMPLOYEE_SOURCE_KEY:
            return True
    for rule in data.get("rules") or []:
        for action in rule.get("actions") or []:
            if action.get("action") == "lookup_source" and action.get("source_key") == CALL_CENTER_EMPLOYEE_SOURCE_KEY:
                return True
    return False


def _mark(stats: TransferStats, action: str, table: str) -> None:
    if action in {"created", "updated", "skipped"}:
        stats.inc(action, table)


def _apply_scalar_changes(obj: Any, values: dict[str, Any], fields: list[str]) -> bool:
    changed = False
    for name in fields:
        if getattr(obj, name) != values.get(name):
            setattr(obj, name, values.get(name))
            changed = True
    return changed


def _existing_rule_key(rule: ClientCardRuleConfig, *, sources_by_id: dict[str, str]) -> str:
    return _rule_stable_key(rule, sources_by_id=sources_by_id)


def import_client_card_config(db: Session, data: dict[str, Any], *, apply: bool = False) -> dict[str, Any]:
    _validate_payload(data)
    stats = TransferStats()
    now = datetime.utcnow()
    base_created_at = _parse_export_created_at(data.get("created_at")) or now

    if db.in_transaction():
        db.rollback()
    transaction = db.begin()
    try:
        _import_blocks(db, data["blocks"], stats=stats, now=now, base_created_at=base_created_at)
        db.flush()
        _import_fields(db, data["fields"], stats=stats, now=now, base_created_at=base_created_at)
        db.flush()
        sources_by_key = _import_sources(
            db,
            data["sources"],
            stats=stats,
            now=now,
            base_created_at=base_created_at,
            include_system_source=_payload_references_system_source(data),
        )
        db.flush()
        _import_rules(db, data["rules"], sources_by_key=sources_by_key, stats=stats, now=now, base_created_at=base_created_at)
        if stats.conflicts or not apply:
            transaction.rollback()
            return {"dry_run": not apply, "applied": False, **stats.as_dict()}
        transaction.commit()
    except Exception:
        transaction.rollback()
        raise
    return {"dry_run": not apply, "applied": bool(apply), **stats.as_dict()}


def _import_blocks(db: Session, blocks: list[dict[str, Any]], *, stats: TransferStats, now: datetime, base_created_at: datetime) -> None:
    seen_keys: set[str] = set()
    existing = {item.key: item for item in db.execute(select(ClientCardBlockConfig)).scalars().all()}
    for index, item in enumerate(blocks):
        key = (item.get("key") or "").strip()
        if not key:
            stats.conflicts.append({"table": "client_card_block_configs", "key": "", "reason": "missing key"})
            continue
        if key in seen_keys:
            stats.conflicts.append({"table": "client_card_block_configs", "key": key, "reason": "duplicate key in import"})
            continue
        seen_keys.add(key)
        values = {
            "title": item.get("title") or key,
            "color_scheme": item.get("color_scheme") or "gray",
            "sort_order": int(item.get("sort_order") or 0),
            "is_active": bool(item.get("is_active")),
            "show_in_card": item.get("show_in_card") is not False,
            "show_in_editor": item.get("show_in_editor") is not False,
            "show_title": item.get("show_title") is not False,
        }
        block = existing.get(key)
        if block is None:
            created_at = base_created_at + timedelta(microseconds=index)
            db.add(ClientCardBlockConfig(id=str(uuid.uuid4()), key=key, created_at=created_at, updated_at=now, **values))
            _mark(stats, "created", "client_card_block_configs")
        else:
            changed = _apply_scalar_changes(block, values, list(values))
            if changed:
                block.updated_at = now
                _mark(stats, "updated", "client_card_block_configs")
            else:
                _mark(stats, "skipped", "client_card_block_configs")


def _import_fields(db: Session, fields: list[dict[str, Any]], *, stats: TransferStats, now: datetime, base_created_at: datetime) -> None:
    seen_keys: set[str] = set()
    block_keys = set(db.execute(select(ClientCardBlockConfig.key)).scalars().all())
    existing = {item.key: item for item in db.execute(select(ClientCardFieldConfig)).scalars().all()}
    for index, item in enumerate(fields):
        key = (item.get("key") or "").strip()
        block_key = (item.get("block_key") or "").strip()
        if not key or block_key not in block_keys:
            stats.conflicts.append({"table": "client_card_field_configs", "key": key, "reason": "missing key or block dependency"})
            continue
        if key in seen_keys:
            stats.conflicts.append({"table": "client_card_field_configs", "key": key, "reason": "duplicate key in import"})
            continue
        seen_keys.add(key)
        values = {
            "label": item.get("label") or key,
            "field_type": item.get("field_type") or "text",
            "block_key": block_key,
            "sort_order": int(item.get("sort_order") or 0),
            "is_visible": bool(item.get("is_visible")),
            "is_visible_in_editor": item.get("is_visible_in_editor") is not False,
            "is_editable": bool(item.get("is_editable")),
            "is_required": bool(item.get("is_required")),
            "is_system": bool(item.get("is_system")),
            "options_json": copy.deepcopy(item.get("options_json")),
        }
        field = existing.get(key)
        if field is None:
            created_at = base_created_at + timedelta(seconds=1, microseconds=index)
            db.add(ClientCardFieldConfig(id=str(uuid.uuid4()), key=key, created_at=created_at, updated_at=now, **values))
            _mark(stats, "created", "client_card_field_configs")
        else:
            conflict = _field_immutable_conflict(field, values)
            if conflict:
                stats.conflicts.append({"table": "client_card_field_configs", "key": key, "reason": conflict})
                continue
            changed = _apply_scalar_changes(field, values, list(values))
            if changed:
                field.updated_at = now
                _mark(stats, "updated", "client_card_field_configs")
            else:
                _mark(stats, "skipped", "client_card_field_configs")


def _field_immutable_conflict(field: ClientCardFieldConfig, values: dict[str, Any]) -> str | None:
    if bool(field.is_system) != bool(values["is_system"]):
        return "is_system mismatch"
    if field.is_system:
        for name in ("field_type", "is_editable", "is_required"):
            if getattr(field, name) != values[name]:
                return f"system field immutable mismatch: {name}"
    return None


def _import_sources(
    db: Session,
    sources: list[dict[str, Any]],
    *,
    stats: TransferStats,
    now: datetime,
    base_created_at: datetime,
    include_system_source: bool = False,
) -> dict[str, ClientCardSourceConfig]:
    seen_keys: set[str] = set()
    existing_by_technical = {
        item.technical_key: item
        for item in db.execute(select(ClientCardSourceConfig).where(ClientCardSourceConfig.technical_key.is_not(None))).scalars().all()
    }
    existing_sources = list(db.execute(select(ClientCardSourceConfig)).scalars().all())
    existing_by_generated = {_source_stable_key(item): item for item in existing_sources if not item.technical_key}
    result: dict[str, ClientCardSourceConfig] = {}
    if include_system_source:
        result[CALL_CENTER_EMPLOYEE_SOURCE_KEY] = _ensure_system_source(db, now=now)
    for item in sources:
        source_key = (item.get("source_key") or item.get("technical_key") or "").strip()
        if not source_key:
            stats.conflicts.append({"table": "client_card_source_configs", "key": "", "reason": "missing source_key"})
            continue
        if source_key == CALL_CENTER_EMPLOYEE_SOURCE_KEY or item.get("technical_key") == CALL_CENTER_EMPLOYEE_SOURCE_KEY:
            _mark(stats, "skipped", "client_card_source_configs")
            continue
        if source_key in seen_keys:
            stats.conflicts.append({"table": "client_card_source_configs", "key": source_key, "reason": "duplicate source_key in import"})
            continue
        seen_keys.add(source_key)
        dev_only = bool(item.get("dev_only")) or _is_dev_only_url(item.get("url_template"))
        _validate_import_source(db, item, dev_only=dev_only)
        values = {
            "technical_key": item.get("technical_key") or source_key,
            "name": item.get("name") or source_key,
            "kind": item.get("kind") or "http",
            "is_active": bool(item.get("is_active")) and not dev_only,
            "method": item.get("method") or "GET",
            "url_template": item.get("url_template") or "",
            "query_template_json": copy.deepcopy(item.get("query_template_json")),
            "headers_template_json": copy.deepcopy(item.get("headers_template_json")),
            "body_template_json": copy.deepcopy(item.get("body_template_json")),
            "timeout_ms": int(item.get("timeout_ms") or 3000),
            "mapping_json": copy.deepcopy(item.get("mapping_json") or []),
            "on_error": item.get("on_error") or "ignore",
        }
        source = existing_by_technical.get(values["technical_key"]) or existing_by_generated.get(source_key)
        if source is None:
            order = int(item.get("order") or len(result) + 1)
            created_at = base_created_at + timedelta(seconds=2, microseconds=order)
            source = ClientCardSourceConfig(id=str(uuid.uuid4()), created_at=created_at, updated_at=now, **values)
            db.add(source)
            _mark(stats, "created", "client_card_source_configs")
        else:
            changed = _apply_scalar_changes(source, values, list(values))
            if changed:
                source.updated_at = now
                _mark(stats, "updated", "client_card_source_configs")
            else:
                _mark(stats, "skipped", "client_card_source_configs")
        result[source_key] = source
    return result


def _ensure_system_source(db: Session, *, now: datetime) -> ClientCardSourceConfig:
    source = db.execute(
        select(ClientCardSourceConfig).where(ClientCardSourceConfig.technical_key == CALL_CENTER_EMPLOYEE_SOURCE_KEY)
    ).scalar_one_or_none()
    values = {
        "technical_key": CALL_CENTER_EMPLOYEE_SOURCE_KEY,
        "name": "Сотрудник КЦ по внутреннему номеру",
        "kind": "system",
        "is_active": True,
        "method": "POST",
        "url_template": "system://call-center-employee-resolver",
        "query_template_json": None,
        "headers_template_json": None,
        "body_template_json": None,
        "timeout_ms": 5000,
        "on_error": "ignore",
    }
    if source is None:
        source = ClientCardSourceConfig(
            id=str(uuid.uuid4()),
            mapping_json=ensure_system_source_mapping_json(None),
            created_at=now,
            updated_at=now,
            **values,
        )
        db.add(source)
        return source
    changed = _apply_scalar_changes(source, values, list(values))
    merged_mapping = ensure_system_source_mapping_json(source.mapping_json)
    if merged_mapping != (source.mapping_json or []):
        source.mapping_json = merged_mapping
        changed = True
    if changed:
        source.updated_at = now
    return source


def _validate_import_source(db: Session, item: dict[str, Any], *, dev_only: bool) -> None:
    parsed = urlparse(item.get("url_template") or "")
    if parsed.username is not None or parsed.password is not None:
        raise ClientCardTransferError("Source URL contains credentials and cannot be imported")
    if dev_only:
        field_keys = source_context_field_keys(db)
        parse_template_refs(item.get("url_template") or "", field_keys=field_keys, allow_secrets=False)
        validate_template_tree(copy.deepcopy(item.get("query_template_json")), field_keys, allow_secrets_in_values=False, validate_keys=True)
        validate_template_tree(copy.deepcopy(item.get("body_template_json")), field_keys, allow_secrets_in_values=False, validate_keys=True)
        validate_template_tree(copy.deepcopy(item.get("headers_template_json")), field_keys, allow_secrets_in_values=True, validate_keys=True)
        validate_mapping(db, copy.deepcopy(item.get("mapping_json") or []))
        return
    if item.get("url_template"):
        validate_url_template_static(item["url_template"])
    validate_source_payload(
        db,
        method=item.get("method") or "GET",
        kind=item.get("kind") or "http",
        on_error=item.get("on_error") or "ignore",
        url_template=item.get("url_template") or "",
        mapping=copy.deepcopy(item.get("mapping_json") or []),
        query=copy.deepcopy(item.get("query_template_json")),
        headers=copy.deepcopy(item.get("headers_template_json")),
        body=copy.deepcopy(item.get("body_template_json")),
    )


def _import_rules(
    db: Session,
    rules: list[dict[str, Any]],
    *,
    sources_by_key: dict[str, ClientCardSourceConfig],
    stats: TransferStats,
    now: datetime,
    base_created_at: datetime,
) -> None:
    field_keys = set(db.execute(select(ClientCardFieldConfig.key)).scalars().all())
    block_keys = set(db.execute(select(ClientCardBlockConfig.key)).scalars().all())
    sources_by_id = {
        source.id: source.technical_key or _source_stable_key(source)
        for source in db.execute(select(ClientCardSourceConfig)).scalars().all()
    }
    existing_rules = list(db.execute(select(ClientCardRuleConfig)).scalars().all())
    existing_by_key = {_existing_rule_key(rule, sources_by_id=sources_by_id): rule for rule in existing_rules}
    existing_by_technical = {rule.technical_key: rule for rule in existing_rules if rule.technical_key}
    seen_keys: set[str] = set()

    for index, item in enumerate(rules):
        rule_key = (item.get("rule_key") or item.get("technical_key") or "").strip()
        if not rule_key:
            stats.conflicts.append({"table": "client_card_rule_configs", "key": "", "reason": "missing rule_key"})
            continue
        if rule_key in seen_keys:
            stats.conflicts.append({"table": "client_card_rule_configs", "key": rule_key, "reason": "duplicate rule_key in import"})
            continue
        seen_keys.add(rule_key)
        condition_field_key = item.get("condition_field_key") or ""
        if item.get("condition_operator") != "always" and condition_field_key and condition_field_key not in field_keys:
            stats.conflicts.append({"table": "client_card_rule_configs", "key": rule_key, "reason": "missing condition field"})
            continue
        try:
            actions = _actions_to_import(item.get("actions") or [], sources_by_key=sources_by_key)
        except ClientCardTransferError as exc:
            stats.conflicts.append({"table": "client_card_rule_configs", "key": rule_key, "reason": str(exc)})
            continue
        action_conflict = _rule_action_dependency_conflict(actions, field_keys=field_keys, block_keys=block_keys)
        if action_conflict:
            stats.conflicts.append({"table": "client_card_rule_configs", "key": rule_key, "reason": action_conflict})
            continue
        values = {
            "technical_key": item.get("technical_key"),
            "name": item.get("name") or rule_key,
            "is_active": bool(item.get("is_active")),
            "is_system": bool(item.get("is_system")),
            "sort_order": int(item.get("sort_order") or 0),
            "condition_field_key": condition_field_key if item.get("condition_operator") != "always" else "",
            "condition_operator": item.get("condition_operator") or "always",
            "condition_value": item.get("condition_value"),
            "actions_json": actions,
        }
        rule = existing_by_technical.get(values["technical_key"]) if values["technical_key"] else None
        if rule is None:
            rule = existing_by_key.get(rule_key)
        if rule is None:
            created_at = base_created_at + timedelta(seconds=3, microseconds=index)
            db.add(ClientCardRuleConfig(id=str(uuid.uuid4()), created_at=created_at, updated_at=now, **values))
            _mark(stats, "created", "client_card_rule_configs")
        else:
            if bool(rule.is_system) != bool(values["is_system"]):
                stats.conflicts.append({"table": "client_card_rule_configs", "key": rule_key, "reason": "is_system mismatch"})
                continue
            changed = _apply_scalar_changes(rule, values, list(values))
            if changed:
                rule.updated_at = now
                _mark(stats, "updated", "client_card_rule_configs")
            else:
                _mark(stats, "skipped", "client_card_rule_configs")


def _rule_action_dependency_conflict(actions: list[dict[str, Any]], *, field_keys: set[str], block_keys: set[str]) -> str | None:
    for action in actions:
        action_name = action.get("action")
        if action_name == "lookup_source":
            continue
        target_type = action.get("target_type", "field")
        target_key = action.get("target_key")
        if target_type == "field" and target_key not in field_keys:
            return f"missing target field: {target_key}"
        if target_type == "block" and target_key not in block_keys:
            return f"missing target block: {target_key}"
        source_key = action.get("source_key")
        if source_key and source_key not in field_keys:
            return f"missing source field: {source_key}"
        if action.get("value_type") == "template":
            for ref in re.findall(r"\{\{([a-zA-Z0-9_]+)\}\}", str(action.get("value") or "")):
                if ref not in field_keys:
                    return f"missing template field: {ref}"
    return None


def write_export(path: Path, data: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def load_export(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def cli(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Export/import client-card configuration safely.")
    subparsers = parser.add_subparsers(dest="command", required=True)
    export_parser = subparsers.add_parser("export")
    export_parser.add_argument("--output", required=True)
    import_parser = subparsers.add_parser("import")
    import_parser.add_argument("--input", required=True)
    import_parser.add_argument("--apply", action="store_true")
    args = parser.parse_args(argv)

    with SessionLocal() as db:
        if args.command == "export":
            data = export_client_card_config(db)
            write_export(Path(args.output), data)
            print(json.dumps({"output": args.output, "stats": data["stats"], "warnings": data.get("warnings", [])}, ensure_ascii=False, indent=2))
            return 0
        data = load_export(Path(args.input))
        result = import_client_card_config(db, data, apply=args.apply)
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 1 if result.get("conflicts") else 0
    return 0


if __name__ == "__main__":
    sys.exit(cli())
