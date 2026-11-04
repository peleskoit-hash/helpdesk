from functools import lru_cache

from fastapi import HTTPException
from sqlalchemy import JSON, Numeric, Select, String, and_, asc, case, cast, desc, exists, func, or_, select
from sqlalchemy.orm import Session
from sqlalchemy.sql import operators

from ..auth import CurrentUser, is_admin, is_readonly_chiki_piki, is_support_user
from ..models import BotButtonSetting, Client, ClientProfile, InternalTaskOccurrence, MessageType, TelegramContact, Ticket, TicketMessage, TicketStatus, TicketTaskKind, TicketView, TicketWorkflowType, TicketIntakeState, User
from ..schemas import TicketCountsOut, TicketListItem, TicketListResponse
from .temporary_rop import TEMPORARY_ROP_FIELD, TEMPORARY_ROP_SUFFIX, with_temporary_rop_suffix
from .ticket_special import _display_assignee_name
from .ticket_subject import build_ticket_subject_map
from .button_config import button_key_matches


def _error(status_code: int, code: str, message: str):
    raise HTTPException(status_code=status_code, detail={"error": {"code": code, "message": message, "details": {}}})


def apply_group_parent_visibility_filter(stmt: Select, user: CurrentUser):
    if not is_admin(user):
        return stmt.where(or_(Ticket.task_kind.is_(None), Ticket.task_kind != TicketTaskKind.GROUP_PARENT.value))
    return stmt


def apply_role_filter(stmt: Select, user: CurrentUser):
    stmt = apply_group_parent_visibility_filter(stmt, user)
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
                        button_key_matches(Ticket.button_config_key),
                        func.lower(func.trim(BotButtonSetting.menu_type)) == "колл центр",
                    )
                )
            ),
            exists(
                select(BotButtonSetting.id).where(
                    and_(
                        button_key_matches(Ticket.instruction_button_key),
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
        stmt = apply_group_parent_visibility_filter(stmt, user)
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


@lru_cache(maxsize=1)
def _casefold_substitutions():
    # Casefold is per character; avoid database locale-dependent lower() behavior.
    result = {}
    for code in range(0x110000):
        char = chr(code)
        if char != char.casefold():
            result[char] = char.casefold()
    return result


def _casefold_search_expression(db: Session, expression, query: str):
    if db.get_bind().dialect.name == "sqlite":
        db.connection().connection.driver_connection.create_function(
            "esdesk_requester_casefold", 1, lambda value: str(value or "").casefold(), deterministic=True,
        )
        return func.esdesk_requester_casefold(expression).contains(query.casefold(), autoescape=True)
    folded = expression
    needle_chars = set(query.casefold())
    # Other characters cannot contribute to a match of this literal query.
    relevant = {source: target for source, target in _casefold_substitutions().items()
                if set(target) & needle_chars}
    singles = {source: target for source, target in relevant.items() if len(source) == len(target) == 1}
    if singles:
        folded = func.translate(folded, "".join(singles), "".join(singles.values()))
    for source, target in relevant.items():
        if source not in singles:
            folded = func.replace(folded, source, target)
    return folded.contains(query.casefold(), autoescape=True)


def _requester_search_expression(db: Session):
    """SQL counterpart of the display helpers below; search never materializes tickets."""
    postgres = db.get_bind().dialect.name == "postgresql"
    trim = func.btrim if postgres else func.trim
    whitespace = "".join(chr(code) for code in range(0x3001) if chr(code).isspace())

    def json_type(value):
        if postgres:
            return func.json_typeof(cast(value, JSON))
        # Inspect the original JSON path: JSON_QUOTE(JSON_EXTRACT(...)) loses booleans.
        path = []
        while getattr(value, "operator", None) is operators.json_getitem_op:
            path.insert(0, value.right.value)
            value = value.left
        return func.json_type(value, "$" + "".join(f".{key}" for key in path))

    def normalized(value):
        return func.nullif(trim(cast(value, String), whitespace), "", type_=String)

    def json_text(value):
        kind = json_type(value)
        scalar = value.as_string()
        return normalized(case(
            (kind.in_(["null", "false"]), None),
            (kind == "true", "True"),
            (and_(kind == "boolean", scalar == "false"), None),
            (and_(kind.in_(["integer", "real", "number"]), cast(scalar, Numeric) == 0), None),
            else_=scalar,
        ))

    snapshot = Ticket.call_center_profile_snapshot_json
    delta = snapshot["profile_delta"]
    delta_is_object = json_type(delta) == "object"
    snapshot_fio = case(
        (and_(delta_is_object, json_type(delta["custom_fields"]) == "object"), json_text(delta["custom_fields"]["custom_fio"])),
        (json_type(snapshot["custom_fields"]) == "object", json_text(snapshot["custom_fields"]["custom_fio"])),
    )
    # The Python helper replaces a falsy snapshot with {}, but rejects truthy non-dicts.
    object_or_empty = or_(json_type(snapshot) == "object", snapshot.is_(None),
                         cast(snapshot, String).in_(["null", "false", "0", "0.0", '""', "[]"]))
    last = case((delta_is_object, json_text(delta["last_name"])), else_=normalized(func.coalesce(
        func.nullif(Ticket.call_center_employee_last_name, ""), snapshot["last_name"].as_string())))
    first = case((delta_is_object, json_text(delta["contact_name"])), else_=normalized(func.coalesce(
        func.nullif(Ticket.call_center_employee_first_name, ""), snapshot["first_name"].as_string())))
    snapshot_name = case((object_or_empty, normalized(func.coalesce(last, "").concat(" ").concat(func.coalesce(first, "")))))
    profile_fio = case((json_type(ClientProfile.custom_fields) == "object", json_text(ClientProfile.custom_fields["custom_fio"])))
    ordinary = func.coalesce(snapshot_fio, case(
        (or_(Ticket.call_center_employee_lookup_at.is_not(None), json_type(snapshot) == "object"), snapshot_name),
        else_=profile_fio,
    ), normalized(TelegramContact.external_display_name), normalized(Client.display_name), normalized(TelegramContact.external_username))
    rop = ClientProfile.custom_fields[TEMPORARY_ROP_FIELD]
    rop_enabled = and_(json_type(rop).in_(["true", "boolean"]), rop.as_boolean().is_(True))
    ordinary = case((and_(rop_enabled, ~ordinary.endswith(TEMPORARY_ROP_SUFFIX)), ordinary.concat(TEMPORARY_ROP_SUFFIX)),
                    else_=func.coalesce(ordinary, "Клиент"))
    return case((InternalTaskOccurrence.id.is_not(None), case(
        (func.lower(normalized(InternalTaskOccurrence.display_author)).in_(["1", "true", "t", "yes", "y", "on"]),
         func.coalesce(normalized(User.full_name), normalized(User.login), "Система")),
        else_="Система",
    )), else_=ordinary)


def _requester_search_filter(db: Session, stmt: Select, query: str):
    names = (stmt.with_only_columns(Ticket.id, maintain_column_froms=True)
             .outerjoin(Client, Client.id == Ticket.client_id)
             .outerjoin(ClientProfile, ClientProfile.client_id == Ticket.client_id)
             .outerjoin(TelegramContact, TelegramContact.client_id == Ticket.client_id)
             .outerjoin(InternalTaskOccurrence, InternalTaskOccurrence.ticket_id == Ticket.id)
             .outerjoin(User, User.id == InternalTaskOccurrence.creator_admin_id)
             .where(_casefold_search_expression(db, _requester_search_expression(db), query))
             .correlate(None))
    return Ticket.id.in_(names)


def apply_search_filter(stmt: Select, query: str | None, db: Session):
    q = (query or "").strip()
    if not q:
        return stmt
    pattern = f"%{q}%"
    return stmt.where(
        or_(
            Ticket.subject.ilike(pattern),
            Ticket.button_config_key.ilike(pattern),
            Ticket.instruction_button_key.ilike(pattern),
            _requester_search_filter(db, stmt, q),
            exists(
                select(BotButtonSetting.id).where(
                    and_(
                        button_key_matches(Ticket.button_config_key),
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
                        button_key_matches(Ticket.instruction_button_key),
                        or_(
                            BotButtonSetting.button_path.ilike(pattern),
                            BotButtonSetting.button_label.ilike(pattern),
                            BotButtonSetting.menu_type.ilike(pattern),
                        ),
                    )
                )
            ),
            Ticket.id.in_(select(TicketMessage.ticket_id).where(TicketMessage.body.ilike(pattern))),
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
    profile_delta = snapshot.get("profile_delta") if isinstance(snapshot, dict) else None
    if isinstance(profile_delta, dict):
        custom_fields = profile_delta.get("custom_fields")
        if isinstance(custom_fields, dict):
            return _normalized_text(custom_fields.get("custom_fio"))
    custom_fields = snapshot.get("custom_fields") if isinstance(snapshot, dict) else None
    if not isinstance(custom_fields, dict):
        return ""
    return _normalized_text(custom_fields.get("custom_fio"))


def _profile_custom_fio(db: Session, ticket: Ticket) -> str:
    profile = db.execute(select(ClientProfile).where(ClientProfile.client_id == ticket.client_id)).scalar_one_or_none()
    return _profile_custom_fio_value(profile)


def _profile_custom_fio_value(profile: ClientProfile | None) -> str:
    if profile is None or not isinstance(profile.custom_fields, dict):
        return ""
    return _normalized_text(profile.custom_fields.get("custom_fio"))


def _telegram_contact(db: Session, ticket: Ticket) -> TelegramContact | None:
    return db.execute(select(TelegramContact).where(TelegramContact.client_id == ticket.client_id)).scalar_one_or_none()


def _call_center_lookup_performed(ticket: Ticket) -> bool:
    return ticket.call_center_employee_lookup_at is not None or isinstance(ticket.call_center_profile_snapshot_json, dict)


def _snapshot_name_parts(ticket: Ticket) -> tuple[str, str]:
    snapshot = ticket.call_center_profile_snapshot_json or {}
    if not isinstance(snapshot, dict):
        return "", ""
    profile_delta = snapshot.get("profile_delta")
    if isinstance(profile_delta, dict):
        return (
            _normalized_text(profile_delta.get("last_name")),
            _normalized_text(profile_delta.get("contact_name")),
        )
    return (
        _normalized_text(ticket.call_center_employee_last_name or snapshot.get("last_name")),
        _normalized_text(ticket.call_center_employee_first_name or snapshot.get("first_name")),
    )


def _ordinary_requester_display_name(db: Session, ticket: Ticket) -> str:
    profile = db.execute(select(ClientProfile).where(ClientProfile.client_id == ticket.client_id)).scalar_one_or_none()
    return _ordinary_requester_display_name_from_profile(db, ticket, profile)


def _ordinary_requester_display_name_from_profile(db: Session, ticket: Ticket, profile: ClientProfile | None) -> str:
    return _ordinary_requester_display_name_from_sources(ticket, profile, _telegram_contact(db, ticket))


def _ordinary_requester_display_name_from_sources(
    ticket: Ticket, profile: ClientProfile | None, telegram_contact: TelegramContact | None,
) -> str:
    values = [_snapshot_custom_fio(ticket)]
    if _call_center_lookup_performed(ticket):
        last_name, first_name = _snapshot_name_parts(ticket)
        values.append(_normalized_text(" ".join(part for part in (last_name, first_name) if part)))
    else:
        values.append(_profile_custom_fio_value(profile))
    values.extend(
        [
            _normalized_text(telegram_contact.external_display_name if telegram_contact else ""),
            _normalized_text(ticket.client.display_name if ticket.client else ""),
            _normalized_text(telegram_contact.external_username if telegram_contact else ""),
        ]
    )
    for value in values:
        if value:
            return with_temporary_rop_suffix(value, profile)
    return "Клиент"


def _display_author_enabled(value) -> bool:
    if value is True:
        return True
    if isinstance(value, str):
        return value.strip().lower() in {"1", "true", "t", "yes", "y", "on"}
    return False


def requester_display_name(db: Session, ticket: Ticket) -> str:
    internal_name = internal_task_requester_display_name(db, ticket)
    if internal_name is not None:
        return internal_name
    return _ordinary_requester_display_name(db, ticket)


def internal_task_requester_display_name(db: Session, ticket: Ticket) -> str | None:
    occurrence = db.execute(
        select(InternalTaskOccurrence)
        .where(InternalTaskOccurrence.ticket_id == ticket.id)
        .limit(1)
    ).scalar_one_or_none()
    if occurrence is None:
        return None
    if not _display_author_enabled(occurrence.display_author):
        return "Система"
    creator = db.get(User, occurrence.creator_admin_id)
    return _normalized_text(creator.full_name if creator else "") or _normalized_text(creator.login if creator else "") or "Система"


def _internal_task_requester_map(db: Session, ticket_ids: list[str]) -> dict[str, str]:
    if not ticket_ids:
        return {}
    rows = db.execute(
        select(
            InternalTaskOccurrence.ticket_id,
            InternalTaskOccurrence.display_author,
            User.full_name,
            User.login,
        )
        .join(User, User.id == InternalTaskOccurrence.creator_admin_id, isouter=True)
        .where(InternalTaskOccurrence.ticket_id.in_(ticket_ids))
    ).all()
    result: dict[str, str] = {}
    for ticket_id, display_author, full_name, login in rows:
        if _display_author_enabled(display_author):
            result[ticket_id] = _normalized_text(full_name) or _normalized_text(login) or "Система"
        else:
            result[ticket_id] = "Система"
    return result


def _client_profile_map(db: Session, client_ids: list[str]) -> dict[str, ClientProfile]:
    ids = [client_id for client_id in dict.fromkeys(client_ids) if client_id]
    if not ids:
        return {}
    rows = db.execute(select(ClientProfile).where(ClientProfile.client_id.in_(ids))).scalars().all()
    return {profile.client_id: profile for profile in rows}


def _list_requester_department(profile: ClientProfile | None, is_internal_task: bool) -> str:
    if is_internal_task:
        return "Система"
    return _normalized_text(profile.department if profile else "") or "—"


def _list_requester_location_type(profile: ClientProfile | None, is_internal_task: bool) -> str:
    if is_internal_task:
        return ""
    return _normalized_text(profile.location_type if profile else "")


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
    base_stmt = apply_search_filter(base_stmt, query, db)

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
    internal_requester_map = _internal_task_requester_map(db, [ticket.id for ticket in tickets])
    profile_map = _client_profile_map(db, [ticket.client_id for ticket in tickets])
    for t in tickets:
        ticket_number = t.ticket_number
        if ticket_number is None and (t.intake_state or TicketIntakeState.READY.value) == TicketIntakeState.READY.value and ensure_ticket_number_fn:
            ticket_number = ensure_ticket_number_fn(db, t)
            has_new_numbers = True
        last_reply_by, last_reply_at = last_reply_meta(db, t.id)
        seen = seen_by_agent(db, t.id, user)
        assignee = db.get(User, t.assignee_id) if t.assignee_id else None
        special_assignee = db.get(User, t.special_assignee_id) if t.special_assignee_id else None
        display_assignee_name = _display_assignee_name(t, assignee, special_assignee)
        internal_requester_name = internal_requester_map.get(t.id)
        is_internal_task = t.id in internal_requester_map
        requester_profile = profile_map.get(t.client_id)
        visible_requester_name = internal_requester_name or _ordinary_requester_display_name_from_profile(db, t, requester_profile)
        items.append(
            TicketListItem(
                id=t.id,
                channel=t.channel,
                ticket_number=ticket_number,
                subject=subject_map.get(t.id, t.subject),
                workflow_type=t.workflow_type or TicketWorkflowType.TICKET.value,
                task_kind=t.task_kind,
                is_internal_task=is_internal_task,
                intake_state=t.intake_state or TicketIntakeState.READY.value,
                status=t.status,
                is_frozen=t.is_frozen,
                seen_by_agent=seen,
                client_name=internal_requester_name or (t.client.display_name if t.client else ""),
                requester_display_name=visible_requester_name,
                requester_department=_list_requester_department(requester_profile, is_internal_task),
                requester_location_type=_list_requester_location_type(requester_profile, is_internal_task),
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
    base_stmt = apply_search_filter(base_stmt, query, db)
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
    internal_requester_map = _internal_task_requester_map(db, [ticket.id for ticket in tickets])
    profile_map = _client_profile_map(db, [ticket.client_id for ticket in tickets])
    for t in tickets:
        if t.ticket_number is None and (t.intake_state or TicketIntakeState.READY.value) == TicketIntakeState.READY.value:
            ensure_ticket_number_fn(db, t)
        last_reply_by, last_reply_at = last_reply_meta(db, t.id)
        assignee = db.get(User, t.assignee_id) if t.assignee_id else None
        special_assignee = db.get(User, t.special_assignee_id) if t.special_assignee_id else None
        display_assignee_name = _display_assignee_name(t, assignee, special_assignee)
        internal_requester_name = internal_requester_map.get(t.id)
        is_internal_task = t.id in internal_requester_map
        requester_profile = profile_map.get(t.client_id)
        visible_requester_name = internal_requester_name or _ordinary_requester_display_name_from_profile(db, t, requester_profile)
        items.append(
            TicketListItem(
                id=t.id,
                channel=t.channel,
                ticket_number=t.ticket_number,
                subject=subject_map.get(t.id, t.subject),
                workflow_type=t.workflow_type or TicketWorkflowType.TICKET.value,
                task_kind=t.task_kind,
                is_internal_task=is_internal_task,
                intake_state=t.intake_state or TicketIntakeState.READY.value,
                status=t.status,
                is_frozen=t.is_frozen,
                seen_by_agent=False,
                client_name=internal_requester_name or (t.client.display_name if t.client else ""),
                requester_display_name=visible_requester_name,
                requester_department=_list_requester_department(requester_profile, is_internal_task),
                requester_location_type=_list_requester_location_type(requester_profile, is_internal_task),
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
    def ticket_count_stmt(statuses: list[str], is_frozen: bool | None, special_only: bool = False, count_user: CurrentUser = user):
        conditions = [Ticket.status.in_(statuses)]
        if special_only:
            conditions.append(Ticket.is_special.is_(True))
        elif TicketStatus.CLOSED.value not in statuses and not is_readonly_chiki_piki(count_user):
            conditions.append(Ticket.is_special.is_(False))
        if is_frozen is not None:
            conditions.append(Ticket.is_frozen.is_(is_frozen))
        stmt = select(Ticket).where(and_(*conditions))
        return apply_role_filter_for_statuses(
            stmt,
            count_user,
            statuses,
            include_support_special_closed=(TicketStatus.CLOSED.value in statuses),
            include_support_all_closed=(TicketStatus.CLOSED.value in statuses),
        )

    def count_tickets(statuses: list[str], is_frozen: bool | None, apply_admin_filter: bool, special_only: bool = False) -> int:
        stmt = ticket_count_stmt(statuses, is_frozen, special_only)
        if apply_admin_filter:
            stmt = apply_admin_assignee_filter(stmt, user, assignee_ids)
        total_stmt = select(func.count()).select_from(stmt.subquery())
        return int(db.execute(total_stmt).scalar_one() or 0)

    def build_by_assignee() -> dict[str, dict[str, int]] | None:
        if not bool(getattr(user, "is_real_admin", False)):
            return None

        agent_ids = db.execute(
            select(User.id).where(User.role.in_(["agent", "trainee"]), User.is_active.is_(True))
        ).scalars().all()
        if not agent_ids:
            return {}

        real_admin = CurrentUser(
            id=user.real_user_id,
            role=user.real_role,
            real_user_id=user.real_user_id,
            real_role=user.real_role,
            is_real_admin=True,
        )
        base = ticket_count_stmt([TicketStatus.NEW.value, TicketStatus.PENDING.value], None, count_user=real_admin).subquery()
        rows = db.execute(
            select(
                base.c.assignee_id,
                func.sum(case((base.c.is_frozen.is_(False), 1), else_=0)).label("active"),
                func.sum(case((base.c.is_frozen.is_(True), 1), else_=0)).label("frozen"),
            )
            .where(base.c.assignee_id.in_(agent_ids))
            .group_by(base.c.assignee_id)
        ).all()
        counts = {agent_id: {"active": 0, "frozen": 0} for agent_id in agent_ids}
        for assignee_id, active, frozen in rows:
            counts[assignee_id] = {"active": int(active or 0), "frozen": int(frozen or 0)}
        return counts

    return TicketCountsOut(
        unassigned=count_tickets([TicketStatus.UNASSIGNED.value], False, False) if (is_admin(user) or is_readonly_chiki_piki(user)) else 0,
        active=count_tickets([TicketStatus.NEW.value, TicketStatus.PENDING.value], False, True),
        frozen=count_tickets([TicketStatus.NEW.value, TicketStatus.PENDING.value], True, True),
        closed=count_tickets([TicketStatus.CLOSED.value], None, False),
        special=count_tickets([TicketStatus.NEW.value, TicketStatus.PENDING.value, TicketStatus.UNASSIGNED.value], None, False, True) if is_admin(user) else 0,
        by_assignee=build_by_assignee(),
    )
