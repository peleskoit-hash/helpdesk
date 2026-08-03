from fastapi import HTTPException
from sqlalchemy import Select, and_, asc, desc, exists, func, or_, select
from sqlalchemy.orm import Session

from ..auth import CurrentUser, is_admin, is_readonly_chiki_piki, is_support_user
from ..models import BotButtonSetting, Client, ClientProfile, MessageType, TelegramContact, Ticket, TicketMessage, TicketStatus, TicketView, TicketWorkflowType, TicketIntakeState, User
from ..schemas import TicketCountsOut, TicketListItem, TicketListResponse
from .ticket_special import _display_assignee_name
from .ticket_subject import build_ticket_subject_map


def _error(status_code: int, code: str, message: str):
    raise HTTPException(status_code=status_code, detail={"error": {"code": code, "message": message, "details": {}}})


def apply_role_filter(stmt: Select, user: CurrentUser):
    if is_support_user(user):
        return stmt.where(and_(Ticket.assignee_id == user.id, Ticket.agent_visible.is_(True)))
    if is_readonly_chiki_piki(user):
        call_center_key_match = or_(
            Ticket.button_config_key.like("Колл Центр|%"),
            Ticket.instruction_button_key.like("Колл Центр|%"),
        )
        call_center_registry_match = or_(
            exists(
                select(BotButtonSetting.id).where(
                    and_(
                        BotButtonSetting.button_key == Ticket.button_config_key,
                        func.lower(func.trim(BotButtonSetting.menu_type)) == "колл центр",
                    )
                )
            ),
            exists(
                select(BotButtonSetting.id).where(
                    and_(
                        BotButtonSetting.button_key == Ticket.instruction_button_key,
                        func.lower(func.trim(BotButtonSetting.menu_type)) == "колл центр",
                    )
                )
            ),
        )
        return stmt.where(
            or_(
                call_center_key_match,
                call_center_registry_match,
            )
        )
    return stmt


def apply_role_filter_for_statuses(
    stmt: Select,
    user: CurrentUser,
    statuses: list[str],
    include_support_special_closed: bool = False,
    include_support_all_closed: bool = False,
):
    if is_support_user(user):
        assigned_clause = and_(Ticket.assignee_id == user.id, Ticket.agent_visible.is_(True))
        if include_support_all_closed and TicketStatus.CLOSED.value in statuses:
            return stmt
        if include_support_special_closed and TicketStatus.CLOSED.value in statuses:
            return stmt.where(or_(assigned_clause, and_(Ticket.status == TicketStatus.CLOSED.value, Ticket.is_special.is_(True))))
        return stmt.where(assigned_clause)
    return apply_role_filter(stmt, user)


def apply_admin_assignee_filter(stmt: Select, user: CurrentUser, assignee_ids: list[str] | None):
    if not is_admin(user):
        return stmt
    if not assignee_ids:
        return stmt.where(False)
    return stmt.where(Ticket.assignee_id.in_(assignee_ids))


def sort_clause(sort: str, order: str):
    field = Ticket.created_at if sort == "created_at" else Ticket.updated_at
    return asc(field) if order == "asc" else desc(field)


def apply_search_filter(stmt: Select, query: str | None):
    q = (query or "").strip()
    if not q:
        return stmt
    pattern = f"%{q}%"
    return stmt.where(
        or_(
            Ticket.subject.ilike(pattern),
            Ticket.button_config_key.ilike(pattern),
            Ticket.instruction_button_key.ilike(pattern),
            exists(
                select(Client.id).where(
                    and_(
                        Client.id == Ticket.client_id,
                        Client.display_name.ilike(pattern),
                    )
                )
            ),
            exists(
                select(BotButtonSetting.id).where(
                    and_(
                        BotButtonSetting.button_key == Ticket.button_config_key,
                        or_(
                            BotButtonSetting.button_path.ilike(pattern),
                            BotButtonSetting.button_label.ilike(pattern),
                            BotButtonSetting.menu_type.ilike(pattern),
                        ),
                    )
                )
            ),
            exists(
                select(BotButtonSetting.id).where(
                    and_(
                        BotButtonSetting.button_key == Ticket.instruction_button_key,
                        or_(
                            BotButtonSetting.button_path.ilike(pattern),
                            BotButtonSetting.button_label.ilike(pattern),
                            BotButtonSetting.menu_type.ilike(pattern),
                        ),
                    )
                )
            ),
            exists(
                select(TicketMessage.id).where(
                    and_(
                        TicketMessage.ticket_id == Ticket.id,
                        TicketMessage.body.ilike(pattern),
                    )
                )
            ),
        )
    )


def last_reply_meta(db: Session, ticket_id: str):
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


def _normalized_text(value) -> str:
    return str(value or "").strip()


def _snapshot_custom_fio(ticket: Ticket) -> str:
    snapshot = ticket.call_center_profile_snapshot_json or {}
    custom_fields = snapshot.get("custom_fields") if isinstance(snapshot, dict) else None
    if not isinstance(custom_fields, dict):
        return ""
    return _normalized_text(custom_fields.get("custom_fio"))


def _profile_custom_fio(db: Session, ticket: Ticket) -> str:
    profile = db.execute(select(ClientProfile).where(ClientProfile.client_id == ticket.client_id)).scalar_one_or_none()
    if profile is None or not isinstance(profile.custom_fields, dict):
        return ""
    return _normalized_text(profile.custom_fields.get("custom_fio"))


def _telegram_contact(db: Session, ticket: Ticket) -> TelegramContact | None:
    return db.execute(select(TelegramContact).where(TelegramContact.client_id == ticket.client_id)).scalar_one_or_none()


def requester_display_name(db: Session, ticket: Ticket) -> str:
    telegram_contact = _telegram_contact(db, ticket)
    for value in (
        _snapshot_custom_fio(ticket),
        _profile_custom_fio(db, ticket),
        _normalized_text(telegram_contact.external_display_name if telegram_contact else ""),
        _normalized_text(ticket.client.display_name if ticket.client else ""),
        _normalized_text(telegram_contact.external_username if telegram_contact else ""),
    ):
        if value:
            return value
    return "Клиент"


def seen_by_agent(db: Session, ticket_id: str, user: CurrentUser) -> bool:
    if not is_support_user(user):
        return False
    stmt = select(TicketView.id).where(
        and_(TicketView.ticket_id == ticket_id, TicketView.user_id == user.id)
    ).limit(1)
    return db.execute(stmt).first() is not None


def list_tickets_paginated(
    *,
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
    include_special_open: bool = False,
    include_support_special_closed: bool = False,
    include_support_all_closed: bool = False,
    require_admin_or_chiki: bool = False,
    query: str | None = None,
    ensure_ticket_number_fn=None,
) -> TicketListResponse:
    if require_admin_or_chiki and not (is_admin(user) or is_readonly_chiki_piki(user)):
        _error(403, "admin_only", "Нераспределённые заявки видны только администратору")

    conditions = [Ticket.status.in_(statuses)]
    if not include_special_open and not is_readonly_chiki_piki(user) and TicketStatus.CLOSED.value not in statuses:
        conditions.append(Ticket.is_special.is_(False))
    if is_frozen is not None:
        conditions.append(Ticket.is_frozen.is_(is_frozen))

    base_stmt = select(Ticket).where(and_(*conditions))
    base_stmt = apply_role_filter_for_statuses(
        base_stmt,
        user,
        statuses,
        include_support_special_closed,
        include_support_all_closed,
    )
    if filter_admin_assignees:
        base_stmt = apply_admin_assignee_filter(base_stmt, user, admin_assignee_ids)
    base_stmt = apply_search_filter(base_stmt, query)

    total_stmt = select(func.count()).select_from(base_stmt.subquery())
    total = db.execute(total_stmt).scalar_one()

    pages = max(1, (total + limit - 1) // limit)
    if page > pages:
        page = pages

    offset = (page - 1) * limit
    stmt = base_stmt.order_by(sort_clause(sort, order)).offset(offset).limit(limit)
    tickets = db.execute(stmt).scalars().all()

    items: list[TicketListItem] = []
    has_new_numbers = False
    subject_map = build_ticket_subject_map(db, tickets)
    for t in tickets:
        if t.ticket_number is None:
            has_new_numbers = True
        ticket_number = ensure_ticket_number_fn(db, t) if ensure_ticket_number_fn else t.ticket_number
        last_reply_by, last_reply_at = last_reply_meta(db, t.id)
        seen = seen_by_agent(db, t.id, user)
        assignee = db.get(User, t.assignee_id) if t.assignee_id else None
        special_assignee = db.get(User, t.special_assignee_id) if t.special_assignee_id else None
        display_assignee_name = _display_assignee_name(t, assignee, special_assignee)
        items.append(
            TicketListItem(
                id=t.id,
                ticket_number=ticket_number,
                subject=subject_map.get(t.id, t.subject),
                workflow_type=t.workflow_type or TicketWorkflowType.TICKET.value,
                intake_state=t.intake_state or TicketIntakeState.READY.value,
                status=t.status,
                is_frozen=t.is_frozen,
                seen_by_agent=seen,
                client_name=t.client.display_name if t.client else "",
                requester_display_name=requester_display_name(db, t),
                assignee_id=t.assignee_id,
                assignee_name=display_assignee_name,
                is_special=bool(t.is_special),
                special_assignee_id=t.special_assignee_id,
                special_assignee_name=special_assignee.full_name if special_assignee else None,
                last_reply_by=last_reply_by,
                last_reply_at=last_reply_at,
                created_at=t.created_at,
                updated_at=t.updated_at,
            )
        )
    if has_new_numbers:
        db.commit()

    return TicketListResponse(items=items, total=total, page=page, pages=pages, limit=limit)


def list_special_tickets(
    *,
    db: Session,
    user: CurrentUser,
    sort: str,
    order: str,
    page: int,
    limit: int,
    query: str | None,
    ensure_ticket_number_fn,
) -> TicketListResponse:
    if not is_admin(user):
        _error(403, "admin_only", "Действие доступно только администратору")
    conditions = [
        Ticket.is_special.is_(True),
        Ticket.status != TicketStatus.CLOSED.value,
    ]
    base_stmt = select(Ticket).where(and_(*conditions))
    base_stmt = apply_search_filter(base_stmt, query)
    total_stmt = select(func.count()).select_from(base_stmt.subquery())
    total = db.execute(total_stmt).scalar_one()
    pages = max(1, (total + limit - 1) // limit)
    if page > pages:
        page = pages
    offset = (page - 1) * limit
    stmt = base_stmt.order_by(sort_clause(sort, order)).offset(offset).limit(limit)
    tickets = db.execute(stmt).scalars().all()
    items: list[TicketListItem] = []
    subject_map = build_ticket_subject_map(db, tickets)
    for t in tickets:
        if t.ticket_number is None:
            ensure_ticket_number_fn(db, t)
        last_reply_by, last_reply_at = last_reply_meta(db, t.id)
        assignee = db.get(User, t.assignee_id) if t.assignee_id else None
        special_assignee = db.get(User, t.special_assignee_id) if t.special_assignee_id else None
        display_assignee_name = _display_assignee_name(t, assignee, special_assignee)
        items.append(
            TicketListItem(
                id=t.id,
                ticket_number=t.ticket_number,
                subject=subject_map.get(t.id, t.subject),
                workflow_type=t.workflow_type or TicketWorkflowType.TICKET.value,
                intake_state=t.intake_state or TicketIntakeState.READY.value,
                status=t.status,
                is_frozen=t.is_frozen,
                seen_by_agent=False,
                client_name=t.client.display_name if t.client else "",
                requester_display_name=requester_display_name(db, t),
                assignee_id=t.assignee_id,
                assignee_name=display_assignee_name,
                is_special=True,
                special_assignee_id=t.special_assignee_id,
                special_assignee_name=special_assignee.full_name if special_assignee else None,
                last_reply_by=last_reply_by,
                last_reply_at=last_reply_at,
                created_at=t.created_at,
                updated_at=t.updated_at,
            )
        )
    db.commit()
    return TicketListResponse(items=items, total=total, page=page, pages=pages, limit=limit)


def build_ticket_counts(
    *,
    db: Session,
    user: CurrentUser,
    assignee_ids: list[str] | None,
) -> TicketCountsOut:
    def count_tickets(statuses: list[str], is_frozen: bool | None, apply_admin_filter: bool, special_only: bool = False) -> int:
        conditions = [Ticket.status.in_(statuses)]
        if special_only:
            conditions.append(Ticket.is_special.is_(True))
        elif TicketStatus.CLOSED.value not in statuses and not is_readonly_chiki_piki(user):
            conditions.append(Ticket.is_special.is_(False))
        if is_frozen is not None:
            conditions.append(Ticket.is_frozen.is_(is_frozen))
        stmt = select(Ticket).where(and_(*conditions))
        stmt = apply_role_filter_for_statuses(
            stmt,
            user,
            statuses,
            include_support_special_closed=(TicketStatus.CLOSED.value in statuses),
            include_support_all_closed=(TicketStatus.CLOSED.value in statuses),
        )
        if apply_admin_filter:
            stmt = apply_admin_assignee_filter(stmt, user, assignee_ids)
        total_stmt = select(func.count()).select_from(stmt.subquery())
        return int(db.execute(total_stmt).scalar_one() or 0)

    return TicketCountsOut(
        unassigned=count_tickets([TicketStatus.UNASSIGNED.value], False, False) if (is_admin(user) or is_readonly_chiki_piki(user)) else 0,
        active=count_tickets([TicketStatus.NEW.value, TicketStatus.PENDING.value], False, True),
        frozen=count_tickets([TicketStatus.NEW.value, TicketStatus.PENDING.value], True, True),
        closed=count_tickets([TicketStatus.CLOSED.value], None, False),
        special=count_tickets([TicketStatus.NEW.value, TicketStatus.PENDING.value, TicketStatus.UNASSIGNED.value], None, False, True) if is_admin(user) else 0,
    )
