"""Daily local avatar retention; invoked by the existing internal task scheduler."""
from datetime import datetime, timedelta, timezone
import logging
from pathlib import Path
from zoneinfo import ZoneInfo

from sqlalchemy import func, or_, select, text

from ..config import settings
from ..db import SessionLocal
from ..models import TelegramAvatarCleanupState, TelegramContact, Ticket
from .telegram_client_avatars import (
    _DIRECTORY, _FILENAME, _delete_file, _has_open_tickets, _locked_contact,
    _resolve_path, delete_full_avatar,
)


logger = logging.getLogger(__name__)
_ORPHAN_GRACE = timedelta(hours=24)


def _try_job_lock(db) -> bool:
    # Transaction-scoped PostgreSQL lock: released even if the worker crashes.
    # Fail closed on unsupported databases; unit tests substitute only this guard.
    return bool(db.scalar(text("SELECT pg_try_advisory_xact_lock(hashtext(:key))"),
                          {"key": "telegram_client_avatar_nightly"}))


def _cleanup_contact(db, contact_id: str, cutoff: datetime) -> None:
    contact = _locked_contact(db, contact_id)
    if _has_open_tickets(db, contact):
        db.rollback()
        return
    latest = db.scalar(select(func.max(Ticket.created_at)).where(
        Ticket.company_id == contact.company_id, Ticket.client_id == contact.client_id,
    ))
    if latest is not None:
        latest = latest.replace(tzinfo=timezone.utc) if latest.tzinfo is None else latest.astimezone(timezone.utc)
    if latest is not None and latest <= cutoff:
        old_path = contact.avatar_path
        contact.avatar_thumbnail = None
        contact.avatar_file_unique_id = None
        contact.avatar_updated_at = None
        contact.avatar_path = None
        db.commit()
        _delete_file(old_path)
    else:
        # No ticket history cannot prove 365 days of inactivity: retain the thumbnail.
        delete_full_avatar(db, contact_id, only_if_inactive=True)


def _cleanup_orphans(session_factory, now_utc: datetime) -> bool:
    root = Path(settings.telegram_client_avatar_storage_dir)
    if not root.exists():
        return True
    successful = True
    for path in root.iterdir():
        try:
            if path.is_symlink() or not _FILENAME.fullmatch(path.name) or not path.is_file():
                continue
            relative = f"{_DIRECTORY}/{path.name}"
            if _resolve_path(relative) != path.resolve():
                continue
            if path.stat().st_mtime > (now_utc - _ORPHAN_GRACE).timestamp():
                continue
            # Recheck the live DB, not a snapshot from before the contact cleanup.
            # save_avatar always creates a fresh UUID file; the grace protects writes
            # that have not committed their avatar_path yet.
            with session_factory() as db:
                registered = db.scalar(select(TelegramContact.id).where(
                    TelegramContact.avatar_path == relative,
                ).limit(1))
                if registered is None:
                    path.unlink(missing_ok=True)
        except FileNotFoundError:
            continue  # Immediate cleanup or avatar replacement already removed it.
        except Exception:
            successful = False
            logger.warning("Telegram avatar orphan cleanup failed")
    return successful


def _cleanup_all(session_factory, now_utc: datetime) -> bool:
    with session_factory() as db:
        contact_ids = db.scalars(select(TelegramContact.id).where(or_(
            TelegramContact.avatar_path.is_not(None), TelegramContact.avatar_thumbnail.is_not(None),
            TelegramContact.avatar_file_unique_id.is_not(None), TelegramContact.avatar_updated_at.is_not(None),
        ))).all()
    successful = True
    for contact_id in contact_ids:
        try:
            with session_factory() as db:
                _cleanup_contact(db, contact_id, now_utc - timedelta(days=365))
        except Exception:
            successful = False
            logger.warning("Telegram avatar contact cleanup failed")
    return _cleanup_orphans(session_factory, now_utc) and successful


def run_avatar_cleanup_once(*, session_factory=SessionLocal, now_utc: datetime | None = None) -> bool:
    try:
        use_live_clock = now_utc is None
        now_utc = now_utc or datetime.now(timezone.utc)
        now_utc = now_utc.replace(tzinfo=timezone.utc) if now_utc.tzinfo is None else now_utc.astimezone(timezone.utc)
        local = now_utc.astimezone(ZoneInfo("Europe/Moscow"))
        with session_factory() as coordinator:
            if not _try_job_lock(coordinator):
                return False
            state = coordinator.get(TelegramAvatarCleanupState, 1)
            if state is None:
                state = TelegramAvatarCleanupState(id=1, next_run_on=local.date())
                coordinator.add(state)
            last = state.last_success_on
            if last is not None and last >= local.date():
                return False
            if state.next_run_on > local.date() or (state.next_run_on == local.date() and local.hour < 3):
                coordinator.commit()  # Persist the initial due date, even before the first run.
                return False
            if not _cleanup_all(session_factory, now_utc):
                coordinator.commit()
                return False  # Retry partial failures on a later scheduler pass.
            success_on = datetime.now(timezone.utc).astimezone(local.tzinfo).date() if use_live_clock else local.date()
            state.last_success_on = success_on
            state.next_run_on = success_on + timedelta(days=1)
            coordinator.commit()
            return True
    except Exception:
        # Never log exception details: paths or personal data can be present.
        logger.warning("Telegram avatar nightly cleanup failed")
        return False
