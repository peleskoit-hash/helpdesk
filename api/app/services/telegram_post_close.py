from __future__ import annotations

import json
import uuid
from datetime import UTC, datetime

from sqlalchemy import and_, desc, select
from sqlalchemy.orm import Session

from ..models import Client, ClientProfile, TelegramContact, TelegramUpdate, Ticket, TicketReview, TicketReviewCycle, TicketReviewCycleState, TicketStatus, TicketTaskKind, TicketWorkflowType, User
from .automatic_messages import render_automatic_message_for_telegram
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


def review_start_callback_data(cycle_id: str) -> str:
    return f"{REVIEW_START_PREFIX}{cycle_id}"


def review_back_callback_data(cycle_id: str) -> str:
    return f"{REVIEW_BACK_PREFIX}{cycle_id}"


def review_rate_callback_data(cycle_id: str, rating: int) -> str:
    return f"{REVIEW_RATE_PREFIX}{cycle_id}:{rating}"


def review_finish_callback_data(cycle_id: str) -> str:
    return f"{REVIEW_FINISH_PREFIX}{cycle_id}"


def is_review_callback_data(data: str) -> bool:
    return (
        data.startswith(REVIEW_START_PREFIX)
        or data.startswith(REVIEW_BACK_PREFIX)
        or data.startswith(REVIEW_RATE_PREFIX)
        or data.startswith(REVIEW_FINISH_PREFIX)
    )


def post_close_actions_markup(cycle: TicketReviewCycle) -> dict:
    return {
        "inline_keyboard": [
            [{"text": "Оставить отзыв", "callback_data": review_start_callback_data(cycle.id)}],
        ],
    }


def review_rating_markup(cycle: TicketReviewCycle) -> dict:
    rows = [
        [{"text": "⭐" * rating, "callback_data": review_rate_callback_data(cycle.id, rating)}]
        for rating in range(1, 6)
    ]
    rows.append([{"text": "Назад", "callback_data": review_back_callback_data(cycle.id)}])
    return {"inline_keyboard": rows}


def review_finish_markup(cycle: TicketReviewCycle) -> dict:
    return {"inline_keyboard": [[{"text": "Завершить отзыв", "callback_data": review_finish_callback_data(cycle.id)}]]}


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


def _now_utc() -> datetime:
    return datetime.now(UTC)


def _agent_name_snapshot(agent: User | None) -> str:
    return ((agent.full_name if agent is not None else None) or "Сотрудник").strip()


def _send_review_message(send_message_fn, external_chat_id: str, text: str, parse_mode: str | None, **kwargs) -> str | None:
    response = send_message_fn(external_chat_id, text, parse_mode=parse_mode, **kwargs)
    return _telegram_message_id(response)


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
    try:
        thank_you_message_id = _send_review_message(send_message_fn, external_chat_id, thank_you_text, thank_you_parse_mode)
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
            markup = review_finish_markup(cycle)
            result_name = "caption_saved"
        else:
            prompt_key = "telegram.review.attachment_no_text"
            fallback = REVIEW_ATTACHMENT_NO_TEXT_FALLBACK_TEXT
            review = db.get(TicketReview, cycle.review_id) if cycle.review_id else None
            markup = review_finish_markup(cycle) if cycle.state == TicketReviewCycleState.AWAITING_TEXT_AFTER_CAPTION.value and review and review.review_text else None
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
                reply_markup=review_rating_markup(cycle),
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
        close_text, close_parse_mode = render_automatic_message_for_telegram(
            db,
            "telegram.ticket.close.client",
            REVIEW_CLOSE_FALLBACK_TEXT,
            ticket=ticket,
            contact=contact,
        )
        try:
            new_message_id = _send_review_message(
                send_message_fn,
                external_chat_id,
                close_text,
                close_parse_mode,
                remove_keyboard=False,
                reply_markup=post_close_actions_markup(cycle),
            )
        except Exception:
            new_message_id = None
        if new_message_id is None:
            db.commit()
            return {"ok": True, "review": "post_close_failed"}
        return_review_cycle_to_offered(db, cycle=cycle)
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
