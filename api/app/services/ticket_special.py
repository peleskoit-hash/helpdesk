from __future__ import annotations

from datetime import datetime
from typing import Callable

from sqlalchemy.orm import Session

from ..models import Ticket, TicketIntakeState, TicketStatus, TicketWorkflowType, User


def _ticket_word(ticket: Ticket) -> str:
    return "задачу" if (ticket.workflow_type or TicketWorkflowType.TICKET.value) == TicketWorkflowType.TASK.value else "заявку"


def _display_assignee_name(ticket: Ticket, assignee: User | None, special_assignee: User | None) -> str | None:
    return (
        special_assignee.full_name
        if ticket.is_special and special_assignee and special_assignee.full_name
        else (assignee.full_name if assignee else None)
    )


def _apply_move_to_special(
    db: Session,
    ticket: Ticket,
    admin_user: User,
    old_assignee: User | None,
    *,
    append_system_message_fn: Callable,
    clear_auto_close_timer_fn: Callable,
) -> None:
    ticket.is_special = True
    ticket.special_assignee_id = admin_user.id
    ticket.special_at = datetime.utcnow()
    ticket.assignee_id = admin_user.id
    clear_auto_close_timer_fn(ticket)
    ticket.intake_state = TicketIntakeState.READY.value
    if ticket.status in {TicketStatus.UNASSIGNED.value, TicketStatus.NEW.value}:
        ticket.status = TicketStatus.NEW.value
    else:
        ticket.status = TicketStatus.PENDING.value
    ticket.updated_at = datetime.utcnow()
    append_system_message_fn(
        db,
        ticket,
        f"{'Задача' if ticket.workflow_type == TicketWorkflowType.TASK.value else 'Заявка'} переведена в особые. Ответственный администратор: {admin_user.full_name}",
        agent_visible=True,
    )
    if (ticket.workflow_type or TicketWorkflowType.TICKET.value) == TicketWorkflowType.TICKET.value:
        append_system_message_fn(
            db,
            ticket,
            "Обратите внимание, что Вы перенесли ЗАЯВКУ в особый список. Пока она не будет закрыта, клиент не сможет создавать новые заявки.",
            agent_visible=True,
        )
    if old_assignee is not None and old_assignee.id != admin_user.id:
        append_system_message_fn(
            db,
            ticket,
            f"{admin_user.full_name} забрал {_ticket_word(ticket)}",
            agent_visible=True,
        )


def _handle_special_assignment(
    db: Session,
    ticket: Ticket,
    assignee: User,
    old_assignee: User | None,
    old_status: str,
    *,
    append_system_message_fn: Callable,
    assignee_change_message_fn: Callable,
    refresh_auto_close_timer_fn: Callable,
    notify_client_about_assignee_fn: Callable,
    error_fn: Callable,
) -> bool:
    if not ticket.is_special:
        return False

    if assignee.role in {"agent", "trainee"}:
        ticket.is_special = False
        ticket.special_assignee_id = None
        ticket.special_at = None
        ticket.assignee_id = assignee.id
        ticket.intake_state = TicketIntakeState.READY.value
        if ticket.status == TicketStatus.UNASSIGNED.value:
            ticket.status = TicketStatus.NEW.value
        ticket.updated_at = datetime.utcnow()
        append_system_message_fn(db, ticket, assignee_change_message_fn(old_assignee, assignee))
        append_system_message_fn(db, ticket, f"Особая {'задача' if ticket.workflow_type == TicketWorkflowType.TASK.value else 'заявка'} передана сотруднику: {assignee.full_name}", agent_visible=True)
        refresh_auto_close_timer_fn(db, ticket, restart_from_now=True)
        if old_assignee is None and old_status == TicketStatus.UNASSIGNED.value:
            notify_client_about_assignee_fn(db, ticket, assignee)
        return True

    if assignee.role == "admin":
        ticket.assignee_id = assignee.id
        ticket.special_assignee_id = assignee.id
        if ticket.special_at is None:
            ticket.special_at = datetime.utcnow()
        ticket.intake_state = TicketIntakeState.READY.value
        if ticket.status == TicketStatus.UNASSIGNED.value:
            ticket.status = TicketStatus.NEW.value
        ticket.updated_at = datetime.utcnow()
        append_system_message_fn(db, ticket, assignee_change_message_fn(old_assignee, assignee))
        append_system_message_fn(
            db,
            ticket,
            f"Ответственный администратор особой {'задачи' if ticket.workflow_type == TicketWorkflowType.TASK.value else 'заявки'} изменён: {assignee.full_name}",
            agent_visible=True,
        )
        return True

    error_fn(409, "special_invalid_assignee", "У особой заявки ответственный может быть только администратором, сотрудником или стажёром")
    return True
