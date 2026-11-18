from __future__ import annotations

from datetime import datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
import uuid

import pytest
from fastapi import HTTPException
from sqlalchemy import MetaData, select
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from api.app.db import Base
from api.app.models import Client, ClientCardBlockConfig, ClientCardFieldConfig, ClientCardRuleConfig, ClientProfile, Company, PendingTaskIntent, TelegramContact, Ticket, TicketStatus, TicketWorkflowType
from api.app.routers import management_users, telegram
from api.app.schemas import ClientProfileUpdate
from api.app.services import client_card, telegram_onboarding
from scripts import merge_telegram_contact_duplicate


def _session(*, legacy_telegram_duplicates=False):
    engine = create_engine("sqlite:///:memory:", future=True)
    metadata = Base.metadata
    if legacy_telegram_duplicates:
        # Model a database from before Telegram identity uniqueness was enforced.
        metadata = MetaData()
        for table in Base.metadata.tables.values():
            table.to_metadata(metadata)
        contacts = metadata.tables[TelegramContact.__tablename__]
        identity_constraint = next(
            constraint for constraint in contacts.constraints
            if constraint.name == "uq_telegram_contacts_company_external_user"
        )
        contacts.constraints.remove(identity_constraint)
    metadata.create_all(engine)
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



def _search_field(key, *, block_key="profile", **overrides):
    config = {
        "label": key,
        "field_type": "text",
        "is_system": not key.startswith("custom_"),
        **overrides,
    }
    return ClientCardFieldConfig(id=key, key=key, block_key=block_key, **config)


def _configure_search(session, *fields):
    session.add_all([
        ClientCardBlockConfig(id="search-profile", key="profile", title="Profile"),
        _search_field("contact_name"),
        _search_field("last_name"),
        _search_field("internal_number"),
        *fields,
    ])
    session.commit()


def test_management_users_list_searches_telegram_and_profile_fields():
    session = _session()
    client, _, _ = _seed_user(session, username="needle")
    _configure_search(session)

    payload = management_users.list_management_users(q="needle", db=session, user=_admin())

    assert payload["total"] == 1
    assert payload["items"][0]["client_id"] == client.id
    assert payload["items"][0]["telegram_username"] == "needle"
    assert payload["items"][0]["telegram_user_id"] == "100500"
    assert payload["items"][0]["telegram_chat_id"] == "9001"
    assert payload["items"][0]["display_name"] == "Petrov Ivan"
    assert payload["items"][0]["internal_number"] == "100"
    assert "phone" not in payload["items"][0]
    assert payload["items"][0]["is_blocked"] is False
    assert payload["blocked_supported"] is True
    assert payload["items"][0]["last_activity_at"] is not None
    for query in ("Ivan TG", "Fallback Name"):
        assert management_users.list_management_users(q=query, db=session, user=_admin())["total"] == 1



def test_management_users_searches_configured_card_fields_and_honors_visibility():
    session = _session()
    client, profile, _ = _seed_user(session)
    session.add_all([
        ClientCardBlockConfig(id="inactive", key="inactive", title="Inactive", is_active=False),
        ClientCardBlockConfig(id="conditional", key="conditional", title="Conditional"),
        ClientCardBlockConfig(id="editor", key="editor", title="Editor", show_in_card=False),
        ClientCardRuleConfig(
            id="hide-search-values", name="Hide configurable values", condition_field_key="destination",
            condition_operator="equals", condition_value="Support",
            actions_json=[
                {"action": "hide", "scope": "both", "target_type": "block", "target_key": "conditional"},
                {"action": "hide", "scope": "editor", "target_key": "custom_conditional_field"},
            ],
        ),
    ])
    _configure_search(
        session,
        _search_field("destination", field_type="select"),
        _search_field("bitrix_login"),
        _search_field("custom_inactive", block_key="inactive"),
        _search_field("custom_hidden", is_visible=False, is_visible_in_editor=False),
        _search_field("custom_conditional_field"),
        _search_field("custom_conditional_block", block_key="conditional"),
        _search_field("custom_editor", block_key="editor", is_visible=False),
        _search_field("custom_admin", admin_only_edit=True),
        _search_field("custom_select", field_type="select",
                      options_json=["restricted-option"], admin_only_values_json=["restricted-option"]),
    )
    profile.bitrix_login = "work-login"
    profile.custom_fields = {
        "custom_new_office": "North Wing",
        "custom_inactive": "inactive-marker",
        "custom_hidden": "hidden-marker",
        "custom_conditional_field": "conditional-field-marker",
        "custom_conditional_block": "conditional-block-marker",
        "custom_editor": "editor-marker",
        "custom_admin": "admin-marker",
        "custom_select": "restricted-option",
    }
    session.commit()

    def search(query, user):
        return management_users.list_management_users(q=query, db=session, user=user)

    assert search("north wing", _agent())["total"] == 0
    session.add(_search_field("custom_new_office", label="Office label"))
    session.commit()
    for query in ("North Wing", "Support", "work-login", "editor-marker", "Petrov Ivan"):
        assert [item["client_id"] for item in search(query, _agent())["items"]] == [client.id]
    for query in ("custom_new_office", "Office label", "inactive-marker", "hidden-marker",
                  "conditional-field-marker", "conditional-block-marker"):
        assert search(query, _admin())["total"] == 0
    for query in ("admin-marker", "restricted-option"):
        assert search(query, _admin())["total"] == 1
        assert search(query, _agent())["total"] == 0


def test_management_users_search_excludes_ids_secrets_and_unconfigured_profile_fields():
    session = _session()
    _, profile, _ = _seed_user(session, external_user_id="998877", external_chat_id="-887766")
    _configure_search(
        session,
        _search_field("external_user_id"),
        _search_field("bitrix_password", field_type="password"),
        _search_field("custom_unmarked", field_type="password"),
        _search_field("custom_api_token"),
        _search_field("custom_sensitive", label="Access Secret"),
        _search_field("custom_web_password", field_type="textarea"),
        _search_field("custom_telegram_chat_id"),
        _search_field("custom_nested"),
        _search_field("custom_web_login"),
    )
    profile.external_user_id = "776655"
    profile.bitrix_password = "bitrix-marker"
    profile.web_password_encrypted = "encrypted-marker"
    profile.web_login = "visible-web-login"
    profile.email = "unconfigured@example.test"
    profile.custom_fields = {
        "custom_unmarked": "password-type-marker",
        "custom_api_token": "token-marker",
        "custom_sensitive": "label-protected-marker",
        "custom_web_password": "legacy-web-password-marker",
        "custom_telegram_chat_id": "665544",
        "custom_nested": {"hidden": "nested-marker"},
    }
    session.commit()

    for query in ("998877", "-887766", "776655", "665544", "bitrix-marker", "encrypted-marker",
                  "password-type-marker", "token-marker", "label-protected-marker",
                  "legacy-web-password-marker", "nested-marker", "unconfigured@example.test", "+70000000000"):
        assert management_users.list_management_users(q=query, db=session, user=_admin())["total"] == 0
    assert management_users.list_management_users(q="visible-web-login", db=session, user=_agent())["total"] == 1



def test_management_users_columns_and_values_use_card_visibility_and_role():
    session = _session()
    _, profile, _ = _seed_user(session)
    _configure_search(
        session,
        _search_field("destination", field_type="select", label="Assigned office",
                      options_json=["restricted-choice"], admin_only_values_json=["restricted-choice"]),
        _search_field("custom_new_office", label="New office"),
        _search_field("custom_fio", label="ФИО"),
        _search_field("telegram_chat_id", label="Configured chat ID"),
        _search_field("custom_password_field", field_type="password"),
        _search_field("custom_secret_token"),
        _search_field("custom_hidden", is_visible=False, is_visible_in_editor=False),
        _search_field("custom_staff_only", admin_only_edit=True),
    )
    profile.destination = "restricted-choice"
    profile.custom_fields = {
        "custom_new_office": "North",
        "custom_fio": "Card name",
        "custom_password_field": "private-password",
        "custom_secret_token": "private-token",
        "custom_hidden": "hidden-value",
        "custom_staff_only": "staff-value",
    }
    session.commit()

    agent = management_users.list_management_users(db=session, user=_agent())
    admin = management_users.list_management_users(db=session, user=_admin())
    columns = {column["key"]: column["label"] for column in agent["columns"]}
    assert columns["custom_fio"] == "ФИО"
    assert {"display_name", "telegram", "last_activity_at", "telegram_user_id"}.isdisjoint(columns)
    assert columns["destination"] == "Assigned office"
    assert columns["custom_new_office"] == "New office"
    assert columns["telegram_chat_id"] == "Configured chat ID"
    assert management_users.list_management_users(q="9001", db=session, user=_agent())["total"] == 0
    assert {"custom_password_field", "custom_secret_token", "custom_hidden", "custom_staff_only"}.isdisjoint(columns)
    row = agent["items"][0]
    assert row["column_values"] == {
        "contact_name": "Ivan", "last_name": "Petrov", "internal_number": "100", "custom_new_office": "North",
        "custom_fio": "Card name", "telegram_chat_id": "9001",
    }
    assert row["destination"] is None
    assert (row["telegram_user_id"], row["telegram_chat_id"]) == ("100500", "9001")
    assert admin["items"][0]["column_values"]["custom_staff_only"] == "staff-value"
    for item in (admin, agent):
        snapshot = repr(item)
        for secret in ("private-password", "private-token", "hidden-value"):
            assert secret not in snapshot



def test_management_users_columns_resolve_card_and_editor_visibility_per_row():
    session = _session()
    sand_client, sand, _ = _seed_user(session, username="sand")
    sales_client, sales, _ = _seed_user(session, username="sales", external_user_id="100501", external_chat_id="9002")
    other_client, other, _ = _seed_user(session, username="other", external_user_id="100502", external_chat_id="9003")

    def rule(key, order, field, operator, value, *actions):
        return ClientCardRuleConfig(
            id=key, name=key, sort_order=order, condition_field_key=field,
            condition_operator=operator, condition_value=value, actions_json=list(actions),
        )

    def visibility(key, action, scope, target="field"):
        return {"target_type": target, "target_key": key, "action": action, "scope": scope}

    session.add_all([
        ClientCardBlockConfig(id="conditional", key="conditional", title="Conditional", show_in_card=False, show_in_editor=False),
        ClientCardBlockConfig(id="inactive", key="inactive", title="Inactive", is_active=False),
        rule("sand", 10, "destination", "equals", "Песок", visibility("sand_owner", "show", "editor")),
        rule("sales", 20, "destination", "equals", "Отдел Продаж", visibility("sales_group", "show", "editor")),
        rule("anydesk", 30, "location_type", "equals", "Удалённый", visibility("anydesk", "show", "both")),
        rule("block", 40, "destination", "not_equals", "Песок", visibility("conditional", "show", "editor", "block")),
        rule("hide-when-empty", 50, "custom_gate", "is_empty", None, visibility("custom_note", "hide", "card")),
        rule("show-when-present", 60, "custom_gate", "is_not_empty", None, visibility("custom_present", "show", "card")),
        rule("show-last", 70, "", "always", None, visibility("custom_note", "show", "card"),
             visibility("custom_password", "show", "both"), visibility("inactive", "show", "both", "block")),
    ])
    hidden = {"is_visible": False, "is_visible_in_editor": False}
    _configure_search(
        session,
        _search_field("destination", field_type="select"),
        _search_field("location_type", field_type="select"),
        _search_field("sand_owner", **hidden),
        _search_field("sales_group", **hidden),
        _search_field("anydesk", **hidden),
        _search_field("custom_gate"),
        _search_field("custom_note", is_visible=True, is_visible_in_editor=False),
        _search_field("custom_present", **hidden),
        _search_field("custom_conditional_block", block_key="conditional"),
        _search_field("custom_inactive", block_key="inactive"),
        _search_field("custom_password", field_type="password", **hidden),
    )
    sand.destination, sales.destination, other.destination = "Песок", "Отдел Продаж", "HR"
    sand.location_type, sales.location_type, other.location_type = "Офисный", "Удалённый", "Офисный"
    for profile in (sand, sales, other):
        profile.sand_owner = "sand-marker"
        profile.sales_group = "sales-marker"
        profile.anydesk = "remote-marker"
        profile.custom_fields = {
            "custom_gate": " present " if profile is sales else "",
            "custom_note": "card-marker",
            "custom_present": "present-marker",
            "custom_conditional_block": "block-marker",
            "custom_password": "protected-marker",
            "custom_inactive": "inactive-marker",
        }
    session.commit()

    payload = management_users.list_management_users(db=session, user=_agent())
    keys = {column["key"] for column in payload["columns"]}
    conditional = {"sand_owner", "sales_group", "anydesk", "custom_note", "custom_conditional_block", "custom_present"}
    assert conditional <= keys
    assert {"custom_password", "custom_inactive"}.isdisjoint(keys)
    values = {row["client_id"]: row["column_values"] for row in payload["items"]}
    assert conditional & values[sand_client.id].keys() == {"sand_owner"}
    assert conditional & values[sales_client.id].keys() == {
        "sales_group", "anydesk", "custom_note", "custom_conditional_block", "custom_present",
    }
    assert conditional & values[other_client.id].keys() == {"custom_conditional_block"}
    assert values[sand_client.id]["sand_owner"] == "sand-marker"
    assert values[sales_client.id]["sales_group"] == "sales-marker"
    assert "protected-marker" not in repr(payload)
    for query in ("sand-marker", "sales-marker", "remote-marker", "card-marker", "block-marker", "present-marker",
                  "protected-marker", "100500", "9001"):
        assert management_users.list_management_users(q=query, db=session, user=_agent())["total"] == 0
    assert management_users.list_management_users(q="Отдел Продаж", db=session, user=_agent())["total"] == 1


def test_management_users_unknown_operator_does_not_show_column():
    session = _session()
    session.add(ClientCardRuleConfig(
        id="show-note", name="Show note", condition_field_key="contact_name",
        condition_operator="is_not_empty",
        actions_json=[{"action": "show", "scope": "editor", "target_key": "custom_note"}],
    ))
    _configure_search(session, _search_field("custom_note", is_visible=False, is_visible_in_editor=False))
    config = client_card._client_card_view_config(session)
    config.rules[0] = config.rules[0].model_copy(update={"condition_operator": "unknown"})
    rules = client_card._client_card_visibility_rules(config)

    assert "custom_note" not in client_card._client_card_column_keys(config, rules, {"contact_name": "Ivan"})


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


@pytest.mark.parametrize("role", ["admin", "agent", "chiki_piki"])
def test_management_users_editor_access(role):
    session = _session()
    client, _, _ = _seed_user(session)
    user = SimpleNamespace(id=role, role=role)
    operations = [
        lambda: management_users.list_management_users(db=session, user=user),
        lambda: management_users.get_client_card_config(db=session, user=user),
        lambda: management_users.get_management_user_profile(client.id, db=session, user=user),
        lambda: management_users.update_management_user_profile(
            client.id, ClientProfileUpdate(contact_name="Anna"), db=session, user=user,
        ),
    ]
    if role in {"admin", "agent"}:
        assert operations[0]()["items"][0]["client_id"] == client.id
        operations[1]()
        assert operations[2]().contact_name == "Ivan"
        assert operations[3]().contact_name == "Anna"
    else:
        for operation in operations:
            with pytest.raises(HTTPException) as exc:
                operation()
            assert exc.value.status_code == 403


def test_agent_cannot_update_protected_fields():
    session = _session()
    client, profile, _ = _seed_user(session)
    session.add_all([
        ClientCardFieldConfig(id="destination", key="destination", label="Destination", field_type="select",
                              block_key="profile", is_system=True, admin_only_edit=True),
        ClientCardFieldConfig(id="custom_locked", key="custom_locked", label="Locked", field_type="text",
                              block_key="profile", is_system=False, is_editable=False),
    ])
    session.commit()
    for payload in (ClientProfileUpdate(destination="Other"),
                    ClientProfileUpdate(custom_fields={"custom_locked": "Other"})):
        with pytest.raises(HTTPException) as exc:
            management_users.update_management_user_profile(client.id, payload, db=session, user=_agent())
        assert exc.value.status_code == 403
    assert profile.destination == "Support"
    assert not profile.custom_fields


@pytest.mark.parametrize("role, expected", [
    ("admin", 200), ("agent", 200), ("chiki_piki", 303), (None, 303),
])
def test_management_users_page_access(monkeypatch, role, expected):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from api.app import main
    from api.app.db import get_db

    user = None if role is None else SimpleNamespace(role=role, is_real_admin=role == "admin")
    monkeypatch.setattr(main, "_require_page_auth", lambda request, db: user)
    app = FastAPI()
    app.add_api_route("/management/users", main.management_users_page, methods=["GET"])
    app.dependency_overrides[get_db] = lambda: None
    with TestClient(app) as client:
        response = client.get("/management/users", follow_redirects=False)
    assert response.status_code == expected
    if expected == 303:
        assert response.headers["location"] == ("/tickets" if role else "/login?next=/management/users")


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
    session = _session(legacy_telegram_duplicates=True)
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
    session = _session(legacy_telegram_duplicates=True)
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
    _configure_search(session)
    company = _company(session)
    target_number = "needle-54"
    for index in range(55):
        _seed_user(
            session,
            company=company,
            username=f"user-{index:02d}",
            internal_number=target_number if index == 54 else f"room-{index:02d}",
            external_user_id=f"user-{index:02d}",
            external_chat_id=f"chat-{index:02d}",
            updated_delta=-1 if index == 54 else index,
        )

    first_page = management_users.list_management_users(limit=50, offset=0, db=session, user=_admin())
    second_page = management_users.list_management_users(limit=50, offset=50, db=session, user=_admin())
    assert len(first_page["items"]) == 50
    assert all(item["internal_number"] != target_number for item in first_page["items"])
    assert len(second_page["items"]) == 5
    target = second_page["items"][-1]
    assert target["internal_number"] == target_number

    payload = management_users.list_management_users(q=target_number, limit=50, offset=0, db=session, user=_admin())

    assert payload["total"] == 1
    assert payload["items"] == [target]


def test_management_users_search_by_username_and_profile_preserves_dedupe():
    session = _session(legacy_telegram_duplicates=True)
    _configure_search(session)
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

    for query in ("belfiss", "Sidorov", "4455"):
        payload = management_users.list_management_users(q=query, db=session, user=_admin())
        assert payload["total"] == 1
        assert payload["items"][0]["client_id"] == canonical_client.id
        assert payload["items"][0]["internal_number"] == "4455"

    for query in ("453920478", "-5267117449"):
        assert management_users.list_management_users(q=query, db=session, user=_admin())["total"] == 0


def test_management_users_block_and_unblock_are_idempotent_and_sync_duplicates():
    session = _session(legacy_telegram_duplicates=True)
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


@pytest.mark.parametrize("operation", [
    management_users.block_management_user,
    management_users.unblock_management_user,
])
def test_management_users_block_requires_admin(operation):
    session = _session()
    client, _, _ = _seed_user(session)

    with pytest.raises(HTTPException) as exc:
        operation(client.id, db=session, user=_agent())

    assert exc.value.status_code == 403


def test_management_users_reset_data_requires_admin():
    session = _session()
    client, _, _ = _seed_user(session)

    with pytest.raises(HTTPException) as exc:
        management_users.reset_management_user_profile_data(client.id, db=session, user=_agent())

    assert exc.value.status_code == 403


def test_management_users_dedupe_reports_blocked_when_duplicate_is_blocked():
    session = _session(legacy_telegram_duplicates=True)
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
    session = _session(legacy_telegram_duplicates=True)
    _configure_search(session)
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
    session = _session(legacy_telegram_duplicates=True)
    _configure_search(session)
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
    session = _session(legacy_telegram_duplicates=True)
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


def test_management_users_reset_data_is_idempotent_and_restores_first_connection_flow(monkeypatch):
    monkeypatch.setattr(telegram, "is_telegram_intake_open", lambda: True)
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
                "chat": {"id": 7001, "type": "private"},
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
    session = _session(legacy_telegram_duplicates=True)
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
    session = _session(legacy_telegram_duplicates=True)
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
    session = _session(legacy_telegram_duplicates=True)
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
    session = _session(legacy_telegram_duplicates=True)
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
    session = _session(legacy_telegram_duplicates=True)
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
    session = _session(legacy_telegram_duplicates=True)
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
    session = _session(legacy_telegram_duplicates=True)
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
    session = _session(legacy_telegram_duplicates=True)
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
    session = _session(legacy_telegram_duplicates=True)
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
