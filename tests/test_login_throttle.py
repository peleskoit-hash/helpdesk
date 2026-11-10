"""Real login routes, isolated databases, controllable clocks and trusted ASGI peers."""
import hashlib
from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import inspect, select
from sqlalchemy.orm import Session

from api.app import main
from api.app.auth import hash_password
from api.app.models import StaffLoginThrottleBucket, User, WebLoginLimit
from api.app.services import login_throttle
from test_web_auth import PASSWORD, ORIGIN, LOGIN, engine, case, make_app


def app_with_staff(engine):
    app = make_app(engine)
    app.add_api_route("/login", main.login_page, methods=["GET"])
    app.add_api_route("/login", main.login_submit, methods=["POST"])
    return app


@pytest.fixture
def flow(case, monkeypatch):
    class Clock:
        @staticmethod
        def now(tz):
            return case.clock[0]

    monkeypatch.setattr(main, "datetime", Clock)
    with Session(case.engine) as db:
        db.get(User, "staff").password_hash = hash_password(PASSWORD)
        db.commit()
    return SimpleNamespace(case=case, realm="staff", app=app_with_staff(case.engine),
                           login="staff", success=303)


def attempt(flow, *, identity=None, password="wrong", ip="192.0.2.1", headers=None, app=None):
    async def peer(scope, receive, send):
        if scope["type"] == "http":
            scope = {**scope, "client": (ip, 12345)}
        await (app or flow.app)(scope, receive, send)

    payload = {"login": flow.login if identity is None else identity, "password": password}
    with TestClient(peer) as http:
        if flow.realm == "web":
            return http.post(LOGIN, json=payload, headers={**ORIGIN, **(headers or {})})
        return http.post("/login", data={**payload, "next": "/profile?tab=security"},
                         headers=headers, follow_redirects=False)


def bucket(flow, scope="account", value=None):
    value = value or (flow.login if scope == "account" else "192.0.2.1")
    with Session(flow.case.engine) as db:
        return db.get(StaffLoginThrottleBucket, ("staff", scope, hashlib.sha256(value.encode()).hexdigest()))


def test_existing_policy_all_steps_cap_and_success_reset(flow):
    for count, delay in enumerate([0, 0, 0, 30, 60, 120, 240, 480, 960, 1920, 3840, 7680, 7680], 1):
        failed = attempt(flow)
        assert failed.status_code == 401
        assert failed.headers.get("retry-after") == (str(delay) if delay else None)
        assert bucket(flow).failure_count == min(count, 12)
        if delay:
            blocked = attempt(flow, password=PASSWORD)
            assert blocked.status_code == 429
            assert blocked.headers["retry-after"] == str(delay)
            flow.case.clock[0] += timedelta(seconds=delay)
    success = attempt(flow, password=PASSWORD)
    assert success.status_code == flow.success
    assert success.headers["location"] == "/profile?tab=security"
    for scope in ["account", "ip"]:
        state = bucket(flow, scope)
        assert state.failure_count == 0
        assert state.last_failure_at is None and state.blocked_until is None
    assert "retry-after" not in attempt(flow).headers


@pytest.mark.parametrize("elapsed,expected", [(179, "120"), (180, None)])
def test_reset_after_three_last_assigned_periods(flow, elapsed, expected):
    for _ in range(4):
        attempt(flow)
    flow.case.clock[0] += timedelta(seconds=30)
    assert attempt(flow).headers["retry-after"] == "60"
    flow.case.clock[0] += timedelta(seconds=elapsed)
    assert attempt(flow).headers.get("retry-after") == expected


@pytest.mark.parametrize("rotation", ["ip", "login"])
def test_rotating_one_dimension_cannot_bypass(flow, rotation):
    for i in range(4):
        options = {"ip": f"192.0.2.{i + 1}"} if rotation == "ip" else {"identity": f"unknown{i}"}
        response = attempt(flow, **options)
        assert response.status_code == 401
        assert response.headers.get("retry-after") == ("30" if i == 3 else None)
    options = {"ip": "203.0.113.99"} if rotation == "ip" else {"identity": "another-unknown"}
    blocked = attempt(flow, **options)
    assert blocked.status_code == 429 and blocked.headers["retry-after"] == "30"


@pytest.mark.parametrize("rotation", ["none", "ip", "login"])
def test_concurrent_requests_cannot_bypass_shared_buckets(flow, rotation):
    def concurrent(i):
        options = {"ip": f"192.0.2.{i + 1}"} if rotation == "ip" else {}
        if rotation == "login":
            options["identity"] = f"unknown{i}"
        return attempt(flow, **options).status_code

    with ThreadPoolExecutor(max_workers=7) as pool:
        statuses = list(pool.map(concurrent, range(7)))
    assert sorted(statuses) == [401] * 4 + [429] * 3
    state = bucket(flow, "ip" if rotation == "login" else "account")
    assert state.failure_count == 4
    assert login_throttle.as_utc(state.blocked_until) == flow.case.clock[0] + timedelta(seconds=30)


def test_blocked_requests_do_not_extend_deadline_or_failure_count(flow):
    for _ in range(4):
        attempt(flow)
    original = bucket(flow)
    for elapsed in [1, 10, 18]:
        flow.case.clock[0] += timedelta(seconds=elapsed)
        blocked = attempt(flow, password=PASSWORD, headers={"X-Forwarded-For": "203.0.113.99"})
        assert blocked.status_code == 429
        assert int(blocked.headers["retry-after"]) == 30 - int((flow.case.clock[0] - login_throttle.as_utc(original.last_failure_at)).total_seconds())
        current = bucket(flow)
        assert (current.failure_count, current.last_failure_at, current.blocked_until) == (
            original.failure_count, original.last_failure_at, original.blocked_until)
    flow.case.clock[0] += timedelta(seconds=1)
    assert attempt(flow, password=PASSWORD).status_code == flow.success


def test_blocked_requests_skip_password_check_and_retry_rounds_up(flow, monkeypatch):
    for _ in range(4):
        attempt(flow)
    flow.case.clock[0] += timedelta(seconds=29, microseconds=500000)

    def forbidden(*args, **kwargs):
        pytest.fail("Blocked requests must not check passwords")

    monkeypatch.setattr(main, "authenticate_user", forbidden)
    monkeypatch.setattr(main, "verify_password", forbidden)
    response = attempt(flow, password=PASSWORD)
    assert response.status_code == 429 and response.headers["retry-after"] == "1"


def test_inactivity_resets_even_maximum_block(flow):
    for delay in [0, 0, 0, 30, 60, 120, 240, 480, 960, 1920, 3840, 7680]:
        assert attempt(flow).status_code == 401
        flow.case.clock[0] += timedelta(seconds=delay)
    flow.case.clock[0] += timedelta(seconds=2 * 7680)
    assert "retry-after" not in attempt(flow).headers
    assert bucket(flow).failure_count == 1


def test_success_cannot_wash_ip_spray_budget(flow):
    for i in range(3):
        assert attempt(flow, identity=f"unknown{i}").status_code == 401
    assert attempt(flow, password=PASSWORD).status_code == flow.success
    assert bucket(flow, "ip").failure_count == 3
    response = attempt(flow, identity="next-unknown")
    assert response.status_code == 401 and response.headers["retry-after"] == "30"
    assert attempt(flow, password=PASSWORD).status_code == 429


def test_success_from_new_peer_resets_account_failures(flow):
    for i in range(3):
        assert attempt(flow, ip=f"192.0.2.{i + 1}").status_code == 401
    assert attempt(flow, password=PASSWORD, ip="203.0.113.50").status_code == flow.success
    assert bucket(flow).failure_count == 0
    assert "retry-after" not in attempt(flow, ip="203.0.113.51").headers


def test_state_survives_app_and_engine_restart(flow):
    for _ in range(4):
        attempt(flow)
    flow.case.engine.dispose()
    response = attempt(flow, app=app_with_staff(flow.case.engine))
    assert response.status_code == 429 and response.headers["retry-after"] == "30"


def test_forwarded_headers_cannot_choose_limit_ip(flow):
    for i in range(5):
        response = attempt(flow, identity=f"unknown{i}", headers={
            "X-Forwarded-For": f"203.0.113.{i}", "X-Real-IP": f"203.0.113.{i}",
            "Forwarded": f"for=203.0.113.{i}",
        })
        assert response.status_code == (401 if i < 4 else 429)
    assert bucket(flow, "ip").failure_count == 4


@pytest.mark.parametrize("identity_kind", ["known", "unknown", "disabled"])
def test_identity_existence_does_not_change_error_contract(flow, identity_kind):
    identity = flow.login
    if identity_kind == "unknown":
        identity = "absent"
    elif identity_kind == "disabled":
        with Session(flow.case.engine) as db:
            db.get(User, "staff").is_active = False
            db.commit()
    for i in range(5):
        response = attempt(flow, identity=identity)
        assert response.status_code == (401 if i < 4 else 429)
        assert response.headers.get("retry-after") == ("30" if i >= 3 else None)
        assert "Неверный логин или пароль, либо пользователь отключён." in response.text
        assert response.headers["cache-control"] == "no-store"


def test_web_and_staff_limit_states_are_independent(case, monkeypatch):
    class Clock:
        @staticmethod
        def now(tz):
            return case.clock[0]

    monkeypatch.setattr(main, "datetime", Clock)
    app = app_with_staff(case.engine)
    # Use the exact same attempted identity and peer in both realms.
    web = SimpleNamespace(case=case, realm="web", app=app, login="same-login")
    staff = SimpleNamespace(case=case, realm="staff", app=app, login="same-login")
    for _ in range(4):
        assert attempt(web).status_code == 401
    assert attempt(web).status_code == 429
    for _ in range(4):
        assert attempt(staff).status_code == 401
    assert attempt(staff).status_code == 429
    with Session(case.engine) as db:
        assert {row.realm for row in db.scalars(select(StaffLoginThrottleBucket))} == {"staff"}


def test_existing_web_pair_block_is_preserved(case):
    with Session(case.engine) as db:
        db.add(WebLoginLimit(login_normalized="client.login", source_ip="192.0.2.1", failure_count=5,
                             last_failure_at=case.clock[0], blocked_until=case.clock[0] + timedelta(seconds=60)))
        db.commit()
    flow = SimpleNamespace(case=case, realm="web", app=make_app(case.engine), login="client.login")
    assert attempt(flow).headers["retry-after"] == "60"
    case.clock[0] += timedelta(seconds=60)
    assert attempt(flow).headers["retry-after"] == "120"


def test_new_schema_is_additive_and_idempotent(case):
    from api.app.db import Base

    Base.metadata.create_all(case.engine)
    Base.metadata.create_all(case.engine)
    inspector = inspect(case.engine)
    assert inspector.get_pk_constraint("login_throttle_buckets")["constrained_columns"] == ["realm", "scope", "key_hash"]
    assert inspector.get_pk_constraint("web_login_limits")["constrained_columns"] == ["login_normalized", "source_ip"]


def test_dev_uvicorn_does_not_trust_forwarded_headers():
    import shlex
    import uvicorn

    command = next(line.strip().removeprefix("command: ") for line in Path("docker-compose.yml").read_text().splitlines()
                   if "command: uvicorn " in line)
    assert "--no-proxy-headers" in shlex.split(command)
    config = uvicorn.Config(lambda scope, receive, send: None, proxy_headers=False)
    config.load()
    from uvicorn.middleware.proxy_headers import ProxyHeadersMiddleware
    assert not isinstance(config.loaded_app, ProxyHeadersMiddleware)


def test_login_blocks_do_not_revoke_existing_sessions(flow):
    success = attempt(flow, password=PASSWORD)
    assert success.status_code == flow.success
    for _ in range(4):
        attempt(flow)
    assert attempt(flow).status_code == 429
    with TestClient(flow.app) as http:
        http.cookies.update(success.cookies)
        response = http.get("/staff-only")
    assert response.status_code == 200


def test_unknown_and_disabled_identities_still_execute_one_password_hash(flow, monkeypatch):
    from api.app import auth as staff_auth

    real_verify = staff_auth.verify_password
    calls = []

    def verify(*args):
        calls.append(True)
        return real_verify(*args)

    monkeypatch.setattr(staff_auth, "verify_password", verify)
    monkeypatch.setattr(main, "verify_password", verify)
    assert attempt(flow, identity="absent").status_code == 401
    assert len(calls) == 1
    assert attempt(flow).status_code == 401
    assert len(calls) == 2
    with Session(flow.case.engine) as db:
        db.get(User, "staff").is_active = False
        db.commit()
    assert attempt(flow).status_code == 401
    assert len(calls) == 3


def test_web_keeps_original_pair_scoped_policy_and_needs_no_staff_table(case):
    # WEB must work even before the STAFF schema has been installed.
    StaffLoginThrottleBucket.__table__.drop(case.engine)
    web = SimpleNamespace(case=case, realm="web", app=make_app(case.engine), login="client.login")
    for _ in range(4):
        assert attempt(web).status_code == 401
    assert attempt(web).status_code == 429
    assert attempt(web, ip="203.0.113.7", password=PASSWORD).status_code == 200
    other_login = attempt(web, identity="unknown")
    assert other_login.status_code == 401 and "retry-after" not in other_login.headers


def test_staff_does_not_modify_existing_web_limits(flow):
    deadline = flow.case.clock[0] + timedelta(seconds=7680)
    with Session(flow.case.engine) as db:
        db.add(WebLoginLimit(login_normalized="staff", source_ip="192.0.2.1", failure_count=12,
                             last_failure_at=flow.case.clock[0], blocked_until=deadline))
        db.commit()
    assert attempt(flow, password=PASSWORD).status_code == 303
    with Session(flow.case.engine) as db:
        limit = db.get(WebLoginLimit, ("staff", "192.0.2.1"))
        assert limit.failure_count == 12
        assert login_throttle.as_utc(limit.blocked_until) == deadline
