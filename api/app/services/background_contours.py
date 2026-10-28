from datetime import datetime, timezone
from io import BytesIO
import logging
import os
from pathlib import Path
from typing import BinaryIO
from uuid import UUID, uuid4
import warnings

from PIL import Image, UnidentifiedImageError
from sqlalchemy import select, update
from sqlalchemy.orm import Session

from ..auth import CurrentUser
from ..config import settings
from ..models import BackgroundContour, User
from ..schemas import BackgroundContourComposition


logger = logging.getLogger(__name__)
MAX_BYTES = 2 * 1024 * 1024
MAX_DIMENSION = 2048
CREATE_ROLES = {"admin", "agent", "trainee"}


class ContourError(ValueError):
    def __init__(self, code: str, message: str, status_code: int = 400):
        super().__init__(message)
        self.code, self.status_code = code, status_code


def _actor(db: Session, user: CurrentUser) -> User:
    actor = db.get(User, user.real_user_id)
    if actor is None or not actor.is_active:
        raise ContourError("contour_forbidden", "Пользователь недоступен", 403)
    return actor


def _out(row: BackgroundContour, actor: User, author_active: bool | None) -> dict:
    result = {key: getattr(row, key) for key in (
        "id", "name", "author_user_id", "author_name", "created_at", "format", "file_size",
        "rotation", "rings", "distance", "base_scale",
    )}
    if result["created_at"].tzinfo is None:
        result["created_at"] = result["created_at"].replace(tzinfo=timezone.utc)
    own = row.author_user_id == actor.id
    return {**result, "image_url": f"/api/v1/background-contours/{row.id}/image",
            "can_delete": own or actor.role == "admin",
            "can_edit": own or (actor.role == "admin" and not author_active)}


def list_contours(db: Session, user: CurrentUser) -> list[dict]:
    actor = _actor(db, user)
    rows = db.execute(select(BackgroundContour, User.is_active).outerjoin(
        User, User.id == BackgroundContour.author_user_id,
    ).order_by(BackgroundContour.created_at.desc(), BackgroundContour.id.desc())).all()
    return [_out(row, actor, active) for row, active in rows]


def _prepare_image(content: bytes) -> tuple[bytes, str]:
    if len(content) > MAX_BYTES:
        raise ContourError("contour_too_large", "Размер контура не должен превышать 2 МБ", 413)
    try:
        with warnings.catch_warnings():
            warnings.simplefilter("error", Image.DecompressionBombWarning)
            with Image.open(BytesIO(content), formats=["PNG", "WEBP"]) as image:
                kind = image.format.lower()
                if max(image.size) > MAX_DIMENSION or min(image.size) < 1:
                    raise ContourError("contour_dimensions", "Размер изображения не должен превышать 2048×2048")
                if getattr(image, "n_frames", 1) != 1:
                    raise ContourError("contour_animated", "Анимированные изображения не поддерживаются")
                image.verify()
            with Image.open(BytesIO(content), formats=["PNG", "WEBP"]) as image:
                image.load()
                rgba = image.convert("RGBA")
                low, high = rgba.getchannel("A").getextrema()
                if low == 255 or high == 0:
                    raise ContourError("contour_transparency", "Нужен видимый контур на прозрачном фоне")
                # A fresh image drops EXIF/XMP/ICC, comments and appended source data.
                clean = Image.new("RGBA", rgba.size)
                clean.paste(rgba)
                clean.paste((0, 0, 0, 0), mask=rgba.getchannel("A").point(lambda alpha: 255 if alpha == 0 else 0))
                output = BytesIO()
                clean.save(output, format=kind.upper(), **({"lossless": True} if kind == "webp" else {}))
    except (UnidentifiedImageError, OSError, SyntaxError, ValueError, Image.DecompressionBombError,
            Image.DecompressionBombWarning) as exc:
        if isinstance(exc, ContourError):
            raise
        raise ContourError("contour_invalid_image", "Файл не является корректным PNG/WebP") from exc
    encoded = output.getvalue()
    if len(encoded) > MAX_BYTES:
        raise ContourError("contour_too_large", "Готовый контур превышает 2 МБ", 413)
    return encoded, kind


def _paths(row: BackgroundContour) -> tuple[Path, Path]:
    try:
        expected = f"{UUID(row.id).hex}.{row.format}"
    except ValueError:
        expected = None
    if row.format not in {"png", "webp"} or row.storage_key != expected:
        raise ContourError("contour_invalid_storage_key", "Недопустимый ключ контура", 503)
    root = Path(settings.background_contour_storage_dir).resolve()
    paths = root / row.storage_key, root / f".deleting-{row.storage_key}"
    for path in paths:
        if path.is_symlink() or path.resolve().parent != root:
            raise ContourError("contour_invalid_storage_key", "Недопустимый путь контура", 503)
    return paths


def _cleanup(path: Path) -> None:
    try:
        path.unlink(missing_ok=True)
    except OSError:
        # Only unreferenced files reach here. Keep them private for manual cleanup.
        logger.warning("Could not clean up background contour file: %s", path.name)


def create_contour(db: Session, user: CurrentUser, name: str, source: BinaryIO,
                   composition: BackgroundContourComposition) -> dict:
    actor = _actor(db, user)
    if actor.role not in CREATE_ROLES:
        raise ContourError("contour_forbidden", "Нет права создавать контуры оформления", 403)
    name = name.strip()
    if not name or len(name) > 120 or any(ord(char) < 32 for char in name):
        raise ContourError("contour_invalid_name", "Название должно содержать от 1 до 120 символов", 422)
    content, kind = _prepare_image(source.read(MAX_BYTES + 1))
    contour_id = str(uuid4())
    row = BackgroundContour(
        id=contour_id, name=name, author_user_id=actor.id, author_name=actor.full_name or actor.login or actor.id,
        created_at=datetime.now(timezone.utc), storage_key=f"{UUID(contour_id).hex}.{kind}", format=kind,
        file_size=len(content), **composition.model_dump(),
    )
    path, _ = _paths(row)
    created = committing = False
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("xb") as target:
            created = True
            if target.write(content) != len(content):
                raise OSError("Incomplete contour write")
            target.flush()
            os.fsync(target.fileno())
        result = _out(row, actor, True)
        db.add(row)
        committing = True
        db.commit()
        return result
    except Exception as exc:
        db.rollback()
        # An acknowledgement may fail after commit. Never delete a possibly referenced file.
        persisted = False
        if committing:
            try:
                persisted = db.scalar(select(BackgroundContour.id).where(BackgroundContour.id == contour_id)) is not None
            except Exception:
                persisted = True
        if created and not persisted:
            _cleanup(path)
        raise ContourError("contour_write_failed", "Не удалось сохранить контур", 503) from exc


def image_content(db: Session, contour_id: str) -> tuple[bytes, str]:
    row = db.get(BackgroundContour, contour_id)
    if row is None:
        raise ContourError("contour_not_found", "Контур не найден", 404)
    try:
        path, backup = _paths(row)
        # A crash/rollback during deletion can leave the durable copy in quarantine.
        return (path if path.exists() else backup).read_bytes(), row.format
    except FileNotFoundError as exc:
        raise ContourError("contour_image_not_found", "Изображение контура не найдено", 404) from exc
    except OSError as exc:
        raise ContourError("contour_storage_error", "Изображение контура недоступно", 503) from exc


def delete_contour(db: Session, user: CurrentUser, contour_id: str) -> None:
    actor = _actor(db, user)
    # Serialize deletion on SQLite as well as PostgreSQL, then refresh stale ORM state.
    db.execute(update(BackgroundContour).where(BackgroundContour.id == contour_id)
               .values(id=BackgroundContour.id).execution_options(synchronize_session=False))
    row = db.scalar(select(BackgroundContour).where(BackgroundContour.id == contour_id)
                    .execution_options(populate_existing=True))
    if row is None:
        raise ContourError("contour_not_found", "Контур не найден", 404)
    if row.author_user_id != actor.id and actor.role != "admin":
        raise ContourError("contour_forbidden", "Удалить контур может только автор или администратор", 403)
    path, backup = _paths(row)
    staged = False
    try:
        if path.exists():
            if backup.exists():
                if not path.samefile(backup):
                    raise OSError("Contour quarantine already exists")
            else:
                os.link(path, backup)  # Exclusive publication: never overwrite another file.
            staged = True
            path.unlink()
        elif backup.exists():
            staged = True
        db.delete(row)
        db.commit()
    except Exception as exc:
        db.rollback()
        if staged:
            try:
                persisted = db.scalar(select(BackgroundContour.id).where(BackgroundContour.id == contour_id)) is not None
                if persisted:
                    if not path.exists():
                        os.link(backup, path)
                    if path.samefile(backup):
                        _cleanup(backup)
                else:
                    _cleanup(backup)
            except Exception:
                logger.warning("Background contour remains in recoverable quarantine: %s", backup.name)
        raise ContourError("contour_delete_failed", "Не удалось удалить контур", 503) from exc
    if staged:
        _cleanup(backup)
