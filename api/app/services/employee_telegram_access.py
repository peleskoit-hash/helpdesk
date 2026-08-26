from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ..config import settings
from ..models import TelegramNotificationAccount, User
from .telegram_employee_binding import REQUEST_STATUS_APPROVED
from .telegram_employee_binding import TelegramBindingOperationError

BOT_ROLE_OWNER = "owner"
BOT_ROLE_ADMIN = "admin"
BOT_ROLE_USER = "user"
BOT_ROLE_NONE = "none"


@dataclass(frozen=True)
class EmployeeTelegramAccess:
    telegram_user_id: str
    bot_role: str
    account: TelegramNotificationAccount | None

    @property
    def is_owner(self) -> bool:
        return self.bot_role == BOT_ROLE_OWNER

    @property
    def is_additional_admin(self) -> bool:
        return self.bot_role == BOT_ROLE_ADMIN

    @property
    def is_bound_user(self) -> bool:
        return self.bot_role in {BOT_ROLE_ADMIN, BOT_ROLE_USER}

    @property
    def can_manage_bindings(self) -> bool:
        return self.bot_role in {BOT_ROLE_OWNER, BOT_ROLE_ADMIN}

    @property
    def can_manage_admins(self) -> bool:
        return self.bot_role == BOT_ROLE_OWNER


def notification_bot_owner_id() -> str | None:
    owner_id = str(settings.telegram_notification_bot_owner_id or "").strip()
    return owner_id or None


def is_notification_bot_owner(telegram_user_id: str | int | None) -> bool:
    owner_id = notification_bot_owner_id()
    if owner_id is None:
        return False
    return str(telegram_user_id or "") == owner_id


def notification_account_by_telegram_user_id(
    db: Session,
    telegram_user_id: str | int,
) -> TelegramNotificationAccount | None:
    return db.execute(
        select(TelegramNotificationAccount).where(
            TelegramNotificationAccount.telegram_user_id == str(telegram_user_id)
        )
    ).scalar_one_or_none()


def resolve_employee_telegram_access(
    db: Session,
    telegram_user_id: str | int,
) -> EmployeeTelegramAccess:
    telegram_user_id_str = str(telegram_user_id)
    if is_notification_bot_owner(telegram_user_id_str):
        return EmployeeTelegramAccess(
            telegram_user_id=telegram_user_id_str,
            bot_role=BOT_ROLE_OWNER,
            account=None,
        )

    account = notification_account_by_telegram_user_id(db, telegram_user_id_str)
    if (
        account is not None
        and not account.is_blocked
        and account.bound_user_id
        and account.request_status == REQUEST_STATUS_APPROVED
    ):
        bound_user = db.get(User, account.bound_user_id)
        bound_user_is_active = bool(bound_user is not None and bound_user.is_active)
        if account.is_notification_bot_admin:
            if bound_user_is_active:
                return EmployeeTelegramAccess(
                    telegram_user_id=telegram_user_id_str,
                    bot_role=BOT_ROLE_ADMIN,
                    account=account,
                )
            return EmployeeTelegramAccess(
                telegram_user_id=telegram_user_id_str,
                bot_role=BOT_ROLE_USER,
                account=account,
            )
        return EmployeeTelegramAccess(
            telegram_user_id=telegram_user_id_str,
            bot_role=BOT_ROLE_USER,
            account=account,
        )

    return EmployeeTelegramAccess(
        telegram_user_id=telegram_user_id_str,
        bot_role=BOT_ROLE_NONE,
        account=account,
    )


def is_notification_bot_additional_admin(
    db: Session,
    telegram_user_id: str | int,
) -> bool:
    return resolve_employee_telegram_access(db, telegram_user_id).is_additional_admin


def can_manage_notification_bindings(
    db: Session,
    telegram_user_id: str | int,
) -> bool:
    return resolve_employee_telegram_access(db, telegram_user_id).can_manage_bindings


def can_manage_notification_admins(
    db: Session,
    telegram_user_id: str | int,
) -> bool:
    return resolve_employee_telegram_access(db, telegram_user_id).can_manage_admins


def count_pending_notification_requests(db: Session) -> int:
    return int(
        db.execute(
            select(func.count())
            .select_from(TelegramNotificationAccount)
            .where(TelegramNotificationAccount.request_status == "pending")
            .where(TelegramNotificationAccount.bound_user_id.is_(None))
            .where(TelegramNotificationAccount.is_blocked.is_(False))
        ).scalar_one()
        or 0
    )


def grant_notification_bot_admin(
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
    user = db.get(User, user_id)
    if (
        account.request_status != REQUEST_STATUS_APPROVED
        or not account.bound_user_id
        or account.is_blocked
        or user is None
        or not user.is_active
        or account.is_notification_bot_admin
    ):
        raise TelegramBindingOperationError(
            "notification_bot_admin_not_allowed",
            "Назначить администратором можно только активного сотрудника с активной Telegram-привязкой",
        )
    account.is_notification_bot_admin = True
    db.flush()
    return account


def revoke_notification_bot_admin(
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
    if not account.is_notification_bot_admin:
        raise TelegramBindingOperationError(
            "notification_bot_admin_not_found",
            "У сотрудника нет прав администратора Notification Bot",
        )
    account.is_notification_bot_admin = False
    db.flush()
    return account
