from datetime import datetime, time
from pathlib import Path
from zoneinfo import ZoneInfo
import uuid

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import FileResponse
from sqlalchemy import Select, and_, asc, desc, func, or_, select
from sqlalchemy.orm import Session

from ..auth import CurrentUser, get_current_user
from ..config import settings
from ..db import get_db
from ..models import ClientProfile, MessageType, TelegramContact, Ticket, TicketAttachment, TicketIntakeState, TicketMessage, TicketStatus, TicketTaskKind, TicketView, TicketWorkflowType, User
from ..schemas import AssignTicketsPayload, ClientProfileOut, ClientProfileUpdate, FreezePayload, TextPayload, TicketCard, TicketListItem, TicketListResponse, WrongProblemPayload
from ..services.assignment import append_system_message, assignee_change_message, assignee_label, notify_client_about_assignee
from ..services.telegram_bot import TelegramApiError, delete_message, edit_message_text, remove_inline_keyboard, send_message, send_problem_menu
from ..services.button_config import BASIC_MENU_NAME, DEFAULT_MENU_NAME, ButtonConfig, MenuSpec, find_button, load_button_config
from .telegram import TICKET_CLOSED_CLIENT_TEXT


router = APIRouter(prefix="/api/v1/tickets", tags=["tickets"])

WRONG_PROBLEM_TEXT_TEMPLATE = (
    'Вы выбрали неверное меню для решения данной проблемы. '
    'В следующий раз, когда у Вас возникнет такая же проблема, выбирайте пожалуйста в боте заявку – "{label}"'
)


def _error(status_code: int, code: str, message: str):
    raise HTTPException(status_code=status_code, detail={"error": {"code": code, "message": message, "details": {}}})


def _apply_role_filter(stmt: Select, user: CurrentUser):
    if user.role == "agent":
        return stmt.where(and_(Ticket.assignee_id == user.id, Ticket.agent_visible.is_(True)))
    return stmt


def _apply_admin_assignee_filter(stmt: Select, user: CurrentUser, assignee_ids: list[str] | None):
    if user.role != "admin":
        return stmt
    if not assignee_ids:
        return stmt.where(False)
    return stmt.where(Ticket.assignee_id.in_(assignee_ids))


def _sort_clause(sort: str, order: str):
    field = Ticket.created_at if sort == "created_at" else Ticket.updated_at
    return asc(field) if order == "asc" else desc(field)


def _last_reply_meta(db: Session, ticket_id: str):
    stmt = (
        select(TicketMessage.message_type, TicketMessage.created_at)
        .where(TicketMessage.ticket_id == ticket_id)
        .order_by(desc(TicketMessage.created_at))
        .limit(1)
    )
    row = db.execute(stmt).first()
    if not row:
        return None, None
    labels = {
        MessageType.CLIENT.value: "Клиент",
        MessageType.AGENT.value: "Сотрудник",
        MessageType.INTERNAL.value: "Комментарий",
        MessageType.SYSTEM.value: "Система",
    }
    by = labels.get(row.message_type, "Система")
    return by, row.created_at


def _seen_by_agent(db: Session, ticket_id: str, user: CurrentUser) -> bool:
    if user.role != "agent":
        return False
    stmt = select(TicketView.id).where(
        and_(TicketView.ticket_id == ticket_id, TicketView.user_id == user.id)
    ).limit(1)
    return db.execute(stmt).first() is not None


def _list_tickets_paginated(
    db: Session,
    user: CurrentUser,
    statuses: list[str],
    is_frozen: bool | None,
    sort: str,
    order: str,
    page: int,
    limit: int,
    admin_assignee_ids: list[str] | None = None,
    filter_admin_assignees: bool = True,
):
    conditions = [Ticket.status.in_(statuses)]
    if is_frozen is not None:
        conditions.append(Ticket.is_frozen.is_(is_frozen))

    base_stmt = select(Ticket).where(and_(*conditions))
    base_stmt = _apply_role_filter(base_stmt, user)
    if filter_admin_assignees:
        base_stmt = _apply_admin_assignee_filter(base_stmt, user, admin_assignee_ids)

    total_stmt = select(func.count()).select_from(base_stmt.subquery())
    total = db.execute(total_stmt).scalar_one()

    pages = max(1, (total + limit - 1) // limit)
    if page > pages:
        page = pages

    offset = (page - 1) * limit
    stmt = base_stmt.order_by(_sort_clause(sort, order)).offset(offset).limit(limit)
    tickets = db.execute(stmt).scalars().all()

    items: list[TicketListItem] = []
    for t in tickets:
        last_reply_by, last_reply_at = _last_reply_meta(db, t.id)
        seen_by_agent = _seen_by_agent(db, t.id, user)
        assignee = db.get(User, t.assignee_id) if t.assignee_id else None
        items.append(
            TicketListItem(
                id=t.id,
                subject=t.subject,
                workflow_type=t.workflow_type or TicketWorkflowType.TICKET.value,
                intake_state=t.intake_state or TicketIntakeState.READY.value,
                status=t.status,
                is_frozen=t.is_frozen,
                seen_by_agent=seen_by_agent,
                client_name=t.client.display_name if t.client else "",
                assignee_id=t.assignee_id,
                assignee_name=assignee.full_name if assignee else None,
                last_reply_by=last_reply_by,
                last_reply_at=last_reply_at,
                created_at=t.created_at,
                updated_at=t.updated_at,
            )
        )

    return TicketListResponse(items=items, total=total, page=page, pages=pages, limit=limit)


def _get_ticket_or_404(db: Session, ticket_id: str, user: CurrentUser) -> Ticket:
    stmt = select(Ticket).where(Ticket.id == ticket_id)
    ticket = db.execute(stmt).scalar_one_or_none()
    if not ticket:
        _error(404, "ticket_not_found", "Тикет не найден")
    if user.role == "agent" and (ticket.assignee_id != user.id or ticket.agent_visible is False):
        _error(403, "forbidden_ticket_access", "Нет доступа к тикету")
    return ticket


def _require_admin(user: CurrentUser):
    if user.role != "admin":
        _error(403, "admin_only", "Действие доступно только администратору")


def _assign_ticket_by_admin(db: Session, ticket: Ticket, assignee: User | None):
    old_assignee = db.get(User, ticket.assignee_id) if ticket.assignee_id else None
    if assignee is None:
        ticket.assignee_id = None
        if ticket.status != TicketStatus.CLOSED.value:
            ticket.status = TicketStatus.UNASSIGNED.value
        ticket.updated_at = datetime.utcnow()
        append_system_message(db, ticket, assignee_change_message(old_assignee, None))
        return

    ticket.assignee_id = assignee.id
    ticket.intake_state = TicketIntakeState.READY.value
    if ticket.status == TicketStatus.UNASSIGNED.value:
        ticket.status = TicketStatus.NEW.value
    ticket.updated_at = datetime.utcnow()
    append_system_message(db, ticket, assignee_change_message(old_assignee, assignee))
    if ticket.status != TicketStatus.CLOSED.value:
        notify_client_about_assignee(db, ticket, assignee)


def _get_or_create_client_profile(db: Session, client_id: str) -> ClientProfile:
    profile = db.execute(select(ClientProfile).where(ClientProfile.client_id == client_id)).scalar_one_or_none()
    if profile is None:
        profile = ClientProfile(
            id=str(uuid.uuid4()),
            client_id=client_id,
            created_at=datetime.utcnow(),
            updated_at=datetime.utcnow(),
        )
        db.add(profile)
        db.commit()
        db.refresh(profile)
    return profile


def _telegram_contact_for_client(db: Session, client_id: str) -> TelegramContact | None:
    return db.execute(select(TelegramContact).where(TelegramContact.client_id == client_id)).scalar_one_or_none()


def _send_to_telegram_client(
    db: Session,
    ticket: Ticket,
    text: str,
    *,
    required: bool = True,
    remove_keyboard: bool = False,
    reply_to_message_id: str | None = None,
) -> dict | None:
    contact = _telegram_contact_for_client(db, ticket.client_id)
    if contact is None:
        if required:
            _error(409, "telegram_contact_not_found", "У клиента нет Telegram-контакта")
        return None
    try:
        return send_message(contact.external_chat_id, text, remove_keyboard=remove_keyboard, reply_to_message_id=reply_to_message_id)
    except TelegramApiError as exc:
        if required:
            _error(502, "telegram_send_failed", f"Telegram не принял сообщение: {exc}")
        return None


def _telegram_message_id(response: dict | None) -> str | None:
    message_id = ((response or {}).get("result") or {}).get("message_id")
    return str(message_id) if message_id is not None else None


def _send_problem_menu_to_telegram_client(db: Session, ticket: Ticket):
    contact = _telegram_contact_for_client(db, ticket.client_id)
    if contact is not None:
        try:
            send_problem_menu(contact.external_chat_id, menu_name=_menu_name_for_ticket_client(db, ticket))
        except TelegramApiError:
            pass


def _menu_name_for_ticket_client(db: Session, ticket: Ticket) -> str:
    profile = _get_or_create_client_profile(db, ticket.client_id)
    if profile.is_verified and profile.destination == "Руководство":
        return DEFAULT_MENU_NAME
    return BASIC_MENU_NAME


def _button_tree_for_menu(config: ButtonConfig, menu: MenuSpec) -> list[dict]:
    tree: list[dict] = []
    for row in menu.rows:
        row_items = []
        for button_key in row:
            button = config.buttons.get(button_key)
            if button is None:
                continue
            item = {
                "key": button.key,
                "label": button.label,
                "kind": button.kind,
                "children": [],
            }
            if button.kind == "submenu" and button.submenu_key and button.submenu_key in config.menus:
                item["children"] = _button_tree_for_menu(config, config.menus[button.submenu_key])
            row_items.append(item)
        if row_items:
            tree.append({"buttons": row_items})
    return tree


def _remove_client_close_button(db: Session, ticket: Ticket):
    if not ticket.client_close_message_id:
        return
    contact = _telegram_contact_for_client(db, ticket.client_id)
    if contact is None:
        ticket.client_close_message_id = None
        return
    try:
        remove_inline_keyboard(contact.external_chat_id, int(ticket.client_close_message_id))
    except Exception:
        pass
    ticket.client_close_message_id = None


def _profile_out(
    profile: ClientProfile,
    telegram_username: str | None,
    telegram_display_name: str | None,
) -> ClientProfileOut:
    return ClientProfileOut(
        telegram_username=telegram_username,
        telegram_display_name=telegram_display_name,
        contact_name=profile.contact_name,
        last_name=profile.last_name,
        email=profile.email,
        phone=profile.phone,
        verification_status=profile.verification_status,
        is_verified=bool(profile.is_verified),
        destination=profile.destination,
        sand_owner=profile.sand_owner,
        sales_group=profile.sales_group,
        department=profile.department,
        first_department=profile.first_department,
        group_name=profile.group_name,
        employee_type=profile.employee_type,
        location_type=profile.location_type,
        anydesk=profile.anydesk,
        internal_number=profile.internal_number,
        external_user_id=profile.external_user_id,
        bitrix_login=profile.bitrix_login,
        bitrix_password=profile.bitrix_password,
        bitrix_id=profile.bitrix_id,
    )


def _attachment_is_image(attachment: TicketAttachment) -> bool:
    mime_type = (attachment.mime_type or "").lower()
    filename = (attachment.original_filename or "").lower()
    return mime_type in {"image/jpeg", "image/png", "image/webp", "image/gif"} or filename.endswith(
        (".jpg", ".jpeg", ".png", ".webp", ".gif")
    )


def _attachment_out(ticket_id: str, attachment: TicketAttachment) -> dict:
    return {
        "id": attachment.id,
        "original_filename": attachment.original_filename,
        "mime_type": attachment.mime_type,
        "file_size": attachment.file_size,
        "download_url": f"/api/v1/tickets/{ticket_id}/attachments/{attachment.id}/download",
        "is_image": _attachment_is_image(attachment),
        "created_at": attachment.created_at,
    }


@router.get("/active", response_model=TicketListResponse)
def list_active(
    sort: str = Query(default="created_at", pattern="^(created_at|updated_at)$"),
    order: str = Query(default="desc", pattern="^(asc|desc)$"),
    page: int = Query(default=1, ge=1),
    limit: int = Query(default=20, ge=1, le=100),
    assignee_ids: list[str] | None = Query(default=None),
    db: Session = Depends(get_db),
    user: CurrentUser = Depends(get_current_user),
):
    statuses = [TicketStatus.NEW.value, TicketStatus.PENDING.value]
    return _list_tickets_paginated(db, user, statuses, False, sort, order, page, limit, assignee_ids)


@router.get("/unassigned", response_model=TicketListResponse)
def list_unassigned(
    sort: str = Query(default="created_at", pattern="^(created_at|updated_at)$"),
    order: str = Query(default="desc", pattern="^(asc|desc)$"),
    page: int = Query(default=1, ge=1),
    limit: int = Query(default=20, ge=1, le=100),
    db: Session = Depends(get_db),
    user: CurrentUser = Depends(get_current_user),
):
    if user.role != "admin":
        _error(403, "admin_only", "Нераспределённые заявки видны только администратору")
    return _list_tickets_paginated(db, user, [TicketStatus.UNASSIGNED.value], False, sort, order, page, limit, None, False)


@router.get("/frozen", response_model=TicketListResponse)
def list_frozen(
    sort: str = Query(default="created_at", pattern="^(created_at|updated_at)$"),
    order: str = Query(default="desc", pattern="^(asc|desc)$"),
    page: int = Query(default=1, ge=1),
    limit: int = Query(default=20, ge=1, le=100),
    assignee_ids: list[str] | None = Query(default=None),
    db: Session = Depends(get_db),
    user: CurrentUser = Depends(get_current_user),
):
    statuses = [TicketStatus.NEW.value, TicketStatus.PENDING.value]
    return _list_tickets_paginated(db, user, statuses, True, sort, order, page, limit, assignee_ids)


@router.get("/closed", response_model=TicketListResponse)
def list_closed(
    sort: str = Query(default="created_at", pattern="^(created_at|updated_at)$"),
    order: str = Query(default="desc", pattern="^(asc|desc)$"),
    page: int = Query(default=1, ge=1),
    limit: int = Query(default=20, ge=1, le=100),
    assignee_ids: list[str] | None = Query(default=None),
    db: Session = Depends(get_db),
    user: CurrentUser = Depends(get_current_user),
):
    statuses = [TicketStatus.CLOSED.value]
    return _list_tickets_paginated(db, user, statuses, None, sort, order, page, limit, assignee_ids)


@router.post("/assign")
def assign_tickets(payload: AssignTicketsPayload, db: Session = Depends(get_db), user: CurrentUser = Depends(get_current_user)):
    _require_admin(user)

    assignee = None
    if payload.assignee_id is not None:
        assignee = db.execute(
            select(User).where(
                and_(
                    User.id == payload.assignee_id,
                    User.role == "agent",
                    User.is_active.is_(True),
                )
            )
        ).scalar_one_or_none()
        if assignee is None:
            _error(404, "agent_not_found", "Агент не найден")

    tickets = db.execute(select(Ticket).where(Ticket.id.in_(payload.ticket_ids))).scalars().all()
    found_ids = {ticket.id for ticket in tickets}
    missing_ids = [ticket_id for ticket_id in payload.ticket_ids if ticket_id not in found_ids]
    if missing_ids:
        _error(404, "ticket_not_found", "Одна или несколько заявок не найдены")

    for ticket in tickets:
        _assign_ticket_by_admin(db, ticket, assignee)
    db.commit()
    return {"ok": True, "assigned": len(tickets)}


@router.get("/{ticket_id}", response_model=TicketCard)
def get_ticket(ticket_id: str, db: Session = Depends(get_db), user: CurrentUser = Depends(get_current_user)):
    t = _get_ticket_or_404(db, ticket_id, user)
    if user.role == "agent":
        view = db.execute(
            select(TicketView).where(and_(TicketView.ticket_id == t.id, TicketView.user_id == user.id))
        ).scalar_one_or_none()
        now = datetime.utcnow()
        if view is None:
            view = TicketView(
                id=str(uuid.uuid4()),
                ticket_id=t.id,
                user_id=user.id,
                first_opened_at=now,
                last_opened_at=now,
            )
            db.add(view)
        else:
            view.last_opened_at = now
        db.commit()

    msg_stmt = select(TicketMessage).where(TicketMessage.ticket_id == t.id).order_by(asc(TicketMessage.created_at))
    messages = db.execute(msg_stmt).scalars().all()
    if user.role == "agent":
        messages = [
            message for message in messages
            if message.message_type != MessageType.SYSTEM.value or message.agent_visible
        ]
    profile = _get_or_create_client_profile(db, t.client_id)
    contact = _telegram_contact_for_client(db, t.client_id)
    telegram_display_name = contact.external_display_name if contact and contact.external_display_name else (t.client.display_name if t.client else None)
    telegram_username = contact.external_username if contact else None
    assignee = db.get(User, t.assignee_id) if t.assignee_id else None
    author_ids = {m.author_user_id for m in messages if m.author_user_id}
    authors = {}
    if author_ids:
        authors = {
            author.id: author.full_name
            for author in db.execute(select(User).where(User.id.in_(author_ids))).scalars().all()
        }
    message_ids = [m.id for m in messages]
    attachments_by_message: dict[str, list[TicketAttachment]] = {message_id: [] for message_id in message_ids}
    if message_ids:
        attachments = db.execute(
            select(TicketAttachment)
            .where(TicketAttachment.message_id.in_(message_ids))
            .order_by(asc(TicketAttachment.created_at))
        ).scalars().all()
        for attachment in attachments:
            attachments_by_message.setdefault(attachment.message_id, []).append(attachment)

    return TicketCard(
        id=t.id,
        subject=t.subject,
        workflow_type=t.workflow_type or TicketWorkflowType.TICKET.value,
        intake_state=t.intake_state or TicketIntakeState.READY.value,
        status=t.status,
        is_frozen=t.is_frozen,
        freeze_until=t.freeze_until,
        closed_at=t.closed_at,
        assignee_id=t.assignee_id,
        assignee_name=assignee.full_name if assignee else None,
        client_name=t.client.display_name if t.client else "",
        client_profile=_profile_out(
            profile,
            telegram_username,
            telegram_display_name,
        ),
        messages=[
            {
                "id": m.id,
                "message_type": m.message_type,
                "body": m.body,
                "author_user_id": m.author_user_id,
                "author_name": authors.get(m.author_user_id) if m.author_user_id else None,
                "telegram_message_id": m.telegram_message_id,
                "message_status": m.message_status or "normal",
                "status_note": m.status_note,
                "replaced_message_id": m.replaced_message_id,
                "created_at": m.created_at,
                "attachments": [_attachment_out(t.id, attachment) for attachment in attachments_by_message.get(m.id, [])],
            }
            for m in messages
        ],
    )


@router.get("/{ticket_id}/attachments/{attachment_id}/download")
def download_attachment(
    ticket_id: str,
    attachment_id: str,
    db: Session = Depends(get_db),
    user: CurrentUser = Depends(get_current_user),
):
    _get_ticket_or_404(db, ticket_id, user)
    attachment = db.get(TicketAttachment, attachment_id)
    if attachment is None or attachment.ticket_id != ticket_id:
        _error(404, "attachment_not_found", "Файл не найден")
    path = Path(attachment.local_path)
    if not path.exists() or not path.is_file():
        _error(404, "attachment_file_missing", "Файл отсутствует в локальном хранилище")
    return FileResponse(
        path,
        media_type=attachment.mime_type or "application/octet-stream",
        filename=attachment.original_filename or path.name,
    )


@router.patch("/{ticket_id}/client-profile", response_model=ClientProfileOut)
def update_client_profile(
    ticket_id: str,
    payload: ClientProfileUpdate,
    db: Session = Depends(get_db),
    user: CurrentUser = Depends(get_current_user),
):
    t = _get_ticket_or_404(db, ticket_id, user)
    profile = _get_or_create_client_profile(db, t.client_id)
    contact = _telegram_contact_for_client(db, t.client_id)

    profile.contact_name = payload.contact_name
    profile.last_name = payload.last_name
    profile.email = payload.email
    profile.phone = payload.phone
    profile.verification_status = payload.verification_status
    profile.is_verified = bool(payload.is_verified)
    profile.destination = payload.destination
    profile.sand_owner = payload.sand_owner
    profile.sales_group = payload.sales_group
    profile.department = payload.department
    profile.first_department = payload.first_department
    profile.group_name = payload.group_name
    profile.employee_type = payload.employee_type
    profile.location_type = payload.location_type
    profile.anydesk = payload.anydesk
    profile.internal_number = payload.internal_number
    profile.external_user_id = payload.external_user_id
    profile.bitrix_login = payload.bitrix_login
    profile.bitrix_password = payload.bitrix_password
    profile.bitrix_id = payload.bitrix_id
    profile.updated_at = datetime.utcnow()

    if t.client and payload.contact_name:
        t.client.display_name = payload.contact_name

    db.commit()
    db.refresh(profile)
    telegram_display_name = contact.external_display_name if contact and contact.external_display_name else (t.client.display_name if t.client else None)
    telegram_username = contact.external_username if contact else None
    return _profile_out(
        profile,
        telegram_username,
        telegram_display_name,
    )


def _add_message(
    db: Session,
    ticket: Ticket,
    message_type: MessageType,
    body: str,
    author_user_id: str | None,
    *,
    agent_visible: bool | None = None,
    telegram_message_id: str | None = None,
    message_status: str = "normal",
    status_note: str | None = None,
    replaced_message_id: str | None = None,
):
    if agent_visible is None:
        agent_visible = message_type != MessageType.SYSTEM
    msg = TicketMessage(
        id=str(uuid.uuid4()),
        ticket_id=ticket.id,
        message_type=message_type.value,
        author_user_id=author_user_id,
        body=body,
        agent_visible=agent_visible,
        telegram_message_id=telegram_message_id,
        message_status=message_status,
        status_note=status_note,
        replaced_message_id=replaced_message_id,
    )
    db.add(msg)
    ticket.updated_at = datetime.utcnow()
    return msg


def _first_client_message(db: Session, ticket: Ticket) -> TicketMessage | None:
    return db.execute(
        select(TicketMessage)
        .where(and_(TicketMessage.ticket_id == ticket.id, TicketMessage.message_type == MessageType.CLIENT.value))
        .order_by(asc(TicketMessage.created_at))
        .limit(1)
    ).scalar_one_or_none()


def _last_agent_message(db: Session, ticket: Ticket) -> TicketMessage | None:
    return db.execute(
        select(TicketMessage)
        .where(and_(TicketMessage.ticket_id == ticket.id, TicketMessage.message_type == MessageType.AGENT.value))
        .order_by(desc(TicketMessage.created_at))
        .limit(1)
    ).scalar_one_or_none()


def _format_task_completion_text(ticket: Ticket, client_message: TicketMessage, agent_message: TicketMessage) -> str:
    created_at = ticket.created_at
    if created_at.tzinfo is None:
        created_at = created_at.replace(tzinfo=ZoneInfo("UTC"))
    local_created_at = created_at.astimezone(ZoneInfo(settings.app_timezone))
    created_text = local_created_at.strftime("%d.%m.%Y, %H:%M")
    return (
        f"По вашей заявке от {created_text}\n"
        f"\"{client_message.body}\"\n"
        "получен ответ:\n"
        f"{agent_message.body}"
    )



def _format_group_task_completion_text(ticket: Ticket, client_message: TicketMessage, answers: list[str]) -> str:
    created_at = ticket.created_at
    if created_at.tzinfo is None:
        created_at = created_at.replace(tzinfo=ZoneInfo("UTC"))
    local_created_at = created_at.astimezone(ZoneInfo(settings.app_timezone))
    created_text = local_created_at.strftime("%d.%m.%Y, %H:%M")
    return (
        f"По вашей заявке от {created_text}\n"
        f"\"{client_message.body}\"\n\n"
        "Получены ответы:\n"
        f"{chr(10).join(answers)}"
    )


@router.get("/{ticket_id}/problem-menu")
def problem_menu_for_ticket(ticket_id: str, db: Session = Depends(get_db), user: CurrentUser = Depends(get_current_user)):
    ticket = _get_ticket_or_404(db, ticket_id, user)
    menu_name = _menu_name_for_ticket_client(db, ticket)
    config = load_button_config(menu_name)
    root_menu = config.menus[config.root_menu_key]
    return {
        "menu_name": menu_name,
        "text": root_menu.text or "Выберите тип проблемы",
        "rows": _button_tree_for_menu(config, root_menu),
    }


def _send_pending_wrong_problem_hint(db: Session, ticket: Ticket):
    if not ticket.wrong_problem_hint:
        return
    if _send_to_telegram_client(db, ticket, ticket.wrong_problem_hint, required=False):
        _add_message(db, ticket, MessageType.SYSTEM, "Подсказка по неверной проблеме отправлена клиенту", None)
        ticket.wrong_problem_hint = None


def _close_group_child_if_ready(db: Session, child: Ticket, last_agent_message: TicketMessage, user: CurrentUser) -> bool:
    if child.task_kind != TicketTaskKind.GROUP_CHILD.value or not child.parent_ticket_id:
        return False

    parent = db.get(Ticket, child.parent_ticket_id)
    if parent is None:
        return False

    _add_message(db, parent, MessageType.AGENT, last_agent_message.body, user.id)

    open_children = db.execute(
        select(Ticket.id)
        .where(
            and_(
                Ticket.parent_ticket_id == parent.id,
                Ticket.id != child.id,
                Ticket.status != TicketStatus.CLOSED.value,
            )
        )
        .limit(1)
    ).first()
    if open_children is not None:
        return True

    parent_client_message = _first_client_message(db, parent)
    if parent_client_message is None:
        return True

    answers = db.execute(
        select(TicketMessage.body)
        .where(and_(TicketMessage.ticket_id == parent.id, TicketMessage.message_type == MessageType.AGENT.value))
        .order_by(asc(TicketMessage.created_at))
    ).scalars().all()
    _send_to_telegram_client(
        db,
        parent,
        _format_group_task_completion_text(parent, parent_client_message, answers),
        required=False,
    )
    parent.status = TicketStatus.CLOSED.value
    parent.closed_at = datetime.utcnow()
    parent.is_frozen = False
    parent.freeze_until = None
    _add_message(db, parent, MessageType.SYSTEM, "Групповая задача завершена системой", None)
    return True


@router.post("/{ticket_id}/reply")
def reply(ticket_id: str, payload: TextPayload, db: Session = Depends(get_db), user: CurrentUser = Depends(get_current_user)):
    ticket = _get_ticket_or_404(db, ticket_id, user)
    if ticket.status == TicketStatus.CLOSED.value:
        _error(409, "ticket_closed_readonly", "Закрытая заявка только для чтения")

    quote_telegram_message_id = None
    if payload.quote_message_id:
        quoted = db.get(TicketMessage, payload.quote_message_id)
        if quoted is not None and quoted.ticket_id == ticket.id:
            quote_telegram_message_id = quoted.telegram_message_id

    sent_message_id = None
    if (ticket.workflow_type or TicketWorkflowType.TICKET.value) != TicketWorkflowType.TASK.value:
        response = _send_to_telegram_client(db, ticket, payload.text, reply_to_message_id=quote_telegram_message_id)
        sent_message_id = _telegram_message_id(response)
    _add_message(db, ticket, MessageType.AGENT, payload.text, user.id, telegram_message_id=sent_message_id)
    _remove_client_close_button(db, ticket)
    if ticket.status == TicketStatus.NEW.value:
        ticket.status = TicketStatus.PENDING.value
    db.commit()
    return {"ok": True}


@router.post("/{ticket_id}/comment")
def comment(ticket_id: str, payload: TextPayload, db: Session = Depends(get_db), user: CurrentUser = Depends(get_current_user)):
    ticket = _get_ticket_or_404(db, ticket_id, user)
    if ticket.status == TicketStatus.CLOSED.value:
        _error(409, "ticket_closed_readonly", "Закрытая заявка только для чтения")

    _add_message(db, ticket, MessageType.INTERNAL, payload.text, user.id)
    db.commit()
    return {"ok": True}


@router.post("/{ticket_id}/greeting")
def greeting(ticket_id: str, db: Session = Depends(get_db), user: CurrentUser = Depends(get_current_user)):
    ticket = _get_ticket_or_404(db, ticket_id, user)
    if ticket.status == TicketStatus.CLOSED.value:
        _error(409, "ticket_closed_readonly", "Закрытая заявка только для чтения")

    # Greeting can be sent only once per ticket.
    existing_greeting_stmt = select(TicketMessage.id).where(
        and_(
            TicketMessage.ticket_id == ticket.id,
            TicketMessage.message_type == MessageType.AGENT.value,
            TicketMessage.body.like("Добр%")
        )
    )
    if db.execute(existing_greeting_stmt).first() is not None:
        _error(409, "greeting_already_sent", "Приветствие уже отправлялось по этой заявке")

    now = datetime.now(ZoneInfo(settings.app_timezone)).time()
    if now <= time(11, 59):
        greet = "Доброе утро"
    elif now <= time(16, 59):
        greet = "Добрый день"
    else:
        greet = "Добрый вечер"

    profile = _get_or_create_client_profile(db, ticket.client_id)
    name = (profile.contact_name or "").strip()
    if not name:
        _error(409, "client_name_required", "Поле 'Имя' в карточке клиента не заполнено")

    text = f"{greet}, {name}!"

    response = _send_to_telegram_client(db, ticket, text)
    _add_message(db, ticket, MessageType.AGENT, text, user.id, telegram_message_id=_telegram_message_id(response))
    _remove_client_close_button(db, ticket)
    if ticket.status == TicketStatus.NEW.value:
        ticket.status = TicketStatus.PENDING.value
    db.commit()
    return {"ok": True, "text": text}


@router.post("/{ticket_id}/freeze")
def freeze(ticket_id: str, payload: FreezePayload, db: Session = Depends(get_db), user: CurrentUser = Depends(get_current_user)):
    ticket = _get_ticket_or_404(db, ticket_id, user)
    if ticket.status == TicketStatus.CLOSED.value:
        _error(409, "freeze_closed_ticket", "Нельзя заморозить закрытую заявку")

    ticket.is_frozen = True
    ticket.freeze_until = payload.freeze_until
    _add_message(db, ticket, MessageType.SYSTEM, "Заявка заморожена", None)
    db.commit()
    return {"ok": True}


@router.post("/{ticket_id}/unfreeze")
def unfreeze(ticket_id: str, db: Session = Depends(get_db), user: CurrentUser = Depends(get_current_user)):
    ticket = _get_ticket_or_404(db, ticket_id, user)
    if not ticket.is_frozen:
        _error(409, "unfreeze_not_frozen", "Заявка не заморожена")

    ticket.is_frozen = False
    ticket.freeze_until = None
    _add_message(db, ticket, MessageType.SYSTEM, "Заявка разморожена", None)
    db.commit()
    return {"ok": True}


@router.post("/{ticket_id}/wrong-problem")
def wrong_problem(
    ticket_id: str,
    payload: WrongProblemPayload,
    db: Session = Depends(get_db),
    user: CurrentUser = Depends(get_current_user),
):
    ticket = _get_ticket_or_404(db, ticket_id, user)
    if ticket.status == TicketStatus.CLOSED.value:
        _error(409, "ticket_closed_readonly", "Закрытая заявка только для чтения")

    config = load_button_config(_menu_name_for_ticket_client(db, ticket))
    button = config.buttons.get(payload.button_key) or find_button(payload.button_key)
    if button is None:
        _error(404, "button_not_found", "Кнопка меню не найдена")
    if button.kind == "submenu":
        _error(409, "submenu_not_final", "Выберите конечную кнопку, а не подменю")

    hint_text = WRONG_PROBLEM_TEXT_TEMPLATE.format(label=button.label)
    if (ticket.workflow_type or TicketWorkflowType.TICKET.value) == TicketWorkflowType.TASK.value:
        ticket.wrong_problem_hint = hint_text
        _add_message(db, ticket, MessageType.SYSTEM, f"После завершения задачи клиенту будет отправлена подсказка: {button.label}", None)
    else:
        response = _send_to_telegram_client(db, ticket, hint_text)
        _add_message(db, ticket, MessageType.AGENT, hint_text, user.id, telegram_message_id=_telegram_message_id(response))
        _add_message(db, ticket, MessageType.SYSTEM, f"Сотрудник подсказал клиенту правильный пункт меню: {button.label}", None)
        _remove_client_close_button(db, ticket)
        if ticket.status == TicketStatus.NEW.value:
            ticket.status = TicketStatus.PENDING.value

    db.commit()
    return {"ok": True, "label": button.label}


@router.patch("/{ticket_id}/messages/{message_id}")
def edit_ticket_message(
    ticket_id: str,
    message_id: str,
    payload: TextPayload,
    db: Session = Depends(get_db),
    user: CurrentUser = Depends(get_current_user),
):
    ticket = _get_ticket_or_404(db, ticket_id, user)
    if ticket.status == TicketStatus.CLOSED.value:
        _error(409, "ticket_closed_readonly", "Закрытая заявка только для чтения")
    message = db.get(TicketMessage, message_id)
    if message is None or message.ticket_id != ticket.id:
        _error(404, "message_not_found", "Сообщение не найдено")
    if message.message_type != MessageType.AGENT.value:
        _error(409, "only_agent_message_edit", "Редактировать можно только сообщения сотрудника")
    if message.message_status == "deleted":
        _error(409, "message_deleted", "Удалённое сообщение нельзя редактировать")
    if not message.telegram_message_id:
        _error(409, "telegram_message_id_missing", "У этого сообщения нет Telegram ID. Его нельзя изменить в Telegram, потому что оно было отправлено до включения синхронизации сообщений.")

    contact = _telegram_contact_for_client(db, ticket.client_id)
    if contact is not None:
        try:
            edit_message_text(contact.external_chat_id, message.telegram_message_id, payload.text)
        except TelegramApiError as exc:
            _error(502, "telegram_edit_failed", f"Telegram не принял редактирование: {exc}")

    message.body = payload.text
    message.message_status = "edited"
    message.status_note = f"{user.full_name} исправил сообщение"
    ticket.updated_at = datetime.utcnow()
    db.commit()
    return {"ok": True}


@router.delete("/{ticket_id}/messages/{message_id}")
def delete_ticket_message(
    ticket_id: str,
    message_id: str,
    db: Session = Depends(get_db),
    user: CurrentUser = Depends(get_current_user),
):
    ticket = _get_ticket_or_404(db, ticket_id, user)
    if ticket.status == TicketStatus.CLOSED.value:
        _error(409, "ticket_closed_readonly", "Закрытая заявка только для чтения")
    message = db.get(TicketMessage, message_id)
    if message is None or message.ticket_id != ticket.id:
        _error(404, "message_not_found", "Сообщение не найдено")
    if message.message_type != MessageType.AGENT.value:
        _error(409, "only_agent_message_delete", "Удалять можно только сообщения сотрудника")
    if message.message_status == "deleted":
        return {"ok": True}
    if not message.telegram_message_id:
        _error(409, "telegram_message_id_missing", "У этого сообщения нет Telegram ID. Его нельзя удалить в Telegram, потому что оно было отправлено до включения синхронизации сообщений.")

    contact = _telegram_contact_for_client(db, ticket.client_id)
    if contact is not None:
        try:
            delete_message(contact.external_chat_id, message.telegram_message_id)
        except TelegramApiError as exc:
            _error(502, "telegram_delete_failed", f"Telegram не принял удаление: {exc}")

    message.message_status = "deleted"
    message.status_note = f"{user.full_name} удалил сообщение"
    ticket.updated_at = datetime.utcnow()
    db.commit()
    return {"ok": True}


@router.post("/{ticket_id}/close")
def close(ticket_id: str, db: Session = Depends(get_db), user: CurrentUser = Depends(get_current_user)):
    ticket = _get_ticket_or_404(db, ticket_id, user)
    if ticket.status == TicketStatus.CLOSED.value:
        _error(409, "ticket_already_closed", "Заявка уже закрыта")
    if ticket.subject == "Первое подключение к боту":
        profile = _get_or_create_client_profile(db, ticket.client_id)
        if not profile.is_verified:
            _error(409, "first_connection_not_verified", "Нельзя закрыть первое подключение, пока карточка клиента не верифицирована")

    last_agent_message = _last_agent_message(db, ticket)
    if last_agent_message is None:
        action_name = "задачу" if (ticket.workflow_type or TicketWorkflowType.TICKET.value) == TicketWorkflowType.TASK.value else "заявку"
        _error(409, "close_not_allowed", f"Нельзя завершить {action_name} без ответа сотрудника")

    ticket.status = TicketStatus.CLOSED.value
    ticket.closed_at = datetime.utcnow()
    ticket.is_frozen = False
    ticket.freeze_until = None
    if (ticket.workflow_type or TicketWorkflowType.TICKET.value) == TicketWorkflowType.TASK.value:
        client_message = _first_client_message(db, ticket)
        if client_message is None:
            _error(409, "task_client_message_required", "У задачи нет текста клиента")
        if ticket.task_kind == TicketTaskKind.GROUP_CHILD.value:
            _close_group_child_if_ready(db, ticket, last_agent_message, user)
            _add_message(db, ticket, MessageType.SYSTEM, "Подзадача завершена сотрудником", None)
        else:
            _send_to_telegram_client(
                db,
                ticket,
                _format_task_completion_text(ticket, client_message, last_agent_message),
                required=False,
            )
            _send_pending_wrong_problem_hint(db, ticket)
            _add_message(db, ticket, MessageType.SYSTEM, "Задача завершена сотрудником", None)
    else:
        _add_message(db, ticket, MessageType.SYSTEM, "Заявка закрыта сотрудником", None)
        _send_to_telegram_client(
            db,
            ticket,
            TICKET_CLOSED_CLIENT_TEXT,
            required=False,
            remove_keyboard=True,
        )
        _send_problem_menu_to_telegram_client(db, ticket)
    db.commit()
    return {"ok": True}


@router.post("/{ticket_id}/restore")
def restore(ticket_id: str, db: Session = Depends(get_db), user: CurrentUser = Depends(get_current_user)):
    ticket = _get_ticket_or_404(db, ticket_id, user)
    if ticket.status != TicketStatus.CLOSED.value:
        _error(409, "ticket_not_closed", "Заявка не закрыта")

    active_exists = db.execute(
        select(Ticket.id)
        .where(
            and_(
                Ticket.client_id == ticket.client_id,
                Ticket.id != ticket.id,
                Ticket.status != TicketStatus.CLOSED.value,
                or_(
                    Ticket.workflow_type.is_(None),
                    Ticket.workflow_type.notin_([TicketWorkflowType.TASK.value, TicketWorkflowType.AUTO.value]),
                ),
            )
        )
        .limit(1)
    ).first()
    if active_exists is not None:
        _error(409, "client_has_active_ticket", "У клиента уже есть активная заявка")

    ticket.status = TicketStatus.NEW.value if ticket.assignee_id else TicketStatus.UNASSIGNED.value
    ticket.closed_at = None
    ticket.is_frozen = False
    ticket.freeze_until = None
    ticket.updated_at = datetime.utcnow()
    assignee = db.get(User, ticket.assignee_id) if ticket.assignee_id else None
    _add_message(db, ticket, MessageType.SYSTEM, f"Заявка возвращена в работу. Ответственный: {assignee_label(assignee)}", None)
    db.commit()
    return {"ok": True}
