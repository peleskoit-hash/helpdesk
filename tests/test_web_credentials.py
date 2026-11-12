import json
import os
import re
import uuid
from types import SimpleNamespace

from cryptography.fernet import Fernet
import pytest
from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, inspect, select, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from api.app.auth import get_current_user, verify_password
from api.app.config import settings
from api.app.db import Base, get_db
from api.app.models import Client, ClientCardFieldConfig, ClientProfile, Company, Ticket, User
from api.app.routers import management_users, tickets
from api.app.schemas import ClientProfileUpdate, WebCredentialsGeneratePayload
from api.app.services import client_export, schema_evolution, web_credentials as web
from api.app.services.client_card import _profile_out
from api.app.services.client_card_derivation import (
    apply_client_card_profile_save_pipeline, apply_client_card_profile_local_save_rules,
    apply_client_card_save_derivations, normalize_client_card_derived_value,
    validate_client_card_direct_user_changes,
)
from api.app.services.encryption import decrypt_secret


@pytest.fixture(params=["sqlite", "postgresql"])
def engine(request, monkeypatch):
    monkeypatch.setattr(settings, "bitrix_password_encryption_key", Fernet.generate_key().decode())
    admin = None
    if request.param == "sqlite":
        engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    else:
        url = os.environ.get("WEB_CREDENTIALS_TEST_DATABASE_URL")
        if not url:
            pytest.skip("Set WEB_CREDENTIALS_TEST_DATABASE_URL to an isolated PostgreSQL test database")
        admin = create_engine(url)
        schema = "web_credentials_" + uuid.uuid4().hex
        with admin.begin() as c:
            c.execute(text(f"CREATE SCHEMA {schema}"))
        engine = create_engine(url, connect_args={"options": f"-csearch_path={schema}"})
    monkeypatch.setattr(schema_evolution, "engine", engine)
    try:
        yield engine
    finally:
        engine.dispose()
        if admin:
            with admin.begin() as c:
                c.execute(text(f"DROP SCHEMA {schema} CASCADE"))
            admin.dispose()


@pytest.fixture
def case(engine):
    Base.metadata.create_all(engine)
    with Session(engine, autoflush=False) as db:
        db.add(Company(id="company", name="Synthetic"))
        db.flush()
        db.add(Client(id="client", company_id="company", display_name="Synthetic"))
        db.flush()
        profile = ClientProfile(id="profile", client_id="client", contact_name="Иван Сергеевич Иванов",
                                custom_fields={"custom_other": "kept"})
        db.add(profile)
        for key, kind in [("custom_web_login", "text"), ("custom_web_password", "password"), ("custom_other", "text")]:
            db.add(ClientCardFieldConfig(id=key, key=key, label=key, field_type=kind, block_key="tg", is_system=False))
        db.add(Ticket(id="ticket", company_id="company", client_id="client", subject="Synthetic", assignee_id=None))
        db.commit()
        yield SimpleNamespace(db=db, profile=profile, admin=SimpleNamespace(id="admin", role="admin"))


def save(case, custom=None, **fields):
    if custom is not None:
        fields["custom_fields"] = custom
    return management_users.update_management_user_profile("client", ClientProfileUpdate(**fields), db=case.db, user=case.admin)


def test_web_direct_validator_recognizes_admin(case):
    for key in web.WEB_KEYS:
        case.db.get(ClientCardFieldConfig, key).admin_only_edit = True
    case.db.commit()
    changes = {"custom_web_login": "admin-login", "custom_web_password": "Admin-A1!"}
    validate_client_card_direct_user_changes(
        case.db, case.profile, {"custom_fields": changes}, changes, user=case.admin,
    )


@pytest.mark.parametrize("path", ["ticket", "management"])
@pytest.mark.parametrize("role", ["admin", "agent", "trainee", "chiki_piki"])
@pytest.mark.parametrize("admin_only", [False, True])
@pytest.mark.parametrize("editable", [False, True])
def test_web_field_permissions_on_both_save_paths(case, path, role, admin_only, editable):
    save(case, {"custom_web_login": "original", "custom_web_password": "Original-A1!"})
    original = (case.profile.web_login, case.profile.web_password_encrypted,
                case.profile.web_password_hash, case.profile.web_credentials_version)
    user = SimpleNamespace(id=f"staff-{role}", role=role)
    case.db.add(User(id=user.id, company_id="company", full_name="Staff", role=role))
    case.db.flush()
    case.db.get(Ticket, "ticket").assignee_id = user.id
    case.profile.destination = "Колл Центр"  # Allow readonly-role access, so its write check is exercised.
    for key in web.WEB_KEYS:
        field = case.db.get(ClientCardFieldConfig, key)
        field.admin_only_edit, field.is_editable = admin_only, editable
    case.db.commit()
    payload = ClientProfileUpdate(custom_fields={"custom_web_login": "changed", "custom_web_password": "Changed-B2!"})
    allowed = editable and (role == "admin" or (path == "ticket" and role in {"agent", "trainee"}))
    action = tickets.update_client_profile if path == "ticket" else management_users.update_management_user_profile
    args = ("ticket" if path == "ticket" else "client", payload)
    if allowed:
        result = action(*args, db=case.db, user=user)
        assert result.web_login == "changed" and result.web_password_set
        assert verify_password("Changed-B2!", case.profile.web_password_hash)
        assert case.profile.web_credentials_version == original[-1] + 1
    else:
        with pytest.raises(HTTPException) as exc:
            action(*args, db=case.db, user=user)
        assert exc.value.status_code == 403
        if path == "ticket" and role == "chiki_piki":
            assert exc.value.detail["error"]["code"] == "readonly_role"
        case.db.rollback()
        case.db.refresh(case.profile)
        assert (case.profile.web_login, case.profile.web_password_encrypted,
                case.profile.web_password_hash, case.profile.web_credentials_version) == original
    assert case.profile.custom_fields == {"custom_other": "kept"}


@pytest.mark.parametrize("role", [None, "agent", "trainee", "chiki_piki", "admin"])
@pytest.mark.parametrize("admin_only,editable", [(False, True), (True, True), (True, False)])
def test_other_save_entry_points_cannot_bypass_web_permissions(case, role, admin_only, editable):
    user = SimpleNamespace(id="staff", role=role) if role else None
    for key in web.WEB_KEYS:
        field = case.db.get(ClientCardFieldConfig, key)
        field.admin_only_edit, field.is_editable = admin_only, editable
    case.db.commit()
    changes = {"custom_web_login": "internal-call", "custom_web_password": "Internal-A1!"}
    if editable and role in {"admin", "agent", "trainee"}:
        result = apply_client_card_profile_save_pipeline(
            case.db, case.profile, {"custom_fields": changes}, changes, user=user,
        )
        case.db.commit()
        assert result.profile.web_login == "internal-call"
    else:
        with pytest.raises(HTTPException) as exc:
            apply_client_card_profile_save_pipeline(case.db, case.profile, {"custom_fields": changes}, changes, user=user)
        assert exc.value.status_code == 403
        case.db.rollback()
        assert case.profile.web_login is None and case.profile.web_password_encrypted is None
    # The legacy wrapper has no actor context; it must never grant implicit privileges.
    with pytest.raises(HTTPException) as exc:
        apply_client_card_save_derivations(case.db, case.profile, {"custom_fields": changes}, changes)
    assert exc.value.status_code == 403
    case.db.rollback()
    before = (case.profile.web_login, case.profile.web_password_encrypted, case.profile.web_credentials_version)
    local_changes = {"custom_web_login": "unauthorized-local", "custom_web_password": "Unauthorized-B2!"}
    apply_client_card_profile_local_save_rules(case.db, case.profile, {"custom_fields": local_changes}, local_changes)
    case.db.flush()
    assert (case.profile.web_login, case.profile.web_password_encrypted, case.profile.web_credentials_version) == before
    assert not (web.WEB_KEYS & case.profile.custom_fields.keys())


def test_web_pipeline_rejects_unconfigured_fields_instead_of_skipping_validation(case):
    case.db.delete(case.db.get(ClientCardFieldConfig, "custom_web_login"))
    case.db.commit()
    changes = {"custom_web_login": "unconfigured"}
    with pytest.raises(HTTPException) as exc:
        apply_client_card_profile_save_pipeline(case.db, case.profile, {"custom_fields": changes}, changes, user=case.admin)
    assert exc.value.status_code == 409
    assert case.profile.web_login is None


@pytest.mark.parametrize("role", ["agent", "trainee"])
@pytest.mark.parametrize("operation", ["save", "generate", "reveal"])
def test_web_operations_require_ticket_access(case, role, operation):
    user = SimpleNamespace(id="unassigned-staff", role=role)
    with pytest.raises(HTTPException) as exc:
        if operation == "save":
            tickets.update_client_profile("ticket", ClientProfileUpdate(custom_fields={"custom_web_login": "blocked"}),
                                          db=case.db, user=user)
        elif operation == "generate":
            tickets.generate_ticket_web_credentials("ticket", WebCredentialsGeneratePayload(), db=case.db, user=user)
        else:
            tickets.reveal_ticket_web_password("ticket", db=case.db, user=user)
    assert exc.value.status_code == 403
    assert exc.value.detail["error"]["code"] == "forbidden_ticket_access"
    assert case.profile.web_login is None


@pytest.mark.parametrize("path,role", [("ticket", "admin"), ("ticket", "agent"), ("management", "admin")])
def test_bitrix_and_ordinary_custom_fields_keep_their_save_behavior(case, path, role):
    user = SimpleNamespace(id=f"staff-{role}", role=role)
    case.db.add(User(id=user.id, company_id="company", full_name="Staff", role=role))
    case.db.flush()
    case.db.get(Ticket, "ticket").assignee_id = user.id
    for key in web.WEB_KEYS:
        case.db.get(ClientCardFieldConfig, key).admin_only_edit = True
    case.db.commit()
    action = tickets.update_client_profile if path == "ticket" else management_users.update_management_user_profile
    result = action("ticket" if path == "ticket" else "client", ClientProfileUpdate(
        bitrix_login="bitrix-login", bitrix_password="Bitrix-A1!", custom_fields={"custom_other": "updated"},
    ), db=case.db, user=user)
    assert result.bitrix_login == "bitrix-login" and result.bitrix_password_set
    assert result.bitrix_password is None
    assert decrypt_secret(case.profile.bitrix_password) == "Bitrix-A1!"
    assert case.profile.custom_fields == {"custom_other": "updated"}
    assert case.profile.web_login is None and case.profile.web_password_encrypted is None


def test_save_encrypt_hash_reveal_preserve_and_version(case, caplog):
    save(case, {"custom_web_login": "User@Example.com", "custom_web_password": "Synthetic-A1!"})
    profile = case.profile
    assert profile.web_login_normalized == "user@example.com"
    assert profile.web_password_encrypted != "Synthetic-A1!"
    assert decrypt_secret(profile.web_password_encrypted) == "Synthetic-A1!"
    assert verify_password("Synthetic-A1!", profile.web_password_hash)
    assert not verify_password("wrong", profile.web_password_hash)
    assert profile.web_credentials_version == 1
    old = profile.web_password_encrypted, profile.web_password_hash
    save(case, {"custom_other": "changed"}, bitrix_login="New Bitrix")
    assert profile.web_login == "User@Example.com"
    assert (profile.web_password_encrypted, profile.web_password_hash) == old
    save(case, {"custom_web_password": "Synthetic-A1!"})
    assert profile.web_credentials_version == 1
    save(case, {"custom_web_password": "New-Synthetic-B2!"})
    assert profile.web_credentials_version == 2
    assert profile.custom_fields == {"custom_other": "changed"}
    out = _profile_out(profile, None, None).model_dump()
    assert out["web_password_set"] is True
    assert "Synthetic" not in json.dumps(out)
    assert "Synthetic" not in caplog.text
    response = management_users.reveal_management_web_password("client", db=case.db, user=case.admin)
    assert json.loads(response.body)["web_password"] == "New-Synthetic-B2!"
    assert response.headers["cache-control"] == "no-store"
    save(case, {"custom_web_password": None})
    assert profile.web_credentials_version == 2
    assert verify_password("New-Synthetic-B2!", profile.web_password_hash)
    save(case, {"custom_web_password": "", "custom_web_login": None, "custom_other": "other edit"})
    assert profile.web_login == "User@Example.com"
    assert profile.web_credentials_version == 2
    assert verify_password("New-Synthetic-B2!", profile.web_password_hash)


def test_generation_drafts_and_confirmation(case):
    db, profile = case.db, case.profile
    for _ in range(12):
        password = web.generate_password()
        assert len(password) == 12
        assert all(re.search(pattern, password) for pattern in (r"[a-z]", r"[A-Z]", r"[0-9]", r"[^a-zA-Z0-9]"))
    first = web.generate_draft(db, profile, WebCredentialsGeneratePayload())
    assert first["web_login"] == "i.ivanov@elitesochi.com"
    assert profile.web_login is None and profile.web_password_encrypted is None
    assert not db.new and not db.dirty
    response = management_users.generate_management_web_credentials("client", WebCredentialsGeneratePayload(), db=db, user=case.admin)
    assert json.loads(response.body)["web_login"] == first["web_login"]
    assert response.headers["cache-control"] == "no-store"
    assert profile.web_password_encrypted is None
    assert web.generate_draft(db, profile, WebCredentialsGeneratePayload(web_login="manual"))["web_login"] == "manual"
    assert web.generate_draft(db, profile, WebCredentialsGeneratePayload(web_password_set=True))["web_password"] is None
    with pytest.raises(HTTPException) as exc:
        web.generate_draft(db, profile, WebCredentialsGeneratePayload(web_login="manual", web_password_set=True))
    assert exc.value.status_code == 409
    regenerated = web.generate_draft(db, profile, WebCredentialsGeneratePayload(web_login="manual", web_password_set=True, regenerate=True))
    assert regenerated["web_password"] != first["web_password"]
    with pytest.raises(HTTPException) as exc:
        web.generate_draft(db, profile, WebCredentialsGeneratePayload(contact_name="Иван", last_name=None))
    assert exc.value.status_code == 422


def test_collisions_manual_error_and_database_uniqueness(case, monkeypatch):
    db, profile = case.db, case.profile
    for index, login in enumerate(["BITRIX", "I.IVANOV@elitesochi.com", "iv.ivanov@elitesochi.com", "iva.ivanov@elitesochi.com"]):
        ident = f"other-{index}"
        db.add(Client(id=ident, company_id="company", display_name="Other"))
        db.flush()
        db.add(ClientProfile(id=ident, client_id=ident, web_login=login, web_login_normalized=login.casefold()))
    db.commit()
    assert web.generate_login(db, profile, bitrix_login="bitrix", contact_name=profile.contact_name) == "iva.ivanov2@elitesochi.com"
    assert web.generate_login(db, profile, bitrix_login="available") == "available"
    save(case, {"custom_web_login": "original", "custom_web_password": "Safe-A1!"})
    encrypted = profile.web_password_encrypted
    with pytest.raises(HTTPException) as exc:
        save(case, {"custom_web_login": "bItRiX", "custom_web_password": "Other-B2!"})
    assert exc.value.status_code == 409
    db.rollback()
    db.refresh(profile)
    assert profile.web_login == "original" and profile.web_password_encrypted == encrypted
    # A competing transaction can claim the login after the availability query.
    monkeypatch.setattr(web, "login_available", lambda *args: True)
    with pytest.raises(HTTPException) as exc:
        save(case, {"custom_web_login": "BITRIX", "custom_web_password": "Racing-B2!"}, contact_name="Changed")
    assert exc.value.status_code == 409
    db.refresh(profile)
    assert profile.web_login == "original" and profile.web_password_encrypted == encrypted
    assert profile.contact_name == "Иван Сергеевич Иванов"
    with pytest.raises(IntegrityError):
        with db.begin_nested():
            db.execute(text("UPDATE client_profiles SET web_login_normalized='bitrix' WHERE id='profile'"))


def test_reveal_and_generate_authorization_and_missing_profile(case):
    agent = SimpleNamespace(id="other-agent", role="agent")
    for action in (management_users.reveal_management_web_password, management_users.generate_management_web_credentials):
        kwargs = {"payload": WebCredentialsGeneratePayload()} if "generate" in action.__name__ else {}
        with pytest.raises(HTTPException) as exc:
            action("client", db=case.db, user=agent, **kwargs)
        assert exc.value.status_code == 403
    with pytest.raises(HTTPException) as exc:
        tickets.reveal_ticket_web_password("ticket", db=case.db, user=agent)
    assert exc.value.status_code == 403
    case.db.get(ClientCardFieldConfig, "custom_web_password").admin_only_edit = True
    case.db.add(User(id=agent.id, company_id="company", full_name="Synthetic agent", role="agent"))
    case.db.flush()
    case.db.get(Ticket, "ticket").assignee_id = agent.id
    case.db.commit()
    response = tickets.reveal_ticket_web_password("ticket", db=case.db, user=agent)
    assert response.status_code == 200
    assert response.headers["cache-control"] == "no-store"
    with pytest.raises(HTTPException) as exc:
        management_users.reveal_management_web_password("missing", db=case.db, user=case.admin)
    assert exc.value.status_code == 404
    assert case.db.query(ClientProfile).count() == 1


def test_secret_storage_failure_and_reveal_failure_do_not_disclose_values(case, monkeypatch, caplog):
    save(case, {"custom_web_login": "user", "custom_web_password": "Protected-A1!"})
    encrypted, hashed = case.profile.web_password_encrypted, case.profile.web_password_hash
    monkeypatch.setattr(settings, "bitrix_password_encryption_key", None)
    with pytest.raises(HTTPException) as exc:
        save(case, {"custom_web_password": "Do-not-log-B2!"})
    assert exc.value.status_code == 503
    assert "Do-not-log" not in str(exc.value.detail) + caplog.text
    case.db.rollback()
    case.db.refresh(case.profile)
    assert (case.profile.web_password_encrypted, case.profile.web_password_hash) == (encrypted, hashed)
    with pytest.raises(HTTPException) as exc:
        management_users.reveal_management_web_password("client", db=case.db, user=case.admin)
    assert exc.value.status_code == 503
    assert "Protected" not in str(exc.value.detail) + caplog.text


def test_legacy_values_are_not_exposed_and_derivation_cannot_write_them(case):
    profile = case.profile
    profile.custom_fields = {"custom_web_password": "Legacy-Synthetic!", "custom_web_login": "legacy", "custom_other": "kept"}
    case.db.commit()
    assert "Legacy-Synthetic!" not in _profile_out(profile, None, None).model_dump_json()
    with pytest.raises(HTTPException):
        normalize_client_card_derived_value("custom_web_password", "secret", {}, set())


@pytest.mark.parametrize("conflict", [False, True])
def test_legacy_migration_repeatable_and_fails_without_partial_data_loss(engine, conflict):
    with engine.begin() as c:
        c.execute(text("CREATE TABLE client_profiles (id VARCHAR(36) PRIMARY KEY, custom_fields JSON)"))
        legacy = json.dumps({"custom_web_login": "User", "custom_web_password": "Legacy-A1!", "custom_other": 3})
        c.execute(text("INSERT INTO client_profiles (id,custom_fields) VALUES ('one',:legacy)"), {"legacy": legacy})
        if conflict:
            c.execute(text("INSERT INTO client_profiles (id,custom_fields) VALUES ('two',:legacy)"), {"legacy": json.dumps({"custom_web_login": "USER"})})
    if conflict:
        with pytest.raises(RuntimeError, match="migration stopped"):
            schema_evolution.ensure_client_profile_columns()
        with engine.connect() as c:
            raw = c.execute(text("SELECT custom_fields FROM client_profiles WHERE id='one'")).scalar_one()
        assert "custom_web_password" in (json.loads(raw) if isinstance(raw, str) else raw)
        return
    schema_evolution.ensure_client_profile_columns()
    with engine.connect() as c:
        first = dict(c.execute(text("SELECT * FROM client_profiles")).mappings().one())
    schema_evolution.ensure_client_profile_columns()
    with engine.connect() as c:
        second = dict(c.execute(text("SELECT * FROM client_profiles")).mappings().one())
    assert second == first
    assert first["web_login_normalized"] == "user"
    assert decrypt_secret(first["web_password_encrypted"]) == "Legacy-A1!"
    assert verify_password("Legacy-A1!", first["web_password_hash"])
    assert first["web_credentials_version"] == 1
    raw = first["custom_fields"]
    assert (json.loads(raw) if isinstance(raw, str) else raw) == {"custom_other": 3}
    with pytest.raises(IntegrityError), engine.begin() as c:
        c.execute(text("INSERT INTO client_profiles(id,web_login_normalized) VALUES ('duplicate','user')"))


def test_new_schema_migration_is_repeatable_and_secret_keys_are_not_in_exports(case):
    save(case, {"custom_web_login": "user", "custom_web_password": "Export-Synthetic!"})
    schema_evolution.ensure_client_profile_columns()
    schema_evolution.ensure_client_profile_columns()
    # Export selection uses Telegram contacts; add a synthetic link, never a real export.
    from api.app.models import TelegramContact
    case.db.add(TelegramContact(id="contact", company_id="company", client_id="client", external_user_id="1", external_chat_id="1"))
    case.db.commit()
    exported = client_export._payload(case.db, 1)
    serialized = json.dumps(exported, default=str)
    assert "Export-Synthetic!" not in serialized
    profile = exported["clients"][0]["profile"]
    assert profile["web_password_hash"] is None and profile["web_password_encrypted"] is None
    assert profile["custom_fields"] == {"custom_other": "kept"}


@pytest.mark.parametrize("kind", ["custom", "technical"])
def test_general_import_refuses_credentials_instead_of_losing_or_copying_them(case, kind):
    from api.app.services import client_import
    raw = json.loads(json.dumps(client_export._record(case.profile), default=client_export._json_default))
    if kind == "custom":
        raw["custom_fields"]["custom_web_password"] = "Never-discard-A1!"
    else:
        raw["web_password_encrypted"] = "Never-copy-ciphertext"
    with pytest.raises(client_import.ClientImportError) as exc:
        client_import._decode_row(ClientProfile, raw)
    assert "Never-" not in str(exc.value)
    assert case.profile.web_password_encrypted is None


@pytest.mark.parametrize("path", ["ticket", "management"])
@pytest.mark.parametrize("role", ["admin", "agent", "trainee", "chiki_piki"])
@pytest.mark.parametrize("admin_only", [False, True])
@pytest.mark.parametrize("editable", [False, True])
def test_web_generation_permissions_on_both_paths(case, path, role, admin_only, editable):
    user = SimpleNamespace(id=f"staff-{role}", role=role)
    case.db.add(User(id=user.id, company_id="company", full_name="Staff", role=role))
    case.db.flush()
    case.db.get(Ticket, "ticket").assignee_id = user.id
    case.profile.destination = "Колл Центр"
    for key in web.WEB_KEYS:
        field = case.db.get(ClientCardFieldConfig, key)
        field.admin_only_edit, field.is_editable = admin_only, editable
    case.db.commit()
    action = tickets.generate_ticket_web_credentials if path == "ticket" else management_users.generate_management_web_credentials
    allowed = editable and (role == "admin" or (path == "ticket" and role in {"agent", "trainee"}))
    if allowed:
        response = action("ticket" if path == "ticket" else "client", WebCredentialsGeneratePayload(), db=case.db, user=user)
        draft = json.loads(response.body)
        assert draft["web_login"] and draft["web_password"]
        assert response.headers["cache-control"] == "no-store"
    else:
        with pytest.raises(HTTPException) as exc:
            action("ticket" if path == "ticket" else "client", WebCredentialsGeneratePayload(), db=case.db, user=user)
        assert exc.value.status_code == 403
    assert case.profile.web_login is None and case.profile.web_password_encrypted is None
    assert case.profile.web_credentials_version == 0


def test_observer_can_read_accessible_card_but_cannot_change_web_credentials(case):
    case.profile.destination = "Колл Центр"
    case.db.commit()
    save(case, {"custom_web_login": "readable", "custom_web_password": "Readable-A1!"})
    observer = SimpleNamespace(id="observer", role="chiki_piki")
    profile = tickets.get_current_client_profile("ticket", db=case.db, user=observer)
    assert profile.web_login == "readable" and profile.web_password_set
    with pytest.raises(HTTPException) as exc:
        tickets.reveal_ticket_web_password("ticket", db=case.db, user=observer)
    assert exc.value.status_code == 403


@pytest.fixture
def password_http_case(case):
    save(case, {"custom_web_login": "visible-login", "custom_web_password": "Web-Secret-A1!"},
         bitrix_password="Bitrix-Secret-B2!")
    case.profile.destination = "\u041a\u043e\u043b\u043b \u0426\u0435\u043d\u0442\u0440"
    for role in ["admin", "agent", "trainee", "chiki_piki"]:
        case.db.add(User(id=f"staff-{role}", company_id="company", full_name="Staff", role=role))
    case.db.commit()
    app = FastAPI()
    app.include_router(tickets.router)
    app.include_router(management_users.router)
    app.dependency_overrides[get_db] = lambda: case.db
    return app


@pytest.mark.parametrize("role", ["admin", "agent", "trainee", "chiki_piki"])
@pytest.mark.parametrize("path,secret", [
    ("/api/v1/tickets/ticket/client-profile/web-password", "Web-Secret-A1!"),
    ("/api/v1/tickets/ticket/client-profile/bitrix-password", "Bitrix-Secret-B2!"),
    ("/api/v1/management/users/client/profile/web-password", "Web-Secret-A1!"),
    ("/api/v1/management/users/client/profile/bitrix-password", "Bitrix-Secret-B2!"),
])
def test_password_reveal_http_permissions(case, password_http_case, monkeypatch, role, path, secret):
    app = password_http_case
    user = SimpleNamespace(id=f"staff-{role}", role=role)
    case.db.get(Ticket, "ticket").assignee_id = user.id
    case.db.commit()
    app.dependency_overrides[get_current_user] = lambda: user
    if role == "chiki_piki":
        def forbidden_decrypt(*args):
            pytest.fail("Observer request must be rejected before decryption")
        for module in [web, tickets, management_users]:
            monkeypatch.setattr(module, "decrypt_secret", forbidden_decrypt)
    with TestClient(app) as client:
        response = client.get(path)
    allowed = role != "chiki_piki" and ("/tickets/" in path or role == "admin")
    assert response.status_code == (200 if allowed else 403)
    if allowed:
        assert secret in response.json().values()
        if path.endswith("/bitrix-password"):
            assert response.headers["cache-control"] == "no-store"
    else:
        assert "Web-Secret" not in response.text and "Bitrix-Secret" not in response.text
        assert case.profile.web_password_encrypted not in response.text
        assert case.profile.bitrix_password not in response.text
        assert case.profile.web_password_hash not in response.text


@pytest.mark.parametrize("path", [
    "/api/v1/tickets/ticket/client-profile/bitrix-password",
    "/api/v1/management/users/client/profile/bitrix-password",
])
def test_missing_bitrix_password_reveal_is_not_cached(case, password_http_case, path):
    case.profile.bitrix_password = None
    case.db.commit()
    password_http_case.dependency_overrides[get_current_user] = lambda: SimpleNamespace(id="staff-admin", role="admin")

    with TestClient(password_http_case) as client:
        response = client.get(path)

    assert response.status_code == 200
    assert response.json() == {"bitrix_password": None}
    assert response.headers["cache-control"] == "no-store"


@pytest.mark.parametrize("path", [
    "/api/v1/tickets/ticket/client-profile",
    "/api/v1/tickets/ticket",
    "/api/v1/management/users/client/profile",
])
def test_observer_ordinary_card_http_never_exposes_passwords(case, password_http_case, path):
    app = password_http_case
    app.dependency_overrides[get_current_user] = lambda: SimpleNamespace(id="staff-chiki_piki", role="chiki_piki")
    # Even stale JSON copies must not escape through the standard card serializer.
    case.profile.custom_fields = {"custom_web_password": "Legacy-Web-C3!", "bitrix_password": "Legacy-Bitrix-D4!"}
    case.db.commit()
    with TestClient(app) as client:
        response = client.get(path)
    assert response.status_code == (403 if "/management/" in path else 200)
    for secret in ["Web-Secret-A1!", "Bitrix-Secret-B2!", "Legacy-Web-C3!", "Legacy-Bitrix-D4!",
                   case.profile.web_password_encrypted, case.profile.web_password_hash, case.profile.bitrix_password]:
        assert secret not in response.text
    if response.status_code == 200:
        profile = response.json().get("client_profile", response.json())
        assert profile["bitrix_password"] is None
        assert profile["bitrix_password_set"] and profile["web_password_set"]
        assert "web_password_encrypted" not in profile and "web_password_hash" not in profile


@pytest.mark.parametrize("path", ["ticket", "management"])
@pytest.mark.parametrize("login,password_set,regenerate", [
    (None, False, False), (None, True, False), ("manual-login", False, False), ("manual-login", True, True),
])
def test_generation_uses_bitrix_sources_and_only_missing_credentials(case, path, login, password_set, regenerate):
    case.profile.bitrix_login = "stored-bitrix"
    case.profile.contact_name = None
    case.profile.last_name = None
    case.db.commit()
    action = tickets.generate_ticket_web_credentials if path == "ticket" else management_users.generate_management_web_credentials
    response = action("ticket" if path == "ticket" else "client", WebCredentialsGeneratePayload(
        web_login=login, web_password_set=password_set, regenerate=regenerate,
    ), db=case.db, user=case.admin)
    draft = json.loads(response.body)
    assert draft["web_login"] == ("stored-bitrix" if not login or regenerate else login)
    assert bool(draft["web_password"]) == (not password_set or regenerate)
    edited = action("ticket" if path == "ticket" else "client", WebCredentialsGeneratePayload(
        bitrix_login="edited-bitrix", web_login=None, web_password_set=True,
    ), db=case.db, user=case.admin)
    assert json.loads(edited.body) == {"web_login": "edited-bitrix", "web_password": None}
    assert case.profile.web_login is None and case.profile.web_password_encrypted is None
    # An explicit null is intentionally different from an omitted source. The UI must
    # not send it merely because the editor lacks a Bitrix control.
    with pytest.raises(HTTPException) as exc:
        action("ticket" if path == "ticket" else "client", WebCredentialsGeneratePayload(
            bitrix_login=None, web_login=None, web_password_set=True,
        ), db=case.db, user=case.admin)
    assert exc.value.status_code == 422


@pytest.mark.parametrize("path", ["ticket", "management"])
def test_explicit_web_password_clear_preserves_login_and_bitrix(case, path):
    save(case, {"custom_web_login": "keep-login", "custom_web_password": "Original-Clear-A1!"},
         bitrix_login="keep-bitrix", bitrix_password="Bitrix-Keep-B2!")
    action = tickets.update_client_profile if path == "ticket" else management_users.update_management_user_profile
    target = "ticket" if path == "ticket" else "client"
    bitrix = case.profile.bitrix_login, case.profile.bitrix_password
    original = case.profile.web_password_encrypted, case.profile.web_password_hash, case.profile.web_credentials_version
    for payload in [ClientProfileUpdate(contact_name="Changed"),
                    ClientProfileUpdate(custom_fields={"custom_web_password": ""}),
                    ClientProfileUpdate(custom_fields={"custom_web_password": None}),
                    ClientProfileUpdate(clear_web_password=False)]:
        action(target, payload, db=case.db, user=case.admin)
        assert (case.profile.web_password_encrypted, case.profile.web_password_hash, case.profile.web_credentials_version) == original
    response = action(target, ClientProfileUpdate(clear_web_password=True, custom_fields={"custom_web_password": None}),
                      db=case.db, user=case.admin)
    assert not response.web_password_set
    assert case.profile.web_password_encrypted is None and case.profile.web_password_hash is None
    assert case.profile.web_credentials_version == original[-1] + 1
    assert case.profile.web_login == "keep-login"
    assert (case.profile.bitrix_login, case.profile.bitrix_password) == bitrix
    case.db.expire_all()
    assert not _profile_out(case.profile, None, None).web_password_set
    assert web.reveal(case.profile) == {"web_password": None}
    reveal = tickets.reveal_ticket_web_password if path == "ticket" else management_users.reveal_management_web_password
    assert json.loads(reveal(target, db=case.db, user=case.admin).body) == {"web_password": None}
    # A repeated deletion does not advance the version when nothing changes.
    action(target, ClientProfileUpdate(clear_web_password=True), db=case.db, user=case.admin)
    assert case.profile.web_credentials_version == original[-1] + 1
    generate = tickets.generate_ticket_web_credentials if path == "ticket" else management_users.generate_management_web_credentials
    draft = json.loads(generate(target, WebCredentialsGeneratePayload(), db=case.db, user=case.admin).body)
    assert draft["web_login"] == "keep-login" and draft["web_password"]
    assert case.profile.web_password_encrypted is None
    action(target, ClientProfileUpdate(custom_fields={"custom_web_password": draft["web_password"]}), db=case.db, user=case.admin)
    assert verify_password(draft["web_password"], case.profile.web_password_hash)
    assert case.profile.web_credentials_version == original[-1] + 2
    assert (case.profile.bitrix_login, case.profile.bitrix_password) == bitrix


@pytest.mark.parametrize("path", ["ticket", "management"])
@pytest.mark.parametrize("role", ["admin", "agent", "trainee", "chiki_piki"])
@pytest.mark.parametrize("editable", [True, False])
def test_web_password_clear_requires_role_access_and_editable_field(case, path, role, editable):
    save(case, {"custom_web_login": "keep", "custom_web_password": "Clear-Protected-A1!"})
    user = SimpleNamespace(id=f"staff-{role}", role=role)
    case.db.add(User(id=user.id, company_id="company", full_name="Staff", role=role))
    case.db.flush()
    case.db.get(Ticket, "ticket").assignee_id = user.id
    case.profile.destination = "\u041a\u043e\u043b\u043b \u0426\u0435\u043d\u0442\u0440"
    field = case.db.get(ClientCardFieldConfig, "custom_web_password")
    field.admin_only_edit, field.is_editable = True, editable
    case.db.commit()
    original = case.profile.web_password_encrypted, case.profile.web_password_hash, case.profile.web_credentials_version
    action = tickets.update_client_profile if path == "ticket" else management_users.update_management_user_profile
    allowed = editable and (role == "admin" or (path == "ticket" and role in {"agent", "trainee"}))
    if allowed:
        action("ticket" if path == "ticket" else "client", ClientProfileUpdate(clear_web_password=True), db=case.db, user=user)
        assert case.profile.web_password_encrypted is None and case.profile.web_password_hash is None
    else:
        with pytest.raises(HTTPException) as exc:
            action("ticket" if path == "ticket" else "client", ClientProfileUpdate(clear_web_password=True), db=case.db, user=user)
        assert exc.value.status_code == 403
        case.db.rollback()
        assert (case.profile.web_password_encrypted, case.profile.web_password_hash, case.profile.web_credentials_version) == original


def test_clear_flag_cannot_bypass_pipeline_authorization_or_conflict_with_replacement(case):
    save(case, {"custom_web_password": "Keep-Pipeline-A1!"})
    original = case.profile.web_password_encrypted, case.profile.web_password_hash, case.profile.web_credentials_version
    with pytest.raises(HTTPException) as exc:
        apply_client_card_profile_save_pipeline(case.db, case.profile, {"clear_web_password": True}, {})
    assert exc.value.status_code == 403
    with pytest.raises(HTTPException) as exc:
        save(case, {"custom_web_password": "Conflicting-B2!"}, clear_web_password=True)
    assert exc.value.status_code == 422
    case.db.rollback()
    assert (case.profile.web_password_encrypted, case.profile.web_password_hash, case.profile.web_credentials_version) == original


def test_actual_browser_api_wire_body_is_parsed_as_generate_payload(case, password_http_case):
    wire_path = os.environ.get("WEB_CREDENTIALS_WIRE_PATH")
    if not wire_path:
        pytest.skip("Set WEB_CREDENTIALS_WIRE_PATH to the real browser API capture")
    records = json.loads(open(wire_path, encoding="utf-8").read())
    # Required scenario: saved WEB password, missing WEB login and a Bitrix source.
    records = [r for r in records if r["data"].get("web_login") is None and r["data"].get("web_password_set")
               and r["data"].get("bitrix_login")]
    assert records
    app = password_http_case
    app.dependency_overrides[get_current_user] = lambda: case.admin
    original = case.profile.web_password_encrypted, case.profile.web_password_hash, case.profile.web_credentials_version
    with TestClient(app) as client:
        for context in ["/tickets/", "/management/"]:
            record = next(r for r in records if context in r["path"])
            response = client.post(record["path"], content=record["body"], headers={"Content-Type": record["headers"]["content-type"]})
            assert response.status_code == 200, response.text
            assert response.json() == {"web_login": record["data"]["bitrix_login"], "web_password": None}
            # Reproduce the original TicketApi request with a string body and no JSON header.
            broken = client.post(record["path"], content=record["body"], headers={"Content-Type": "text/plain;charset=UTF-8"})
            assert broken.status_code == 422
            assert broken.json()["detail"][0]["loc"] == ["body"]
            assert broken.json()["detail"][0]["type"] == "model_attributes_type"
    assert (case.profile.web_password_encrypted, case.profile.web_password_hash, case.profile.web_credentials_version) == original


@pytest.mark.parametrize("path", ["/api/v1/tickets/ticket/client-profile", "/api/v1/management/users/client/profile"])
@pytest.mark.parametrize("browser_body", [False, True])
def test_clear_web_password_http_commit_and_fresh_read(case, engine, password_http_case, path, browser_body):
    app = password_http_case
    app.dependency_overrides[get_current_user] = lambda: case.admin
    def request_db():
        with Session(engine, autoflush=False) as db:
            yield db
    app.dependency_overrides[get_db] = request_db
    wire = None
    if browser_body:
        wire_path = os.environ.get("WEB_PASSWORD_CLEAR_WIRE_PATH")
        if not wire_path:
            pytest.skip("Set WEB_PASSWORD_CLEAR_WIRE_PATH to the browser PATCH capture")
        with open(wire_path, encoding="utf-8") as source:
            wire = next(r for r in json.load(source) if r["path"] == path and r["data"].get("clear_web_password") is True)
        case.profile.web_login = "keep-login"
        case.profile.web_login_normalized = "keep-login"
        case.profile.bitrix_login = "bitrix.current"
        case.db.commit()
    original_version = case.profile.web_credentials_version
    original_login = case.profile.web_login
    original_bitrix = case.profile.bitrix_password
    with TestClient(app) as client:
        assert client.get(path).json()["web_password_set"] is True
        assert client.get(path + "/web-password").json() == {"web_password": "Web-Secret-A1!"}
        with Session(engine) as before:
            row = before.scalar(select(ClientProfile).where(ClientProfile.client_id == "client"))
            original_secrets = row.web_password_encrypted, row.web_password_hash, row.web_credentials_version
        if not browser_body:
            # Reading and unrelated/legacy empty PATCH bodies must preserve the password.
            for patch in [{"contact_name": "Changed"}, {"custom_fields": {"custom_web_password": None}},
                          {"custom_fields": {"custom_web_password": ""}}]:
                unchanged = client.patch(path, json=patch)
                assert unchanged.status_code == 200 and unchanged.json()["web_password_set"] is True
                with Session(engine) as check:
                    row = check.scalar(select(ClientProfile).where(ClientProfile.client_id == "client"))
                    assert (row.web_password_encrypted, row.web_password_hash, row.web_credentials_version) == original_secrets
        if wire:
            response = client.patch(path, content=wire["body"], headers={"content-type": wire["headers"]["content-type"]})
        else:
            response = client.patch(path, json={"clear_web_password": True,
                "custom_fields": {"custom_web_login": "visible-login", "custom_web_password": None}})
        assert response.status_code == 200, response.text
        assert response.json()["web_password_set"] is False
        with Session(engine) as fresh:
            profile = fresh.scalar(select(ClientProfile).where(ClientProfile.client_id == "client"))
            assert profile.web_password_encrypted is None and profile.web_password_hash is None
            assert profile.web_credentials_version == original_version + 1
            assert profile.web_login == original_login
            assert profile.bitrix_password == original_bitrix
        reopened = client.get(path)
        assert reopened.status_code == 200 and reopened.json()["web_password_set"] is False
        revealed = client.get(path + "/web-password")
        assert revealed.status_code == 200 and revealed.json() == {"web_password": None}
