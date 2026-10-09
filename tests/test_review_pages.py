from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from starlette.middleware.sessions import SessionMiddleware

from api.app import main
from api.app.auth import CurrentUser
from api.app.db import get_db
from api.app.config import settings
from api.app.models import User


ROOT = Path(__file__).resolve().parents[1]
PAGE_ROUTES = ("/reviews", "/management/reviews")


def test_auth_session_exposes_configured_app_timezone(monkeypatch):
    monkeypatch.setattr(settings, "app_timezone", "Asia/Tokyo")
    app = FastAPI()
    app.add_middleware(SessionMiddleware, secret_key="test-session-timezone")
    for route in main.app.routes:
        if route.path == "/api/v1/auth/session":
            app.add_api_route(route.path, route.endpoint, methods=["GET"])
    real = SimpleNamespace(id="real-admin", full_name="Admin User", login="admin", availability_status="online")
    effective = SimpleNamespace(id="agent-1", full_name="Agent User", login="agent", availability_status="waiting")
    users = {real.id: real, effective.id: effective}
    app.dependency_overrides[get_db] = lambda: SimpleNamespace(get=lambda model, user_id: users.get(user_id) if model is User else None)
    app.dependency_overrides[main.get_current_user] = lambda: CurrentUser(
        id=effective.id, role="agent", real_user_id=real.id, real_role="admin", is_real_admin=True,
    )
    response = TestClient(app).get("/api/v1/auth/session")
    assert response.status_code == 200
    payload = response.json()
    assert payload["app_timezone"] == settings.app_timezone
    assert payload["real_user"]["id"] == real.id
    assert payload["real_user"]["availability_status"] == "online"
    assert payload["effective_user"]["role"] == "agent"
    assert payload["effective_user"]["availability_status"] == "waiting"
    assert payload["is_real_admin"] is True
    assert payload["session_login_ts"] == 0


def page_client(monkeypatch, role):
    user = None if role is None else CurrentUser(
        id="user-1", role=role, real_user_id="user-1",
        real_role=role, is_real_admin=role == "admin",
    )
    monkeypatch.setattr(main, "_require_page_auth", lambda request, db: user)
    # Use the registered page routes without application startup or the DEV database.
    app = FastAPI()
    for route in main.app.routes:
        if route.path in PAGE_ROUTES:
            app.add_api_route(route.path, route.endpoint, methods=["GET"])
    app.dependency_overrides[get_db] = lambda: None
    return TestClient(app)


@pytest.mark.parametrize("role", ["admin", "agent", "trainee", "chiki_piki", "observer"])
@pytest.mark.parametrize("path", PAGE_ROUTES)
def test_review_page_roles_and_html(monkeypatch, role, path):
    client = page_client(monkeypatch, role)
    response = client.get(path, follow_redirects=False)
    allowed = role == "admin" if path == "/management/reviews" else role in {"agent", "trainee"}
    if not allowed:
        assert response.status_code == 303
        assert response.headers["location"] == "/tickets"
        assert "/static/reviews.js" not in response.text
        return
    assert response.status_code == 200
    assert 'data-shell-page="reviews"' in response.text
    assert '/static/sidebar.js?v=20261009a' in response.text
    assert '/static/reviews.js?v=20260907a' in response.text
    assert '/static/reviews.css?v=20260907b' in response.text
    assert 'id="reviewDateFrom" type="date" disabled' in response.text
    assert 'id="reviewTableBody"' in response.text
    assert 'class="status-panel hidden"' in response.text
    for availability_status in ("offline", "online", "on_ticket", "waiting", "lunch"):
        assert f'data-availability-status="{availability_status}"' in response.text


@pytest.mark.parametrize("path", PAGE_ROUTES)
def test_review_pages_redirect_anonymous_to_login(monkeypatch, path):
    response = page_client(monkeypatch, None).get(path, follow_redirects=False)
    assert response.status_code == 303
    assert response.headers["location"] == f"/login?next={path}"


def test_review_navigation_contract():
    shell = (ROOT / "api/app/static/app_shell.js").read_text(encoding="utf-8")
    management = (ROOT / "api/app/templates/management.html").read_text(encoding="utf-8")
    for path in PAGE_ROUTES:
        assert f'["{path}", {{ page: "reviews", scripts: ["/static/reviews.js?v=20260907a"] }}]' in shell
    assert 'href="/management/reviews"' in management
