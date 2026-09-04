from __future__ import annotations

from types import SimpleNamespace

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from api.app.db import Base
from api.app.routers.settings_automatic_messages import list_automatic_messages
from api.app.services.automatic_messages import get_automatic_message_definition


REVIEW_KEYS = {
    "telegram.review.rating_prompt": "Выбор оценки",
    "telegram.review.text_prompt": "Запрос текста отзыва",
    "telegram.review.attachment_no_text": "Вложение без текста",
    "telegram.review.attachment_caption": "Подпись к вложению сохранена",
    "telegram.review.thank_you": "Благодарность за отзыв",
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
