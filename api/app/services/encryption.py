from __future__ import annotations

import logging

from cryptography.fernet import Fernet, InvalidToken

from ..config import settings

logger = logging.getLogger(__name__)


class SecretEncryptionError(RuntimeError):
    pass


class SecretEncryptionConfigurationError(SecretEncryptionError):
    pass


def _fernet() -> Fernet:
    key = (settings.bitrix_password_encryption_key or "").strip()
    if not key:
        raise SecretEncryptionConfigurationError(
            "BITRIX_PASSWORD_ENCRYPTION_KEY must be set to a valid Fernet key."
        )
    try:
        return Fernet(key.encode("utf-8"))
    except (TypeError, ValueError) as exc:
        raise SecretEncryptionConfigurationError(
            "BITRIX_PASSWORD_ENCRYPTION_KEY must be a valid Fernet key."
        ) from exc


def ensure_secret_encryption_configured() -> None:
    _fernet()


def encrypt_secret(value: str | None) -> str | None:
    if value is None:
        return None
    return _fernet().encrypt(value.encode("utf-8")).decode("utf-8")


def decrypt_secret(value: str | None) -> str | None:
    if value is None:
        return None
    try:
        return _fernet().decrypt(value.encode("utf-8")).decode("utf-8")
    except InvalidToken as exc:
        logger.warning("Encrypted secret cannot be decrypted with the configured key.")
        raise SecretEncryptionError(
            "Encrypted secret cannot be decrypted with the configured BITRIX_PASSWORD_ENCRYPTION_KEY."
        ) from exc
    except (UnicodeDecodeError, ValueError, TypeError) as exc:
        logger.warning("Encrypted secret has invalid format.")
        raise SecretEncryptionError("Encrypted secret has invalid format.") from exc
