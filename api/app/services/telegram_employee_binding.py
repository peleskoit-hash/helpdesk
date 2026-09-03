import uuid
from datetime import datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..models import TelegramNotificationAccount, User

REQUEST_STATUS_PENDING = "pending"
REQUEST_STATUS_APPROVED = "approved"
UNSET = object()


class TelegramBindingOperationError(ValueError):
    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code
        self.message = message


def notification_account_by_telegram_user_id(
    db: Session,
    telegram_user_id: str | int,
) -> TelegramNotificationAccount | None:
    return db.execute(
        select(TelegramNotificationAccount).where(
            TelegramNotificationAccount.telegram_user_id == str(telegram_user_id)
        )
    ).scalar_one_or_none()


def is_blocked_approved_binding(account: TelegramNotificationAccount | None) -> bool:
    return bool(
        account is not None
        and account.request_status == REQUEST_STATUS_APPROVED
        and account.bound_user_id
        and account.is_blocked
    )


def touch_notification_interaction(
    db: Session,
    *,
    telegram_user_id: str | int,
    telegram_chat_id: str | int | None | object = UNSET,
    telegram_username: str | None | object = UNSET,
    telegram_display_name: str | None | object = UNSET,
) -> TelegramNotificationAccount | None:
    account = notification_account_by_telegram_user_id(db, telegram_user_id)
    if account is None:
        return None

    is_blocked_binding = is_blocked_approved_binding(account)
    if telegram_chat_id is not UNSET:
        account.telegram_chat_id = str(telegram_chat_id) if telegram_chat_id is not None else None
    if telegram_username is not UNSET:
        account.telegram_username = telegram_username or None
    if telegram_display_name is not UNSET:
        account.telegram_display_name = telegram_display_name or None
    if (
        account.request_status == REQUEST_STATUS_APPROVED
        and account.bound_user_id
        and not account.is_blocked
        and account.telegram_chat_id
    ):
        account.delivery_available = True
    if not is_blocked_binding:
        account.updated_at = datetime.utcnow()
    db.flush()
    return account


def list_pending_requests(db: Session) -> list[TelegramNotificationAccount]:
    return db.execute(
        select(TelegramNotificationAccount)
        .where(TelegramNotificationAccount.bound_user_id.is_(None))
        .where(TelegramNotificationAccount.request_status == REQUEST_STATUS_PENDING)
        .where(TelegramNotificationAccount.is_blocked.is_(False))
        .order_by(TelegramNotificationAccount.created_at.asc(), TelegramNotificationAccount.id.asc())
    ).scalars().all()


def create_or_update_pending_request(
    db: Session,
    *,
    telegram_user_id: str,
    telegram_chat_id: str | None = None,
    telegram_username: str | None = None,
    telegram_display_name: str | None = None,
) -> TelegramNotificationAccount:
    now = datetime.utcnow()
    account = notification_account_by_telegram_user_id(db, telegram_user_id)

    if account is None:
        account = TelegramNotificationAccount(
            id=str(uuid.uuid4()),
            telegram_user_id=str(telegram_user_id),
            telegram_chat_id=str(telegram_chat_id) if telegram_chat_id is not None else None,
            telegram_username=(telegram_username or None),
            telegram_display_name=(telegram_display_name or None),
            bound_user_id=None,
            request_status=REQUEST_STATUS_PENDING,
            delivery_available=False,
            is_blocked=False,
            created_at=now,
            updated_at=now,
        )
        db.add(account)
        db.flush()
        return account

    if account.request_status == REQUEST_STATUS_APPROVED and account.bound_user_id:
        if telegram_chat_id is not None:
            account.telegram_chat_id = str(telegram_chat_id)
        if telegram_username is not None:
            account.telegram_username = telegram_username or None
        if telegram_display_name is not None:
            account.telegram_display_name = telegram_display_name or None
        account.updated_at = now
        db.flush()
        return account

    account.request_status = REQUEST_STATUS_PENDING
    account.bound_user_id = None
    account.delivery_available = False
    account.is_blocked = False
    account.is_notification_bot_admin = False
    if telegram_chat_id is not None:
        account.telegram_chat_id = str(telegram_chat_id)
    if telegram_username is not None:
        account.telegram_username = telegram_username or None
    if telegram_display_name is not None:
        account.telegram_display_name = telegram_display_name or None
    account.updated_at = now
    db.flush()
    return account


def approve_binding(
    db: Session,
    *,
    user_id: str,
    telegram_user_id: str,
) -> TelegramNotificationAccount | None:
    account = db.execute(
        select(TelegramNotificationAccount).where(
            TelegramNotificationAccount.telegram_user_id == str(telegram_user_id)
        )
    ).scalar_one_or_none()
    if account is None:
        return None
    if (
        account.request_status != REQUEST_STATUS_PENDING
        or account.bound_user_id is not None
        or account.is_blocked
    ):
        raise TelegramBindingOperationError(
            "telegram_binding_request_not_pending",
            "Telegram-запрос должен быть новым и не привязанным",
        )
    user = db.get(User, user_id)
    if user is None:
        raise TelegramBindingOperationError(
            "telegram_binding_user_not_found",
            "Сотрудник для привязки не найден",
        )
    if not user.is_active:
        raise TelegramBindingOperationError(
            "telegram_binding_user_inactive",
            "Нельзя привязать Telegram к неактивному сотруднику",
        )

    now = datetime.utcnow()
    existing_for_user = db.execute(
        select(TelegramNotificationAccount).where(
            TelegramNotificationAccount.bound_user_id == user_id,
            TelegramNotificationAccount.id != account.id,
        )
    ).scalar_one_or_none()
    if existing_for_user is not None:
        raise TelegramBindingOperationError(
            "telegram_user_already_bound",
            "У выбранного сотрудника уже есть Telegram-привязка",
        )

    account.bound_user_id = user_id
    account.request_status = REQUEST_STATUS_APPROVED
    account.delivery_available = bool(account.telegram_chat_id) and not account.is_blocked
    account.updated_at = now
    db.flush()
    return account


def reject_binding(
    db: Session,
    *,
    telegram_user_id: str,
) -> TelegramNotificationAccount | None:
    account = db.execute(
        select(TelegramNotificationAccount).where(
            TelegramNotificationAccount.telegram_user_id == str(telegram_user_id)
        )
    ).scalar_one_or_none()
    if account is None:
        return None
    if (
        account.request_status != REQUEST_STATUS_PENDING
        or account.bound_user_id is not None
        or account.is_blocked
    ):
        raise TelegramBindingOperationError(
            "telegram_binding_request_not_pending",
            "Отклонить можно только новый Telegram-запрос",
        )

    db.delete(account)
    db.flush()
    return account


def unbind_employee(
    db: Session,
    *,
    user_id: str,
) -> TelegramNotificationAccount | None:
    account = db.execute(
        select(TelegramNotificationAccount).where(
            TelegramNotificationAccount.bound_user_id == user_id
        )
    ).scalar_one_or_none()
    if account is None:
        return None
    if account.request_status != REQUEST_STATUS_APPROVED or not account.bound_user_id:
        raise TelegramBindingOperationError(
            "telegram_binding_not_active",
            "Отвязать можно только существующую Telegram-привязку",
        )

    db.delete(account)
    db.flush()
    return account


def block_account(
    db: Session,
    *,
    telegram_user_id: str,
) -> TelegramNotificationAccount | None:
    account = db.execute(
        select(TelegramNotificationAccount).where(
            TelegramNotificationAccount.telegram_user_id == str(telegram_user_id)
        )
    ).scalar_one_or_none()
    if account is None:
        return None
    if account.request_status != REQUEST_STATUS_APPROVED or not account.bound_user_id or account.is_blocked:
        raise TelegramBindingOperationError(
            "telegram_binding_not_active",
            "Заблокировать можно только активную Telegram-привязку",
        )

    account.is_blocked = True
    account.delivery_available = False
    account.updated_at = datetime.utcnow()
    db.flush()
    return account


def unblock_account(
    db: Session,
    *,
    telegram_user_id: str,
) -> TelegramNotificationAccount | None:
    account = db.execute(
        select(TelegramNotificationAccount).where(
            TelegramNotificationAccount.telegram_user_id == str(telegram_user_id)
        )
    ).scalar_one_or_none()
    if account is None:
        return None
    if account.request_status != REQUEST_STATUS_APPROVED or not account.bound_user_id or not account.is_blocked:
        raise TelegramBindingOperationError(
            "telegram_binding_not_blocked",
            "Разблокировать можно только заблокированную Telegram-привязку",
        )

    account.is_blocked = False
    account.delivery_available = bool(account.telegram_chat_id)
    account.updated_at = datetime.utcnow()
    db.flush()
    return account
