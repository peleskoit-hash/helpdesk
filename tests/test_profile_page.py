from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from api.app import main
from api.app.auth import CurrentUser
from api.app.db import get_db


ROOT = Path(__file__).resolve().parents[1]
PAGE_ROUTE = "/profile"


def page_client(monkeypatch, role):
    user = None if role is None else CurrentUser(
        id="user-1",
        role=role,
        real_user_id="user-1",
        real_role=role,
        is_real_admin=role == "admin",
    )
    monkeypatch.setattr(main, "_require_page_auth", lambda request, db: user)
    app = FastAPI()
    for route in main.app.routes:
        if route.path == PAGE_ROUTE:
            app.add_api_route(route.path, route.endpoint, methods=["GET"])
    app.dependency_overrides[get_db] = lambda: None
    return TestClient(app)


@pytest.mark.parametrize("role", ["admin", "agent", "trainee", "chiki_piki"])
def test_profile_page_is_available_to_authenticated_roles(monkeypatch, role):
    response = page_client(monkeypatch, role).get(PAGE_ROUTE, follow_redirects=False)

    assert response.status_code == 200
    assert 'data-shell-page="profile"' in response.text
    assert "/static/profile.js?v=20261025-roles-editor-v2" in response.text
    assert "Личные данные" in response.text


def test_profile_page_redirects_anonymous_to_login(monkeypatch):
    response = page_client(monkeypatch, None).get(PAGE_ROUTE, follow_redirects=False)

    assert response.status_code == 303
    assert response.headers["location"] == "/login?next=/profile"


def test_profile_shell_navigation_contract():
    shell = (ROOT / "api/app/static/app_shell.js").read_text(encoding="utf-8")
    template = (ROOT / "api/app/templates/profile.html").read_text(encoding="utf-8")

    assert '["/profile", { page: "profile", scripts: ["/static/profile.js?v=20261025-roles-editor-v2"] }]' in shell
    assert 'data-shell-page="profile"' in template
    assert 'id="profileRoot"' in template
    assert 'id="loginModal"' in template
    assert 'id="passwordModal"' in template
