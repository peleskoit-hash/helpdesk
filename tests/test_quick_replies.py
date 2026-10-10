from __future__ import annotations

from datetime import datetime, timedelta
from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from fastapi import HTTPException
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, select, text
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from api.app.db import Base, get_db
from api.app.models import Company, QuickReply, QuickReplyUsage, SavedFile, User
from api.app.routers.quick_replies import (
    QuickReplyPayload,
    create_quick_reply,
    delete_quick_reply,
    get_current_user,
    list_quick_replies,
    router as quick_replies_router,
    search_quick_replies,
    update_quick_reply,
    upload_quick_reply_file,
    use_quick_reply,
)
from api.app.routers import management_saved_files
from api.app.services import saved_files, schema_evolution


def _session():
    engine = create_engine(
        "sqlite://",
        future=True,
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine, autoflush=False, autocommit=False)
    return Session()


def _user(user_id: str, role: str):
    return SimpleNamespace(id=user_id, role=role, real_user_id=user_id, real_role=role, is_real_admin=role == "admin")


def _seed_users(db):
    company = Company(id="company-1", name="ACME")
    users = [
        User(id="admin-1", company_id=company.id, full_name="Admin", role="admin"),
        User(id="agent-1", company_id=company.id, full_name="Agent One", role="agent"),
        User(id="agent-2", company_id=company.id, full_name="Agent Two", role="agent"),
        User(id="trainee-1", company_id=company.id, full_name="Trainee", role="trainee"),
        User(id="chiki-1", company_id=company.id, full_name="Chiki", role="chiki_piki"),
    ]
    db.add(company)
    db.add_all(users)
    db.commit()


def _create(db, user_id: str, role: str, title: str, body: str):
    return create_quick_reply(QuickReplyPayload(short_title=title, body=body), db=db, user=_user(user_id, role))


def _create_saved(db, *, display_filename: str = "doc.pdf") -> SavedFile:
    saved = saved_files.create_saved_file(
        db,
        content=b"file-bytes",
        display_filename=display_filename,
        mime_type="application/pdf",
    )
    db.commit()
    db.refresh(saved)
    return saved


def _client(db, *, role: str = "agent") -> TestClient:
    app = FastAPI()
    app.include_router(quick_replies_router)
    app.include_router(management_saved_files.router)
    app.dependency_overrides[get_db] = lambda: db
    app.dependency_overrides[get_current_user] = lambda: _user(f"{role}-1", role)
    app.dependency_overrides[management_saved_files.get_current_user] = lambda: _user(f"{role}-1", role)
    return TestClient(app)


def test_admin_and_agent_create_while_chiki_piki_cannot_create():
    db = _session()
    _seed_users(db)

    admin_reply = _create(db, "admin-1", "admin", "Admin title", "Admin body")
    agent_reply = _create(db, "agent-1", "agent", "Agent title", "Agent body")

    assert admin_reply.author_user_id == "admin-1"
    assert agent_reply.author_user_id == "agent-1"
    with pytest.raises(HTTPException) as exc:
        _create(db, "chiki-1", "chiki_piki", "Nope", "Readonly")
    assert exc.value.status_code == 403


def test_create_without_files_keeps_empty_saved_files_list():
    db = _session()
    _seed_users(db)

    reply = _create(db, "agent-1", "agent", "Plain", "No files")

    assert reply.saved_files == []
    assert db.get(QuickReply, reply.id).saved_file_ids_json == []


def test_create_and_update_with_saved_files_dedupes_and_returns_metadata(monkeypatch, tmp_path):
    monkeypatch.setattr(saved_files.settings, "saved_file_storage_dir", str(tmp_path))
    db = _session()
    _seed_users(db)
    first = _create_saved(db, display_filename="first.pdf")
    second = _create_saved(db, display_filename="second.pdf")

    created = create_quick_reply(
        QuickReplyPayload(short_title="With files", body="Body", saved_file_ids=[first.id, first.id, second.id]),
        db=db,
        user=_user("agent-1", "agent"),
    )

    assert [item.id for item in created.saved_files] == [first.id, second.id]
    assert [item.display_filename for item in created.saved_files] == ["first.pdf", "second.pdf"]
    assert db.get(QuickReply, created.id).saved_file_ids_json == [first.id, second.id]

    updated = update_quick_reply(
        created.id,
        QuickReplyPayload(short_title="Updated", body="Body", saved_file_ids=[second.id]),
        db=db,
        user=_user("agent-1", "agent"),
    )

    assert [item.id for item in updated.saved_files] == [second.id]
    assert db.get(QuickReply, created.id).saved_file_ids_json == [second.id]


def test_patch_without_saved_file_ids_preserves_existing_attachments(monkeypatch, tmp_path):
    monkeypatch.setattr(saved_files.settings, "saved_file_storage_dir", str(tmp_path))
    db = _session()
    _seed_users(db)
    saved = _create_saved(db)
    reply = create_quick_reply(
        QuickReplyPayload(short_title="With file", body="Body", saved_file_ids=[saved.id]),
        db=db,
        user=_user("agent-1", "agent"),
    )

    updated = update_quick_reply(
        reply.id,
        QuickReplyPayload(short_title="Text only", body="Still attached"),
        db=db,
        user=_user("agent-1", "agent"),
    )

    assert [item.id for item in updated.saved_files] == [saved.id]
    assert db.get(QuickReply, reply.id).saved_file_ids_json == [saved.id]


def test_saved_file_ids_must_exist(monkeypatch, tmp_path):
    monkeypatch.setattr(saved_files.settings, "saved_file_storage_dir", str(tmp_path))
    db = _session()
    _seed_users(db)

    with pytest.raises(HTTPException) as exc:
        create_quick_reply(
            QuickReplyPayload(short_title="Missing", body="Body", saved_file_ids=["missing-file"]),
            db=db,
            user=_user("agent-1", "agent"),
        )

    assert exc.value.status_code == 404
    assert exc.value.detail["error"]["code"] == "saved_file_not_found"


def test_five_saved_files_are_allowed(monkeypatch, tmp_path):
    monkeypatch.setattr(saved_files.settings, "saved_file_storage_dir", str(tmp_path))
    db = _session()
    _seed_users(db)
    files = [_create_saved(db, display_filename=f"file-{index}.pdf") for index in range(5)]

    reply = create_quick_reply(
        QuickReplyPayload(short_title="Five files", body="Body", saved_file_ids=[saved.id for saved in files]),
        db=db,
        user=_user("agent-1", "agent"),
    )

    assert [item.id for item in reply.saved_files] == [saved.id for saved in files]
    assert db.get(QuickReply, reply.id).saved_file_ids_json == [saved.id for saved in files]


def test_six_saved_files_return_422(monkeypatch, tmp_path):
    monkeypatch.setattr(saved_files.settings, "saved_file_storage_dir", str(tmp_path))
    db = _session()
    _seed_users(db)
    files = [_create_saved(db, display_filename=f"file-{index}.pdf") for index in range(6)]

    with pytest.raises(HTTPException) as exc:
        create_quick_reply(
            QuickReplyPayload(short_title="Six files", body="Body", saved_file_ids=[saved.id for saved in files]),
            db=db,
            user=_user("agent-1", "agent"),
        )

    assert exc.value.status_code == 422
    assert exc.value.detail["error"]["message"] == "К быстрому ответу можно прикрепить не более 5 файлов"


def test_failed_patch_with_six_saved_files_keeps_existing_attachments(monkeypatch, tmp_path):
    monkeypatch.setattr(saved_files.settings, "saved_file_storage_dir", str(tmp_path))
    db = _session()
    _seed_users(db)
    existing = _create_saved(db, display_filename="existing.pdf")
    files = [_create_saved(db, display_filename=f"file-{index}.pdf") for index in range(6)]
    reply = create_quick_reply(
        QuickReplyPayload(short_title="Existing", body="Body", saved_file_ids=[existing.id]),
        db=db,
        user=_user("agent-1", "agent"),
    )

    with pytest.raises(HTTPException) as exc:
        update_quick_reply(
            reply.id,
            QuickReplyPayload(short_title="Too many", body="Body", saved_file_ids=[saved.id for saved in files]),
            db=db,
            user=_user("agent-1", "agent"),
        )

    stored = db.get(QuickReply, reply.id)
    db.refresh(stored)
    assert exc.value.status_code == 422
    assert stored.saved_file_ids_json == [existing.id]


def test_quick_reply_file_upload_allowed_for_admin_and_agent_only(monkeypatch, tmp_path):
    monkeypatch.setattr(saved_files.settings, "saved_file_storage_dir", str(tmp_path))
    db = _session()
    _seed_users(db)

    for user_id, role in [("admin-1", "admin"), ("agent-1", "agent")]:
        upload = SimpleNamespace(file=b"content", filename=f"{role}.txt", content_type="text/plain")
        saved = upload_quick_reply_file(file=upload, db=db, user=_user(user_id, role))
        assert saved.display_filename == f"{role}.txt"

    upload = SimpleNamespace(file=b"content", filename="no.txt", content_type="text/plain")
    with pytest.raises(HTTPException) as exc:
        upload_quick_reply_file(file=upload, db=db, user=_user("trainee-1", "trainee"))
    assert exc.value.status_code == 403


def test_agent_upload_endpoint_does_not_grant_management_saved_files_access(monkeypatch, tmp_path):
    monkeypatch.setattr(saved_files.settings, "saved_file_storage_dir", str(tmp_path))
    db = _session()
    _seed_users(db)

    upload_response = _client(db, role="agent").post(
        "/api/v1/quick-replies/files",
        files={"file": ("agent.txt", b"content", "text/plain")},
    )

    assert upload_response.status_code == 200
    assert db.get(SavedFile, upload_response.json()["id"]) is not None

    management_response = _client(db, role="agent").get("/api/v1/management/saved-files")
    assert management_response.status_code == 403


def test_download_quick_reply_file_returns_bound_saved_file(monkeypatch, tmp_path):
    monkeypatch.setattr(saved_files.settings, "saved_file_storage_dir", str(tmp_path))
    db = _session()
    _seed_users(db)
    saved = _create_saved(db, display_filename="answer.pdf")
    reply = create_quick_reply(
        QuickReplyPayload(short_title="With file", body="Body", saved_file_ids=[saved.id]),
        db=db,
        user=_user("agent-1", "agent"),
    )

    response = _client(db, role="agent").get(f"/api/v1/quick-replies/{reply.id}/files/{saved.id}/download")

    assert response.status_code == 200
    assert response.content == b"file-bytes"
    assert response.headers["content-type"] == "application/pdf"
    assert "filename*=UTF-8''answer.pdf" in response.headers["content-disposition"]


def test_download_quick_reply_file_rejects_unbound_saved_file(monkeypatch, tmp_path):
    monkeypatch.setattr(saved_files.settings, "saved_file_storage_dir", str(tmp_path))
    db = _session()
    _seed_users(db)
    bound = _create_saved(db, display_filename="bound.pdf")
    other = _create_saved(db, display_filename="other.pdf")
    reply = create_quick_reply(
        QuickReplyPayload(short_title="With file", body="Body", saved_file_ids=[bound.id]),
        db=db,
        user=_user("agent-1", "agent"),
    )

    response = _client(db, role="agent").get(f"/api/v1/quick-replies/{reply.id}/files/{other.id}/download")

    assert response.status_code == 404
    assert response.json()["detail"]["error"]["code"] == "quick_reply_file_not_found"


def test_edit_permissions_and_author_is_immutable():
    db = _session()
    _seed_users(db)
    reply = _create(db, "agent-1", "agent", "Original", "Body")

    updated = update_quick_reply(
        reply.id,
        QuickReplyPayload(short_title="Owner edit", body="Updated"),
        db=db,
        user=_user("agent-1", "agent"),
    )
    assert updated.short_title == "Owner edit"
    assert updated.author_user_id == "agent-1"

    with pytest.raises(HTTPException) as exc:
        update_quick_reply(reply.id, QuickReplyPayload(short_title="Other", body="No"), db=db, user=_user("agent-2", "agent"))
    assert exc.value.status_code == 403

    admin_updated = update_quick_reply(
        reply.id,
        QuickReplyPayload(short_title="Admin edit", body="Still agent authored"),
        db=db,
        user=_user("admin-1", "admin"),
    )
    assert admin_updated.author_user_id == "agent-1"


def test_all_authenticated_roles_can_list_search_and_use():
    db = _session()
    _seed_users(db)
    reply = _create(db, "agent-1", "agent", "VPN", "Restart AnyDesk")

    for user_id, role in [("admin-1", "admin"), ("agent-2", "agent"), ("trainee-1", "trainee"), ("chiki-1", "chiki_piki")]:
        user = _user(user_id, role)
        assert list_quick_replies(db=db, user=user)
        assert search_quick_replies(q="vpn", db=db, user=user)[0].id == reply.id
        assert use_quick_reply(reply.id, db=db, user=user).id == reply.id


def test_list_search_and_use_return_saved_files(monkeypatch, tmp_path):
    monkeypatch.setattr(saved_files.settings, "saved_file_storage_dir", str(tmp_path))
    db = _session()
    _seed_users(db)
    saved = _create_saved(db, display_filename="answer.pdf")
    reply = create_quick_reply(
        QuickReplyPayload(short_title="Files", body="See file", saved_file_ids=[saved.id]),
        db=db,
        user=_user("agent-1", "agent"),
    )
    user = _user("agent-2", "agent")

    assert [item.id for item in list_quick_replies(db=db, user=user)[0].saved_files] == [saved.id]
    assert [item.id for item in search_quick_replies(q="Files", db=db, user=user)[0].saved_files] == [saved.id]
    assert [item.id for item in use_quick_reply(reply.id, db=db, user=user).saved_files] == [saved.id]


def test_search_is_case_insensitive_ranked_and_limited_to_five():
    db = _session()
    _seed_users(db)
    body_only = _create(db, "agent-1", "agent", "Other", "Need Printer help")
    middle = _create(db, "agent-1", "agent", "Office printer", "Middle")
    prefix = _create(db, "agent-1", "agent", "Printer setup", "Prefix")
    for index in range(4):
        _create(db, "agent-1", "agent", f"Z body extra {index}", "Printer extra")

    results = search_quick_replies(q="pRiNtEr", db=db, user=_user("agent-1", "agent"))

    assert len(results) == 5
    assert results[0].id == prefix.id
    assert results.index(next(item for item in results if item.id == middle.id)) < results.index(next(item for item in results if item.id == body_only.id))


def test_empty_search_returns_personal_mru_top_five_without_duplicates():
    db = _session()
    _seed_users(db)
    replies = [_create(db, "agent-1", "agent", f"R{index}", f"Body {index}") for index in range(6)]

    for reply in replies:
        use_quick_reply(reply.id, db=db, user=_user("agent-1", "agent"))
    for index, reply in enumerate(replies):
        usage = db.get(QuickReplyUsage, {"user_id": "agent-1", "quick_reply_id": reply.id})
        usage.last_used_at = datetime.utcnow() + timedelta(minutes=index)
    db.commit()

    use_quick_reply(replies[1].id, db=db, user=_user("agent-1", "agent"))
    repeated = db.get(QuickReplyUsage, {"user_id": "agent-1", "quick_reply_id": replies[1].id})
    repeated.last_used_at = datetime.utcnow() + timedelta(minutes=10)
    db.commit()
    assert db.execute(select(QuickReplyUsage).where(QuickReplyUsage.user_id == "agent-1")).scalars().all().__len__() == 6

    results = search_quick_replies(q="", db=db, user=_user("agent-1", "agent"))
    assert len(results) == 5
    assert results[0].id == replies[1].id
    assert replies[0].id not in {item.id for item in results}

    use_quick_reply(replies[0].id, db=db, user=_user("agent-2", "agent"))
    user_b_results = search_quick_replies(q="", db=db, user=_user("agent-2", "agent"))
    assert [item.id for item in user_b_results] == [replies[0].id]


def test_admin_deletes_own_reply():
    db = _session()
    _seed_users(db)
    reply = _create(db, "admin-1", "admin", "Admin title", "Admin body")

    assert delete_quick_reply(reply.id, db=db, user=_user("admin-1", "admin")) == {"ok": True}

    assert db.get(QuickReply, reply.id) is None


def test_delete_quick_reply_keeps_saved_file(monkeypatch, tmp_path):
    monkeypatch.setattr(saved_files.settings, "saved_file_storage_dir", str(tmp_path))
    db = _session()
    _seed_users(db)
    saved = _create_saved(db)
    path = saved_files.resolve_saved_file_path(saved)
    reply = create_quick_reply(
        QuickReplyPayload(short_title="Delete", body="Body", saved_file_ids=[saved.id]),
        db=db,
        user=_user("agent-1", "agent"),
    )

    delete_quick_reply(reply.id, db=db, user=_user("admin-1", "admin"))

    assert db.get(QuickReply, reply.id) is None
    assert db.get(SavedFile, saved.id) is not None
    assert path.exists()


def test_detaching_saved_file_keeps_saved_file(monkeypatch, tmp_path):
    monkeypatch.setattr(saved_files.settings, "saved_file_storage_dir", str(tmp_path))
    db = _session()
    _seed_users(db)
    saved = _create_saved(db)
    reply = create_quick_reply(
        QuickReplyPayload(short_title="Detach", body="Body", saved_file_ids=[saved.id]),
        db=db,
        user=_user("agent-1", "agent"),
    )

    updated = update_quick_reply(
        reply.id,
        QuickReplyPayload(short_title="Detach", body="Body", saved_file_ids=[]),
        db=db,
        user=_user("agent-1", "agent"),
    )

    assert updated.saved_files == []
    assert db.get(SavedFile, saved.id) is not None


def test_one_saved_file_can_be_used_in_multiple_quick_replies(monkeypatch, tmp_path):
    monkeypatch.setattr(saved_files.settings, "saved_file_storage_dir", str(tmp_path))
    db = _session()
    _seed_users(db)
    saved = _create_saved(db)

    first = create_quick_reply(
        QuickReplyPayload(short_title="First", body="Body", saved_file_ids=[saved.id]),
        db=db,
        user=_user("agent-1", "agent"),
    )
    second = create_quick_reply(
        QuickReplyPayload(short_title="Second", body="Body", saved_file_ids=[saved.id]),
        db=db,
        user=_user("agent-2", "agent"),
    )

    assert [item.id for item in first.saved_files] == [saved.id]
    assert [item.id for item in second.saved_files] == [saved.id]


def test_quick_reply_schema_evolution_adds_saved_file_column_without_losing_rows(monkeypatch):
    engine = create_engine("sqlite:///:memory:", future=True)
    monkeypatch.setattr(schema_evolution, "engine", engine)
    with engine.begin() as conn:
        conn.execute(
            text(
                """
            CREATE TABLE quick_replies (
                id VARCHAR(36) PRIMARY KEY,
                short_title VARCHAR(255) NOT NULL,
                body TEXT NOT NULL,
                author_user_id VARCHAR(36) NOT NULL,
                created_at TIMESTAMP,
                updated_at TIMESTAMP
            )
            """
            )
        )
        conn.execute(
            text(
                """
            INSERT INTO quick_replies (id, short_title, body, author_user_id, created_at, updated_at)
            VALUES ('reply-1', 'Old', 'Body', 'agent-1', '2026-01-01', '2026-01-02')
            """
            )
        )

    schema_evolution.ensure_quick_reply_tables()
    schema_evolution.ensure_quick_reply_tables()

    with engine.connect() as conn:
        columns = {row["name"] for row in conn.execute(text("PRAGMA table_info(quick_replies)")).mappings().all()}
        row = conn.execute(text("SELECT short_title, body, saved_file_ids_json FROM quick_replies WHERE id = 'reply-1'")).mappings().one()

    assert "saved_file_ids_json" in columns
    assert row["short_title"] == "Old"
    assert row["body"] == "Body"
    assert row["saved_file_ids_json"] is None


def test_admin_deletes_other_agent_reply():
    db = _session()
    _seed_users(db)
    reply = _create(db, "agent-1", "agent", "Agent title", "Agent body")

    assert delete_quick_reply(reply.id, db=db, user=_user("admin-1", "admin")) == {"ok": True}

    assert db.get(QuickReply, reply.id) is None


def test_agent_cannot_delete_own_reply():
    db = _session()
    _seed_users(db)
    reply = _create(db, "agent-1", "agent", "Agent title", "Agent body")

    with pytest.raises(HTTPException) as exc:
        delete_quick_reply(reply.id, db=db, user=_user("agent-1", "agent"))

    assert exc.value.status_code == 403
    assert db.get(QuickReply, reply.id) is not None


def test_delete_missing_reply_returns_404():
    db = _session()
    _seed_users(db)

    with pytest.raises(HTTPException) as exc:
        delete_quick_reply("missing", db=db, user=_user("admin-1", "admin"))

    assert exc.value.status_code == 404


def test_delete_cleans_usage_and_keeps_other_replies():
    db = _session()
    _seed_users(db)
    deleted = _create(db, "agent-1", "agent", "Delete me", "Body")
    kept = _create(db, "agent-1", "agent", "Keep me", "Body")
    use_quick_reply(deleted.id, db=db, user=_user("agent-1", "agent"))
    use_quick_reply(kept.id, db=db, user=_user("agent-1", "agent"))

    delete_quick_reply(deleted.id, db=db, user=_user("admin-1", "admin"))

    assert db.get(QuickReply, deleted.id) is None
    assert db.get(QuickReply, kept.id) is not None
    assert db.execute(select(QuickReplyUsage).where(QuickReplyUsage.quick_reply_id == deleted.id)).scalars().all() == []
    assert db.execute(select(QuickReplyUsage).where(QuickReplyUsage.quick_reply_id == kept.id)).scalars().all()
