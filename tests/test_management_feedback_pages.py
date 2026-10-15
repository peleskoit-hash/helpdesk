from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from api.app import main
from api.app.auth import CurrentUser
from api.app.db import get_db


ROOT = Path(__file__).resolve().parents[1]
PAGE_ROUTE = "/management/feedback"


def page_client(monkeypatch, role):
    user = None if role is None else CurrentUser(
        id="user-1", role=role, real_user_id="user-1",
        real_role=role, is_real_admin=role == "admin",
    )
    monkeypatch.setattr(main, "_require_page_auth", lambda request, db: user)
    app = FastAPI()
    for route in main.app.routes:
        if route.path == PAGE_ROUTE:
            app.add_api_route(route.path, route.endpoint, methods=["GET"])
    app.dependency_overrides[get_db] = lambda: None
    return TestClient(app)


@pytest.mark.parametrize("role", ["admin", "agent", "trainee", "chiki_piki", "observer"])
def test_management_feedback_page_is_admin_only(monkeypatch, role):
    response = page_client(monkeypatch, role).get(PAGE_ROUTE, follow_redirects=False)
    if role != "admin":
        assert response.status_code == 303
        assert response.headers["location"] == "/tickets"
        assert "/static/management_feedback.js" not in response.text
        return
    assert response.status_code == 200
    assert 'data-shell-page="management-feedback"' in response.text
    assert "/static/management_feedback.css?v=20261013a" in response.text
    assert "/static/management_feedback.js?v=20260909b" in response.text
    assert 'id="managementFeedbackSearch"' in response.text
    assert 'id="managementFeedbackTableBody"' in response.text


def test_management_feedback_page_redirects_anonymous_to_login(monkeypatch):
    response = page_client(monkeypatch, None).get(PAGE_ROUTE, follow_redirects=False)
    assert response.status_code == 303
    assert response.headers["location"] == "/login?next=/management/feedback"


def test_management_feedback_navigation_contract():
    shell = (ROOT / "api/app/static/app_shell.js").read_text(encoding="utf-8")
    management = (ROOT / "api/app/templates/management.html").read_text(encoding="utf-8")
    assert '["/management/feedback", { page: "management-feedback", scripts: ["/static/management_feedback.js?v=20260909b"] }]' in shell
    assert 'href="/management/feedback">Предложения и замечания</a>' in management
