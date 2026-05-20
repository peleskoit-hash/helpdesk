import uuid
from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..auth import CurrentUser, get_current_user, hash_password, is_admin
from ..db import get_db
from ..models import Company, User
from ..schemas import SettingsUserCreate, SettingsUserOut, SettingsUserUpdate


router = APIRouter(prefix="/api/v1/settings", tags=["settings"])

ROLE_LABELS = {
    "admin": "Администратор",
    "agent": "Сотрудник",
    "trainee": "Стажёр",
    "chiki_piki": "Чики-пики",
}


def _error(status_code: int, code: str, message: str):
    raise HTTPException(status_code=status_code, detail={"error": {"code": code, "message": message, "details": {}}})


def _require_admin(user: CurrentUser) -> None:
    if not is_admin(user):
        _error(403, "admin_only", "Настройки доступны только администратору")


def role_label(role: str) -> str:
    return ROLE_LABELS.get(role, role)


def _company_id(db: Session) -> str:
    company_id = db.execute(select(Company.id).order_by(Company.created_at.asc()).limit(1)).scalar_one_or_none()
    if company_id is None:
        _error(500, "company_not_found", "Компания не найдена")
    return company_id


def _user_out(user: User) -> SettingsUserOut:
    return SettingsUserOut(
        id=user.id,
        full_name=user.full_name,
        login=user.login,
        role=user.role,
        role_label=role_label(user.role),
        is_active=bool(user.is_active),
        availability_status=user.availability_status or "offline",
        legacy_ticket_count=int(user.legacy_ticket_count or 0),
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
    return [_user_out(item) for item in users]


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
    target.updated_at = datetime.utcnow()

    db.commit()
    db.refresh(target)
    return _user_out(target)
