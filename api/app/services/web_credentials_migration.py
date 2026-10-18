"""Fail closed on ambiguous legacy credentials; never include their values in errors."""
import json

from sqlalchemy import text

from ..auth import hash_password
from .bitrix_password_migration import looks_like_fernet_token
from .encryption import decrypt_secret, encrypt_secret
from .web_credentials import WEB_KEYS, normalize_login


def migrate_legacy_web_credentials(conn):
    plans, owners = [], {}
    for row in conn.execute(text(
        "SELECT id, custom_fields, web_login, web_login_normalized, web_password_encrypted, "
        "web_password_hash, web_credentials_version FROM client_profiles"
    )).mappings():
        try:
            custom = json.loads(row["custom_fields"]) if isinstance(row["custom_fields"], str) else dict(row["custom_fields"] or {})
            legacy_login = custom.get("custom_web_login")
            login = row["web_login"] or legacy_login
            normalized = normalize_login(login)
            if row["web_login"] and legacy_login and normalize_login(legacy_login) != normalized:
                raise ValueError()
            if normalized and normalized in owners:
                raise ValueError()
            if normalized:
                owners[normalized] = row["id"]
            encrypted, hashed = row["web_password_encrypted"], row["web_password_hash"]
            version = row["web_credentials_version"] or 0
            password = custom.get("custom_web_password")
            if password:
                if not isinstance(password, str):
                    raise ValueError()
                if looks_like_fernet_token(password):
                    password = decrypt_secret(password)
                if encrypted and decrypt_secret(encrypted) != password:
                    raise ValueError()
                if not encrypted:
                    encrypted, hashed = encrypt_secret(password), hash_password(password)
                    version += 1
                elif not hashed:
                    hashed = hash_password(password)
            if any(k in custom for k in WEB_KEYS) or row["web_login_normalized"] != normalized:
                plans.append(dict(id=row["id"], login=login.strip() if normalized else None, normalized=normalized,
                                  encrypted=encrypted, hashed=hashed, version=version,
                                  custom=json.dumps({k: v for k, v in custom.items() if k not in WEB_KEYS})))
        except Exception:
            raise RuntimeError("WEB credential migration stopped: legacy values conflict or cannot be protected; no credential data was changed.") from None
    json_type = "JSONB" if conn.dialect.name == "postgresql" else "TEXT"
    for plan in plans:
        conn.execute(text(
            "UPDATE client_profiles SET web_login=:login, web_login_normalized=:normalized, "
            "web_password_encrypted=:encrypted, web_password_hash=:hashed, web_credentials_version=:version, "
            f"custom_fields=CAST(:custom AS {json_type}) WHERE id=:id"
        ), plan)
    conn.execute(text("CREATE UNIQUE INDEX IF NOT EXISTS ix_client_profiles_web_login_normalized "
                      "ON client_profiles(web_login_normalized)"))
