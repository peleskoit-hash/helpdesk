from pathlib import Path

from sqlalchemy import create_engine, select, text
from sqlalchemy.orm import sessionmaker

from api.app.db import Base
from api.app.models import SavedFile
from api.app.services import saved_files, schema_evolution


def _session():
    engine = create_engine("sqlite:///:memory:", future=True)
    Base.metadata.create_all(engine)
    return sessionmaker(bind=engine, future=True)()


def test_schema_evolution_creates_saved_files_idempotently(monkeypatch):
    engine = create_engine("sqlite:///:memory:", future=True)
    monkeypatch.setattr(schema_evolution, "engine", engine)

    schema_evolution.ensure_saved_files_table()
    schema_evolution.ensure_saved_files_table()

    with engine.connect() as conn:
        columns = {
            row["name"]
            for row in conn.execute(text("PRAGMA table_info(saved_files)")).mappings().all()
        }

    assert columns == {"id", "display_filename", "storage_key", "mime_type", "file_size", "created_at"}


def test_create_saved_file_stores_db_row_and_physical_file_inside_root(monkeypatch, tmp_path):
    monkeypatch.setattr(saved_files.settings, "saved_file_storage_dir", str(tmp_path))
    session = _session()

    saved = saved_files.create_saved_file(
        session,
        content=b"hello",
        display_filename="report.pdf",
        mime_type="application/pdf",
    )
    session.commit()

    path = saved_files.resolve_saved_file_path(saved)
    assert session.get(SavedFile, saved.id) is not None
    assert path.read_bytes() == b"hello"
    assert saved.storage_key != "report.pdf"
    assert Path(saved.storage_key).name == saved.storage_key
    assert path.resolve().is_relative_to(tmp_path.resolve())


def test_rename_changes_only_display_filename(monkeypatch, tmp_path):
    monkeypatch.setattr(saved_files.settings, "saved_file_storage_dir", str(tmp_path))
    session = _session()
    saved = saved_files.create_saved_file(session, content=b"data", display_filename="old.pdf")
    session.commit()
    original_id = saved.id
    original_storage_key = saved.storage_key
    original_path = saved_files.resolve_saved_file_path(saved)

    saved_files.rename_saved_file(session, saved, "new.pdf")
    session.commit()

    assert saved.id == original_id
    assert saved.display_filename == "new.pdf"
    assert saved.storage_key == original_storage_key
    assert saved_files.resolve_saved_file_path(saved) == original_path


def test_delete_removes_physical_file_and_db_row(monkeypatch, tmp_path):
    monkeypatch.setattr(saved_files.settings, "saved_file_storage_dir", str(tmp_path))
    session = _session()
    saved = saved_files.create_saved_file(session, content=b"data", display_filename="file.pdf")
    session.commit()
    saved_id = saved.id
    path = saved_files.resolve_saved_file_path(saved)

    saved_files.delete_saved_file(session, saved)
    session.commit()

    assert not path.exists()
    assert session.get(SavedFile, saved_id) is None


def test_missing_physical_file_returns_controlled_error(monkeypatch, tmp_path):
    monkeypatch.setattr(saved_files.settings, "saved_file_storage_dir", str(tmp_path))
    session = _session()
    saved = saved_files.create_saved_file(session, content=b"data", display_filename="file.pdf")
    session.commit()
    saved_files.resolve_saved_file_path(saved).unlink()

    try:
        saved_files.resolve_saved_file_path(saved)
    except saved_files.SavedFileStorageError as exc:
        assert str(exc) == "saved_file_missing"
    else:
        raise AssertionError("missing file must not resolve")


def test_malicious_filename_cannot_escape_storage_root(monkeypatch, tmp_path):
    monkeypatch.setattr(saved_files.settings, "saved_file_storage_dir", str(tmp_path))
    session = _session()

    saved = saved_files.create_saved_file(session, content=b"pdf", display_filename="../../outside.pdf")
    session.commit()

    path = saved_files.resolve_saved_file_path(saved)
    assert path.resolve().is_relative_to(tmp_path.resolve())
    assert not (tmp_path.parent / "outside.pdf").exists()


def test_duplicate_display_filenames_are_allowed(monkeypatch, tmp_path):
    monkeypatch.setattr(saved_files.settings, "saved_file_storage_dir", str(tmp_path))
    session = _session()

    first = saved_files.create_saved_file(session, content=b"one", display_filename="same.pdf")
    second = saved_files.create_saved_file(session, content=b"two", display_filename="same.pdf")
    session.commit()

    rows = session.execute(select(SavedFile).where(SavedFile.display_filename == "same.pdf")).scalars().all()
    assert len(rows) == 2
    assert first.storage_key != second.storage_key
