"""One-off DEV client export. Run from api/: python -m app.services.client_export.

Contains plaintext passwords: transfer manually via SSH/SFTP and delete the file
on both hosts after successful migration. Future import must use encrypt_secret()
with the PROD key and preserve PROD Telegram menu state. No import is implemented.
"""
from __future__ import annotations

import argparse
import csv
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import subprocess
import tempfile

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..db import SessionLocal
from ..models import Client, ClientProfile, Company, TelegramContact
from .encryption import decrypt_secret


class ClientExportError(RuntimeError):
    pass


def _record(row):
    return {column.key: getattr(row, column.key) for column in row.__table__.columns}


def _payload(db: Session, expected_count: int):
    # Avoid flushing caller state: exporting must never write to DEV.
    with db.no_autoflush:
        contacts = list(db.scalars(select(TelegramContact).order_by(TelegramContact.id)))
        if len(contacts) != expected_count or expected_count <= 0:
            raise ClientExportError("Unexpected Telegram client count.")
        client_ids = {contact.client_id for contact in contacts}
        chat_ids = [str(contact.external_chat_id or "").strip() for contact in contacts]
        if not all(chat_ids) or len(set(chat_ids)) != len(chat_ids):
            raise ClientExportError("Invalid or duplicate Telegram links.")
        clients = {row.id: row for row in db.scalars(select(Client).where(Client.id.in_(client_ids)))}
        profiles = list(db.scalars(select(ClientProfile).where(ClientProfile.client_id.in_(client_ids))))
        profiles_by_client = {row.client_id: row for row in profiles}
        company_ids = set(db.scalars(select(Company.id).where(Company.id.in_({row.company_id for row in clients.values()}))))
        if len(clients) != len(client_ids) or len(profiles) != len(client_ids) or len(profiles_by_client) != len(client_ids):
            raise ClientExportError("Missing or duplicate client/profile links.")
        records = []
        for contact in contacts:
            client = clients[contact.client_id]
            if client.company_id not in company_ids or client.company_id != contact.company_id:
                raise ClientExportError("Invalid client/company links.")
            profile = _record(profiles_by_client[client.id])
            if profile["bitrix_password"]:
                try:
                    profile["bitrix_password"] = decrypt_secret(profile["bitrix_password"])
                except Exception:
                    raise ClientExportError("Password decryption failed; export cancelled.") from None
            records.append({"client": _record(client), "profile": profile, "telegram_contact": _record(contact)})
    return {
        "format_version": 1,
        "metadata": {
            "scope": "telegram_clients", "source": "DEV", "client_count": len(records),
            "created_at": datetime.now(timezone.utc).isoformat(),
            "match_key": "telegram_contact.external_chat_id",
            "bitrix_password_format": "plaintext",
            "telegram_service_state": "source_only_do_not_restore_over_prod",
        },
        "clients": records,
    }


def _json_default(value):
    if isinstance(value, datetime):
        return value.isoformat()
    raise ClientExportError("Unsupported export value.")


def _output_path(output: Path):
    if not output.is_absolute():
        raise ClientExportError("An absolute output path is required.")
    parent = output.parent.resolve(strict=True)
    repository = Path(__file__).resolve().parents[2]
    if parent == repository or repository in parent.parents or any((p / ".git").exists() for p in (parent, *parent.parents)):
        raise ClientExportError("Output must be outside Git repositories.")
    target = parent / output.name
    if os.path.lexists(target):
        raise ClientExportError("Output already exists; refusing to overwrite.")
    return target


def _restrict_permissions(path: Path):
    if os.name == "nt":
        # The temporary file is still empty when removing inherited Windows ACLs.
        identity = subprocess.run(["whoami", "/user", "/fo", "csv", "/nh"],
                                  capture_output=True, text=True, check=True)
        sid = next(csv.reader(identity.stdout.splitlines()))[1]
        subprocess.run(["icacls", str(path), "/inheritance:r", "/grant:r", f"*{sid}:F"],
                       capture_output=True, check=True)
    else:
        os.chmod(path, 0o600)


def export_clients(db: Session, output: Path, *, expected_count: int = 142) -> int:
    """Write a complete private JSON without replacing an existing destination.

    No real export should be inspected or attached to Codex. Interrupted processes
    may leave .esdesk-clients-* files: delete these as well after migration.
    """
    temporary = None
    try:
        target = _output_path(Path(output))
        data = _payload(db, expected_count)
        content = json.dumps(data, ensure_ascii=False, indent=2, default=_json_default, allow_nan=False) + "\n"
        fd, name = tempfile.mkstemp(prefix=".esdesk-clients-", dir=target.parent)
        temporary = Path(name)
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as stream:
            _restrict_permissions(temporary)
            stream.write(content)
            stream.flush()
            os.fsync(stream.fileno())
        # Hard-link publication is atomic and cannot overwrite an existing file.
        # Both paths are on the same filesystem and share the private permissions.
        os.link(temporary, target)
        return len(data["clients"])
    except ClientExportError:
        raise
    except Exception:
        # Never propagate SQL parameters, paths, personal data or decrypted values.
        raise ClientExportError("Client export failed; no diagnostic data disclosed.") from None
    finally:
        if temporary is not None:
            try:
                temporary.unlink(missing_ok=True)
            except OSError:
                raise ClientExportError("Temporary export cleanup failed; remove migration files manually.") from None


def cli(argv=None):
    parser = argparse.ArgumentParser(description="Export 142 DEV Telegram clients to a private local JSON.")
    parser.add_argument("--output", required=True, type=Path, help="Absolute path outside Git; must not exist")
    args = parser.parse_args(argv)
    try:
        with SessionLocal() as db:
            count = export_clients(db, args.output)
    except Exception:
        print("Client export failed; no export contents disclosed.")
        return 1
    print(f"Export complete: {count} clients. Transfer securely and delete after successful migration.")
    return 0


if __name__ == "__main__":
    raise SystemExit(cli())
