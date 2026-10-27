from __future__ import annotations

from datetime import datetime
import uuid
from pathlib import Path

import pytest

from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from api.app.auth import CurrentUser
from api.app.db import Base, get_db
from api.app.models import BotButtonSetting, QuickReply, SavedFile
from api.app.routers import management_saved_files
from api.app.services import saved_files


def _session_factory():
    engine = create_engine(
        "sqlite://",
        future=True,
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    return sessionmaker(bind=engine, autoflush=False, autocommit=False)


def _current_user(role: str = "admin") -> CurrentUser:
    return CurrentUser(id=f"{role}-1", role=role, real_user_id=f"{role}-1", real_role=role, is_real_admin=role == "admin")


def _client(session, *, role: str = "admin") -> TestClient:
    app = FastAPI()
    app.include_router(management_saved_files.router)
    app.dependency_overrides[get_db] = lambda: session
    app.dependency_overrides[management_saved_files.get_current_user] = lambda: _current_user(role)
    return TestClient(app)


def _create_saved(session, *, content: bytes = b"data", display_filename: str = "file.pdf") -> SavedFile:
    saved = saved_files.create_saved_file(
        session,
        content=content,
        display_filename=display_filename,
        mime_type="application/pdf",
    )
    session.commit()
    session.refresh(saved)
    return saved


def _seed_button_row(session, **overrides) -> BotButtonSetting:
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


def _seed_quick_reply(session, saved_file_ids: list[str]) -> QuickReply:
    now = datetime.utcnow()
    row = QuickReply(
        id=str(uuid.uuid4()),
        short_title="Reply",
        body="Body",
        author_user_id="agent-1",
        saved_file_ids_json=saved_file_ids,
        created_at=now,
        updated_at=now,
    )
    session.add(row)
    session.commit()
    return row


def test_admin_can_list_saved_files(monkeypatch, tmp_path):
    monkeypatch.setattr(saved_files.settings, "saved_file_storage_dir", str(tmp_path))
    Session = _session_factory()
    session = Session()
    saved = _create_saved(session, display_filename="report.pdf")

    response = _client(session).get("/api/v1/management/saved-files")

    assert response.status_code == 200
    assert response.json()["items"] == [
        {
            "id": saved.id,
            "display_filename": "report.pdf",
            "file_size": 4,
            "mime_type": "application/pdf",
            "created_at": saved.created_at.isoformat(),
            "used": False,
            "usage_sources": [],
        }
    ]


def test_non_admin_cannot_use_management_saved_files_api(monkeypatch, tmp_path):
    monkeypatch.setattr(saved_files.settings, "saved_file_storage_dir", str(tmp_path))
    Session = _session_factory()
    session = Session()

    response = _client(session, role="agent").get("/api/v1/management/saved-files")

    assert response.status_code == 403
    assert response.json()["detail"]["error"]["code"] == "admin_only"


def test_upload_creates_saved_file_and_physical_file(monkeypatch, tmp_path):
    monkeypatch.setattr(saved_files.settings, "saved_file_storage_dir", str(tmp_path))
    Session = _session_factory()
    session = Session()

    response = _client(session).post(
        "/api/v1/management/saved-files",
        files={"file": ("../../unsafe.pdf", b"upload-bytes", "application/pdf")},
    )

    assert response.status_code == 200
    payload = response.json()
    saved = session.get(SavedFile, payload["id"])
    assert saved is not None
    assert saved.display_filename == ".._.._unsafe.pdf"
    assert saved.storage_key != saved.display_filename
    assert saved.mime_type == "application/pdf"
    assert saved_files.resolve_saved_file_path(saved).read_bytes() == b"upload-bytes"


def test_download_returns_bytes_and_display_filename(monkeypatch, tmp_path):
    monkeypatch.setattr(saved_files.settings, "saved_file_storage_dir", str(tmp_path))
    Session = _session_factory()
    session = Session()
    saved = _create_saved(session, content=b"download-bytes", display_filename="Документ.pdf")

    response = _client(session).get(f"/api/v1/management/saved-files/{saved.id}/download")

    assert response.status_code == 200
    assert response.content == b"download-bytes"
    assert "filename*=UTF-8''%D0%94%D0%BE%D0%BA%D1%83%D0%BC%D0%B5%D0%BD%D1%82.pdf" in response.headers["content-disposition"]


def test_rename_changes_only_display_filename(monkeypatch, tmp_path):
    monkeypatch.setattr(saved_files.settings, "saved_file_storage_dir", str(tmp_path))
    Session = _session_factory()
    session = Session()
    saved = _create_saved(session, display_filename="old.pdf")
    original_id = saved.id
    original_storage_key = saved.storage_key
    original_path = saved_files.resolve_saved_file_path(saved)

    response = _client(session).patch(
        f"/api/v1/management/saved-files/{saved.id}",
        json={"display_filename": "new.pdf"},
    )

    session.refresh(saved)
    assert response.status_code == 200
    assert saved.id == original_id
    assert saved.display_filename == "new.pdf"
    assert saved.storage_key == original_storage_key
    assert saved_files.resolve_saved_file_path(saved) == original_path


def test_unused_file_delete_removes_db_row_and_storage(monkeypatch, tmp_path):
    monkeypatch.setattr(saved_files.settings, "saved_file_storage_dir", str(tmp_path))
    Session = _session_factory()
    session = Session()
    saved = _create_saved(session)
    saved_id = saved.id
    path = saved_files.resolve_saved_file_path(saved)

    response = _client(session).delete(f"/api/v1/management/saved-files/{saved_id}")

    assert response.status_code == 200
    assert response.json() == {"deleted": True}
    assert session.get(SavedFile, saved_id) is None
    assert not path.exists()


def test_direct_bot_button_usage_marks_file_used(monkeypatch, tmp_path):
    monkeypatch.setattr(saved_files.settings, "saved_file_storage_dir", str(tmp_path))
    Session = _session_factory()
    session = Session()
    saved = _create_saved(session)
    _seed_button_row(session, files_json=[f"saved:{saved.id}"])

    item = _client(session).get("/api/v1/management/saved-files").json()["items"][0]

    assert item["used"] is True
    assert item["usage_sources"] == ["bot_buttons"]


def test_conditional_bot_button_usage_marks_file_used(monkeypatch, tmp_path):
    monkeypatch.setattr(saved_files.settings, "saved_file_storage_dir", str(tmp_path))
    Session = _session_factory()
    session = Session()
    saved = _create_saved(session)
    _seed_button_row(
        session,
        conditional_files_json=[{"conditions": [], "files": [f"saved:{saved.id}"]}],
    )

    item = _client(session).get("/api/v1/management/saved-files").json()["items"][0]

    assert item["used"] is True
    assert item["usage_sources"] == ["bot_buttons"]


def test_multiple_bot_button_refs_do_not_duplicate_usage_source(monkeypatch, tmp_path):
    monkeypatch.setattr(saved_files.settings, "saved_file_storage_dir", str(tmp_path))
    Session = _session_factory()
    session = Session()
    saved = _create_saved(session)
    ref = f"saved:{saved.id}"
    _seed_button_row(
        session,
        files_json=[ref, ref],
        conditional_files_json=[{"conditions": [], "files": [ref]}],
    )

    item = _client(session).get("/api/v1/management/saved-files").json()["items"][0]

    assert item["usage_sources"] == ["bot_buttons"]


def test_quick_reply_usage_marks_file_used(monkeypatch, tmp_path):
    monkeypatch.setattr(saved_files.settings, "saved_file_storage_dir", str(tmp_path))
    Session = _session_factory()
    session = Session()
    saved = _create_saved(session)
    _seed_quick_reply(session, [saved.id])

    item = _client(session).get("/api/v1/management/saved-files").json()["items"][0]

    assert item["used"] is True
    assert item["usage_sources"] == ["quick_replies"]


def test_bot_button_and_quick_reply_usage_sources_are_both_reported(monkeypatch, tmp_path):
    monkeypatch.setattr(saved_files.settings, "saved_file_storage_dir", str(tmp_path))
    Session = _session_factory()
    session = Session()
    saved = _create_saved(session)
    _seed_button_row(session, files_json=[f"saved:{saved.id}"])
    _seed_quick_reply(session, [saved.id])

    item = _client(session).get("/api/v1/management/saved-files").json()["items"][0]

    assert item["used"] is True
    assert item["usage_sources"] == ["bot_buttons", "quick_replies"]


def test_quick_reply_used_file_delete_is_blocked(monkeypatch, tmp_path):
    monkeypatch.setattr(saved_files.settings, "saved_file_storage_dir", str(tmp_path))
    Session = _session_factory()
    session = Session()
    saved = _create_saved(session)
    saved_id = saved.id
    path = saved_files.resolve_saved_file_path(saved)
    _seed_quick_reply(session, [saved.id])

    response = _client(session).delete(f"/api/v1/management/saved-files/{saved.id}")

    assert response.status_code == 409
    assert response.json()["detail"]["error"]["details"] == {"used": True, "usage_sources": ["quick_replies"]}
    assert session.get(SavedFile, saved_id) is not None
    assert path.exists()


def test_used_file_delete_is_blocked_and_preserves_row_and_file(monkeypatch, tmp_path):
    monkeypatch.setattr(saved_files.settings, "saved_file_storage_dir", str(tmp_path))
    Session = _session_factory()
    session = Session()
    saved = _create_saved(session)
    saved_id = saved.id
    path = saved_files.resolve_saved_file_path(saved)
    _seed_button_row(session, files_json=[f"saved:{saved.id}"])

    response = _client(session).delete(f"/api/v1/management/saved-files/{saved.id}")

    assert response.status_code == 409
    assert response.json()["detail"]["error"]["code"] == "saved_file_in_use"
    assert response.json()["detail"]["error"]["details"] == {"used": True, "usage_sources": ["bot_buttons"]}
    assert session.get(SavedFile, saved_id) is not None
    assert path.exists()


def test_unrelated_legacy_filename_is_not_saved_file_usage(monkeypatch, tmp_path):
    monkeypatch.setattr(saved_files.settings, "saved_file_storage_dir", str(tmp_path))
    Session = _session_factory()
    session = Session()
    _create_saved(session)
    _seed_button_row(
        session,
        files_json=["legacy.pdf"],
        conditional_files_json=[{"conditions": [], "files": ["legacy.pdf"]}],
    )

    item = _client(session).get("/api/v1/management/saved-files").json()["items"][0]

    assert item["used"] is False
    assert item["usage_sources"] == []


def test_replace_used_file_keeps_identity_bindings_and_updates_consumers(monkeypatch, tmp_path):
    from api.app.routers import telegram, quick_replies

    monkeypatch.setattr(saved_files.settings, "saved_file_storage_dir", str(tmp_path))
    session = _session_factory()()
    saved = _create_saved(session)
    saved_id, created_at = saved.id, saved.created_at
    old_path = saved_files.resolve_saved_file_path(saved)
    ref = f"saved:{saved_id}"
    rules = [{"conditions": [], "files": [ref]}]
    button = _seed_button_row(session, files_json=[ref], conditional_files_json=rules)
    reply = _seed_quick_reply(session, [saved_id])
    commit = session.commit

    def checked_commit():
        assert old_path.read_bytes() == b"data"
        assert saved_files.resolve_saved_file_path(saved).read_bytes() == b"new content"
        commit()

    monkeypatch.setattr(session, "commit", checked_commit)
    client = _client(session)
    response = client.put(
        f"/api/v1/management/saved-files/{saved_id}/content",
        files={"file": ("new.txt", b"new content", "text/plain")},
    )

    assert response.status_code == 200
    assert response.json() == {
        "id": saved_id, "created_at": created_at.isoformat(),
        "display_filename": "new.txt", "file_size": 11, "mime_type": "text/plain",
        "used": True, "usage_sources": ["bot_buttons", "quick_replies"],
    }
    session.expire_all()
    assert saved.id == saved_id and saved.created_at == created_at
    assert button.files_json == [ref] and button.conditional_files_json == rules
    assert reply.saved_file_ids_json == [saved_id]
    assert not old_path.exists()
    assert len(list(tmp_path.iterdir())) == 1
    download = client.get(f"/api/v1/management/saved-files/{saved_id}/download")
    assert download.content == b"new content"
    assert download.headers["content-type"].startswith("text/plain")
    path, filename = telegram._resolve_button_file_path(session, ref)
    assert path.read_bytes() == b"new content" and filename == "new.txt"
    reply_download = quick_replies.download_quick_reply_file(
        reply.id, saved_id, db=session, user=_current_user(),
    )
    assert Path(reply_download.path).read_bytes() == b"new content"


@pytest.mark.parametrize("failure", ["write", "verify", "fsync", "commit"])
def test_replace_failure_preserves_original_and_cleans_new_file(monkeypatch, tmp_path, failure):
    monkeypatch.setattr(saved_files.settings, "saved_file_storage_dir", str(tmp_path))
    session = _session_factory()()
    saved = _create_saved(session)
    saved_id, storage_key, created_at = saved.id, saved.storage_key, saved.created_at
    old_path = saved_files.resolve_saved_file_path(saved)

    def fail_write(source, target):
        target.write(b"partial")
        raise OSError("disk full")

    def fail_commit():
        session.flush()
        raise RuntimeError("commit failed")

    def fail_fsync(fd):
        raise OSError("fsync failed")

    if failure == "write":
        monkeypatch.setattr(saved_files.shutil, "copyfileobj", fail_write)
    elif failure == "verify":
        monkeypatch.setattr(saved_files.shutil, "copyfileobj", lambda source, target: target.seek(10))
    elif failure == "fsync":
        monkeypatch.setattr(saved_files.os, "fsync", fail_fsync)
    else:
        monkeypatch.setattr(session, "commit", fail_commit)
    client = _client(session)
    response = client.put(
        f"/api/v1/management/saved-files/{saved_id}/content",
        files={"file": ("new.txt", b"new", "text/plain")},
    )
    assert response.status_code == 500
    assert response.json()["detail"]["error"]["code"] == "saved_file_replace_failed"
    session.expire_all()
    assert saved.storage_key == storage_key and saved.created_at == created_at
    assert saved.display_filename == "file.pdf" and saved.file_size == 4
    assert saved.mime_type == "application/pdf"
    assert list(tmp_path.iterdir()) == [old_path]
    assert client.get(f"/api/v1/management/saved-files/{saved_id}/download").content == b"data"


def test_replace_old_file_cleanup_failure_keeps_committed_new_file(monkeypatch, tmp_path):
    monkeypatch.setattr(saved_files.settings, "saved_file_storage_dir", str(tmp_path))
    session = _session_factory()()
    saved = _create_saved(session)
    old_path = saved_files.resolve_saved_file_path(saved)
    unlink = Path.unlink

    def fail_old_unlink(path, *args, **kwargs):
        if path == old_path:
            raise PermissionError("busy")
        return unlink(path, *args, **kwargs)

    monkeypatch.setattr(Path, "unlink", fail_old_unlink)
    client = _client(session)
    response = client.put(
        f"/api/v1/management/saved-files/{saved.id}/content",
        files={"file": ("new.txt", b"new", "text/plain")},
    )
    assert response.status_code == 200
    assert old_path.read_bytes() == b"data"
    assert client.get(f"/api/v1/management/saved-files/{saved.id}/download").content == b"new"


@pytest.mark.parametrize("role", ["agent", "trainee", "chiki_piki"])
def test_replace_is_admin_only(monkeypatch, tmp_path, role):
    monkeypatch.setattr(saved_files.settings, "saved_file_storage_dir", str(tmp_path))
    session = _session_factory()()
    saved = _create_saved(session)
    path = saved_files.resolve_saved_file_path(saved)
    response = _client(session, role=role).put(
        f"/api/v1/management/saved-files/{saved.id}/content",
        files={"file": ("new.txt", b"new", "text/plain")},
    )
    assert response.status_code == 403
    assert response.json()["detail"]["error"]["code"] == "admin_only"
    assert list(tmp_path.iterdir()) == [path] and path.read_bytes() == b"data"


def test_replace_missing_id_does_not_write_file(monkeypatch, tmp_path):
    monkeypatch.setattr(saved_files.settings, "saved_file_storage_dir", str(tmp_path))
    response = _client(_session_factory()()).put(
        f"/api/v1/management/saved-files/{uuid.uuid4()}/content",
        files={"file": ("new.txt", b"new", "text/plain")},
    )
    assert response.status_code == 404
    assert response.json()["detail"]["error"]["code"] == "saved_file_not_found"
    assert list(tmp_path.iterdir()) == []
