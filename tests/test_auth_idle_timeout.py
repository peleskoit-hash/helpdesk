from types import SimpleNamespace

import pytest
from fastapi import HTTPException, Request

from api.app import auth


@pytest.fixture
def session_case(monkeypatch):
    clock = [1_800_000_000]
    monkeypatch.setattr(auth, "time", SimpleNamespace(time=lambda: clock[0]))
    monkeypatch.setattr(auth.settings, "disable_admin_idle_timeout", False)
    user = SimpleNamespace(id="staff", role="agent", is_active=True, auth_version=0)
    monkeypatch.setattr(auth, "_get_user_by_id", lambda db, user_id: user)
    request = Request({"type": "http", "session": {}})
    auth.login_user(request, user)
    return request, user, clock


@pytest.mark.parametrize("legacy", [True, False])
def test_session_auth_version_revokes_old_cookie(session_case, legacy):
    request, user, _ = session_case
    assert request.session[auth.SESSION_AUTH_VERSION_KEY] == 0
    if legacy:
        del request.session[auth.SESSION_AUTH_VERSION_KEY]
    assert auth.get_current_user(request, db=None).id == user.id
    user.auth_version = 1
    with pytest.raises(HTTPException) as exc:
        auth.get_current_user(request, db=None)
    assert exc.value.status_code == 401
    assert exc.value.detail["error"]["code"] == "session_revoked"
    assert request.session == {}
    auth.login_user(request, user)
    assert request.session[auth.SESSION_AUTH_VERSION_KEY] == 1
    assert auth.get_current_user(request, db=None).id == user.id


@pytest.mark.parametrize("role", ["agent", "admin", "chiki_piki"])
@pytest.mark.parametrize("idle_seconds", [12 * 60 * 60 - 1, 12 * 60 * 60, 12 * 60 * 60 + 1])
def test_staff_session_twelve_hour_idle_boundary(session_case, role, idle_seconds):
    request, user, clock = session_case
    user.role = role
    login_ts = clock[0]
    clock[0] += idle_seconds
    if idle_seconds > 12 * 60 * 60:
        with pytest.raises(HTTPException) as error:
            auth.get_current_user(request, db=None)
        assert error.value.status_code == 401
        assert error.value.detail["error"]["code"] == "session_expired"
        assert request.session == {}
    else:
        assert auth.get_current_user(request, db=None).id == user.id
        assert request.session[auth.SESSION_LAST_ACTIVITY_KEY] == clock[0]
        assert request.session[auth.SESSION_LOGIN_TS_KEY] == login_ts


@pytest.mark.parametrize("role", ["agent", "admin", "chiki_piki"])
def test_idle_timeout_exemption_still_applies_only_to_admin(session_case, monkeypatch, role):
    request, user, clock = session_case
    user.role = role
    monkeypatch.setattr(auth.settings, "disable_admin_idle_timeout", True)
    clock[0] += 13 * 60 * 60
    if role == "admin":
        assert auth.get_current_user(request, db=None).is_real_admin
        assert request.session[auth.SESSION_LAST_ACTIVITY_KEY] == clock[0]
    else:
        with pytest.raises(HTTPException) as error:
            auth.get_current_user(request, db=None)
        assert error.value.status_code == 401
        assert request.session == {}


def test_activity_renews_idle_window_without_resetting_login_time(session_case):
    request, user, clock = session_case
    login_ts = clock[0]
    for _ in range(2):
        clock[0] += 11 * 60 * 60
        assert auth.get_current_user(request, db=None).id == user.id
        assert request.session[auth.SESSION_LAST_ACTIVITY_KEY] == clock[0]
        assert request.session[auth.SESSION_LOGIN_TS_KEY] == login_ts
