import copy
from datetime import datetime
import json

from cryptography.fernet import Fernet, InvalidToken
import pytest
from sqlalchemy import create_engine, event, select
from sqlalchemy.orm import Session

from api.app.config import settings
from api.app.db import Base
from api.app.models import Client, ClientProfile, Company, TelegramContact, Ticket, User
from api.app.services import client_export, client_import
from api.app.services.encryption import decrypt_secret, encrypt_secret

OLD = datetime(2000, 1, 1)
DEV_DATE = datetime(2020, 1, 1)


def engine():
    result = create_engine("sqlite:///:memory:")
    @event.listens_for(result, "connect")
    def foreign_keys(connection, _):
        connection.execute("PRAGMA foreign_keys=ON")
    Base.metadata.create_all(result)
    return result


@pytest.fixture
def migration(monkeypatch):
    dev_key, prod_key = Fernet.generate_key(), Fernet.generate_key()
    monkeypatch.setattr(settings, "bitrix_password_encryption_key", dev_key.decode())
    dev = engine()
    with Session(dev) as db:
        db.add(Company(id="dev-company", name="Synthetic DEV"))
        db.flush()
        for index in range(142):
            db.add(Client(id=f"dev-client-{index}", company_id="dev-company", display_name=f"dev-name-{index}"))
        db.flush()
        for index in range(142):
            db.add(ClientProfile(id=f"dev-profile-{index}", client_id=f"dev-client-{index}",
                contact_name=f"dev-contact-{index}", email=None, phone="   ", last_name="",
                employee_type="Employee", is_verified=False, destination="DEV destination",
                bitrix_password=encrypt_secret(f"dev-password-{index}"),
                custom_fields={"replace": "DEV", "keep_empty": None, "false": False, "zero": 0,
                               "nested": {"x": [None, "", False, 0]}}, created_at=DEV_DATE, updated_at=DEV_DATE))
            db.add(TelegramContact(id=f"dev-contact-id-{index}", company_id="dev-company", client_id=f"dev-client-{index}",
                external_chat_id=f"chat-{index}", external_user_id=f"dev-user-{index}", external_username=f"dev-username-{index}",
                is_blocked=False, active_menu_message_id="dev-menu", menu_message_ids_json=["dev-menu"],
                created_at=DEV_DATE, updated_at=DEV_DATE))
        db.commit()
        data = json.loads(json.dumps(client_export._payload(db, 142), default=client_export._json_default))
    dev.dispose()
    # Use numeric order for predictable input record numbers in assertions.
    data["clients"].sort(key=lambda row: int(row["client"]["id"].rsplit("-", 1)[1]))
    monkeypatch.setattr(settings, "bitrix_password_encryption_key", prod_key.decode())
    prod = engine()
    with Session(prod) as db:
        db.add(Company(id="prod-company", name="Synthetic PROD"))
        db.flush()
        for index in range(26):
            db.add(Client(id=f"prod-client-{index}", company_id="prod-company", display_name=f"prod-name-{index}"))
        db.flush()
        for index in range(25):
            db.add(ClientProfile(id=f"prod-profile-{index}", client_id=f"prod-client-{index}",
                email="prod-email", phone="prod-phone", last_name="prod-last", is_verified=True,
                bitrix_password=encrypt_secret(f"prod-password-{index}"),
                custom_fields={"replace": "PROD", "keep_empty": "keep", "only_prod": "keep", "false": True, "zero": 9},
                created_at=OLD, updated_at=OLD))
        for index in range(22):
            db.add(TelegramContact(id=f"prod-contact-{index}", company_id="prod-company", client_id=f"prod-client-{index}",
                external_chat_id=f"chat-{index}" if index < 4 else f"prod-chat-{index}", external_user_id=f"prod-user-{index}",
                external_username="prod-username", is_blocked=True, active_menu_message_id="prod-menu",
                menu_message_ids_json=["prod-menu"], created_at=OLD, updated_at=OLD))
        for index in range(7):
            db.add(User(id=f"prod-agent-{index}", company_id="prod-company", full_name="Synthetic agent", role="agent"))
        db.flush()
        for index in range(425):
            db.add(Ticket(id=f"ticket-{index}", company_id="prod-company", client_id=f"prod-client-{index % 26}",
                          assignee_id=f"prod-agent-{index % 7}", subject="Synthetic ticket"))
        db.commit()
    yield prod, data, dev_key
    prod.dispose()


def snapshot(prod):
    with prod.connect() as connection:
        return {table.name: [dict(row._mapping) for row in connection.execute(select(table).order_by(*table.primary_key.columns))]
                for table in Base.metadata.sorted_tables}


def run(prod, data, **kwargs):
    with Session(prod) as db:
        return client_import.import_clients(db, data, **kwargs)


def test_dry_run_plans_138_new_and_4_updates_without_dml(migration):
    prod, data, _ = migration
    before = snapshot(prod)
    dml = []
    @event.listens_for(prod, "before_cursor_execute")
    def statements(conn, cursor, statement, parameters, context, executemany):
        if statement.lstrip().upper().startswith(("INSERT", "UPDATE", "DELETE")):
            dml.append(statement.split()[0])
    report = run(prod, data)
    assert report["dry_run"] and not report["applied"]
    assert report["conflicts"] == []
    assert report["created"] == {"client": 138, "profile": 138, "telegram_contact": 138}
    assert report["updated"] == {"client": 4, "profile": 4, "telegram_contact": 4}
    assert "email" in report["records"][0]["skipped"]["profile"]
    assert "contact_name" in report["records"][0]["updated"]["profile"]
    assert not dml
    assert snapshot(prod) == before


def test_apply_preserves_prod_ids_tickets_users_dates_and_telegram_state(migration):
    prod, data, dev_key = migration
    before = snapshot(prod)
    report = run(prod, data, apply=True)
    assert report["applied"] and not report["conflicts"]
    after = snapshot(prod)
    assert len(after["clients"]) == 164
    assert len(after["client_profiles"]) == 163
    assert len(after["telegram_contacts"]) == 160
    for table in before:
        if table not in {"clients", "client_profiles", "telegram_contacts"}:
            assert after[table] == before[table]
    with Session(prod) as db:
        for index in range(4):
            contact = db.get(TelegramContact, f"prod-contact-{index}")
            profile = db.get(ClientProfile, f"prod-profile-{index}")
            assert contact.client_id == profile.client_id == f"prod-client-{index}"
            assert contact.company_id == "prod-company"
            assert contact.is_blocked is True
            assert contact.active_menu_message_id == "prod-menu"
            assert contact.menu_message_ids_json == ["prod-menu"]
            assert contact.created_at == profile.created_at == OLD
            assert contact.updated_at != OLD and profile.updated_at != OLD
            assert contact.external_username == f"dev-username-{index}"
            assert profile.email == "prod-email" and profile.phone == "prod-phone" and profile.last_name == "prod-last"
            assert profile.is_verified is False
            assert profile.contact_name == f"dev-contact-{index}"
            assert profile.destination == "DEV destination"
            assert profile.custom_fields == {"replace": "DEV", "keep_empty": "keep", "only_prod": "keep",
                                             "false": False, "zero": 0, "nested": {"x": [None, "", False, 0]}}
            assert decrypt_secret(profile.bitrix_password) == f"dev-password-{index}"
            with pytest.raises(InvalidToken):
                Fernet(dev_key).decrypt(profile.bitrix_password.encode())
            assert profile.bitrix_password != data["clients"][index]["profile"]["bitrix_password"]
        new = db.scalar(select(TelegramContact).where(TelegramContact.external_chat_id == "chat-4"))
        profile = db.scalar(select(ClientProfile).where(ClientProfile.client_id == new.client_id))
        assert new.company_id == "prod-company"
        assert new.id != data["clients"][4]["telegram_contact"]["id"]
        assert new.client_id != "dev-client-4"
        assert profile.id != "dev-profile-4"
        assert new.active_menu_message_id is None and new.menu_message_ids_json is None
        assert new.is_blocked is False
        assert new.created_at == profile.created_at == DEV_DATE
        for index in range(4, 22):
            contact = db.get(TelegramContact, f"prod-contact-{index}")
            assert contact.external_username == "prod-username" and contact.updated_at == OLD


def test_repeated_import_is_idempotent(migration):
    prod, data, _ = migration
    assert run(prod, data, apply=True)["applied"]
    before = snapshot(prod)
    report = run(prod, data, apply=True)
    assert report["applied"] and not report["conflicts"]
    assert all(value == 0 for value in report["created"].values())
    assert all(value == 0 for value in report["updated"].values())
    assert report["unchanged"] == dict.fromkeys(client_import.MODELS, 142)
    assert snapshot(prod) == before


@pytest.mark.parametrize("empty", [None, "", "   "])
def test_empty_dev_password_preserves_prod_ciphertext(migration, empty):
    prod, data, _ = migration
    with Session(prod) as db:
        ciphertext = db.get(ClientProfile, "prod-profile-0").bitrix_password
    data["clients"][0]["profile"]["bitrix_password"] = empty
    assert run(prod, data, apply=True)["applied"]
    with Session(prod) as db:
        assert db.get(ClientProfile, "prod-profile-0").bitrix_password == ciphertext


def test_equal_password_not_reencrypted_and_nested_false_differs_from_zero(migration):
    prod, data, _ = migration
    with Session(prod) as db:
        profile = db.get(ClientProfile, "prod-profile-0")
        profile.bitrix_password = encrypt_secret("dev-password-0")
        ciphertext = profile.bitrix_password
        profile.custom_fields = {"false": 0, "zero": False}
        db.commit()
    assert run(prod, data, apply=True)["applied"]
    with Session(prod) as db:
        profile = db.get(ClientProfile, "prod-profile-0")
        assert profile.bitrix_password == ciphertext
        assert profile.custom_fields["false"] is False
        assert type(profile.custom_fields["zero"]) is int


@pytest.mark.parametrize("offset", [0, 4])
def test_shared_source_client_creates_separate_prod_clients(migration, offset):
    prod, data, _ = migration
    data["clients"][offset + 1]["client"] = copy.deepcopy(data["clients"][offset]["client"])
    data["clients"][offset + 1]["profile"] = copy.deepcopy(data["clients"][offset]["profile"])
    data["clients"][offset + 1]["telegram_contact"]["client_id"] = f"dev-client-{offset}"
    assert run(prod, data, apply=True)["applied"]
    with Session(prod) as db:
        contacts = list(db.scalars(select(TelegramContact).where(TelegramContact.external_chat_id.in_([f"chat-{offset}", f"chat-{offset + 1}"]))))
        assert len(contacts) == len({row.client_id for row in contacts}) == 2
    assert run(prod, data, apply=True)["applied"]


def test_missing_prod_profile_is_created_without_changing_client_or_ticket(migration):
    prod, data, _ = migration
    with Session(prod) as db:
        db.delete(db.get(ClientProfile, "prod-profile-0"))
        db.commit()
    before = snapshot(prod)
    report = run(prod, data, apply=True)
    assert report["applied"] and report["created"]["profile"] == 139
    with Session(prod) as db:
        profile = db.scalar(select(ClientProfile).where(ClientProfile.client_id == "prod-client-0"))
        assert profile is not None and profile.id != "dev-profile-0"
    assert snapshot(prod)["tickets"] == before["tickets"]


@pytest.mark.parametrize("conflict", ["existing_user", "source_user", "ambiguous_company", "missing_company", "prod_links", "shared_prod", "bad_password"])
def test_conflicts_cancel_entire_apply_and_dry_run(migration, monkeypatch, conflict):
    prod, data, _ = migration
    kwargs = {}
    if conflict == "existing_user":
        data["clients"][4]["telegram_contact"]["external_user_id"] = "prod-user-4"
    elif conflict == "source_user":
        data["clients"][5]["telegram_contact"]["external_user_id"] = "dev-user-4"
    elif conflict == "ambiguous_company":
        with Session(prod) as db:
            db.add(Company(id="second-company", name="Synthetic other"))
            db.commit()
    elif conflict == "missing_company":
        kwargs["company_id"] = "not-a-company"
    elif conflict == "prod_links":
        with Session(prod) as db:
            db.add(Company(id="second-company", name="Synthetic other"))
            db.flush()
            db.get(TelegramContact, "prod-contact-0").company_id = "second-company"
            db.commit()
    elif conflict == "shared_prod":
        with Session(prod) as db:
            db.get(TelegramContact, "prod-contact-1").client_id = "prod-client-0"
            db.commit()
    else:
        with Session(prod) as db:
            db.get(ClientProfile, "prod-profile-3").bitrix_password = "invalid-synthetic-ciphertext"
            db.commit()
    before = snapshot(prod)
    for apply in (False, True):
        report = run(prod, data, apply=apply, **kwargs)
        assert not report["applied"] and report["conflicts"]
        assert snapshot(prod) == before


def test_explicit_company_resolves_ambiguity(migration):
    prod, data, _ = migration
    with Session(prod) as db:
        db.add(Company(id="second-company", name="Synthetic other"))
        db.commit()
    assert run(prod, data, apply=True, company_id="prod-company")["applied"]


@pytest.mark.parametrize("invalid", ["count", "version", "duplicate_chat", "profile_link", "company_link", "unknown_field", "date", "type"])
def test_invalid_input_is_rejected_without_database_changes(migration, invalid):
    prod, data, _ = migration
    row = data["clients"][0]
    if invalid == "count":
        data["clients"].pop()
    elif invalid == "version":
        data["format_version"] = 2
    elif invalid == "duplicate_chat":
        data["clients"][1]["telegram_contact"]["external_chat_id"] = "chat-0"
    elif invalid == "profile_link":
        row["profile"]["client_id"] = "wrong-client"
    elif invalid == "company_link":
        row["telegram_contact"]["company_id"] = "wrong-company"
    elif invalid == "unknown_field":
        row["profile"]["unknown"] = "synthetic-private-value"
    elif invalid == "date":
        row["profile"]["created_at"] = "invalid-synthetic-date"
    else:
        row["profile"]["is_verified"] = 0
    before = snapshot(prod)
    with pytest.raises(client_import.ClientImportError):
        run(prod, data, apply=True)
    assert snapshot(prod) == before


def test_flush_failure_rolls_back_already_inserted_clients_without_diagnostic_leaks(migration, caplog):
    prod, data, _ = migration
    before = snapshot(prod)
    @event.listens_for(prod, "before_cursor_execute")
    def fail(conn, cursor, statement, parameters, context, executemany):
        if statement.upper().startswith("INSERT INTO CLIENT_PROFILES"):
            raise RuntimeError("synthetic-sensitive-error")
    with pytest.raises(client_import.ClientImportError) as error:
        run(prod, data, apply=True)
    assert str(error.value) == "import_failed_transaction_rolled_back"
    assert snapshot(prod) == before
    assert "synthetic-sensitive-error" not in caplog.text


def test_cli_defaults_to_dry_run_and_reports_no_personal_data(migration, tmp_path, monkeypatch, capsys, caplog):
    prod, data, _ = migration
    path = tmp_path / "synthetic.json"
    path.write_text(json.dumps(data), encoding="utf-8")
    monkeypatch.setattr(client_import, "SessionLocal", lambda: Session(prod))
    before = snapshot(prod)
    assert client_import.cli(["import", "--input", str(path)]) == 0
    captured = capsys.readouterr()
    report = json.loads(captured.out)
    assert report["dry_run"] and not report["applied"]
    diagnostics = captured.out + captured.err + caplog.text
    for private in ("dev-password-0", "dev-user-0", "chat-0", "dev-name-0", "prod-email", settings.bitrix_password_encryption_key):
        assert private not in diagnostics
    assert snapshot(prod) == before
    assert list(tmp_path.iterdir()) == [path]


@pytest.mark.parametrize("content", ['{"private":"synthetic-private",', '{"format_version":1,"format_version":2}'])
def test_cli_malformed_json_prints_only_safe_error(tmp_path, capsys, content):
    path = tmp_path / "synthetic.json"
    path.write_text(content)
    assert client_import.cli(["import", "--input", str(path), "--apply"]) == 1
    captured = capsys.readouterr()
    assert json.loads(captured.out) == {"applied": False, "error": "import_failed_no_data_disclosed"}
    assert "synthetic-private" not in captured.out + captured.err


@pytest.mark.parametrize("empty", [None, "", "   "])
def test_new_client_empty_password_is_unset(migration, empty):
    prod, data, _ = migration
    data["clients"][4]["profile"]["bitrix_password"] = empty
    assert run(prod, data, apply=True)["applied"]
    with Session(prod) as db:
        contact = db.scalar(select(TelegramContact).where(TelegramContact.external_chat_id == "chat-4"))
        profile = db.scalar(select(ClientProfile).where(ClientProfile.client_id == contact.client_id))
        assert profile.bitrix_password is None


def test_cli_apply_is_explicit_and_creates_no_intermediate_files(migration, tmp_path, monkeypatch, capsys):
    prod, data, _ = migration
    path = tmp_path / "synthetic.json"
    path.write_text(json.dumps(data), encoding="utf-8")
    monkeypatch.setattr(client_import, "SessionLocal", lambda: Session(prod))
    assert client_import.cli(["import", "--input", str(path), "--apply"]) == 0
    report = json.loads(capsys.readouterr().out)
    assert report["applied"] and not report["dry_run"] and not report["conflicts"]
    assert len(snapshot(prod)["telegram_contacts"]) == 160
    assert list(tmp_path.iterdir()) == [path]


def test_missing_encryption_key_rolls_back_without_logging_values(migration, monkeypatch, caplog):
    prod, data, _ = migration
    before = snapshot(prod)
    monkeypatch.setattr(settings, "bitrix_password_encryption_key", None)
    report = run(prod, data, apply=True)
    assert not report["applied"] and report["conflicts"]
    assert snapshot(prod) == before
    diagnostics = json.dumps(report) + caplog.text
    assert "dev-password-0" not in diagnostics and "chat-0" not in diagnostics


def test_empty_dev_telegram_fields_preserve_prod(migration):
    prod, data, _ = migration
    data["clients"][0]["telegram_contact"].update(external_user_id="   ", external_username=None, external_display_name="")
    assert run(prod, data, apply=True)["applied"]
    with Session(prod) as db:
        contact = db.get(TelegramContact, "prod-contact-0")
        assert contact.external_user_id == "prod-user-0"
        assert contact.external_username == "prod-username"
        assert contact.external_display_name is None


def test_new_contact_without_telegram_user_is_conflict(migration):
    prod, data, _ = migration
    data["clients"][4]["telegram_contact"]["external_user_id"] = " "
    before = snapshot(prod)
    report = run(prod, data, apply=True)
    assert not report["applied"]
    assert {"index": 5, "code": "missing_telegram_user"} in report["conflicts"]
    assert snapshot(prod) == before
