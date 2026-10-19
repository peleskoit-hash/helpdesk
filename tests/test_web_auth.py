"""Isolated HTTP/schema checks; never starts the user's DEV application."""
import os
import uuid
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest
from cryptography.fernet import Fernet
from fastapi import Depends, FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, event, inspect, select, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session
from starlette.middleware.sessions import SessionMiddleware

from api.app.auth import get_current_user, hash_password
from api.app.config import settings
from api.app.db import Base, get_db
from api.app.models import (Client, ClientProfile, Company, TelegramContact, Ticket, User,
                            WebClientSession, WebLoginLimit)
from api.app.routers.web_auth import router
from api.app.services import web_auth as auth, web_credentials

ORIGIN = {"Origin": "http://testserver"}
LOGIN = "/api/v1/web/auth/login"
ME = "/api/v1/web/auth/me"
LOGOUT = "/api/v1/web/auth/logout"
PASSWORD = "Synthetic-Web-A1!"


@pytest.fixture(params=["sqlite", "postgresql"])
def engine(request, tmp_path):
    admin = None
    if request.param == "sqlite":
        url = "sqlite:///" + (tmp_path / "web-auth.sqlite").as_posix()
        engine = create_engine(url, connect_args={"check_same_thread": False, "timeout": 20})

        @event.listens_for(engine, "connect")
        def foreign_keys(connection, _):
            connection.execute("PRAGMA foreign_keys=ON")
    else:
        url = os.environ.get("WEB_AUTH_TEST_DATABASE_URL")
        if not url:
            pytest.skip("Set WEB_AUTH_TEST_DATABASE_URL to an isolated PostgreSQL test database")
        admin = create_engine(url)
        schema = "web_auth_" + uuid.uuid4().hex
        with admin.begin() as connection:
            connection.execute(text(f"CREATE SCHEMA {schema}"))
        engine = create_engine(url, connect_args={"options": f"-csearch_path={schema}"})
    try:
        Base.metadata.create_all(engine)
        yield engine
    finally:
        engine.dispose()
        if admin:
            with admin.begin() as connection:
                connection.execute(text(f"DROP SCHEMA {schema} CASCADE"))
            admin.dispose()


def make_app(engine):
    app = FastAPI()
    app.add_middleware(SessionMiddleware, secret_key="synthetic-test-secret", session_cookie="esdesk_session")
    app.include_router(router)

    def db_session():
        with Session(engine, autoflush=False) as db:
            yield db

    app.dependency_overrides[get_db] = db_session

    @app.get("/staff-only")
    def staff_only(user=Depends(get_current_user)):
        return {"user_id": user.id}

    @app.post("/api/v1/web/action")
    def action(client=Depends(auth.get_current_web_client), db=Depends(get_db)):
        auth.register_web_activity(db, client)
        db.commit()
        return {"client_id": client.id}

    return app


@pytest.fixture
def case(engine, monkeypatch):
    monkeypatch.setattr(settings, "app_env", "dev")
    monkeypatch.setattr(settings, "session_cookie_secure", False)
    monkeypatch.setattr(settings, "app_public_base_url", None)
    monkeypatch.setattr(settings, "bitrix_password_encryption_key", Fernet.generate_key().decode())
    clock = [datetime(2026, 10, 18, 16, 0, tzinfo=timezone.utc)]  # 19:00 Moscow
    monkeypatch.setattr(auth, "utcnow", lambda: clock[0])
    with Session(engine) as db:
        db.add(Company(id="company", name="Synthetic"))
        db.flush()
        db.add(Client(id="client", company_id="company", display_name="Synthetic"))
        db.add(User(id="staff", company_id="company", full_name="Staff", login="staff", role="agent"))
        db.flush()
        db.add(ClientProfile(id="profile", client_id="client", is_verified=True,
                             web_login="Client.Login", web_login_normalized="client.login",
                             web_password_hash=hash_password(PASSWORD), web_password_encrypted="synthetic",
                             web_credentials_version=1))
        db.add(TelegramContact(id="contact", company_id="company", client_id="client",
                               external_user_id="123", external_chat_id="123"))
        db.commit()
    app = make_app(engine)
    with TestClient(app) as http:
        yield SimpleNamespace(engine=engine, app=app, http=http, clock=clock)


def login(case, **changes):
    return case.http.post(LOGIN, json={"login": "client.login", "password": PASSWORD, **changes}, headers=ORIGIN)


def change_profile(case, **values):
    with Session(case.engine) as db:
        profile = db.get(ClientProfile, "profile")
        for key, value in values.items():
            setattr(profile, key, value)
        db.commit()


def ticket(case, **values):
    with Session(case.engine) as db:
        db.add(Ticket(id="ticket", company_id="company", client_id="client", subject="Synthetic",
                      **{"channel": "web", "status": "new", "workflow_type": "ticket", "task_kind": "normal", **values}))
        db.commit()


def test_login_safe_output_hash_and_cookie(case):
    response = login(case, login="  CLIENT.Login  ")
    assert response.status_code == 200, response.text
    assert response.json() == {"client_id": "client"}
    cookie = response.headers["set-cookie"]
    assert "HttpOnly" in cookie and "SameSite=strict" in cookie and "Path=/api/v1/web" in cookie
    assert "Secure" not in cookie and "esdesk_session=" not in cookie
    token = case.http.cookies[auth.COOKIE_NAME]
    with Session(case.engine) as db:
        session = db.scalar(select(WebClientSession))
        assert session.token_hash == auth.token_hash(token) and session.token_hash != token
        assert session.credentials_version == 1
        assert auth.as_utc(session.normal_expires_at) == case.clock[0].replace(hour=17)
        assert db.scalar(select(Client.id)) == "client"
    assert case.http.get(ME).json() == {"client_id": "client"}
    assert response.headers["cache-control"] == "no-store"


@pytest.mark.parametrize("reason", ["wrong_login", "wrong_password", "unverified", "no_password", "no_contact", "wrong_company"])
def test_invalid_credentials_are_indistinguishable(case, reason):
    values = {}
    if reason == "wrong_login":
        values["login"] = "unknown"
    elif reason == "wrong_password":
        values["password"] = "wrong"
    elif reason == "unverified":
        change_profile(case, is_verified=False)
    elif reason == "no_password":
        change_profile(case, web_password_hash=None, web_password_encrypted=None)
    else:
        with Session(case.engine) as db:
            contact = db.get(TelegramContact, "contact")
            if reason == "no_contact":
                db.delete(contact)
            else:
                db.add(Company(id="other", name="Other"))
                db.flush()
                contact.company_id = "other"
            db.commit()
    response = login(case, **values)
    assert response.status_code == 401
    assert response.json()["detail"]["error"]["code"] == "web_invalid_credentials"
    assert response.headers["retry-after"] == "30"
    assert auth.COOKIE_NAME not in case.http.cookies
    with Session(case.engine) as db:
        assert db.scalar(select(WebClientSession)) is None


def test_logout_only_revokes_current_session(case):
    login(case)
    first = case.http.cookies[auth.COOKIE_NAME]
    login(case)
    second = case.http.cookies[auth.COOKIE_NAME]
    assert first != second
    assert case.http.post(LOGOUT, headers=ORIGIN).status_code == 204
    assert case.http.get(ME).status_code == 401
    with TestClient(make_app(case.engine)) as fresh:
        fresh.cookies.set(auth.COOKIE_NAME, first, path=auth.COOKIE_PATH)
        assert fresh.get(ME).status_code == 200
        fresh.cookies.set(auth.COOKIE_NAME, second, path=auth.COOKIE_PATH)
        assert fresh.get(ME).status_code == 401


def test_staff_and_web_sessions_are_independent(case):
    import base64
    import json
    from itsdangerous import TimestampSigner

    staff_cookie = TimestampSigner("synthetic-test-secret").sign(
        base64.b64encode(json.dumps({"auth_user_id": "staff"}).encode())).decode()
    case.http.cookies.set("esdesk_session", staff_cookie)
    assert case.http.get("/staff-only").status_code == 200
    assert case.http.get(ME).status_code == 401
    login(case)
    assert case.http.get("/staff-only").status_code == 200
    case.http.cookies.delete("esdesk_session")
    assert case.http.get("/staff-only").status_code == 401
    assert case.http.get(ME).status_code == 200


def test_session_and_limit_survive_engine_and_app_restart(case):
    login(case)
    token = case.http.cookies[auth.COOKIE_NAME]
    login(case, login="unknown")
    case.engine.dispose()
    with TestClient(make_app(case.engine)) as fresh:
        fresh.cookies.set(auth.COOKIE_NAME, token, path=auth.COOKIE_PATH)
        assert fresh.get(ME).status_code == 200
        response = fresh.post(LOGIN, json={"login": "unknown", "password": "wrong"}, headers=ORIGIN)
        assert response.status_code == 429 and response.headers["retry-after"] == "30"


@pytest.mark.parametrize("start,cutoff", [
    ("2026-10-18T16:59:59+00:00", "2026-10-18T17:00:00+00:00"),
    ("2026-10-18T17:00:00+00:00", "2026-10-19T17:00:00+00:00"),
    ("2026-12-31T18:00:00+00:00", "2027-01-01T17:00:00+00:00"),
])
def test_normal_deadline_boundaries(case, start, cutoff):
    case.clock[0] = datetime.fromisoformat(start)
    assert login(case).status_code == 200
    case.clock[0] = datetime.fromisoformat(cutoff) - timedelta(microseconds=1)
    assert case.http.get(ME).status_code == 200
    case.clock[0] += timedelta(microseconds=1)
    assert case.http.get(ME).status_code == 401
    case.clock[0] += timedelta(days=1)
    assert case.http.get(ME).status_code == 401


def test_open_web_ticket_idle_deadline_and_polling(case):
    login(case)
    ticket(case)
    initial = case.clock[0]
    case.clock[0] += timedelta(hours=23, minutes=59, seconds=59)
    for _ in range(3):
        assert case.http.get(ME).status_code == 200
    with Session(case.engine) as db:
        assert auth.as_utc(db.scalar(select(WebClientSession)).last_activity_at) == initial
    case.clock[0] += timedelta(seconds=1)
    assert case.http.get(ME).status_code == 401


def test_explicit_activity_and_closing_ticket_restore_original_deadline(case):
    login(case)
    ticket(case)
    case.clock[0] += timedelta(hours=23)
    assert case.http.post("/api/v1/web/action", headers=ORIGIN).status_code == 200
    case.clock[0] += timedelta(hours=23)
    assert case.http.get(ME).status_code == 200
    with Session(case.engine) as db:
        db.get(Ticket, "ticket").status = "closed"
        db.commit()
    assert case.http.get(ME).status_code == 401


@pytest.mark.parametrize("values", [
    {"channel": "telegram"}, {"status": "closed"}, {"task_kind": "group_child"},
    {"task_kind": "group_parent"}, {"workflow_type": "task"}, {"workflow_type": "auto"},
])
def test_only_ordinary_open_web_tickets_extend_session(case, values):
    login(case)
    ticket(case, **values)
    case.clock[0] = case.clock[0].replace(hour=17)
    assert case.http.get(ME).status_code == 401


@pytest.mark.parametrize("status", ["unassigned", "new", "pending"])
def test_all_open_statuses(case, status):
    login(case)
    ticket(case, status=status)
    case.clock[0] = case.clock[0].replace(hour=17)
    assert case.http.get(ME).status_code == 200


@pytest.mark.parametrize("clear", [False, True])
def test_real_credential_change_revokes_all_previous_sessions(case, clear):
    login(case)
    old_token = case.http.cookies[auth.COOKIE_NAME]
    login(case)
    with Session(case.engine) as db:
        profile = db.get(ClientProfile, "profile")
        web_credentials.apply_changes(db, profile, {} if clear else {"custom_web_password": "New-Web-B2!"},
                                      clear_password=clear)
        db.commit()
        assert profile.web_credentials_version == 2
    assert case.http.get(ME).status_code == 401
    with TestClient(make_app(case.engine)) as fresh:
        fresh.cookies.set(auth.COOKIE_NAME, old_token, path=auth.COOKIE_PATH)
        assert fresh.get(ME).status_code == 401
    response = login(case, password="New-Web-B2!")
    assert response.status_code == (401 if clear else 200)


def test_verification_revoked_after_login(case):
    login(case)
    change_profile(case, is_verified=False)
    assert case.http.get(ME).status_code == 401


def test_every_throttle_step_and_no_password_check_while_blocked(case, monkeypatch):
    real_verify = auth.verify_password
    calls = []

    def verify(*args):
        calls.append(True)
        return real_verify(*args)

    monkeypatch.setattr(auth, "verify_password", verify)
    for attempt in range(1, 12):
        delay = 30 * 2 ** min(attempt - 1, 8)
        response = login(case, password="wrong")
        assert response.status_code == 401 and response.headers["retry-after"] == str(delay)
        assert len(calls) == attempt
        blocked = login(case)
        assert blocked.status_code == 429 and blocked.headers["retry-after"] == str(delay)
        assert len(calls) == attempt
        case.clock[0] += timedelta(seconds=delay)
    assert login(case).status_code == 200
    with Session(case.engine) as db:
        limit = db.scalar(select(WebLoginLimit))
        assert limit.failure_count == 0 and limit.blocked_until is None and limit.last_failure_at is None
    assert login(case, password="wrong").headers["retry-after"] == "30"


@pytest.mark.parametrize("elapsed,expected", [(89, "60"), (90, "30"), (91, "30")])
def test_failure_counter_resets_after_three_periods(case, elapsed, expected):
    assert login(case, password="wrong").status_code == 401
    case.clock[0] += timedelta(seconds=elapsed)
    assert login(case, password="wrong").headers["retry-after"] == expected


def test_limit_key_casefold_and_untrusted_forwarded_for(case):
    login(case, password="wrong")
    response = case.http.post(LOGIN, json={"login": "CLIENT.LOGIN", "password": PASSWORD},
                              headers={**ORIGIN, "X-Forwarded-For": "203.0.113.7"})
    assert response.status_code == 429
    with Session(case.engine) as db:
        assert db.scalar(select(WebLoginLimit)).source_ip == "testclient"
        # A different trusted peer has a separate limit.
        assert auth.login(db, "client.login", PASSWORD, "192.0.2.5")[1] == "client"


def test_concurrent_first_failures_are_serialized(case):
    def attempt(_):
        with TestClient(make_app(case.engine)) as http:
            return http.post(LOGIN, json={"login": "client.login", "password": "wrong"}, headers=ORIGIN).status_code

    with ThreadPoolExecutor(max_workers=4) as pool:
        results = list(pool.map(attempt, range(4)))
    assert sorted(results) == [401, 429, 429, 429]
    with Session(case.engine) as db:
        assert db.scalar(select(WebLoginLimit)).failure_count == 1


def test_concurrent_successes_issue_independent_sessions(case):
    def attempt(_):
        with TestClient(make_app(case.engine)) as http:
            response = http.post(LOGIN, json={"login": "client.login", "password": PASSWORD}, headers=ORIGIN)
            assert response.status_code == 200
            return http.cookies[auth.COOKIE_NAME]

    with ThreadPoolExecutor(max_workers=4) as pool:
        tokens = list(pool.map(attempt, range(4)))
    assert len(set(tokens)) == 4
    with Session(case.engine) as db:
        assert len(list(db.scalars(select(WebClientSession)))) == 4
        assert db.scalar(select(WebLoginLimit)).failure_count == 0


@pytest.mark.parametrize("elapsed,expected", [(179, "120"), (180, "30")])
def test_reset_uses_last_assigned_delay(case, elapsed, expected):
    assert login(case, password="wrong").headers["retry-after"] == "30"
    case.clock[0] += timedelta(seconds=30)
    assert login(case, password="wrong").headers["retry-after"] == "60"
    case.clock[0] += timedelta(seconds=elapsed)
    assert login(case, password="wrong").headers["retry-after"] == expected


def test_invalid_cookie_and_cookie_swapping(case):
    login(case)
    token = case.http.cookies[auth.COOKIE_NAME]
    case.http.cookies.clear()
    case.http.cookies.set("esdesk_session", token)
    assert case.http.get(ME).status_code == 401
    assert case.http.get("/staff-only").status_code == 401
    for invalid in ["random-invalid", "x" * 129]:
        case.http.cookies.set(auth.COOKIE_NAME, invalid, path=auth.COOKIE_PATH)
        assert case.http.get(ME).status_code == 401


def test_csrf_and_secure_production_cookie(case, monkeypatch):
    for origin in [None, "null", "http://evil.example", "http://testserver:444", "https://testserver"]:
        response = case.http.post(LOGIN, json={"login": "client.login", "password": PASSWORD},
                                  headers={"Origin": origin} if origin else {})
        assert response.status_code == 403
    assert login(case).status_code == 200
    assert case.http.post(LOGOUT).status_code == 403
    assert case.http.post("/api/v1/web/action", headers={"Origin": "http://evil.example"}).status_code == 403
    assert case.http.get(ME).status_code == 200
    monkeypatch.setattr(settings, "app_env", "prod")
    monkeypatch.setattr(settings, "app_public_base_url", "https://desk.example")
    with TestClient(make_app(case.engine), base_url="https://desk.example") as https:
        response = https.post(LOGIN, json={"login": "client.login", "password": PASSWORD},
                               headers={"Origin": "https://desk.example"})
        assert response.status_code == 200 and "Secure" in response.headers["set-cookie"]
        assert https.get(ME).status_code == 200
        assert https.post(LOGOUT, headers={"Origin": "https://desk.example"}).status_code == 204


@pytest.mark.parametrize("payload", [
    {"password": PASSWORD}, {"login": "client.login", "password": PASSWORD * 100},
    {"login": {"password": PASSWORD}, "password": PASSWORD},
])
def test_validation_does_not_echo_secrets(case, payload):
    response = case.http.post(LOGIN, json=payload, headers=ORIGIN)
    assert response.status_code == 422 and PASSWORD not in response.text


def test_schema_idempotence_foreign_keys_and_indexes(case):
    Base.metadata.create_all(case.engine)
    Base.metadata.create_all(case.engine)
    inspector = inspect(case.engine)
    assert {"web_client_sessions", "web_login_limits"} <= set(inspector.get_table_names())
    assert inspector.get_pk_constraint("web_login_limits")["constrained_columns"] == ["login_normalized", "source_ip"]
    assert inspector.get_foreign_keys("web_client_sessions")[0]["referred_table"] == "clients"
    assert {"ix_web_client_sessions_client_id", "ix_web_client_sessions_normal_expires_at"} <= {
        index["name"] for index in inspector.get_indexes("web_client_sessions")}
    with Session(case.engine) as db:
        db.add(WebClientSession(token_hash="x" * 64, client_id="missing", credentials_version=1,
                               created_at=case.clock[0], normal_expires_at=case.clock[0], last_activity_at=case.clock[0]))
        with pytest.raises(IntegrityError):
            db.commit()
