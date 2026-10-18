"""Private WEB credentials behind the two existing Socials fields."""
import re
import secrets
import string
import unicodedata

from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from ..auth import can_edit_client_profile, hash_password, is_readonly_chiki_piki, verify_password
from ..models import ClientCardFieldConfig, ClientProfile
from .encryption import SecretEncryptionError, decrypt_secret, encrypt_secret

WEB_KEYS = {"custom_web_login", "custom_web_password"}
WEB_COLUMNS = {"web_login", "web_login_normalized", "web_password_encrypted", "web_password_hash", "web_credentials_version"}


def error(status, code, message):
    raise HTTPException(status, detail={"error": {"code": code, "message": message, "details": {}}})


def without_web_fields(values):
    return {k: v for k, v in dict(values or {}).items() if k not in WEB_KEYS}


def require_profile(db, client_id):
    profile = db.scalar(select(ClientProfile).where(ClientProfile.client_id == client_id))
    if profile is None:
        error(404, "client_profile_not_found", "Профиль клиента не найден")
    return profile


def authorize_fields(db, user, keys, *, write=False):
    if not write and "custom_web_password" in keys and is_readonly_chiki_piki(user):
        error(403, "password_reveal_forbidden", "Нет прав на раскрытие пароля")
    if write and (user is None or not can_edit_client_profile(user)):
        error(403, "web_credentials_forbidden", "Нет прав на редактирование WEB-реквизитов")
    fields = {f.key: f for f in db.scalars(select(ClientCardFieldConfig).where(ClientCardFieldConfig.key.in_(keys)))}
    for key in keys:
        field = fields.get(key)
        if field is None:
            error(409, "web_field_missing", "Поле WEB-реквизитов отсутствует в конфигурации карточки")
        if write and not field.is_editable:
            error(403, "web_credentials_forbidden", "Нет прав на WEB-реквизиты")


def normalize_login(value):
    if value is None or value == "":
        return None
    if not isinstance(value, str) or len(value.strip()) > 255 or any(c.isspace() for c in value.strip()):
        error(422, "web_login_invalid", "WEB-логин должен содержать до 255 символов без пробелов")
    normalized = value.strip().casefold()
    if len(normalized) > 255:
        error(422, "web_login_invalid", "WEB-логин слишком длинный")
    return normalized or None


def login_available(db, login, profile_id):
    return db.scalar(select(ClientProfile.id).where(
        ClientProfile.web_login_normalized == normalize_login(login), ClientProfile.id != profile_id,
    ).limit(1)) is None


def apply_changes(db, profile, changes, *, clear_password=False):
    if clear_password and changes.get("custom_web_password") not in (None, ""):
        error(422, "web_password_operation_conflict", "Нельзя одновременно заменить и удалить WEB-пароль")
    if not changes and not clear_password:
        return
    if "custom_web_login" in changes:
        login = changes["custom_web_login"]
        normalized = normalize_login(login)
        if normalized and not login_available(db, login, profile.id):
            error(409, "web_login_taken", "Этот WEB-логин уже занят")
        if normalized:
            profile.web_login = login.strip()
            profile.web_login_normalized = normalized
    # Empty controls from stale clients are harmless without an explicit clear operation.
    if clear_password:
        if profile.web_password_encrypted is not None or profile.web_password_hash is not None:
            profile.web_password_encrypted = None
            profile.web_password_hash = None
            profile.web_credentials_version = (profile.web_credentials_version or 0) + 1
    elif changes.get("custom_web_password") not in (None, ""):
        password = changes["custom_web_password"]
        if not isinstance(password, str) or len(password) > 1024:
            error(422, "web_password_invalid", "WEB-пароль должен содержать не более 1024 символов")
        if not verify_password(password, profile.web_password_hash):
            try:
                encrypted = encrypt_secret(password)
            except SecretEncryptionError:
                error(503, "web_password_unavailable", "Защищённое хранение WEB-пароля временно недоступно")
            profile.web_password_encrypted = encrypted
            profile.web_password_hash = hash_password(password)
            profile.web_credentials_version = (profile.web_credentials_version or 0) + 1
    try:
        db.flush()
    except IntegrityError:
        db.rollback()
        error(409, "web_credentials_conflict", "Не удалось сохранить WEB-реквизиты: логин уже занят или карточка изменилась")


def reveal(profile):
    try:
        return {"web_password": decrypt_secret(profile.web_password_encrypted)}
    except SecretEncryptionError:
        error(503, "web_password_unavailable", "WEB-пароль временно недоступен")


_RU = dict(zip("абвгдеёжзийклмнопрстуфхцчшщъыьэюя", (
    "a", "b", "v", "g", "d", "e", "yo", "zh", "z", "i", "y", "k", "l", "m", "n", "o", "p",
    "r", "s", "t", "u", "f", "kh", "ts", "ch", "sh", "shch", "", "y", "", "e", "yu", "ya",
)))


def latin_name(value):
    translated = "".join(_RU.get(c, c) for c in (value or "").lower())
    return re.sub("[^a-z]", "", unicodedata.normalize("NFKD", translated).encode("ascii", "ignore").decode())


def generate_login(db, profile, *, bitrix_login=None, contact_name=None, last_name=None):
    bitrix_login = (bitrix_login or "").strip()
    if bitrix_login and login_available(db, bitrix_login, profile.id):
        return bitrix_login
    parts = (contact_name or "").split()
    first = latin_name(parts[0]) if parts else ""
    surname = latin_name(last_name or (parts[-1] if len(parts) > 1 else ""))
    if not first or not surname:
        error(422, "web_name_required", "Для генерации WEB-логина заполните имя и фамилию")
    for size in range(1, min(3, len(first)) + 1):
        candidate = f"{first[:size]}.{surname}@elitesochi.com"
        if login_available(db, candidate, profile.id):
            return candidate
    suffix = 2
    while True:
        candidate = f"{first[:3]}.{surname}{suffix}@elitesochi.com"
        if login_available(db, candidate, profile.id):
            return candidate
        suffix += 1


def generate_password():
    groups = (string.ascii_lowercase, string.ascii_uppercase, string.digits, "!@#$%&*+-_=?")
    chars = [secrets.choice(group) for group in groups]
    chars += [secrets.choice("".join(groups)) for _ in range(8)]
    secrets.SystemRandom().shuffle(chars)
    return "".join(chars)


def generate_draft(db, profile, payload):
    provided = payload.model_fields_set
    login = payload.web_login if "web_login" in provided else profile.web_login
    has_password = payload.web_password_set if "web_password_set" in provided else bool(profile.web_password_encrypted)
    both = bool(login and has_password)
    if both and not payload.regenerate:
        error(409, "web_regeneration_confirmation_required", "Подтвердите перегенерацию WEB-логина и пароля")
    generated_login = generate_login(
        db, profile,
        **{key: getattr(payload, key) if key in provided else getattr(profile, key)
           for key in ("bitrix_login", "contact_name", "last_name")},
    ) if not login or both else login
    return {"web_login": generated_login, "web_password": generate_password() if not has_password or both else None}
