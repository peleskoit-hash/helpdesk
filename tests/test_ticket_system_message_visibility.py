"""The wrong-problem notice is visible without changing other history filters."""
from datetime import datetime
from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from api.app.auth import get_current_user
from api.app.db import Base, get_db
from api.app.models import Client, Company, Ticket, TicketMessage, User
from api.app.routers.tickets import router


NOTICE = "Подсказка по неверной проблеме отправлена клиенту"
OTHER_HIDDEN_PREFIXES = (
    "Ответственный изменён:", "На распределении", "Клиент начал создание обращения",
    "Клиент выбрал кнопку:", "Клиент нажал /start", "Заявка заморожена",
    "Заявка разморожена", "Заявка возвращена в работу.",
    "Сотрудник подсказал клиенту правильный пункт меню:", "Клиенту ушёл текст в бота:",
    "После завершения задачи клиенту будет отправлена подсказка:",
    "Клиент уведомлен об ограничении размера файлов Telegram Bot API",
)


@pytest.mark.parametrize("role", ["agent", "trainee", "admin"])
def test_history_exposes_old_and_new_wrong_problem_notices_only(role):
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    try:
        Base.metadata.create_all(engine)
        with Session(engine, autoflush=False) as db:
            db.add(Company(id="company", name="Synthetic"))
            db.flush()
            db.add(Client(id="client", company_id="company", display_name="Клиент"))
            db.add(User(id="viewer", company_id="company", role=role, full_name="Viewer"))
            db.flush()
            db.add(Ticket(id="ticket", ticket_number=1, company_id="company", client_id="client",
                          assignee_id="viewer", subject="Test", status="closed", agent_visible=True))
            db.flush()
            db.add(TicketMessage(id="old-notice", ticket_id="ticket", message_type="system", body=NOTICE,
                                 agent_visible=False, created_at=datetime(2025, 1, 1)))
            for index, prefix in enumerate(OTHER_HIDDEN_PREFIXES):
                db.add(TicketMessage(id=f"hidden-{index}", ticket_id="ticket", message_type="system",
                                     body=prefix + " подробности", agent_visible=False))
            db.add(TicketMessage(id="other-visible", ticket_id="ticket", message_type="system",
                                 body="Другое видимое системное сообщение", agent_visible=True))
            db.add(TicketMessage(id="client-text", ticket_id="ticket", message_type="client",
                                 body="Клиент нажал /start", agent_visible=True))
            db.commit()
            db.expunge_all()
            user = SimpleNamespace(id="viewer", role=role)
            app = FastAPI()
            app.include_router(router)
            app.dependency_overrides[get_db] = lambda: db
            app.dependency_overrides[get_current_user] = lambda: user
            with TestClient(app) as http:
                for new_message in [False, True]:
                    if new_message:
                        db.add(TicketMessage(id="new-notice", ticket_id="ticket", message_type="system",
                                             body=NOTICE, agent_visible=True))
                        db.commit()
                        db.expunge_all()
                    response = http.get("/api/v1/tickets/ticket")
                    assert response.status_code == 200, response.text
                    ids = {message["id"] for message in response.json()["messages"]}
                    expected = {"old-notice", "other-visible", "client-text"}
                    if new_message:
                        expected.add("new-notice")
                    if role == "admin":
                        expected.update(f"hidden-{index}" for index in range(len(OTHER_HIDDEN_PREFIXES)))
                    assert ids == expected
                    assert db.get(TicketMessage, "old-notice").agent_visible is False
                    bot_copy = db.get(TicketMessage, f"hidden-{OTHER_HIDDEN_PREFIXES.index('Клиенту ушёл текст в бота:')}")
                    assert bot_copy.agent_visible is False
    finally:
        engine.dispose()
