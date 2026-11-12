from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool
from starlette.middleware.sessions import SessionMiddleware

from api.app import main
from api.app.auth import hash_password
from api.app.db import Base, get_db
from api.app.models import Company, User


@pytest.mark.parametrize("saved_theme", ["light", "dark"])
def test_login_v2_real_routes_preserve_saved_appearance(saved_theme):
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(engine)
    with Session(engine) as db:
        db.add(Company(id="test", name="Test"))
        user = User(id="agent", company_id="test", full_name="Agent", login="agent", role="agent",
                    password_hash=hash_password("secret"), is_active=True, theme_preference=saved_theme,
                    accent_hue_light=19, accent_hue_dark=153, accent_strength_light=77, accent_strength_dark=91)
        db.add(user)
        db.commit()
        fields = ["theme_preference", "accent_hue_light", "accent_hue_dark", "accent_strength_light", "accent_strength_dark", "appearance_write_state_json"]
        saved = {key: getattr(user, key) for key in fields}
        app = FastAPI()
        app.add_middleware(SessionMiddleware, secret_key="test-only")
        app.add_api_route("/login", main.login_page, methods=["GET"])
        app.add_api_route("/login", main.login_submit, methods=["POST"])
        app.dependency_overrides[get_db] = lambda: db
        with TestClient(app) as client:
            page = client.get("/login?next=/profile")
            assert page.status_code == 200
            assert 'ES DESK' in page.text and 'Вход в Helpdesk' not in page.text
            assert '<title>ES DESK</title>' in page.text
            assert '/static/login-v2.css?v=20261028-studio-final-v2' in page.text
            assert '/static/login.js?v=20261112-lockout' in page.text
            assert '/static/theme.js' not in page.text
            assert 'autocomplete="username"' in page.text and 'autocomplete="current-password"' in page.text
            assert '{{' not in page.text
            failed = client.post("/login", data={"username": "agent", "password": "wrong", "next": "/profile"})
            assert failed.status_code == 401 and "Неверный логин" in failed.text
            assert 'value="agent"' in failed.text and 'value="wrong"' not in failed.text
            success = client.post("/login", data={"username": "agent", "password": "secret", "next": "/profile"}, follow_redirects=False)
            assert success.status_code == 303 and success.headers["location"] == "/profile"
            assert client.get("/login", follow_redirects=False).headers["location"] == "/tickets"
            db.refresh(user)
            assert {key: getattr(user, key) for key in fields} == saved
    engine.dispose()


def test_login_v2_uses_original_matching_image_canvases():
    import struct

    for name in ["неоновая_киберпанк_студия_es_desk.png", "esdesk_light_people.png"]:
        data = (Path("api/app/static") / name).read_bytes()
        assert data[:8] == b"\x89PNG\r\n\x1a\n"
        assert struct.unpack(">II", data[16:24]) == (1672, 941)


@pytest.fixture
def login_client():
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(engine)
    try:
        with Session(engine) as db:
            db.add(Company(id="test", name="Test"))
            db.add(User(id="agent", company_id="test", full_name="Agent", login="agent", role="agent",
                        password_hash=hash_password("secret"), is_active=True))
            db.commit()
            app = FastAPI()
            app.add_middleware(SessionMiddleware, secret_key="test-only")
            app.add_api_route("/login", main.login_page, methods=["GET"])
            app.add_api_route("/login", main.login_submit, methods=["POST"])
            app.dependency_overrides[get_db] = lambda: db
            with TestClient(app) as client:
                yield client
    finally:
        engine.dispose()


@pytest.mark.parametrize("field", ["login", "username"])
def test_login_escapes_attribute_values(login_client, field):
    payload = "\"><script>alert('x')</script>&"
    next_path = "/tickets?q=" + payload
    response = login_client.post("/login", data={field: payload, "password": "wrong", "next": next_path})
    assert response.status_code == 401
    escaped = "&quot;&gt;&lt;script&gt;alert(&#x27;x&#x27;)&lt;/script&gt;&amp;"
    assert f'value="{escaped}"' in response.text
    assert f'name="next" value="/tickets?q={escaped}"' in response.text
    assert payload not in response.text


def test_login_get_escapes_next_attribute(login_client):
    response = login_client.get("/login", params={"next": "/tickets?q=\"><script>alert('x')</script>&"})
    assert response.status_code == 200
    assert 'name="next" value="/tickets?q=&quot;&gt;&lt;script&gt;alert(&#x27;x&#x27;)&lt;/script&gt;&amp;"' in response.text
    assert '<script>alert(' not in response.text


@pytest.mark.parametrize("next_path", [
    "//evil.example/path", "///evil.example", "https://evil.example", "tickets", "",
    r"/\evil.example", r"/tickets\path", "/%2fevil.example", "/%252fevil.example",
    "/%5cevil.example", "/%255cevil.example", "/\tevil.example", "/%0a/evil.example",
    " //evil.example", "/tickets?x=%0d%0aLocation:evil", "/tickets?x=\x7f",
])
def test_login_rejects_unsafe_next_in_all_flows(login_client, next_path):
    page = login_client.get("/login", params={"next": next_path})
    assert page.status_code == 200
    assert 'name="next" value="/tickets"' in page.text
    failed = login_client.post("/login", data={"username": "agent", "password": "wrong", "next": next_path})
    assert failed.status_code == 401
    assert 'name="next" value="/tickets"' in failed.text
    success = login_client.post("/login", data={"username": "agent", "password": "secret", "next": next_path}, follow_redirects=False)
    assert success.status_code == 303
    assert success.headers["location"] == "/tickets"


@pytest.mark.parametrize("next_path", ["/profile", "/tickets?status=open&search=a%20b"])
def test_login_preserves_internal_next(login_client, next_path):
    from html import escape

    page = login_client.get("/login", params={"next": next_path})
    assert f'name="next" value="{escape(next_path, quote=True)}"' in page.text
    response = login_client.post("/login", data={"username": "agent", "password": "secret", "next": next_path}, follow_redirects=False)
    assert response.status_code == 303
    assert response.headers["location"] == next_path
