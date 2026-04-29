import html
import uuid
from datetime import datetime

from sqlalchemy import and_, asc, func, select
from sqlalchemy.orm import Session

from ..models import MessageType, TelegramContact, Ticket, TicketIntakeState, TicketMessage, TicketStatus, TicketWorkflowType, User
from .telegram_bot import TelegramApiError, send_message


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
        send_message(contact.external_chat_id, f"Вам ответит <b>{agent_name}</b>", parse_mode="HTML")
    except TelegramApiError:
        pass


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

    old_assignee = None
    ticket.assignee_id = agent.id
    ticket.status = TicketStatus.NEW.value
    ticket.updated_at = datetime.utcnow()
    agent.last_assigned_at = datetime.utcnow()
    append_system_message(db, ticket, assignee_change_message(old_assignee, agent))
    if notify_client:
        notify_client_about_assignee(db, ticket, agent)
    return True


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
                Ticket.agent_visible.is_(True),
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
                Ticket.agent_visible.is_(True),
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
