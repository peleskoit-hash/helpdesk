"""Read-only avatar delivery for an authorized ticket; never populate storage."""
from sqlalchemy import select

from ..config import settings
from ..models import TelegramContact
from .telegram_client_avatars import MAX_IMAGE_BYTES, _resolve_path, prepare_avatar
from .telegram_client_avatar_sync import _download, _result


_MEDIA_TYPES = {"jpg": "image/jpeg", "png": "image/png", "webp": "image/webp"}


def contact_for_ticket(db, ticket):
    return db.scalar(select(TelegramContact).where(
        TelegramContact.company_id == ticket.company_id,
        TelegramContact.client_id == ticket.client_id,
    ))


def full_avatar_for_view(contact) -> tuple[bytes, str] | None:
    if contact is None or not contact.avatar_thumbnail:
        return None
    try:
        path = _resolve_path(contact.avatar_path)
        if path is not None and path.is_file():
            with path.open("rb") as source:
                content = source.read(MAX_IMAGE_BYTES + 1)
            if 0 < len(content) <= MAX_IMAGE_BYTES:
                return content, _MEDIA_TYPES[path.suffix[1:]]
    except OSError:
        pass
    token = settings.telegram_bot_token
    if not token or not contact.avatar_file_unique_id:
        return None
    try:
        photos = _result("getUserProfilePhotos", {
            "user_id": int(contact.external_user_id), "offset": 0, "limit": 1,
        }, token)
        if photos.get("total_count", 0) < 1:
            return None
        photo = max(photos["photos"][0], key=lambda item: (item["width"] * item["height"], item.get("file_size", 0)))
        if photo["file_unique_id"] != contact.avatar_file_unique_id:
            return None
        if photo.get("file_size", 0) > MAX_IMAGE_BYTES:
            return None
        file = _result("getFile", {"file_id": photo["file_id"]}, token)
        if file.get("file_size", 0) > MAX_IMAGE_BYTES:
            return None
        content = _download(file["file_path"], token)
        _, extension = prepare_avatar(content)
        return content, _MEDIA_TYPES[extension]
    except Exception:
        # Telegram/urllib exception details can contain tokens or personal data.
        return None
