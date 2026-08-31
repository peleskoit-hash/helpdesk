from __future__ import annotations

from types import SimpleNamespace

import pytest
from fastapi import HTTPException
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from api.app.db import Base
from api.app.models import AutomaticMessageTemplate
from api.app.routers import settings_notification_bot_basic
from api.app.routers.settings_notification_bot_basic import NotificationBotBasicUpdatePayload
from api.app.services.automatic_messages import (
    NOTIFICATION_BOT_BASIC_TAGS,
    NOTIFICATION_BOT_BASIC_DEFINITIONS,
    get_notification_bot_basic_definition,
    list_notification_bot_basic_definitions,
    normalize_notification_bot_basic_semantic_tag_labels,
    notification_bot_basic_tag_items,
    notification_bot_basic_tag_labels_by_semantic,
    render_notification_bot_basic_semantic_tags,
    serialize_notification_bot_basic_template,
)


def _session():
    engine = create_engine("sqlite:///:memory:", future=True)
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine, autoflush=False, autocommit=False)
    return Session()


def _admin():
    return SimpleNamespace(id="admin-1", role="admin")


def _save(session, notification_type: str, **payload):
    return settings_notification_bot_basic.update_notification_bot_basic_template(
        notification_type,
        NotificationBotBasicUpdatePayload(**payload),
        db=session,
        user=_admin(),
    )


def test_initial_four_notification_types_exist_and_enabled_by_default(monkeypatch):
    monkeypatch.setattr(settings_notification_bot_basic, "_require_admin", lambda user: None)
    session = _session()

    result = settings_notification_bot_basic.list_notification_bot_basic_templates(db=session, user=_admin())

    assert [item["type"] for item in result["items"]] == [
        "new_assignment",
        "new_message",
        "transfer_to_agent",
        "transfer_from_agent",
    ]
    assert all(item["enabled"] is True for item in result["items"])


def test_initial_templates_match_specification():
    expected = {
        "new_assignment": (
            "Вам назначена новая [Тип заявки]\n\n"
            "Автор: [Автор заявки/задачи]\n"
            "Подразделение: [Подразделение автора]\n"
            "Заголовок: [Заголовок заявки]\n\n"
            "[Ссылка на заявку]"
        ),
        "new_message": (
            "Новое сообщение в заявке\n\n"
            "Автор: [Автор сообщения]\n"
            "Вид: [Вид сообщения]\n\n"
            "[Тело сообщения]\n\n"
            "[Ссылка на заявку]"
        ),
        "transfer_to_agent": (
            "Вам передана заявка\n\n"
            "Предыдущий агент: [Предыдущий агент]\n"
            "Заголовок: [Заголовок заявки]\n\n"
            "[Ссылка на заявку]"
        ),
        "transfer_from_agent": (
            "Ваша заявка передана\n\n"
            "Куда передана: [Куда передана заявка]\n"
            "Администратор: [Администратор перевода]\n"
            "Заголовок: [Заголовок заявки]\n\n"
            "[Ссылка на заявку]"
        ),
    }

    assert {
        definition.key.rsplit(".", 1)[-1]: definition.default_text
        for definition in NOTIFICATION_BOT_BASIC_DEFINITIONS
    } == expected


def test_runtime_tag_whitelist_keeps_real_display_labels_per_notification_type():
    assert NOTIFICATION_BOT_BASIC_TAGS == {
        "new_assignment": (
            "[Номер заявки]",
            "[Тип заявки]",
            "[Заголовок заявки]",
            "[Автор заявки/задачи]",
            "[Подразделение автора]",
            "[Дата создания]",
            "[Ссылка на заявку]",
        ),
        "new_message": (
            "[Номер заявки]",
            "[Тип заявки]",
            "[Заголовок заявки]",
            "[Автор сообщения]",
            "[Вид сообщения]",
            "[Тело сообщения]",
            "[Дата сообщения]",
            "[Ссылка на заявку]",
        ),
        "transfer_to_agent": (
            "[Номер заявки]",
            "[Тип заявки]",
            "[Заголовок заявки]",
            "[Предыдущий агент]",
            "[Администратор перевода]",
            "[Дата перевода]",
            "[Ссылка на заявку]",
        ),
        "transfer_from_agent": (
            "[Номер заявки]",
            "[Тип заявки]",
            "[Заголовок заявки]",
            "[Куда передана заявка]",
            "[Администратор перевода]",
            "[Дата перевода]",
            "[Ссылка на заявку]",
        ),
    }


def test_notification_type_settings_are_independent(monkeypatch):
    monkeypatch.setattr(settings_notification_bot_basic, "_require_admin", lambda user: None)
    session = _session()

    _save(session, "new_assignment", enabled=False, template="")

    disabled = serialize_notification_bot_basic_template(
        session,
        get_notification_bot_basic_definition("new_assignment"),
    )
    untouched = serialize_notification_bot_basic_template(
        session,
        get_notification_bot_basic_definition("new_message"),
    )
    assert disabled["enabled"] is False
    assert disabled["template"] == ""
    assert untouched["enabled"] is True
    assert untouched["template"] == get_notification_bot_basic_definition("new_message").default_text


def test_enabled_empty_template_is_rejected(monkeypatch):
    monkeypatch.setattr(settings_notification_bot_basic, "_require_admin", lambda user: None)
    session = _session()

    with pytest.raises(HTTPException) as exc:
        _save(session, "new_assignment", enabled=True, template="")

    assert exc.value.status_code == 422
    assert exc.value.detail["error"]["message"] == "Добавьте текст уведомления или отключите этот тип"


def test_disabled_empty_template_is_allowed(monkeypatch):
    monkeypatch.setattr(settings_notification_bot_basic, "_require_admin", lambda user: None)
    session = _session()

    result = _save(session, "new_assignment", enabled=False, template="")

    assert result["enabled"] is False
    assert result["template"] == ""


def test_static_text_without_tags_is_allowed(monkeypatch):
    monkeypatch.setattr(settings_notification_bot_basic, "_require_admin", lambda user: None)
    session = _session()

    result = _save(session, "new_message", enabled=True, template="Просто текст")

    assert result["template"] == "Просто текст"


def test_unknown_notification_type_is_rejected(monkeypatch):
    monkeypatch.setattr(settings_notification_bot_basic, "_require_admin", lambda user: None)
    session = _session()

    with pytest.raises(HTTPException) as exc:
        _save(session, "unknown", enabled=True, template="text")

    assert exc.value.status_code == 404


def test_link_preview_and_silent_flags_are_saved(monkeypatch):
    monkeypatch.setattr(settings_notification_bot_basic, "_require_admin", lambda user: None)
    session = _session()

    result = _save(
        session,
        "new_message",
        enabled=True,
        template="Новое <b>сообщение</b>",
        disable_link_preview=True,
        send_silently=True,
    )

    assert result["disable_link_preview"] is True
    assert result["send_silently"] is True
    row = session.query(AutomaticMessageTemplate).filter_by(key="telegram.notification_bot.basic.new_message").one()
    assert row.disable_link_preview is True
    assert row.send_silently is True


def test_invalid_telegram_markup_is_not_saved(monkeypatch):
    monkeypatch.setattr(settings_notification_bot_basic, "_require_admin", lambda user: None)
    session = _session()

    with pytest.raises(HTTPException) as exc:
        _save(session, "new_message", enabled=True, template="<b>broken")

    assert exc.value.status_code == 422
    assert session.query(AutomaticMessageTemplate).count() == 0


def test_valid_telegram_markup_is_saved(monkeypatch):
    monkeypatch.setattr(settings_notification_bot_basic, "_require_admin", lambda user: None)
    session = _session()

    result = _save(session, "new_message", enabled=True, template='<a href="https://example.com">Открыть</a>')

    assert result["template"] == '<a href="https://example.com">Открыть</a>'


def test_registry_extension_does_not_mix_with_runtime_automatic_messages():
    keys = {definition.key for definition in list_notification_bot_basic_definitions()}
    assert keys
    assert all(key.startswith("telegram.notification_bot.basic.") for key in keys)


def test_recipient_tg_username_semantic_tag_is_available_for_all_admin_types():
    session = _session()
    labels = notification_bot_basic_tag_labels_by_semantic(context="admin")

    assert labels["recipient_telegram_username"] == "[TG @username адресата]"
    for definition in list_notification_bot_basic_definitions():
        item = serialize_notification_bot_basic_template(session, definition)
        assert "[TG @username адресата]" in item["tags"]


def test_displayed_admin_tags_have_centralized_tooltip_descriptions_in_order():
    session = _session()

    expected = {
        "new_assignment": [
            ("[Номер заявки]", "Номер заявки или задачи в ES DESK."),
            ("[Тип заявки]", "Тип: заявка, задача, системная задача и т. п."),
            ("[Заголовок заявки]", "Заголовок заявки или задачи."),
            ("[Автор заявки/задачи]", "Сотрудник или система, создавшие заявку или задачу."),
            ("[Подразделение автора]", "Подразделение автора на момент создания."),
            ("[Дата создания]", "Дата и время создания заявки или задачи."),
            ("[Ссылка на заявку]", "Прямая ссылка на заявку или задачу в ES DESK."),
            ("[TG @username адресата]", "Telegram @username сотрудника, которому отправляется уведомление."),
        ],
        "new_message": [
            ("[Номер заявки]", "Номер заявки или задачи в ES DESK."),
            ("[Тип заявки]", "Тип: заявка, задача, системная задача и т. п."),
            ("[Заголовок заявки]", "Заголовок заявки или задачи."),
            ("[Автор сообщения]", "Кто отправил новое сообщение или комментарий."),
            ("[Вид сообщения]", "Тип сообщения: ответ или комментарий."),
            ("[Тело сообщения]", "Текст сообщения и информация о прикреплённых файлах."),
            ("[Дата сообщения]", "Дата и время отправки сообщения."),
            ("[Ссылка на заявку]", "Прямая ссылка на заявку или задачу в ES DESK."),
            ("[TG @username адресата]", "Telegram @username сотрудника, которому отправляется уведомление."),
        ],
        "transfer_to_agent": [
            ("[Номер заявки]", "Номер заявки или задачи в ES DESK."),
            ("[Тип заявки]", "Тип: заявка, задача, системная задача и т. п."),
            ("[Заголовок заявки]", "Заголовок заявки или задачи."),
            ("[Предыдущий агент]", "Сотрудник, от которого передали заявку."),
            ("[Администратор перевода]", "Кто выполнил перевод заявки. Для автоматики — «Система»."),
            ("[Дата перевода]", "Дата и время передачи заявки."),
            ("[Ссылка на заявку]", "Прямая ссылка на заявку или задачу в ES DESK."),
            ("[TG @username адресата]", "Telegram @username сотрудника, которому отправляется уведомление."),
        ],
        "transfer_from_agent": [
            ("[Номер заявки]", "Номер заявки или задачи в ES DESK."),
            ("[Тип заявки]", "Тип: заявка, задача, системная задача и т. п."),
            ("[Заголовок заявки]", "Заголовок заявки или задачи."),
            ("[Куда передана заявка]", "Новый сотрудник, «Распределение» или административный список."),
            ("[Администратор перевода]", "Кто выполнил перевод заявки. Для автоматики — «Система»."),
            ("[Дата перевода]", "Дата и время передачи заявки."),
            ("[Ссылка на заявку]", "Прямая ссылка на заявку или задачу в ES DESK."),
            ("[TG @username адресата]", "Telegram @username сотрудника, которому отправляется уведомление."),
        ],
    }

    for notification_type, expected_items in expected.items():
        item = serialize_notification_bot_basic_template(
            session,
            get_notification_bot_basic_definition(notification_type),
        )
        assert [(tag["label"], tag["description"]) for tag in item["tag_items"]] == expected_items
        assert [tag["label"] for tag in item["tag_items"]] == item["tags"]
        assert notification_bot_basic_tag_items(notification_type, context="admin") == item["tag_items"]


def test_recipient_tg_username_semantic_tag_substitution_is_shared_for_admin_and_personal_labels():
    text = "Admin: [TG @username адресата]\nPersonal: [Мой TG @username]"

    assert render_notification_bot_basic_semantic_tags(text, recipient_telegram_username="agent_one") == (
        "Admin: @agent_one\nPersonal: @agent_one"
    )
    assert render_notification_bot_basic_semantic_tags(text, recipient_telegram_username="@agent_two") == (
        "Admin: @agent_two\nPersonal: @agent_two"
    )


def test_notification_bot_basic_serializer_normalizes_template_and_default_template_by_context(monkeypatch):
    monkeypatch.setattr(settings_notification_bot_basic, "_require_admin", lambda user: None)
    session = _session()
    _save(session, "new_assignment", enabled=True, template="Привет, [TG @username адресата]")

    admin_item = serialize_notification_bot_basic_template(
        session,
        get_notification_bot_basic_definition("new_assignment"),
        tag_context="admin",
    )
    personal_item = serialize_notification_bot_basic_template(
        session,
        get_notification_bot_basic_definition("new_assignment"),
        tag_context="personal",
    )

    assert admin_item["template"] == "Привет, [TG @username адресата]"
    assert personal_item["template"] == "Привет, [Мой TG @username]"
    assert "[TG @username адресата]" not in personal_item["template"]
    assert personal_item["default_template"] == normalize_notification_bot_basic_semantic_tag_labels(
        get_notification_bot_basic_definition("new_assignment").default_text,
        context="personal",
    )


def test_notification_bot_basic_save_route_uses_put_not_patch():
    route = next(
        route
        for route in settings_notification_bot_basic.router.routes
        if getattr(route, "path", "") == "/api/v1/settings/notification-bot/basic/{notification_type}"
    )
    assert "PUT" in route.methods
    assert "PATCH" not in route.methods
