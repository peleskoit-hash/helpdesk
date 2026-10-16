"""One-off transfer of five bot settings tables, plus read-only reference manifests.

Run from api/: python -m app.services.bot_settings_transfer --help.
No files are copied and no users/saved_files are created or updated.
"""
from __future__ import annotations

import argparse
import copy
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import tempfile
import uuid

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..config import settings
from ..db import SessionLocal
from ..models import (AutomaticMessageTemplate, BotButtonSetting, BotMenuDestinationBinding,
                      BotMenuLevelLayout, BotMenuProfile, SavedFile, User)
from .button_config import build_level_layout_key
from .system_tickets import is_system_menu_type

TABLES = {
    "bot_button_settings": (BotButtonSetting, "button_key"),
    "bot_menu_level_layouts": (BotMenuLevelLayout, "level_key"),
    "bot_menu_profiles": (BotMenuProfile, "menu_type"),
    "bot_menu_destination_bindings": (BotMenuDestinationBinding, "destination_value"),
    "automatic_message_templates": (AutomaticMessageTemplate, "key"),
}
BUTTONS, LAYOUTS, PROFILES, BINDINGS, TEMPLATES = TABLES
USER_FIELDS = ("fixed_assignee_user_id", "deleted_by_user_id")


class BotSettingsTransferError(RuntimeError):
    pass


def _record(row):
    return {col.key: (value.isoformat() if isinstance(value := getattr(row, col.key), datetime) else copy.deepcopy(value))
            for col in row.__table__.columns}


def _same(left, right):
    if isinstance(left, datetime) or isinstance(right, datetime):
        if not isinstance(left, datetime) or not isinstance(right, datetime):
            return False
        def utc(value):
            return value.replace(tzinfo=timezone.utc) if value.tzinfo is None else value.astimezone(timezone.utc)
        return utc(left) == utc(right)
    return json.dumps(left, sort_keys=True, allow_nan=False) == json.dumps(right, sort_keys=True, allow_nan=False)


def _decode(model, raw):
    if not isinstance(raw, dict) or set(raw) != set(model.__table__.columns.keys()):
        raise BotSettingsTransferError("invalid_row_schema")
    row = copy.deepcopy(raw)
    for col in model.__table__.columns:
        value = row[col.key]
        if value is None:
            if not col.nullable:
                raise BotSettingsTransferError("invalid_null")
            continue
        kind = col.type.python_type
        if kind is datetime:
            if not isinstance(value, str):
                raise BotSettingsTransferError("invalid_date")
            parsed = datetime.fromisoformat(value)
            row[col.key] = parsed.astimezone(timezone.utc) if parsed.tzinfo is not None else parsed
        else:
            if col.key in {"layout_json", "files_json", "conditional_files_json"}:
                kind = list
            if type(value) is not kind or kind is str and getattr(col.type, "length", None) and len(value) > col.type.length:
                raise BotSettingsTransferError("invalid_field_type_or_length")
    return row


def _index(rows, key, *, optional=False):
    result = {}
    for row in rows:
        value = row[key]
        if optional and value is None:
            continue
        if not isinstance(value, str) or not value.strip() or value in result:
            raise BotSettingsTransferError("duplicate_or_empty_logical_key")
        result[value] = row
    return result


def _structure(tables):
    buttons = tables[BUTTONS]
    by_key = _index(buttons, "button_key")
    _index(buttons, "stable_button_id", optional=True)
    menus = {row["menu_type"] for row in tables[PROFILES]}
    paths = {}
    for row in buttons:
        paths.setdefault((row["menu_type"], row["button_path"]), []).append(row)
        if row["menu_type"] not in menus and not is_system_menu_type(row["menu_type"]):
            raise BotSettingsTransferError("missing_menu_profile")
        if row["fixed_assignee_enabled"] and not row["fixed_assignee_user_id"]:
            raise BotSettingsTransferError("missing_fixed_assignee")
    for row in buttons:
        visited, parent = {row["button_path"]}, row["parent_path"]
        while parent:
            candidates = paths.get((row["menu_type"], parent), [])
            if parent in visited or len(candidates) != 1:
                raise BotSettingsTransferError("invalid_button_parent")
            folder = candidates[0]
            if folder["button_kind"] not in {"section", "submenu", "group_parent"} and folder["workflow_type"] not in {"submenu", "group_task"}:
                raise BotSettingsTransferError("invalid_button_parent_kind")
            visited.add(parent)
            parent = folder["parent_path"]
    for layout in tables[LAYOUTS]:
        # Nested menus are separate level_key rows, not nested arbitrary JSON.
        menu = layout["menu_type"]
        root = build_level_layout_key(menu, "")
        parents = [path for (name, path) in paths if name == menu and build_level_layout_key(menu, path) == layout["level_key"]]
        if layout["level_key"] == root:
            parent = ""
        elif len(parents) == 1:
            parent = parents[0]
        else:
            raise BotSettingsTransferError("unresolved_layout_level")
        if menu not in menus and not is_system_menu_type(menu):
            raise BotSettingsTransferError("missing_layout_menu")
        seen = set()
        for item in layout["layout_json"] or []:
            if (not isinstance(item, dict) or type(item.get("columns")) is not int or item["columns"] not in {1, 2, 3}
                    or not isinstance(item.get("button_keys"), list) or len(item["button_keys"]) > 3):
                raise BotSettingsTransferError("invalid_layout_row")
            for key in item["button_keys"]:
                button = by_key.get(key) if isinstance(key, str) else None
                if button is None or key in seen or button["menu_type"] != menu or button["parent_path"] != parent:
                    raise BotSettingsTransferError("unresolved_layout_button")
                seen.add(key)
    if any(row["menu_type"] not in menus for row in tables[BINDINGS]):
        raise BotSettingsTransferError("unresolved_destination_menu")
    if sum(row["is_fallback"] and row["is_active"] for row in tables[PROFILES]) > 1:
        raise BotSettingsTransferError("ambiguous_fallback_menu")


def _file_lists(button):
    yield button["files_json"]
    for rule in button["conditional_files_json"] or []:
        if not isinstance(rule, dict) or not isinstance(rule.get("conditions"), list):
            raise BotSettingsTransferError("invalid_conditional_files")
        yield rule.get("files")


def _refs(buttons):
    files, users = set(), set()
    for row in buttons:
        for values in _file_lists(row):
            if values is not None and not isinstance(values, list):
                raise BotSettingsTransferError("invalid_file_list")
            for ref in values or []:
                if not isinstance(ref, str) or not ref.strip():
                    raise BotSettingsTransferError("invalid_file_reference")
                files.add(ref)
        users.update(row[field] for field in USER_FIELDS if row[field] is not None)
    return files, users


def _file_digest(row, cache):
    try:
        if row.id not in cache:
            root = Path(settings.saved_file_storage_dir).resolve()
            path = (root / row.storage_key).resolve()
            if root not in path.parents or not path.is_file() or path.stat().st_size != row.file_size:
                raise BotSettingsTransferError("saved_file_missing_or_invalid")
            digest = hashlib.sha256()
            with path.open("rb") as stream:
                for block in iter(lambda: stream.read(1024 * 1024), b""):
                    digest.update(block)
            cache[row.id] = digest.hexdigest()
        return cache[row.id]
    except OSError:
        raise BotSettingsTransferError("saved_file_unreadable") from None


def _identity(value):
    return value.strip().casefold() if isinstance(value, str) else ""


def _user_manifest(user, users):
    result = {}
    for field in ("login", "email"):
        value = _identity(getattr(user, field))
        result[field] = value if value and sum(_identity(getattr(row, field)) == value for row in users) == 1 else None
    if not any(result.values()):
        raise BotSettingsTransferError("source_employee_has_no_unique_identity")
    return result


def export_bot_settings(db: Session):
    try:
        with db.no_autoflush:
            tables = {name: [_record(row) for row in db.scalars(select(model).order_by(getattr(model, key)))]
                      for name, (model, key) in TABLES.items()}
            _structure(tables)
            file_refs, user_refs = _refs(tables[BUTTONS])
            files = list(db.scalars(select(SavedFile)))
            users = list(db.scalars(select(User)))
            manifest_files, manifest_users, cache = {}, {}, {}
            for ref in sorted(file_refs):
                candidates = [row for row in files if row.id == ref[6:]] if ref.startswith("saved:") else [row for row in files if row.display_filename == ref]
                if len(candidates) != 1:
                    raise BotSettingsTransferError("unresolved_source_saved_file")
                row = candidates[0]
                uuid.UUID(row.id)  # Runtime saved: references require UUIDs.
                manifest_files[ref] = {"id": row.id, "storage_key": row.storage_key, "file_size": row.file_size,
                                       "mime_type": row.mime_type, "sha256": _file_digest(row, cache)}
            for ref in sorted(user_refs):
                candidates = [row for row in users if row.id == ref]
                if len(candidates) != 1:
                    raise BotSettingsTransferError("unresolved_source_employee")
                manifest_users[ref] = _user_manifest(candidates[0], users)
        return {"format_version": 1, "scope": "bot_settings", "tables": tables,
                "counts": {name: len(rows) for name, rows in tables.items()},
                "references": {"files": manifest_files, "users": manifest_users}}
    except BotSettingsTransferError:
        raise
    except Exception:
        raise BotSettingsTransferError("export_failed_no_data_disclosed") from None


def _validate(data):
    try:
        json.dumps(data, allow_nan=False)
        if (not isinstance(data, dict) or set(data) != {"format_version", "scope", "tables", "counts", "references"}
                or type(data["format_version"]) is not int or data["format_version"] != 1 or data["scope"] != "bot_settings"
                or set(data["tables"]) != set(TABLES) or set(data["counts"]) != set(TABLES)
                or set(data["references"]) != {"files", "users"}):
            raise BotSettingsTransferError("invalid_export_format")
        tables = {}
        for name, (model, key) in TABLES.items():
            raw = data["tables"][name]
            if not isinstance(raw, list) or type(data["counts"][name]) is not int or data["counts"][name] != len(raw):
                raise BotSettingsTransferError("invalid_export_count")
            tables[name] = [_decode(model, row) for row in raw]
            _index(tables[name], key)
            _index(tables[name], "id")
        _structure(tables)
        files, users = _refs(tables[BUTTONS])
        refs = data["references"]
        if set(refs["files"]) != files or set(refs["users"]) != users:
            raise BotSettingsTransferError("incomplete_reference_manifest")
        for desc in refs["files"].values():
            if (set(desc) != {"id", "storage_key", "file_size", "mime_type", "sha256"}
                    or type(desc["file_size"]) is not int or desc["file_size"] < 0
                    or not isinstance(desc["sha256"], str) or len(desc["sha256"]) != 64
                    or any(char not in "0123456789abcdef" for char in desc["sha256"])):
                raise BotSettingsTransferError("invalid_file_manifest")
            uuid.UUID(desc["id"])
            if not isinstance(desc["storage_key"], str) or not desc["storage_key"] or desc["mime_type"] is not None and not isinstance(desc["mime_type"], str):
                raise BotSettingsTransferError("invalid_file_manifest")
        for desc in refs["users"].values():
            if set(desc) != {"login", "email"} or not any(desc.values()) or any(value is not None and (not isinstance(value, str) or not _identity(value)) for value in desc.values()):
                raise BotSettingsTransferError("invalid_employee_manifest")
        return tables
    except BotSettingsTransferError:
        raise
    except Exception:
        raise BotSettingsTransferError("invalid_export") from None


def _resolve_files(manifest, files):
    result, cache = {}, {}
    for ref, desc in manifest.items():
        strong = [row for row in files if row.id == desc["id"] or row.storage_key == desc["storage_key"]]
        candidates = strong or [row for row in files if row.file_size == desc["file_size"]]
        matches = []
        for row in candidates:
            try:
                if row.file_size == desc["file_size"] and _file_digest(row, cache) == desc["sha256"]:
                    matches.append(row)
            except BotSettingsTransferError:
                if strong:
                    raise
        if len(matches) != 1 or len(strong) > 1:
            raise BotSettingsTransferError("unresolved_or_ambiguous_saved_file")
        try:
            uuid.UUID(matches[0].id)
        except (ValueError, TypeError):
            raise BotSettingsTransferError("invalid_saved_file_id") from None
        result[ref] = f"saved:{matches[0].id}"
    return result


def _resolve_users(manifest, users):
    result = {}
    for ref, desc in manifest.items():
        matches = set()
        for field, value in desc.items():
            if value is None:
                continue
            candidates = [row.id for row in users if _identity(getattr(row, field)) == _identity(value)]
            if len(candidates) > 1:
                raise BotSettingsTransferError("ambiguous_employee")
            matches.update(candidates)
        if len(matches) != 1:
            raise BotSettingsTransferError("unresolved_or_conflicting_employee")
        result[ref] = next(iter(matches))
    return result


def _remap_button(row, files, users, staff):
    row = copy.deepcopy(row)
    def replace(values):
        return None if values is None else [files[ref] for ref in values]
    row["files_json"] = replace(row["files_json"])
    for rule in row["conditional_files_json"] or []:
        if "files" in rule:
            rule["files"] = replace(rule["files"])
    for field in USER_FIELDS:
        if row[field] is not None:
            row[field] = users[row[field]]
    fixed = staff.get(row["fixed_assignee_user_id"])
    if fixed and (fixed.role not in {"admin", "agent", "trainee"} or row["fixed_assignee_enabled"] and not fixed.is_active):
        raise BotSettingsTransferError("invalid_fixed_assignee")
    return row


def _plan(db, source, references, report, *, lock):
    def rows(model):
        statement = select(model)
        return list(db.scalars(statement.with_for_update() if lock else statement))
    current = {name: rows(model) for name, (model, _) in TABLES.items()}
    files = _resolve_files(references["files"], rows(SavedFile))
    staff_rows = rows(User)
    users = _resolve_users(references["users"], staff_rows)
    staff = {row.id: row for row in staff_rows}
    buttons = current[BUTTONS]
    by_key = {row.button_key: row for row in buttons}
    by_stable = {row.stable_button_id: row for row in buttons if row.stable_button_id is not None}
    aliases, matches, used = {}, {}, set()
    for index, row in enumerate(source[BUTTONS], 1):
        keyed, stable = by_key.get(row["button_key"]), by_stable.get(row["stable_button_id"])
        if keyed is not None and stable is not None and keyed.id != stable.id:
            raise BotSettingsTransferError("conflicting_button_identity")
        target = keyed if keyed is not None else stable
        if target is not None and target.id in used:
            raise BotSettingsTransferError("duplicate_target_button")
        if target is not None:
            used.add(target.id)
        matches[row["button_key"]] = target
        aliases[row["button_key"]] = target.button_key if target is not None else row["button_key"]
    final, operations = {}, []
    for name, (model, key) in TABLES.items():
        by_logical = {getattr(row, key): row for row in current[name]}
        matched = set()
        final_rows = {row.id: _decode(model, _record(row)) for row in current[name]}
        for index, original in enumerate(source[name], 1):
            values = copy.deepcopy(original)
            existing = matches[values[key]] if name == BUTTONS else by_logical.get(values[key])
            if name == BUTTONS:
                values = _remap_button(values, files, users, staff)
                values["button_key"] = aliases[original["button_key"]]
                if existing is not None and existing.stable_button_id is not None:
                    values["stable_button_id"] = existing.stable_button_id
            elif name == LAYOUTS:
                for item in values["layout_json"] or []:
                    item["button_keys"] = [aliases[ref] for ref in item["button_keys"]]
            values["id"] = existing.id if existing is not None else str(uuid.uuid4())
            # Preserve DEV timestamps: runtime uses them to order sibling buttons.
            changed = {field: value for field, value in values.items() if field != "id" and (existing is None or not _same(getattr(existing, field), value))}
            action = "created" if existing is None else "updated" if changed else "unchanged"
            report[action][name] += 1
            report["records"].append({"table": name, "index": index, "action": action, "fields": sorted(changed)})
            if existing is not None:
                matched.add(existing.id)
            final_rows[values["id"]] = values
            operations.append((model, existing, values, changed))
        report["prod_only"][name] = sorted(getattr(row, key) for row in current[name] if row.id not in matched)
        final[name] = list(final_rows.values())
        _index(final[name], key)
    _structure(final)
    return operations


def import_bot_settings(db: Session, data: dict, *, apply=False):
    source = _validate(data)
    if db.in_transaction() or db.new or db.dirty or db.deleted:
        raise BotSettingsTransferError("fresh_session_required")
    report = {"dry_run": not apply, "applied": False, "conflicts": [], "records": [], "prod_only": {},
              **{action: dict.fromkeys(TABLES, 0) for action in ("created", "updated", "unchanged")}}
    try:
        with db.begin() as transaction, db.no_autoflush:
            try:
                operations = _plan(db, source, data["references"], report, lock=apply)
            except BotSettingsTransferError as error:
                report["conflicts"].append({"code": str(error)})
            if report["conflicts"] or not apply:
                transaction.rollback()
            else:
                for model, existing, values, changed in operations:
                    if existing is None:
                        db.add(model(**values))
                    else:
                        for field, value in changed.items():
                            setattr(existing, field, value)
                db.flush()
        report["applied"] = bool(apply and not report["conflicts"])
        return report
    except Exception:
        raise BotSettingsTransferError("import_failed_transaction_rolled_back") from None


def _unique_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise BotSettingsTransferError("duplicate_json_key")
        result[key] = value
    return result


def _write_export(path, data):
    content = json.dumps(data, ensure_ascii=False, indent=2, allow_nan=False) + "\n"
    fd, name = tempfile.mkstemp(prefix=".bot-settings-", dir=path.parent)
    temporary = Path(name)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as stream:
            stream.write(content)
            stream.flush()
            os.fsync(stream.fileno())
        os.link(temporary, path)  # Atomic publication, never overwrite a prior export.
    finally:
        temporary.unlink(missing_ok=True)


def cli(argv=None):
    parser = argparse.ArgumentParser(description="One-off transfer of bot settings only.")
    commands = parser.add_subparsers(dest="command", required=True)
    export = commands.add_parser("export")
    export.add_argument("--output", required=True, type=Path)
    load = commands.add_parser("import")
    load.add_argument("--input", required=True, type=Path)
    load.add_argument("--apply", action="store_true")
    args = parser.parse_args(argv)
    try:
        with SessionLocal() as db:
            if args.command == "export":
                data = export_bot_settings(db)
                _write_export(args.output, data)
                report = {"exported": data["counts"]}
            else:
                with args.input.open(encoding="utf-8") as stream:
                    data = json.load(stream, object_pairs_hook=_unique_object)
                report = import_bot_settings(db, data, apply=args.apply)
        print(json.dumps(report, ensure_ascii=True))
        return 1 if report.get("conflicts") else 0
    except BotSettingsTransferError as error:
        print(json.dumps({"applied": False, "error": str(error)}))
    except Exception:
        print(json.dumps({"applied": False, "error": "transfer_failed_no_data_disclosed"}))
    return 1


if __name__ == "__main__":
    raise SystemExit(cli())
