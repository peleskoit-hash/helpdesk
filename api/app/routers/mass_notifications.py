from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException, Query, Request, UploadFile
from sqlalchemy import distinct, select
from sqlalchemy.orm import Session

from ..auth import CurrentUser, get_current_user, is_admin
from ..db import get_db
from ..models import Client, ClientProfile, TelegramContact, ClientCardFieldConfig
from ..services.client_card import _field_options_out
from ..schemas import (
    MassNotificationPayload,
    MassNotificationRecipientOut,
    MassNotificationSendResult,
)
from ..services.telegram_bot import TelegramApiError, send_document, send_message, send_photo


router = APIRouter(prefix="/api/v1/mass-notifications", tags=["mass-notifications"])

IMAGE_EXTENSIONS = {".jpg", ".jpeg", ".png", ".webp", ".gif", ".bmp"}
ALLOWED_FILTER_FIELDS = {
    "destination": ClientProfile.destination,
    "sand_owner": ClientProfile.sand_owner,
    "sales_group": ClientProfile.sales_group,
    "department": ClientProfile.department,
    "first_department": ClientProfile.first_department,
    "group_name": ClientProfile.group_name,
    "location_type": ClientProfile.location_type,
    "employee_type": ClientProfile.employee_type,
}


def _error(status_code: int, code: str, message: str):
    raise HTTPException(
        status_code=status_code,
        detail={"error": {"code": code, "message": message, "details": {}}},
    )


def _admin_only(current_user: CurrentUser) -> None:
    if not is_admin(current_user):
        _error(403, "forbidden", "Только администратор может делать массовую рассылку")


def _recipients_query(
    filter_field: str | None,
    filter_value: str | None,
    branch_field: str | None = None,
    branch_value: str | None = None,
    location_type: str | None = None,
):
    stmt = (
        select(
            TelegramContact.client_id,
            TelegramContact.external_chat_id,
            TelegramContact.external_username,
            TelegramContact.external_display_name,
            Client.display_name,
            ClientProfile.destination,
        )
        .join(Client, Client.id == TelegramContact.client_id)
        .outerjoin(ClientProfile, ClientProfile.client_id == TelegramContact.client_id)
    )
    if filter_field and filter_value:
        field = ALLOWED_FILTER_FIELDS.get(filter_field)
        if not field:
            _error(400, "invalid_filter_field", "Неизвестное поле фильтра")
        stmt = stmt.where(field == filter_value)
    if branch_field and branch_value:
        branch = ALLOWED_FILTER_FIELDS.get(branch_field)
        if not branch:
            _error(400, "invalid_branch_field", "Неизвестное поле ветвления")
        stmt = stmt.where(branch == branch_value)
    if location_type:
        stmt = stmt.where(ClientProfile.location_type == location_type)
    return stmt.order_by(Client.display_name.asc())


def _is_image_file(filename: str | None, mime_type: str | None) -> bool:
    if mime_type and mime_type.lower().startswith("image/"):
        return True
    if filename:
        ext = Path(filename).suffix.lower()
        return ext in IMAGE_EXTENSIONS
    return False


@router.get("/recipients", response_model=list[MassNotificationRecipientOut])
def list_recipients(
    filter_field: str | None = Query(default=None),
    filter_value: str | None = Query(default=None),
    branch_field: str | None = Query(default=None),
    branch_value: str | None = Query(default=None),
    location_type: str | None = Query(default=None),
    db: Session = Depends(get_db),
    current_user: CurrentUser = Depends(get_current_user),
):
    _admin_only(current_user)
    rows = db.execute(
        _recipients_query(filter_field, filter_value, branch_field, branch_value, location_type)
    ).all()
    return [
        MassNotificationRecipientOut(
            client_id=row.client_id,
            client_name=row.display_name or "Клиент",
            chat_id=row.external_chat_id,
            destination=row.destination,
            telegram_username=row.external_username,
            telegram_display_name=row.external_display_name,
        )
        for row in rows
    ]


@router.get("/filter-options")
def list_filter_options(
    filter_field: str = Query(...),
    destination: str | None = Query(default=None),
    db: Session = Depends(get_db),
    current_user: CurrentUser = Depends(get_current_user),
):
    _admin_only(current_user)
    field = ALLOWED_FILTER_FIELDS.get(filter_field)
    if not field:
        _error(400, "invalid_filter_field", "Неизвестное поле фильтра")
    config = db.execute(select(ClientCardFieldConfig).where(ClientCardFieldConfig.key == filter_field)).scalar_one_or_none()
    if config is None or config.field_type == "select":
        return {"field": filter_field, "values": (_field_options_out(config) or []) if config is not None else []}
    stmt = (
        select(distinct(field))
        .select_from(TelegramContact)
        .join(ClientProfile, ClientProfile.client_id == TelegramContact.client_id)
        .where(field.isnot(None))
        .order_by(field.asc())
    )
    if destination:
        stmt = stmt.where(ClientProfile.destination == destination)
    values = [row[0] for row in db.execute(stmt).all() if str(row[0]).strip()]
    return {"field": filter_field, "values": values}


@router.post("/send", response_model=MassNotificationSendResult)
async def send_mass_notification(
    request: Request,
    db: Session = Depends(get_db),
    current_user: CurrentUser = Depends(get_current_user),
):
    _admin_only(current_user)

    text: str = ""
    filter_field: str | None = None
    filter_value: str | None = None
    branch_field: str | None = None
    branch_value: str | None = None
    location_type: str | None = None
    files: list[UploadFile] = []

    content_type = (request.headers.get("content-type") or "").lower()
    if "multipart/form-data" in content_type:
        form = await request.form()
        text = str(form.get("text") or "").strip()
        filter_field_raw = form.get("filter_field")
        filter_value_raw = form.get("filter_value")
        branch_field_raw = form.get("branch_field")
        branch_value_raw = form.get("branch_value")
        location_type_raw = form.get("location_type")
        filter_field = str(filter_field_raw).strip() if filter_field_raw else None
        filter_value = str(filter_value_raw).strip() if filter_value_raw else None
        branch_field = str(branch_field_raw).strip() if branch_field_raw else None
        branch_value = str(branch_value_raw).strip() if branch_value_raw else None
        location_type = str(location_type_raw).strip() if location_type_raw else None
        files = [item for item in form.getlist("files") if isinstance(item, UploadFile)]
    else:
        payload_json = MassNotificationPayload(**(await request.json()))
        text = (payload_json.text or "").strip()
        if payload_json.destination:
            filter_field = "destination"
            filter_value = payload_json.destination

    if not text and not files:
        _error(400, "empty_payload", "Укажите текст и/или вложения для рассылки")

    file_blobs: list[dict] = []
    for file in files:
        name = (file.filename or "attachment").strip() or "attachment"
        content = await file.read()
        if not content:
            continue
        file_blobs.append(
            {
                "filename": name,
                "mime_type": file.content_type or "application/octet-stream",
                "bytes": content,
                "is_image": _is_image_file(name, file.content_type),
            }
        )

    rows = db.execute(
        _recipients_query(filter_field, filter_value, branch_field, branch_value, location_type)
    ).all()
    failed_recipients: list[str] = []
    sent = 0

    for row in rows:
        chat_id = str(row.external_chat_id)
        label = row.display_name or chat_id
        try:
            if text:
                send_message(chat_id, text)
            for blob in file_blobs:
                if blob["is_image"]:
                    send_photo(
                        chat_id=chat_id,
                        content=blob["bytes"],
                        filename=blob["filename"],
                        mime_type=blob["mime_type"],
                    )
                else:
                    send_document(
                        chat_id=chat_id,
                        file_path=blob["bytes"],
                        filename=blob["filename"],
                        mime_type=blob["mime_type"],
                    )
            sent += 1
        except TelegramApiError as exc:
            failed_recipients.append(f"{label}: {exc}")

    return MassNotificationSendResult(
        ok=True,
        total=len(rows),
        sent=sent,
        failed=len(failed_recipients),
        failed_recipients=failed_recipients[:100],
    )
