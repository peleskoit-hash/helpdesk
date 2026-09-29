from __future__ import annotations

from datetime import datetime
from types import SimpleNamespace
import uuid

from sqlalchemy import create_engine, event, select
from sqlalchemy.orm import sessionmaker

from api.app.db import Base
from api.app.models import (
    Client,
    ClientCardBlockConfig,
    ClientCardFieldConfig,
    ClientCardRuleConfig,
    ClientCardSourceConfig,
    ClientProfile,
    Company,
    InternalTaskAssignmentMode,
    InternalTaskDefinition,
    InternalTaskDefinitionState,
    InternalTaskOccurrence,
    InternalTaskScheduleKind,
    MessageType,
    TelegramContact,
    Ticket,
    TicketMessage,
    TicketStatus,
    TicketWorkflowType,
    User,
)
from api.app.routers import telegram, tickets
from api.app.schemas import ClientCardSourceRunRequest, ClientProfileUpdate
from api.app.services import call_center_employee_resolver, client_card_sources
from api.app.services.call_center_employee_resolver import CallCenterEmployee, ResolveResult
from api.app.services.ticket_list import _display_author_enabled, list_special_tickets, list_tickets_paginated, requester_display_name
from api.app.services.ticket_call_center_snapshot import (
    CALL_CENTER_EMPLOYEE_SOURCE_KEY,
    apply_call_center_employee_snapshot_on_ticket_create,
)


def _session():
    engine = create_engine("sqlite:///:memory:", future=True)
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine, autoflush=False, autocommit=False)
    return Session()


def _admin():
    return SimpleNamespace(id="admin-1", role="admin")


def _seed_case(
    session,
    *,
    department="\u041a\u043e\u043b\u043b \u0426\u0435\u043d\u0442\u0440",
    destination=None,
    internal_number="344",
):
    now = datetime.utcnow()
    destination = department if destination is None else destination
    company = Company(id=str(uuid.uuid4()), name="Company")
    admin = User(id="admin-1", company_id=company.id, full_name="Admin", role="admin")
    client = Client(id=str(uuid.uuid4()), company_id=company.id, display_name="Client")
    profile = ClientProfile(
        id=str(uuid.uuid4()),
        client_id=client.id,
        contact_name="Old",
        last_name="Client",
        destination=destination,
        department=department,
        internal_number=internal_number,
        custom_fields={},
        created_at=now,
        updated_at=now,
    )
    ticket = Ticket(
        id=str(uuid.uuid4()),
        company_id=company.id,
        client_id=client.id,
        subject="Ticket",
        status=TicketStatus.NEW.value,
        workflow_type=TicketWorkflowType.TICKET.value,
        assignee_id=admin.id,
        agent_visible=True,
        created_at=now,
        updated_at=now,
    )
    session.add_all([company, admin, client, profile, ticket])
    session.commit()
    return client, profile, ticket


def _seed_client_only(
    session,
    *,
    department="\u041a\u043e\u043b\u043b \u0426\u0435\u043d\u0442\u0440",
    destination=None,
    internal_number="344",
):
    now = datetime.utcnow()
    destination = department if destination is None else destination
    company = Company(id=str(uuid.uuid4()), name="Company")
    client = Client(id=str(uuid.uuid4()), company_id=company.id, display_name="Client")
    profile = ClientProfile(
        id=str(uuid.uuid4()),
        client_id=client.id,
        contact_name="Old",
        last_name="Client",
        destination=destination,
        department=department,
        internal_number=internal_number,
        custom_fields={},
        created_at=now,
        updated_at=now,
    )
    session.add_all([company, client, profile])
    session.commit()
    return company, client, profile


def _save_rule(name, actions) -> ClientCardRuleConfig:
    now = datetime.utcnow()
    return ClientCardRuleConfig(
        id=str(uuid.uuid4()),
        name=name,
        is_active=True,
        is_system=False,
        sort_order=10,
        condition_field_key="",
        condition_operator="always",
        condition_value=None,
        actions_json=actions,
        created_at=now,
        updated_at=now,
    )


def _custom_fio_field() -> list:
    now = datetime.utcnow()
    block = ClientCardBlockConfig(
        id=str(uuid.uuid4()),
        key="custom_block_fio",
        title="Custom",
        color_scheme="gray",
        sort_order=100,
        is_active=True,
        created_at=now,
        updated_at=now,
    )
    field = ClientCardFieldConfig(
        id=str(uuid.uuid4()),
        key="custom_fio",
        label="FIO",
        field_type="text",
        block_key=block.key,
        sort_order=10,
        is_visible=True,
        is_visible_in_editor=True,
        is_editable=True,
        is_required=False,
        is_system=False,
        options_json=None,
        created_at=now,
        updated_at=now,
    )
    return [block, field]


def _custom_text_field(*, key: str, label: str) -> list:
    now = datetime.utcnow()
    block = ClientCardBlockConfig(
        id=str(uuid.uuid4()),
        key=f"custom_block_{key}",
        title="Custom",
        color_scheme="gray",
        sort_order=110,
        is_active=True,
        created_at=now,
        updated_at=now,
    )
    field = ClientCardFieldConfig(
        id=str(uuid.uuid4()),
        key=key,
        label=label,
        field_type="text",
        block_key=block.key,
        sort_order=10,
        is_visible=True,
        is_visible_in_editor=True,
        is_editable=True,
        is_required=False,
        is_system=False,
        options_json=None,
        created_at=now,
        updated_at=now,
    )
    return [block, field]


def _source(**overrides) -> ClientCardSourceConfig:
    now = datetime.utcnow()
    data = {
        "id": str(uuid.uuid4()),
        "technical_key": CALL_CENTER_EMPLOYEE_SOURCE_KEY,
        "name": "Call center employee lookup",
        "kind": "http",
        "is_active": True,
        "method": "GET",
        "url_template": "https://api.example.com/users/{{internal_number}}",
        "query_template_json": None,
        "headers_template_json": None,
        "body_template_json": None,
        "timeout_ms": 3000,
        "mapping_json": [
            {"response_path": "data.first_name", "target_key": "contact_name"},
            {"response_path": "data.last_name", "target_key": "last_name"},
            {"response_path": "data.id", "target_key": "bitrix_id"},
            {"response_path": "data.login", "target_key": "bitrix_login"},
        ],
        "on_error": "ignore",
        "created_at": now,
        "updated_at": now,
    }
    data.update(overrides)
    return ClientCardSourceConfig(**data)


def _employee_result(
    *,
    internal_number: str = "344",
    first_name: str = "Maria",
    last_name: str = "Ivanova",
    bitrix_id: str = "101",
    bitrix_login: str | None = "m.ivanova",
) -> ResolveResult:
    return ResolveResult(
        status=call_center_employee_resolver.STATUS_FOUND,
        internal_number=internal_number,
        employee=CallCenterEmployee(
            internal_number=internal_number,
            bitrix_id=bitrix_id,
            first_name=first_name,
            last_name=last_name,
            full_name=f"{last_name} {first_name}".strip(),
            department_ids=[60],
            bitrix_login=bitrix_login,
        ),
    )


def _empty_result(internal_number: str = "344") -> ResolveResult:
    return ResolveResult(
        status=call_center_employee_resolver.STATUS_TECHNICAL_ERROR,
        internal_number=internal_number,
    )


def _agent_message(session, ticket):
    session.add(
        TicketMessage(
            id=str(uuid.uuid4()),
            ticket_id=ticket.id,
            message_type=MessageType.AGENT.value,
            body="Done",
            author_user_id="admin-1",
            agent_visible=True,
            created_at=datetime.utcnow(),
        )
    )
    session.commit()


def _telegram_contact(client_id: str, *, company_id: str = "company-1", display_name: str | None = "Telegram Name", username: str | None = "telegram_user"):
    now = datetime.utcnow()
    return TelegramContact(
        id=str(uuid.uuid4()),
        company_id=company_id,
        client_id=client_id,
        external_user_id=str(uuid.uuid4()),
        external_chat_id=str(uuid.uuid4()),
        external_username=username,
        external_display_name=display_name,
        created_at=now,
        updated_at=now,
    )


def test_call_center_internal_number_lookup_runs_once_on_ticket_create(monkeypatch):
    session = _session()
    _, profile, ticket = _seed_case(session)
    session.add(_source())
    session.commit()
    calls = []

    def fake_resolve(db, internal_number, final_wait_seconds=None):
        calls.append(internal_number)
        return _employee_result(internal_number=internal_number)

    monkeypatch.setattr(call_center_employee_resolver, "resolve", fake_resolve)

    assert apply_call_center_employee_snapshot_on_ticket_create(session, ticket) is True
    assert apply_call_center_employee_snapshot_on_ticket_create(session, ticket) is False

    assert calls == ["344"]
    assert ticket.call_center_internal_number == "344"
    assert ticket.call_center_employee_first_name == "Maria"
    assert ticket.call_center_employee_last_name == "Ivanova"
    assert ticket.call_center_employee_id == "101"
    assert ticket.call_center_employee_bitrix_login == "m.ivanova"
    assert profile.contact_name == "Maria"
    assert profile.last_name == "Ivanova"
    assert profile.bitrix_id == "101"
    assert profile.bitrix_login == "m.ivanova"


def test_call_center_lookup_values_are_available_to_template_rule_engine(monkeypatch):
    session = _session()
    _, profile, ticket = _seed_case(session)
    session.add(_source())
    session.add_all(_custom_fio_field())
    session.add(
        _save_rule(
            "Full name",
            [
                {
                    "target_type": "field",
                    "target_key": "custom_fio",
                    "action": "set_value",
                    "scope": "save",
                    "value_type": "template",
                    "value": "{{last_name}} {{contact_name}}",
                },
            ],
        )
    )
    session.commit()

    monkeypatch.setattr(
        call_center_employee_resolver,
        "resolve",
        lambda db, internal_number, final_wait_seconds=None: _employee_result(internal_number=internal_number),
    )

    assert apply_call_center_employee_snapshot_on_ticket_create(session, ticket) is True

    assert profile.custom_fields == {"custom_fio": "Ivanova Maria"}
    assert ticket.call_center_profile_snapshot_json["profile_delta"] == {
        "contact_name": "Maria",
        "last_name": "Ivanova",
        "bitrix_id": "101",
        "external_user_id": "101",
        "bitrix_login": "m.ivanova",
        "internal_number": "344",
        "custom_fields": {"custom_fio": "Ivanova Maria"},
    }


def test_incoming_ticket_creation_pipeline_runs_call_center_lookup_once(monkeypatch):
    session = _session()
    company, client, _ = _seed_client_only(session)
    session.add(_source())
    session.commit()
    calls = []

    monkeypatch.setattr(telegram, "choose_online_agent", lambda db, company_id: None)

    def fake_resolve(db, internal_number, final_wait_seconds=None):
        calls.append(internal_number)
        return _employee_result(internal_number=internal_number)

    monkeypatch.setattr(call_center_employee_resolver, "resolve", fake_resolve)

    ticket = telegram._ensure_ticket_for_incoming(session, company.id, client.id, "Need help")
    same_ticket = telegram._ensure_ticket_for_incoming(session, company.id, client.id, "Need help again")

    assert same_ticket.id == ticket.id
    assert calls == ["344"]
    assert ticket.call_center_employee_first_name == "Maria"
    assert ticket.call_center_employee_last_name == "Ivanova"
    assert ticket.call_center_employee_id == "101"
    assert ticket.call_center_employee_bitrix_login == "m.ivanova"


def test_non_call_center_or_empty_internal_number_does_not_run_lookup(monkeypatch):
    for department, destination, internal_number in [
        ("Support", "Support", "344"),
        ("", "\u041e\u0442\u0434\u0435\u043b \u041f\u0440\u043e\u0434\u0430\u0436", "344"),
        ("\u041a\u043e\u043b\u043b \u0426\u0435\u043d\u0442\u0440", "\u041e\u0442\u0434\u0435\u043b \u041f\u0440\u043e\u0434\u0430\u0436", "344"),
        ("\u041a\u043e\u043b\u043b \u0426\u0435\u043d\u0442\u0440", "\u041a\u043e\u043b\u043b \u0426\u0435\u043d\u0442\u0440", ""),
    ]:
        session = _session()
        _, _, ticket = _seed_case(session, department=department, destination=destination, internal_number=internal_number)
        session.add(_source())
        session.commit()
        calls = []
        monkeypatch.setattr(call_center_employee_resolver, "resolve", lambda db, internal_number, final_wait_seconds=None: calls.append(internal_number))

        assert apply_call_center_employee_snapshot_on_ticket_create(session, ticket) is False
        assert calls == []
        assert ticket.call_center_employee_lookup_at is None
        assert ticket.call_center_employee_first_name is None


def test_exact_destination_call_center_runs_lookup_without_department_dependency(monkeypatch):
    session = _session()
    _, _, ticket = _seed_case(
        session,
        department="",
        destination="\u041a\u043e\u043b\u043b \u0426\u0435\u043d\u0442\u0440",
    )
    session.add(_source())
    session.commit()
    calls = []

    def fake_resolve(db, internal_number, final_wait_seconds=None):
        calls.append(internal_number)
        return _employee_result(internal_number=internal_number)

    monkeypatch.setattr(call_center_employee_resolver, "resolve", fake_resolve)

    assert apply_call_center_employee_snapshot_on_ticket_create(session, ticket) is True
    assert calls == ["344"]


def test_source_error_does_not_block_ticket_creation(monkeypatch):
    session = _session()
    _, _, ticket = _seed_case(session)
    session.add(_source())
    session.commit()
    calls = []

    def fake_resolve(db, internal_number, final_wait_seconds=None):
        calls.append(internal_number)
        return _empty_result(internal_number)

    monkeypatch.setattr(call_center_employee_resolver, "resolve", fake_resolve)

    assert apply_call_center_employee_snapshot_on_ticket_create(session, ticket) is False
    session.commit()
    assert session.get(Ticket, ticket.id) is not None
    assert calls == ["344"]
    assert ticket.call_center_employee_lookup_at is not None
    assert ticket.call_center_employee_first_name is None


def test_view_edit_and_close_do_not_run_call_center_lookup(monkeypatch):
    session = _session()
    _, _, ticket = _seed_case(session)
    session.add(_source())
    session.commit()

    monkeypatch.setattr(
        call_center_employee_resolver,
        "resolve",
        lambda db, internal_number, final_wait_seconds=None: _employee_result(internal_number=internal_number),
    )
    apply_call_center_employee_snapshot_on_ticket_create(session, ticket)
    session.commit()
    original = (
        ticket.call_center_employee_first_name,
        ticket.call_center_employee_last_name,
        ticket.call_center_employee_id,
        ticket.call_center_employee_bitrix_login,
    )

    calls = []
    monkeypatch.setattr(call_center_employee_resolver, "resolve", lambda db, internal_number, final_wait_seconds=None: calls.append(internal_number))
    monkeypatch.setattr(tickets, "_ticket_closed_client_text", lambda db, ticket: "Closed")

    card = tickets.get_ticket(ticket.id, db=session, user=_admin())
    assert card.call_center_employee_first_name == "Maria"

    tickets.update_client_profile(ticket.id, ClientProfileUpdate(contact_name="Edited"), db=session, user=_admin())
    _agent_message(session, ticket)
    tickets.close(ticket.id, db=session, user=_admin())
    session.refresh(ticket)

    assert calls == []
    assert ticket.status == TicketStatus.CLOSED.value
    assert (
        ticket.call_center_employee_first_name,
        ticket.call_center_employee_last_name,
        ticket.call_center_employee_id,
        ticket.call_center_employee_bitrix_login,
    ) == original


def test_manual_source_run_on_snapshot_ticket_keeps_historical_overlay(monkeypatch):
    session = _session()
    _, profile, ticket = _seed_case(session)
    source = _source()
    session.add(source)
    session.commit()
    response = {"first_name": "Maria", "last_name": "Ivanova", "id": 101, "login": "m.ivanova"}

    def fake_execute(_source, _context):
        return SimpleNamespace(http_status=200, payload={"data": dict(response)}, duration_ms=2)

    monkeypatch.setattr(client_card_sources, "execute_source", fake_execute)
    monkeypatch.setattr(
        call_center_employee_resolver,
        "resolve",
        lambda db, internal_number, final_wait_seconds=None: _employee_result(
            internal_number=internal_number,
            first_name=response["first_name"],
            last_name=response["last_name"],
            bitrix_id=str(response["id"]),
            bitrix_login=response["login"],
        ),
    )
    apply_call_center_employee_snapshot_on_ticket_create(session, ticket)
    session.commit()

    response.update({"first_name": "Anna", "last_name": "Petrova", "id": 202, "login": "a.petrova"})
    returned_profile = tickets.run_client_profile_source(
        ticket.id,
        ClientCardSourceRunRequest(source_id=source.id),
        db=session,
        user=_admin(),
    )
    session.refresh(profile)
    session.refresh(ticket)

    assert profile.contact_name == "Anna"
    assert profile.last_name == "Petrova"
    assert profile.bitrix_id == "202"
    assert profile.bitrix_login == "a.petrova"
    assert ticket.call_center_employee_first_name == "Maria"
    assert ticket.call_center_employee_last_name == "Ivanova"
    assert ticket.call_center_employee_id == "101"
    assert returned_profile.contact_name == "Anna"
    assert returned_profile.last_name == "Petrova"
    assert returned_profile.bitrix_id == "202"
    assert returned_profile.bitrix_login == "a.petrova"


def test_changed_source_response_affects_only_new_tickets(monkeypatch):
    session = _session()
    client, profile, old_ticket = _seed_case(session)
    session.add(_source())
    session.add_all(_custom_fio_field())
    session.add_all(_custom_text_field(key="favorite_color", label="Favorite color"))
    session.add(
        _save_rule(
            "Full name",
            [
                {
                    "target_type": "field",
                    "target_key": "custom_fio",
                    "action": "set_value",
                    "scope": "save",
                    "value_type": "template",
                    "value": "{{last_name}} {{contact_name}}",
                },
            ],
        )
    )
    session.commit()
    response = {"first_name": "Maria", "last_name": "Ivanova", "id": 101, "login": "m.ivanova"}

    def fake_execute(source, context):
        return SimpleNamespace(http_status=200, payload={"data": dict(response)}, duration_ms=2)

    monkeypatch.setattr(client_card_sources, "execute_source", fake_execute)
    monkeypatch.setattr(
        call_center_employee_resolver,
        "resolve",
        lambda db, internal_number, final_wait_seconds=None: _employee_result(
            internal_number=internal_number,
            first_name=response["first_name"],
            last_name=response["last_name"],
            bitrix_id=str(response["id"]),
            bitrix_login=response["login"],
        ),
    )
    apply_call_center_employee_snapshot_on_ticket_create(session, old_ticket)
    session.commit()

    response.update({"first_name": "Anna", "last_name": "Petrova", "id": 202, "login": "a.petrova"})
    new_ticket = Ticket(
        id=str(uuid.uuid4()),
        company_id=old_ticket.company_id,
        client_id=client.id,
        subject="New ticket",
        status=TicketStatus.NEW.value,
        workflow_type=TicketWorkflowType.TICKET.value,
        created_at=datetime.utcnow(),
        updated_at=datetime.utcnow(),
    )
    session.add(new_ticket)
    session.flush()
    apply_call_center_employee_snapshot_on_ticket_create(session, new_ticket)
    session.commit()
    session.refresh(old_ticket)
    session.refresh(new_ticket)
    session.refresh(profile)

    assert profile.internal_number == "344"
    assert profile.contact_name == "Anna"
    assert profile.last_name == "Petrova"
    assert profile.bitrix_id == "202"
    assert profile.bitrix_login == "a.petrova"
    assert profile.custom_fields == {"custom_fio": "Petrova Anna"}
    assert old_ticket.call_center_employee_first_name == "Maria"
    assert old_ticket.call_center_employee_last_name == "Ivanova"
    assert old_ticket.call_center_employee_id == "101"
    assert old_ticket.call_center_profile_snapshot_json["profile_delta"] == {
        "contact_name": "Maria",
        "last_name": "Ivanova",
        "bitrix_id": "101",
        "external_user_id": "101",
        "bitrix_login": "m.ivanova",
        "internal_number": "344",
        "custom_fields": {"custom_fio": "Ivanova Maria"},
    }
    assert old_ticket.call_center_profile_snapshot_json["profile_delta"]["custom_fields"] == {"custom_fio": "Ivanova Maria"}
    assert new_ticket.call_center_employee_first_name == "Anna"
    assert new_ticket.call_center_employee_last_name == "Petrova"
    assert new_ticket.call_center_employee_id == "202"
    assert new_ticket.call_center_profile_snapshot_json["profile_delta"] == {
        "contact_name": "Anna",
        "last_name": "Petrova",
        "bitrix_id": "202",
        "external_user_id": "202",
        "bitrix_login": "a.petrova",
        "internal_number": "344",
        "custom_fields": {"custom_fio": "Petrova Anna"},
    }
    assert new_ticket.call_center_profile_snapshot_json["profile_delta"]["custom_fields"] == {"custom_fio": "Petrova Anna"}
    old_card = tickets.get_ticket(old_ticket.id, db=session, user=_admin())
    new_card = tickets.get_ticket(new_ticket.id, db=session, user=_admin())
    assert old_card.client_profile.contact_name == "Maria"
    assert old_card.client_profile.last_name == "Ivanova"
    assert old_card.client_profile.bitrix_id == "101"
    assert old_card.client_profile.bitrix_login == "m.ivanova"
    assert old_card.client_profile.custom_fields["custom_fio"] == "Ivanova Maria"
    assert new_card.client_profile.contact_name == "Anna"
    assert new_card.client_profile.last_name == "Petrova"
    assert new_card.client_profile.bitrix_id == "202"
    assert new_card.client_profile.bitrix_login == "a.petrova"
    assert new_card.client_profile.custom_fields["custom_fio"] == "Petrova Anna"
    assert "favorite_color" not in old_ticket.call_center_profile_snapshot_json.get("profile_delta", {}).get("custom_fields", {})
    assert "favorite_color" not in new_ticket.call_center_profile_snapshot_json.get("profile_delta", {}).get("custom_fields", {})

    calls = []
    monkeypatch.setattr(client_card_sources, "execute_source", lambda source, context: calls.append(source.id))
    patched_old_profile = tickets.update_client_profile(
        old_ticket.id,
        ClientProfileUpdate(phone="555", custom_fields={"favorite_color": "Blue"}),
        db=session,
        user=_admin(),
    )
    session.refresh(profile)

    assert calls == []
    assert profile.contact_name == "Anna"
    assert profile.last_name == "Petrova"
    assert profile.bitrix_id == "202"
    assert profile.bitrix_login == "a.petrova"
    assert profile.custom_fields == {"custom_fio": "Petrova Anna", "favorite_color": "Blue"}
    assert profile.phone == "555"
    assert patched_old_profile.contact_name == "Anna"
    assert patched_old_profile.last_name == "Petrova"
    assert patched_old_profile.custom_fields["custom_fio"] == "Petrova Anna"
    assert patched_old_profile.custom_fields["favorite_color"] == "Blue"

    full_payload_old_profile = tickets.update_client_profile(
        old_ticket.id,
        ClientProfileUpdate(
            contact_name="Maria",
            last_name="Ivanova",
            bitrix_id="101",
            bitrix_login="m.ivanova",
            phone="777",
            custom_fields={"custom_fio": "Ivanova Maria", "favorite_color": "Green"},
        ),
        db=session,
        user=_admin(),
    )
    session.refresh(profile)

    assert profile.contact_name == "Maria"
    assert profile.last_name == "Ivanova"
    assert profile.bitrix_id == "101"
    assert profile.bitrix_login == "m.ivanova"
    assert profile.phone == "777"
    assert profile.custom_fields == {"custom_fio": "Ivanova Maria", "favorite_color": "Green"}
    assert full_payload_old_profile.contact_name == "Maria"
    assert full_payload_old_profile.last_name == "Ivanova"
    assert full_payload_old_profile.bitrix_id == "101"
    assert full_payload_old_profile.bitrix_login == "m.ivanova"
    assert full_payload_old_profile.custom_fields["custom_fio"] == "Ivanova Maria"
    assert full_payload_old_profile.custom_fields["favorite_color"] == "Green"

    monkeypatch.setattr(tickets, "_ticket_closed_client_text", lambda db, ticket: "Closed")
    _agent_message(session, old_ticket)
    tickets.close(old_ticket.id, db=session, user=_admin())
    reopened_old_card = tickets.get_ticket(old_ticket.id, db=session, user=_admin())
    session.refresh(profile)

    assert calls == []
    assert reopened_old_card.client_profile.contact_name == "Maria"
    assert reopened_old_card.client_profile.last_name == "Ivanova"
    assert reopened_old_card.client_profile.custom_fields["custom_fio"] == "Ivanova Maria"
    assert reopened_old_card.client_profile.custom_fields["favorite_color"] == "Green"
    assert profile.contact_name == "Maria"
    assert profile.last_name == "Ivanova"
    assert profile.custom_fields == {"custom_fio": "Ivanova Maria", "favorite_color": "Green"}


def test_existing_tickets_are_not_migrated_or_changed(monkeypatch):
    session = _session()
    client, _, existing_ticket = _seed_case(session)
    session.add(_source())
    session.commit()
    monkeypatch.setattr(
        call_center_employee_resolver,
        "resolve",
        lambda db, internal_number, final_wait_seconds=None: _employee_result(
            internal_number=internal_number,
            first_name="Anna",
            last_name="Petrova",
            bitrix_id="202",
            bitrix_login="a.petrova",
        ),
    )

    new_ticket = Ticket(
        id=str(uuid.uuid4()),
        company_id=existing_ticket.company_id,
        client_id=client.id,
        subject="New ticket",
        status=TicketStatus.NEW.value,
        workflow_type=TicketWorkflowType.TICKET.value,
        created_at=datetime.utcnow(),
        updated_at=datetime.utcnow(),
    )
    session.add(new_ticket)
    session.flush()
    apply_call_center_employee_snapshot_on_ticket_create(session, new_ticket)
    session.commit()
    session.refresh(existing_ticket)

    assert existing_ticket.call_center_employee_lookup_at is None
    assert existing_ticket.call_center_employee_first_name is None
    assert new_ticket.call_center_employee_first_name == "Anna"


def test_empty_or_incorrect_response_fields_do_not_wipe_saved_snapshot(monkeypatch):
    session = _session()
    _, _, ticket = _seed_case(session)
    session.add(_source())
    session.commit()
    ticket.call_center_internal_number = "344"
    ticket.call_center_employee_first_name = "Maria"
    ticket.call_center_employee_last_name = "Ivanova"
    ticket.call_center_employee_id = "101"
    ticket.call_center_employee_bitrix_login = "m.ivanova"
    session.commit()
    calls = []

    def fake_resolve(db, internal_number, final_wait_seconds=None):
        calls.append(internal_number)
        return _employee_result(internal_number=internal_number, first_name="", last_name="", bitrix_id="", bitrix_login=None)

    monkeypatch.setattr(call_center_employee_resolver, "resolve", fake_resolve)

    assert apply_call_center_employee_snapshot_on_ticket_create(session, ticket) is False
    assert calls == []
    assert ticket.call_center_employee_first_name == "Maria"
    assert ticket.call_center_employee_last_name == "Ivanova"
    assert ticket.call_center_employee_id == "101"
    assert ticket.call_center_employee_bitrix_login == "m.ivanova"


def test_ticket_list_uses_ticket_specific_snapshot_fio_for_each_row():
    session = _session()
    client, profile, old_ticket = _seed_case(session)
    session.add(_telegram_contact(client.id, company_id=old_ticket.company_id, display_name="Telegram Мария", username="tg_maria"))
    new_ticket = Ticket(
        id=str(uuid.uuid4()),
        company_id=old_ticket.company_id,
        client_id=client.id,
        subject="New ticket",
        status=TicketStatus.NEW.value,
        workflow_type=TicketWorkflowType.TICKET.value,
        created_at=datetime.utcnow(),
        updated_at=datetime.utcnow(),
    )
    session.add(new_ticket)
    session.flush()

    old_ticket.call_center_profile_snapshot_json = {"custom_fields": {"custom_fio": "Иванова Мария"}}
    new_ticket.call_center_profile_snapshot_json = {"custom_fields": {"custom_fio": "Петрова Анна"}}
    profile.contact_name = "Анна"
    profile.last_name = "Петрова"
    profile.custom_fields = {"custom_fio": "Петрова Анна"}
    client.display_name = "Мария"
    session.commit()

    payload = list_tickets_paginated(
        db=session,
        user=_admin(),
        statuses=[TicketStatus.NEW.value],
        is_frozen=False,
        sort="created_at",
        order="asc",
        page=1,
        limit=20,
        filter_admin_assignees=False,
    )
    by_id = {item.id: item for item in payload.items}

    assert by_id[old_ticket.id].requester_display_name == "Иванова Мария"
    assert by_id[new_ticket.id].requester_display_name == "Петрова Анна"
    assert by_id[old_ticket.id].requester_display_name != by_id[old_ticket.id].client_name
    assert by_id[new_ticket.id].requester_display_name != "Анна"


def _internal_definition(session, company_id: str, creator_id: str) -> InternalTaskDefinition:
    now = datetime.utcnow()
    definition = InternalTaskDefinition(
        id=str(uuid.uuid4()),
        company_id=company_id,
        title="Internal",
        description="Internal description",
        creator_admin_id=creator_id,
        responsible_admin_id=creator_id,
        display_author=True,
        assignment_mode=InternalTaskAssignmentMode.DISTRIBUTION.value,
        assignee_user_id=None,
        notify_on_completion=False,
        deadline_at_utc=None,
        schedule_kind=InternalTaskScheduleKind.IMMEDIATE.value,
        timezone_name="Europe/Moscow",
        state=InternalTaskDefinitionState.FINISHED.value,
        created_at=now,
        updated_at=now,
    )
    session.add(definition)
    return definition


def _internal_occurrence(
    session,
    definition: InternalTaskDefinition,
    ticket: Ticket,
    *,
    display_author: bool,
    occurrence_at_utc: datetime | None = None,
) -> None:
    now = datetime.utcnow()
    occurrence_at_utc = occurrence_at_utc or now
    session.add(
        InternalTaskOccurrence(
            id=str(uuid.uuid4()),
            definition_id=definition.id,
            occurrence_at_utc=occurrence_at_utc,
            ticket_id=ticket.id,
            title_snapshot=ticket.subject,
            description_snapshot="Internal description",
            creator_admin_id=definition.creator_admin_id,
            responsible_admin_id=definition.responsible_admin_id,
            display_author=display_author,
            effective_assignment_mode=InternalTaskAssignmentMode.DISTRIBUTION.value,
            effective_assignee_user_id=None,
            notify_on_completion=False,
            deadline_at_utc=None,
            materialized_at_utc=now,
            created_at=now,
        )
    )


def test_ticket_list_batches_internal_task_requester_lookup_without_per_row_queries():
    session = _session()
    client, _profile, ordinary_ticket = _seed_case(session)
    admin = session.get(User, "admin-1")
    definition = _internal_definition(session, ordinary_ticket.company_id, admin.id)
    internal_tickets = []
    for index, display_author in enumerate([True, False]):
        ticket = Ticket(
            id=str(uuid.uuid4()),
            company_id=ordinary_ticket.company_id,
            client_id=client.id,
            subject=f"Internal {index}",
            status=TicketStatus.NEW.value,
            workflow_type=TicketWorkflowType.TASK.value,
            created_at=datetime.utcnow(),
            updated_at=datetime.utcnow(),
        )
        session.add(ticket)
        internal_tickets.append(ticket)
        _internal_occurrence(
            session,
            definition,
            ticket,
            display_author=display_author,
            occurrence_at_utc=datetime(2026, 9, 16, 10, index),
        )
    session.commit()

    occurrence_queries = []

    def before_cursor_execute(_conn, _cursor, statement, _parameters, _context, _executemany):
        if "internal_task_occurrences" in statement.lower():
            occurrence_queries.append(statement)

    engine = session.get_bind()
    event.listen(engine, "before_cursor_execute", before_cursor_execute)
    try:
        payload = list_tickets_paginated(
            db=session,
            user=_admin(),
            statuses=[TicketStatus.NEW.value],
            is_frozen=False,
            sort="created_at",
            order="asc",
            page=1,
            limit=20,
            filter_admin_assignees=False,
        )
    finally:
        event.remove(engine, "before_cursor_execute", before_cursor_execute)

    by_subject = {item.subject: item for item in payload.items}
    assert by_subject["Internal 0"].requester_display_name == "Admin"
    assert by_subject["Internal 1"].requester_display_name == "Система"
    assert by_subject[ordinary_ticket.subject].requester_display_name == "Client"
    assert len(occurrence_queries) == 1


def test_special_ticket_list_batches_internal_task_requester_lookup_without_per_row_queries():
    session = _session()
    client, _profile, ordinary_ticket = _seed_case(session)
    admin = session.get(User, "admin-1")
    definition = _internal_definition(session, ordinary_ticket.company_id, admin.id)
    ordinary_ticket.is_special = True
    for index in range(2):
        ticket = Ticket(
            id=str(uuid.uuid4()),
            company_id=ordinary_ticket.company_id,
            client_id=client.id,
            subject=f"Special internal {index}",
            status=TicketStatus.NEW.value,
            workflow_type=TicketWorkflowType.TASK.value,
            is_special=True,
            created_at=datetime.utcnow(),
            updated_at=datetime.utcnow(),
        )
        session.add(ticket)
        _internal_occurrence(
            session,
            definition,
            ticket,
            display_author=index == 0,
            occurrence_at_utc=datetime(2026, 9, 16, 10, index),
        )
    session.commit()

    occurrence_queries = []

    def before_cursor_execute(_conn, _cursor, statement, _parameters, _context, _executemany):
        if "internal_task_occurrences" in statement.lower():
            occurrence_queries.append(statement)

    engine = session.get_bind()
    event.listen(engine, "before_cursor_execute", before_cursor_execute)
    try:
        payload = list_special_tickets(
            db=session,
            user=_admin(),
            sort="created_at",
            order="asc",
            page=1,
            limit=20,
            query=None,
            ensure_ticket_number_fn=lambda _db, ticket: ticket.ticket_number,
        )
    finally:
        event.remove(engine, "before_cursor_execute", before_cursor_execute)

    by_subject = {item.subject: item for item in payload.items}
    assert by_subject["Special internal 0"].requester_display_name == "Admin"
    assert by_subject["Special internal 1"].requester_display_name == "Система"
    assert len(occurrence_queries) == 1


def test_internal_task_display_author_false_like_values_hide_author():
    for value in (False, None, 0, "", "0", "false", "False", "no", "off"):
        assert _display_author_enabled(value) is False

    for value in (True, "1", "true", "True", "yes", "on"):
        assert _display_author_enabled(value) is True


def test_ticket_list_requester_display_name_keeps_old_snapshot_after_profile_change_and_close():
    session = _session()
    client, profile, old_ticket = _seed_case(session)
    session.add(_telegram_contact(client.id, company_id=old_ticket.company_id, display_name="Telegram Мария"))
    old_ticket.call_center_profile_snapshot_json = {"custom_fields": {"custom_fio": "Иванова Мария"}}
    profile.contact_name = "Анна"
    profile.last_name = "Петрова"
    profile.custom_fields = {"custom_fio": "Петрова Анна"}
    client.display_name = "Мария"
    old_ticket.status = TicketStatus.CLOSED.value
    old_ticket.closed_at = datetime.utcnow()
    session.commit()

    payload = list_tickets_paginated(
        db=session,
        user=_admin(),
        statuses=[TicketStatus.CLOSED.value],
        is_frozen=None,
        sort="updated_at",
        order="desc",
        page=1,
        limit=20,
        filter_admin_assignees=False,
    )

    assert payload.items[0].requester_display_name == "Иванова Мария"


def test_ticket_list_requester_display_name_fallback_prefers_profile_fio_then_telegram_name():
    session = _session()
    client, profile, ticket = _seed_case(session)
    session.add(_telegram_contact(client.id, company_id=ticket.company_id, display_name="Telegram Анна", username="anna_user"))
    profile.contact_name = "Мария"
    profile.custom_fields = {"custom_fio": "Петрова Анна"}
    session.commit()

    payload = list_tickets_paginated(
        db=session,
        user=_admin(),
        statuses=[TicketStatus.NEW.value],
        is_frozen=False,
        sort="created_at",
        order="asc",
        page=1,
        limit=20,
        filter_admin_assignees=False,
    )
    assert payload.items[0].requester_display_name == "Петрова Анна"

    ticket.call_center_profile_snapshot_json = {"custom_fields": {"custom_fio": "   "}}
    profile.custom_fields = {"custom_fio": ""}
    session.commit()

    payload = list_tickets_paginated(
        db=session,
        user=_admin(),
        statuses=[TicketStatus.NEW.value],
        is_frozen=False,
        sort="created_at",
        order="asc",
        page=1,
        limit=20,
        filter_admin_assignees=False,
    )
    assert payload.items[0].requester_display_name == "Telegram Анна"
    assert payload.items[0].requester_display_name != "Мария"

def test_resolver_runtime_error_does_not_abort_ticket_create(monkeypatch):
    session = _session()
    _, _, ticket = _seed_case(session)
    session.add(_source())
    session.commit()

    def fake_resolve(db, internal_number, final_wait_seconds=None):
        raise RuntimeError("boom")

    monkeypatch.setattr(call_center_employee_resolver, "resolve", fake_resolve)

    assert apply_call_center_employee_snapshot_on_ticket_create(session, ticket) is False
    assert ticket.call_center_internal_number == "344"
    assert ticket.call_center_profile_snapshot_json["resolution_status"] == call_center_employee_resolver.STATUS_TECHNICAL_ERROR
    assert ticket.call_center_profile_snapshot_json["error_code"] == "RuntimeError"


def test_resolver_value_error_does_not_abort_ticket_create(monkeypatch):
    session = _session()
    _, _, ticket = _seed_case(session)
    session.add(_source())
    session.commit()

    monkeypatch.setattr(call_center_employee_resolver, "resolve", lambda *args, **kwargs: (_ for _ in ()).throw(ValueError("bad")))

    assert apply_call_center_employee_snapshot_on_ticket_create(session, ticket) is False
    assert ticket.call_center_profile_snapshot_json["resolution_status"] == call_center_employee_resolver.STATUS_TECHNICAL_ERROR
    assert ticket.call_center_profile_snapshot_json["error_code"] == "ValueError"


def test_call_center_sequence_a_empty_b_preserves_historical_snapshots(monkeypatch):
    session = _session()
    client, profile, ticket_a = _seed_case(session, internal_number="717")
    session.add_all(_custom_fio_field())
    session.add(
        _save_rule(
            "Full name",
            [
                {
                    "target_type": "field",
                    "target_key": "custom_fio",
                    "action": "set_value",
                    "scope": "save",
                    "value_type": "template",
                    "value": "{{last_name}} {{contact_name}}",
                },
            ],
        )
    )
    session.commit()

    results = {
        "717": _employee_result(internal_number="717", first_name="Татьяна", last_name="Левшина", bitrix_id="71701", bitrix_login="t.levshina"),
        "000": ResolveResult(status=call_center_employee_resolver.STATUS_NOT_FOUND, internal_number="000"),
        "726": _employee_result(internal_number="726", first_name="Сабрина", last_name="Минигареева", bitrix_id="72601", bitrix_login="s.minigareeva"),
    }

    monkeypatch.setattr(call_center_employee_resolver, "resolve", lambda db, internal_number, final_wait_seconds=None: results[internal_number])

    assert apply_call_center_employee_snapshot_on_ticket_create(session, ticket_a) is True
    session.commit()

    profile.internal_number = "000"
    ticket_empty = Ticket(
        id=str(uuid.uuid4()),
        company_id=ticket_a.company_id,
        client_id=client.id,
        subject="Empty ticket",
        status=TicketStatus.NEW.value,
        workflow_type=TicketWorkflowType.TICKET.value,
        created_at=datetime.utcnow(),
        updated_at=datetime.utcnow(),
    )
    session.add(ticket_empty)
    session.flush()
    assert apply_call_center_employee_snapshot_on_ticket_create(session, ticket_empty) is False
    session.commit()

    profile.internal_number = "726"
    ticket_b = Ticket(
        id=str(uuid.uuid4()),
        company_id=ticket_a.company_id,
        client_id=client.id,
        subject="Ticket B",
        status=TicketStatus.NEW.value,
        workflow_type=TicketWorkflowType.TICKET.value,
        created_at=datetime.utcnow(),
        updated_at=datetime.utcnow(),
    )
    session.add(ticket_b)
    session.flush()
    assert apply_call_center_employee_snapshot_on_ticket_create(session, ticket_b) is True
    session.commit()

    session.refresh(profile)
    session.refresh(ticket_a)
    session.refresh(ticket_empty)
    session.refresh(ticket_b)

    card_a = tickets.get_ticket(ticket_a.id, db=session, user=_admin())
    card_empty = tickets.get_ticket(ticket_empty.id, db=session, user=_admin())
    card_b = tickets.get_ticket(ticket_b.id, db=session, user=_admin())

    assert card_a.client_profile.contact_name == "Татьяна"
    assert card_a.client_profile.last_name == "Левшина"
    assert card_a.client_profile.bitrix_id == "71701"
    assert card_a.client_profile.bitrix_login == "t.levshina"
    assert card_a.client_profile.custom_fields["custom_fio"] == "Левшина Татьяна"
    assert card_a.client_profile.internal_number == "717"

    assert card_empty.client_profile.contact_name == ""
    assert card_empty.client_profile.last_name == ""
    assert card_empty.client_profile.bitrix_id == ""
    assert card_empty.client_profile.external_user_id == ""
    assert card_empty.client_profile.bitrix_login == ""
    assert card_empty.client_profile.custom_fields["custom_fio"] == ""
    assert card_empty.client_profile.internal_number == "000"
    assert ticket_empty.call_center_profile_snapshot_json["resolution_status"] == call_center_employee_resolver.STATUS_NOT_FOUND

    assert card_b.client_profile.contact_name == "Сабрина"
    assert card_b.client_profile.last_name == "Минигареева"
    assert card_b.client_profile.bitrix_id == "72601"
    assert card_b.client_profile.bitrix_login == "s.minigareeva"
    assert card_b.client_profile.custom_fields["custom_fio"] == "Минигареева Сабрина"
    assert card_b.client_profile.internal_number == "726"

    assert profile.contact_name == "Сабрина"
    assert profile.last_name == "Минигареева"
    assert profile.bitrix_id == "72601"
    assert profile.bitrix_login == "s.minigareeva"
    assert profile.internal_number == "726"
    assert profile.custom_fields["custom_fio"] == "Минигареева Сабрина"

    editable_empty_profile = tickets.get_current_client_profile(ticket_empty.id, db=session, user=_admin())
    assert editable_empty_profile.contact_name == "Сабрина"
    assert editable_empty_profile.last_name == "Минигареева"
    assert editable_empty_profile.bitrix_id == "72601"
    assert editable_empty_profile.bitrix_login == "s.minigareeva"
    assert editable_empty_profile.internal_number == "726"
    assert editable_empty_profile.custom_fields["custom_fio"] == "Минигареева Сабрина"

    updated_old_profile = tickets.update_client_profile(
        ticket_empty.id,
        ClientProfileUpdate(phone="777"),
        db=session,
        user=_admin(),
    )
    session.refresh(profile)
    session.refresh(ticket_empty)

    assert updated_old_profile.contact_name == "Сабрина"
    assert updated_old_profile.custom_fields["custom_fio"] == "Минигареева Сабрина"
    assert updated_old_profile.internal_number == "726"
    assert profile.phone == "777"
    assert ticket_empty.call_center_profile_snapshot_json["profile_delta"]["custom_fields"]["custom_fio"] == ""

    reopened_a = tickets.get_ticket(ticket_a.id, db=session, user=_admin())
    reopened_empty = tickets.get_ticket(ticket_empty.id, db=session, user=_admin())
    reopened_b = tickets.get_ticket(ticket_b.id, db=session, user=_admin())

    assert reopened_a.client_profile.custom_fields["custom_fio"] == "Левшина Татьяна"
    assert reopened_empty.client_profile.contact_name == ""
    assert reopened_empty.client_profile.bitrix_id == ""
    assert reopened_b.client_profile.custom_fields["custom_fio"] == "Минигареева Сабрина"

    payload = list_tickets_paginated(
        db=session,
        user=_admin(),
        statuses=[TicketStatus.NEW.value],
        is_frozen=False,
        sort="created_at",
        order="asc",
        page=1,
        limit=20,
        filter_admin_assignees=False,
    )
    by_id = {item.id: item for item in payload.items}
    assert by_id[ticket_a.id].requester_display_name == "Левшина Татьяна"
    assert by_id[ticket_empty.id].requester_display_name == "Client"
    assert by_id[ticket_b.id].requester_display_name == "Минигареева Сабрина"


def test_editable_current_profile_stays_separate_from_historical_ticket_snapshot(monkeypatch):
    session = _session()
    client, profile, ticket_a = _seed_case(session, internal_number="717")
    session.add_all(_custom_fio_field())
    session.add(
        _save_rule(
            "Full name",
            [
                {
                    "target_type": "field",
                    "target_key": "custom_fio",
                    "action": "set_value",
                    "scope": "save",
                    "value_type": "template",
                    "value": "{{last_name}} {{contact_name}}",
                },
            ],
        )
    )
    session.commit()

    results = {
        "717": _employee_result(internal_number="717", first_name="Татьяна", last_name="Левшина", bitrix_id="71701", bitrix_login="t.levshina"),
        "726": _employee_result(internal_number="726", first_name="Сабрина", last_name="Минигареева", bitrix_id="72601", bitrix_login="s.minigareeva"),
    }
    monkeypatch.setattr(call_center_employee_resolver, "resolve", lambda db, internal_number, final_wait_seconds=None: results[internal_number])

    assert apply_call_center_employee_snapshot_on_ticket_create(session, ticket_a) is True
    session.commit()

    editable_before = tickets.get_current_client_profile(ticket_a.id, db=session, user=_admin())
    assert editable_before.internal_number == "717"

    saved_profile = tickets.update_client_profile(
        ticket_a.id,
        ClientProfileUpdate(internal_number="726"),
        db=session,
        user=_admin(),
    )
    session.refresh(profile)

    assert profile.internal_number == "726"
    assert saved_profile.internal_number == "726"

    editable_after = tickets.get_current_client_profile(ticket_a.id, db=session, user=_admin())
    assert editable_after.internal_number == "726"

    card_a = tickets.get_ticket(ticket_a.id, db=session, user=_admin())
    assert card_a.client_profile.internal_number == "717"
    assert card_a.client_profile.contact_name == "Татьяна"
    assert card_a.client_profile.custom_fields["custom_fio"] == "Левшина Татьяна"

    ticket_b = Ticket(
        id=str(uuid.uuid4()),
        company_id=ticket_a.company_id,
        client_id=client.id,
        subject="Ticket B",
        status=TicketStatus.NEW.value,
        workflow_type=TicketWorkflowType.TICKET.value,
        created_at=datetime.utcnow(),
        updated_at=datetime.utcnow(),
    )
    session.add(ticket_b)
    session.flush()
    assert apply_call_center_employee_snapshot_on_ticket_create(session, ticket_b) is True
    session.commit()

    reopened_a = tickets.get_ticket(ticket_a.id, db=session, user=_admin())
    card_b = tickets.get_ticket(ticket_b.id, db=session, user=_admin())

    assert reopened_a.client_profile.internal_number == "717"
    assert reopened_a.client_profile.contact_name == "Татьяна"
    assert card_b.client_profile.internal_number == "726"
    assert card_b.client_profile.contact_name == "Сабрина"
    assert card_b.client_profile.custom_fields["custom_fio"] == "Минигареева Сабрина"


def test_explicit_empty_snapshot_values_override_non_empty_profile():
    session = _session()
    _, profile, ticket = _seed_case(session, internal_number="555")
    profile.contact_name = "Сабрина"
    profile.last_name = "Минигареева"
    profile.bitrix_id = "72601"
    profile.external_user_id = "72601"
    profile.bitrix_login = "s.minigareeva"
    profile.custom_fields = {"custom_fio": "Минигареева Сабрина", "favorite_color": "Blue"}
    ticket.call_center_employee_lookup_at = datetime.utcnow()
    ticket.call_center_profile_snapshot_json = {
        "resolution_status": call_center_employee_resolver.STATUS_NOT_FOUND,
        "profile_delta": {
            "contact_name": "",
            "last_name": "",
            "bitrix_id": "",
            "external_user_id": "",
            "bitrix_login": "",
            "internal_number": "555",
            "custom_fields": {"custom_fio": ""},
        },
    }
    session.commit()

    card = tickets.get_ticket(ticket.id, db=session, user=_admin())

    assert card.client_profile.contact_name == ""
    assert card.client_profile.last_name == ""
    assert card.client_profile.bitrix_id == ""
    assert card.client_profile.external_user_id == ""
    assert card.client_profile.bitrix_login == ""
    assert card.client_profile.custom_fields["custom_fio"] == ""
    assert card.client_profile.custom_fields["favorite_color"] == "Blue"
    assert card.client_profile.internal_number == "555"


def test_requester_display_name_for_call_center_ticket_does_not_use_profile_fio():
    session = _session()
    client, profile, ticket = _seed_case(session)
    session.add(_telegram_contact(client.id, company_id=ticket.company_id, display_name="Telegram Анна", username="anna_user"))
    profile.custom_fields = {"custom_fio": "Профильное ФИО"}
    ticket.call_center_employee_lookup_at = datetime.utcnow()
    ticket.call_center_profile_snapshot_json = {
        "resolution_status": call_center_employee_resolver.STATUS_NOT_FOUND,
        "profile_delta": {
            "contact_name": "",
            "last_name": "",
            "bitrix_id": "",
            "external_user_id": "",
            "bitrix_login": "",
            "internal_number": "344",
            "custom_fields": {"custom_fio": ""},
        },
    }
    session.commit()

    assert requester_display_name(session, ticket) == "Telegram Анна"


def test_requester_display_name_for_non_call_center_ticket_keeps_profile_fallback():
    session = _session()
    _, profile, ticket = _seed_case(session, department="Support", destination="Support")
    profile.custom_fields = {"custom_fio": "Профильное ФИО"}
    session.commit()

    assert requester_display_name(session, ticket) == "Профильное ФИО"


def test_technical_error_does_not_clear_last_working_profile(monkeypatch):
    session = _session()
    _, profile, ticket = _seed_case(session)
    session.add(_source())
    profile.contact_name = "Татьяна"
    profile.last_name = "Левшина"
    profile.bitrix_id = "71701"
    profile.external_user_id = "71701"
    profile.bitrix_login = "t.levshina"
    profile.bitrix_password = "keep-secret"
    profile.custom_fields = {"custom_fio": "Левшина Татьяна", "favorite_color": "Blue"}
    session.commit()

    monkeypatch.setattr(
        call_center_employee_resolver,
        "resolve",
        lambda *args, **kwargs: ResolveResult(
            status=call_center_employee_resolver.STATUS_TECHNICAL_ERROR,
            internal_number="344",
        ),
    )

    assert apply_call_center_employee_snapshot_on_ticket_create(session, ticket) is False

    assert profile.contact_name == "Татьяна"
    assert profile.last_name == "Левшина"
    assert profile.bitrix_id == "71701"
    assert profile.external_user_id == "71701"
    assert profile.bitrix_login == "t.levshina"
    assert profile.bitrix_password == "keep-secret"
    assert profile.custom_fields == {"custom_fio": "Левшина Татьяна", "favorite_color": "Blue"}
    assert "bitrix_password" not in ticket.call_center_profile_snapshot_json["profile_delta"]


def test_successful_not_found_clears_only_resolver_owned_profile_fields(monkeypatch):
    session = _session()
    _, profile, ticket = _seed_case(session, internal_number="000")
    session.add(_source())
    profile.contact_name = "Татьяна"
    profile.last_name = "Левшина"
    profile.bitrix_id = "71701"
    profile.external_user_id = "71701"
    profile.bitrix_login = "t.levshina"
    profile.bitrix_password = "keep-secret"
    profile.phone = "123"
    profile.custom_fields = {"custom_fio": "Левшина Татьяна", "favorite_color": "Blue"}
    session.commit()

    monkeypatch.setattr(
        call_center_employee_resolver,
        "resolve",
        lambda *args, **kwargs: ResolveResult(
            status=call_center_employee_resolver.STATUS_NOT_FOUND,
            internal_number="000",
        ),
    )

    assert apply_call_center_employee_snapshot_on_ticket_create(session, ticket) is False

    assert profile.contact_name is None
    assert profile.last_name is None
    assert profile.bitrix_id is None
    assert profile.external_user_id is None
    assert profile.bitrix_login is None
    assert profile.internal_number == "000"
    assert profile.bitrix_password == "keep-secret"
    assert profile.phone == "123"
    assert profile.custom_fields == {"favorite_color": "Blue"}
    assert ticket.call_center_profile_snapshot_json["profile_delta"] == {
        "contact_name": "",
        "last_name": "",
        "bitrix_id": "",
        "external_user_id": "",
        "bitrix_login": "",
        "internal_number": "000",
        "custom_fields": {"custom_fio": ""},
    }
