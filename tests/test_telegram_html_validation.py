from __future__ import annotations

import json
from pathlib import Path

import pytest

from api.app.services.telegram_text_format import (
    TelegramHtmlValidationError,
    telegram_html_code,
    telegram_html_link,
    telegram_html_text,
    validate_telegram_html,
)


CASES = json.loads((Path(__file__).with_name("telegram_html_validation_cases.json")).read_text(encoding="utf-8"))


@pytest.mark.parametrize("case", CASES, ids=[case["name"] for case in CASES])
def test_backend_telegram_html_validation_contract(case):
    if case["ok"]:
        validate_telegram_html(case["text"])
        return

    with pytest.raises(TelegramHtmlValidationError) as exc:
        validate_telegram_html(case["text"])
    assert case["message_contains"] in str(exc.value)


def test_telegram_html_helpers_preserve_falsey_non_none_values():
    assert telegram_html_text(None) == ""
    assert telegram_html_text(0) == "0"
    assert telegram_html_text(False) == "False"
    assert telegram_html_code(0) == "<code>0</code>"
    assert telegram_html_code(False) == "<code>False</code>"
    assert telegram_html_link(0, False) == '<a href="False">0</a>'
