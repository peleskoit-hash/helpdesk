from __future__ import annotations

import base64
import binascii
from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..models import ClientProfile
from .encryption import (
    SecretEncryptionError,
    ensure_secret_encryption_configured,
    decrypt_secret,
    encrypt_secret,
)


class BitrixPasswordMigrationError(RuntimeError):
    pass


@dataclass(frozen=True)
class BitrixPasswordMigrationResult:
    scanned: int = 0
    encrypted: int = 0
    already_encrypted: int = 0


def looks_like_fernet_token(value: str | None) -> bool:
    token = str(value or "").strip()
    if not token:
        return False
    try:
        decoded = base64.urlsafe_b64decode(token.encode("utf-8") + b"=" * (-len(token) % 4))
    except (binascii.Error, ValueError, UnicodeEncodeError):
        return False
    return len(decoded) >= 57 and decoded[0] == 0x80


def migrate_legacy_bitrix_passwords(db: Session) -> BitrixPasswordMigrationResult:
    profiles = list(
        db.execute(select(ClientProfile).where(ClientProfile.bitrix_password.is_not(None))).scalars()
    )
    profiles = [profile for profile in profiles if str(profile.bitrix_password or "").strip()]
    if not profiles:
        return BitrixPasswordMigrationResult()

    try:
        ensure_secret_encryption_configured()
    except SecretEncryptionError as exc:
        raise BitrixPasswordMigrationError(
            "Cannot migrate ClientProfile.bitrix_password values: "
            "BITRIX_PASSWORD_ENCRYPTION_KEY is missing or invalid."
        ) from exc

    plaintext_profiles: list[ClientProfile] = []
    already_encrypted = 0
    for profile in profiles:
        value = str(profile.bitrix_password or "")
        if looks_like_fernet_token(value):
            try:
                decrypt_secret(value)
            except SecretEncryptionError as exc:
                raise BitrixPasswordMigrationError(
                    "Cannot migrate ClientProfile.bitrix_password values: encrypted value cannot be "
                    "decrypted with the configured BITRIX_PASSWORD_ENCRYPTION_KEY."
                ) from exc
            already_encrypted += 1
        else:
            plaintext_profiles.append(profile)

    for profile in plaintext_profiles:
        profile.bitrix_password = encrypt_secret(str(profile.bitrix_password or ""))

    return BitrixPasswordMigrationResult(
        scanned=len(profiles),
        encrypted=len(plaintext_profiles),
        already_encrypted=already_encrypted,
    )
