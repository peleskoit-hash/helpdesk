from __future__ import annotations

import importlib.util
from pathlib import Path


MODULE_PATH = Path(__file__).resolve().parents[1] / "tools" / "check_mojibake.py"
SPEC = importlib.util.spec_from_file_location("check_mojibake", MODULE_PATH)
check_mojibake = importlib.util.module_from_spec(SPEC)
assert SPEC is not None and SPEC.loader is not None
SPEC.loader.exec_module(check_mojibake)

NORMAL_MESSAGE = "".join(
    chr(code)
    for code in [
        0x41F,
        0x43E,
        0x43B,
        0x44C,
        0x437,
        0x43E,
        0x432,
        0x430,
        0x442,
        0x435,
        0x43B,
        0x44C,
        0x20,
        0x43D,
        0x435,
        0x20,
        0x43D,
        0x430,
        0x439,
        0x434,
        0x435,
        0x43D,
    ]
)
NORMAL_LINE = f'message = "{NORMAL_MESSAGE}"'


def test_normal_cyrillic_line_passes():
    assert check_mojibake.line_looks_mojibake(NORMAL_LINE) is False
    assert check_mojibake.line_has_lost_cyrillic(NORMAL_LINE) is False

def test_long_question_run_is_detected():
    assert check_mojibake.line_has_lost_cyrillic('message = "???????????? ?? ??????"') is True


def test_literal_short_mojibake_example_is_not_flagged():
    assert check_mojibake.line_has_lost_cyrillic("Example literal: `????`") is False


def test_fixed_files_no_longer_report_issues():
    root = Path(__file__).resolve().parents[1]
    files = [
        root / "api" / "app" / "routers" / "users.py",
        root / "api" / "app" / "routers" / "settings_bot_button_structure.py",
        root / "docs" / "CODEX_TOKEN_BUDGET.md",
        root / "tools" / "check_mojibake.py",
    ]
    for path in files:
        assert check_mojibake.find_issues(path) == []
