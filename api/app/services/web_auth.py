"""Independent, database-backed client authentication. Times are stored in UTC."""
import hashlib
import math
import secrets
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from urllib.parse import urlsplit
from zoneinfo import ZoneInfo

from fastapi import Depends, HTTPException, Request
from sqlalchemy import select, update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.orm import Session

from ..auth import hash_password, verify_password
from ..config import settings
from ..db import get_db
from ..models import (Client, ClientProfile, TelegramContact, Ticket, TicketStatus,
                      TicketTaskKind, TicketWorkflowType, WebClientSession, WebLoginLimit)
from .web_credentials import normalize_login

COOKIE_NAME = "esdesk_web_session"
COOKIE_PATH = "/api/v1/web"
MOSCOW = ZoneInfo("Europe/Moscow")
_DUMMY_HASH = hash_password(secrets.token_urlsafe(32))


def utcnow():
    return datetime.now(timezone.utc)


def as_utc(value):
    # SQLite returns naive values even for timezone=True columns.
    return value.replace(tzinfo=timezone.utc) if value.tzinfo is None else value.astimezone(timezone.utc)


def normal_expiry(now):
    local = now.astimezone(MOSCOW)
    cutoff = local.replace(hour=20, minute=0, second=0, microsecond=0)
    if local >= cutoff:
        cutoff += timedelta(days=1)
    return cutoff.astimezone(timezone.utc)


def token_hash(token):
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def fail(status=401, code="web_unauthorized", headers=None):
    raise HTTPException(status, detail={"error": {"code": code,
                        "message": "Требуется WEB-авторизация", "details": {}}}, headers=headers)


def _origin(value):
    try:
        parsed = urlsplit(value)
        if parsed.scheme not in {"http", "https"} or not parsed.hostname or parsed.username or parsed.password:
            return None
        return parsed.scheme, parsed.hostname.lower(), parsed.port or (443 if parsed.scheme == "https" else 80)
    except ValueError:
        return None


def require_web_csrf(request: Request):
    """Require exact same Origin on every unsafe browser request, including login.

    APP_PUBLIC_BASE_URL supplies the public origin behind a reverse proxy.
    Forwarded headers are deliberately not parsed here.
    """
    if request.method in {"GET", "HEAD", "OPTIONS"}:
        return
    expected = _origin(settings.app_public_base_url or str(request.base_url))
    if (request.headers.get("sec-fetch-site") == "cross-site" or expected is None
            or _origin(request.headers.get("origin", "")) != expected):
        fail(403, "web_csrf_failed")


def eligible_profile(db, profile):
    if profile is None or not profile.is_verified or not profile.web_login_normalized or not profile.web_password_hash:
        return False
    # A verified imported profile alone is not a Telegram client identity.
    return db.scalar(select(TelegramContact.id).join(Client, Client.id == TelegramContact.client_id).where(
        TelegramContact.client_id == profile.client_id,
        TelegramContact.company_id == Client.company_id,
        TelegramContact.external_user_id != "",
    ).limit(1)) is not None


def login(db: Session, login_value: str, password: str, source_ip: str):
    normalized = normalize_login(login_value) or ""
    # The upsert acquires a row lock in PostgreSQL and a write lock in SQLite,
    # including the first concurrent attempt for a previously unseen pair.
    table = WebLoginLimit.__table__
    insert = sqlite_insert if db.get_bind().dialect.name == "sqlite" else pg_insert
    statement = insert(table).values(login_normalized=normalized, source_ip=source_ip, failure_count=0)
    db.execute(statement.on_conflict_do_update(
        index_elements=[table.c.login_normalized, table.c.source_ip],
        set_={"failure_count": table.c.failure_count},
    ))
    limit = db.scalar(select(WebLoginLimit).where(
        WebLoginLimit.login_normalized == normalized, WebLoginLimit.source_ip == source_ip,
    ).execution_options(populate_existing=True))
    now = utcnow()  # Read after acquiring the lock, so waiting attempts use current time.
    if limit.blocked_until and now < as_utc(limit.blocked_until):
        retry = math.ceil((as_utc(limit.blocked_until) - now).total_seconds())
        db.rollback()
        fail(429, "web_login_limited", {"Retry-After": str(retry)})
    if limit.last_failure_at:
        delay = timedelta(seconds=30 * 2 ** min(limit.failure_count - 1, 8))
        if now >= as_utc(limit.last_failure_at) + 3 * delay:
            limit.failure_count = 0
    profile = db.scalar(select(ClientProfile).where(ClientProfile.web_login_normalized == normalized))
    # Unknown and ineligible identities still execute PBKDF2; never decrypt Socials.
    eligible = eligible_profile(db, profile)
    valid_password = verify_password(password, profile.web_password_hash if eligible else _DUMMY_HASH)
    if not eligible or not valid_password:
        limit.failure_count = min(limit.failure_count + 1, 9)
        delay_seconds = 30 * 2 ** (limit.failure_count - 1)
        limit.last_failure_at = now
        limit.blocked_until = now + timedelta(seconds=delay_seconds)
        db.commit()
        fail(401, "web_invalid_credentials", {"Retry-After": str(delay_seconds)})
    limit.failure_count = 0
    limit.last_failure_at = limit.blocked_until = None
    token = secrets.token_urlsafe(32)
    session = WebClientSession(token_hash=token_hash(token), client_id=profile.client_id,
                               credentials_version=profile.web_credentials_version,
                               created_at=now, normal_expires_at=normal_expiry(now), last_activity_at=now)
    db.add(session)
    db.commit()
    return token, session.client_id


@dataclass(frozen=True)
class CurrentWebClient:
    id: str
    session_hash: str


def has_open_web_ticket(db, client_id):
    return db.scalar(select(Ticket.id).where(
        Ticket.client_id == client_id, Ticket.channel == "web",
        Ticket.workflow_type == TicketWorkflowType.TICKET.value,
        Ticket.task_kind == TicketTaskKind.NORMAL.value,
        Ticket.status.in_([TicketStatus.UNASSIGNED.value, TicketStatus.NEW.value, TicketStatus.PENDING.value]),
    ).limit(1)) is not None


def get_current_web_client(request: Request, db: Session = Depends(get_db)) -> CurrentWebClient:
    require_web_csrf(request)
    token = request.cookies.get(COOKIE_NAME, "")
    if not token or len(token) > 128:
        fail()
    session = db.get(WebClientSession, token_hash(token))
    if session is None:
        fail()
    profile = db.scalar(select(ClientProfile).where(ClientProfile.client_id == session.client_id))
    if not eligible_profile(db, profile) or profile.web_credentials_version != session.credentials_version:
        db.delete(session)
        db.commit()
        fail()
    now = utcnow()
    expiry = (as_utc(session.last_activity_at) + timedelta(hours=24)
              if has_open_web_ticket(db, session.client_id) else as_utc(session.normal_expires_at))
    if now >= expiry:
        db.delete(session)
        db.commit()
        fail()
    return CurrentWebClient(id=session.client_id, session_hash=session.token_hash)


def register_web_activity(db: Session, client: CurrentWebClient):
    """Call after a successful real client action, in that action's transaction.

    The route must first depend on get_current_web_client. GET/polling must never
    call this function. The caller commits together with the business action.
    """
    now = utcnow()
    db.execute(update(WebClientSession).where(
        WebClientSession.token_hash == client.session_hash,
        WebClientSession.client_id == client.id,
        WebClientSession.last_activity_at < now,
    ).values(last_activity_at=now))
