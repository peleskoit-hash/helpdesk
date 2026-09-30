from __future__ import annotations

from datetime import datetime
from pathlib import Path
import uuid

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..config import settings
from ..models import BotButtonSetting, SavedFile
from .button_config import BUTTON_CONFIG_DIR


LEGACY_BOT_BUTTON_PDF_FILENAMES = (
    "Памятка обучающемуся сотруднику.pdf",
    "Памятка удалённому обучающемуся сотруднику.pdf",
    "Руководство для МПП.pdf",
    "Руководство удалённому МПП .pdf",
)
LEGACY_BOT_BUTTON_SAVED_FILE_NAMESPACE = uuid.UUID("8af2eb8d-92e0-4a3a-806b-5d4259f29d73")


def legacy_bot_button_saved_file_id(filename: str) -> str:
    return str(uuid.uuid5(LEGACY_BOT_BUTTON_SAVED_FILE_NAMESPACE, filename))


def migrate_legacy_bot_button_pdf_saved_files(db: Session) -> dict[str, str]:
    migrated_refs: dict[str, str] = {}
    storage_root = Path(settings.saved_file_storage_dir).resolve()
    storage_root.mkdir(parents=True, exist_ok=True)

    for filename in LEGACY_BOT_BUTTON_PDF_FILENAMES:
        source = (BUTTON_CONFIG_DIR / "files" / filename).resolve()
        saved_file_id = legacy_bot_button_saved_file_id(filename)
        saved_file = db.get(SavedFile, saved_file_id)

        if saved_file is None:
            if not source.exists() or not source.is_file():
                continue
            content = source.read_bytes()
            storage_key = f"{saved_file_id}.pdf"
            target = (storage_root / storage_key).resolve()
            try:
                target.relative_to(storage_root)
            except ValueError:
                continue
            if not target.exists():
                target.write_bytes(content)
            saved_file = SavedFile(
                id=saved_file_id,
                display_filename=filename,
                storage_key=storage_key,
                mime_type="application/pdf",
                file_size=len(content),
                created_at=datetime.utcnow(),
            )
            db.add(saved_file)
        else:
            target = (storage_root / saved_file.storage_key).resolve()
            try:
                target.relative_to(storage_root)
            except ValueError:
                continue
            if not target.exists():
                if not source.exists() or not source.is_file():
                    continue
                target.write_bytes(source.read_bytes())

        migrated_refs[filename] = f"saved:{saved_file_id}"

    if migrated_refs:
        _backfill_bot_button_file_refs(db, migrated_refs)
    return migrated_refs


def _replace_file_list_refs(values: object, migrated_refs: dict[str, str]) -> tuple[object, bool]:
    if not isinstance(values, list):
        return values, False
    changed = False
    replaced: list[object] = []
    for value in values:
        if isinstance(value, str) and value in migrated_refs:
            replaced.append(migrated_refs[value])
            changed = True
        else:
            replaced.append(value)
    return replaced, changed


def _replace_conditional_file_refs(rules: object, migrated_refs: dict[str, str]) -> tuple[object, bool]:
    if not isinstance(rules, list):
        return rules, False
    changed = False
    replaced_rules: list[object] = []
    for rule in rules:
        if not isinstance(rule, dict):
            replaced_rules.append(rule)
            continue
        replaced_rule = dict(rule)
        replaced_files, files_changed = _replace_file_list_refs(replaced_rule.get("files"), migrated_refs)
        if files_changed:
            replaced_rule["files"] = replaced_files
            changed = True
        replaced_rules.append(replaced_rule)
    return replaced_rules, changed


def _backfill_bot_button_file_refs(db: Session, migrated_refs: dict[str, str]) -> None:
    rows = db.execute(select(BotButtonSetting)).scalars().all()
    for row in rows:
        files_json, files_changed = _replace_file_list_refs(row.files_json, migrated_refs)
        if files_changed:
            row.files_json = files_json
            row.files_overridden = True
            row.updated_at = datetime.utcnow()

        conditional_files_json, conditional_changed = _replace_conditional_file_refs(
            row.conditional_files_json,
            migrated_refs,
        )
        if conditional_changed:
            row.conditional_files_json = conditional_files_json
            row.conditional_files_overridden = True
            row.updated_at = datetime.utcnow()
