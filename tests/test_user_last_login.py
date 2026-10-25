from datetime import datetime, timezone
from types import SimpleNamespace

from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, inspect, text
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool
from starlette.middleware.sessions import SessionMiddleware

from api.app import main
from api.app.auth import hash_password
from api.app.db import Base, get_db
from api.app.models import Company, User
from api.app.routers.settings_users import list_users
from api.app.services import schema_evolution


def test_successful_login_persists_timestamp_and_failed_login_does_not(monkeypatch):
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(engine)
    with Session(engine) as db:
        db.add(Company(id="company", name="Test"))
        user = User(id="agent", company_id="company", full_name="Agent", login="agent", role="agent", password_hash=hash_password("secret"), is_active=True)
        never = User(id="never", company_id="company", full_name="Never", login="never", role="agent", is_active=True)
        db.add_all([user, never]); db.commit()
        updated_at = user.updated_at
        app = FastAPI()
        app.add_middleware(SessionMiddleware, secret_key="test-only")
        app.add_api_route("/login", main.login_submit, methods=["POST"])
        app.dependency_overrides[get_db] = lambda: db
        client = TestClient(app)
        assert client.post("/login", data={"login": "agent", "password": "wrong"}).status_code == 401
        assert user.last_login_at is None
        first = datetime(2026, 10, 25, 10, 30, tzinfo=timezone.utc)

        class Clock:
            @staticmethod
            def now(tz):
                return first

        monkeypatch.setattr(main, "datetime", Clock)
        assert client.post("/login", data={"login": "agent", "password": "secret"}, follow_redirects=False).status_code == 303
        db.refresh(user)
        assert user.last_login_at.replace(tzinfo=timezone.utc) == first
        assert user.updated_at == updated_at
        first = datetime(2026, 10, 26, 11, 30, tzinfo=timezone.utc)
        client.post("/login", data={"login": "agent", "password": "secret"}, follow_redirects=False)
        db.refresh(user)
        assert user.last_login_at.replace(tzinfo=timezone.utc) == first
        user.is_active = False; db.commit()
        assert client.post("/login", data={"login": "agent", "password": "secret"}).status_code == 401
        db.refresh(user)
        assert user.last_login_at.replace(tzinfo=timezone.utc) == first
        output = {item.id: item for item in list_users(db, SimpleNamespace(role="admin", real_role="admin", is_real_admin=True))}
        assert output["agent"].last_login_at == user.last_login_at
        assert output["agent"].role == "agent"
        assert output["agent"].role_label == "Тех. поддержка"
        assert output["never"].last_login_at is None


def test_login_timestamp_schema_upgrade_is_nullable_and_idempotent(monkeypatch):
    engine = create_engine("sqlite://")
    with engine.begin() as conn:
        conn.execute(text("CREATE TABLE users (id VARCHAR(36) PRIMARY KEY, email VARCHAR(255))"))
        conn.execute(text("INSERT INTO users (id,email) VALUES ('old','old@test')"))
    monkeypatch.setattr(schema_evolution, "engine", engine)
    schema_evolution.ensure_user_columns()
    assert "last_login_at" in {col["name"] for col in inspect(engine).get_columns("users")}
    with engine.begin() as conn:
        assert conn.execute(text("SELECT last_login_at FROM users WHERE id='old'")).scalar() is None
        conn.execute(text("UPDATE users SET last_login_at='2026-10-25 10:30:00' WHERE id='old'"))
    schema_evolution.ensure_user_columns()
    with engine.begin() as conn:
        assert conn.execute(text("SELECT last_login_at FROM users WHERE id='old'")).scalar() == "2026-10-25 10:30:00"
