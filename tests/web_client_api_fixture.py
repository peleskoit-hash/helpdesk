"""Test-only HTTP host for the EXISTING auth router; no DEV DB or main startup."""
import os
import socket
from datetime import datetime, timedelta, timezone

import uvicorn
from fastapi import Depends, FastAPI, Request
from fastapi.staticfiles import StaticFiles
from sqlalchemy import create_engine, delete, select
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool
from starlette.middleware.sessions import SessionMiddleware

from api.app.auth import hash_password
from api.app.config import settings
from api.app.db import Base, get_db
from api.app.models import (AutomaticMessageTemplate, BotButtonSetting, Client, ClientProfile, Company,
                            TelegramContact, Ticket, TicketMessage, WebClientSession, WebLoginLimit, WebTaskResult, WebTaskResultFile)
from api.app.routers.web_auth import router
from api.app.routers.web_menu import router as menu_router
from api.app.routers.web_tasks import router as task_router
from api.app.routers.web_task_results import router as results_router
from api.app.routers.web_history import router as history_router
from api.app.services import web_auth


def run():
    settings.app_env = "dev"
    settings.session_cookie_secure = False
    settings.telegram_bot_token = ''
    settings.employee_telegram_bot_token = ''
    settings.app_public_base_url = "http://127.0.0.1:8000"
    settings.web_public_base_url = os.environ.get("WEB_CLIENT_TEST_ORIGIN")
    clock_start = datetime(2026, 10, 19, 16, tzinfo=timezone.utc)
    clock = [clock_start]
    web_auth.utcnow = lambda: clock[0]
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(engine)
    with Session(engine) as db:
        db.add(Company(id="synthetic-company", name="Synthetic"))
        db.flush()
        db.add(Client(id="synthetic-client", company_id="synthetic-company", display_name="Synthetic"))
        db.flush()
        db.add(ClientProfile(id="synthetic-profile", client_id="synthetic-client", is_verified=True,
                             web_login="synthetic", web_login_normalized="synthetic",
                             web_password_hash=hash_password("Synthetic-Web-A1!"), web_credentials_version=1))
        db.add(TelegramContact(id="synthetic-contact", company_id="synthetic-company", client_id="synthetic-client",
                               external_user_id="123", external_chat_id="123"))
        db.add(BotButtonSetting(id='synthetic-button', button_key='synthetic-button', menu_type='Базовое',
                                workflow_type='task', button_path='Synthetic', button_label='Synthetic menu',
                                confirmation_enabled=True, confirmation_text='Synthetic confirmation'))
        for key, body in [('telegram.task.request_text', 'Synthetic description prompt'),
                          ('telegram.task.created', 'Synthetic task created'),
                          ('telegram.intake.task_timeout', 'Synthetic task expired'),
                          ('telegram.ticket.close.unassigned.client', 'Synthetic task cancelled')]:
            db.add(AutomaticMessageTemplate(id=key, key=key, body=body, is_overridden=True))
        db.commit()
    app = FastAPI()
    app.mount('/static', StaticFiles(directory='api/app/static'), name='static')
    app.add_middleware(SessionMiddleware, secret_key="synthetic-test-only", session_cookie="esdesk_session")
    app.include_router(router)
    app.include_router(menu_router)
    app.include_router(task_router)
    app.include_router(results_router)
    app.include_router(history_router)
    requests = []

    def get_test_db():
        with Session(engine) as db:
            yield db

    app.dependency_overrides[get_db] = get_test_db

    @app.middleware("http")
    async def record(request: Request, call_next):
        if request.url.path.startswith("/api/v1/web/"):
            requests.append({"path": request.url.path, "origin": request.headers.get("origin"),
                             "cookie_names": list(request.cookies),
                             "forwarded_for": request.headers.get("x-forwarded-for")})
        return await call_next(request)

    @app.post("/__test__/reset")
    def reset(db=Depends(get_db)):
        db.execute(delete(WebTaskResultFile))
        db.execute(delete(WebTaskResult))
        db.execute(delete(TicketMessage))
        db.execute(delete(Ticket))
        db.execute(delete(WebClientSession))
        db.execute(delete(WebLoginLimit))
        db.commit()
        clock[0] = clock_start
        requests.clear()
        return {}

    @app.post('/__test__/seed-results')
    def seed_results(db=Depends(get_db)):
        task_id = '11111111-1111-4111-8111-111111111111'
        file_id = '33333333-3333-4333-8333-333333333333'
        db.add(Ticket(id=task_id, company_id='synthetic-company', client_id='synthetic-client',
                      subject='Live Ticket subject', channel='web', workflow_type='task', intake_state='ready',
                      task_kind='normal', status='closed', created_at=clock[0] - timedelta(days=1)))
        db.flush()
        db.add(WebTaskResult(task_id=task_id, message_id=file_id, client_id='synthetic-client',
                            company_id='synthetic-company', text='Published final answer', closed_at=clock[0]))
        db.flush()
        db.add(WebTaskResultFile(id=file_id, result_task_id=task_id, filename='final.txt',
                                mime_type='text/plain', size=12, position=0, data=b'result bytes'))
        db.commit()
        return {}

    @app.post("/__test__/origin")
    async def origin(request: Request):
        settings.web_public_base_url = (await request.json())["origin"]
        return {}

    @app.post("/__test__/expire")
    def expire():
        clock[0] += timedelta(hours=2)
        return {}

    @app.post('/__test__/task-timeout')
    def task_timeout():
        clock[0] += timedelta(minutes=10)
        return {}

    @app.get("/__test__/state")
    def state(db=Depends(get_db)):
        return {"requests": requests, "app_public_base_url": settings.app_public_base_url,
                'tasks': [{'id': row.id, 'channel': row.channel, 'intake_state': row.intake_state,
                           'workflow_type': row.workflow_type} for row in db.scalars(select(Ticket))],
                'client_messages': [row.body for row in db.scalars(select(TicketMessage).where(TicketMessage.message_type == 'client'))],
                "activities": [row.last_activity_at.isoformat()
                for row in db.scalars(select(WebClientSession))]}

    @app.post("/__test__/shutdown")
    def shutdown():
        server.should_exit = True
        return {}

    sock = socket.socket()
    sock.bind(("127.0.0.1", int(os.environ.get("WEB_CLIENT_TEST_API_PORT", "0"))))
    server = uvicorn.Server(uvicorn.Config(app, log_level="critical", access_log=False))
    print(f"READY http://127.0.0.1:{sock.getsockname()[1]}", flush=True)
    try:
        server.run(sockets=[sock])
    finally:
        sock.close()
        engine.dispose()


if __name__ == "__main__":
    run()
