from __future__ import annotations

import base64
import uuid

import pytest
from cryptography.fernet import Fernet
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from api.app.config import DEFAULT_SESSION_SECRET, settings
from api.app.db import Base
from api.app.models import Client, ClientProfile, Company
from api.app.services.bitrix_password_migration import (
    BitrixPasswordMigrationError,
    looks_like_fernet_token,
    migrate_legacy_bitrix_passwords,
)
from api.app.services.encryption import decrypt_secret, encrypt_secret


def _session():
    engine = create_engine("sqlite:///:memory:", future=True)
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine, autoflush=False, autocommit=False)
    return Session()


def _set_key(monkeypatch, key: bytes | str | None) -> str | None:
    value = key.decode("utf-8") if isinstance(key, bytes) else key
    monkeypatch.setattr(settings, "bitrix_password_encryption_key", value)
    return value


def _profile(session, password: str | None) -> ClientProfile:
    company = Company(id=str(uuid.uuid4()), name="ACME")
    client = Client(id=str(uuid.uuid4()), company_id=company.id, display_name="Client")
    profile = ClientProfile(id=str(uuid.uuid4()), client_id=client.id, bitrix_password=password, custom_fields={})
    session.add_all([company, client, profile])
    session.commit()
    return profile


def test_plaintext_bitrix_password_is_encrypted_and_decrypts_after_migration(monkeypatch):
    _set_key(monkeypatch, Fernet.generate_key())
    session = _session()
    profile = _profile(session, "legacy-secret")

    result = migrate_legacy_bitrix_passwords(session)
    session.commit()
    session.refresh(profile)

    assert result.scanned == 1
    assert result.encrypted == 1
    assert profile.bitrix_password != "legacy-secret"
    assert looks_like_fernet_token(profile.bitrix_password)
    assert decrypt_secret(profile.bitrix_password) == "legacy-secret"


def test_encrypted_bitrix_password_is_kept_byte_for_byte(monkeypatch):
    _set_key(monkeypatch, Fernet.generate_key())
    encrypted = encrypt_secret("already-secret")
    session = _session()
    profile = _profile(session, encrypted)

    result = migrate_legacy_bitrix_passwords(session)
    session.commit()
    session.refresh(profile)

    assert result.scanned == 1
    assert result.encrypted == 0
    assert result.already_encrypted == 1
    assert profile.bitrix_password == encrypted


def test_bitrix_password_migration_is_idempotent(monkeypatch):
    _set_key(monkeypatch, Fernet.generate_key())
    session = _session()
    profile = _profile(session, "legacy-secret")

    migrate_legacy_bitrix_passwords(session)
    session.commit()
    session.refresh(profile)
    first_value = profile.bitrix_password

    result = migrate_legacy_bitrix_passwords(session)
    session.commit()
    session.refresh(profile)

    assert result.encrypted == 0
    assert result.already_encrypted == 1
    assert profile.bitrix_password == first_value


def test_mixed_plaintext_and_encrypted_bitrix_passwords_are_migrated(monkeypatch):
    _set_key(monkeypatch, Fernet.generate_key())
    encrypted = encrypt_secret("already-secret")
    session = _session()
    plaintext_profile = _profile(session, "legacy-secret")
    encrypted_profile = _profile(session, encrypted)

    result = migrate_legacy_bitrix_passwords(session)
    session.commit()
    session.refresh(plaintext_profile)
    session.refresh(encrypted_profile)

    assert result.scanned == 2
    assert result.encrypted == 1
    assert result.already_encrypted == 1
    assert decrypt_secret(plaintext_profile.bitrix_password) == "legacy-secret"
    assert encrypted_profile.bitrix_password == encrypted


def test_wrong_key_encrypted_bitrix_password_is_not_overwritten_and_error_is_safe(monkeypatch, caplog):
    current_key = Fernet.generate_key()
    other_key = Fernet.generate_key()
    _set_key(monkeypatch, current_key)
    wrong_key_token = Fernet(other_key).encrypt(b"plaintext-secret-do-not-leak").decode("utf-8")
    session = _session()
    profile = _profile(session, wrong_key_token)

    with pytest.raises(BitrixPasswordMigrationError) as exc:
        migrate_legacy_bitrix_passwords(session)
    session.rollback()
    session.refresh(profile)

    assert profile.bitrix_password == wrong_key_token
    assert "plaintext-secret-do-not-leak" not in str(exc.value)
    assert "plaintext-secret-do-not-leak" not in caplog.text


def test_corrupt_fernet_like_bitrix_password_is_not_overwritten(monkeypatch):
    _set_key(monkeypatch, Fernet.generate_key())
    corrupt_token = base64.urlsafe_b64encode(b"\x80" + b"0" * 56).decode("utf-8")
    assert looks_like_fernet_token(corrupt_token)
    session = _session()
    profile = _profile(session, corrupt_token)

    with pytest.raises(BitrixPasswordMigrationError) as exc:
        migrate_legacy_bitrix_passwords(session)
    session.rollback()
    session.refresh(profile)

    assert profile.bitrix_password == corrupt_token
    assert "bitrix_password" in str(exc.value)


def test_bitrix_password_migration_error_leaves_no_partial_changes(monkeypatch):
    current_key = Fernet.generate_key()
    other_key = Fernet.generate_key()
    _set_key(monkeypatch, current_key)
    wrong_key_token = Fernet(other_key).encrypt(b"wrong-key-secret").decode("utf-8")
    session = _session()
    plaintext_profile = _profile(session, "legacy-secret")
    encrypted_profile = _profile(session, wrong_key_token)

    with pytest.raises(BitrixPasswordMigrationError):
        migrate_legacy_bitrix_passwords(session)
    session.rollback()
    session.refresh(plaintext_profile)
    session.refresh(encrypted_profile)

    assert plaintext_profile.bitrix_password == "legacy-secret"
    assert encrypted_profile.bitrix_password == wrong_key_token


def test_dev_clean_database_does_not_require_bitrix_password_key(monkeypatch):
    _set_key(monkeypatch, None)
    session = _session()

    result = migrate_legacy_bitrix_passwords(session)

    assert result.scanned == 0


def test_dev_database_with_bitrix_password_requires_valid_key(monkeypatch):
    _set_key(monkeypatch, None)
    session = _session()
    _profile(session, "legacy-secret")

    with pytest.raises(BitrixPasswordMigrationError) as exc:
        migrate_legacy_bitrix_passwords(session)

    assert "legacy-secret" not in str(exc.value)
    assert "BITRIX_PASSWORD_ENCRYPTION_KEY is missing or invalid" in str(exc.value)


def test_prod_without_valid_bitrix_password_key_fails_runtime_validation(monkeypatch):
    monkeypatch.setattr(settings, "app_env", "prod")
    monkeypatch.setattr(settings, "session_secret_key", "non-default-session-secret")
    _set_key(monkeypatch, None)

    with pytest.raises(RuntimeError) as missing_exc:
        settings.validate_runtime_guards()
    assert "BITRIX_PASSWORD_ENCRYPTION_KEY must be set" in str(missing_exc.value)

    _set_key(monkeypatch, "not-a-fernet-key")
    with pytest.raises(RuntimeError) as invalid_exc:
        settings.validate_runtime_guards()
    assert "BITRIX_PASSWORD_ENCRYPTION_KEY must be a valid Fernet key" in str(invalid_exc.value)

    monkeypatch.setattr(settings, "session_secret_key", DEFAULT_SESSION_SECRET)
