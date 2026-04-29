from __future__ import annotations

import argparse
import re
import sys
from dataclasses import dataclass
from pathlib import Path

from app.services.button_config import ALLOWED_KINDS_RU, clear_button_config_cache, load_button_config


FIELD_DOC_NAME = "Использование полей.md"


@dataclass
class ValidationIssue:
    path: Path
    line: int
    message: str

    def render(self) -> str:
        return f"{self.path.name}:{self.line}: {self.message}"


def _line_number(raw: str, index: int) -> int:
    return raw.count("\n", 0, index) + 1


def _find_unclosed_quotes(raw: str, path: Path) -> list[ValidationIssue]:
    issues: list[ValidationIssue] = []
    quote_open = False
    quote_start = 0
    escaped = False

    for index, char in enumerate(raw):
        if char == "\\" and not escaped:
            escaped = True
            continue
        if char == '"' and not escaped:
            if quote_open:
                quote_open = False
            else:
                quote_open = True
                quote_start = index
        escaped = False

    if quote_open:
        issues.append(ValidationIssue(path, _line_number(raw, quote_start), "Не закрыта кавычка"))
    return issues


def _documented_fields(config_dir: Path) -> set[str]:
    doc = config_dir / FIELD_DOC_NAME
    if not doc.exists():
        return set()
    return set(re.findall(r"\{\{([a-zA-Z0-9_]+)\}\}", doc.read_text(encoding="utf-8")))


def _used_fields(raw: str) -> set[str]:
    return set(re.findall(r"\{\{([a-zA-Z0-9_]+)\}\}", raw))


def _validate_file(path: Path, config_dir: Path) -> list[ValidationIssue]:
    raw = path.read_text(encoding="utf-8")
    issues: list[ValidationIssue] = []
    issues.extend(_find_unclosed_quotes(raw, path))

    allowed_kinds = set(ALLOWED_KINDS_RU)
    documented_fields = _documented_fields(config_dir)

    header_pattern = re.compile(r"(?m)^\[([^\]]+)\]\s*-\s*тип\s*([^,\n]+)")
    headers = list(header_pattern.finditer(raw))
    if not headers:
        issues.append(ValidationIssue(path, 1, "Не найдено ни одного описания кнопки вида [Название] - тип ..."))

    detailed_labels: set[str] = set()
    for header in headers:
        label = header.group(1).strip()
        raw_kind = header.group(2).strip()
        detailed_labels.add(label)
        if raw_kind not in allowed_kinds:
            issues.append(
                ValidationIssue(
                    path,
                    _line_number(raw, header.start()),
                    f"Неизвестный тип кнопки '{raw_kind}'. Разрешено: {', '.join(sorted(allowed_kinds))}",
                )
            )

    before_details = raw.split("Далее", 1)[0]
    menu_labels = re.findall(r"\[([^\]]+)\]", before_details)
    for label in menu_labels:
        if label not in detailed_labels:
            index = before_details.find(f"[{label}]")
            issues.append(ValidationIssue(path, _line_number(before_details, index), f"Кнопка '{label}' есть в меню, но ниже нет её описания"))
    for label in sorted(detailed_labels - set(menu_labels)):
        index = raw.find(f"[{label}]")
        issues.append(
            ValidationIssue(
                path,
                _line_number(raw, index),
                f"Кнопка '{label}' описана ниже, но не добавлена в верхнее меню",
            )
        )

    for header_index, header in enumerate(headers):
        start = header.start()
        end = headers[header_index + 1].start() if header_index + 1 < len(headers) else len(raw)
        segment = raw[start:end]
        label = header.group(1).strip()
        raw_kind = header.group(2).strip()
        line = _line_number(raw, start)

        if raw_kind == "Подменю" and "Кнопки подменю:" not in segment:
            issues.append(ValidationIssue(path, line, f"У подменю '{label}' нет блока 'Кнопки подменю:'"))
        if raw_kind == "групповая задача" and "Подзадачи:" not in segment:
            issues.append(ValidationIssue(path, line, f"У групповой задачи '{label}' нет блока 'Подзадачи:'"))
        if raw_kind == "групповая задача" and f"Конец подзадач для кнопки [{label}]" not in segment:
            issues.append(ValidationIssue(path, line, f"У групповой задачи '{label}' нет строки 'Конец подзадач для кнопки [{label}]'"))

    if documented_fields:
        for field in sorted(_used_fields(raw) - documented_fields):
            index = raw.find("{{" + field + "}}")
            issues.append(ValidationIssue(path, _line_number(raw, index), f"Поле '{{{{{field}}}}}' не описано в '{FIELD_DOC_NAME}'"))

    return issues


def validate_configs(config_dir: Path) -> list[ValidationIssue]:
    issues: list[ValidationIssue] = []
    txt_files = sorted(config_dir.glob("*.txt"))
    if not txt_files:
        return [ValidationIssue(config_dir, 1, "В папке не найдено ни одного .txt файла конфигурации")]

    for path in txt_files:
        issues.extend(_validate_file(path, config_dir))

    if issues:
        return issues

    try:
        clear_button_config_cache()
        load_button_config()
    except Exception as exc:
        issues.append(ValidationIssue(config_dir, 1, f"Конфиг не собирается: {exc}"))
    return issues


def main() -> int:
    parser = argparse.ArgumentParser(description="Проверка конфигураций кнопок Telegram")
    parser.add_argument("--config-dir", default="/button-config")
    args = parser.parse_args()

    config_dir = Path(args.config_dir)
    issues = validate_configs(config_dir)
    if issues:
        print("Найдены ошибки в конфигурации кнопок:")
        for issue in issues:
            print("- " + issue.render())
        return 1

    print("Проверка пройдена: ошибок не найдено")
    return 0


if __name__ == "__main__":
    sys.exit(main())
