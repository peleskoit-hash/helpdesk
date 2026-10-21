from __future__ import annotations

import hashlib
import re
import secrets
import uuid
from datetime import datetime, timedelta

from sqlalchemy import select, update
from sqlalchemy.orm import Session

from ..config import settings
from ..models import TelegramAutomationGroup, TelegramGroupRegistrationCode, User
from .telegram_bot import TelegramApiError, telegram_request

REGISTRATION_COMMAND = re.compile(r"^/register_group(?:@([A-Za-z0-9_]+))?(?:\s|$)")
CODE_TTL = timedelta(minutes=10)


def is_group_registration(message: dict) -> bool:
    return (
        (message.get("chat") or {}).get("type") in {"group", "supergroup"}
        and bool(REGISTRATION_COMMAND.match(message.get("text") or ""))
    )


def _request(method: str, payload: dict | None = None):
    response = telegram_request(method, payload, timeout=10)
    if not response.get("ok"):
        raise TelegramApiError("group_check_failed")
    return response.get("result")


def require_admin(db: Session, user_id: str) -> User:
    user = db.get(User, user_id)
    if user is None or not user.is_active or user.role != "admin":
        raise PermissionError("Доступно только действующему администратору ES DESK")
    return user


def _is_archive(chat_id: str) -> bool:
    return chat_id == str(settings.telegram_staging_chat_id or "").strip()


def create_registration_code(db: Session, user: User) -> dict:
    bot = _request("getMe")
    code = secrets.token_urlsafe(24)
    expires = datetime.utcnow() + CODE_TTL
    db.add(TelegramGroupRegistrationCode(
        id=str(uuid.uuid4()), code_hash=hashlib.sha256(code.encode()).hexdigest(),
        environment=settings.app_env, company_id=user.company_id, created_by_user_id=user.id,
        bot_id=str(bot["id"]), expires_at=expires,
    ))
    db.commit()
    return {"command": f"/register_group@{bot['username']} {code}", "expires_at": expires.isoformat() + "Z",
            "environment": settings.app_env}


def _check_group(chat_id: str, *, expected_bot_id: str, sender_id: str | None = None) -> tuple[dict, dict, str | None]:
    if _is_archive(chat_id):
        raise ValueError("archive_group_forbidden")
    bot = _request("getMe")
    if str(bot["id"]) != expected_bot_id:
        raise ValueError("wrong_bot")
    chat = _request("getChat", {"chat_id": chat_id})
    if chat.get("type") not in {"group", "supergroup"} or str(chat.get("id")) != chat_id:
        raise ValueError("group_required")
    if sender_id is not None:
        admins = _request("getChatAdministrators", {"chat_id": chat_id})
        if not any(str(member.get("user", {}).get("id")) == sender_id
                   and member.get("status") in {"creator", "administrator"} for member in admins):
            raise ValueError("group_admin_required")
    member = _request("getChatMember", {"chat_id": chat_id, "user_id": bot["id"]})
    if member.get("status") not in {"creator", "administrator", "member", "restricted"}:
        raise ValueError("bot_not_member")
    if member.get("status") == "restricted" and (
        not member.get("is_member") or not member.get("can_send_messages")
    ):
        raise ValueError("bot_cannot_send")
    sent = _request("sendMessage", {
        "chat_id": chat_id, "disable_notification": True,
        "text": f"ES DESK {settings.app_env.upper()}: проверка подключения группы. Пересылка обращений пока не включена.",
    })
    if not isinstance(sent, dict) or not sent.get("message_id"):
        raise ValueError("send_check_failed")
    cleanup_error = None
    try:
        _request("deleteMessage", {"chat_id": chat_id, "message_id": sent["message_id"]})
    except (TelegramApiError, ValueError):
        cleanup_error = "probe_cleanup_failed"
    return bot, chat, cleanup_error


def register_group(db: Session, message: dict) -> dict:
    result = {"ok": True, "group_registration": False}
    text = message.get("text") or ""
    parts = text.split()
    chat_id = str((message.get("chat") or {}).get("id", ""))
    if (len(parts) != 2 or message.get("sender_chat") or message.get("forward_origin")
            or not (message.get("from") or {}).get("id") or _is_archive(chat_id)):
        return {**result, "reason": "invalid_registration"}
    now = datetime.utcnow()
    row = db.scalar(select(TelegramGroupRegistrationCode).where(
        TelegramGroupRegistrationCode.code_hash == hashlib.sha256(parts[1].encode()).hexdigest(),
        TelegramGroupRegistrationCode.environment == settings.app_env,
        TelegramGroupRegistrationCode.used_at.is_(None),
        TelegramGroupRegistrationCode.expires_at > now,
    ).with_for_update())
    if row is None:
        return {**result, "reason": "invalid_or_expired_code"}
    try:
        admin = require_admin(db, row.created_by_user_id)
        if admin.company_id != row.company_id:
            raise PermissionError()
        bot = _request("getMe")
        target = REGISTRATION_COMMAND.match(text).group(1)
        if str(bot["id"]) != row.bot_id or (target and target.lower() != bot["username"].lower()):
            return {**result, "reason": "wrong_bot"}
        existing = db.scalar(select(TelegramAutomationGroup).where(
            TelegramAutomationGroup.environment == settings.app_env,
            TelegramAutomationGroup.bot_id == row.bot_id,
            TelegramAutomationGroup.chat_id == chat_id,
        ).with_for_update())
        if existing is not None and existing.company_id != row.company_id:
            return {**result, "reason": "group_unavailable"}
        _, chat, warning = _check_group(chat_id, expected_bot_id=row.bot_id, sender_id=str(message["from"]["id"]))
    except PermissionError:
        return {**result, "reason": "registration_admin_unavailable"}
    except (TelegramApiError, ValueError):
        return {**result, "reason": "group_verification_failed"}
    consumed = db.execute(update(TelegramGroupRegistrationCode).where(
        TelegramGroupRegistrationCode.id == row.id,
        TelegramGroupRegistrationCode.used_at.is_(None),
        TelegramGroupRegistrationCode.expires_at > datetime.utcnow(),
    ).values(used_at=datetime.utcnow()))
    if consumed.rowcount != 1:
        return {**result, "reason": "invalid_or_expired_code"}
    group = existing or TelegramAutomationGroup(
        id=str(uuid.uuid4()), company_id=row.company_id, environment=settings.app_env,
        bot_id=row.bot_id, chat_id=chat_id,
    )
    group.title = str(chat.get("title") or chat_id)[:255]
    group.status = "verified"
    group.checked_at = datetime.utcnow()
    group.last_error = warning
    db.add(group)
    db.flush()
    return {"ok": True, "group_registration": True, "group_id": group.id}


def group_for_admin(db: Session, user: User, group_id: str) -> TelegramAutomationGroup:
    group = db.scalar(select(TelegramAutomationGroup).where(
        TelegramAutomationGroup.id == group_id,
        TelegramAutomationGroup.company_id == user.company_id,
        TelegramAutomationGroup.environment == settings.app_env,
    ).with_for_update())
    if group is None or _is_archive(group.chat_id):
        raise ValueError("Группа недоступна")
    return group


def recheck_group(db: Session, group: TelegramAutomationGroup) -> None:
    try:
        _, chat, warning = _check_group(group.chat_id, expected_bot_id=group.bot_id)
        group.title = str(chat.get("title") or group.chat_id)[:255]
        group.status, group.last_error = "verified", warning
    except (TelegramApiError, ValueError):
        group.status, group.last_error = "unavailable", "group_verification_failed"
    group.checked_at = datetime.utcnow()


def group_out(group: TelegramAutomationGroup) -> dict:
    return {key: getattr(group, key) for key in (
        "id", "chat_id", "title", "registered_at", "checked_at", "status", "last_error", "environment",
    )}
