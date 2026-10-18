"""One-off JSON v1 client import; run from api/: python -m app.services.client_import import --input PATH.

Use a fresh Session. Dry-run only plans changes in memory. Reports contain input
row numbers, schema field names and fixed codes, never source values or IDs.
After successful migration, remove the plaintext export from both hosts manually.
"""
from __future__ import annotations

import argparse
import copy
from collections import Counter
from datetime import datetime, timezone
import json
from pathlib import Path
import uuid

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..db import SessionLocal
from ..models import Client, ClientProfile, Company, TelegramContact
from .encryption import decrypt_secret, encrypt_secret
from .web_credentials import WEB_COLUMNS, WEB_KEYS, without_web_fields

MODELS = {"client": Client, "profile": ClientProfile, "telegram_contact": TelegramContact}
PROTECTED = {"id", "client_id", "company_id", "created_at", "updated_at"}
TELEGRAM_STATE = {"is_blocked", "active_menu_message_id", "menu_message_ids_json"}


class ClientImportError(RuntimeError):
    pass


def _empty(value):
    return value is None or isinstance(value, str) and not value.strip()


def _same(left, right):
    # JSON equality must distinguish false from 0, including nested custom values.
    return json.dumps(left, sort_keys=True, allow_nan=False) == json.dumps(right, sort_keys=True, allow_nan=False)


def _decode_row(model, raw):
    columns = model.__table__.columns
    if model is ClientProfile and isinstance(raw, dict):
        custom = raw.get("custom_fields")
        if isinstance(custom, dict) and any(not _empty(custom.get(k)) for k in WEB_KEYS):
            raise ClientImportError("legacy_web_credentials_require_secure_migration")
        if any(not _empty(raw.get(k)) for k in WEB_COLUMNS - {"web_credentials_version"}):
            raise ClientImportError("web_credentials_import_not_supported")
        raw = {**raw, **{k: (0 if k == "web_credentials_version" else None) for k in WEB_COLUMNS}}
        if isinstance(custom, dict):
            raw["custom_fields"] = without_web_fields(custom)
    if not isinstance(raw, dict) or set(raw) != set(columns.keys()):
        raise ClientImportError("invalid_field_schema")
    row = copy.deepcopy(raw)
    for column in columns:
        value = row[column.key]
        if value is None:
            if not column.nullable:
                raise ClientImportError("invalid_field_value")
            continue
        kind = column.type.python_type
        if kind is datetime:
            if not isinstance(value, str):
                raise ClientImportError("invalid_date")
            row[column.key] = datetime.fromisoformat(value)
        else:
            if column.key == "menu_message_ids_json":
                kind = list
            if type(value) is not kind:
                raise ClientImportError("invalid_field_type")
            if kind is str and getattr(column.type, "length", None) and len(value) > column.type.length:
                raise ClientImportError("field_too_long")
        if column.key in {"id", "client_id", "company_id", "external_chat_id"} and _empty(value):
            raise ClientImportError("missing_identity")
    return row


def _validate(data):
    try:
        # Reject non-JSON objects and non-finite numbers even for direct callers.
        json.dumps(data, allow_nan=False)
        if not isinstance(data, dict) or set(data) != {"format_version", "metadata", "clients"}:
            raise ClientImportError("invalid_format")
        meta = data["metadata"]
        if type(data["format_version"]) is not int or data["format_version"] != 1 or not isinstance(meta, dict):
            raise ClientImportError("invalid_format_version")
        if (meta.get("scope") != "telegram_clients" or meta.get("source") != "DEV"
                or meta.get("match_key") != "telegram_contact.external_chat_id"
                or meta.get("bitrix_password_format") != "plaintext"):
            raise ClientImportError("invalid_metadata")
        if type(meta.get("client_count")) is not int or meta["client_count"] != 142 or not isinstance(data["clients"], list) or len(data["clients"]) != 142:
            raise ClientImportError("invalid_count")
        records, chats, contacts, sources, profile_owners = [], set(), set(), {}, {}
        for raw in data["clients"]:
            if not isinstance(raw, dict) or set(raw) != set(MODELS):
                raise ClientImportError("invalid_record")
            row = {name: _decode_row(model, raw[name]) for name, model in MODELS.items()}
            client, profile, contact = row["client"], row["profile"], row["telegram_contact"]
            if profile["client_id"] != client["id"] or contact["client_id"] != client["id"] or contact["company_id"] != client["company_id"]:
                raise ClientImportError("invalid_source_links")
            chat = contact["external_chat_id"]
            if chat != chat.strip() or chat in chats or contact["id"] in contacts:
                raise ClientImportError("duplicate_or_invalid_source_contact")
            # Shared source clients are allowed, but their repeated snapshots must agree.
            snapshot = (raw["client"], raw["profile"])
            if client["id"] in sources and not _same(sources[client["id"]], snapshot):
                raise ClientImportError("inconsistent_source_profile")
            if profile["id"] in profile_owners and profile_owners[profile["id"]] != client["id"]:
                raise ClientImportError("invalid_source_profile")
            sources[client["id"]] = snapshot
            profile_owners[profile["id"]] = client["id"]
            chats.add(chat)
            contacts.add(contact["id"])
            records.append(row)
        return records
    except ClientImportError:
        raise
    except Exception:
        raise ClientImportError("invalid_export") from None


def _merge(existing, source, protected):
    values, skipped = {}, set()
    for field, value in source.items():
        if field in protected or field in WEB_COLUMNS or _empty(value):
            skipped.add(field)
            continue
        old = getattr(existing, field)
        if field == "custom_fields":
            merged = copy.deepcopy(old or {})
            for key, item in value.items():
                if _empty(item):
                    skipped.add(field)
                else:
                    merged[key] = copy.deepcopy(item)
            if not _same(merged, old):
                values[field] = merged
        elif field == "bitrix_password":
            if _empty(old) or decrypt_secret(old) != value:
                values[field] = encrypt_secret(value)
        elif old != value:
            values[field] = copy.deepcopy(value)
    if values and hasattr(existing, "updated_at"):
        values["updated_at"] = datetime.now(timezone.utc)
    return values, sorted(skipped)


def _plan(db, records, company_id, report, *, lock):
    def rows(model):
        query = select(model)
        return list(db.scalars(query.with_for_update() if lock else query))
    companies = {row.id for row in rows(Company)}
    clients = {row.id: row for row in rows(Client)}
    profile_rows = rows(ClientProfile)
    profiles = {row.client_id: row for row in profile_rows}
    contacts = rows(TelegramContact)
    by_chat = {row.external_chat_id: row for row in contacts}
    owners = {(row.company_id, row.external_user_id): row.id for row in contacts}
    client_uses = Counter(row.client_id for row in contacts)
    if len(profiles) != len(profile_rows) or len(by_chat) != len(contacts) or len(owners) != len(contacts):
        raise ClientImportError("invalid_prod_uniqueness")
    if any(_empty(row.external_chat_id) or row.external_chat_id != row.external_chat_id.strip() for row in contacts):
        raise ClientImportError("invalid_prod_chat_identity")
    target_company = company_id if company_id in companies else next(iter(companies)) if company_id is None and len(companies) == 1 else None
    operations = []
    for index, record in enumerate(records, 1):
        detail = {"index": index, "created": [], "updated": {}, "skipped": {}}
        report["records"].append(detail)
        contact = by_chat.get(record["telegram_contact"]["external_chat_id"])
        client = clients.get(contact.client_id) if contact else None
        if contact and (client is None or client.company_id != contact.company_id or client.company_id not in companies):
            report["conflicts"].append({"index": index, "code": "invalid_prod_links"})
            continue
        if contact and client_uses[client.id] > 1:
            report["conflicts"].append({"index": index, "code": "shared_prod_client"})
            continue
        if not contact and target_company is None:
            report["conflicts"].append({"index": index, "code": "ambiguous_or_missing_company"})
            continue
        client_id = client.id if client else str(uuid.uuid4())
        destination_company = client.company_id if client else target_company
        profile = profiles.get(client_id)
        record_ops = []
        try:
            for name, model in MODELS.items():
                existing = {"client": client, "profile": profile, "telegram_contact": contact}[name]
                source = record[name]
                if existing is None:
                    values = copy.deepcopy(source)
                    values["id"] = client_id if name == "client" else str(uuid.uuid4())
                    if name != "client":
                        values["client_id"] = client_id
                    if name != "profile":
                        values["company_id"] = destination_company
                    if name == "profile":
                        password = values["bitrix_password"]
                        values["bitrix_password"] = None if _empty(password) else encrypt_secret(password)
                    if name == "telegram_contact":
                        values["active_menu_message_id"] = None
                        values["menu_message_ids_json"] = None
                        detail["skipped"][name] = ["active_menu_message_id", "menu_message_ids_json"]
                    detail["created"].append(name)
                else:
                    values, skipped = _merge(existing, source, PROTECTED | (TELEGRAM_STATE if name == "telegram_contact" else set()))
                    detail["skipped"][name] = skipped
                    if values:
                        detail["updated"][name] = sorted(values)
                record_ops.append((name, model, existing, values))
        except Exception:
            report["conflicts"].append({"index": index, "code": "field_or_password_processing_failed"})
            continue
        telegram_values = record_ops[-1][3]
        user_id = telegram_values.get("external_user_id", contact.external_user_id if contact else None)
        if _empty(user_id):
            report["conflicts"].append({"index": index, "code": "missing_telegram_user"})
            continue
        owner_key = (destination_company, user_id)
        contact_id = contact.id if contact else telegram_values["id"]
        if owner_key in owners and owners[owner_key] != contact_id:
            report["conflicts"].append({"index": index, "code": "telegram_user_conflict"})
            continue
        owners[owner_key] = contact_id
        operations.extend(record_ops)
        for name, model, existing, values in record_ops:
            action = "created" if existing is None else "updated" if values else "unchanged"
            report[action][name] += 1
    return operations


def import_clients(db: Session, data: dict, *, apply: bool = False, company_id: str | None = None) -> dict:
    """Plan/apply all 142 records atomically. Requires a fresh, unused session."""
    records = _validate(data)
    if db.in_transaction() or db.new or db.dirty or db.deleted:
        raise ClientImportError("fresh_session_required")
    report = {"dry_run": not apply, "applied": False, "conflicts": [], "records": [],
              **{action: {name: 0 for name in MODELS} for action in ("created", "updated", "unchanged")}}
    try:
        with db.begin() as transaction, db.no_autoflush:
            operations = _plan(db, records, company_id, report, lock=apply)
            if report["conflicts"] or not apply:
                transaction.rollback()
            else:
                # Explicit order satisfies FKs without relying on ORM relationships.
                for name in MODELS:
                    for kind, model, existing, values in operations:
                        if kind != name:
                            continue
                        if existing is None:
                            db.add(model(**values))
                        else:
                            for field, value in values.items():
                                setattr(existing, field, value)
                    db.flush()
        report["applied"] = apply and not report["conflicts"]
        return report
    except ClientImportError:
        raise
    except Exception:
        raise ClientImportError("import_failed_transaction_rolled_back") from None


def _unique_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ClientImportError("duplicate_json_key")
        result[key] = value
    return result


def cli(argv=None):
    parser = argparse.ArgumentParser(description="One-off client import; reports never include field values.")
    commands = parser.add_subparsers(dest="command", required=True)
    command = commands.add_parser("import")
    command.add_argument("--input", required=True, type=Path)
    command.add_argument("--apply", action="store_true")
    command.add_argument("--company-id", help="Explicit existing PROD company for new clients")
    args = parser.parse_args(argv)
    try:
        with args.input.open(encoding="utf-8") as stream:
            data = json.load(stream, object_pairs_hook=_unique_object)
        with SessionLocal() as db:
            report = import_clients(db, data, apply=args.apply, company_id=args.company_id)
    except Exception:
        print(json.dumps({"applied": False, "error": "import_failed_no_data_disclosed"}))
        return 1
    print(json.dumps(report, ensure_ascii=True))
    return 1 if report["conflicts"] else 0


if __name__ == "__main__":
    raise SystemExit(cli())
