from __future__ import annotations

from datetime import datetime
from mimetypes import guess_extension
from pathlib import Path
from typing import BinaryIO
import uuid

from sqlalchemy.orm import Session

from ..config import settings
from ..models import SavedFile


class SavedFileStorageError(RuntimeError):
    pass


def _storage_root() -> Path:
    root = Path(settings.saved_file_storage_dir).resolve()
    root.mkdir(parents=True, exist_ok=True)
    return root


def _safe_display_filename(value: str | None, fallback: str = "file.bin") -> str:
    name = (value or "").strip() or fallback
    for char in '<>:"/\\|?*':
        name = name.replace(char, "_")
    return name[:180]


def _storage_extension(filename: str | None, mime_type: str | None) -> str:
    ext = Path(filename or "").suffix
    if not ext and mime_type:
        ext = guess_extension(mime_type) or ""
    return ext or ".bin"


def _storage_key(filename: str | None, mime_type: str | None) -> str:
    return f"{uuid.uuid4().hex}{_storage_extension(filename, mime_type)}"


def resolve_saved_file_path(saved_file: SavedFile) -> Path:
    root = _storage_root()
    path = (root / saved_file.storage_key).resolve()
    try:
        path.relative_to(root)
    except ValueError as exc:
        raise SavedFileStorageError("saved_file_path_outside_storage") from exc
    if not path.exists():
        raise SavedFileStorageError("saved_file_missing")
    return path


def create_saved_file(
    db: Session,
    *,
    content: bytes | BinaryIO,
    display_filename: str,
    mime_type: str | None = None,
) -> SavedFile:
    root = _storage_root()
    storage_key = _storage_key(display_filename, mime_type)
    path = (root / storage_key).resolve()
    try:
        path.relative_to(root)
    except ValueError as exc:
        raise SavedFileStorageError("saved_file_path_outside_storage") from exc

    data = content.read() if hasattr(content, "read") else content
    if isinstance(data, str):
        data = data.encode("utf-8")
    data = data or b""
    path.write_bytes(data)

    saved_file = SavedFile(
        id=str(uuid.uuid4()),
        display_filename=_safe_display_filename(display_filename, fallback=f"file{path.suffix or '.bin'}"),
        storage_key=storage_key,
        mime_type=mime_type,
        file_size=len(data),
        created_at=datetime.utcnow(),
    )
    db.add(saved_file)
    return saved_file


def rename_saved_file(db: Session, saved_file: SavedFile, display_filename: str) -> SavedFile:
    saved_file.display_filename = _safe_display_filename(display_filename)
    db.add(saved_file)
    return saved_file


def delete_saved_file(db: Session, saved_file: SavedFile) -> None:
    path = resolve_saved_file_path(saved_file)
    path.unlink()
    db.delete(saved_file)
