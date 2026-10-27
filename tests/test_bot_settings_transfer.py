from datetime import datetime
import json
from types import SimpleNamespace
import uuid

import pytest
from sqlalchemy import create_engine, event, select
from sqlalchemy.orm import Session

from api.app.config import settings
from api.app.db import Base
from api.app.models import (AutomaticMessageTemplate, BotButtonSetting, BotMenuDestinationBinding,
    BotMenuLevelLayout, BotMenuProfile, Client, ClientProfile, Company, SavedFile, Ticket, User)
from api.app.services import bot_settings_transfer as transfer
from api.app.services.button_config import build_level_layout_key

DEV_DATE = datetime(2022, 1, 1)
PROD_DATE = datetime(2001, 1, 1)
CONTENT = b"synthetic-file-content"


def uid(value):
    return str(uuid.uuid5(uuid.NAMESPACE_URL, "synthetic-bot-transfer/" + value))


def make_engine():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    return engine


def seed(engine, root, source):
    prefix, count, date = ("dev", 66, DEV_DATE) if source else ("prod", 56, PROD_DATE)
    root.mkdir()
    storage_key = prefix + ".pdf"
    (root / storage_key).write_bytes(CONTENT)
    with Session(engine) as db:
        db.add(Company(id=prefix, name="Synthetic"))
        db.flush()
        db.add(User(id=uid(prefix + "-staff"), company_id=prefix, login="agent.unique", email="staff@example.invalid",
                    full_name="Synthetic staff", role="agent", is_active=True))
        if not source:
            # A coinciding DEV UUID identifies a different PROD employee.
            db.add(User(id=uid("dev-staff"), company_id=prefix, login="other.agent", email="other@example.invalid",
                        full_name="Synthetic other", role="agent", is_active=True))
            db.add(Client(id="unrelated-client", company_id=prefix, display_name="Synthetic client"))
            db.flush()
            db.add(ClientProfile(id="unrelated-profile", client_id="unrelated-client", custom_fields={"private": "unchanged"}))
            db.add(Ticket(id="unrelated-ticket", company_id=prefix, client_id="unrelated-client", subject="Synthetic ticket"))
        db.add(SavedFile(id=uid(prefix + "-file"), storage_key=storage_key, display_filename="guide.pdf",
                         mime_type="application/pdf", file_size=len(CONTENT), created_at=date))
        for index in range(5):
            db.add(BotMenuProfile(id=uid(f"{prefix}-menu-{index}"), menu_type=f"menu-{index}", display_name=f"{prefix}-menu-{index}",
                                 is_fallback=index == 0, is_active=True, created_at=date, updated_at=date))
            db.add(BotMenuDestinationBinding(id=uid(f"{prefix}-binding-{index}"), destination_value=f"destination-{index}",
                                            menu_type=f"menu-{index}", created_at=date, updated_at=date))
        buttons = []
        for index in range(count):
            menu, path, parent, kind = f"menu-{index % 5}", f"Button-{index}", "", "terminal"
            if index < 4:
                menu = "menu-0"
                path, parent, kind = [("Folder", "", "section"), ("Folder/Inner", "Folder", "section"),
                                     ("Folder/Leaf", "Folder", "terminal"), ("Folder/Inner/Leaf", "Folder/Inner", "terminal")][index]
            key = f"{menu}|{'db' if kind == 'section' else 'ticket'}|{path}"
            row = BotButtonSetting(id=uid(f"{prefix}-button-{index}"), stable_button_id=uid(f"{prefix}-stable-{index}"),
                button_key=key, menu_type=menu, workflow_type="submenu" if kind == "section" else "ticket",
                button_path=path, parent_path=parent, button_label=f"Button {index}", button_kind=kind,
                instruction_text="DEV instruction\n raw ", client_text=None if source else "old client text",
                client_text_overridden=False if source else True, label_override=None if source else "old label",
                is_enabled=False if source else True, currently_present=False if source and index == 5 else True,
                visibility_overridden=True, files_json=None, conditional_files_json=None,
                fixed_assignee_enabled=index == 2, fixed_assignee_user_id=uid(prefix + "-staff") if index == 2 else None,
                is_archived=source and index == 5, deleted_at=date if (source and index == 5) or (not source and index == 6) else None,
                deleted_by_user_id=uid(prefix + "-staff") if index == 5 else None, created_at=date, updated_at=date)
            if index == 2:
                row.files_json = ["saved:" + uid(prefix + "-file")]
                row.files_overridden = source
            if index == 3:
                row.conditional_files_json = [{"conditions": [{"field": "custom", "operator": "eq", "value": False}], "files": ["guide.pdf"]}]
                row.conditional_files_overridden = source
            db.add(row)
            buttons.append(row)
        for index, (menu, parent) in enumerate([(f"menu-{i}", "") for i in range(5)] + [("menu-0", "Folder"), ("menu-0", "Folder/Inner")]):
            keys = [row.button_key for row in buttons if row.menu_type == menu and row.parent_path == parent][:3]
            db.add(BotMenuLevelLayout(id=uid(f"{prefix}-layout-{index}"), menu_type=menu, level_key=build_level_layout_key(menu, parent),
                                     layout_json=[{"columns": min(3, len(keys)), "button_keys": keys}], created_at=date, updated_at=date))
        for index in range(23 if source else 12):
            db.add(AutomaticMessageTemplate(id=uid(f"{prefix}-message-{index}"), key=f"template-{index}",
                body=None if source and index % 2 == 0 else f"{prefix} <b>message</b>\n ", is_overridden=source,
                enabled=not source, html_enabled=not source, send_silently=source, created_at=date, updated_at=date))
        db.commit()


@pytest.fixture
def case(tmp_path, monkeypatch):
    dev, prod = make_engine(), make_engine()
    dev_root, prod_root = tmp_path / "dev-files", tmp_path / "prod-files"
    seed(dev, dev_root, True)
    seed(prod, prod_root, False)
    monkeypatch.setattr(settings, "saved_file_storage_dir", str(dev_root))
    with Session(dev) as db:
        data = transfer.export_bot_settings(db)
    monkeypatch.setattr(settings, "saved_file_storage_dir", str(prod_root))
    yield SimpleNamespace(dev=dev, prod=prod, data=data, dev_root=dev_root, prod_root=prod_root)
    dev.dispose()
    prod.dispose()


def snapshot(engine):
    with engine.connect() as connection:
        return {table.name: [dict(row._mapping) for row in connection.execute(select(table).order_by(*table.primary_key.columns))]
                for table in Base.metadata.sorted_tables}


def run(case, **kwargs):
    with Session(case.prod) as db:
        return transfer.import_bot_settings(db, case.data, **kwargs)


def test_export_counts_full_values_and_read_only_manifests(case):
    assert list(case.data["counts"].values()) == [66, 7, 5, 5, 23]
    assert set(case.data) == {"format_version", "scope", "tables", "counts", "references"}
    assert set(case.data["references"]) == {"files", "users"}
    assert len(case.data["references"]["files"]) == 2
    before = snapshot(case.dev)
    with Session(case.dev) as db:
        # Resolve using the source files, never the target storage directory.
        original = settings.saved_file_storage_dir
        settings.saved_file_storage_dir = str(case.dev_root)
        try:
            assert transfer.export_bot_settings(db) == case.data
        finally:
            settings.saved_file_storage_dir = original
    assert snapshot(case.dev) == before


def test_dry_run_has_no_dml_and_plans_all_five_tables(case):
    before = snapshot(case.prod)
    dml = []
    @event.listens_for(case.prod, "before_cursor_execute")
    def statements(conn, cursor, statement, parameters, context, executemany):
        if statement.upper().startswith(("INSERT", "UPDATE", "DELETE")):
            dml.append(statement.split()[0])
    report = run(case)
    assert report["dry_run"] and not report["applied"] and not report["conflicts"]
    assert list(report["created"].values()) == [10, 0, 0, 0, 11]
    assert list(report["updated"].values()) == [56, 7, 5, 5, 12]
    assert not dml
    assert snapshot(case.prod) == before


def test_apply_preserves_prod_ids_and_all_unrelated_tables(case):
    before = snapshot(case.prod)
    files_before = {path.name: path.read_bytes() for path in case.prod_root.iterdir()}
    report = run(case, apply=True)
    assert report["applied"] and not report["conflicts"]
    after = snapshot(case.prod)
    assert [len(after[name]) for name in transfer.TABLES] == [66, 7, 5, 5, 23]
    for name in before:
        if name not in transfer.TABLES:
            assert after[name] == before[name]
    for name, (_, key) in transfer.TABLES.items():
        old = {row[key]: row for row in before[name]}
        for row in after[name]:
            if row[key] in old:
                assert row["id"] == old[row[key]]["id"]
                if name == transfer.BUTTONS:
                    assert row["stable_button_id"] == old[row[key]]["stable_button_id"]
    with Session(case.prod) as db:
        button = db.get(BotButtonSetting, uid("prod-button-2"))
        assert button.fixed_assignee_user_id == uid("prod-staff")
        assert button.fixed_assignee_user_id != uid("dev-staff")
        assert button.files_json == ["saved:" + uid("prod-file")]
        assert button.label_override is None and button.client_text is None
        assert button.is_enabled is False and button.client_text_overridden is False
        assert button.instruction_text == "DEV instruction\n raw "
        assert button.created_at == button.updated_at == DEV_DATE
        rule = db.get(BotButtonSetting, uid("prod-button-3")).conditional_files_json[0]
        assert rule["files"] == ["saved:" + uid("prod-file")]
        assert rule["conditions"][0]["value"] is False
        assert db.get(BotButtonSetting, uid("prod-button-5")).deleted_by_user_id == uid("prod-staff")
        assert db.get(BotButtonSetting, uid("prod-button-6")).deleted_at is None
        message = db.scalar(select(AutomaticMessageTemplate).where(AutomaticMessageTemplate.key == "template-0"))
        assert message.body is None and message.enabled is False and message.is_overridden is True
        new = db.scalar(select(AutomaticMessageTemplate).where(AutomaticMessageTemplate.key == "template-22"))
        assert new.id != uid("dev-message-22")
    assert {path.name: path.read_bytes() for path in case.prod_root.iterdir()} == files_before


def test_stable_button_match_remaps_nested_layout_and_repeated_import_is_idempotent(case):
    source = next(row for row in case.data["tables"][transfer.BUTTONS] if row["id"] == uid("dev-button-3"))
    with Session(case.prod) as db:
        target = db.get(BotButtonSetting, uid("prod-button-3"))
        target.button_key = "old-prod-button-key"
        target.stable_button_id = source["stable_button_id"]
        db.commit()
    assert run(case, apply=True)["applied"]
    with Session(case.prod) as db:
        layout = db.get(BotMenuLevelLayout, uid("prod-layout-6"))
        assert layout.layout_json[0]["button_keys"] == ["old-prod-button-key"]
    before = snapshot(case.prod)
    report = run(case, apply=True)
    assert report["applied"] and not report["conflicts"]
    assert sum(report["created"].values()) == sum(report["updated"].values()) == 0
    assert list(report["unchanged"].values()) == [66, 7, 5, 5, 23]
    assert snapshot(case.prod) == before


def test_prod_only_records_are_reported_and_preserved(case):
    with Session(case.prod) as db:
        db.add(BotMenuProfile(id=uid("only-menu"), menu_type="prod-only-menu", display_name="keep", is_fallback=False))
        db.add(BotButtonSetting(id=uid("only-button"), button_key="prod-only-button", menu_type="prod-only-menu",
                               workflow_type="ticket", button_path="Only", button_label="keep", parent_path=""))
        db.add(BotMenuLevelLayout(id=uid("only-layout"), menu_type="prod-only-menu",
            level_key=build_level_layout_key("prod-only-menu", ""), layout_json=[{"columns": 1, "button_keys": ["prod-only-button"]}]))
        db.add(BotMenuDestinationBinding(id=uid("only-binding"), menu_type="prod-only-menu", destination_value="only-destination"))
        db.add(AutomaticMessageTemplate(id=uid("only-message"), key="prod-only-message", body="keep", enabled=False))
        db.commit()
    before = snapshot(case.prod)
    report = run(case, apply=True)
    assert report["applied"]
    assert list(report["prod_only"].values()) == [["prod-only-button"], [build_level_layout_key("prod-only-menu", "")],
                                               ["prod-only-menu"], ["only-destination"], ["prod-only-message"]]
    after = snapshot(case.prod)
    for name, (_, key) in transfer.TABLES.items():
        only_key = report["prod_only"][name][0]
        assert next(row for row in after[name] if row[key] == only_key) == next(row for row in before[name] if row[key] == only_key)


@pytest.mark.parametrize("problem", ["missing_file", "missing_physical_file", "file_content", "ambiguous_file", "missing_user", "ambiguous_user", "conflicting_user", "inactive_user", "button_identity"])
def test_reference_conflicts_cancel_apply_and_dry_run(case, problem):
    if problem == "missing_file":
        with Session(case.prod) as db:
            db.delete(db.get(SavedFile, uid("prod-file"))); db.commit()
    elif problem == "missing_physical_file":
        (case.prod_root / "prod.pdf").unlink()
    elif problem == "file_content":
        (case.prod_root / "prod.pdf").write_bytes(b"X" * len(CONTENT))
    elif problem == "ambiguous_file":
        (case.prod_root / "duplicate.pdf").write_bytes(CONTENT)
        with Session(case.prod) as db:
            db.add(SavedFile(id=uid("duplicate-file"), display_filename="other.pdf", storage_key="duplicate.pdf",
                             mime_type="application/pdf", file_size=len(CONTENT))); db.commit()
    else:
        with Session(case.prod) as db:
            user = db.get(User, uid("prod-staff"))
            other = db.get(User, uid("dev-staff"))
            if problem == "missing_user":
                user.login, user.email = "different-login", "different@example.invalid"
            elif problem == "ambiguous_user":
                other.email = user.email
            elif problem == "conflicting_user":
                other.email, user.email = user.email, "different@example.invalid"
            elif problem == "inactive_user":
                user.is_active = False
            else:
                source = next(row for row in case.data["tables"][transfer.BUTTONS] if row["id"] == uid("dev-button-2"))
                db.get(BotButtonSetting, uid("prod-button-3")).stable_button_id = source["stable_button_id"]
            db.commit()
    before = snapshot(case.prod)
    for apply in (False, True):
        report = run(case, apply=apply)
        assert not report["applied"] and report["conflicts"]
        assert snapshot(case.prod) == before


@pytest.mark.parametrize("problem", ["count", "duplicate_key", "duplicate_stable", "layout", "parent", "binding", "files", "version"])
def test_invalid_export_fails_without_writes(case, problem):
    tables = case.data["tables"]
    if problem == "count":
        case.data["counts"][transfer.BUTTONS] += 1
    elif problem == "duplicate_key":
        tables[transfer.BUTTONS][1]["button_key"] = tables[transfer.BUTTONS][0]["button_key"]
    elif problem == "duplicate_stable":
        tables[transfer.BUTTONS][1]["stable_button_id"] = tables[transfer.BUTTONS][0]["stable_button_id"]
    elif problem == "layout":
        tables[transfer.LAYOUTS][0]["layout_json"][0]["button_keys"] = ["missing-button"]
    elif problem == "parent":
        tables[transfer.BUTTONS][0]["parent_path"] = "missing-parent"
    elif problem == "binding":
        tables[transfer.BINDINGS][0]["menu_type"] = "missing-menu"
    elif problem == "files":
        case.data["references"]["files"] = {}
    else:
        case.data["format_version"] = 2
    before = snapshot(case.prod)
    with pytest.raises(transfer.BotSettingsTransferError):
        run(case, apply=True)
    assert snapshot(case.prod) == before


def test_flush_failure_rolls_back_all_settings_tables(case):
    before = snapshot(case.prod)
    inserted = []
    @event.listens_for(case.prod, "before_cursor_execute")
    def fail(conn, cursor, statement, parameters, context, executemany):
        if statement.upper().startswith("INSERT INTO AUTOMATIC_MESSAGE_TEMPLATES"):
            inserted.append(True)
        if statement.upper().startswith("INSERT INTO BOT_BUTTON_SETTINGS"):
            raise RuntimeError("synthetic-private-diagnostic")
    with pytest.raises(transfer.BotSettingsTransferError) as error:
        run(case, apply=True)
    assert str(error.value) == "import_failed_transaction_rolled_back"
    assert inserted
    assert snapshot(case.prod) == before


def test_export_missing_source_file_is_safe_and_does_not_write(case, monkeypatch, tmp_path, capsys):
    monkeypatch.setattr(settings, "saved_file_storage_dir", str(case.dev_root))
    monkeypatch.setattr(transfer, "SessionLocal", lambda: Session(case.dev))
    (case.dev_root / "dev.pdf").unlink()
    output = tmp_path / "not-created.json"
    assert transfer.cli(["export", "--output", str(output)]) == 1
    assert not output.exists()
    assert "staff@example.invalid" not in capsys.readouterr().out


def test_cli_export_import_defaults_to_dry_run_and_apply_is_explicit(case, monkeypatch, tmp_path, capsys):
    output = tmp_path / "synthetic-settings.json"
    monkeypatch.setattr(settings, "saved_file_storage_dir", str(case.dev_root))
    monkeypatch.setattr(transfer, "SessionLocal", lambda: Session(case.dev))
    assert transfer.cli(["export", "--output", str(output)]) == 0
    assert json.loads(capsys.readouterr().out)["exported"] == case.data["counts"]
    monkeypatch.setattr(settings, "saved_file_storage_dir", str(case.prod_root))
    monkeypatch.setattr(transfer, "SessionLocal", lambda: Session(case.prod))
    before = snapshot(case.prod)
    assert transfer.cli(["import", "--input", str(output)]) == 0
    captured = capsys.readouterr()
    assert json.loads(captured.out)["dry_run"]
    assert "staff@example.invalid" not in captured.out and "DEV instruction" not in captured.out
    assert snapshot(case.prod) == before
    assert transfer.cli(["import", "--input", str(output), "--apply"]) == 0
    assert json.loads(capsys.readouterr().out)["applied"]
    assert not list(tmp_path.glob(".bot-settings-*"))


def test_coinciding_saved_file_uuid_cannot_override_content_identity(case):
    (case.prod_root / "prod.pdf").write_bytes(b"X" * len(CONTENT))
    (case.prod_root / "correct.pdf").write_bytes(CONTENT)
    with Session(case.prod) as db:
        db.get(SavedFile, uid("prod-file")).id = uid("dev-file")
        db.add(SavedFile(id=uid("correct-file"), storage_key="correct.pdf", display_filename="correct.pdf",
                         file_size=len(CONTENT), mime_type="application/pdf"))
        db.commit()
    before = snapshot(case.prod)
    report = run(case, apply=True)
    assert not report["applied"] and report["conflicts"]
    assert snapshot(case.prod) == before


def test_source_employee_without_unique_login_or_email_cancels_export(case, monkeypatch):
    monkeypatch.setattr(settings, "saved_file_storage_dir", str(case.dev_root))
    with Session(case.dev) as db:
        user = db.get(User, uid("dev-staff"))
        user.login = None
        user.email = None
        db.commit()
        with pytest.raises(transfer.BotSettingsTransferError, match="source_employee_has_no_unique_identity"):
            transfer.export_bot_settings(db)


@pytest.mark.parametrize("offset", ["+00:00", "+03:00"])
def test_utc_equivalent_timestamps_are_idempotent(case, offset):
    for rows in case.data["tables"].values():
        for row in rows:
            for key in ("created_at", "updated_at"):
                row[key] += offset
    assert run(case, apply=True)["applied"]
    before = snapshot(case.prod)
    report = run(case, apply=True)
    assert report["applied"] and sum(report["updated"].values()) == 0
    assert snapshot(case.prod) == before


def test_export_never_overwrites_an_existing_file(case, monkeypatch, tmp_path, capsys):
    monkeypatch.setattr(settings, "saved_file_storage_dir", str(case.dev_root))
    monkeypatch.setattr(transfer, "SessionLocal", lambda: Session(case.dev))
    output = tmp_path / "existing.json"
    output.write_text("keep")
    assert transfer.cli(["export", "--output", str(output)]) == 1
    assert output.read_text() == "keep"
    assert not list(tmp_path.glob(".bot-settings-*"))
    assert "staff@example.invalid" not in capsys.readouterr().out



def test_import_preserves_existing_db_legacy_key_after_type_change(case):
    source = next(row for row in case.data["tables"][transfer.BUTTONS] if row["id"] == uid("dev-button-2"))
    with Session(case.prod) as db:
        target = db.get(BotButtonSetting, uid("prod-button-2"))
        target.stable_button_id = source["stable_button_id"]
        target.button_key = "menu-0|task|Folder/Leaf"
        target.legacy_button_key = "menu-0|db|Folder/Leaf"
        layout = db.get(BotMenuLevelLayout, uid("prod-layout-5"))
        layout.layout_json = [{"columns": 1, "button_keys": [target.button_key]}]
        db.commit()
    report = run(case, apply=True)
    assert report["applied"] and not report["conflicts"], report["conflicts"]
    with Session(case.prod) as db:
        target = db.get(BotButtonSetting, uid("prod-button-2"))
        assert target.legacy_button_key == "menu-0|db|Folder/Leaf"
        assert target.button_key == "menu-0|task|Folder/Leaf"
        assert db.get(BotMenuLevelLayout, uid("prod-layout-5")).layout_json[0]["button_keys"] == ["menu-0|db|Folder/Inner", target.button_key]
