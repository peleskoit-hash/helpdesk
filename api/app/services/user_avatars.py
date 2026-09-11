from __future__ import annotations

from pathlib import Path, PurePosixPath
import uuid

from ..config import settings


MAX_AVATAR_BYTES = 5 * 1024 * 1024

_ALLOWED_CONTENT_TYPES = {
    "image/jpeg": "jpeg",
    "image/jpg": "jpeg",
    "image/png": "png",
    "image/webp": "webp",
}

_EXTENSIONS = {
    "jpeg": ".jpg",
    "png": ".png",
    "webp": ".webp",
}

_MEDIA_TYPES = {
    "jpeg": "image/jpeg",
    "png": "image/png",
    "webp": "image/webp",
}


class AvatarValidationError(ValueError):
    def __init__(self, code: str, message: str, status_code: int = 400) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.status_code = status_code


def avatar_storage_dir() -> Path:
    return Path(settings.avatar_storage_dir)


def avatar_storage_root() -> Path:
    return avatar_storage_dir().parent


def avatar_url(user_id: str, avatar_path: str | None) -> str | None:
    if not (avatar_path or "").strip():
        return None
    return f"/api/v1/users/{user_id}/avatar"


def detect_avatar_type(content: bytes) -> str | None:
    if content.startswith(b"\xff\xd8\xff"):
        return "jpeg"
    if content.startswith(b"\x89PNG\r\n\x1a\n"):
        return "png"
    if len(content) >= 12 and content.startswith(b"RIFF") and content[8:12] == b"WEBP":
        return "webp"
    return None


def media_type_for_kind(kind: str) -> str:
    return _MEDIA_TYPES.get(kind, "application/octet-stream")


def validate_avatar_upload(content: bytes, content_type: str | None) -> tuple[str, str]:
    if len(content) > MAX_AVATAR_BYTES:
        raise AvatarValidationError("avatar_too_large", "Размер аватара не должен превышать 5 МБ", 413)

    declared_kind = _ALLOWED_CONTENT_TYPES.get((content_type or "").split(";", 1)[0].strip().lower())
    detected_kind = detect_avatar_type(content)
    if detected_kind is None:
        raise AvatarValidationError("avatar_invalid_image", "Файл не является поддерживаемым изображением")
    if declared_kind is None:
        raise AvatarValidationError("avatar_unsupported_type", "Поддерживаются только JPG, PNG и WEBP")
    if declared_kind != detected_kind:
        raise AvatarValidationError("avatar_invalid_image", "Тип изображения не соответствует содержимому файла")

    return detected_kind, media_type_for_kind(detected_kind)


def save_avatar_file(content: bytes, kind: str) -> str:
    storage_dir = avatar_storage_dir()
    storage_dir.mkdir(parents=True, exist_ok=True)
    filename = f"{uuid.uuid4().hex}{_EXTENSIONS[kind]}"
    (storage_dir / filename).write_bytes(content)
    return f"avatars/{filename}"


def resolve_avatar_path(relative_path: str | None) -> Path | None:
    value = (relative_path or "").strip()
    if not value:
        return None
    path = PurePosixPath(value)
    if path.is_absolute() or path.parts[:1] != ("avatars",) or len(path.parts) != 2:
        return None
    if path.name in {"", ".", ".."}:
        return None

    storage_dir = avatar_storage_dir().resolve()
    candidate = (avatar_storage_root() / Path(*path.parts)).resolve()
    try:
        candidate.relative_to(storage_dir)
    except ValueError:
        return None
    return candidate


def delete_avatar_file(relative_path: str | None) -> None:
    path = resolve_avatar_path(relative_path)
    if path is None:
        return
    try:
        if path.is_file():
            path.unlink()
    except OSError:
        return
