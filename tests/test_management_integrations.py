from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path
import uuid

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, event, select
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from api.app.auth import CurrentUser
from api.app.db import Base, get_db
from api.app.models import IntegrationLog
from api.app.routers import management_integrations


REPO_ROOT = Path(__file__).resolve().parents[1]


def _session_factory():
    engine = create_engine(
        "sqlite://",
        future=True,
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    return sessionmaker(bind=engine, autoflush=False, autocommit=False), engine


def _admin() -> CurrentUser:
    return CurrentUser(id="admin", role="admin", real_user_id="admin", real_role="admin", is_real_admin=True)


def _agent() -> CurrentUser:
    return CurrentUser(id="agent", role="agent", real_user_id="agent", real_role="agent", is_real_admin=False)


def _client(session, user: CurrentUser = None):
    app = FastAPI()
    app.include_router(management_integrations.router)
    app.dependency_overrides[get_db] = lambda: session
    app.dependency_overrides[management_integrations.get_current_user] = lambda: user or _admin()
    return TestClient(app)


def _log(
    session,
    *,
    created_at: datetime,
    event_type: str = "task_create",
    result: str = "success",
    code: str = "task_created",
    description: str = "created",
    deal_id: str | None = None,
    task_id: str | None = None,
    bitrix_id: str | None = None,
    call_time: datetime | None = None,
    message_text: str | None = None,
    telegram_message_id: str | None = None,
) -> IntegrationLog:
    row = IntegrationLog(
        id=str(uuid.uuid4()),
        created_at=created_at,
        source="external_api",
        event_type=event_type,
        result=result,
        code=code,
        description=description,
        deal_id=deal_id,
        task_id=task_id,
        bitrix_id=bitrix_id,
        call_time=call_time,
        message_text=message_text,
        telegram_message_id=telegram_message_id,
    )
    session.add(row)
    session.commit()
    return row


def _body(session, **params):
    return _client(session).get("/api/v1/management/integrations/logs", params=params).json()


def test_normalize_datetime_keeps_timezone_aware_utc():
    aware = management_integrations._normalize_datetime(datetime.fromisoformat("2026-09-04T10:00:00+03:00"))
    naive = management_integrations._normalize_datetime(datetime(2026, 9, 4, 10, 0, 0))

    assert aware == datetime(2026, 9, 4, 7, 0, 0, tzinfo=timezone.utc)
    assert aware.tzinfo is timezone.utc
    assert naive == datetime(2026, 9, 4, 10, 0, 0, tzinfo=timezone.utc)
    assert naive.tzinfo is timezone.utc


def test_admin_gets_list():
    Session, _engine = _session_factory()
    session = Session()
    row = _log(session, created_at=datetime(2026, 9, 4, 10, 0, 0), deal_id="deal-1", task_id="task-1")

    response = _client(session).get("/api/v1/management/integrations/logs")

    assert response.status_code == 200
    body = response.json()
    assert body["total"] == 1
    assert body["limit"] == 50
    assert body["offset"] == 0
    assert body["items"][0]["id"] == row.id
    assert body["items"][0]["source"] == "external_api"
    assert body["items"][0]["event_type"] == "task_create"
    assert body["items"][0]["result"] == "success"
    assert body["items"][0]["code"] == "task_created"
    assert body["items"][0]["description"] == "created"
    assert body["items"][0]["deal_id"] == "deal-1"
    assert body["items"][0]["task_id"] == "task-1"


def test_non_admin_forbidden():
    Session, _engine = _session_factory()
    session = Session()

    response = _client(session, _agent()).get("/api/v1/management/integrations/logs")

    assert response.status_code == 403


def test_event_type_filter():
    Session, _engine = _session_factory()
    session = Session()
    wanted = _log(session, created_at=datetime(2026, 9, 4, 10, 0, 0), event_type="telegram_send", code="telegram_sent")
    _log(session, created_at=datetime(2026, 9, 4, 11, 0, 0), event_type="task_create")

    body = _body(session, event_type="telegram_send")

    assert body["total"] == 1
    assert body["items"][0]["id"] == wanted.id


def test_result_filter():
    Session, _engine = _session_factory()
    session = Session()
    wanted = _log(session, created_at=datetime(2026, 9, 4, 10, 0, 0), result="error", code="boom")
    _log(session, created_at=datetime(2026, 9, 4, 11, 0, 0), result="success")

    body = _body(session, result="error")

    assert body["total"] == 1
    assert body["items"][0]["id"] == wanted.id


def test_created_from_inclusive():
    Session, _engine = _session_factory()
    session = Session()
    boundary = datetime(2026, 9, 4, 10, 0, 0)
    _log(session, created_at=boundary - timedelta(seconds=1))
    wanted = _log(session, created_at=boundary)

    body = _body(session, created_from="2026-09-04T10:00:00+00:00")

    assert body["total"] == 1
    assert body["items"][0]["id"] == wanted.id


def test_created_to_exclusive():
    Session, _engine = _session_factory()
    session = Session()
    boundary = datetime(2026, 9, 4, 10, 0, 0)
    wanted = _log(session, created_at=boundary - timedelta(microseconds=1))
    _log(session, created_at=boundary)

    body = _body(session, created_to="2026-09-04T10:00:00+00:00")

    assert body["total"] == 1
    assert body["items"][0]["id"] == wanted.id


def test_created_at_desc():
    Session, _engine = _session_factory()
    session = Session()
    first = _log(session, created_at=datetime(2026, 9, 4, 12, 0, 0), deal_id="new")
    second = _log(session, created_at=datetime(2026, 9, 4, 10, 0, 0), deal_id="old")

    body = _body(session)

    assert [item["id"] for item in body["items"]] == [first.id, second.id]


def test_invalid_event_type_rejected():
    Session, _engine = _session_factory()
    session = Session()
    _log(session, created_at=datetime(2026, 9, 4, 10, 0, 0))

    response = _client(session).get("/api/v1/management/integrations/logs", params={"event_type": "unknown"})

    assert response.status_code == 422


def test_invalid_result_rejected():
    Session, _engine = _session_factory()
    session = Session()
    _log(session, created_at=datetime(2026, 9, 4, 10, 0, 0))

    response = _client(session).get("/api/v1/management/integrations/logs", params={"result": "unknown"})

    assert response.status_code == 422


def test_limit_offset():
    Session, _engine = _session_factory()
    session = Session()
    rows = [
        _log(session, created_at=datetime(2026, 9, 4, 10, idx, 0), deal_id=str(idx))
        for idx in range(5)
    ]

    body = _body(session, limit=2, offset=2)

    assert body["limit"] == 2
    assert body["offset"] == 2
    assert body["total"] == 5
    assert [item["id"] for item in body["items"]] == [rows[2].id, rows[1].id]


def test_total_counts_whole_filtered_result():
    Session, _engine = _session_factory()
    session = Session()
    for idx in range(5):
        _log(session, created_at=datetime(2026, 9, 4, 10, idx, 0), result="success")
    _log(session, created_at=datetime(2026, 9, 4, 11, 0, 0), result="error")

    body = _body(session, result="success", limit=2, offset=0)

    assert len(body["items"]) == 2
    assert body["total"] == 5


def test_server_side_pagination_uses_count_and_limited_select():
    Session, engine = _session_factory()
    session = Session()
    for idx in range(120):
        _log(session, created_at=datetime(2026, 9, 4, 10, 0, 0) + timedelta(seconds=idx), result="success")
    statements = []

    def before_cursor_execute(_conn, _cursor, statement, _parameters, _context, _executemany):
        if "integration_logs" in statement and statement.lstrip().upper().startswith("SELECT"):
            statements.append(statement)

    event.listen(engine, "before_cursor_execute", before_cursor_execute)
    try:
        body = _body(session, result="success", limit=25, offset=50)
    finally:
        event.remove(engine, "before_cursor_execute", before_cursor_execute)

    assert body["total"] == 120
    assert len(body["items"]) == 25
    assert len(statements) == 2
    assert "count" in statements[0].lower()
    assert "limit" in statements[1].lower()
    assert "offset" in statements[1].lower()


def test_combination_filters():
    Session, _engine = _session_factory()
    session = Session()
    wanted = _log(
        session,
        created_at=datetime(2026, 9, 4, 12, 0, 0),
        event_type="telegram_send",
        result="duplicate",
        bitrix_id="42",
        call_time=datetime(2026, 9, 4, 9, 0, 0, tzinfo=timezone.utc),
        message_text="full text",
        telegram_message_id="100",
    )
    _log(session, created_at=datetime(2026, 9, 4, 12, 0, 0), event_type="telegram_send", result="success")
    _log(session, created_at=datetime(2026, 9, 5, 12, 0, 0), event_type="telegram_send", result="duplicate")

    body = _body(
        session,
        event_type="telegram_send",
        result="duplicate",
        created_from="2026-09-04T00:00:00+00:00",
        created_to="2026-09-05T00:00:00+00:00",
    )

    assert body["total"] == 1
    item = body["items"][0]
    assert item["id"] == wanted.id
    assert item["bitrix_id"] == "42"
    assert item["message_text"] == "full text"
    assert item["telegram_message_id"] == "100"


def test_empty_result():
    Session, _engine = _session_factory()
    session = Session()
    _log(session, created_at=datetime(2026, 9, 4, 10, 0, 0), result="success")

    body = _body(session, result="error")

    assert body == {"items": [], "total": 0, "limit": 50, "offset": 0}


def test_management_integrations_page_route_is_admin_guarded_in_main():
    source = (REPO_ROOT / "api" / "app" / "main.py").read_text(encoding="utf-8")
    start = source.index('@app.get("/management/integrations")')
    end = source.index('@app.get("/management/users")')
    block = source[start:end]

    assert "def management_integrations_page" in block
    assert 'return _redirect_login("/management/integrations")' in block
    assert "if not user.is_real_admin" in block
    assert 'templates" / "management_integrations.html"' in block
