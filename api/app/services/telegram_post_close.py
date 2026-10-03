from __future__ import annotations

import json
import uuid
from datetime import UTC, datetime

from sqlalchemy import and_, desc, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from ..models import BotButtonSetting, Client, ClientProfile, TelegramContact, TelegramUpdate, Ticket, TicketFeedback, TicketFeedbackInput, TicketFeedbackState, TicketReview, TicketReviewCycle, TicketReviewCycleState, TicketStatus, TicketTaskKind, TicketWorkflowType, User
from .automatic_messages import render_automatic_message_for_telegram
from .system_tickets import FEEDBACK_SYSTEM_BUTTONS, REVIEW_SYSTEM_BUTTONS
from .telegram_text_format import prepare_telegram_html_message, strip_telegram_html
from .ticket_reviews import (
    cancel_review_cycle,
    clear_review_current_message_id,
    complete_review_cycle,
    create_review_cycle,
    return_review_cycle_to_offered,
    save_review_caption,
    save_review_rating,
    save_review_text,
    set_review_current_message_id,
    start_review_rating_cycle,
)
from .ticket_subject import resolve_ticket_subject


REVIEW_START_PREFIX = "review:start:"
REVIEW_BACK_PREFIX = "review:back:"
REVIEW_RATE_PREFIX = "review:rate:"
REVIEW_FINISH_PREFIX = "review:finish:"
REVIEW_STALE_TEXT = "Это действие больше неактуально"
REVIEW_CLOSE_FALLBACK_TEXT = "Ваше обращение закрыто. Если Вам снова будет необходима помощь технической поддержки, нажмите /start."
REVIEW_RATING_PROMPT_FALLBACK_TEXT = "Оцените, пожалуйста, качество работы сотрудника."
REVIEW_TEXT_PROMPT_FALLBACK_TEXT = "Если хотите, напишите комментарий к вашей оценке."
REVIEW_ATTACHMENT_NO_TEXT_FALLBACK_TEXT = "Вложение не будет добавлено к отзыву. Отправьте текст сообщения или добавьте подпись к вложению."
REVIEW_ATTACHMENT_CAPTION_FALLBACK_TEXT = "Подпись к вложению сохранена как текст отзыва. Вы можете отправить другой текст или завершить отзыв."
REVIEW_THANK_YOU_FALLBACK_TEXT = "Спасибо за ваш отзыв!"
FEEDBACK_START_PREFIX = "feedback:start:"
FEEDBACK_BACK_PREFIX = "feedback:back:"
FEEDBACK_STALE_TEXT = "Это действие больше неактуально"
FEEDBACK_PROMPT_FALLBACK_TEXT = "Напишите предложение или замечание одним текстовым сообщением."
FEEDBACK_THANK_YOU_FALLBACK_TEXT = "Спасибо! Ваше предложение или замечание сохранено."
FEEDBACK_TEXT_REQUIRED_FALLBACK_TEXT = "Пожалуйста, отправьте предложение или замечание текстом."
_SYSTEM_BUTTON_DEFS = {item["key"]: item["text"] for item in (*REVIEW_SYSTEM_BUTTONS, *FEEDBACK_SYSTEM_BUTTONS)}


def _system_button(db: Session, key: str, callback_data: str) -> dict:
    default_text = _SYSTEM_BUTTON_DEFS[key]
    row = db.execute(select(BotButtonSetting).where(BotButtonSetting.button_key == key)).scalar_one_or_none()
    text = (row.label_override or "").strip() if row is not None and bool(row.label_overridden) else ""
    button = {"text": text or default_text, "callback_data": callback_data}
    style = str(getattr(row, "button_style", "") or "").strip().lower() if row is not None else ""
    if style in {"primary", "success", "danger"}:
        button["style"] = style
    return button


def review_start_callback_data(cycle_id: str) -> str:
    return f"{REVIEW_START_PREFIX}{cycle_id}"


def review_back_callback_data(cycle_id: str) -> str:
    return f"{REVIEW_BACK_PREFIX}{cycle_id}"


def review_rate_callback_data(cycle_id: str, rating: int) -> str:
    return f"{REVIEW_RATE_PREFIX}{cycle_id}:{rating}"


def review_finish_callback_data(cycle_id: str) -> str:
    return f"{REVIEW_FINISH_PREFIX}{cycle_id}"


def feedback_start_callback_data(ticket_id: str) -> str:
    return f"{FEEDBACK_START_PREFIX}{ticket_id}"


def feedback_back_callback_data(input_id: str) -> str:
    return f"{FEEDBACK_BACK_PREFIX}{input_id}"


def is_review_callback_data(data: str) -> bool:
    return (
        data.startswith(REVIEW_START_PREFIX)
        or data.startswith(REVIEW_BACK_PREFIX)
        or data.startswith(REVIEW_RATE_PREFIX)
        or data.startswith(REVIEW_FINISH_PREFIX)
    )


def is_feedback_callback_data(data: str) -> bool:
    return data.startswith(FEEDBACK_START_PREFIX) or data.startswith(FEEDBACK_BACK_PREFIX)


def feedback_input_markup(input_state: TicketFeedbackInput, db: Session | None = None) -> dict:
    button = {"text": "Назад", "callback_data": feedback_back_callback_data(input_state.id)}
    if db is not None:
        button = _system_button(db, "__system__|feedback|back", feedback_back_callback_data(input_state.id))
    return {"inline_keyboard": [[button]]}


def _feedback_exists(db: Session, ticket_id: str | None) -> bool:
    if not ticket_id:
        return False
    return db.execute(select(TicketFeedback.id).where(TicketFeedback.ticket_id == ticket_id).limit(1)).first() is not None


def _review_completed_for_cycle(db: Session, cycle: TicketReviewCycle | None) -> bool:
    if cycle is None:
        return False
    if cycle.state == TicketReviewCycleState.COMPLETED.value:
        return True
    return db.execute(select(TicketReview.id).where(TicketReview.cycle_id == cycle.id).limit(1)).first() is not None


def _feedback_supported_ticket(ticket: Ticket | None, *, assume_closed: bool = False) -> bool:
    if ticket is None:
        return False
    if not assume_closed and ticket.status != TicketStatus.CLOSED.value:
        return False
    workflow_type = ticket.workflow_type or TicketWorkflowType.TICKET.value
    return workflow_type in {TicketWorkflowType.TICKET.value, TicketWorkflowType.TASK.value}


def available_post_close_actions(
    db: Session,
    *,
    ticket: Ticket | None,
    contact: TelegramContact | None,
    review_cycle: TicketReviewCycle | None = None,
    assume_closed: bool = False,
) -> dict[str, bool]:
    if ticket is None or contact is None or ticket.client_id != contact.client_id:
        return {"review": False, "feedback": False}
    review_available = (
        review_cycle is not None
        and is_ordinary_review_ticket(ticket)
        and (assume_closed or ticket.status == TicketStatus.CLOSED.value)
        and review_cycle.client_id == contact.client_id
        and review_cycle.ticket_id == ticket.id
        and review_cycle.state == TicketReviewCycleState.OFFERED.value
        and not _review_completed_for_cycle(db, review_cycle)
    )
    feedback_available = _feedback_supported_ticket(ticket, assume_closed=assume_closed) and not _feedback_exists(db, ticket.id)
    return {"review": review_available, "feedback": feedback_available}


def post_close_actions_markup(
    db: Session,
    *,
    ticket: Ticket | None,
    contact: TelegramContact | None,
    review_cycle: TicketReviewCycle | None = None,
    assume_closed: bool = False,
) -> dict | None:
    actions = available_post_close_actions(
        db,
        ticket=ticket,
        contact=contact,
        review_cycle=review_cycle,
        assume_closed=assume_closed,
    )
    rows = []
    if actions["review"] and review_cycle is not None:
        rows.append([_system_button(db, "__system__|review|start", review_start_callback_data(review_cycle.id))])
    if actions["feedback"] and ticket is not None:
        rows.append([_system_button(db, "__system__|feedback|start", feedback_start_callback_data(ticket.id))])
    return {"inline_keyboard": rows} if rows else None


def _markup_has_review_action(markup: dict | None) -> bool:
    for row in (markup or {}).get("inline_keyboard") or []:
        for button in row:
            if str(button.get("callback_data") or "").startswith(REVIEW_START_PREFIX):
                return True
    return False


def review_rating_markup(cycle: TicketReviewCycle, db: Session | None = None) -> dict:
    rows = [
        [
            _system_button(db, f"__system__|review|rate_{rating}", review_rate_callback_data(cycle.id, rating))
            if db is not None
            else {"text": "⭐" * rating, "callback_data": review_rate_callback_data(cycle.id, rating)}
        ]
        for rating in range(1, 6)
    ]
    rows.append([
        _system_button(db, "__system__|review|back", review_back_callback_data(cycle.id))
        if db is not None
        else {"text": "Назад", "callback_data": review_back_callback_data(cycle.id)}
    ])
    return {"inline_keyboard": rows}


def review_finish_markup(cycle: TicketReviewCycle, db: Session | None = None) -> dict:
    button = {"text": "Завершить отзыв", "callback_data": review_finish_callback_data(cycle.id)}
    if db is not None:
        button = _system_button(db, "__system__|review|finish", review_finish_callback_data(cycle.id))
    return {"inline_keyboard": [[button]]}


def is_ordinary_review_ticket(ticket: Ticket | None) -> bool:
    if ticket is None:
        return False
    return (ticket.workflow_type or TicketWorkflowType.TICKET.value) == TicketWorkflowType.TICKET.value and (
        ticket.task_kind or TicketTaskKind.NORMAL.value
    ) == TicketTaskKind.NORMAL.value


def valid_review_agent(user: User | None) -> bool:
    return user is not None and user.role in {"agent", "trainee"}


def attributable_review_agent_id_for_actor(db: Session, ticket: Ticket, actor) -> str | None:
    if not is_ordinary_review_ticket(ticket):
        return None
    actor_role = getattr(actor, "role", None)
    actor_id = getattr(actor, "id", None)
    if actor_role in {"agent", "trainee"}:
        actor_user = db.get(User, actor_id) if actor_id else None
        if actor_user is None:
            return None
        return actor_user.id if valid_review_agent(actor_user) else None
    if actor_role == "admin":
        assignee = db.get(User, ticket.assignee_id) if ticket.assignee_id else None
        return assignee.id if valid_review_agent(assignee) else None
    return None


def attributable_review_agent_id_for_client_close(db: Session, ticket: Ticket) -> str | None:
    if not is_ordinary_review_ticket(ticket):
        return None
    assignee = db.get(User, ticket.assignee_id) if ticket.assignee_id else None
    return assignee.id if valid_review_agent(assignee) else None


def _telegram_message_id(response: dict | None) -> str | None:
    message_id = ((response or {}).get("result") or {}).get("message_id")
    return str(message_id) if message_id is not None else None


def prepare_review_offer_cycle(
    db: Session,
    *,
    ticket: Ticket,
    agent_id: str | None,
) -> TicketReviewCycle | None:
    if not agent_id or not is_ordinary_review_ticket(ticket):
        return None
    contact = db.execute(select(TelegramContact).where(TelegramContact.client_id == ticket.client_id)).scalar_one_or_none()
    if contact is None:
        return None
    return create_review_cycle(db, ticket_id=ticket.id, agent_id=agent_id, client_id=ticket.client_id)


def finalize_review_offer_close_message(
    db: Session,
    *,
    cycle: TicketReviewCycle,
    close_response: dict | None,
    telegram_message_id_fn=_telegram_message_id,
) -> TicketReviewCycle:
    message_id = telegram_message_id_fn(close_response)
    if message_id is None:
        cancel_review_cycle(db, cycle=cycle)
        return cycle
    set_review_current_message_id(db, cycle=cycle, message_id=message_id)
    return cycle


def _cycle_from_callback_data(db: Session, data: str) -> tuple[str, TicketReviewCycle | None]:
    if data.startswith(REVIEW_START_PREFIX):
        cycle_id = data[len(REVIEW_START_PREFIX) :]
        return "start", db.get(TicketReviewCycle, cycle_id)
    if data.startswith(REVIEW_BACK_PREFIX):
        cycle_id = data[len(REVIEW_BACK_PREFIX) :]
        return "back", db.get(TicketReviewCycle, cycle_id)
    if data.startswith(REVIEW_RATE_PREFIX):
        rest = data[len(REVIEW_RATE_PREFIX) :]
        cycle_id = rest.rsplit(":", 1)[0]
        return "rate", db.get(TicketReviewCycle, cycle_id)
    if data.startswith(REVIEW_FINISH_PREFIX):
        cycle_id = data[len(REVIEW_FINISH_PREFIX) :]
        return "finish", db.get(TicketReviewCycle, cycle_id)
    return "", None


def _rating_from_callback_data(data: str) -> int | None:
    if not data.startswith(REVIEW_RATE_PREFIX):
        return None
    rest = data[len(REVIEW_RATE_PREFIX) :]
    if ":" not in rest:
        return None
    rating_text = rest.rsplit(":", 1)[1]
    try:
        rating = int(rating_text)
    except ValueError:
        return None
    return rating if 1 <= rating <= 5 else None


def _latest_cycle_id_for_pair(db: Session, cycle: TicketReviewCycle) -> str | None:
    return db.execute(
        select(TicketReviewCycle.id)
        .where(
            and_(
                TicketReviewCycle.ticket_id == cycle.ticket_id,
                TicketReviewCycle.agent_id == cycle.agent_id,
            )
        )
        .order_by(desc(TicketReviewCycle.created_at), desc(TicketReviewCycle.id))
        .limit(1)
    ).scalar_one_or_none()


def _valid_cycle_for_callback(
    db: Session,
    *,
    cycle: TicketReviewCycle | None,
    contact: TelegramContact,
    callback_message_id: int | None,
    expected_state: str,
) -> tuple[bool, Ticket | None]:
    if cycle is None or cycle.client_id != contact.client_id:
        return False, None
    if cycle.state != expected_state or cycle.state in {TicketReviewCycleState.CANCELLED.value, TicketReviewCycleState.COMPLETED.value}:
        return False, None
    if callback_message_id is None or cycle.review_current_message_id != str(callback_message_id):
        return False, None
    if _latest_cycle_id_for_pair(db, cycle) != cycle.id:
        return False, None
    ticket = db.get(Ticket, cycle.ticket_id)
    if not is_ordinary_review_ticket(ticket) or ticket.status != TicketStatus.CLOSED.value:
        return False, ticket
    agent = db.get(User, cycle.agent_id)
    if not valid_review_agent(agent):
        return False, ticket
    return True, ticket


def _active_cycle_context_for_contact(
    db: Session,
    *,
    contact: TelegramContact,
    states: set[str],
) -> tuple[TicketReviewCycle | None, Ticket | None, User | None]:
    cycles = db.execute(
        select(TicketReviewCycle)
        .where(
            and_(
                TicketReviewCycle.client_id == contact.client_id,
                TicketReviewCycle.state.in_(states),
            )
        )
        .order_by(desc(TicketReviewCycle.created_at), desc(TicketReviewCycle.id))
    ).scalars().all()
    for cycle in cycles:
        if _latest_cycle_id_for_pair(db, cycle) != cycle.id:
            continue
        ticket = db.get(Ticket, cycle.ticket_id)
        if not is_ordinary_review_ticket(ticket) or ticket.status != TicketStatus.CLOSED.value:
            continue
        agent = db.get(User, cycle.agent_id)
        if not valid_review_agent(agent):
            continue
        return cycle, ticket, agent
    return None, None, None


def _active_feedback_input_context_for_contact(
    db: Session,
    *,
    contact: TelegramContact,
) -> tuple[TicketFeedbackInput | None, Ticket | None]:
    inputs = db.execute(
        select(TicketFeedbackInput)
        .where(
            and_(
                TicketFeedbackInput.client_id == contact.client_id,
                TicketFeedbackInput.state == TicketFeedbackState.AWAITING_TEXT.value,
            )
        )
        .order_by(desc(TicketFeedbackInput.created_at), desc(TicketFeedbackInput.id))
    ).scalars().all()
    for input_state in inputs:
        ticket = db.get(Ticket, input_state.ticket_id)
        if not _feedback_supported_ticket(ticket):
            continue
        if _feedback_exists(db, input_state.ticket_id):
            continue
        return input_state, ticket
    return None, None


def _safe_delete_exact_message(delete_message_fn, external_chat_id: str, message_id: str | int | None) -> None:
    if message_id is None:
        return
    try:
        delete_message_fn(external_chat_id, int(message_id))
    except Exception:
        pass


def _safe_remove_exact_inline_keyboard(safe_remove_inline_keyboard_fn, external_chat_id: str, message_id: str | int | None) -> None:
    if message_id is None:
        return
    try:
        safe_remove_inline_keyboard_fn(external_chat_id, int(message_id))
    except Exception:
        pass


def _client_name_snapshot(db: Session, contact: TelegramContact) -> str:
    profile = db.execute(select(ClientProfile).where(ClientProfile.client_id == contact.client_id)).scalar_one_or_none()
    if profile is not None:
        profile_name = " ".join(
            part.strip()
            for part in (profile.last_name or "", profile.contact_name or "")
            if part and part.strip()
        )
        if profile_name:
            return profile_name
    client = db.get(Client, contact.client_id)
    client_name = (client.display_name if client is not None else None) or ""
    telegram_name = contact.external_display_name or ""
    return client_name.strip() or telegram_name.strip() or "Клиент"


def create_feedback_input(db: Session, *, ticket: Ticket, contact: TelegramContact) -> TicketFeedbackInput:
    existing, existing_ticket = _active_feedback_input_context_for_contact(db, contact=contact)
    if existing is not None and existing_ticket is not None and existing.ticket_id == ticket.id:
        return existing
    if existing is not None:
        existing.state = TicketFeedbackState.CANCELLED.value
        existing.cancelled_at = _now_utc()
        existing.current_message_id = None
        existing.updated_at = _now_utc()
    now = _now_utc()
    input_state = TicketFeedbackInput(
        id=str(uuid.uuid4()),
        ticket_id=ticket.id,
        client_id=contact.client_id,
        state=TicketFeedbackState.AWAITING_TEXT.value,
        created_at=now,
        updated_at=now,
    )
    db.add(input_state)
    db.flush()
    return input_state


def save_ticket_feedback(
    db: Session,
    *,
    ticket: Ticket,
    contact: TelegramContact,
    text: str,
    submitted_at: datetime | None = None,
) -> TicketFeedback | None:
    if _feedback_exists(db, ticket.id):
        return db.execute(select(TicketFeedback).where(TicketFeedback.ticket_id == ticket.id)).scalar_one_or_none()
    now = submitted_at or _now_utc()
    try:
        with db.begin_nested():
            feedback = TicketFeedback(
                id=str(uuid.uuid4()),
                ticket_id=ticket.id,
                client_id=contact.client_id,
                client_name_snapshot=_client_name_snapshot(db, contact),
                ticket_title_snapshot=resolve_ticket_subject(db, ticket),
                text=text,
                submitted_at=now,
                created_at=now,
                updated_at=now,
            )
            db.add(feedback)
            db.flush()
    except IntegrityError:
        return db.execute(select(TicketFeedback).where(TicketFeedback.ticket_id == ticket.id)).scalar_one_or_none()
    return feedback


def _now_utc() -> datetime:
    return datetime.now(UTC)


def _agent_name_snapshot(agent: User | None) -> str:
    return ((agent.full_name if agent is not None else None) or "Сотрудник").strip()


def _send_review_message(send_message_fn, external_chat_id: str, text: str, parse_mode: str | None, **kwargs) -> str | None:
    response = send_message_fn(external_chat_id, text, parse_mode=parse_mode, **kwargs)
    return _telegram_message_id(response)


def _render_post_close_message_for_back(
    db: Session,
    *,
    ticket: Ticket,
    ticket_closed_client_text: str,
) -> tuple[str, str | None]:
    if (ticket.workflow_type or TicketWorkflowType.TICKET.value) == TicketWorkflowType.TASK.value:
        from .ticket_lifecycle import format_task_post_close_text

        text = format_task_post_close_text(db, ticket)
    else:
        text = ticket_closed_client_text
    try:
        return prepare_telegram_html_message(text)
    except Exception:
        return strip_telegram_html(text), None


def _render_review_message(
    db: Session,
    key: str,
    fallback: str,
    *,
    ticket: Ticket | None,
    contact: TelegramContact,
    agent: User | None = None,
) -> tuple[str, str | None]:
    return render_automatic_message_for_telegram(db, key, fallback, ticket=ticket, contact=contact, agent=agent)


def _handle_stale_callback(
    *,
    external_chat_id: str,
    callback_id: str,
    callback_message_id: int | None,
    notify_client: bool,
    answer_callback_query_fn,
    safe_remove_inline_keyboard_fn,
) -> None:
    if notify_client and callback_id:
        answer_callback_query_fn(callback_id, REVIEW_STALE_TEXT)
    if callback_message_id is not None:
        _safe_remove_exact_inline_keyboard(safe_remove_inline_keyboard_fn, external_chat_id, callback_message_id)


def _complete_review_with_thank_you(
    db: Session,
    *,
    cycle: TicketReviewCycle,
    ticket: Ticket | None,
    contact: TelegramContact,
    agent: User | None,
    external_chat_id: str,
    send_message_fn,
    delete_message_fn,
) -> bool:
    old_message_id = cycle.review_current_message_id
    thank_you_text, thank_you_parse_mode = _render_review_message(
        db,
        "telegram.review.thank_you",
        REVIEW_THANK_YOU_FALLBACK_TEXT,
        ticket=ticket,
        contact=contact,
        agent=agent,
    )
    thank_you_markup = post_close_actions_markup(db, ticket=ticket, contact=contact, review_cycle=None)
    try:
        thank_you_message_id = _send_review_message(
            send_message_fn,
            external_chat_id,
            thank_you_text,
            thank_you_parse_mode,
            reply_markup=thank_you_markup,
            remove_keyboard=thank_you_markup is None,
        )
    except Exception:
        thank_you_message_id = None
    if thank_you_message_id is None:
        _safe_delete_exact_message(delete_message_fn, external_chat_id, old_message_id)
        complete_review_cycle(db, cycle=cycle, preserve_current_message_id=False)
        return False

    set_review_current_message_id(db, cycle=cycle, message_id=thank_you_message_id)
    _safe_delete_exact_message(delete_message_fn, external_chat_id, old_message_id)
    complete_review_cycle(db, cycle=cycle, preserve_current_message_id=True)
    return True


def cleanup_review_flow_for_contact(
    db: Session,
    *,
    contact: TelegramContact,
    external_chat_id: str,
    delete_message_fn,
    reason: str,
) -> TicketReviewCycle | None:
    cycle, _ticket, _agent = _active_cycle_context_for_contact(
        db,
        contact=contact,
        states={
            TicketReviewCycleState.OFFERED.value,
            TicketReviewCycleState.AWAITING_RATING.value,
            TicketReviewCycleState.AWAITING_TEXT.value,
            TicketReviewCycleState.AWAITING_TEXT_AFTER_CAPTION.value,
            TicketReviewCycleState.COMPLETED.value,
        },
    )
    if cycle is None:
        return None

    _safe_delete_exact_message(delete_message_fn, external_chat_id, cycle.review_current_message_id)
    if cycle.state == TicketReviewCycleState.COMPLETED.value:
        clear_review_current_message_id(db, cycle=cycle)
    elif reason == "start" and cycle.state in {
        TicketReviewCycleState.AWAITING_TEXT.value,
        TicketReviewCycleState.AWAITING_TEXT_AFTER_CAPTION.value,
    }:
        complete_review_cycle(db, cycle=cycle, preserve_current_message_id=False)
    else:
        cancel_review_cycle(db, cycle=cycle)
    return cycle


def cleanup_feedback_flow_for_contact(
    db: Session,
    *,
    contact: TelegramContact,
    external_chat_id: str,
    delete_message_fn,
) -> TicketFeedbackInput | None:
    input_state, _ticket = _active_feedback_input_context_for_contact(db, contact=contact)
    if input_state is None:
        return None
    _safe_delete_exact_message(delete_message_fn, external_chat_id, input_state.current_message_id)
    input_state.state = TicketFeedbackState.CANCELLED.value
    input_state.cancelled_at = _now_utc()
    input_state.current_message_id = None
    input_state.updated_at = _now_utc()
    db.flush()
    return input_state


def cleanup_review_flow_for_ticket_reopen(
    db: Session,
    *,
    ticket: Ticket,
    contact: TelegramContact | None,
    delete_message_fn,
) -> TicketReviewCycle | None:
    if not is_ordinary_review_ticket(ticket):
        return None
    cycle = db.execute(
        select(TicketReviewCycle)
        .where(
            and_(
                TicketReviewCycle.ticket_id == ticket.id,
                TicketReviewCycle.client_id == ticket.client_id,
                TicketReviewCycle.state.in_(
                    [
                        TicketReviewCycleState.OFFERED.value,
                        TicketReviewCycleState.AWAITING_RATING.value,
                        TicketReviewCycleState.AWAITING_TEXT.value,
                        TicketReviewCycleState.AWAITING_TEXT_AFTER_CAPTION.value,
                        TicketReviewCycleState.COMPLETED.value,
                    ]
                ),
            )
        )
        .order_by(desc(TicketReviewCycle.created_at), desc(TicketReviewCycle.id))
        .limit(1)
    ).scalar_one_or_none()
    if cycle is None:
        return None

    if contact is not None:
        _safe_delete_exact_message(delete_message_fn, contact.external_chat_id, cycle.review_current_message_id)
    if cycle.state == TicketReviewCycleState.COMPLETED.value:
        clear_review_current_message_id(db, cycle=cycle)
    else:
        cancel_review_cycle(db, cycle=cycle)
    return cycle


def cleanup_feedback_flow_for_ticket_reopen(
    db: Session,
    *,
    ticket: Ticket,
    contact: TelegramContact | None,
    delete_message_fn,
) -> TicketFeedbackInput | None:
    input_state = db.execute(
        select(TicketFeedbackInput)
        .where(
            and_(
                TicketFeedbackInput.ticket_id == ticket.id,
                TicketFeedbackInput.client_id == ticket.client_id,
                TicketFeedbackInput.state == TicketFeedbackState.AWAITING_TEXT.value,
            )
        )
        .order_by(desc(TicketFeedbackInput.created_at), desc(TicketFeedbackInput.id))
        .limit(1)
    ).scalar_one_or_none()
    if input_state is None:
        return None
    if contact is not None:
        _safe_delete_exact_message(delete_message_fn, contact.external_chat_id, input_state.current_message_id)
    input_state.state = TicketFeedbackState.CANCELLED.value
    input_state.cancelled_at = _now_utc()
    input_state.current_message_id = None
    input_state.updated_at = _now_utc()
    db.flush()
    return input_state


def process_feedback_incoming_message(
    db: Session,
    *,
    contact: TelegramContact,
    external_chat_id: str,
    text: str,
    has_attachment: bool,
    notify_client: bool,
    delete_message_fn,
    send_message_fn,
) -> dict | None:
    input_state, ticket = _active_feedback_input_context_for_contact(db, contact=contact)
    if input_state is None or ticket is None:
        return None

    old_message_id = input_state.current_message_id
    if has_attachment or not text.strip():
        hint_text, hint_parse_mode = _render_review_message(
            db,
            "telegram.feedback.text_required",
            FEEDBACK_TEXT_REQUIRED_FALLBACK_TEXT,
            ticket=ticket,
            contact=contact,
        )
        if notify_client:
            try:
                new_message_id = _send_review_message(
                    send_message_fn,
                    external_chat_id,
                    hint_text,
                    hint_parse_mode,
                    reply_markup=feedback_input_markup(input_state, db),
                )
            except Exception:
                new_message_id = None
            if new_message_id is not None:
                input_state.current_message_id = new_message_id
                input_state.updated_at = _now_utc()
                _safe_delete_exact_message(delete_message_fn, external_chat_id, old_message_id)
        db.commit()
        return {"ok": True, "feedback": "text_required", "input_id": input_state.id}

    feedback = save_ticket_feedback(db, ticket=ticket, contact=contact, text=text.strip())
    input_state.state = TicketFeedbackState.COMPLETED.value
    input_state.completed_at = _now_utc()
    input_state.current_message_id = None
    input_state.updated_at = _now_utc()

    if notify_client:
        thank_you_text, thank_you_parse_mode = _render_review_message(
            db,
            "telegram.feedback.thank_you",
            FEEDBACK_THANK_YOU_FALLBACK_TEXT,
            ticket=ticket,
            contact=contact,
        )
        review_cycle = db.execute(
            select(TicketReviewCycle)
            .where(
                and_(
                    TicketReviewCycle.ticket_id == ticket.id,
                    TicketReviewCycle.client_id == contact.client_id,
                    TicketReviewCycle.state == TicketReviewCycleState.OFFERED.value,
                )
            )
            .order_by(desc(TicketReviewCycle.created_at), desc(TicketReviewCycle.id))
            .limit(1)
        ).scalar_one_or_none()
        thank_you_markup = post_close_actions_markup(db, ticket=ticket, contact=contact, review_cycle=review_cycle)
        thank_you_message_id = None
        try:
            thank_you_message_id = _send_review_message(
                send_message_fn,
                external_chat_id,
                thank_you_text,
                thank_you_parse_mode,
                reply_markup=thank_you_markup,
                remove_keyboard=thank_you_markup is None,
            )
        except Exception:
            pass
        if review_cycle is not None and thank_you_message_id is not None and _markup_has_review_action(thank_you_markup):
            set_review_current_message_id(db, cycle=review_cycle, message_id=thank_you_message_id)
        _safe_delete_exact_message(delete_message_fn, external_chat_id, old_message_id)
    db.commit()
    return {"ok": True, "feedback": "completed", "feedback_id": feedback.id if feedback is not None else None}


def process_review_incoming_message(
    db: Session,
    *,
    contact: TelegramContact,
    external_chat_id: str,
    text: str,
    has_attachment: bool,
    notify_client: bool,
    delete_message_fn,
    send_message_fn,
) -> dict | None:
    cycle, ticket, agent = _active_cycle_context_for_contact(
        db,
        contact=contact,
        states={
            TicketReviewCycleState.AWAITING_TEXT.value,
            TicketReviewCycleState.AWAITING_TEXT_AFTER_CAPTION.value,
        },
    )
    if cycle is None:
        return None

    old_message_id = cycle.review_current_message_id
    if has_attachment:
        if text:
            save_review_caption(db, cycle=cycle, caption=text)
            prompt_key = "telegram.review.attachment_caption"
            fallback = REVIEW_ATTACHMENT_CAPTION_FALLBACK_TEXT
            markup = review_finish_markup(cycle, db)
            result_name = "caption_saved"
        else:
            prompt_key = "telegram.review.attachment_no_text"
            fallback = REVIEW_ATTACHMENT_NO_TEXT_FALLBACK_TEXT
            review = db.get(TicketReview, cycle.review_id) if cycle.review_id else None
            markup = review_finish_markup(cycle, db) if cycle.state == TicketReviewCycleState.AWAITING_TEXT_AFTER_CAPTION.value and review and review.review_text else None
            result_name = "attachment_without_caption"

        warning_text, warning_parse_mode = _render_review_message(
            db,
            prompt_key,
            fallback,
            ticket=ticket,
            contact=contact,
            agent=agent,
        )
        if notify_client:
            try:
                new_message_id = _send_review_message(
                    send_message_fn,
                    external_chat_id,
                    warning_text,
                    warning_parse_mode,
                    reply_markup=markup,
                )
            except Exception:
                new_message_id = None
            if new_message_id is not None:
                set_review_current_message_id(db, cycle=cycle, message_id=new_message_id)
                _safe_delete_exact_message(delete_message_fn, external_chat_id, old_message_id)
            elif text:
                _safe_delete_exact_message(delete_message_fn, external_chat_id, old_message_id)
                complete_review_cycle(db, cycle=cycle, preserve_current_message_id=False)
        db.commit()
        return {"ok": True, "review": result_name, "cycle_id": cycle.id}

    if not text:
        return None

    save_review_text(db, cycle=cycle, review_text=text)
    sent_thank_you = False
    if notify_client:
        sent_thank_you = _complete_review_with_thank_you(
            db,
            cycle=cycle,
            ticket=ticket,
            contact=contact,
            agent=agent,
            external_chat_id=external_chat_id,
            send_message_fn=send_message_fn,
            delete_message_fn=delete_message_fn,
        )
    else:
        complete_review_cycle(db, cycle=cycle, preserve_current_message_id=False)
    db.commit()
    return {"ok": True, "review": "completed", "cycle_id": cycle.id, "thank_you_sent": sent_thank_you}


def _feedback_ticket_id_from_callback_data(data: str) -> str | None:
    if data.startswith(FEEDBACK_START_PREFIX):
        return data[len(FEEDBACK_START_PREFIX) :]
    return None


def _feedback_input_id_from_callback_data(data: str) -> str | None:
    if data.startswith(FEEDBACK_BACK_PREFIX):
        return data[len(FEEDBACK_BACK_PREFIX) :]
    return None


def _latest_review_offer_for_ticket(db: Session, *, ticket: Ticket, contact: TelegramContact) -> TicketReviewCycle | None:
    return db.execute(
        select(TicketReviewCycle)
        .where(
            and_(
                TicketReviewCycle.ticket_id == ticket.id,
                TicketReviewCycle.client_id == contact.client_id,
                TicketReviewCycle.state == TicketReviewCycleState.OFFERED.value,
            )
        )
        .order_by(desc(TicketReviewCycle.created_at), desc(TicketReviewCycle.id))
        .limit(1)
    ).scalar_one_or_none()


def process_feedback_callback(
    db: Session,
    payload: dict,
    callback: dict,
    notify_client: bool,
    *,
    first_company_fn,
    get_or_create_contact_from_user_fn,
    answer_callback_query_fn,
    safe_remove_inline_keyboard_fn,
    delete_message_fn,
    send_message_fn,
    ticket_closed_client_text: str,
) -> dict:
    message = callback.get("message") or {}
    chat = message.get("chat") or {}
    from_user = callback.get("from") or {}
    external_chat_id = str(chat.get("id", ""))
    callback_id = str(callback.get("id", ""))
    callback_message_id = message.get("message_id") if isinstance(message.get("message_id"), int) else None
    data = str(callback.get("data") or "")

    db.add(
        TelegramUpdate(
            id=str(uuid.uuid4()),
            update_id=str(payload.get("update_id")),
            external_chat_id=external_chat_id or None,
            payload_json=json.dumps(payload, ensure_ascii=False),
            created_at=datetime.utcnow(),
        )
    )

    company = first_company_fn(db)
    if company is None or not external_chat_id:
        db.commit()
        return {"ok": True, "ignored": "invalid_feedback_callback"}

    contact = get_or_create_contact_from_user_fn(db, company.id, external_chat_id, from_user)

    if data.startswith(FEEDBACK_START_PREFIX):
        ticket_id = _feedback_ticket_id_from_callback_data(data)
        ticket = db.get(Ticket, ticket_id) if ticket_id else None
        active_review, _active_review_ticket, _active_review_agent = _active_cycle_context_for_contact(
            db,
            contact=contact,
            states={
                TicketReviewCycleState.AWAITING_RATING.value,
                TicketReviewCycleState.AWAITING_TEXT.value,
                TicketReviewCycleState.AWAITING_TEXT_AFTER_CAPTION.value,
            },
        )
        if ticket is None or not _feedback_supported_ticket(ticket) or ticket.client_id != contact.client_id or _feedback_exists(db, ticket.id):
            db.commit()
            _handle_stale_callback(
                external_chat_id=external_chat_id,
                callback_id=callback_id,
                callback_message_id=callback_message_id,
                notify_client=notify_client,
                answer_callback_query_fn=answer_callback_query_fn,
                safe_remove_inline_keyboard_fn=safe_remove_inline_keyboard_fn,
            )
            return {"ok": True, "feedback": "stale"}
        if active_review is not None:
            db.commit()
            _handle_stale_callback(
                external_chat_id=external_chat_id,
                callback_id=callback_id,
                callback_message_id=callback_message_id,
                notify_client=notify_client,
                answer_callback_query_fn=answer_callback_query_fn,
                safe_remove_inline_keyboard_fn=safe_remove_inline_keyboard_fn,
            )
            return {"ok": True, "feedback": "stale"}
        if notify_client and callback_id:
            answer_callback_query_fn(callback_id)
        input_state = create_feedback_input(db, ticket=ticket, contact=contact)
        prompt_text, prompt_parse_mode = _render_review_message(
            db,
            "telegram.feedback.prompt",
            FEEDBACK_PROMPT_FALLBACK_TEXT,
            ticket=ticket,
            contact=contact,
        )
        old_message_id = callback_message_id
        try:
            new_message_id = _send_review_message(
                send_message_fn,
                external_chat_id,
                prompt_text,
                prompt_parse_mode,
                reply_markup=feedback_input_markup(input_state, db),
            )
        except Exception:
            new_message_id = None
        if new_message_id is None:
            input_state.state = TicketFeedbackState.CANCELLED.value
            input_state.cancelled_at = _now_utc()
            input_state.current_message_id = None
            input_state.updated_at = _now_utc()
            db.commit()
            return {"ok": True, "feedback": "prompt_failed"}
        input_state.current_message_id = new_message_id
        input_state.updated_at = _now_utc()
        if old_message_id is not None:
            _safe_delete_exact_message(delete_message_fn, external_chat_id, old_message_id)
        db.commit()
        return {"ok": True, "feedback": "awaiting_text", "input_id": input_state.id}

    input_id = _feedback_input_id_from_callback_data(data)
    input_state = db.get(TicketFeedbackInput, input_id) if input_id else None
    ticket = db.get(Ticket, input_state.ticket_id) if input_state is not None else None
    if (
        input_state is None
        or ticket is None
        or input_state.client_id != contact.client_id
        or input_state.state != TicketFeedbackState.AWAITING_TEXT.value
        or callback_message_id is None
        or input_state.current_message_id != str(callback_message_id)
    ):
        db.commit()
        _handle_stale_callback(
            external_chat_id=external_chat_id,
            callback_id=callback_id,
            callback_message_id=callback_message_id,
            notify_client=notify_client,
            answer_callback_query_fn=answer_callback_query_fn,
            safe_remove_inline_keyboard_fn=safe_remove_inline_keyboard_fn,
        )
        return {"ok": True, "feedback": "stale"}

    old_message_id = input_state.current_message_id
    review_cycle = _latest_review_offer_for_ticket(db, ticket=ticket, contact=contact)
    markup = post_close_actions_markup(db, ticket=ticket, contact=contact, review_cycle=review_cycle)
    post_close_text, post_close_parse_mode = _render_post_close_message_for_back(
        db,
        ticket=ticket,
        ticket_closed_client_text=ticket_closed_client_text,
    )
    try:
        new_message_id = _send_review_message(
            send_message_fn,
            external_chat_id,
            post_close_text,
            post_close_parse_mode,
            reply_markup=markup,
            remove_keyboard=markup is None,
        )
    except Exception:
        new_message_id = None
    if new_message_id is None:
        db.commit()
        return {"ok": True, "feedback": "post_close_failed", "input_id": input_state.id}
    if notify_client and callback_id:
        answer_callback_query_fn(callback_id)
    input_state.state = TicketFeedbackState.CANCELLED.value
    input_state.cancelled_at = _now_utc()
    input_state.current_message_id = None
    input_state.updated_at = _now_utc()
    if review_cycle is not None and _markup_has_review_action(markup):
        set_review_current_message_id(db, cycle=review_cycle, message_id=new_message_id)
    _safe_delete_exact_message(delete_message_fn, external_chat_id, old_message_id)
    db.commit()
    return {"ok": True, "feedback": "cancelled", "input_id": input_state.id}


def process_review_callback(
    db: Session,
    payload: dict,
    callback: dict,
    notify_client: bool,
    *,
    first_company_fn,
    get_or_create_contact_from_user_fn,
    answer_callback_query_fn,
    safe_remove_inline_keyboard_fn,
    delete_message_fn,
    send_message_fn,
    edit_message_reply_markup_fn,
    ticket_closed_client_text: str,
) -> dict:
    message = callback.get("message") or {}
    chat = message.get("chat") or {}
    from_user = callback.get("from") or {}
    external_chat_id = str(chat.get("id", ""))
    callback_id = str(callback.get("id", ""))
    callback_message_id = message.get("message_id") if isinstance(message.get("message_id"), int) else None
    data = str(callback.get("data") or "")

    db.add(
        TelegramUpdate(
            id=str(uuid.uuid4()),
            update_id=str(payload.get("update_id")),
            external_chat_id=external_chat_id or None,
            payload_json=json.dumps(payload, ensure_ascii=False),
            created_at=datetime.utcnow(),
        )
    )

    company = first_company_fn(db)
    if company is None or not external_chat_id:
        db.commit()
        return {"ok": True, "ignored": "invalid_review_callback"}

    contact = get_or_create_contact_from_user_fn(db, company.id, external_chat_id, from_user)
    action, cycle = _cycle_from_callback_data(db, data)

    expected_by_action = {
        "start": TicketReviewCycleState.OFFERED.value,
        "back": TicketReviewCycleState.AWAITING_RATING.value,
        "rate": TicketReviewCycleState.AWAITING_RATING.value,
        "finish": TicketReviewCycleState.AWAITING_TEXT_AFTER_CAPTION.value,
    }
    expected_state = expected_by_action.get(action, "")
    is_valid, ticket = _valid_cycle_for_callback(
        db,
        cycle=cycle,
        contact=contact,
        callback_message_id=callback_message_id,
        expected_state=expected_state,
    )
    if not is_valid or cycle is None:
        db.commit()
        _handle_stale_callback(
            external_chat_id=external_chat_id,
            callback_id=callback_id,
            callback_message_id=callback_message_id,
            notify_client=notify_client,
            answer_callback_query_fn=answer_callback_query_fn,
            safe_remove_inline_keyboard_fn=safe_remove_inline_keyboard_fn,
        )
        return {"ok": True, "review": "stale"}

    if action == "start":
        active_feedback, _active_feedback_ticket = _active_feedback_input_context_for_contact(db, contact=contact)
        if active_feedback is not None:
            db.commit()
            _handle_stale_callback(
                external_chat_id=external_chat_id,
                callback_id=callback_id,
                callback_message_id=callback_message_id,
                notify_client=notify_client,
                answer_callback_query_fn=answer_callback_query_fn,
                safe_remove_inline_keyboard_fn=safe_remove_inline_keyboard_fn,
            )
            return {"ok": True, "review": "stale"}
        if notify_client and callback_id:
            answer_callback_query_fn(callback_id)
        old_message_id = cycle.review_current_message_id
        rating_text, rating_parse_mode = render_automatic_message_for_telegram(
            db,
            "telegram.review.rating_prompt",
            REVIEW_RATING_PROMPT_FALLBACK_TEXT,
            ticket=ticket,
            contact=contact,
            agent=db.get(User, cycle.agent_id),
        )
        try:
            new_message_id = _send_review_message(
                send_message_fn,
                external_chat_id,
                rating_text,
                rating_parse_mode,
                reply_markup=review_rating_markup(cycle, db),
            )
        except Exception:
            new_message_id = None
        if new_message_id is None:
            db.commit()
            return {"ok": True, "review": "rating_prompt_failed"}
        start_review_rating_cycle(db, cycle=cycle)
        set_review_current_message_id(db, cycle=cycle, message_id=new_message_id)
        _safe_delete_exact_message(delete_message_fn, external_chat_id, old_message_id)
        db.commit()
        return {"ok": True, "review": "awaiting_rating", "cycle_id": cycle.id}

    if action == "back":
        if cycle.review_id or db.execute(select(TicketReview.id).where(TicketReview.cycle_id == cycle.id).limit(1)).first() is not None:
            db.commit()
            _handle_stale_callback(
                external_chat_id=external_chat_id,
                callback_id=callback_id,
                callback_message_id=callback_message_id,
                notify_client=notify_client,
                answer_callback_query_fn=answer_callback_query_fn,
                safe_remove_inline_keyboard_fn=safe_remove_inline_keyboard_fn,
            )
            return {"ok": True, "review": "stale"}
        if notify_client and callback_id:
            answer_callback_query_fn(callback_id)
        old_message_id = cycle.review_current_message_id
        return_review_cycle_to_offered(db, cycle=cycle)
        markup = post_close_actions_markup(db, ticket=ticket, contact=contact, review_cycle=cycle)
        post_close_text, post_close_parse_mode = _render_post_close_message_for_back(
            db,
            ticket=ticket,
            ticket_closed_client_text=ticket_closed_client_text,
        )
        try:
            new_message_id = _send_review_message(
                send_message_fn,
                external_chat_id,
                post_close_text,
                post_close_parse_mode,
                remove_keyboard=markup is None,
                reply_markup=markup,
            )
        except Exception:
            new_message_id = None
        if new_message_id is None:
            cycle.state = TicketReviewCycleState.AWAITING_RATING.value
            cycle.review_current_message_id = old_message_id
            cycle.updated_at = _now_utc()
            db.commit()
            return {"ok": True, "review": "post_close_failed"}
        if _markup_has_review_action(markup):
            set_review_current_message_id(db, cycle=cycle, message_id=new_message_id)
        _safe_delete_exact_message(delete_message_fn, external_chat_id, old_message_id)
        db.commit()
        return {"ok": True, "review": "offered", "cycle_id": cycle.id}

    if action == "rate":
        rating = _rating_from_callback_data(data)
        if rating is None or cycle.review_id:
            db.commit()
            _handle_stale_callback(
                external_chat_id=external_chat_id,
                callback_id=callback_id,
                callback_message_id=callback_message_id,
                notify_client=notify_client,
                answer_callback_query_fn=answer_callback_query_fn,
                safe_remove_inline_keyboard_fn=safe_remove_inline_keyboard_fn,
            )
            return {"ok": True, "review": "stale"}
        if notify_client and callback_id:
            answer_callback_query_fn(callback_id)
        old_message_id = cycle.review_current_message_id
        agent = db.get(User, cycle.agent_id)
        rated_at = _now_utc()
        review = save_review_rating(
            db,
            cycle=cycle,
            rating=rating,
            agent_name_snapshot=_agent_name_snapshot(agent),
            client_name_snapshot=_client_name_snapshot(db, contact),
            ticket_subject_snapshot=resolve_ticket_subject(db, ticket),
            rated_at=rated_at,
        )
        text_prompt, text_parse_mode = render_automatic_message_for_telegram(
            db,
            "telegram.review.text_prompt",
            REVIEW_TEXT_PROMPT_FALLBACK_TEXT,
            ticket=ticket,
            contact=contact,
            agent=agent,
        )
        try:
            new_message_id = _send_review_message(send_message_fn, external_chat_id, text_prompt, text_parse_mode)
        except Exception:
            new_message_id = None
        if new_message_id is None:
            _safe_delete_exact_message(delete_message_fn, external_chat_id, old_message_id)
            complete_review_cycle(db, cycle=cycle, preserve_current_message_id=False)
            db.commit()
            return {"ok": True, "review": "completed_without_text_prompt", "cycle_id": cycle.id, "review_id": review.id}
        set_review_current_message_id(db, cycle=cycle, message_id=new_message_id)
        _safe_delete_exact_message(delete_message_fn, external_chat_id, old_message_id)
        db.commit()
        return {"ok": True, "review": "awaiting_text", "cycle_id": cycle.id, "review_id": review.id}

    if action == "finish":
        review = db.get(TicketReview, cycle.review_id) if cycle.review_id else None
        if review is None or not review.review_text:
            db.commit()
            _handle_stale_callback(
                external_chat_id=external_chat_id,
                callback_id=callback_id,
                callback_message_id=callback_message_id,
                notify_client=notify_client,
                answer_callback_query_fn=answer_callback_query_fn,
                safe_remove_inline_keyboard_fn=safe_remove_inline_keyboard_fn,
            )
            return {"ok": True, "review": "stale"}
        if notify_client and callback_id:
            answer_callback_query_fn(callback_id)
        sent_thank_you = _complete_review_with_thank_you(
            db,
            cycle=cycle,
            ticket=ticket,
            contact=contact,
            agent=db.get(User, cycle.agent_id),
            external_chat_id=external_chat_id,
            send_message_fn=send_message_fn,
            delete_message_fn=delete_message_fn,
        )
        db.commit()
        return {"ok": True, "review": "completed", "cycle_id": cycle.id, "thank_you_sent": sent_thank_you}

    db.commit()
    return {"ok": True, "ignored": "invalid_review_callback"}
