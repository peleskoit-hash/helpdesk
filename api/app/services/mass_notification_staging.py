import re
from pathlib import Path, PureWindowsPath
from stat import S_ISREG
from unicodedata import category

from ..config import settings
from .telegram_bot import send_document, send_photo


PHOTO_MIME_TYPES = {".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".png": "image/png"}
PHOTO_LIMIT = 10 * 1024 ** 2
DOCUMENT_LIMIT = 20 * 1024 ** 2


class MassNotificationStagingError(RuntimeError):
    """The attachment could not be validated or its upload was not confirmed."""


def stage_mass_notification_file(local_path: Path, original_filename: str, mime_type: str | None) -> dict:
    """Upload one existing local file to staging and return its confirmed message ID."""
    staging_chat_id = (settings.telegram_staging_chat_id or "").strip()
    if not staging_chat_id:
        raise MassNotificationStagingError("TELEGRAM_STAGING_CHAT_ID is not configured")

    try:
        info = local_path.stat()
    except OSError as exc:
        raise MassNotificationStagingError("Cannot access the local attachment") from exc
    if not S_ISREG(info.st_mode):
        raise MassNotificationStagingError("The local attachment must be a regular file")
    if info.st_size == 0:
        raise MassNotificationStagingError("The local attachment is empty")

    cleaned = "".join(char for char in original_filename if category(char) not in {"Cc", "Cf", "Cs"})
    filename = PureWindowsPath(cleaned).name.strip().replace('"', "_")
    if filename in {"", ".", ".."}:
        filename = "attachment"

    content_type = (mime_type or "").partition(";")[0].strip().lower() or None
    if content_type and not re.fullmatch(r"[a-z0-9!#$%&'*+.^_`|~-]+/[a-z0-9!#$%&'*+.^_`|~-]+", content_type):
        raise MassNotificationStagingError("Invalid attachment MIME type")
    photo_type = PHOTO_MIME_TYPES.get(Path(filename).suffix.lower())
    if photo_type and content_type not in {None, "application/octet-stream", photo_type}:
        raise MassNotificationStagingError("The photo extension does not match its MIME type")

    limit = PHOTO_LIMIT if photo_type else DOCUMENT_LIMIT
    if info.st_size > limit:
        raise MassNotificationStagingError(f"The attachment exceeds the {limit // 1024 ** 2} MiB limit")

    if photo_type:
        response = send_photo(
            chat_id=staging_chat_id, content=local_path.read_bytes(), filename=filename, mime_type=photo_type,
        )
    else:
        response = send_document(
            chat_id=staging_chat_id, file_path=local_path, filename=filename, mime_type=content_type,
        )
    result = response.get("result") if isinstance(response, dict) else None
    message_id = result.get("message_id") if isinstance(result, dict) else None
    if type(message_id) is not int or message_id <= 0 or response.get("ok") is False:
        raise MassNotificationStagingError("Telegram did not confirm a valid staging message ID")

    return {
        "staging_chat_id": staging_chat_id,
        "message_id": message_id,
        "media_type": "photo" if photo_type else "document",
        "original_filename": filename,
        "file_size": info.st_size,
    }
