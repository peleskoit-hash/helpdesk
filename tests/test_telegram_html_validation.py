from __future__ import annotations

import json
from pathlib import Path

import pytest

from api.app.services.telegram_text_format import TelegramHtmlValidationError, validate_telegram_html


CASES = json.loads((Path(__file__).with_name("telegram_html_validation_cases.json")).read_text(encoding="utf-8"))


@pytest.mark.parametrize("case", CASES, ids=[case["name"] for case in CASES])
def test_backend_telegram_html_validation_contract(case):
    if case["ok"]:
        validate_telegram_html(case["text"])
        return

    with pytest.raises(TelegramHtmlValidationError) as exc:
        validate_telegram_html(case["text"])
    assert case["message_contains"] in str(exc.value)
