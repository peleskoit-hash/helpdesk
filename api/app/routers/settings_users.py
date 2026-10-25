import uuid
from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..auth import CurrentUser, get_current_user, hash_password, is_admin
from ..db import get_db
from ..models import Company, TelegramNotificationAccount, User
from ..schemas import SettingsUserCreate, SettingsUserOut, SettingsUserUpdate
from ..services import telegram_employee_binding
from ..services.statistics_lifecycle import initialize_user_status_history, sync_user_status_history_after_profile_update
from ..services.telegram_employee_binding import TelegramBindingOperationError


router = APIRouter(prefix="/api/v1/settings", tags=["settings"])

ROLE_LABELS = {
    "admin": "Администратор",
    "agent": "Тех. поддержка",
    "trainee": "Стажёр",
    "chiki_piki": "Чики-пики",
}


class TelegramBindingPendingOut(BaseModel):
    telegram_user_id: str
    telegram_chat_id: str | None = None
    telegram_username: str | None = None
    telegram_display_name: str | None = None
    bound_user_id: str | None = None
    request_status: str
    delivery_available: bool
    created_at: datetime | None = None
    updated_at: datetime | None = None


class TelegramBindingApprovePayload(BaseModel):
    user_id: str


class TelegramBindingAccountOut(BaseModel):
    telegram_user_id: str
    telegram_chat_id: str | None = None
    telegram_username: str | None = None
    telegram_display_name: str | None = None
    bound_user_id: str | None = None
    request_status: str
    delivery_available: bool
    created_at: datetime | None = None
    updated_at: datetime | None = None


def _error(status_code: int, code: str, message: str):
    raise HTTPException(status_code=status_code, detail={"error": {"code": code, "message": message, "details": {}}})


def _require_admin(user: CurrentUser) -> None:
    if not is_admin(user):
        _error(403, "admin_only", "Настройки доступны только администратору")


def _binding_operation_error(exc: TelegramBindingOperationError):
    _error(409, exc.code, exc.message)


def role_label(role: str) -> str:
    return ROLE_LABELS.get(role, role)


def _company_id(db: Session) -> str:
    company_id = db.execute(select(Company.id).order_by(Company.created_at.asc()).limit(1)).scalar_one_or_none()
    if company_id is None:
        _error(500, "company_not_found", "Компания не найдена")
    return company_id


def _user_out(user: User) -> SettingsUserOut:
    return _user_out_with_notification(user, None)


def _telegram_binding_account_out(account: TelegramNotificationAccount) -> TelegramBindingAccountOut:
    return TelegramBindingAccountOut(
        telegram_user_id=account.telegram_user_id,
        telegram_chat_id=account.telegram_chat_id,
        telegram_username=account.telegram_username,
        telegram_display_name=account.telegram_display_name,
        bound_user_id=account.bound_user_id,
        request_status=account.request_status,
        delivery_available=bool(account.delivery_available),
        created_at=account.created_at,
        updated_at=account.updated_at,
    )


def _telegram_notification_display(account: TelegramNotificationAccount) -> str:
    username = (account.telegram_username or "").strip()
    if username:
        return f"@{username} (Tg ID: {account.telegram_user_id})"
    return f"Без username (Tg ID: {account.telegram_user_id})"


def _user_out_with_notification(
    user: User,
    notification_account: TelegramNotificationAccount | None,
) -> SettingsUserOut:
    return SettingsUserOut(
        id=user.id,
        full_name=user.full_name,
        login=user.login,
        role=user.role,
        role_label=role_label(user.role),
        is_active=bool(user.is_active),
        availability_status=user.availability_status or "offline",
        legacy_ticket_count=int(user.legacy_ticket_count or 0),
        telegram_notification_bound=notification_account is not None,
        telegram_notification_display=_telegram_notification_display(notification_account) if notification_account else None,
        last_login_at=user.last_login_at,
        created_at=user.created_at,
        updated_at=user.updated_at,
    )


def _ensure_unique_login(db: Session, login: str, exclude_user_id: str | None = None) -> None:
    stmt = select(User.id).where(User.login == login)
    if exclude_user_id:
        stmt = stmt.where(User.id != exclude_user_id)
    if db.execute(stmt).first() is not None:
        _error(409, "login_already_exists", "Пользователь с таким login уже существует")


@router.get("/users", response_model=list[SettingsUserOut])
def list_users(db: Session = Depends(get_db), user: CurrentUser = Depends(get_current_user)):
    _require_admin(user)
    users = db.execute(select(User).order_by(User.full_name.asc())).scalars().all()
    user_ids = [item.id for item in users]
    accounts_by_user_id: dict[str, TelegramNotificationAccount] = {}
    if user_ids:
        accounts = db.execute(
            select(TelegramNotificationAccount).where(TelegramNotificationAccount.bound_user_id.in_(user_ids))
        ).scalars().all()
        accounts_by_user_id = {
            item.bound_user_id: item
            for item in accounts
            if item.bound_user_id
        }
    return [_user_out_with_notification(item, accounts_by_user_id.get(item.id)) for item in users]


@router.get("/users/telegram-binding-requests", response_model=list[TelegramBindingPendingOut])
def list_telegram_binding_requests(db: Session = Depends(get_db), user: CurrentUser = Depends(get_current_user)):
    _require_admin(user)
    accounts = telegram_employee_binding.list_pending_requests(db)
    return [
        TelegramBindingPendingOut(
            telegram_user_id=account.telegram_user_id,
            telegram_chat_id=account.telegram_chat_id,
            telegram_username=account.telegram_username,
            telegram_display_name=account.telegram_display_name,
            bound_user_id=account.bound_user_id,
            request_status=account.request_status,
            delivery_available=bool(account.delivery_available),
            created_at=account.created_at,
            updated_at=account.updated_at,
        )
        for account in accounts
    ]


@router.post("/users/telegram-binding-requests/{telegram_user_id}/approve", response_model=TelegramBindingAccountOut)
def approve_telegram_binding_request(
    telegram_user_id: str,
    payload: TelegramBindingApprovePayload,
    db: Session = Depends(get_db),
    user: CurrentUser = Depends(get_current_user),
):
    _require_admin(user)
    target_user = db.get(User, payload.user_id)
    if target_user is None:
        _error(404, "user_not_found", "Пользователь не найден")

    try:
        account = telegram_employee_binding.approve_binding(
            db,
            user_id=target_user.id,
            telegram_user_id=telegram_user_id,
        )
    except TelegramBindingOperationError as exc:
        db.rollback()
        _binding_operation_error(exc)
    if account is None:
        _error(404, "telegram_binding_request_not_found", "Telegram-запрос на привязку не найден")

    db.commit()
    db.refresh(account)
    return _telegram_binding_account_out(account)


@router.post("/users/telegram-binding-requests/{telegram_user_id}/reject", response_model=TelegramBindingAccountOut)
def reject_telegram_binding_request(
    telegram_user_id: str,
    db: Session = Depends(get_db),
    user: CurrentUser = Depends(get_current_user),
):
    _require_admin(user)
    try:
        account = telegram_employee_binding.reject_binding(db, telegram_user_id=telegram_user_id)
    except TelegramBindingOperationError as exc:
        db.rollback()
        _binding_operation_error(exc)
    if account is None:
        _error(404, "telegram_binding_request_not_found", "Telegram-запрос на привязку не найден")

    response = _telegram_binding_account_out(account)
    db.commit()
    return response


@router.delete("/users/{user_id}/telegram-binding", response_model=TelegramBindingAccountOut)
def unbind_user_telegram(
    user_id: str,
    db: Session = Depends(get_db),
    user: CurrentUser = Depends(get_current_user),
):
    _require_admin(user)
    target_user = db.get(User, user_id)
    if target_user is None:
        _error(404, "user_not_found", "Пользователь не найден")

    try:
        account = telegram_employee_binding.unbind_employee(db, user_id=user_id)
    except TelegramBindingOperationError as exc:
        db.rollback()
        _binding_operation_error(exc)
    if account is None:
        _error(404, "telegram_binding_not_found", "У сотрудника нет Telegram-привязки")

    response = _telegram_binding_account_out(account)
    db.commit()
    return response


@router.post("/users", response_model=SettingsUserOut)
def create_user(payload: SettingsUserCreate, db: Session = Depends(get_db), user: CurrentUser = Depends(get_current_user)):
    _require_admin(user)
    login = payload.login.strip()
    full_name = payload.full_name.strip()
    if not login:
        _error(422, "login_required", "Login обязателен")
    if not full_name:
        _error(422, "full_name_required", "ФИО обязательно")
    _ensure_unique_login(db, login)

    now = datetime.utcnow()
    new_user = User(
        id=str(uuid.uuid4()),
        company_id=_company_id(db),
        email=f"{login}@local.helpdesk",
        login=login,
        password_hash=hash_password(payload.password),
        full_name=full_name,
        role=payload.role,
        is_active=payload.is_active,
        availability_status=payload.availability_status,
        legacy_ticket_count=max(0, int(payload.legacy_ticket_count or 0)),
        created_at=now,
        updated_at=now,
    )
    db.add(new_user)
    initialize_user_status_history(db, new_user, now=now)
    db.commit()
    db.refresh(new_user)
    return _user_out(new_user)


@router.patch("/users/{user_id}", response_model=SettingsUserOut)
def update_user(
    user_id: str,
    payload: SettingsUserUpdate,
    db: Session = Depends(get_db),
    user: CurrentUser = Depends(get_current_user),
):
    _require_admin(user)
    target = db.get(User, user_id)
    if target is None:
        _error(404, "user_not_found", "Пользователь не найден")

    previous_role = target.role
    previous_is_active = target.is_active
    previous_availability_status = target.availability_status
    now = datetime.utcnow()
    if payload.full_name is not None:
        full_name = payload.full_name.strip()
        if not full_name:
            _error(422, "full_name_required", "ФИО обязательно")
        target.full_name = full_name
    if payload.login is not None:
        login = payload.login.strip()
        if not login:
            _error(422, "login_required", "Login обязателен")
        _ensure_unique_login(db, login, exclude_user_id=target.id)
        target.login = login
        target.email = f"{login}@local.helpdesk"
    if payload.password is not None and payload.password.strip():
        target.password_hash = hash_password(payload.password)
    if payload.role is not None:
        target.role = payload.role
    if payload.is_active is not None:
        target.is_active = payload.is_active
    if payload.availability_status is not None:
        target.availability_status = payload.availability_status
    if payload.legacy_ticket_count is not None:
        target.legacy_ticket_count = max(0, int(payload.legacy_ticket_count))
    sync_user_status_history_after_profile_update(
        db,
        target,
        previous_role=previous_role,
        previous_is_active=previous_is_active,
        previous_availability_status=previous_availability_status,
        now=now,
    )
    target.updated_at = now

    db.commit()
    db.refresh(target)
    notification_account = db.execute(
        select(TelegramNotificationAccount).where(TelegramNotificationAccount.bound_user_id == target.id)
    ).scalar_one_or_none()
    return _user_out_with_notification(target, notification_account)
