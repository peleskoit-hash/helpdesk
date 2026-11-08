from __future__ import annotations

import base64
import logging
import uuid
from datetime import datetime
from math import ceil

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..config import settings
from ..models import TelegramNotificationAccount, User
from .employee_notification_backlog import handle_backlog_callback
from .employee_telegram_access import (
    BOT_ROLE_ADMIN,
    BOT_ROLE_OWNER,
    BOT_ROLE_USER,
    EmployeeTelegramAccess,
    count_pending_notification_requests,
    grant_notification_bot_admin,
    resolve_employee_telegram_access,
    revoke_notification_bot_admin,
)
from .telegram_bot import answer_callback_query, send_message, set_bot_commands
from .telegram_employee_binding import (
    REQUEST_STATUS_APPROVED,
    REQUEST_STATUS_PENDING,
    TelegramBindingOperationError,
    approve_binding,
    block_account,
    create_or_update_pending_request,
    is_blocked_approved_binding,
    notification_account_by_telegram_user_id,
    reject_binding,
    touch_notification_interaction,
    unblock_account,
    unbind_employee,
)

logger = logging.getLogger(__name__)

PAGE_SIZE = 20
BUTTONS_PER_ROW = 2

EMPLOYEE_BOT_COMMANDS = [
    {"command": "start", "description": "Открыть меню"},
]

EMPLOYEE_START_BUTTON_TEXT = "Старт"
EMPLOYEE_START_PROMPT_TEXT = "Используйте кнопку «Старт» для открытия меню."
EMPLOYEE_MENU_TEXT = "Выберите раздел."
EMPLOYEE_BINDING_ACK_TEXT = (
    "Запрос на привязку Telegram для уведомлений отправлен администратору. "
    "После подтверждения вы сможете получать уведомления в этот чат."
)
EMPLOYEE_PENDING_TEXT = "Запрос на привязку уже отправлен и ожидает подтверждения администратора."
EMPLOYEE_BLOCKED_TEXT = "Доступ к боту уведомлений заблокирован. Обратитесь к администратору."
EMPLOYEE_PRIVATE_CHAT_ONLY_TEXT = "Используйте этого бота только в личном чате."
EMPLOYEE_FORBIDDEN_TEXT = "Недостаточно прав для этого действия."
EMPLOYEE_NOT_FOUND_TEXT = "Запись не найдена или уже изменилась."
CALLBACK_PREFIX = "n"
UUID_REF_LENGTH = 22


def _message_text(update: dict) -> str:
    message = update.get("message") or {}
    return str(message.get("text") or "").strip()


def _command_name(text: str) -> str | None:
    if not text.startswith("/"):
        return None
    command = text.split(maxsplit=1)[0]
    command = command.split("@", 1)[0]
    return command.lower()


def _display_name(from_user: dict) -> str | None:
    parts = [
        str(from_user.get("first_name") or "").strip(),
        str(from_user.get("last_name") or "").strip(),
    ]
    display_name = " ".join(part for part in parts if part)
    return display_name or None


def set_employee_bot_commands() -> dict:
    return set_bot_commands(EMPLOYEE_BOT_COMMANDS, bot_token=settings.employee_telegram_bot_token)


def _employee_start_reply_markup() -> dict:
    return {
        "keyboard": [[{"text": EMPLOYEE_START_BUTTON_TEXT}]],
        "resize_keyboard": True,
    }


def _send_employee_bot_text(chat_id: str, text: str, *, reply_markup: dict | None = None) -> None:
    if not settings.employee_telegram_bot_token:
        return
    send_message(
        chat_id,
        text,
        reply_markup=reply_markup,
        bot_token=settings.employee_telegram_bot_token,
    )


def _answer_employee_callback(callback_query_id: str, text: str | None = None) -> None:
    if not settings.employee_telegram_bot_token:
        return
    answer_callback_query(callback_query_id, text, bot_token=settings.employee_telegram_bot_token)


def _inline_menu_button(text: str, callback_data: str) -> list[dict]:
    return [{"text": text, "callback_data": callback_data}]


def _send_employee_start_keyboard(chat_id: str) -> None:
    _send_employee_bot_text(
        chat_id,
        EMPLOYEE_START_PROMPT_TEXT,
        reply_markup=_employee_start_reply_markup(),
    )


def _callback_data(action: str, *parts: str | int) -> str:
    payload = ":".join([CALLBACK_PREFIX, action, *[str(part) for part in parts]])
    if len(payload.encode("utf-8")) > 64:
        raise ValueError(f"callback_data exceeds Telegram limit: {payload}")
    return payload


def _uuid_to_ref(value: str) -> str:
    encoded = base64.urlsafe_b64encode(uuid.UUID(value).bytes).decode("ascii").rstrip("=")
    if len(encoded) != UUID_REF_LENGTH:
        raise ValueError(f"unexpected UUID ref length: {encoded}")
    return encoded


def _uuid_ref_to_value(value: str) -> str | None:
    if len(value) != UUID_REF_LENGTH:
        return None
    try:
        raw = base64.urlsafe_b64decode(f"{value}==".encode("ascii"))
        decoded = str(uuid.UUID(bytes=raw))
    except (ValueError, TypeError):
        return None
    return decoded if _uuid_to_ref(decoded) == value else None


def _account_ref(account_id: str) -> str:
    return _uuid_to_ref(account_id)


def _account_id_from_ref(account_ref: str) -> str | None:
    return _uuid_ref_to_value(account_ref)


def _callback_tokens(data: str) -> list[str] | None:
    parts = str(data or "").split(":")
    if len(parts) < 2 or parts[0] != CALLBACK_PREFIX:
        return None
    return parts[1:]


def _callback_part(tokens: list[str], index: int) -> str | None:
    if index < 0 or index >= len(tokens):
        return None
    return tokens[index]


def _callback_page(tokens: list[str], index: int) -> int | None:
    value = _callback_part(tokens, index)
    if value is None or not value.isdigit():
        return None
    return int(value)


def _invalid_callback(chat_id: str) -> dict:
    _send_employee_bot_text(chat_id, EMPLOYEE_NOT_FOUND_TEXT)
    return {"ok": False, "reason": "invalid_callback"}


def _parse_callback(data: str) -> tuple[str, list[str]] | None:
    tokens = _callback_tokens(data)
    if tokens is None:
        return None
    action = _callback_part(tokens, 0)
    if action is None:
        return None
    args = tokens[1:]
    page_actions = {"ad", "aa", "nr", "bu", "bk"}
    account_actions = {"rq", "rr", "rrc", "bd", "bp", "bc", "up", "uc", "kc", "kp", "kc2", "ac", "arp", "arc", "aas", "aac"}
    if action in {"m", "mb", "su", "suc"}:
        return (action, args) if len(args) == 0 else None
    if action in page_actions:
        return (action, args) if len(args) == 1 and args[0].isdigit() else None
    if action in account_actions:
        if len(args) != 1 or _account_id_from_ref(args[0]) is None:
            return None
        return action, args
    if action == "rb":
        if len(args) != 2 or _account_id_from_ref(args[0]) is None or not args[1].isdigit():
            return None
        return action, args
    if action in {"rs", "rc"}:
        if len(args) != 2:
            return None
        account_id = _uuid_ref_to_value(args[0])
        user_id = _uuid_ref_to_value(args[1])
        if account_id is None or user_id is None:
            return None
        return action, args
    return None


def _rows(buttons: list[dict], per_row: int = BUTTONS_PER_ROW) -> list[list[dict]]:
    return [buttons[index : index + per_row] for index in range(0, len(buttons), per_row)]


def _markup(rows: list[list[dict]]) -> dict:
    return {"inline_keyboard": rows}


def _pending_requests_button_text(db: Session) -> str:
    count = count_pending_notification_requests(db)
    return "Новые запросы" if count <= 0 else f"Новые запросы ({count})"


def _employee_inline_menu_markup(db: Session, access: EmployeeTelegramAccess) -> dict:
    keyboard: list[list[dict]] = []
    if access.bot_role == BOT_ROLE_OWNER:
        keyboard.extend(
            [
                _inline_menu_button(_pending_requests_button_text(db), _callback_data("nr", 0)),
                _inline_menu_button("Привязанные пользователи", _callback_data("bu", 0)),
                _inline_menu_button("Заблокированные", _callback_data("bk", 0)),
                _inline_menu_button("Администраторы", _callback_data("ad", 0)),
            ]
        )
    elif access.bot_role == BOT_ROLE_ADMIN:
        keyboard.extend(
            [
                _inline_menu_button(_pending_requests_button_text(db), _callback_data("nr", 0)),
                _inline_menu_button("Привязанные пользователи", _callback_data("bu", 0)),
                _inline_menu_button("Заблокированные", _callback_data("bk", 0)),
                _inline_menu_button("Моя привязка", _callback_data("mb")),
            ]
        )
    elif access.bot_role == BOT_ROLE_USER:
        keyboard.append(_inline_menu_button("Моя привязка", _callback_data("mb")))
    return _markup(keyboard)


def _send_employee_role_menu(db: Session, chat_id: str, access: EmployeeTelegramAccess) -> None:
    _send_employee_bot_text(
        chat_id,
        EMPLOYEE_MENU_TEXT,
        reply_markup=_employee_inline_menu_markup(db, access),
    )


def _telegram_label(account: TelegramNotificationAccount) -> str:
    display_name = (account.telegram_display_name or "").strip() or f"Tg ID {account.telegram_user_id}"
    username = (account.telegram_username or "").strip()
    username_label = f"@{username}" if username else "без username"
    return f"{display_name} — {username_label}"


def _user_label(user: User) -> str:
    return f"{user.full_name} — {user.role}"


def _status_label(account: TelegramNotificationAccount) -> str:
    if account.is_blocked:
        return "заблокирована"
    if account.request_status == REQUEST_STATUS_APPROVED and account.bound_user_id:
        return "активна"
    if account.request_status == REQUEST_STATUS_PENDING:
        return "ожидает подтверждения"
    return account.request_status


def _date_label(value: datetime | None) -> str:
    return value.strftime("%Y-%m-%d %H:%M") if value else "неизвестно"


def _page(items: list, page: int) -> tuple[list, int, int]:
    page_count = max(1, ceil(len(items) / PAGE_SIZE))
    safe_page = min(max(page, 0), page_count - 1)
    start = safe_page * PAGE_SIZE
    return items[start : start + PAGE_SIZE], safe_page, page_count


def _nav(prefix: str, page: int, page_count: int) -> list[dict]:
    if page_count <= 1:
        return []
    buttons: list[dict] = []
    if page > 0:
        buttons.append({"text": "Пред. страница", "callback_data": f"{prefix}:{page - 1}"})
    if page < page_count - 1:
        buttons.append({"text": "След. страница", "callback_data": f"{prefix}:{page + 1}"})
    return buttons


def _account_by_id(db: Session, account_id: str) -> TelegramNotificationAccount | None:
    return db.get(TelegramNotificationAccount, account_id)


def _pending_account_by_id(db: Session, account_id: str) -> TelegramNotificationAccount | None:
    account = _account_by_id(db, account_id)
    if (
        account is None
        or account.request_status != REQUEST_STATUS_PENDING
        or account.bound_user_id is not None
        or account.is_blocked
    ):
        return None
    return account


def _bound_account_by_id(db: Session, account_id: str) -> TelegramNotificationAccount | None:
    account = _account_by_id(db, account_id)
    if account is None or account.request_status != REQUEST_STATUS_APPROVED or not account.bound_user_id:
        return None
    return account


def _active_bound_account_by_id(db: Session, account_id: str) -> TelegramNotificationAccount | None:
    account = _bound_account_by_id(db, account_id)
    if account is None or account.is_blocked:
        return None
    return account


def _blocked_account_by_id(db: Session, account_id: str) -> TelegramNotificationAccount | None:
    account = _bound_account_by_id(db, account_id)
    if account is None or not account.is_blocked:
        return None
    return account


def _list_pending_accounts(db: Session) -> list[TelegramNotificationAccount]:
    return db.execute(
        select(TelegramNotificationAccount)
        .where(TelegramNotificationAccount.request_status == REQUEST_STATUS_PENDING)
        .where(TelegramNotificationAccount.bound_user_id.is_(None))
        .where(TelegramNotificationAccount.is_blocked.is_(False))
        .order_by(TelegramNotificationAccount.created_at.asc(), TelegramNotificationAccount.id.asc())
    ).scalars().all()


def _list_bound_accounts(db: Session) -> list[TelegramNotificationAccount]:
    return db.execute(
        select(TelegramNotificationAccount)
        .join(User, TelegramNotificationAccount.bound_user_id == User.id)
        .where(TelegramNotificationAccount.request_status == REQUEST_STATUS_APPROVED)
        .where(TelegramNotificationAccount.bound_user_id.is_not(None))
        .where(TelegramNotificationAccount.is_blocked.is_(False))
        .order_by(User.full_name.asc(), TelegramNotificationAccount.id.asc())
    ).scalars().all()


def _list_blocked_accounts(db: Session) -> list[TelegramNotificationAccount]:
    return db.execute(
        select(TelegramNotificationAccount)
        .where(TelegramNotificationAccount.request_status == REQUEST_STATUS_APPROVED)
        .where(TelegramNotificationAccount.bound_user_id.is_not(None))
        .where(TelegramNotificationAccount.is_blocked.is_(True))
        .order_by(TelegramNotificationAccount.updated_at.desc(), TelegramNotificationAccount.id.asc())
    ).scalars().all()


def _list_admin_accounts(db: Session) -> list[TelegramNotificationAccount]:
    return db.execute(
        select(TelegramNotificationAccount)
        .join(User, TelegramNotificationAccount.bound_user_id == User.id)
        .where(TelegramNotificationAccount.is_notification_bot_admin.is_(True))
        .order_by(User.full_name.asc(), TelegramNotificationAccount.id.asc())
    ).scalars().all()


def _list_available_users(db: Session) -> list[User]:
    bound_user_ids = select(TelegramNotificationAccount.bound_user_id).where(
        TelegramNotificationAccount.bound_user_id.is_not(None)
    )
    return db.execute(
        select(User)
        .where(User.is_active.is_(True))
        .where(User.id.not_in(bound_user_ids))
        .order_by(User.full_name.asc(), User.id.asc())
    ).scalars().all()


def _list_admin_candidates(db: Session) -> list[TelegramNotificationAccount]:
    return db.execute(
        select(TelegramNotificationAccount)
        .join(User, TelegramNotificationAccount.bound_user_id == User.id)
        .where(TelegramNotificationAccount.request_status == REQUEST_STATUS_APPROVED)
        .where(TelegramNotificationAccount.bound_user_id.is_not(None))
        .where(TelegramNotificationAccount.is_blocked.is_(False))
        .where(TelegramNotificationAccount.is_notification_bot_admin.is_(False))
        .where(User.is_active.is_(True))
        .order_by(User.full_name.asc(), TelegramNotificationAccount.id.asc())
    ).scalars().all()


def _user_for_account(db: Session, account: TelegramNotificationAccount | None) -> User | None:
    if account is None or not account.bound_user_id:
        return None
    return db.get(User, account.bound_user_id)


def _danger_markup(confirm_callback: str, cancel_callback: str | None = None) -> dict:
    return _markup(
        [
            [{"text": "Подтвердить", "callback_data": confirm_callback}],
            [{"text": "Отмена", "callback_data": cancel_callback or _callback_data("m")}],
        ]
    )


def _require_bindings_access(access: EmployeeTelegramAccess) -> bool:
    return access.can_manage_bindings


def _require_admin_access(access: EmployeeTelegramAccess) -> bool:
    return access.can_manage_admins


def _show_new_requests(db: Session, chat_id: str, page: int) -> dict:
    items, page, page_count = _page(_list_pending_accounts(db), page)
    buttons = [{"text": _telegram_label(account), "callback_data": _callback_data("rq", _account_ref(account.id))} for account in items]
    keyboard = _rows(buttons)
    nav = _nav(_callback_data("nr"), page, page_count)
    if nav:
        keyboard.append(nav)
    keyboard.append([{"text": "Назад", "callback_data": _callback_data("m")}])
    text = "Новые запросы" if items else "Новых запросов нет."
    _send_employee_bot_text(chat_id, text, reply_markup=_markup(keyboard))
    return {"ok": True, "action": "new_requests_shown", "page": page, "count": len(items)}


def _show_request_card(db: Session, chat_id: str, account_id: str) -> dict:
    account = _pending_account_by_id(db, account_id)
    if account is None:
        _send_employee_bot_text(chat_id, EMPLOYEE_NOT_FOUND_TEXT)
        return {"ok": False, "reason": "not_found"}
    text = (
        "Новая заявка\n"
        f"Telegram: {_telegram_label(account)}\n"
        f"display name: {(account.telegram_display_name or 'не указан')}\n"
        f"username: {('@' + account.telegram_username) if account.telegram_username else 'без username'}\n"
        f"дата запроса: {_date_label(account.created_at)}"
    )
    _send_employee_bot_text(
        chat_id,
        text,
        reply_markup=_markup(
            [
                [{"text": "Привязать к сотруднику", "callback_data": _callback_data("rb", _account_ref(account.id), 0)}],
                [{"text": "Отклонить", "callback_data": _callback_data("rr", _account_ref(account.id))}],
                [{"text": "Назад", "callback_data": _callback_data("nr", 0)}],
            ]
        ),
    )
    return {"ok": True, "action": "request_card_shown"}


def _show_bind_user_list(db: Session, chat_id: str, account_id: str, page: int) -> dict:
    if _pending_account_by_id(db, account_id) is None:
        _send_employee_bot_text(chat_id, EMPLOYEE_NOT_FOUND_TEXT)
        return {"ok": False, "reason": "not_found"}
    items, page, page_count = _page(_list_available_users(db), page)
    buttons = [
        {"text": _user_label(user), "callback_data": _callback_data("rs", _account_ref(account_id), _uuid_to_ref(user.id))}
        for user in items
    ]
    keyboard = _rows(buttons)
    nav = _nav(_callback_data("rb", _account_ref(account_id)), page, page_count)
    if nav:
        keyboard.append(nav)
    keyboard.append([{"text": "Назад", "callback_data": _callback_data("rq", _account_ref(account_id))}])
    _send_employee_bot_text(chat_id, "Выберите сотрудника.", reply_markup=_markup(keyboard))
    return {"ok": True, "action": "bind_user_list_shown", "page": page, "count": len(items)}


def _show_bind_confirmation(db: Session, chat_id: str, account_id: str, user_ref: str) -> dict:
    account = _pending_account_by_id(db, account_id)
    user_id = _uuid_ref_to_value(user_ref)
    user = db.get(User, user_id) if user_id is not None else None
    if account is None or user is None or not user.is_active:
        _send_employee_bot_text(chat_id, EMPLOYEE_NOT_FOUND_TEXT)
        return {"ok": False, "reason": "not_found"}
    _send_employee_bot_text(
        chat_id,
        f"Подтвердить привязку {_telegram_label(account)} к сотруднику {_user_label(user)}?",
        reply_markup=_markup(
            [
                [{"text": "Подтвердить привязку", "callback_data": _callback_data("rc", _account_ref(account_id), user_ref)}],
                [{"text": "Отмена", "callback_data": _callback_data("rq", _account_ref(account_id))}],
            ]
        ),
    )
    return {"ok": True, "action": "bind_confirmation_shown"}


def _approve_request(db: Session, chat_id: str, account_id: str, user_ref: str) -> dict:
    account = _pending_account_by_id(db, account_id)
    if account is None:
        _send_employee_bot_text(chat_id, EMPLOYEE_NOT_FOUND_TEXT)
        return {"ok": False, "reason": "not_found"}
    user_id = _uuid_ref_to_value(user_ref)
    user = db.get(User, user_id) if user_id is not None else None
    if user is None:
        _send_employee_bot_text(chat_id, EMPLOYEE_NOT_FOUND_TEXT)
        return {"ok": False, "reason": "not_found"}
    try:
        approve_binding(db, user_id=user.id, telegram_user_id=account.telegram_user_id)
        db.commit()
    except TelegramBindingOperationError as exc:
        db.rollback()
        _send_employee_bot_text(chat_id, exc.message)
        return {"ok": False, "reason": exc.code}
    _send_employee_bot_text(chat_id, "Telegram-привязка подтверждена.", reply_markup=_markup([[{"text": "Назад", "callback_data": _callback_data("nr", 0)}]]))
    return {"ok": True, "action": "binding_approved"}


def _reject_request_prompt(db: Session, chat_id: str, account_id: str) -> dict:
    if _pending_account_by_id(db, account_id) is None:
        _send_employee_bot_text(chat_id, EMPLOYEE_NOT_FOUND_TEXT)
        return {"ok": False, "reason": "not_found"}
    _send_employee_bot_text(
        chat_id,
        "Отклонить и удалить новую заявку?",
        reply_markup=_danger_markup(_callback_data("rrc", _account_ref(account_id)), _callback_data("rq", _account_ref(account_id))),
    )
    return {"ok": True, "action": "reject_confirmation_shown"}


def _reject_request(db: Session, chat_id: str, account_id: str) -> dict:
    account = _pending_account_by_id(db, account_id)
    if account is None:
        _send_employee_bot_text(chat_id, EMPLOYEE_NOT_FOUND_TEXT)
        return {"ok": False, "reason": "not_found"}
    try:
        reject_binding(db, telegram_user_id=account.telegram_user_id)
        db.commit()
    except TelegramBindingOperationError as exc:
        db.rollback()
        _send_employee_bot_text(chat_id, exc.message)
        return {"ok": False, "reason": exc.code}
    _send_employee_bot_text(chat_id, "Заявка удалена.", reply_markup=_markup([[{"text": "Назад", "callback_data": _callback_data("nr", 0)}]]))
    return {"ok": True, "action": "binding_rejected"}


def _show_bound_users(db: Session, chat_id: str, page: int) -> dict:
    items, page, page_count = _page(_list_bound_accounts(db), page)
    buttons = []
    for account in items:
        user = _user_for_account(db, account)
        buttons.append({"text": _user_label(user) if user else account.telegram_user_id, "callback_data": _callback_data("bd", _account_ref(account.id))})
    keyboard = _rows(buttons)
    nav = _nav(_callback_data("bu"), page, page_count)
    if nav:
        keyboard.append(nav)
    keyboard.append([{"text": "Назад", "callback_data": _callback_data("m")}])
    _send_employee_bot_text(chat_id, "Привязанные пользователи" if items else "Активных привязок нет.", reply_markup=_markup(keyboard))
    return {"ok": True, "action": "bound_users_shown", "page": page, "count": len(items)}


def _show_bound_card(db: Session, chat_id: str, account_id: str) -> dict:
    account = _active_bound_account_by_id(db, account_id)
    user = _user_for_account(db, account)
    if account is None or user is None:
        _send_employee_bot_text(chat_id, EMPLOYEE_NOT_FOUND_TEXT)
        return {"ok": False, "reason": "not_found"}
    text = (
        "Telegram-привязка\n"
        f"сотрудник: {_user_label(user)}\n"
        f"Telegram: {_telegram_label(account)}\n"
        f"статус технической доставки: {'доступна' if account.delivery_available else 'недоступна'}"
    )
    _send_employee_bot_text(
        chat_id,
        text,
        reply_markup=_markup(
            [
                [{"text": "Заблокировать", "callback_data": _callback_data("bp", _account_ref(account.id))}],
                [{"text": "Отвязать", "callback_data": _callback_data("up", _account_ref(account.id))}],
                [{"text": "Назад", "callback_data": _callback_data("bu", 0)}],
            ]
        ),
    )
    return {"ok": True, "action": "bound_card_shown"}


def _block_prompt(db: Session, chat_id: str, account_id: str) -> dict:
    if _active_bound_account_by_id(db, account_id) is None:
        _send_employee_bot_text(chat_id, EMPLOYEE_NOT_FOUND_TEXT)
        return {"ok": False, "reason": "not_found"}
    _send_employee_bot_text(chat_id, "Заблокировать эту Telegram-привязку?", reply_markup=_danger_markup(_callback_data("bc", _account_ref(account_id)), _callback_data("bd", _account_ref(account_id))))
    return {"ok": True, "action": "block_confirmation_shown"}


def _block_account(db: Session, chat_id: str, account_id: str) -> dict:
    account = _active_bound_account_by_id(db, account_id)
    if account is None:
        _send_employee_bot_text(chat_id, EMPLOYEE_NOT_FOUND_TEXT)
        return {"ok": False, "reason": "not_found"}
    try:
        block_account(db, telegram_user_id=account.telegram_user_id)
        db.commit()
    except TelegramBindingOperationError as exc:
        db.rollback()
        _send_employee_bot_text(chat_id, exc.message)
        return {"ok": False, "reason": exc.code}
    _send_employee_bot_text(chat_id, "Telegram-привязка заблокирована.", reply_markup=_markup([[{"text": "Назад", "callback_data": _callback_data("bu", 0)}]]))
    return {"ok": True, "action": "binding_blocked"}


def _unbind_prompt(db: Session, chat_id: str, account_id: str) -> dict:
    if _active_bound_account_by_id(db, account_id) is None:
        _send_employee_bot_text(chat_id, EMPLOYEE_NOT_FOUND_TEXT)
        return {"ok": False, "reason": "not_found"}
    _send_employee_bot_text(chat_id, "Отвязать и удалить эту Telegram-привязку?", reply_markup=_danger_markup(_callback_data("uc", _account_ref(account_id)), _callback_data("bd", _account_ref(account_id))))
    return {"ok": True, "action": "unbind_confirmation_shown"}


def _unbind_account(db: Session, chat_id: str, account_id: str) -> dict:
    account = _active_bound_account_by_id(db, account_id)
    if account is None:
        _send_employee_bot_text(chat_id, EMPLOYEE_NOT_FOUND_TEXT)
        return {"ok": False, "reason": "not_found"}
    try:
        unbind_employee(db, user_id=account.bound_user_id)
        db.commit()
    except TelegramBindingOperationError as exc:
        db.rollback()
        _send_employee_bot_text(chat_id, exc.message)
        return {"ok": False, "reason": exc.code}
    _send_employee_bot_text(chat_id, "Telegram-привязка удалена.", reply_markup=_markup([[{"text": "Назад", "callback_data": _callback_data("bu", 0)}]]))
    return {"ok": True, "action": "binding_unbound"}


def _show_blocked_users(db: Session, chat_id: str, page: int) -> dict:
    items, page, page_count = _page(_list_blocked_accounts(db), page)
    buttons = []
    for account in items:
        user = _user_for_account(db, account)
        buttons.append({"text": _user_label(user) if user else account.telegram_user_id, "callback_data": _callback_data("kc", _account_ref(account.id))})
    keyboard = _rows(buttons)
    nav = _nav(_callback_data("bk"), page, page_count)
    if nav:
        keyboard.append(nav)
    keyboard.append([{"text": "Назад", "callback_data": _callback_data("m")}])
    _send_employee_bot_text(chat_id, "Заблокированные" if items else "Заблокированных привязок нет.", reply_markup=_markup(keyboard))
    return {"ok": True, "action": "blocked_users_shown", "page": page, "count": len(items)}


def _show_blocked_card(db: Session, chat_id: str, account_id: str) -> dict:
    account = _blocked_account_by_id(db, account_id)
    user = _user_for_account(db, account)
    if account is None or user is None:
        _send_employee_bot_text(chat_id, EMPLOYEE_NOT_FOUND_TEXT)
        return {"ok": False, "reason": "not_found"}
    text = (
        "Заблокированная привязка\n"
        f"сотрудник: {_user_label(user)}\n"
        f"Telegram: {_telegram_label(account)}\n"
        f"дата блокировки: {_date_label(account.updated_at)}"
    )
    _send_employee_bot_text(
        chat_id,
        text,
        reply_markup=_markup(
            [
                [{"text": "Разблокировать", "callback_data": _callback_data("kp", _account_ref(account.id))}],
                [{"text": "Назад", "callback_data": _callback_data("bk", 0)}],
            ]
        ),
    )
    return {"ok": True, "action": "blocked_card_shown"}


def _unblock_prompt(db: Session, chat_id: str, account_id: str) -> dict:
    if _blocked_account_by_id(db, account_id) is None:
        _send_employee_bot_text(chat_id, EMPLOYEE_NOT_FOUND_TEXT)
        return {"ok": False, "reason": "not_found"}
    _send_employee_bot_text(chat_id, "Разблокировать эту Telegram-привязку?", reply_markup=_danger_markup(_callback_data("kc2", _account_ref(account_id)), _callback_data("kc", _account_ref(account_id))))
    return {"ok": True, "action": "unblock_confirmation_shown"}


def _unblock_account(db: Session, chat_id: str, account_id: str) -> dict:
    account = _blocked_account_by_id(db, account_id)
    if account is None:
        _send_employee_bot_text(chat_id, EMPLOYEE_NOT_FOUND_TEXT)
        return {"ok": False, "reason": "not_found"}
    try:
        unblock_account(db, telegram_user_id=account.telegram_user_id)
        db.commit()
    except TelegramBindingOperationError as exc:
        db.rollback()
        _send_employee_bot_text(chat_id, exc.message)
        return {"ok": False, "reason": exc.code}
    _send_employee_bot_text(chat_id, "Telegram-привязка разблокирована.", reply_markup=_markup([[{"text": "Назад", "callback_data": _callback_data("bk", 0)}]]))
    return {"ok": True, "action": "binding_unblocked"}


def _show_admins(db: Session, chat_id: str, page: int) -> dict:
    items, page, page_count = _page(_list_admin_accounts(db), page)
    buttons = []
    for account in items:
        user = _user_for_account(db, account)
        buttons.append({"text": _user_label(user) if user else account.telegram_user_id, "callback_data": _callback_data("ac", _account_ref(account.id))})
    keyboard = _rows(buttons)
    nav = _nav(_callback_data("ad"), page, page_count)
    if nav:
        keyboard.append(nav)
    keyboard.append([{"text": "Добавить администратора", "callback_data": _callback_data("aa", 0)}])
    keyboard.append([{"text": "Назад", "callback_data": _callback_data("m")}])
    _send_employee_bot_text(chat_id, "Администраторы Notification Bot", reply_markup=_markup(keyboard))
    return {"ok": True, "action": "admins_shown", "page": page, "count": len(items)}


def _show_admin_card(db: Session, chat_id: str, account_id: str) -> dict:
    account = _account_by_id(db, account_id)
    user = _user_for_account(db, account)
    if account is None or user is None or not account.is_notification_bot_admin:
        _send_employee_bot_text(chat_id, EMPLOYEE_NOT_FOUND_TEXT)
        return {"ok": False, "reason": "not_found"}
    text = (
        "Администратор Notification Bot\n"
        f"сотрудник: {_user_label(user)}\n"
        f"Telegram: {_telegram_label(account)}\n"
        "роль в боте: additional admin\n"
        f"активность сотрудника: {'активен' if user.is_active else 'неактивен'}"
    )
    _send_employee_bot_text(
        chat_id,
        text,
        reply_markup=_markup(
            [
                [{"text": "Снять права администратора", "callback_data": _callback_data("arp", _account_ref(account.id))}],
                [{"text": "Назад", "callback_data": _callback_data("ad", 0)}],
            ]
        ),
    )
    return {"ok": True, "action": "admin_card_shown"}


def _admin_revoke_prompt(db: Session, chat_id: str, account_id: str) -> dict:
    account = _account_by_id(db, account_id)
    if account is None or not account.is_notification_bot_admin:
        _send_employee_bot_text(chat_id, EMPLOYEE_NOT_FOUND_TEXT)
        return {"ok": False, "reason": "not_found"}
    _send_employee_bot_text(chat_id, "Снять права администратора Notification Bot?", reply_markup=_danger_markup(_callback_data("arc", _account_ref(account.id)), _callback_data("ac", _account_ref(account.id))))
    return {"ok": True, "action": "admin_revoke_confirmation_shown"}


def _admin_revoke(db: Session, chat_id: str, account_id: str) -> dict:
    account = _account_by_id(db, account_id)
    if account is None or not account.bound_user_id:
        _send_employee_bot_text(chat_id, EMPLOYEE_NOT_FOUND_TEXT)
        return {"ok": False, "reason": "not_found"}
    try:
        revoke_notification_bot_admin(db, user_id=account.bound_user_id)
        db.commit()
    except TelegramBindingOperationError as exc:
        db.rollback()
        _send_employee_bot_text(chat_id, exc.message)
        return {"ok": False, "reason": exc.code}
    _send_employee_bot_text(chat_id, "Права администратора сняты.", reply_markup=_markup([[{"text": "Назад", "callback_data": _callback_data("ad", 0)}]]))
    return {"ok": True, "action": "admin_revoked"}


def _show_admin_candidates(db: Session, chat_id: str, page: int) -> dict:
    items, page, page_count = _page(_list_admin_candidates(db), page)
    buttons = []
    for account in items:
        user = _user_for_account(db, account)
        buttons.append({"text": _user_label(user) if user else account.telegram_user_id, "callback_data": _callback_data("aas", _account_ref(account.id))})
    keyboard = _rows(buttons)
    nav = _nav(_callback_data("aa"), page, page_count)
    if nav:
        keyboard.append(nav)
    keyboard.append([{"text": "Назад", "callback_data": _callback_data("ad", 0)}])
    _send_employee_bot_text(chat_id, "Выберите сотрудника для роли администратора.", reply_markup=_markup(keyboard))
    return {"ok": True, "action": "admin_candidates_shown", "page": page, "count": len(items)}


def _admin_grant_prompt(db: Session, chat_id: str, account_id: str) -> dict:
    account = _active_bound_account_by_id(db, account_id)
    user = _user_for_account(db, account)
    if account is None or user is None or not user.is_active or account.is_notification_bot_admin:
        _send_employee_bot_text(chat_id, EMPLOYEE_NOT_FOUND_TEXT)
        return {"ok": False, "reason": "not_found"}
    _send_employee_bot_text(
        chat_id,
        f"Назначить {_user_label(user)} администратором Notification Bot?",
        reply_markup=_markup(
            [
                [{"text": "Подтвердить", "callback_data": _callback_data("aac", _account_ref(account.id))}],
                [{"text": "Отмена", "callback_data": _callback_data("aa", 0)}],
            ]
        ),
    )
    return {"ok": True, "action": "admin_grant_confirmation_shown"}


def _admin_grant(db: Session, chat_id: str, account_id: str) -> dict:
    account = _active_bound_account_by_id(db, account_id)
    if account is None or not account.bound_user_id:
        _send_employee_bot_text(chat_id, EMPLOYEE_NOT_FOUND_TEXT)
        return {"ok": False, "reason": "not_found"}
    try:
        grant_notification_bot_admin(db, user_id=account.bound_user_id)
        db.commit()
    except TelegramBindingOperationError as exc:
        db.rollback()
        _send_employee_bot_text(chat_id, exc.message)
        return {"ok": False, "reason": exc.code}
    _send_employee_bot_text(chat_id, "Администратор Notification Bot назначен.", reply_markup=_markup([[{"text": "Назад", "callback_data": _callback_data("ad", 0)}]]))
    return {"ok": True, "action": "admin_granted"}


def _show_my_binding(db: Session, chat_id: str, access: EmployeeTelegramAccess) -> dict:
    if access.account is None or not access.account.bound_user_id or access.account.is_blocked:
        _send_employee_bot_text(chat_id, EMPLOYEE_NOT_FOUND_TEXT)
        return {"ok": False, "reason": "not_found"}
    user = db.get(User, access.account.bound_user_id)
    if user is None:
        _send_employee_bot_text(chat_id, EMPLOYEE_NOT_FOUND_TEXT)
        return {"ok": False, "reason": "not_found"}
    text = (
        "Моя привязка\n"
        f"сотрудник ES DESK: {_user_label(user)}\n"
        f"Telegram: {_telegram_label(access.account)}\n"
        f"статус привязки: {_status_label(access.account)}"
    )
    _send_employee_bot_text(
        chat_id,
        text,
        reply_markup=_markup(
            [
                [{"text": "Отменить привязку", "callback_data": _callback_data("su")}],
                [{"text": "Назад", "callback_data": _callback_data("m")}],
            ]
        ),
    )
    return {"ok": True, "action": "my_binding_shown"}


def _self_unbind_prompt(db: Session, chat_id: str, access: EmployeeTelegramAccess) -> dict:
    if access.account is None or not access.account.bound_user_id:
        _send_employee_bot_text(chat_id, EMPLOYEE_NOT_FOUND_TEXT)
        return {"ok": False, "reason": "not_found"}
    _send_employee_bot_text(chat_id, "Отменить и удалить вашу Telegram-привязку?", reply_markup=_danger_markup(_callback_data("suc"), _callback_data("mb")))
    return {"ok": True, "action": "self_unbind_confirmation_shown"}


def _notify_self_unbind(db: Session, initiator_account: TelegramNotificationAccount, user: User | None) -> None:
    if not settings.employee_telegram_bot_token:
        return
    text = (
        "Пользователь самостоятельно отменил Telegram-привязку.\n"
        f"сотрудник: {_user_label(user) if user else initiator_account.bound_user_id}\n"
        f"Telegram: {_telegram_label(initiator_account)}"
    )
    recipient_chat_ids: set[str] = set()
    owner_id = str(settings.telegram_notification_bot_owner_id or "").strip()
    if owner_id and owner_id != initiator_account.telegram_user_id:
        recipient_chat_ids.add(owner_id)
    admin_accounts = _list_admin_accounts(db)
    for account in admin_accounts:
        if account.telegram_user_id == initiator_account.telegram_user_id:
            continue
        admin_user = _user_for_account(db, account)
        if (
            account.telegram_chat_id
            and account.request_status == REQUEST_STATUS_APPROVED
            and not account.is_blocked
            and admin_user is not None
            and admin_user.is_active
        ):
            recipient_chat_ids.add(account.telegram_chat_id)
    for recipient in recipient_chat_ids:
        try:
            _send_employee_bot_text(str(recipient), text)
        except Exception:
            logger.exception("Failed to notify Notification Bot admin about self-unbind")


def _self_unbind(db: Session, chat_id: str, access: EmployeeTelegramAccess) -> dict:
    if access.account is None or not access.account.bound_user_id:
        _send_employee_bot_text(chat_id, EMPLOYEE_NOT_FOUND_TEXT)
        return {"ok": False, "reason": "not_found"}
    account = access.account
    user = db.get(User, account.bound_user_id)
    try:
        unbind_employee(db, user_id=account.bound_user_id)
        _notify_self_unbind(db, account, user)
        db.commit()
    except TelegramBindingOperationError as exc:
        db.rollback()
        _send_employee_bot_text(chat_id, exc.message)
        return {"ok": False, "reason": exc.code}
    _send_employee_bot_text(chat_id, "Ваша Telegram-привязка удалена.", reply_markup=_employee_start_reply_markup())
    return {"ok": True, "action": "self_unbound"}


def _create_pending_binding_request(
    db: Session,
    *,
    telegram_user_id: int | str,
    telegram_chat_id: int | str,
    from_user: dict,
) -> dict:
    account = create_or_update_pending_request(
        db,
        telegram_user_id=str(telegram_user_id),
        telegram_chat_id=str(telegram_chat_id),
        telegram_username=from_user.get("username"),
        telegram_display_name=_display_name(from_user),
    )
    db.commit()
    db.refresh(account)
    _send_employee_bot_text(str(telegram_chat_id), EMPLOYEE_BINDING_ACK_TEXT, reply_markup=_employee_start_reply_markup())
    return {
        "ok": True,
        "action": "binding_requested",
        "telegram_user_id": account.telegram_user_id,
        "request_status": account.request_status,
        "is_blocked": bool(account.is_blocked),
    }


def _handle_start_or_button(db: Session, message: dict, *, opened_via_start_command: bool) -> dict:
    from_user = message.get("from") or {}
    chat = message.get("chat") or {}
    chat_type = str(chat.get("type") or "").strip().lower()
    if chat_type != "private":
        if settings.employee_telegram_bot_token and chat.get("id") is not None:
            try:
                send_message(
                    str(chat["id"]),
                    EMPLOYEE_PRIVATE_CHAT_ONLY_TEXT,
                    bot_token=settings.employee_telegram_bot_token,
                )
            except Exception:
                logger.exception("Failed to send private-chat-only warning for employee bot update")
        return {"ok": True, "ignored": True, "reason": "non_private_chat"}

    telegram_user_id = from_user.get("id")
    telegram_chat_id = chat.get("id")
    if telegram_user_id is None or telegram_chat_id is None:
        return {"ok": True, "ignored": True, "reason": "missing_identity"}

    account = touch_notification_interaction(
        db,
        telegram_user_id=telegram_user_id,
        telegram_chat_id=telegram_chat_id,
        telegram_username=from_user.get("username"),
        telegram_display_name=_display_name(from_user),
    )
    access = resolve_employee_telegram_access(db, telegram_user_id)
    if is_blocked_approved_binding(account):
        db.commit()
        _send_employee_bot_text(str(telegram_chat_id), EMPLOYEE_BLOCKED_TEXT, reply_markup=_employee_start_reply_markup())
        return {
            "ok": True,
            "action": "binding_blocked",
            "telegram_user_id": account.telegram_user_id,
            "request_status": account.request_status,
            "is_blocked": True,
        }
    if access.bot_role in {BOT_ROLE_OWNER, BOT_ROLE_ADMIN, BOT_ROLE_USER}:
        db.commit()
        if opened_via_start_command:
            _send_employee_start_keyboard(str(telegram_chat_id))
        _send_employee_role_menu(db, str(telegram_chat_id), access)
        return {
            "ok": True,
            "action": "menu_shown",
            "telegram_user_id": str(telegram_user_id),
            "bot_role": access.bot_role,
        }
    if account is not None and account.request_status == REQUEST_STATUS_PENDING and account.bound_user_id is None and not account.is_blocked:
        db.commit()
        _send_employee_bot_text(str(telegram_chat_id), EMPLOYEE_PENDING_TEXT, reply_markup=_employee_start_reply_markup())
        return {
            "ok": True,
            "action": "binding_pending",
            "telegram_user_id": account.telegram_user_id,
            "request_status": account.request_status,
            "is_blocked": False,
        }
    return _create_pending_binding_request(
        db,
        telegram_user_id=telegram_user_id,
        telegram_chat_id=telegram_chat_id,
        from_user=from_user,
    )


def _handle_callback_query(db: Session, callback_query: dict) -> dict:
    callback_query_id = str(callback_query.get("id") or "")
    from_user = callback_query.get("from") or {}
    message = callback_query.get("message") or {}
    chat = message.get("chat") or {}
    chat_id = chat.get("id")
    data = str(callback_query.get("data") or "")
    _answer_employee_callback(callback_query_id)

    telegram_user_id = from_user.get("id")
    if telegram_user_id is None or chat_id is None:
        return {"ok": True, "ignored": True, "reason": "missing_callback_identity"}
    chat_id_str = str(chat_id)
    if data.startswith("nb:"):
        return handle_backlog_callback(db, callback_query)
    parsed = _parse_callback(data)
    if parsed is None:
        db.rollback()
        return _invalid_callback(chat_id_str)
    action, args = parsed
    touch_notification_interaction(
        db,
        telegram_user_id=telegram_user_id,
        telegram_chat_id=chat_id,
        telegram_username=from_user.get("username"),
        telegram_display_name=_display_name(from_user),
    )
    access = resolve_employee_telegram_access(db, telegram_user_id)

    if action == "m":
        db.commit()
        if access.bot_role in {BOT_ROLE_OWNER, BOT_ROLE_ADMIN, BOT_ROLE_USER}:
            _send_employee_role_menu(db, chat_id_str, access)
            return {"ok": True, "action": "menu_shown", "bot_role": access.bot_role}
        _send_employee_bot_text(chat_id_str, EMPLOYEE_FORBIDDEN_TEXT)
        return {"ok": False, "reason": "forbidden"}

    if action in {"mb", "su", "suc"}:
        db.commit()
        if access.bot_role not in {BOT_ROLE_ADMIN, BOT_ROLE_USER}:
            _send_employee_bot_text(chat_id_str, EMPLOYEE_FORBIDDEN_TEXT)
            return {"ok": False, "reason": "forbidden"}
        if action == "mb":
            return _show_my_binding(db, chat_id_str, access)
        if action == "su":
            return _self_unbind_prompt(db, chat_id_str, access)
        return _self_unbind(db, chat_id_str, access)

    admin_actions = {"ad", "ac", "arp", "arc", "aa", "aas", "aac"}
    if action in admin_actions:
        db.commit()
        if not _require_admin_access(access):
            _send_employee_bot_text(chat_id_str, EMPLOYEE_FORBIDDEN_TEXT)
            return {"ok": False, "reason": "forbidden"}
        if action == "ad":
            return _show_admins(db, chat_id_str, int(args[0]))
        if action == "aa":
            return _show_admin_candidates(db, chat_id_str, int(args[0]))
        account_id = _account_id_from_ref(args[0])
        if account_id is None:
            db.rollback()
            return _invalid_callback(chat_id_str)
        if action == "ac":
            return _show_admin_card(db, chat_id_str, account_id)
        if action == "arp":
            return _admin_revoke_prompt(db, chat_id_str, account_id)
        if action == "arc":
            return _admin_revoke(db, chat_id_str, account_id)
        if action == "aas":
            return _admin_grant_prompt(db, chat_id_str, account_id)
        if action == "aac":
            return _admin_grant(db, chat_id_str, account_id)

    db.commit()
    if not _require_bindings_access(access):
        _send_employee_bot_text(chat_id_str, EMPLOYEE_FORBIDDEN_TEXT)
        return {"ok": False, "reason": "forbidden"}

    if action in {"nr", "bu", "bk"}:
        page = int(args[0])
        if action == "nr":
            return _show_new_requests(db, chat_id_str, page)
        if action == "bu":
            return _show_bound_users(db, chat_id_str, page)
        return _show_blocked_users(db, chat_id_str, page)

    account_id = _account_id_from_ref(args[0]) if args else None
    if action in {"rq", "rr", "rrc", "bd", "bp", "bc", "up", "uc", "kc", "kp", "kc2"} and account_id is None:
        db.rollback()
        return _invalid_callback(chat_id_str)
    if action == "rq":
        return _show_request_card(db, chat_id_str, account_id)
    if action == "rb":
        if account_id is None:
            db.rollback()
            return _invalid_callback(chat_id_str)
        return _show_bind_user_list(db, chat_id_str, account_id, int(args[1]))
    if action in {"rs", "rc"}:
        user_ref = args[1]
        if account_id is None:
            db.rollback()
            return _invalid_callback(chat_id_str)
        if action == "rs":
            return _show_bind_confirmation(db, chat_id_str, account_id, user_ref)
        return _approve_request(db, chat_id_str, account_id, user_ref)
    if action == "rr":
        return _reject_request_prompt(db, chat_id_str, account_id)
    if action == "rrc":
        return _reject_request(db, chat_id_str, account_id)
    if action == "bd":
        return _show_bound_card(db, chat_id_str, account_id)
    if action == "bp":
        return _block_prompt(db, chat_id_str, account_id)
    if action == "bc":
        return _block_account(db, chat_id_str, account_id)
    if action == "up":
        return _unbind_prompt(db, chat_id_str, account_id)
    if action == "uc":
        return _unbind_account(db, chat_id_str, account_id)
    if action == "kc":
        return _show_blocked_card(db, chat_id_str, account_id)
    if action == "kp":
        return _unblock_prompt(db, chat_id_str, account_id)
    if action == "kc2":
        return _unblock_account(db, chat_id_str, account_id)
    return _invalid_callback(chat_id_str)


def process_employee_telegram_update(db: Session, update: dict) -> dict:
    callback_query = update.get("callback_query")
    if isinstance(callback_query, dict):
        return _handle_callback_query(db, callback_query)

    message = update.get("message")
    if not isinstance(message, dict):
        return {"ok": True, "ignored": True, "reason": "unsupported_update"}

    message_text = _message_text(update)
    command = _command_name(message_text or "")
    if command != "/start" and message_text != EMPLOYEE_START_BUTTON_TEXT:
        return {"ok": True, "ignored": True, "reason": "unsupported_command"}

    return _handle_start_or_button(db, message, opened_via_start_command=(command == "/start"))
