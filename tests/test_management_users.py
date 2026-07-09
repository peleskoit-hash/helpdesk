from __future__ import annotations

from datetime import datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
import uuid

import pytest
from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from api.app.db import Base
from api.app.models import Client, ClientProfile, Company, TelegramContact, Ticket
from api.app.routers import management_users
from api.app.schemas import ClientProfileUpdate
from api.app.services import telegram_onboarding
from scripts import merge_telegram_contact_duplicate


def _session():
    engine = create_engine("sqlite:///:memory:", future=True)
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine, autoflush=False, autocommit=False)
    return Session()


def _admin():
    return SimpleNamespace(id="admin-1", role="admin", is_real_admin=True)


def _agent():
    return SimpleNamespace(id="agent-1", role="agent", is_real_admin=False)


def _company(session):
    company = Company(id=str(uuid.uuid4()), name="ACME")
    session.add(company)
    session.flush()
    return company


def _seed_user(
    session,
    *,
    company=None,
    username="helper",
    phone="+70000000000",
    internal_number="100",
    external_user_id="100500",
    external_chat_id="9001",
    verified=True,
    updated_delta=5,
):
    company = company or _company(session)
    client = Client(id=str(uuid.uuid4()), company_id=company.id, display_name="Fallback Name")
    profile = ClientProfile(
        id=str(uuid.uuid4()),
        client_id=client.id,
        contact_name="Ivan",
        last_name="Petrov",
        phone=phone,
        internal_number=internal_number,
        destination="Support",
        is_verified=verified,
    )
    contact = TelegramContact(
        id=str(uuid.uuid4()),
        company_id=company.id,
        client_id=client.id,
        external_user_id=external_user_id,
        external_chat_id=external_chat_id,
        external_username=username,
        external_display_name="Ivan TG",
    )
    ticket = Ticket(
        id=str(uuid.uuid4()),
        company_id=company.id,
        client_id=client.id,
        subject="Hello",
        updated_at=datetime.utcnow() + timedelta(minutes=updated_delta),
    )
    session.add_all([client, profile, contact, ticket])
    session.commit()
    return client, profile, contact


def test_management_users_list_searches_telegram_and_profile_fields():
    session = _session()
    client, _, _ = _seed_user(session, username="needle")

    payload = management_users.list_management_users(q="needle", db=session, user=_admin())

    assert payload["total"] == 1
    assert payload["items"][0]["client_id"] == client.id
    assert payload["items"][0]["telegram_username"] == "needle"
    assert payload["items"][0]["display_name"] == "Petrov Ivan"
    assert payload["items"][0]["internal_number"] == "100"
    assert "phone" not in payload["items"][0]
    assert payload["items"][0]["last_activity_at"] is not None


def test_management_users_blocked_filter_is_empty_until_blocking_exists():
    session = _session()
    _seed_user(session)

    payload = management_users.list_management_users(blocked=True, db=session, user=_admin())

    assert payload == {
        "items": [],
        "total": 0,
        "limit": 50,
        "offset": 0,
        "blocked_supported": False,
    }


def test_management_users_requires_admin():
    session = _session()
    _seed_user(session)

    with pytest.raises(HTTPException) as exc:
        management_users.list_management_users(db=session, user=_agent())

    assert exc.value.status_code == 403


def test_management_page_route_is_admin_guarded_in_main():
    source = Path("api/app/main.py").read_text(encoding="utf-8")
    start = source.index('@app.get("/management")')
    end = source.index('@app.get("/management/users")')
    block = source[start:end]
    assert "def management_page" in block
    assert 'return _redirect_login("/management")' in block
    assert "if not user.is_real_admin" in block
    assert 'templates" / "management.html"' in block


def test_management_user_profile_update_refreshes_existing_profile():
    session = _session()
    client, _, _ = _seed_user(session)

    updated = management_users.update_management_user_profile(
        client.id,
        ClientProfileUpdate(contact_name="Anna", last_name="Smirnova", phone="+7111", is_verified=False),
        db=session,
        user=_admin(),
    )

    profile = session.execute(select(ClientProfile).where(ClientProfile.client_id == client.id)).scalar_one()
    assert updated.contact_name == "Anna"
    assert updated.last_name == "Smirnova"
    assert profile.phone == "+7111"
    assert profile.is_verified is False


def test_management_user_profile_patch_does_not_clear_unset_fields():
    session = _session()
    client, profile, _ = _seed_user(session)
    profile.email = "ivan@example.test"
    profile.verification_status = "manual"
    profile.destination = "Support"
    session.commit()

    management_users.update_management_user_profile(
        client.id,
        ClientProfileUpdate(contact_name="Anna", last_name="Smirnova", phone="+7111", is_verified=False),
        db=session,
        user=_admin(),
    )

    session.refresh(profile)
    assert profile.email == "ivan@example.test"
    assert profile.verification_status == "manual"
    assert profile.destination == "Support"


def test_management_users_dedupes_same_external_user_id_and_picks_verified_canonical():
    session = _session()
    company = _company(session)
    duplicate_client, _, _ = _seed_user(
        session,
        company=company,
        username="dupe",
        external_user_id="453920478",
        external_chat_id="-5267117449",
        verified=False,
        updated_delta=100,
    )
    canonical_client, _, _ = _seed_user(
        session,
        company=company,
        username="dupe",
        external_user_id="453920478",
        external_chat_id="453920478",
        verified=True,
        updated_delta=1,
    )

    payload = management_users.list_management_users(db=session, user=_admin())

    rows = [item for item in payload["items"] if item["telegram_user_id"] == "453920478"]
    assert len(rows) == 1
    assert rows[0]["client_id"] == canonical_client.id
    assert rows[0]["client_id"] != duplicate_client.id


def test_management_users_dedupe_keeps_same_external_user_id_in_different_companies():
    session = _session()
    company_a = _company(session)
    company_b = _company(session)
    duplicate_client, _, _ = _seed_user(
        session,
        company=company_a,
        username="dupe-a",
        external_user_id="453920478",
        external_chat_id="-5267117449",
        verified=False,
        updated_delta=100,
    )
    canonical_client, _, _ = _seed_user(
        session,
        company=company_a,
        username="dupe-a",
        external_user_id="453920478",
        external_chat_id="453920478",
        verified=True,
        updated_delta=1,
    )
    other_company_client, _, _ = _seed_user(
        session,
        company=company_b,
        username="dupe-b",
        external_user_id="453920478",
        external_chat_id="999001",
        verified=True,
        updated_delta=2,
    )

    payload = management_users.list_management_users(db=session, user=_admin())

    rows = [item for item in payload["items"] if item["telegram_user_id"] == "453920478"]
    client_ids = {item["client_id"] for item in rows}
    assert len(rows) == 2
    assert canonical_client.id in client_ids
    assert other_company_client.id in client_ids
    assert duplicate_client.id not in client_ids


def test_management_users_paginates_after_dedupe():
    session = _session()
    company = _company(session)
    for index in range(55):
        _seed_user(
            session,
            company=company,
            username=f"user-{index:02d}",
            external_user_id=f"user-{index:02d}",
            external_chat_id=f"chat-{index:02d}",
            updated_delta=index,
        )

    first_page = management_users.list_management_users(limit=50, offset=0, db=session, user=_admin())
    second_page = management_users.list_management_users(limit=50, offset=50, db=session, user=_admin())

    assert first_page["total"] == 55
    assert second_page["total"] == 55
    assert len(first_page["items"]) == 50
    assert len(second_page["items"]) == 5
    first_ids = {item["client_id"] for item in first_page["items"]}
    second_ids = {item["client_id"] for item in second_page["items"]}
    assert first_ids.isdisjoint(second_ids)


def test_management_users_searches_full_result_set_before_pagination():
    session = _session()
    company = _company(session)
    for index in range(55):
        _seed_user(
            session,
            company=company,
            username=f"user-{index:02d}",
            internal_number="needle-54" if index == 54 else f"room-{index:02d}",
            external_user_id=f"user-{index:02d}",
            external_chat_id=f"chat-{index:02d}",
            updated_delta=index,
        )

    payload = management_users.list_management_users(q="needle-54", limit=50, offset=0, db=session, user=_admin())

    assert payload["total"] == 1
    assert len(payload["items"]) == 1
    assert payload["items"][0]["internal_number"] == "needle-54"


def test_management_users_search_after_dedupe_by_username_user_id_and_profile():
    session = _session()
    company = _company(session)
    canonical_client, profile, _ = _seed_user(
        session,
        company=company,
        username="belfiss",
        external_user_id="453920478",
        external_chat_id="453920478",
        internal_number="4455",
    )
    _seed_user(
        session,
        company=company,
        username="belfiss",
        external_user_id="453920478",
        external_chat_id="-5267117449",
        internal_number="7788",
        verified=False,
    )
    profile.last_name = "Sidorov"
    session.commit()

    for query in ("belfiss", "453920478", "Sidorov", "4455"):
        payload = management_users.list_management_users(q=query, db=session, user=_admin())
        assert payload["total"] == 1
        assert payload["items"][0]["client_id"] == canonical_client.id
        assert payload["items"][0]["internal_number"] == "4455"

    phone_payload = management_users.list_management_users(q="+79998887766", db=session, user=_admin())
    assert phone_payload["total"] == 0


def test_telegram_upsert_reuses_existing_external_user_id_and_preserves_private_chat():
    session = _session()
    company = _company(session)
    client, _, contact = _seed_user(
        session,
        company=company,
        username="oldname",
        external_user_id="453920478",
        external_chat_id="453920478",
    )

    updated = telegram_onboarding.get_or_create_contact_from_user(
        session,
        company.id,
        "-5267117449",
        {"id": 453920478, "username": "belfiss", "first_name": "Ivan", "last_name": "Petrov"},
    )
    session.commit()

    contacts = session.execute(select(TelegramContact).where(TelegramContact.external_user_id == "453920478")).scalars().all()
    assert len(contacts) == 1
    assert updated.id == contact.id
    assert updated.client_id == client.id
    assert updated.external_chat_id == "453920478"
    assert updated.external_username == "belfiss"
    assert updated.external_display_name == "Ivan Petrov"


def test_telegram_upsert_replaces_negative_chat_with_private_chat():
    session = _session()
    company = _company(session)
    client, _, contact = _seed_user(
        session,
        company=company,
        username="oldname",
        external_user_id="453920478",
        external_chat_id="-5267117449",
    )

    updated = telegram_onboarding.get_or_create_contact_from_user(
        session,
        company.id,
        "453920478",
        {"id": 453920478, "username": "belfiss", "first_name": "Ivan", "last_name": "Petrov"},
    )
    session.commit()

    assert updated.id == contact.id
    assert updated.client_id == client.id
    assert updated.external_chat_id == "453920478"


def test_telegram_user_id_lookup_is_company_scoped():
    session = _session()
    company_a = _company(session)
    company_b = _company(session)
    _seed_user(
        session,
        company=company_a,
        username="company-a",
        external_user_id="453920478",
        external_chat_id="453920478",
    )

    assert telegram_onboarding.telegram_contact_by_user_id(session, company_b.id, "453920478") is None


def test_merge_duplicate_plan_finds_canonical_and_duplicate():
    session = _session()
    company = _company(session)
    canonical_client, _, _ = _seed_user(
        session,
        company=company,
        username="belfiss",
        external_user_id="453920478",
        external_chat_id="453920478",
        verified=True,
    )
    duplicate_client, duplicate_profile, _ = _seed_user(
        session,
        company=company,
        username="belfiss",
        external_user_id="453920478",
        external_chat_id="-5267117449",
        phone="+71112223344",
        verified=False,
    )
    duplicate_profile.email = "dupe@example.test"
    session.commit()

    plan = merge_telegram_contact_duplicate.build_plan(session, "453920478", "453920478", "-5267117449")

    assert plan.canonical.client_id == canonical_client.id
    assert plan.duplicate.client_id == duplicate_client.id
    assert plan.ticket_count == 1
    assert "email" in plan.merged_profile_fields
    assert plan.duplicate_chat_id_is_private is False
    assert plan.canonical_chat_id_preserved is True
    assert plan.canonical_chat_id_after_apply == "453920478"


def test_merge_duplicate_apply_preserves_private_chat_and_relinks_tickets():
    session = _session()
    company = _company(session)
    canonical_client, _, canonical_contact = _seed_user(
        session,
        company=company,
        username="belfiss",
        external_user_id="453920478",
        external_chat_id="453920478",
        verified=True,
    )
    duplicate_client, _, duplicate_contact = _seed_user(
        session,
        company=company,
        username="belfiss",
        external_user_id="453920478",
        external_chat_id="-5267117449",
        verified=False,
    )

    plan = merge_telegram_contact_duplicate.build_plan(session, "453920478", "453920478", "-5267117449")
    merge_telegram_contact_duplicate.apply_plan(session, plan)

    canonical = session.get(TelegramContact, canonical_contact.id)
    assert canonical is not None
    assert canonical.external_chat_id == "453920478"
    assert session.get(TelegramContact, duplicate_contact.id) is None
    duplicate_tickets = session.execute(select(Ticket).where(Ticket.client_id == duplicate_client.id)).scalars().all()
    canonical_tickets = session.execute(select(Ticket).where(Ticket.client_id == canonical_client.id)).scalars().all()
    assert duplicate_tickets == []
    assert len(canonical_tickets) == 2
