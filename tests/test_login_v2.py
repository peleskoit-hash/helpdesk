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
            assert 'ES DESK+' in page.text and 'Вход в Helpdesk' not in page.text
            assert '<title>ES DESK+</title>' in page.text
            assert '/static/login-v2.css?v=20261028-studio-final-v2' in page.text
            assert '/static/login.js?v=20261028-studio-final-v2' in page.text
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
