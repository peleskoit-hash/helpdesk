"""Local avatar storage; no Telegram requests.

Mutations own commit/rollback: use a dedicated Session without unrelated changes.
A missing Telegram photo (file_unique_id=None) leaves the saved avatar intact.
"""
from __future__ import annotations

from datetime import datetime, timezone
from io import BytesIO
import logging
from pathlib import Path
import re
import uuid

from PIL import Image, ImageOps, UnidentifiedImageError
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..config import settings
from ..models import TelegramContact, Ticket, TicketStatus


MAX_IMAGE_BYTES = 10 * 1024 * 1024
MAX_IMAGE_PIXELS = 20_000_000
_EXTENSIONS = {"JPEG": "jpg", "PNG": "png", "WEBP": "webp"}
_DIRECTORY = "telegram_client_avatars"
_FILENAME = re.compile(r"[0-9a-f]{32}\.(?:jpg|png|webp)")
logger = logging.getLogger(__name__)


def prepare_avatar(content: bytes) -> tuple[bytes, str]:
    """Validate bounded input and return a padded 256x256 WebP and extension."""
    if not content or len(content) > MAX_IMAGE_BYTES:
        raise ValueError("Avatar must contain between 1 byte and 10 MiB")
    try:
        with Image.open(BytesIO(content), formats=list(_EXTENSIONS)) as image:
            extension = _EXTENSIONS[image.format]
            if image.width * image.height > MAX_IMAGE_PIXELS:
                raise ValueError("Avatar exceeds the pixel limit")
            if getattr(image, "n_frames", 1) != 1:
                raise ValueError("Animated avatars are unsupported")
            image.verify()
        with Image.open(BytesIO(content), formats=list(_EXTENSIONS)) as image:
            image.load()
            oriented = ImageOps.exif_transpose(image).convert("RGBA")
            scaled = ImageOps.contain(oriented, (256, 256), Image.Resampling.LANCZOS)
            thumbnail = Image.new("RGBA", (256, 256), (0, 0, 0, 0))
            thumbnail.paste(scaled, ((256 - scaled.width) // 2, (256 - scaled.height) // 2))
            output = BytesIO()
            thumbnail.save(output, format="WEBP", quality=85)
            return output.getvalue(), extension
    except (UnidentifiedImageError, OSError, SyntaxError, Image.DecompressionBombError) as exc:
        raise ValueError("Invalid avatar image") from exc


def _resolve_path(relative_path: str | None) -> Path | None:
    # Accept only our generated filenames, including on Windows (no drive/ADS paths).
    if not relative_path:
        return None
    parts = relative_path.split("/")
    if len(parts) != 2 or parts[0] != _DIRECTORY or not _FILENAME.fullmatch(parts[1]):
        return None
    root = Path(settings.telegram_client_avatar_storage_dir).resolve()
    candidate = (root / parts[1]).resolve()
    if candidate.parent != root:
        return None
    return candidate


def _delete_file(relative_path: str | None) -> None:
    path = _resolve_path(relative_path)
    if path is not None:
        try:
            path.unlink(missing_ok=True)
        except OSError:
            # The committed thumbnail/path must not be rolled back on cleanup failure.
            logger.warning("Could not remove an obsolete Telegram client avatar file")


def _locked_contact(db: Session, contact_id: str) -> TelegramContact:
    contact = db.scalar(
        select(TelegramContact)
        .where(TelegramContact.id == contact_id)
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    if contact is None:
        raise ValueError("Telegram contact does not exist")
    return contact


def needs_avatar_download(contact: TelegramContact, file_unique_id: str | None) -> bool:
    """Check photo identity and local storage before acquiring image bytes."""
    if file_unique_id is None:
        return False
    path = _resolve_path(contact.avatar_path)
    return (contact.avatar_file_unique_id != file_unique_id or not contact.avatar_thumbnail
            or path is None or not path.is_file())


def _has_open_tickets(db: Session, contact: TelegramContact) -> bool:
    return db.scalar(select(Ticket.id).where(
        Ticket.company_id == contact.company_id,
        Ticket.client_id == contact.client_id,
        Ticket.status != TicketStatus.CLOSED.value,
    ).limit(1)) is not None


def save_avatar(db: Session, contact_id: str, file_unique_id: str | None, content: bytes | None = None,
                *, require_open_ticket: bool = False) -> bool:
    """Replace a changed photo or restore its missing full file from supplied bytes.

    Return False when no write is needed or bytes have not been supplied yet.
    Callers can use needs_avatar_download before acquiring image bytes. This service
    accepts bytes only; tokens, URLs and Telegram file_id are never persisted.
    Background refreshes require an open ticket, checked under the contact lock.
    """
    if file_unique_id is None:
        return False
    if not re.fullmatch(r"[A-Za-z0-9_-]{1,255}", file_unique_id):
        raise ValueError("Invalid Telegram file_unique_id")
    new_path = None
    try:
        contact = _locked_contact(db, contact_id)
        if require_open_ticket and not _has_open_tickets(db, contact):
            db.rollback()
            return False
        if content is None or not needs_avatar_download(contact, file_unique_id):
            db.rollback()  # Release the row lock without altering the avatar timestamp.
            return False
        same_photo = contact.avatar_file_unique_id == file_unique_id
        thumbnail, extension = prepare_avatar(content or b"")
        directory = Path(settings.telegram_client_avatar_storage_dir)
        directory.mkdir(parents=True, exist_ok=True)
        filename = f"{uuid.uuid4().hex}.{extension}"
        with (directory / filename).open("xb") as target:
            new_path = f"{_DIRECTORY}/{filename}"
            target.write(content)
        old_path = contact.avatar_path
        if not same_photo or not contact.avatar_thumbnail:
            contact.avatar_thumbnail = thumbnail
            contact.avatar_file_unique_id = file_unique_id
            contact.avatar_updated_at = datetime.now(timezone.utc)
        contact.avatar_path = new_path
        db.commit()
    except Exception:
        db.rollback()
        _delete_file(new_path)
        raise
    _delete_file(old_path)
    return True


def delete_full_avatar(db: Session, contact_id: str, *, only_if_inactive: bool = False) -> bool:
    """Remove only the full photo; optionally recheck inactivity under the contact lock."""
    try:
        contact = _locked_contact(db, contact_id)
        if only_if_inactive and _has_open_tickets(db, contact):
            db.rollback()
            return False
        old_path = contact.avatar_path
        if not old_path:
            db.rollback()
            return False
        contact.avatar_path = None
        db.commit()
    except Exception:
        db.rollback()
        raise
    _delete_file(old_path)
    return True
