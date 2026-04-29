import time
import asyncio
import uuid
from datetime import datetime
from pathlib import Path

from fastapi import FastAPI
from fastapi.responses import FileResponse
from fastapi.responses import RedirectResponse
from fastapi.staticfiles import StaticFiles
from sqlalchemy import text
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.exc import OperationalError

from .config import settings
from .db import Base, SessionLocal, engine
from .models import Client, Company, Ticket, TicketStatus, User
from .routers.telegram import router as telegram_router
from .routers.tickets import router as tickets_router
from .routers.users import router as users_router
from .services.button_config import clear_button_config_cache, load_button_config
from .services.telegram_bot import TelegramApiError, set_bot_commands
from .services.telegram_polling import telegram_polling_loop


BASE_DIR = Path(__file__).resolve().parent

app = FastAPI(title="Helpdesk MVP API", version="0.1.0")
app.include_router(tickets_router)
app.include_router(telegram_router)
app.include_router(users_router)
app.mount("/static", StaticFiles(directory=str(BASE_DIR / "static")), name="static")
telegram_polling_task: asyncio.Task | None = None


def _ensure_client_profile_columns():
    required_columns = {
        "last_name": "VARCHAR(200)",
        "is_verified": "BOOLEAN",
        "destination": "VARCHAR(200)",
        "sand_owner": "VARCHAR(200)",
        "sales_group": "VARCHAR(200)",
        "location_type": "VARCHAR(100)",
        "anydesk": "VARCHAR(100)",
        "bitrix_password": "VARCHAR(255)",
        "bitrix_id": "VARCHAR(100)",
    }

    with engine.begin() as conn:
        dialect = conn.dialect.name
        existing: set[str] = set()

        if dialect == "sqlite":
            rows = conn.execute(text("PRAGMA table_info(client_profiles)")).mappings().all()
            existing = {row["name"] for row in rows}
        else:
            rows = conn.execute(
                text(
                    """
                    SELECT column_name
                    FROM information_schema.columns
                    WHERE table_name = 'client_profiles'
                    """
                )
            ).fetchall()
            existing = {row[0] for row in rows}

        for column_name, column_type in required_columns.items():
            if column_name in existing:
                continue
            conn.execute(text(f"ALTER TABLE client_profiles ADD COLUMN {column_name} {column_type}"))
        conn.execute(text("UPDATE client_profiles SET is_verified = FALSE WHERE is_verified IS NULL"))


def _ensure_telegram_contact_columns():
    required_columns = {
        "external_username": "VARCHAR(100)",
        "external_display_name": "VARCHAR(200)",
    }

    with engine.begin() as conn:
        dialect = conn.dialect.name
        existing: set[str] = set()

        if dialect == "sqlite":
            rows = conn.execute(text("PRAGMA table_info(telegram_contacts)")).mappings().all()
            existing = {row["name"] for row in rows}
        else:
            rows = conn.execute(
                text(
                    """
                    SELECT column_name
                    FROM information_schema.columns
                    WHERE table_name = 'telegram_contacts'
                    """
                )
            ).fetchall()
            existing = {row[0] for row in rows}

        for column_name, column_type in required_columns.items():
            if column_name in existing:
                continue
            conn.execute(text(f"ALTER TABLE telegram_contacts ADD COLUMN {column_name} {column_type}"))


def _ensure_user_columns():
    required_columns = {
        "availability_status": "VARCHAR(40)",
        "last_assigned_at": "TIMESTAMP",
    }

    with engine.begin() as conn:
        dialect = conn.dialect.name
        existing: set[str] = set()

        if dialect == "sqlite":
            rows = conn.execute(text("PRAGMA table_info(users)")).mappings().all()
            existing = {row["name"] for row in rows}
        else:
            rows = conn.execute(
                text(
                    """
                    SELECT column_name
                    FROM information_schema.columns
                    WHERE table_name = 'users'
                    """
                )
            ).fetchall()
            existing = {row[0] for row in rows}

        for column_name, column_type in required_columns.items():
            if column_name not in existing:
                conn.execute(text(f"ALTER TABLE users ADD COLUMN {column_name} {column_type}"))
        conn.execute(text("UPDATE users SET availability_status = 'online' WHERE availability_status IS NULL"))


def _ensure_ticket_columns():
    required_columns = {
        "workflow_type": "VARCHAR(20)",
        "intake_state": "VARCHAR(20)",
        "client_close_message_id": "VARCHAR(100)",
        "task_kind": "VARCHAR(30)",
        "parent_ticket_id": "VARCHAR(36)",
        "agent_visible": "BOOLEAN",
        "load_counted": "BOOLEAN",
        "button_config_key": "VARCHAR(100)",
        "wrong_problem_hint": "TEXT",
    }

    with engine.begin() as conn:
        dialect = conn.dialect.name
        existing: set[str] = set()

        if dialect == "sqlite":
            rows = conn.execute(text("PRAGMA table_info(tickets)")).mappings().all()
            existing = {row["name"] for row in rows}
        else:
            rows = conn.execute(
                text(
                    """
                    SELECT column_name
                    FROM information_schema.columns
                    WHERE table_name = 'tickets'
                    """
                )
            ).fetchall()
            existing = {row[0] for row in rows}

        for column_name, column_type in required_columns.items():
            if column_name not in existing:
                conn.execute(text(f"ALTER TABLE tickets ADD COLUMN {column_name} {column_type}"))
        conn.execute(text("UPDATE tickets SET workflow_type = 'ticket' WHERE workflow_type IS NULL"))
        conn.execute(text("UPDATE tickets SET intake_state = 'ready' WHERE intake_state IS NULL"))
        conn.execute(text("UPDATE tickets SET task_kind = 'normal' WHERE task_kind IS NULL"))
        conn.execute(text("UPDATE tickets SET agent_visible = TRUE WHERE agent_visible IS NULL"))
        conn.execute(text("UPDATE tickets SET load_counted = TRUE WHERE load_counted IS NULL"))


def _ensure_ticket_message_columns():
    required_columns = {
        "agent_visible": "BOOLEAN",
        "telegram_message_id": "VARCHAR(100)",
        "message_status": "VARCHAR(20)",
        "status_note": "VARCHAR(255)",
        "replaced_message_id": "VARCHAR(36)",
    }

    with engine.begin() as conn:
        dialect = conn.dialect.name
        existing: set[str] = set()

        if dialect == "sqlite":
            rows = conn.execute(text("PRAGMA table_info(ticket_messages)")).mappings().all()
            existing = {row["name"] for row in rows}
        else:
            rows = conn.execute(
                text(
                    """
                    SELECT column_name
                    FROM information_schema.columns
                    WHERE table_name = 'ticket_messages'
                    """
                )
            ).fetchall()
            existing = {row[0] for row in rows}

        for column_name, column_type in required_columns.items():
            if column_name not in existing:
                conn.execute(text(f"ALTER TABLE ticket_messages ADD COLUMN {column_name} {column_type}"))
        conn.execute(text("UPDATE ticket_messages SET agent_visible = TRUE WHERE message_type != 'system' AND agent_visible IS NULL"))
        conn.execute(text("UPDATE ticket_messages SET agent_visible = FALSE WHERE message_type = 'system' AND agent_visible IS NULL"))
        conn.execute(text("UPDATE ticket_messages SET message_status = 'normal' WHERE message_status IS NULL"))


def _ensure_ticket_attachment_table():
    with engine.begin() as conn:
        dialect = conn.dialect.name
        if dialect == "sqlite":
            conn.execute(
                text(
                    """
                    CREATE TABLE IF NOT EXISTS ticket_attachments (
                        id VARCHAR(36) PRIMARY KEY,
                        ticket_id VARCHAR(36) NOT NULL,
                        message_id VARCHAR(36) NOT NULL,
                        source VARCHAR(40) NOT NULL,
                        telegram_file_id VARCHAR(255),
                        telegram_file_unique_id VARCHAR(255),
                        telegram_file_path VARCHAR(500),
                        original_filename VARCHAR(255),
                        mime_type VARCHAR(255),
                        file_size INTEGER,
                        local_path VARCHAR(1000) NOT NULL,
                        created_at TIMESTAMP
                    )
                    """
                )
            )
            return

        conn.execute(
            text(
                """
                CREATE TABLE IF NOT EXISTS ticket_attachments (
                    id VARCHAR(36) PRIMARY KEY,
                    ticket_id VARCHAR(36) NOT NULL REFERENCES tickets(id),
                    message_id VARCHAR(36) NOT NULL REFERENCES ticket_messages(id),
                    source VARCHAR(40) NOT NULL,
                    telegram_file_id VARCHAR(255),
                    telegram_file_unique_id VARCHAR(255),
                    telegram_file_path VARCHAR(500),
                    original_filename VARCHAR(255),
                    mime_type VARCHAR(255),
                    file_size INTEGER,
                    local_path VARCHAR(1000) NOT NULL,
                    created_at TIMESTAMP
                )
                """
            )
        )


def _ensure_test_agents():
    agent_specs = [
        ("00000000-0000-0000-0000-000000000001", "agent1@test.local", "Агент 1"),
        ("00000000-0000-0000-0000-000000000002", "agent2@test.local", "Агент 2"),
        ("00000000-0000-0000-0000-000000000003", "agent3@test.local", "Агент 3"),
    ]

    with SessionLocal() as db:
        company_id = db.execute(select(Company.id).order_by(Company.created_at.asc()).limit(1)).scalar_one_or_none()
        if company_id is None:
            return

        for agent_id, email, full_name in agent_specs:
            user = db.execute(select(User).where(User.id == agent_id)).scalar_one_or_none()
            if user is None:
                db.add(
                    User(
                        id=agent_id,
                        company_id=company_id,
                        email=email,
                        full_name=full_name,
                        role="agent",
                        is_active=True,
                        availability_status="offline",
                    )
                )
                continue

            user.email = email
            user.full_name = full_name
            user.role = "agent"
            user.is_active = True
            if not user.availability_status:
                user.availability_status = "offline"
        db.commit()


@app.on_event("startup")
async def on_startup():
    global telegram_polling_task

    # Compose can start API before Postgres is ready.
    last_error = None
    for _ in range(20):
        try:
            Base.metadata.create_all(bind=engine)
            _ensure_client_profile_columns()
            _ensure_telegram_contact_columns()
            _ensure_user_columns()
            _ensure_ticket_columns()
            _ensure_ticket_message_columns()
            _ensure_ticket_attachment_table()
            _ensure_test_agents()
            last_error = None
            break
        except OperationalError as exc:
            last_error = exc
            await asyncio.sleep(1)

    if last_error is not None:
        raise last_error

    if settings.telegram_bot_token:
        try:
            set_bot_commands()
        except TelegramApiError as exc:
            print(f"Telegram command menu setup error: {exc}")

    with SessionLocal() as db:
        exists = db.execute(select(Company.id).limit(1)).first()
        if exists:
            if settings.telegram_polling_enabled and settings.telegram_bot_token and telegram_polling_task is None:
                telegram_polling_task = asyncio.create_task(telegram_polling_loop())
            return

        company_id = str(uuid.uuid4())
        admin_id = str(uuid.uuid4())
        agent_id = "00000000-0000-0000-0000-000000000001"
        client_id = str(uuid.uuid4())
        ticket_id = str(uuid.uuid4())

        try:
            db.add(Company(id=company_id, name="Test Company"))
            db.commit()

            db.add(User(id=admin_id, company_id=company_id, email="admin@test.local", full_name="Admin", role="admin", availability_status="online"))
            db.add(User(id=agent_id, company_id=company_id, email="agent1@test.local", full_name="Агент 1", role="agent", availability_status="online"))
            db.add(Client(id=client_id, company_id=company_id, display_name="Тест Клиент"))
            db.commit()

            db.add(
                Ticket(
                    id=ticket_id,
                    company_id=company_id,
                    client_id=client_id,
                    assignee_id=agent_id,
                    subject="Тестовая заявка",
                    status=TicketStatus.NEW.value,
                    is_frozen=False,
                    created_at=datetime.utcnow(),
                    updated_at=datetime.utcnow(),
                )
            )
            db.commit()
        except IntegrityError:
            db.rollback()

    if settings.telegram_polling_enabled and settings.telegram_bot_token and telegram_polling_task is None:
        telegram_polling_task = asyncio.create_task(telegram_polling_loop())


@app.on_event("shutdown")
async def on_shutdown():
    global telegram_polling_task

    if telegram_polling_task is not None:
        telegram_polling_task.cancel()
        try:
            await telegram_polling_task
        except asyncio.CancelledError:
            pass
        telegram_polling_task = None


@app.get("/health")
def health():
    return {"ok": True}


@app.post("/api/v1/button-config/reload")
def reload_button_config():
    clear_button_config_cache()
    config = load_button_config()
    return {
        "ok": True,
        "menus": len(config.menus),
        "buttons": len(config.buttons),
    }


@app.get("/")
def index():
    return RedirectResponse(url="/tickets")


@app.get("/tickets")
def tickets_page():
    return FileResponse(str(BASE_DIR / "templates" / "tickets.html"))


@app.get("/tickets/{ticket_id}")
def ticket_page(ticket_id: str):
    return FileResponse(str(BASE_DIR / "templates" / "ticket.html"))
