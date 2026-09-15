import html
import uuid
from datetime import datetime
from typing import Callable

from sqlalchemy import and_, asc, func, or_, select
from sqlalchemy.orm import Session

from ..models import MessageType, TelegramContact, Ticket, TicketIntakeState, TicketMessage, TicketStatus, TicketWorkflowType, User
from .automatic_messages import render_automatic_message_for_telegram
from .employee_telegram_notifications import schedule_assignment_notification
from .statistics_lifecycle import sync_work_interval_after_assignment_change
from .telegram_bot import TelegramApiError, send_message

GroupParentAssignedHook = Callable[[Session, Ticket, User], None]
_group_parent_assigned_hook: GroupParentAssignedHook | None = None


def set_group_parent_assigned_hook(hook: GroupParentAssignedHook | None) -> None:
    global _group_parent_assigned_hook
    _group_parent_assigned_hook = hook


def run_group_parent_assigned_hook(db: Session, ticket: Ticket, agent: User) -> None:
    if _group_parent_assigned_hook is not None:
        _group_parent_assigned_hook(db, ticket, agent)


def append_system_message(db: Session, ticket: Ticket, body: str, *, agent_visible: bool = False):
    db.add(
        TicketMessage(
            id=str(uuid.uuid4()),
            ticket_id=ticket.id,
            message_type=MessageType.SYSTEM.value,
            author_user_id=None,
            body=body,
            agent_visible=agent_visible,
            created_at=datetime.utcnow(),
        )
    )
    ticket.updated_at = datetime.utcnow()


def assignee_label(user: User | None) -> str:
    return user.full_name if user is not None and user.full_name else "нераспределён"


def assignee_change_message(old_assignee: User | None, new_assignee: User | None) -> str:
    return f"Ответственный изменён: {assignee_label(old_assignee)} -> {assignee_label(new_assignee)}"


def telegram_contact_for_ticket(db: Session, ticket: Ticket) -> TelegramContact | None:
    return db.execute(select(TelegramContact).where(TelegramContact.client_id == ticket.client_id)).scalar_one_or_none()


def notify_client_about_assignee(db: Session, ticket: Ticket, agent: User):
    contact = telegram_contact_for_ticket(db, ticket)
    if contact is None:
        return

    agent_name = html.escape(agent.full_name or "сотрудник")
    try:
        text, parse_mode = render_automatic_message_for_telegram(
            db,
            "telegram.assignment.agent_assigned",
            f"Вам ответит <b>{agent_name}</b>",
            ticket=ticket,
            contact=contact,
            agent=agent,
            extra={"agent_full_name": agent.full_name or "сотрудник"},
        )
        send_message(contact.external_chat_id, text, parse_mode=parse_mode)
    except TelegramApiError:
        pass


def schedule_employee_assignment_notification(
    db: Session,
    ticket: Ticket,
    *,
    old_assignee: User | None,
    new_assignee: User | None,
    old_is_special: bool = False,
    new_is_special: bool = False,
    actor_user_id: str | None = None,
) -> None:
    schedule_assignment_notification(
        db,
        old_assignee_id=old_assignee.id if old_assignee is not None else None,
        new_assignee_id=new_assignee.id if new_assignee is not None else None,
        ticket=ticket,
        old_is_special=old_is_special,
        new_is_special=new_is_special,
        actor_user_id=actor_user_id,
    )


def agent_active_load(db: Session, agent: User) -> int:
    return db.execute(
        select(func.count())
        .select_from(Ticket)
        .where(
            and_(
                Ticket.assignee_id == agent.id,
                Ticket.status.in_([TicketStatus.NEW.value, TicketStatus.PENDING.value]),
                Ticket.is_frozen.is_(False),
                Ticket.load_counted.is_(True),
            )
        )
    ).scalar_one()


def choose_online_agent(db: Session, company_id: str) -> User | None:
    agents = db.execute(
        select(User)
        .where(
            and_(
                User.company_id == company_id,
                User.role == "agent",
                User.is_active.is_(True),
                User.availability_status == "online",
            )
        )
    ).scalars().all()
    if not agents:
        return None

    return min(
        agents,
        key=lambda agent: (
            agent_active_load(db, agent),
            agent.last_assigned_at or datetime.min,
            agent.full_name,
        ),
    )


def assign_ticket_to_agent(db: Session, ticket: Ticket, agent: User, notify_client: bool = True) -> bool:
    if ticket.status != TicketStatus.UNASSIGNED.value:
        return False
    if ticket.intake_state != TicketIntakeState.READY.value:
        return False
    if ticket.workflow_type == TicketWorkflowType.AUTO.value:
        return False
    if agent.role != "agent" or not agent.is_active or agent.availability_status != "online":
        return False

    old_assignee_id = ticket.assignee_id
    old_assignee = db.get(User, ticket.assignee_id) if ticket.assignee_id else None
    now = datetime.utcnow()
    ticket.assignee_id = agent.id
    ticket.status = TicketStatus.NEW.value
    ticket.updated_at = now
    agent.last_assigned_at = now
    sync_work_interval_after_assignment_change(db, ticket, old_assignee_id=old_assignee_id, new_assignee_id=agent.id, now=now)
    append_system_message(db, ticket, assignee_change_message(old_assignee, agent))
    run_group_parent_assigned_hook(db, ticket, agent)
    if notify_client and (ticket.workflow_type or TicketWorkflowType.TICKET.value) == TicketWorkflowType.TICKET.value:
        notify_client_about_assignee(db, ticket, agent)
    schedule_employee_assignment_notification(db, ticket, old_assignee=old_assignee, new_assignee=agent)
    return True


def assign_ticket_by_admin(
    db: Session,
    ticket: Ticket,
    assignee: User,
    *,
    actor_user_id: str | None = None,
    old_is_special: bool | None = None,
    notify_client: bool = True,
) -> None:
    old_assignee = db.get(User, ticket.assignee_id) if ticket.assignee_id else None
    old_status = ticket.status
    previous_is_special = bool(ticket.is_special) if old_is_special is None else old_is_special
    actor_user = db.get(User, actor_user_id) if actor_user_id else None
    actor_name = actor_user.full_name if actor_user and actor_user.full_name else "Администратор"
    now = datetime.utcnow()

    ticket.assignee_id = assignee.id
    ticket.intake_state = TicketIntakeState.READY.value
    if ticket.status == TicketStatus.UNASSIGNED.value:
        ticket.status = TicketStatus.NEW.value
    ticket.updated_at = now
    sync_work_interval_after_assignment_change(
        db,
        ticket,
        old_assignee_id=old_assignee.id if old_assignee else None,
        new_assignee_id=assignee.id,
        now=now,
    )
    append_system_message(db, ticket, assignee_change_message(old_assignee, assignee))
    run_group_parent_assigned_hook(db, ticket, assignee)
    schedule_employee_assignment_notification(
        db,
        ticket,
        old_assignee=old_assignee,
        new_assignee=assignee,
        old_is_special=previous_is_special,
        new_is_special=bool(ticket.is_special),
        actor_user_id=actor_user.id if actor_user is not None else None,
    )
    if actor_user is not None and actor_user.role == "admin":
        ticket_word = "задачу" if (ticket.workflow_type or TicketWorkflowType.TICKET.value) == TicketWorkflowType.TASK.value else "заявку"
        if assignee.id == actor_user.id and old_assignee is not None and old_assignee.id != actor_user.id:
            append_system_message(
                db,
                ticket,
                f"{actor_name} забрал {ticket_word}",
                agent_visible=True,
            )
        if old_assignee is not None and old_assignee.id == actor_user.id and assignee.id != actor_user.id:
            append_system_message(
                db,
                ticket,
                f"{actor_name} перевёл {ticket_word} на Вас",
                agent_visible=True,
            )
    if (
        notify_client
        and ticket.status != TicketStatus.CLOSED.value
        and old_assignee is None
        and old_status == TicketStatus.UNASSIGNED.value
    ):
        notify_client_about_assignee(db, ticket, assignee)


def resolve_forced_assignee(db: Session, forced_assignee_type: str, forced_assignee_value: str) -> tuple[User | None, str | None, User | None]:
    value = (forced_assignee_value or "").strip()
    if not value:
        return None, "not_found", None

    user: User | None = None
    if forced_assignee_type == "login":
        user = db.execute(select(User).where(User.login == value)).scalar_one_or_none()
    elif forced_assignee_type == "user_id":
        user = db.execute(select(User).where(User.id == value)).scalar_one_or_none()
    else:
        return None, "not_found", None

    if user is None:
        return None, "not_found", None
    if not user.is_active:
        return None, "inactive", user
    if user.role not in {"agent", "trainee"}:
        return None, "invalid_role", user
    return user, None, user


def assign_ticket_to_forced_user(
    db: Session,
    ticket: Ticket,
    forced_assignee_type: str,
    forced_assignee_value: str,
) -> tuple[User | None, str | None, User | None]:
    if ticket.workflow_type == TicketWorkflowType.AUTO.value:
        return None, "unsupported", None

    assignee, error, candidate = resolve_forced_assignee(db, forced_assignee_type, forced_assignee_value)
    if assignee is None:
        return None, error or "not_found", candidate

    old_assignee_id = ticket.assignee_id
    now = datetime.utcnow()
    ticket.assignee_id = assignee.id
    ticket.intake_state = TicketIntakeState.READY.value
    if ticket.status != TicketStatus.CLOSED.value:
        ticket.status = TicketStatus.NEW.value
    ticket.updated_at = now
    assignee.last_assigned_at = now
    sync_work_interval_after_assignment_change(db, ticket, old_assignee_id=old_assignee_id, new_assignee_id=assignee.id, now=now)
    append_system_message(
        db,
        ticket,
        f"Заявка назначена на сотрудника из настройки кнопки: {assignee_label(assignee)}",
        agent_visible=True,
    )
    return assignee, None, assignee


def assign_unassigned_to_agent(db: Session, agent: User, notify_client: bool = True) -> int:
    if agent.role != "agent" or not agent.is_active or agent.availability_status != "online":
        return 0

    tickets = db.execute(
        select(Ticket)
        .where(
            and_(
                Ticket.company_id == agent.company_id,
                Ticket.status == TicketStatus.UNASSIGNED.value,
                Ticket.intake_state == TicketIntakeState.READY.value,
                Ticket.workflow_type != TicketWorkflowType.AUTO.value,
                or_(
                    Ticket.agent_visible.is_(True),
                    Ticket.task_kind == "group_parent",
                ),
                Ticket.assignee_id.is_(None),
                Ticket.is_frozen.is_(False),
            )
        )
        .order_by(asc(Ticket.created_at))
    ).scalars().all()

    assigned = 0
    for ticket in tickets:
        if assign_ticket_to_agent(db, ticket, agent, notify_client=notify_client):
            assigned += 1
    return assigned


def assign_unassigned_fairly(db: Session, company_id: str, notify_client: bool = True) -> int:
    tickets = db.execute(
        select(Ticket)
        .where(
            and_(
                Ticket.company_id == company_id,
                Ticket.status == TicketStatus.UNASSIGNED.value,
                Ticket.intake_state == TicketIntakeState.READY.value,
                Ticket.workflow_type != TicketWorkflowType.AUTO.value,
                or_(
                    Ticket.agent_visible.is_(True),
                    Ticket.task_kind == "group_parent",
                ),
                Ticket.assignee_id.is_(None),
                Ticket.is_frozen.is_(False),
            )
        )
        .order_by(asc(Ticket.created_at))
    ).scalars().all()

    assigned = 0
    for ticket in tickets:
        agent = choose_online_agent(db, company_id)
        if agent is None:
            break
        if assign_ticket_to_agent(db, ticket, agent, notify_client=notify_client):
            assigned += 1
    return assigned
