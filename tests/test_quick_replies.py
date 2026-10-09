from __future__ import annotations

from datetime import datetime, timedelta
from types import SimpleNamespace

import pytest
from fastapi import HTTPException
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from api.app.db import Base
from api.app.models import Company, QuickReply, QuickReplyUsage, User
from api.app.routers.quick_replies import (
    QuickReplyPayload,
    create_quick_reply,
    delete_quick_reply,
    list_quick_replies,
    search_quick_replies,
    update_quick_reply,
    use_quick_reply,
)


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
