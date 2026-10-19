"""Search follows the real requester-name priority before SQL count/pagination."""
import os
import uuid
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest
from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, event, insert, text
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from api.app.auth import get_current_user
from api.app.db import Base, get_db
from api.app.models import (BotButtonSetting, Client, ClientProfile, Company, InternalTaskDefinition,
                            InternalTaskOccurrence, TelegramContact, Ticket, TicketMessage, User)
from api.app.routers.tickets import router
from api.app.services.ticket_list import list_special_tickets, list_tickets_paginated, requester_display_name

NOW = datetime(2026, 10, 19, 12, tzinfo=timezone.utc)


@pytest.fixture(params=["sqlite", "postgresql", "postgresql_text"])
def case(request):
    admin_engine = None
    if request.param == "sqlite":
        engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)

        @event.listens_for(engine, "connect")
        def foreign_keys(connection, _):
            connection.execute("PRAGMA foreign_keys=ON")
    else:
        url = os.environ.get("TICKET_SEARCH_TEST_DATABASE_URL")
        if not url:
            pytest.skip("Set TICKET_SEARCH_TEST_DATABASE_URL for isolated PostgreSQL schemas")
        admin_engine = create_engine(url)
        schema = "ticket_search_" + uuid.uuid4().hex
        with admin_engine.begin() as connection:
            connection.execute(text(f"CREATE SCHEMA {schema}"))
        engine = create_engine(url, connect_args={"options": f"-csearch_path={schema}"})
    try:
        Base.metadata.create_all(engine)
        if request.param == "postgresql_text":
            # Reproduce the deployed schema, which predates the Boolean ORM mapping.
            with engine.begin() as connection:
                connection.execute(text("ALTER TABLE internal_task_occurrences ALTER COLUMN display_author "
                                        "TYPE VARCHAR(32) USING display_author::varchar"))
                connection.execute(text("ALTER TABLE client_profiles ALTER COLUMN custom_fields "
                                        "TYPE JSONB USING custom_fields::jsonb"))
        with Session(engine, autoflush=False) as db:
            db.add(Company(id="company", name="Synthetic"))
            db.flush()
            db.add_all([User(id="admin", company_id="company", full_name="Администратор", role="admin"),
                        User(id="agent", company_id="company", full_name="Сотрудник", role="agent"),
                        User(id="other", company_id="company", full_name="Другой сотрудник", role="agent")])
            db.commit()
            yield SimpleNamespace(db=db, engine=engine, admin=SimpleNamespace(id="admin", role="admin"))
    finally:
        engine.dispose()
        if admin_engine:
            with admin_engine.begin() as connection:
                connection.execute(text(f"DROP SCHEMA {schema} CASCADE"))
            admin_engine.dispose()


def seed(case, *, fio="", display="", username="", client_name="Вытесненный Клиент", snapshot=None,
         lookup=False, cc_first=None, cc_last=None, rop=False, profile=True, **ticket_values):
    db = case.db
    key = uuid.uuid4().hex
    case.ticket_number = getattr(case, "ticket_number", 0) + 1
    db.add(Client(id=key, company_id="company", display_name=client_name))
    db.flush()
    if profile:
        db.add(ClientProfile(id=key, client_id=key, custom_fields={"custom_fio": fio, "custom_vremennyy_rop": rop}))
    db.add(TelegramContact(id=key, company_id="company", client_id=key,
                           external_user_id=key, external_chat_id=key,
                           external_display_name=display, external_username=username))
    ticket = Ticket(id=key, client_id=key, company_id="company", ticket_number=case.ticket_number,
                    subject="Neutral", status="new", assignee_id="agent", agent_visible=True,
                    call_center_profile_snapshot_json=snapshot,
                    call_center_employee_lookup_at=NOW if lookup else None,
                    call_center_employee_first_name=cc_first, call_center_employee_last_name=cc_last,
                    created_at=NOW, updated_at=NOW)
    for name, value in ticket_values.items():
        setattr(ticket, name, value)
    db.add(ticket)
    db.flush()
    return ticket


def internal(case, *, author=True, full_name="Создатель Мария", login="creator_login", **values):
    db = case.db
    ticket = seed(case, fio="Вытесненное ФИО", workflow_type="task", **values)
    creator = User(id=uuid.uuid4().hex, company_id="company", full_name=full_name, login=login, role="admin")
    db.add(creator)
    db.flush()
    definition = InternalTaskDefinition(id=ticket.id, company_id="company", title="Neutral", description="Neutral",
        creator_admin_id=creator.id, responsible_admin_id="admin", display_author=author,
        assignment_mode="distribution", notify_on_completion=False, schedule_kind="immediate",
        timezone_name="Europe/Moscow", state="finished")
    db.add(definition)
    db.flush()
    db.add(InternalTaskOccurrence(id=ticket.id, definition_id=definition.id, occurrence_at_utc=NOW,
        ticket_id=ticket.id, title_snapshot="Neutral", description_snapshot="Neutral",
        creator_admin_id=creator.id, responsible_admin_id="admin", display_author=author,
        effective_assignment_mode="distribution", notify_on_completion=False, materialized_at_utc=NOW))
    db.flush()
    return ticket


def listing(case, query=None, *, special=False, user=None, **options):
    statuses = options.pop("statuses", ["new"])
    common = dict(db=case.db, user=user or case.admin, sort="created_at", order="asc", page=1, limit=100, query=query)
    common.update(options)
    if special:
        return list_special_tickets(**common, ensure_ticket_number_fn=lambda db, ticket: ticket.ticket_number)
    return list_tickets_paginated(**common, statuses=statuses,
                                  is_frozen=False, filter_admin_assignees=False)


def test_profile_fio_is_searchable(case):
    ticket = seed(case, fio="Петрова Анна")
    case.db.commit()
    assert requester_display_name(case.db, ticket) == "Петрова Анна"
    result = listing(case, "пЕтРоВа")
    assert result.total == 1 and result.items[0].id == ticket.id


def test_closed_tickets_of_same_client_keep_their_own_snapshot_names(case):
    old = seed(case, fio="Петрова Анна", status="closed", closed_at=NOW,
               snapshot={"custom_fields": {"custom_fio": "Иванова Мария"}})
    new = Ticket(id=uuid.uuid4().hex, company_id="company", client_id=old.client_id,
                 ticket_number=old.ticket_number + 1, subject="Neutral", assignee_id="agent",
                 status="closed", closed_at=NOW, created_at=NOW, updated_at=NOW,
                 call_center_profile_snapshot_json={"profile_delta": {"custom_fields": {"custom_fio": "Петрова Анна"}}})
    case.db.add(new)
    case.db.commit()
    assert {item.id for item in listing(case, "иВаНоВа", statuses=["closed"]).items} == {old.id}
    assert {item.id for item in listing(case, "пЕтРоВа", statuses=["closed"]).items} == {new.id}


def test_displayed_names_and_hidden_sources_follow_real_priority(case):
    expected = {}

    def ordinary(name, **values):
        ticket = seed(case, **values)
        expected[ticket.id] = name

    ordinary("Иванова Мария", snapshot={"custom_fields": {"custom_fio": " Иванова Мария "}},
             fio="Вытесненное ФИО", display="Вытесненный Telegram", username="hidden_handle")
    ordinary("Снимкова Алёна", snapshot={"custom_fields": {"custom_fio": "Вытесненное ФИО"},
             "profile_delta": {"custom_fields": {"custom_fio": "Снимкова Алёна"},
                               "last_name": "Вытесненная", "contact_name": "Вытесненное"}}, fio="Вытесненное ФИО")
    ordinary("Орлова Ольга", lookup=True, cc_last="Орлова", cc_first="Ольга", fio="Вытесненное ФИО")
    ordinary("Соколова Светлана",
             snapshot={"last_name": "Соколова", "first_name": "Светлана"},
             fio="Вытесненное ФИО")
    ordinary("Дельтова Дарья", snapshot={"profile_delta": {"last_name": "Дельтова", "contact_name": "Дарья"}},
             cc_last="Вытесненная", cc_first="Вытесненное", fio="Вытесненное ФИО")
    ordinary("Петрова Анна", fio=" Петрова Анна ", display="Вытесненный Telegram", username="hidden_handle")
    ordinary("Telegram Ёлкина", display=" Telegram Ёлкина ", username="hidden_handle")
    ordinary("Телеграм Без Снимка", display="Телеграм Без Снимка", fio="Вытесненное ФИО", lookup=True,
             cc_first="Вытесненное", cc_last="Вытесненная",
             snapshot={"custom_fields": {"custom_fio": "Вытесненное ФИО"},
                       "profile_delta": {"custom_fields": {"custom_fio": ""}, "contact_name": "", "last_name": ""}})
    ordinary("Клиентов Кирилл", display=" ", client_name=" Клиентов Кирилл ", username="hidden_handle")
    ordinary("visible_Username", display=" ", client_name=" ", username=" visible_Username ", profile=False)
    ordinary("Клиент", client_name=" ", profile=False)
    ordinary("Иванов Иван ВРиО РОПа", fio="Иванов Иван", rop=True)
    for name, values in [("Создатель Мария", {}), ("visible_creator", {"full_name": " ", "login": "visible_creator"}),
                         ("Система", {"author": False, "full_name": "Скрытый Автор", "login": "hidden_author"}),
                         ("Система", {"full_name": " ", "login": None})]:
        ticket = internal(case, **values)
        expected[ticket.id] = name
    case.db.commit()
    assert {ticket_id: requester_display_name(case.db, case.db.get(Ticket, ticket_id))
            for ticket_id in expected} == expected
    for query in [name[1:-1].swapcase() for name in expected.values()] + ["  аНнА  ", "ёлК", "врио ропа"]:
        result = listing(case, query)
        ids = {key for key, name in expected.items() if query.strip().casefold() in name.casefold()}
        assert {item.id for item in result.items} == ids, query
        assert result.total == len(ids)
    for hidden in ["вЫтЕсНеН", "hidden_handle", "скрытый автор", "hidden_author"]:
        assert listing(case, hidden).total == 0, hidden


@pytest.mark.parametrize("special", [False, True])
def test_name_filter_precedes_pagination_without_loading_the_archive(case, special):
    matching = []
    for index in range(205):
        ticket = seed(case, fio="Целевая Анна" if index >= 202 else "Неподходящее Имя",
                      created_at=NOW + timedelta(seconds=index), is_special=special)
        if index >= 202:
            matching.append(ticket.id)
    case.db.commit()
    case.db.expunge_all()
    statements = []
    parameter_counts = []
    loaded_tickets = []

    def capture(_connection, _cursor, statement, parameters, _context, _executemany):
        statements.append(statement.lower())
        parameter_counts.append(len(parameters))

    def loaded(_session, instance):
        if isinstance(instance, Ticket):
            loaded_tickets.append(instance.id)

    event.listen(case.engine, "before_cursor_execute", capture)
    event.listen(case.db, "loaded_as_persistent", loaded)
    try:
        first = listing(case, "цЕЛЕВАЯ", special=special, limit=2)
    finally:
        event.remove(case.engine, "before_cursor_execute", capture)
        event.remove(case.db, "loaded_as_persistent", loaded)
    assert (first.total, first.pages, first.page) == (3, 2, 1)
    assert [item.id for item in first.items] == matching[:2]
    assert loaded_tickets == matching[:2]
    assert len(statements) <= 20
    assert max(parameter_counts) < 500
    assert sum("from client_profiles" in sql for sql in statements) <= 3
    assert sum("from telegram_contacts" in sql for sql in statements) <= 4
    second = listing(case, "цЕЛЕВАЯ", special=special, limit=2, page=99)
    assert (second.total, second.pages, second.page) == (3, 2, 2)
    assert [item.id for item in second.items] == matching[2:]


def test_unicode_casefold_and_literal_name_search(case):
    names = ["Ёлкина АННА", "Straße", "İpek", "Σίσυφος", "Ꭰꭰ", "100%_имя", "100xxимя",
             "\u00a0\tПетрова Анна\u3000", "Иванов Иван ВРиО РОПа"]
    expected = {seed(case, fio=name, rop=name.endswith("РОПа")).id: name.strip() for name in names}
    case.db.commit()
    for query in ["ёлКИНА", "анна", "STRASSE", "ss", "i\u0307p", "ΣΊΣ", "σ", "Ꭰ", "100%_имя", "ВРИО РОПА"]:
        result = listing(case, query)
        ids = {key for key, name in expected.items() if query.casefold() in name.casefold()}
        assert {item.id for item in result.items} == ids, query
        assert result.total == len(ids)


def test_many_name_matches_do_not_become_sql_id_parameters(case):
    first = seed(case, fio="Целевая Анна")
    case.db.execute(insert(Ticket), [{
        "id": uuid.uuid4().hex, "company_id": "company", "client_id": first.client_id,
        "ticket_number": index + 2, "subject": "Neutral", "status": "new", "assignee_id": "agent",
        "created_at": NOW + timedelta(seconds=index + 1), "updated_at": NOW,
    } for index in range(1200)])
    case.db.commit()
    case.db.expunge_all()
    parameter_counts = []

    def capture(_connection, _cursor, _statement, parameters, _context, _executemany):
        parameter_counts.append(len(parameters))

    event.listen(case.engine, "before_cursor_execute", capture)
    try:
        result = listing(case, "аНнА", limit=2, page=600)
    finally:
        event.remove(case.engine, "before_cursor_execute", capture)
    assert (result.total, result.pages, result.page, len(result.items)) == (1201, 601, 600, 2)
    assert len(parameter_counts) <= 20
    assert max(parameter_counts) < 500


def test_search_preserves_other_fields_and_role_visibility(case):
    named = seed(case, fio="Целевая Анна", button_config_key="Колл Центр|ticket|neutral")
    hidden = seed(case, fio="Целевая Анна", assignee_id="other")
    invisible = seed(case, fio="Целевая Анна", agent_visible=False)
    case.db.commit()
    for role in ["admin", "agent", "trainee", "chiki_piki"]:
        user = SimpleNamespace(id="agent" if role != "admin" else "admin", role=role)
        baseline = listing(case, user=user)
        searched = listing(case, "цЕЛЕВАЯ", user=user)
        assert {item.id for item in searched.items} == {item.id for item in baseline.items}
        assert searched.total == baseline.total
        if role in {"agent", "trainee", "chiki_piki"}:
            assert {item.id for item in searched.items} == {named.id}
    named.subject = "legacy-topic"
    named.button_config_key = "legacy-key"
    named.instruction_button_key = "legacy-instruction"
    case.db.add(TicketMessage(id=uuid.uuid4().hex, ticket_id=named.id, message_type="client", body="legacy-message"))
    case.db.add(BotButtonSetting(id=uuid.uuid4().hex, button_key="legacy-key", button_path="legacy-path",
        button_label="legacy-label", menu_type="legacy-menu", workflow_type="ticket"))
    case.db.commit()
    for query in ["legacy-topic", "legacy-key", "legacy-instruction", "legacy-message", "legacy-path", "legacy-label", "legacy-menu"]:
        assert {item.id for item in listing(case, query).items} == {named.id}, query


def test_http_search_keeps_admin_assignee_filter(case):
    visible = seed(case, fio="Петрова Анна")
    seed(case, fio="Петрова Анна", assignee_id="other")
    case.db.commit()
    app = FastAPI()
    app.include_router(router)
    app.dependency_overrides[get_db] = lambda: case.db
    app.dependency_overrides[get_current_user] = lambda: case.admin
    with TestClient(app) as http:
        response = http.get("/api/v1/tickets/active", params={"query": "пЕТРова", "assignee_ids": "agent", "limit": 1})
        assert response.status_code == 200, response.text
        assert response.json()["total"] == 1
        assert response.json()["items"][0]["id"] == visible.id


def test_special_internal_author_search_and_permissions(case):
    ticket = internal(case, is_special=True)
    internal(case, author=False, is_special=True, login="hidden_author")
    case.db.commit()
    result = listing(case, "сОЗДАТЕЛЬ", special=True)
    assert result.total == 1 and result.items[0].id == ticket.id
    assert listing(case, "сИСТЕМА", special=True).total == 1
    assert listing(case, "hidden_author", special=True).total == 0
    with pytest.raises(HTTPException) as exc:
        listing(case, "сИСТЕМА", special=True, user=SimpleNamespace(id="agent", role="agent"))
    assert exc.value.status_code == 403


@pytest.mark.parametrize("case", ["postgresql_text"], indirect=True)
def test_postgresql_text_display_author_http_search(case):
    from api.app.services.ticket_list import _display_author_enabled

    values = ["1", "true", "t", "yes", "y", "on", " TRUE ", "\tYeS\n", "\u00a0ON\u3000",
              "false", "0", "off", "no", "", "  ", "unexpected"]
    expected_names = {}
    for value in values:
        ticket = internal(case, full_name="Автор Мария", login=uuid.uuid4().hex)
        case.db.execute(text("UPDATE internal_task_occurrences SET display_author=:value WHERE ticket_id=:id"),
                        {"value": value, "id": ticket.id})
        expected_names[ticket.id] = "Автор Мария" if _display_author_enabled(value) else "Система"
    ordinary = seed(case, fio="Петрова Анна", subject="unique-topic")
    ordinary_id = ordinary.id
    case.db.add(TicketMessage(id=uuid.uuid4().hex, ticket_id=ordinary_id, message_type="client", body="unique-message"))
    case.db.commit()
    case.db.expunge_all()
    assert {key: requester_display_name(case.db, case.db.get(Ticket, key))
            for key in expected_names} == expected_names
    app = FastAPI()
    app.include_router(router)
    app.dependency_overrides[get_db] = lambda: case.db
    app.dependency_overrides[get_current_user] = lambda: case.admin
    with TestClient(app, raise_server_exceptions=False) as http:
        for query in ["аВТОР", "сИСТЕМА", "no-matches-here", "unique-topic", "unique-message", "пЕТРова"]:
            response = http.get("/api/v1/tickets/active", params={"query": query, "assignee_ids": "agent", "limit": 100})
            assert response.status_code == 200, (query, response.text)
            expected = {key for key, name in expected_names.items() if query.casefold() in name.casefold()}
            if query in {"unique-topic", "unique-message", "пЕТРова"}:
                expected.add(ordinary_id)
            result = response.json()
            assert result["total"] == len(expected)
            assert {item["id"] for item in result["items"]} == expected
            assert {item["id"]: item["requester_display_name"] for item in result["items"]
                    if item["id"] in expected_names} == {key: expected_names[key] for key in expected if key in expected_names}
