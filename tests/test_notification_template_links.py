from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from api.app.auth import get_current_user
from api.app.db import get_db
from api.app.models import MessageType
from api.app.routers import employee_notification_preferences, settings_notification_bot_basic
from api.app.services.telegram_text_format import TelegramHtmlValidationError, validate_telegram_html
from test_employee_notification_preferences import _admin_payload, _save_base, _user
from test_internal_task_completion_notification import (
    _base_entities, _capture_delivery, _close, _message, _session_factory,
)
from test_ticket_messages_outbound import _session_bundle


TEMPLATE = '<a href="[Ссылка на заявку]">[Задача]</a>'
BASE_URL = '/api/v1/settings/notification-bot/basic/internal_task_completion'
PERSONAL_URL = '/api/v1/me/notification-bot'


@pytest.fixture
def api():
    engine, _, db = _session_bundle()
    user = _user(db, role='admin')
    app = FastAPI()
    app.include_router(settings_notification_bot_basic.router)
    app.include_router(employee_notification_preferences.router)
    app.dependency_overrides[get_db] = lambda: db
    app.dependency_overrides[get_current_user] = lambda: user
    try:
        with TestClient(app) as client:
            # Keep the existing inheritance transition: first switch to a personal copy.
            result = client.put(PERSONAL_URL, json=_admin_payload(use_base_settings=False).model_dump())
            assert result.status_code == 200, result.text
            yield client, db
    finally:
        db.close()
        engine.dispose()


def save(client, personal, template):
    if personal:
        return client.put(PERSONAL_URL, json=_admin_payload(use_base_settings=False, template=template).model_dump())
    return client.put(BASE_URL, json={'enabled': True, 'html_enabled': True, 'template': template})


@pytest.mark.parametrize('personal', [False, True])
@pytest.mark.parametrize('template', [TEMPLATE, '<a href="https://example.com/task?x=1&amp;y=2">Задача</a>'])
def test_template_link_is_saved_and_reloaded(api, personal, template):
    client, db = api
    response = save(client, personal, template)
    assert response.status_code == 200, response.text
    db.expire_all()
    personal_item = client.get(PERSONAL_URL).json()['items'][0]
    if personal:
        assert personal_item['use_base_settings'] is False
        assert personal_item['template'] == template
    else:
        assert response.json()['template'] == template
        base = client.get('/api/v1/settings/notification-bot/basic').json()['items']
        assert next(item for item in base if item['type'] == 'internal_task_completion')['template'] == template
        # Saving the base must not overwrite the existing independent personal copy.
        assert personal_item['use_base_settings'] is False
        assert personal_item['template'] != template


@pytest.mark.parametrize('personal', [False, True])
@pytest.mark.parametrize('template', [
    '<a href="javascript:alert(1)">Задача</a>',
    '<a href="[Произвольный плейсхолдер]">Задача</a>',
    '<a href="[Задача]">Задача</a>',
    '<a href="[Ссылка на заявку]" onclick="alert(1)">[Задача]</a>',
    '<a href="[Ссылка на заявку]" href="https://example.com">[Задача]</a>',
    '<a href="[Ссылка на заявку]"><b>[Задача]</a></b>',
    '<a href="[Ссылка на заявку]">[Задача]',
])
def test_unsafe_template_links_and_broken_html_remain_rejected(api, personal, template):
    client, _ = api
    response = save(client, personal, template)
    assert response.status_code == 422, response.text
    assert response.json()['detail']['error']['code'] == 'notification_template_invalid'


@pytest.mark.parametrize('personal', [False, True])
def test_saved_template_substitutes_link_before_sending(monkeypatch, personal):
    factory = _session_factory()
    with factory() as db:
        data = _base_entities(db, pref_enabled=True, use_base_internal_task_completion_settings=not personal)
        texts, files = _capture_delivery(monkeypatch, factory)
        if personal:
            employee_notification_preferences.update_my_notification_bot_preferences(
                _admin_payload(use_base_settings=False, template=TEMPLATE), db=db,
                user=SimpleNamespace(id=data.responsible.id, role='admin'),
            )
        else:
            _save_base(db, 'internal_task_completion', TEMPLATE)
        _message(db, data.ticket, MessageType.AGENT.value, 'Final', data.agent.id)
        _close(db, data.ticket, data.agent)
        assert len(texts) == 1
        assert texts[0].startswith('<a href="https://desk.example.test/tickets/')
        assert texts[0].endswith('">Snapshot title</a>')
        assert '[Ссылка на заявку]' not in texts[0]
        assert '[Задача]' not in texts[0]
        validate_telegram_html(texts[0])
        assert files == []


def test_raw_placeholder_is_still_invalid_in_a_finished_telegram_message():
    with pytest.raises(TelegramHtmlValidationError):
        validate_telegram_html(TEMPLATE)
