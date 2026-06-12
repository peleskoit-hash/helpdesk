import base64
import hashlib
import hmac
import os
import time
from dataclasses import dataclass

from fastapi import Depends, HTTPException, Request
from sqlalchemy import select
from sqlalchemy.orm import Session

from .config import settings
from .db import get_db
from .models import User


ADMIN_ROLE = "admin"
SUPPORT_ROLES = {"agent", "trainee"}
READONLY_CHIKI_ROLE = "chiki_piki"
KNOWN_ROLES = {ADMIN_ROLE, READONLY_CHIKI_ROLE, *SUPPORT_ROLES}

SESSION_USER_ID_KEY = "auth_user_id"
SESSION_LAST_ACTIVITY_KEY = "last_activity_ts"
SESSION_LOGIN_TS_KEY = "login_ts"
SESSION_IDLE_TIMEOUT_SECONDS = 60 * 60


@dataclass
class CurrentUser:
    id: str
    role: str
    real_user_id: str
    real_role: str
    is_real_admin: bool


def _auth_error(status_code: int, code: str, message: str):
    raise HTTPException(status_code=status_code, detail={"error": {"code": code, "message": message, "details": {}}})


def hash_password(password: str) -> str:
    salt = os.urandom(16)
    iterations = 260_000
    digest = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, iterations)
    salt_b64 = base64.b64encode(salt).decode("ascii")
    digest_b64 = base64.b64encode(digest).decode("ascii")
    return f"pbkdf2_sha256${iterations}${salt_b64}${digest_b64}"


def verify_password(password: str, password_hash: str | None) -> bool:
    if not password_hash:
        return False
    try:
        algorithm, iterations_raw, salt_b64, digest_b64 = password_hash.split("$", 3)
        if algorithm != "pbkdf2_sha256":
            return False
        iterations = int(iterations_raw)
        salt = base64.b64decode(salt_b64.encode("ascii"))
        expected = base64.b64decode(digest_b64.encode("ascii"))
    except Exception:
        return False
    actual = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, iterations)
    return hmac.compare_digest(actual, expected)


def authenticate_user(db: Session, login: str, password: str) -> User | None:
    user = db.execute(select(User).where(User.login == login.strip())).scalar_one_or_none()
    if user is None or not user.is_active:
        return None
    if not verify_password(password, user.password_hash):
        return None
    return user


def login_user(request: Request, user: User) -> None:
    request.session.clear()
    now_ts = int(time.time())
    request.session[SESSION_USER_ID_KEY] = user.id
    request.session[SESSION_LAST_ACTIVITY_KEY] = now_ts
    request.session[SESSION_LOGIN_TS_KEY] = now_ts


def logout_user(request: Request) -> None:
    request.session.clear()


def _get_user_by_id(db: Session, user_id: str | None) -> User | None:
    if not user_id:
        return None
    return db.execute(select(User).where(User.id == user_id)).scalar_one_or_none()


def _enforce_activity_timeout(request: Request, real_user: User) -> None:
    if real_user.role == ADMIN_ROLE and settings.disable_admin_idle_timeout:
        request.session[SESSION_LAST_ACTIVITY_KEY] = int(time.time())
        return

    last_activity = int(request.session.get(SESSION_LAST_ACTIVITY_KEY) or 0)
    now = int(time.time())
    if not last_activity:
        request.session[SESSION_LAST_ACTIVITY_KEY] = now
        return
    if now - last_activity > SESSION_IDLE_TIMEOUT_SECONDS:
        request.session.clear()
        _auth_error(401, "session_expired", "Сессия истекла. Выполните вход заново.")
    request.session[SESSION_LAST_ACTIVITY_KEY] = now


def _resolve_effective_user(request: Request, db: Session, real_user: User) -> User:
    # TODO(remove-before-production): admin test-mode override is temporarily disabled.
    # Effective context must always be equal to the real authenticated user.
    return real_user


def get_current_user(request: Request, db: Session = Depends(get_db)) -> CurrentUser:
    real_user_id = request.session.get(SESSION_USER_ID_KEY)
    if not real_user_id:
        _auth_error(401, "unauthorized", "Требуется авторизация")

    real_user = _get_user_by_id(db, real_user_id)
    if real_user is None or not real_user.is_active:
        request.session.clear()
        _auth_error(401, "unauthorized", "Пользователь недоступен. Выполните вход заново.")
    if real_user.role not in KNOWN_ROLES:
        request.session.clear()
        _auth_error(401, "invalid_role", "Недопустимая роль пользователя")

    _enforce_activity_timeout(request, real_user)
    effective_user = _resolve_effective_user(request, db, real_user)

    return CurrentUser(
        id=effective_user.id,
        role=effective_user.role,
        real_user_id=real_user.id,
        real_role=real_user.role,
        is_real_admin=real_user.role == ADMIN_ROLE,
    )


def get_optional_current_user(request: Request, db: Session) -> CurrentUser | None:
    try:
        return get_current_user(request, db)
    except HTTPException:
        return None


def is_admin(user: CurrentUser) -> bool:
    return user.role == ADMIN_ROLE


def is_support_user(user: CurrentUser) -> bool:
    return user.role in SUPPORT_ROLES


def is_readonly_chiki_piki(user: CurrentUser) -> bool:
    return user.role == READONLY_CHIKI_ROLE


def can_manage_ticket(user: CurrentUser) -> bool:
    return is_admin(user) or is_support_user(user)


def can_reply(user: CurrentUser) -> bool:
    return is_admin(user) or is_support_user(user)


def can_comment(user: CurrentUser) -> bool:
    return is_admin(user) or is_support_user(user)


def can_assign(user: CurrentUser) -> bool:
    return is_admin(user)


def can_edit_client_profile(user: CurrentUser) -> bool:
    return is_admin(user) or is_support_user(user)


def can_edit_messages(user: CurrentUser) -> bool:
    return is_admin(user) or is_support_user(user)
