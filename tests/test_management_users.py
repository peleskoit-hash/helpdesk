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
from api.app.models import Client, ClientProfile, Company, PendingTaskIntent, TelegramContact, Ticket, TicketStatus, TicketWorkflowType
from api.app.routers import management_users, telegram
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
    is_blocked=False,
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
        is_blocked=is_blocked,
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
    assert payload["items"][0]["is_blocked"] is False
    assert payload["blocked_supported"] is True
    assert payload["items"][0]["last_activity_at"] is not None


def test_management_users_blocked_filter_returns_only_blocked_contacts():
    session = _session()
    company = _company(session)
    open_client, _, _ = _seed_user(session, company=company, username="open", external_user_id="open", external_chat_id="open")
    blocked_client, _, _ = _seed_user(
        session,
        company=company,
        username="blocked",
        external_user_id="blocked",
        external_chat_id="blocked",
        is_blocked=True,
    )

    all_payload = management_users.list_management_users(db=session, user=_admin())
    payload = management_users.list_management_users(blocked=True, db=session, user=_admin())

    assert all_payload["total"] == 1
    assert all_payload["items"][0]["client_id"] == open_client.id
    assert all_payload["items"][0]["is_blocked"] is False
    assert payload["blocked_supported"] is True
    assert payload["total"] == 1
    assert payload["items"][0]["client_id"] == blocked_client.id
    assert payload["items"][0]["is_blocked"] is True


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
    assert 'RedirectResponse(url=f"/settings{query}", status_code=303)' in block


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


def test_management_users_block_and_unblock_are_idempotent_and_sync_duplicates():
    session = _session()
    company = _company(session)
    canonical_client, _, canonical_contact = _seed_user(
        session,
        company=company,
        username="dupe",
        external_user_id="453920478",
        external_chat_id="453920478",
        verified=True,
    )
    _, _, duplicate_contact = _seed_user(
        session,
        company=company,
        username="dupe",
        external_user_id="453920478",
        external_chat_id="-5267117449",
        verified=False,
    )

    first = management_users.block_management_user(canonical_client.id, db=session, user=_admin())
    second = management_users.block_management_user(canonical_client.id, db=session, user=_admin())

    session.refresh(canonical_contact)
    session.refresh(duplicate_contact)
    assert first["is_blocked"] is True
    assert second["is_blocked"] is True
    assert first["updated_contacts"] == 2
    assert canonical_contact.is_blocked is True
    assert duplicate_contact.is_blocked is True

    blocked_payload = management_users.list_management_users(blocked=True, db=session, user=_admin())
    assert blocked_payload["total"] == 1
    assert blocked_payload["items"][0]["client_id"] == canonical_client.id
    assert blocked_payload["items"][0]["is_blocked"] is True

    third = management_users.unblock_management_user(canonical_client.id, db=session, user=_admin())
    fourth = management_users.unblock_management_user(canonical_client.id, db=session, user=_admin())

    session.refresh(canonical_contact)
    session.refresh(duplicate_contact)
    assert third["is_blocked"] is False
    assert fourth["is_blocked"] is False
    assert canonical_contact.is_blocked is False
    assert duplicate_contact.is_blocked is False


def test_management_users_block_requires_admin():
    session = _session()
    client, _, _ = _seed_user(session)

    with pytest.raises(HTTPException) as exc:
        management_users.block_management_user(client.id, db=session, user=_agent())

    assert exc.value.status_code == 403


def test_management_users_reset_data_requires_admin():
    session = _session()
    client, _, _ = _seed_user(session)

    with pytest.raises(HTTPException) as exc:
        management_users.reset_management_user_profile_data(client.id, db=session, user=_agent())

    assert exc.value.status_code == 403


def test_management_users_dedupe_reports_blocked_when_duplicate_is_blocked():
    session = _session()
    company = _company(session)
    canonical_client, _, _ = _seed_user(
        session,
        company=company,
        username="dupe",
        external_user_id="453920478",
        external_chat_id="453920478",
        verified=True,
        is_blocked=False,
    )
    _seed_user(
        session,
        company=company,
        username="dupe",
        external_user_id="453920478",
        external_chat_id="-5267117449",
        verified=False,
        is_blocked=True,
    )

    all_payload = management_users.list_management_users(db=session, user=_admin())
    blocked_payload = management_users.list_management_users(blocked=True, db=session, user=_admin())

    rows = [item for item in all_payload["items"] if item["telegram_user_id"] == "453920478"]
    assert len(rows) == 0
    assert blocked_payload["total"] == 1
    assert blocked_payload["items"][0]["client_id"] == canonical_client.id
    assert blocked_payload["items"][0]["is_blocked"] is True


def test_management_users_search_preserves_blocked_state_across_hidden_duplicate():
    session = _session()
    company = _company(session)
    canonical_client, canonical_profile, canonical_contact = _seed_user(
        session,
        company=company,
        username="visible-user",
        external_user_id="453920478",
        external_chat_id="453920478",
        verified=True,
        is_blocked=False,
    )
    canonical_profile.internal_number = "ROOM-42"
    canonical_contact.external_username = "visible-user"
    blocked_client, blocked_profile, blocked_contact = _seed_user(
        session,
        company=company,
        username="hidden-user",
        external_user_id="453920478",
        external_chat_id="-5267117449",
        verified=False,
        is_blocked=True,
    )
    blocked_profile.internal_number = "OTHER-99"
    blocked_contact.external_username = "hidden-user"
    session.commit()

    payload = management_users.list_management_users(q="ROOM-42", db=session, user=_admin())
    blocked_payload = management_users.list_management_users(q="ROOM-42", blocked=True, db=session, user=_admin())

    assert payload["total"] == 0
    assert blocked_payload["total"] == 1
    assert blocked_payload["items"][0]["client_id"] == canonical_client.id
    assert blocked_payload["items"][0]["is_blocked"] is True


def test_management_users_blocked_state_stays_company_scoped_for_same_external_user_id():
    session = _session()
    company_a = _company(session)
    company_b = _company(session)
    client_a, profile_a, contact_a = _seed_user(
        session,
        company=company_a,
        username="company-a",
        external_user_id="453920478",
        external_chat_id="453920478",
        verified=True,
        is_blocked=False,
    )
    profile_a.internal_number = "ROOM-A"
    duplicate_a = TelegramContact(
        id=str(uuid.uuid4()),
        company_id=company_a.id,
        client_id=client_a.id,
        external_user_id="453920478",
        external_chat_id="-5267117449",
        external_username="company-a-hidden",
        external_display_name="Ivan TG Hidden",
        is_blocked=True,
    )
    client_b, profile_b, contact_b = _seed_user(
        session,
        company=company_b,
        username="company-b",
        external_user_id="453920478",
        external_chat_id="999001",
        verified=True,
        is_blocked=False,
    )
    profile_b.internal_number = "ROOM-B"
    session.add(duplicate_a)
    session.commit()

    payload_a = management_users.list_management_users(q="ROOM-A", db=session, user=_admin())
    blocked_payload_a = management_users.list_management_users(q="ROOM-A", blocked=True, db=session, user=_admin())
    payload_b = management_users.list_management_users(q="ROOM-B", db=session, user=_admin())

    assert payload_a["total"] == 0
    assert blocked_payload_a["total"] == 1
    assert blocked_payload_a["items"][0]["client_id"] == client_a.id
    assert blocked_payload_a["items"][0]["is_blocked"] is True

    assert payload_b["total"] == 1
    assert payload_b["items"][0]["client_id"] == client_b.id
    assert payload_b["items"][0]["is_blocked"] is False


def test_management_users_reset_data_clears_profiles_for_identity_and_preserves_tickets():
    session = _session()
    company = _company(session)
    client, profile, contact = _seed_user(
        session,
        company=company,
        external_user_id="453920478",
        external_chat_id="453920478",
        is_blocked=True,
    )
    profile.email = "ivan@example.test"
    profile.verification_status = "manual"
    profile.external_user_id = "inner-1"
    profile.custom_fields = {"custom_note": "keep-me-not", "custom_fio": "Иван Петров"}
    client.display_name = "Mapped Name"
    duplicate_client = Client(id=str(uuid.uuid4()), company_id=company.id, display_name="Duplicate Name")
    duplicate_profile = ClientProfile(
        id=str(uuid.uuid4()),
        client_id=duplicate_client.id,
        contact_name="Dupe",
        last_name="User",
        destination="Support",
        is_verified=True,
        custom_fields={"custom_note": "duplicate"},
    )
    duplicate_contact = TelegramContact(
        id=str(uuid.uuid4()),
        company_id=company.id,
        client_id=duplicate_client.id,
        external_user_id="453920478",
        external_chat_id="-5267117449",
        external_username="dupe-user",
        external_display_name="Dupe TG",
        is_blocked=True,
    )
    duplicate_ticket = Ticket(
        id=str(uuid.uuid4()),
        company_id=company.id,
        client_id=duplicate_client.id,
        subject="Duplicate Ticket",
        status=TicketStatus.CLOSED.value,
        call_center_profile_snapshot_json={"profile_delta": {"contact_name": "Historical", "custom_fields": {"custom_fio": "Historic Name"}}},
    )
    session.add_all([duplicate_client, duplicate_profile, duplicate_contact, duplicate_ticket])
    original_ticket = session.execute(select(Ticket).where(Ticket.client_id == client.id)).scalar_one()
    original_ticket.status = TicketStatus.CLOSED.value
    original_ticket.closed_at = datetime.utcnow()
    session.commit()

    original_snapshot = duplicate_ticket.call_center_profile_snapshot_json
    result = management_users.reset_management_user_profile_data(client.id, db=session, user=_admin())

    session.refresh(contact)
    session.refresh(duplicate_contact)
    session.refresh(client)
    session.refresh(duplicate_client)
    assert result["ok"] is True
    assert result["client_id"] == client.id
    assert result["is_blocked"] is True
    assert result["profile"]["telegram_username"] == contact.external_username
    assert result["profile"]["telegram_display_name"] == contact.external_display_name
    assert result["profile"]["is_verified"] is False
    assert result["profile"]["destination"] is None
    assert result["profile"]["custom_fields"] == {}
    assert session.execute(
        select(ClientProfile).where(ClientProfile.client_id.in_([client.id, duplicate_client.id]))
    ).scalars().all() == []
    assert contact.company_id == company.id
    assert contact.external_user_id == "453920478"
    assert contact.external_chat_id == "453920478"
    assert contact.is_blocked is True
    assert duplicate_contact.is_blocked is True
    assert client.display_name == "Ivan TG"
    assert duplicate_client.display_name == "Dupe TG"
    session.refresh(duplicate_ticket)
    assert session.get(Ticket, duplicate_ticket.id) is not None
    assert duplicate_ticket.call_center_profile_snapshot_json == original_snapshot


def test_management_users_reset_data_rejects_client_with_active_ordinary_ticket():
    session = _session()
    company = _company(session)
    client, profile, contact = _seed_user(
        session,
        company=company,
        external_user_id="453920478",
        external_chat_id="453920478",
        verified=True,
        is_blocked=True,
    )
    profile.email = "ivan@example.test"
    profile.verification_status = "manual"
    profile.custom_fields = {"custom_note": "keep"}
    ticket = session.execute(select(Ticket).where(Ticket.client_id == client.id)).scalar_one()
    original_ticket_status = ticket.status
    original_ticket_closed_at = ticket.closed_at
    session.commit()

    with pytest.raises(HTTPException) as exc:
        management_users.reset_management_user_profile_data(client.id, db=session, user=_admin())

    assert exc.value.status_code == 409
    assert exc.value.detail["error"]["code"] == "client_has_active_ticket"
    assert "активная заявка" in exc.value.detail["error"]["message"]
    session.refresh(profile)
    session.refresh(ticket)
    session.refresh(contact)
    assert session.get(ClientProfile, profile.id) is not None
    assert profile.is_verified is True
    assert profile.email == "ivan@example.test"
    assert profile.verification_status == "manual"
    assert profile.custom_fields == {"custom_note": "keep"}
    assert ticket.status == original_ticket_status
    assert ticket.closed_at == original_ticket_closed_at
    assert contact.is_blocked is True

    ticket.status = TicketStatus.CLOSED.value
    ticket.closed_at = datetime.utcnow()
    session.commit()

    result = management_users.reset_management_user_profile_data(client.id, db=session, user=_admin())

    assert result["ok"] is True
    assert session.execute(select(ClientProfile).where(ClientProfile.client_id == client.id)).scalar_one_or_none() is None


def test_management_users_reset_data_rejects_client_with_active_task():
    session = _session()
    company = _company(session)
    client, profile, _ = _seed_user(
        session,
        company=company,
        external_user_id="9001",
        external_chat_id="9001",
        verified=True,
        is_blocked=False,
    )
    ordinary_ticket = session.execute(select(Ticket).where(Ticket.client_id == client.id)).scalar_one()
    ordinary_ticket.status = TicketStatus.CLOSED.value
    ordinary_ticket.closed_at = datetime.utcnow()
    task_ticket = Ticket(
        id=str(uuid.uuid4()),
        company_id=company.id,
        client_id=client.id,
        subject="Task",
        workflow_type=TicketWorkflowType.TASK.value,
        status=TicketStatus.NEW.value,
    )
    session.add(task_ticket)
    session.commit()

    with pytest.raises(HTTPException) as exc:
        management_users.reset_management_user_profile_data(client.id, db=session, user=_admin())

    assert exc.value.status_code == 409
    assert exc.value.detail["error"]["code"] == "client_has_active_ticket"
    assert session.get(ClientProfile, profile.id) is not None

    task_ticket.status = TicketStatus.CLOSED.value
    task_ticket.closed_at = datetime.utcnow()
    session.commit()

    result = management_users.reset_management_user_profile_data(client.id, db=session, user=_admin())

    assert result["ok"] is True
    assert session.execute(select(ClientProfile).where(ClientProfile.client_id == client.id)).scalar_one_or_none() is None


def test_management_users_reset_data_rejects_client_with_active_auto_task():
    session = _session()
    company = _company(session)
    client, profile, _ = _seed_user(
        session,
        company=company,
        external_user_id="9002",
        external_chat_id="9002",
        verified=True,
        is_blocked=False,
    )
    ordinary_ticket = session.execute(select(Ticket).where(Ticket.client_id == client.id)).scalar_one()
    ordinary_ticket.status = TicketStatus.CLOSED.value
    ordinary_ticket.closed_at = datetime.utcnow()
    auto_ticket = Ticket(
        id=str(uuid.uuid4()),
        company_id=company.id,
        client_id=client.id,
        subject="Auto",
        workflow_type=TicketWorkflowType.AUTO.value,
        status=TicketStatus.NEW.value,
    )
    session.add(auto_ticket)
    session.commit()

    with pytest.raises(HTTPException) as exc:
        management_users.reset_management_user_profile_data(client.id, db=session, user=_admin())

    assert exc.value.status_code == 409
    assert exc.value.detail["error"]["code"] == "client_has_active_ticket"
    assert session.get(ClientProfile, profile.id) is not None

    auto_ticket.status = TicketStatus.CLOSED.value
    auto_ticket.closed_at = datetime.utcnow()
    session.commit()

    result = management_users.reset_management_user_profile_data(client.id, db=session, user=_admin())

    assert result["ok"] is True
    assert session.execute(select(ClientProfile).where(ClientProfile.client_id == client.id)).scalar_one_or_none() is None


def test_management_users_reset_data_is_idempotent_and_restores_first_connection_flow():
    session = _session()
    company = _company(session)
    client, _, contact = _seed_user(
        session,
        company=company,
        external_user_id="7001",
        external_chat_id="7001",
        verified=True,
        is_blocked=False,
    )
    ticket = session.execute(select(Ticket).where(Ticket.client_id == client.id)).scalar_one()
    ticket.status = TicketStatus.CLOSED.value
    ticket.closed_at = datetime.utcnow()
    session.commit()

    first = management_users.reset_management_user_profile_data(client.id, db=session, user=_admin())
    second = management_users.reset_management_user_profile_data(client.id, db=session, user=_admin())

    assert first["ok"] is True
    assert second["ok"] is True
    assert first["is_blocked"] is False
    assert second["is_blocked"] is False
    assert session.execute(select(ClientProfile).where(ClientProfile.client_id == client.id)).scalar_one_or_none() is None

    result = telegram.process_telegram_update(
        session,
        {
            "update_id": 5001,
            "message": {
                "message_id": 5001,
                "chat": {"id": 7001},
                "from": {"id": 7001, "first_name": "Ivan", "last_name": "Petrov"},
                "text": "/start",
            },
        },
        notify_client=False,
    )

    assert result["ok"] is True
    assert result["first_connection"] is True


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
    assert plan.canonical_chat_id_is_private is True
    assert plan.duplicate_chat_id_is_private is False
    assert plan.canonical_chat_id_preserved is True
    assert plan.canonical_chat_id_after_apply == "453920478"
    assert plan.duplicate_client_can_delete is True


def test_merge_duplicate_preflight_fails_for_different_company():
    session = _session()
    canonical_company = _company(session)
    duplicate_company = _company(session)
    _seed_user(
        session,
        company=canonical_company,
        username="belfiss",
        external_user_id="453920478",
        external_chat_id="453920478",
    )
    _seed_user(
        session,
        company=duplicate_company,
        username="belfiss",
        external_user_id="453920478",
        external_chat_id="-5267117449",
    )

    canonical = telegram_onboarding.telegram_contact_by_chat_id(session, "453920478", company_id=canonical_company.id)
    duplicate = telegram_onboarding.telegram_contact_by_chat_id(session, "-5267117449", company_id=duplicate_company.id)

    with pytest.raises(SystemExit, match="different companies"):
        merge_telegram_contact_duplicate.validate_preflight(canonical, duplicate)


def test_merge_duplicate_preflight_fails_for_different_user_id():
    session = _session()
    company = _company(session)
    _seed_user(
        session,
        company=company,
        username="belfiss",
        external_user_id="453920478",
        external_chat_id="453920478",
    )
    client = Client(id=str(uuid.uuid4()), company_id=company.id, display_name="Fallback Name")
    profile = ClientProfile(id=str(uuid.uuid4()), client_id=client.id, contact_name="Ivan", is_verified=False)
    contact = TelegramContact(
        id=str(uuid.uuid4()),
        company_id=company.id,
        client_id=client.id,
        external_user_id="999001",
        external_chat_id="-5267117449",
        external_username="belfiss",
        external_display_name="Ivan TG",
    )
    ticket = Ticket(id=str(uuid.uuid4()), company_id=company.id, client_id=client.id, subject="Hello")
    session.add_all([client, profile, contact, ticket])
    session.commit()

    canonical = telegram_onboarding.telegram_contact_by_chat_id(session, "453920478", company_id=company.id)

    with pytest.raises(SystemExit, match="different external_user_id"):
        merge_telegram_contact_duplicate.validate_preflight(canonical, contact)


def test_merge_duplicate_preflight_fails_for_reversed_roles():
    session = _session()
    company = _company(session)
    _seed_user(
        session,
        company=company,
        username="belfiss",
        external_user_id="453920478",
        external_chat_id="453920478",
    )
    _seed_user(
        session,
        company=company,
        username="belfiss",
        external_user_id="453920478",
        external_chat_id="-5267117449",
    )

    canonical = telegram_onboarding.telegram_contact_by_chat_id(session, "-5267117449", company_id=company.id)
    duplicate = telegram_onboarding.telegram_contact_by_chat_id(session, "453920478", company_id=company.id)

    with pytest.raises(SystemExit, match="external_chat_id exactly equal to external_user_id"):
        merge_telegram_contact_duplicate.validate_preflight(canonical, duplicate)


def test_merge_duplicate_preflight_fails_for_same_contact():
    session = _session()
    company = _company(session)
    _seed_user(
        session,
        company=company,
        username="belfiss",
        external_user_id="453920478",
        external_chat_id="453920478",
    )

    canonical = telegram_onboarding.telegram_contact_by_chat_id(session, "453920478", company_id=company.id)

    with pytest.raises(SystemExit, match="same TelegramContact"):
        merge_telegram_contact_duplicate.validate_preflight(canonical, canonical)


def test_merge_duplicate_preflight_fails_for_same_client():
    session = _session()
    company = _company(session)
    client, _, canonical_contact = _seed_user(
        session,
        company=company,
        username="belfiss",
        external_user_id="453920478",
        external_chat_id="453920478",
    )
    duplicate_contact = TelegramContact(
        id=str(uuid.uuid4()),
        company_id=company.id,
        client_id=client.id,
        external_user_id="453920478",
        external_chat_id="-5267117449",
        external_username="belfiss",
        external_display_name="Ivan TG",
    )
    session.add(duplicate_contact)
    session.commit()

    with pytest.raises(SystemExit, match="same Client"):
        merge_telegram_contact_duplicate.validate_preflight(canonical_contact, duplicate_contact)


def test_merge_duplicate_preflight_fails_when_canonical_positive_chat_does_not_match_user_id():
    session = _session()
    company = _company(session)
    canonical_client, _, canonical_contact = _seed_user(
        session,
        company=company,
        username="strict-canonical",
        external_user_id="123",
        external_chat_id="999",
    )
    duplicate_client = Client(id=str(uuid.uuid4()), company_id=company.id, display_name="Duplicate")
    duplicate_profile = ClientProfile(id=str(uuid.uuid4()), client_id=duplicate_client.id, contact_name="Dupe", is_verified=False)
    duplicate_contact = TelegramContact(
        id=str(uuid.uuid4()),
        company_id=company.id,
        client_id=duplicate_client.id,
        external_user_id="123",
        external_chat_id="-1001",
        external_username="strict-duplicate",
        external_display_name="Duplicate TG",
    )
    duplicate_ticket = Ticket(id=str(uuid.uuid4()), company_id=company.id, client_id=duplicate_client.id, subject="Hello")
    session.add_all([duplicate_client, duplicate_profile, duplicate_contact, duplicate_ticket])
    session.commit()

    with pytest.raises(SystemExit, match="external_chat_id exactly equal to external_user_id"):
        merge_telegram_contact_duplicate.validate_preflight(canonical_contact, duplicate_contact)


def test_merge_duplicate_preflight_passes_for_exact_private_canonical_and_negative_duplicate():
    session = _session()
    company = _company(session)
    _seed_user(
        session,
        company=company,
        username="strict-canonical",
        external_user_id="123",
        external_chat_id="123",
    )
    _seed_user(
        session,
        company=company,
        username="strict-duplicate",
        external_user_id="123",
        external_chat_id="-1001",
    )

    plan = merge_telegram_contact_duplicate.build_plan(session, "123", "123", "-1001")

    assert plan.canonical.external_chat_id == "123"
    assert plan.duplicate.external_chat_id == "-1001"


def test_merge_duplicate_preflight_fails_for_arbitrary_positive_duplicate_chat():
    session = _session()
    company = _company(session)
    _seed_user(
        session,
        company=company,
        username="strict-canonical",
        external_user_id="123",
        external_chat_id="123",
    )
    duplicate_client, _, duplicate_contact = _seed_user(
        session,
        company=company,
        username="strict-duplicate",
        external_user_id="123",
        external_chat_id="999",
    )

    with pytest.raises(SystemExit, match="negative historical chat id; manual review required"):
        merge_telegram_contact_duplicate.build_plan(session, "123", "123", duplicate_contact.external_chat_id)


def test_merge_duplicate_plan_marks_delete_blocked_when_extra_contact_would_remain():
    session = _session()
    company = _company(session)
    _seed_user(
        session,
        company=company,
        username="belfiss",
        external_user_id="453920478",
        external_chat_id="453920478",
    )
    duplicate_client, _, duplicate_contact = _seed_user(
        session,
        company=company,
        username="belfiss",
        external_user_id="453920478",
        external_chat_id="-5267117449",
    )
    extra_contact = TelegramContact(
        id=str(uuid.uuid4()),
        company_id=company.id,
        client_id=duplicate_client.id,
        external_user_id="453920478",
        external_chat_id="-5267117450",
        external_username="belfiss-archive",
        external_display_name="Ivan TG Archive",
    )
    session.add(extra_contact)
    session.commit()

    plan = merge_telegram_contact_duplicate.build_plan(session, "453920478", "453920478", duplicate_contact.external_chat_id)

    assert plan.duplicate_client_can_delete is False


def test_merge_duplicate_plan_keeps_delete_allowed_when_only_supported_refs_will_be_relinked():
    session = _session()
    company = _company(session)
    _seed_user(
        session,
        company=company,
        username="belfiss",
        external_user_id="453920478",
        external_chat_id="453920478",
    )
    duplicate_client, _, duplicate_contact = _seed_user(
        session,
        company=company,
        username="belfiss",
        external_user_id="453920478",
        external_chat_id="-5267117449",
    )
    extra_pending = PendingTaskIntent(
        id=str(uuid.uuid4()),
        company_id=company.id,
        client_id=duplicate_client.id,
        external_chat_id="-5267117450",
        subject="Another pending task",
        created_at=datetime.utcnow(),
        expires_at=datetime.utcnow() + timedelta(hours=1),
    )
    session.add(extra_pending)
    session.commit()

    plan = merge_telegram_contact_duplicate.build_plan(session, "453920478", "453920478", duplicate_contact.external_chat_id)

    assert plan.duplicate_client_can_delete is True


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
