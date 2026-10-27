from datetime import datetime
import uuid

from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker

from api.app.db import Base
from api.app.models import BotButtonSetting, SavedFile
from api.app.routers import telegram as telegram_router
from api.app.services import bot_button_saved_file_migration as migration
from api.app.services import saved_files


def _session():
    engine = create_engine("sqlite:///:memory:", future=True)
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine, autoflush=False, autocommit=False)
    return Session()


def _seed_row(session, **overrides):
    now = datetime.utcnow()
    payload = {
        "id": str(uuid.uuid4()),
        "stable_button_id": str(uuid.uuid4()),
        "button_key": f"menu-a|ticket|{uuid.uuid4()}",
        "legacy_button_key": None,
        "menu_type": "menu-a",
        "workflow_type": "ticket",
        "button_path": "A",
        "button_label": "A",
        "currently_present": True,
        "button_kind": "terminal",
        "parent_path": "",
        "is_archived": False,
        "created_at": now,
        "updated_at": now,
    }
    payload.update(overrides)
    row = BotButtonSetting(**payload)
    session.add(row)
    session.commit()
    return row


def _write_legacy_pdfs(root, *, skip: set[str] | None = None):
    files_dir = root / "files"
    files_dir.mkdir(parents=True)
    skip = skip or set()
    for filename in migration.LEGACY_BOT_BUTTON_PDF_FILENAMES:
        if filename not in skip:
            (files_dir / filename).write_bytes(f"bytes:{filename}".encode("utf-8"))


def _saved_ref(filename: str) -> str:
    return f"saved:{migration.legacy_bot_button_saved_file_id(filename)}"


def test_migration_creates_four_saved_files_and_copies_bytes(monkeypatch, tmp_path):
    button_root = tmp_path / "button-config"
    storage_root = tmp_path / "saved"
    _write_legacy_pdfs(button_root)
    monkeypatch.setattr(migration, "BUTTON_CONFIG_DIR", button_root)
    monkeypatch.setattr(saved_files.settings, "saved_file_storage_dir", str(storage_root))
    monkeypatch.setattr(migration.settings, "saved_file_storage_dir", str(storage_root))
    session = _session()

    migrated = migration.migrate_legacy_bot_button_pdf_saved_files(session)
    session.commit()

    rows = session.execute(select(SavedFile).order_by(SavedFile.display_filename)).scalars().all()
    assert set(migrated) == set(migration.LEGACY_BOT_BUTTON_PDF_FILENAMES)
    assert len(rows) == 4
    for filename in migration.LEGACY_BOT_BUTTON_PDF_FILENAMES:
        saved = session.get(SavedFile, migration.legacy_bot_button_saved_file_id(filename))
        assert saved is not None
        assert saved.display_filename == filename
        assert saved.mime_type == "application/pdf"
        assert saved.storage_key != filename
        path = saved_files.resolve_saved_file_path(saved)
        assert path.read_bytes() == (button_root / "files" / filename).read_bytes()
        assert saved.file_size == path.stat().st_size


def test_migration_backfills_direct_and_conditional_refs_and_flags(monkeypatch, tmp_path):
    button_root = tmp_path / "button-config"
    storage_root = tmp_path / "saved"
    _write_legacy_pdfs(button_root)
    monkeypatch.setattr(migration, "BUTTON_CONFIG_DIR", button_root)
    monkeypatch.setattr(saved_files.settings, "saved_file_storage_dir", str(storage_root))
    monkeypatch.setattr(migration.settings, "saved_file_storage_dir", str(storage_root))
    session = _session()
    first, second, third, fourth = migration.LEGACY_BOT_BUTTON_PDF_FILENAMES
    row = _seed_row(
        session,
        files_json=[first, "other.pdf", second],
        files_overridden=False,
        conditional_files_json=[
            {"conditions": [{"field": "x", "operator": "eq", "value": "1"}], "files": [third, "other-rule.pdf"]},
            {"conditions": [], "files": [fourth]},
        ],
        conditional_files_overridden=False,
    )

    migration.migrate_legacy_bot_button_pdf_saved_files(session)
    session.commit()
    session.refresh(row)

    assert row.files_json == [_saved_ref(first), "other.pdf", _saved_ref(second)]
    assert row.conditional_files_json == [
        {"conditions": [{"field": "x", "operator": "eq", "value": "1"}], "files": [_saved_ref(third), "other-rule.pdf"]},
        {"conditions": [], "files": [_saved_ref(fourth)]},
    ]
    assert row.files_overridden is True
    assert row.conditional_files_overridden is True


def test_migration_is_idempotent(monkeypatch, tmp_path):
    button_root = tmp_path / "button-config"
    storage_root = tmp_path / "saved"
    _write_legacy_pdfs(button_root)
    monkeypatch.setattr(migration, "BUTTON_CONFIG_DIR", button_root)
    monkeypatch.setattr(saved_files.settings, "saved_file_storage_dir", str(storage_root))
    monkeypatch.setattr(migration.settings, "saved_file_storage_dir", str(storage_root))
    session = _session()
    first = migration.LEGACY_BOT_BUTTON_PDF_FILENAMES[0]
    row = _seed_row(session, files_json=[first])

    migration.migrate_legacy_bot_button_pdf_saved_files(session)
    session.commit()
    first_ids = [saved.id for saved in session.execute(select(SavedFile)).scalars().all()]
    first_storage_keys = [saved.storage_key for saved in session.execute(select(SavedFile)).scalars().all()]
    first_files = sorted(path.name for path in storage_root.iterdir())
    session.refresh(row)
    first_refs = list(row.files_json)

    migration.migrate_legacy_bot_button_pdf_saved_files(session)
    session.commit()
    session.refresh(row)

    assert [saved.id for saved in session.execute(select(SavedFile)).scalars().all()] == first_ids
    assert [saved.storage_key for saved in session.execute(select(SavedFile)).scalars().all()] == first_storage_keys
    assert sorted(path.name for path in storage_root.iterdir()) == first_files
    assert row.files_json == first_refs


def test_missing_source_is_not_created_or_backfilled(monkeypatch, tmp_path):
    button_root = tmp_path / "button-config"
    storage_root = tmp_path / "saved"
    missing = migration.LEGACY_BOT_BUTTON_PDF_FILENAMES[0]
    present = migration.LEGACY_BOT_BUTTON_PDF_FILENAMES[1]
    _write_legacy_pdfs(button_root, skip={missing})
    monkeypatch.setattr(migration, "BUTTON_CONFIG_DIR", button_root)
    monkeypatch.setattr(saved_files.settings, "saved_file_storage_dir", str(storage_root))
    monkeypatch.setattr(migration.settings, "saved_file_storage_dir", str(storage_root))
    session = _session()
    row = _seed_row(session, files_json=[missing, present])

    migration.migrate_legacy_bot_button_pdf_saved_files(session)
    session.commit()
    session.refresh(row)

    assert session.get(SavedFile, migration.legacy_bot_button_saved_file_id(missing)) is None
    assert session.get(SavedFile, migration.legacy_bot_button_saved_file_id(present)) is not None
    assert row.files_json == [missing, _saved_ref(present)]


def test_runtime_resolves_migrated_saved_ref(monkeypatch, tmp_path):
    button_root = tmp_path / "button-config"
    storage_root = tmp_path / "saved"
    _write_legacy_pdfs(button_root)
    monkeypatch.setattr(migration, "BUTTON_CONFIG_DIR", button_root)
    monkeypatch.setattr(saved_files.settings, "saved_file_storage_dir", str(storage_root))
    monkeypatch.setattr(migration.settings, "saved_file_storage_dir", str(storage_root))
    session = _session()
    filename = migration.LEGACY_BOT_BUTTON_PDF_FILENAMES[0]

    migration.migrate_legacy_bot_button_pdf_saved_files(session)
    session.commit()

    path, telegram_filename = telegram_router._resolve_button_file_path(session, _saved_ref(filename))
    assert path.read_bytes() == (button_root / "files" / filename).read_bytes()
    assert telegram_filename == filename

    saved = session.get(SavedFile, migration.legacy_bot_button_saved_file_id(filename))
    saved_files.replace_saved_file_content(
        session, saved, content=b"replacement", display_filename="updated.txt", mime_type="text/plain",
    )
    migration.migrate_legacy_bot_button_pdf_saved_files(session)
    session.commit()

    path, telegram_filename = telegram_router._resolve_button_file_path(session, _saved_ref(filename))
    assert path.read_bytes() == b"replacement"
    assert telegram_filename == "updated.txt"
    assert (button_root / "files" / filename).read_bytes() != b"replacement"


def test_existing_saved_file_missing_physical_copy_is_recovered(monkeypatch, tmp_path):
    button_root = tmp_path / "button-config"
    storage_root = tmp_path / "saved"
    _write_legacy_pdfs(button_root)
    monkeypatch.setattr(migration, "BUTTON_CONFIG_DIR", button_root)
    monkeypatch.setattr(saved_files.settings, "saved_file_storage_dir", str(storage_root))
    monkeypatch.setattr(migration.settings, "saved_file_storage_dir", str(storage_root))
    session = _session()
    filename = migration.LEGACY_BOT_BUTTON_PDF_FILENAMES[0]
    saved = SavedFile(
        id=migration.legacy_bot_button_saved_file_id(filename),
        display_filename=filename,
        storage_key="existing.pdf",
        mime_type="application/pdf",
        file_size=0,
        created_at=datetime.utcnow(),
    )
    session.add(saved)
    session.commit()

    migration.migrate_legacy_bot_button_pdf_saved_files(session)
    session.commit()

    assert saved.storage_key == "existing.pdf"
    assert (storage_root / "existing.pdf").read_bytes() == (button_root / "files" / filename).read_bytes()
