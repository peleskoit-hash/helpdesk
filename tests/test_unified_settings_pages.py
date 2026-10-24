from urllib.parse import unquote

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from api.app import main
from api.app.auth import CurrentUser
from api.app.db import get_db


DESTINATIONS = (
    "/settings/users", "/settings/client-card", "/settings/bot-buttons",
    "/settings/notification-bot-basic", "/settings/automatic-messages",
    "/management/integrations", "/quick-replies", "/management/users",
    "/management/reviews", "/management/feedback", "/management/statistics",
    "/management/saved-files", "/mass-notifications",
)


@pytest.mark.parametrize("role", [None, "admin", "agent", "trainee", "chiki_piki"])
@pytest.mark.parametrize("path", ("/settings", "/management") + DESTINATIONS)
def test_settings_entrypoints_and_existing_permissions(monkeypatch, role, path):
    user = None if role is None else CurrentUser(
        id="test", role=role, real_user_id="test", real_role=role,
        is_real_admin=role == "admin",
    )
    monkeypatch.setattr(main, "_require_page_auth", lambda request, db: user)
    app = FastAPI()
    for route in main.app.routes:
        if route.path in ("/settings", "/management") + DESTINATIONS:
            app.add_api_route(route.path, route.endpoint, methods=["GET"])
    app.dependency_overrides[get_db] = lambda: None
    client = TestClient(app)
    response = client.get(path, follow_redirects=False)
    if role is None:
        assert response.status_code == 303
        assert unquote(response.headers["location"]) == f"/login?next={path}"
    elif role != "admin" and path != "/quick-replies":
        assert response.status_code == 303
        assert response.headers["location"] == "/tickets"
    elif path == "/management":
        assert response.status_code == 303
        assert response.headers["location"] == "/settings"
        response = client.get("/management?source=bookmark")
        assert response.url.path == "/settings"
        assert response.url.query == b"source=bookmark"
        assert 'data-shell-page="settings-home"' in response.text
    else:
        assert response.status_code == 200
        if path == "/settings":
            assert response.text.count('class="settings-home-link"') == 13
            for target in DESTINATIONS:
                assert f'href="{target}"' in response.text
