from __future__ import annotations

from types import SimpleNamespace

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from api.app.db import Base
from api.app.models import AutomaticMessageTemplate
from api.app.routers.settings_automatic_messages import list_automatic_messages
from api.app.services.automatic_messages import (
    get_automatic_message_definition,
    get_effective_automatic_message_text,
    serialize_automatic_message,
)


REVIEW_KEYS = {
    "telegram.review.rating_prompt": "Выбор оценки",
    "telegram.review.text_prompt": "Запрос текста отзыва",
    "telegram.review.attachment_no_text": "Вложение без текста",
    "telegram.review.attachment_caption": "Подпись к вложению сохранена",
    "telegram.review.thank_you": "Благодарность за отзыв",
}
FEEDBACK_KEYS = {
    "telegram.feedback.prompt": "Запрос предложения",
    "telegram.feedback.thank_you": "Благодарность за предложение",
    "telegram.feedback.text_required": "Нужен текст",
}
EXPECTED_DEFAULTS = {
    "telegram.review.rating_prompt": (
        "Оцените, пожалуйста, качество работы сотрудника. Выбрав от 1-5 ⭐.\n"
        "Или нажмите /start для создания новой задачи"
    ),
    "telegram.review.text_prompt": (
        "Если хотите, напишите комментарий к вашей оценке. \n"
        "Или нажмите /start для создания новой задачи"
    ),
    "telegram.review.attachment_no_text": (
        "Вложение не будет добавлено к отзыву. Отправьте текст сообщения или добавьте подпись к вложению.\n"
        "Или нажмите /start для создания новой задачи"
    ),
    "telegram.review.attachment_caption": (
        "Подпись к вложению сохранена как текст отзыва. Вы можете отправить другой текст или завершить отзыв нажав /start."
    ),
    "telegram.review.thank_you": "Спасибо за ваш отзыв!\n\nНажмите /start для создания новой задачи",
    "telegram.feedback.prompt": (
        "Напишите предложение или замечание одним текстовым сообщением, или нажмите /start для создания нового обращения"
    ),
    "telegram.feedback.thank_you": (
        "Спасибо! Ваше предложение или замечание сохранено. Нажмите /start для создания нового обращения"
    ),
    "telegram.feedback.text_required": (
        "Пожалуйста, отправьте предложение или замечание текстом, или нажмите /start для создания нового обращения"
    ),
}


def _session():
    engine = create_engine("sqlite:///:memory:", future=True)
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine, autoflush=False, autocommit=False)
    return Session()


def test_review_automatic_message_definitions_are_registered_and_listed():
    for key, title in REVIEW_KEYS.items():
        definition = get_automatic_message_definition(key)
        assert definition is not None
        assert definition.title == title
        assert definition.category == "reviews"
        assert definition.category_title == "Отзывы"

    response = list_automatic_messages(db=_session(), user=SimpleNamespace(role="admin"))
    listed = {item["key"]: item for item in response["items"] if item["key"].startswith("telegram.review.")}
    reviews_category = next(item for item in response["categories"] if item["category"] == "reviews")

    assert set(listed) == set(REVIEW_KEYS)
    assert reviews_category["category_title"] == "Отзывы"
    assert {item["key"] for item in reviews_category["items"]} == set(REVIEW_KEYS)


def test_feedback_automatic_message_definitions_are_registered_and_listed():
    for key, title in FEEDBACK_KEYS.items():
        definition = get_automatic_message_definition(key)
        assert definition is not None
        assert definition.title == title
        assert definition.category == "feedback"
        assert definition.category_title == "Предложения и замечания"

    response = list_automatic_messages(db=_session(), user=SimpleNamespace(role="admin"))
    listed = {item["key"]: item for item in response["items"] if item["key"].startswith("telegram.feedback.")}
    feedback_category = next(item for item in response["categories"] if item["category"] == "feedback")

    assert set(listed) == set(FEEDBACK_KEYS)
    assert feedback_category["category_title"] == "Предложения и замечания"
    assert {item["key"] for item in feedback_category["items"]} == set(FEEDBACK_KEYS)


def test_review_and_feedback_automatic_message_defaults_are_exact():
    for key, expected in EXPECTED_DEFAULTS.items():
        definition = get_automatic_message_definition(key)
        assert definition is not None
        assert definition.default_text == expected


def test_review_and_feedback_effective_text_uses_default_without_override():
    session = _session()

    for key, expected in EXPECTED_DEFAULTS.items():
        definition = get_automatic_message_definition(key)
        assert definition is not None
        assert get_effective_automatic_message_text(session, key) == expected
        payload = serialize_automatic_message(session, definition)
        assert payload["default_text"] == expected
        assert payload["effective_text"] == expected
        assert payload["override_text"] is None
        assert payload["is_overridden"] is False


def test_review_and_feedback_override_has_priority_over_default():
    session = _session()
    key = "telegram.feedback.prompt"
    override_text = "Пользовательский override"
    session.add(
        AutomaticMessageTemplate(
            id="override-1",
            key=key,
            body=override_text,
            is_overridden=True,
            html_enabled=True,
        )
    )
    session.commit()
    definition = get_automatic_message_definition(key)
    assert definition is not None

    assert get_effective_automatic_message_text(session, key) == override_text
    payload = serialize_automatic_message(session, definition)
    assert payload["default_text"] == EXPECTED_DEFAULTS[key]
    assert payload["effective_text"] == override_text
    assert payload["override_text"] == override_text
    assert payload["is_overridden"] is True
