from __future__ import annotations

import re
from urllib.parse import quote

from fastapi import APIRouter, Depends, File, HTTPException, UploadFile
from fastapi.responses import FileResponse
from pydantic import BaseModel
from sqlalchemy import asc, select
from sqlalchemy.orm import Session

from ..auth import CurrentUser, get_current_user, is_admin
from ..db import get_db
from ..models import BotButtonSetting, QuickReply, SavedFile
from ..services.saved_files import (
    SavedFileStorageError,
    create_saved_file,
    delete_saved_file,
    rename_saved_file,
    resolve_saved_file_path,
)


router = APIRouter(prefix="/api/v1/management/saved-files", tags=["management-saved-files"])


class SavedFileRenamePayload(BaseModel):
    display_filename: str


def _error(status_code: int, code: str, message: str, details: dict | None = None):
    raise HTTPException(
        status_code=status_code,
        detail={"error": {"code": code, "message": message, "details": details or {}}},
    )


def _require_admin(user: CurrentUser) -> None:
    if not is_admin(user):
        _error(403, "admin_only", "Доступ только для администратора")


def _content_disposition_attachment(filename: str) -> str:
    normalized = (filename or "").strip() or "file"
    ascii_filename = re.sub(r"[^A-Za-z0-9._ -]+", "_", normalized).strip() or "file"
    ascii_filename = ascii_filename.replace("\\", "_").replace('"', "_")
    encoded_filename = quote(normalized, safe="")
    return f'attachment; filename="{ascii_filename}"; filename*=UTF-8\'\'{encoded_filename}'


def _bot_button_file_refs(row: BotButtonSetting) -> set[str]:
    refs: set[str] = set()
    if isinstance(row.files_json, list):
        refs.update(value for value in row.files_json if isinstance(value, str))
    if isinstance(row.conditional_files_json, list):
        for rule in row.conditional_files_json:
            if not isinstance(rule, dict):
                continue
            files = rule.get("files")
            if isinstance(files, list):
                refs.update(value for value in files if isinstance(value, str))
    return refs


def _usage_sources_for_saved_file(db: Session, saved_file_id: str) -> list[str]:
    usage_sources: list[str] = []
    ref = f"saved:{saved_file_id}"
    rows = db.execute(select(BotButtonSetting)).scalars().all()
    for row in rows:
        if ref in _bot_button_file_refs(row):
            usage_sources.append("bot_buttons")
            break
    quick_replies = db.execute(select(QuickReply)).scalars().all()
    for reply in quick_replies:
        if isinstance(reply.saved_file_ids_json, list) and saved_file_id in {str(value) for value in reply.saved_file_ids_json}:
            usage_sources.append("quick_replies")
            break
    return usage_sources


def _saved_file_out(db: Session, saved_file: SavedFile) -> dict:
    usage_sources = _usage_sources_for_saved_file(db, saved_file.id)
    return {
        "id": saved_file.id,
        "display_filename": saved_file.display_filename,
        "file_size": saved_file.file_size,
        "mime_type": saved_file.mime_type,
        "created_at": saved_file.created_at,
        "used": bool(usage_sources),
        "usage_sources": usage_sources,
    }


def _get_saved_file_or_404(db: Session, saved_file_id: str) -> SavedFile:
    saved_file = db.get(SavedFile, saved_file_id)
    if saved_file is None:
        _error(404, "saved_file_not_found", "Сохранённый файл не найден")
    return saved_file


@router.get("")
def list_saved_files(db: Session = Depends(get_db), user: CurrentUser = Depends(get_current_user)):
    _require_admin(user)
    rows = db.execute(select(SavedFile).order_by(asc(SavedFile.created_at), asc(SavedFile.id))).scalars().all()
    return {"items": [_saved_file_out(db, row) for row in rows]}


@router.post("")
def upload_saved_file(
    file: UploadFile = File(...),
    db: Session = Depends(get_db),
    user: CurrentUser = Depends(get_current_user),
):
    _require_admin(user)
    saved_file = create_saved_file(
        db,
        content=file.file,
        display_filename=file.filename or "file.bin",
        mime_type=file.content_type,
    )
    db.commit()
    db.refresh(saved_file)
    return _saved_file_out(db, saved_file)


@router.get("/{saved_file_id}/download")
def download_saved_file(
    saved_file_id: str,
    db: Session = Depends(get_db),
    user: CurrentUser = Depends(get_current_user),
):
    _require_admin(user)
    saved_file = _get_saved_file_or_404(db, saved_file_id)
    try:
        path = resolve_saved_file_path(saved_file)
    except SavedFileStorageError:
        _error(404, "saved_file_content_not_found", "Физический файл не найден")
    headers = {"Content-Disposition": _content_disposition_attachment(saved_file.display_filename)}
    return FileResponse(
        str(path),
        media_type=saved_file.mime_type or "application/octet-stream",
        headers=headers,
    )


@router.patch("/{saved_file_id}")
def rename_management_saved_file(
    saved_file_id: str,
    payload: SavedFileRenamePayload,
    db: Session = Depends(get_db),
    user: CurrentUser = Depends(get_current_user),
):
    _require_admin(user)
    saved_file = _get_saved_file_or_404(db, saved_file_id)
    rename_saved_file(db, saved_file, payload.display_filename)
    db.commit()
    db.refresh(saved_file)
    return _saved_file_out(db, saved_file)


@router.delete("/{saved_file_id}")
def delete_management_saved_file(
    saved_file_id: str,
    db: Session = Depends(get_db),
    user: CurrentUser = Depends(get_current_user),
):
    _require_admin(user)
    saved_file = _get_saved_file_or_404(db, saved_file_id)
    usage_sources = _usage_sources_for_saved_file(db, saved_file.id)
    if usage_sources:
        _error(
            409,
            "saved_file_in_use",
            "Сохранённый файл используется",
            {"used": True, "usage_sources": usage_sources},
        )
    try:
        delete_saved_file(db, saved_file)
    except SavedFileStorageError:
        _error(409, "saved_file_storage_error", "Физический файл не удалось удалить")
    db.commit()
    return {"deleted": True}
