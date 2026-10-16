from datetime import datetime
import json
import os
from pathlib import Path
import stat

from cryptography.fernet import Fernet
import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from api.app.db import Base
from api.app.models import Client, ClientProfile, Company, TelegramContact
from api.app.services import client_export
from api.app.services.encryption import encrypt_secret
from api.app.config import settings


@pytest.fixture
def db(monkeypatch):
    monkeypatch.setattr(settings, "bitrix_password_encryption_key", Fernet.generate_key().decode())
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    with Session(engine, autoflush=False) as session:
        yield session
    engine.dispose()


def seed(db, count=2):
    db.add(Company(id="company", name="Synthetic company"))
    for index in range(count):
        client = Client(id=f"client-{index}", company_id="company", display_name="Synthetic client")
        profile = ClientProfile(id=f"profile-{index}", client_id=client.id,
                                employee_type="Employee", custom_fields={"nested": [None, False, 0, ""]},
                                bitrix_password=encrypt_secret("synthetic-password"), is_verified=False)
        contact = TelegramContact(id=f"contact-{index}", client_id=client.id, company_id="company",
                                  external_user_id=str(index), external_chat_id=str(index), is_blocked=False,
                                  active_menu_message_id="0", menu_message_ids_json=[0, "1"])
        # Populate every optional string to exercise full-field export.
        for column in profile.__table__.columns:
            if column.nullable and column.type.python_type is str and column.key != "bitrix_password":
                setattr(profile, column.key, f"synthetic-{column.key}")
        db.add_all([client, profile, contact])
    db.add(Client(id="system", company_id="company", display_name="System"))
    db.add(ClientProfile(id="system-profile", client_id="system", custom_fields={}))
    db.commit()


def normalized(row):
    return {column.key: value.isoformat() if isinstance(value := getattr(row, column.key), datetime) else value
            for column in row.__table__.columns}


def test_complete_142_client_export_is_private_and_read_only(db, tmp_path, capsys, caplog):
    seed(db, 142)
    profile = db.get(ClientProfile, "profile-0")
    encrypted = profile.bitrix_password
    target = tmp_path / "clients.json"
    assert client_export.export_clients(db, target) == 142
    data = json.loads(target.read_text(encoding="utf-8"))
    assert set(data) == {"format_version", "metadata", "clients"}
    assert settings.bitrix_password_encryption_key not in target.read_text()
    assert data["format_version"] == 1
    assert data["metadata"]["client_count"] == 142
    assert len(data["clients"]) == 142
    for entry in data["clients"]:
        assert set(entry) == {"client", "profile", "telegram_contact"}
        client = db.get(Client, entry["client"]["id"])
        assert entry["client"] == normalized(client)
        expected = normalized(db.get(ClientProfile, entry["profile"]["id"]))
        expected["bitrix_password"] = "synthetic-password"
        assert entry["profile"] == expected
        assert entry["telegram_contact"] == normalized(db.get(TelegramContact, entry["telegram_contact"]["id"]))
        assert entry["profile"]["client_id"] == entry["telegram_contact"]["client_id"] == client.id
    assert len({entry["telegram_contact"]["external_chat_id"] for entry in data["clients"]}) == 142
    db.expire_all()
    assert db.get(ClientProfile, "profile-0").bitrix_password == encrypted
    assert not db.new and not db.dirty and not db.deleted
    assert list(tmp_path.iterdir()) == [target]
    if os.name == "posix":
        assert stat.S_IMODE(target.stat().st_mode) == 0o600
    else:
        acl = client_export.subprocess.run(["icacls", str(target)], capture_output=True, text=True, check=True)
        assert "(I)" not in acl.stdout
    assert not capsys.readouterr().out
    assert not caplog.text


@pytest.mark.parametrize("problem", ["count", "profile", "client", "company", "chat", "duplicate_chat", "decrypt"])
def test_invalid_data_cancels_without_output(db, tmp_path, capsys, caplog, problem):
    seed(db)
    contact = db.get(TelegramContact, "contact-0")
    if problem == "profile":
        db.delete(db.get(ClientProfile, "profile-0"))
        db.commit()
    elif problem == "client":
        contact.client_id = "missing"
    elif problem == "company":
        contact.company_id = "missing"
    elif problem == "chat":
        contact.external_chat_id = " "
    elif problem == "duplicate_chat":
        contact.external_chat_id = db.get(TelegramContact, "contact-1").external_chat_id
    elif problem == "decrypt":
        # Fail after the first password was decrypted successfully.
        db.get(ClientProfile, "profile-1").bitrix_password = "synthetic-invalid-token"
    with pytest.raises(client_export.ClientExportError):
        client_export.export_clients(db, tmp_path / "clients.json", expected_count=142 if problem == "count" else 2)
    assert not list(tmp_path.iterdir())
    captured = capsys.readouterr()
    diagnostics = captured.out + captured.err + caplog.text
    assert "synthetic-password" not in diagnostics
    assert "synthetic-invalid-token" not in diagnostics
    assert "synthetic-email" not in diagnostics


@pytest.mark.parametrize("count", [2, 142])
def test_duplicate_client_exports_one_record_per_unique_chat(db, tmp_path, count):
    seed(db, count)
    for index in range(count):
        db.get(TelegramContact, f"contact-{index}").client_id = "client-0"
    db.commit()
    target = tmp_path / "clients.json"
    assert client_export.export_clients(db, target, expected_count=count) == count
    data = json.loads(target.read_text(encoding="utf-8"))
    entries = data["clients"]
    assert len(entries) == data["metadata"]["client_count"] == count
    assert {entry["telegram_contact"]["external_chat_id"] for entry in entries} == {str(index) for index in range(count)}
    assert {entry["client"]["id"] for entry in entries} == {"client-0"}
    assert {entry["profile"]["client_id"] for entry in entries} == {"client-0"}
    assert {entry["telegram_contact"]["client_id"] for entry in entries} == {"client-0"}
    assert all(entry["client"] == entries[0]["client"] for entry in entries)
    assert all(entry["profile"] == entries[0]["profile"] for entry in entries)


@pytest.mark.parametrize("password", [None, ""])
def test_empty_password_is_preserved(db, tmp_path, password):
    seed(db)
    db.get(ClientProfile, "profile-0").bitrix_password = password
    target = tmp_path / "clients.json"
    client_export.export_clients(db, target, expected_count=2)
    assert json.loads(target.read_text())["clients"][0]["profile"]["bitrix_password"] == password


def test_output_path_rules_and_no_overwrite(db, tmp_path):
    seed(db)
    repository = Path(client_export.__file__).resolve().parents[3]
    nested_repo = tmp_path / "repo"
    nested_repo.mkdir()
    (nested_repo / ".git").mkdir()
    existing = tmp_path / "existing.json"
    existing.write_text("keep")
    for target in (Path("relative.json"), repository / "clients.json", nested_repo / "clients.json", existing):
        with pytest.raises(client_export.ClientExportError):
            client_export.export_clients(db, target, expected_count=2)
    assert existing.read_text() == "keep"


@pytest.mark.parametrize("git_marker", ["directory", "file"])
def test_docker_output_path_and_parent_git_repository(tmp_path, monkeypatch, git_marker):
    application = tmp_path / "app"
    services = application / "app" / "services"
    services.mkdir(parents=True)
    monkeypatch.setattr(client_export, "__file__", str(services / "client_export.py"))
    outside = tmp_path / "tmp"
    outside.mkdir()
    target = outside / "esdesk-clients.json"
    assert client_export._output_path(target) == target.resolve()
    for parent in (application, services):
        with pytest.raises(client_export.ClientExportError):
            client_export._output_path(parent / "clients.json")
    # A parent checkout/worktree must still be protected outside the app root.
    marker = tmp_path / ".git"
    if git_marker == "directory":
        marker.mkdir()
    else:
        marker.write_text("gitdir: elsewhere")
    with pytest.raises(client_export.ClientExportError):
        client_export._output_path(target)


@pytest.mark.parametrize("failure", ["permissions", "write", "publish"])
def test_io_failures_remove_temporary_files(db, tmp_path, monkeypatch, failure):
    seed(db)
    def fail(*args, **kwargs):
        raise OSError("synthetic-sensitive-diagnostic")
    if failure == "permissions":
        monkeypatch.setattr(client_export, "_restrict_permissions", fail)
    elif failure == "write":
        monkeypatch.setattr(client_export.os, "fsync", fail)
    else:
        monkeypatch.setattr(client_export.os, "link", fail)
    with pytest.raises(client_export.ClientExportError) as error:
        client_export.export_clients(db, tmp_path / "clients.json", expected_count=2)
    assert "synthetic-sensitive-diagnostic" not in str(error.value)
    assert not list(tmp_path.iterdir())


def test_cli_failure_never_prints_underlying_exception(monkeypatch, capsys, tmp_path):
    class FailingSession:
        def __enter__(self):
            raise RuntimeError("synthetic-personal-data-and-secret")
        def __exit__(self, *args):
            pass
    monkeypatch.setattr(client_export, "SessionLocal", FailingSession)
    assert client_export.cli(["--output", str(tmp_path / "clients.json")]) == 1
    captured = capsys.readouterr()
    assert "synthetic-personal-data-and-secret" not in captured.out + captured.err
    assert not list(tmp_path.iterdir())


def test_publication_exposes_only_complete_json_and_never_replaces_racing_file(db, tmp_path, monkeypatch):
    seed(db)
    target = tmp_path / "clients.json"
    link = os.link
    def concurrent_link(source, destination):
        assert not target.exists()
        complete = json.loads(Path(source).read_text())
        assert len(complete["clients"]) == 2
        target.write_text("existing-file")
        link(source, destination)
    monkeypatch.setattr(client_export.os, "link", concurrent_link)
    with pytest.raises(client_export.ClientExportError):
        client_export.export_clients(db, target, expected_count=2)
    assert target.read_text() == "existing-file"
    assert list(tmp_path.iterdir()) == [target]


def test_cli_success_reports_only_count(db, tmp_path, monkeypatch, capsys):
    seed(db, 142)
    monkeypatch.setattr(client_export, "SessionLocal", lambda: db)
    assert client_export.cli(["--output", str(tmp_path / "clients.json")]) == 0
    captured = capsys.readouterr()
    assert captured.out == "Export complete: 142 clients. Transfer securely and delete after successful migration.\n"
    assert captured.err == ""
