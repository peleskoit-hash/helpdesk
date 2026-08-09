from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_DIRS = ("api/app", "TG buttons", ".")
TEXT_EXTENSIONS = {
    ".py", ".js", ".css", ".html", ".md", ".txt", ".yml", ".yaml", ".json", ".ini", ".toml",
}
IGNORE_DIR_NAMES = {".git", ".venv", "__pycache__", "node_modules", ".mypy_cache", ".pytest_cache"}
MARKER_CHARS = set("РСÐÑÃ")
QUESTION_RUN_RE = re.compile(r"\?{5,}")


def iter_files(paths: list[str]) -> list[Path]:
    result: list[Path] = []
    for raw in paths:
        base = (ROOT / raw).resolve() if not Path(raw).is_absolute() else Path(raw)
        if not base.exists():
            continue
        if base.is_file():
            if base.suffix.lower() in TEXT_EXTENSIONS:
                result.append(base)
            continue
        for path in base.rglob("*"):
            if not path.is_file():
                continue
            if any(part in IGNORE_DIR_NAMES for part in path.parts):
                continue
            if path.suffix.lower() in TEXT_EXTENSIONS:
                result.append(path)
    return sorted(set(result))


def marker_score(text: str) -> int:
    return sum(1 for ch in text if ch in MARKER_CHARS)


def cyrillic_score(text: str) -> int:
    return sum(1 for ch in text if ("А" <= ch <= "я") or ch in {"Ё", "ё"})


def line_looks_mojibake(line: str) -> bool:
    old_m = marker_score(line)
    if old_m < 3:
        return False
    try:
        fixed = line.replace("\x98", "И").encode("cp1251").decode("utf-8")
    except Exception:
        return False
    new_m = marker_score(fixed)
    if new_m >= old_m - 1:
        return False
    return cyrillic_score(fixed) > cyrillic_score(line)


def line_has_lost_cyrillic(line: str) -> bool:
    runs = QUESTION_RUN_RE.findall(line)
    if not runs:
        return False
    total_questions = sum(len(run) for run in runs)
    longest_run = max(len(run) for run in runs)
    if total_questions < 8:
        return False
    if len(runs) >= 2:
        return True
    return longest_run >= 8 and any(marker in line for marker in {'"', "'", "`"})


def find_issues(path: Path) -> list[tuple[int, str]]:
    issues: list[tuple[int, str]] = []
    try:
        text = path.read_text(encoding="utf-8")
    except UnicodeDecodeError:
        issues.append((0, "Файл не читается как UTF-8"))
        return issues
    for lineno, line in enumerate(text.splitlines(), start=1):
        if line_looks_mojibake(line) or line_has_lost_cyrillic(line):
            issues.append((lineno, line.strip()))
    return issues


def safe_print(value: str) -> None:
    sys.stdout.buffer.write((value + "\n").encode("utf-8", errors="replace"))


def main() -> int:
    parser = argparse.ArgumentParser(description="Detect common mojibake in project text files")
    parser.add_argument("paths", nargs="*", default=list(DEFAULT_DIRS))
    args = parser.parse_args()

    files = iter_files(args.paths)
    found = False
    for file_path in files:
        rel = file_path.relative_to(ROOT)
        issues = find_issues(file_path)
        if not issues:
            continue
        found = True
        for lineno, line in issues:
            if lineno == 0:
                safe_print(f"{rel}: {line}")
            else:
                safe_print(f"{rel}:{lineno}: {line}")
    if found:
        safe_print("\nНайдены потенциально битые строки (mojibake). Исправьте кодировку на UTF-8.")
        return 1
    safe_print("Mojibake check passed: подозрительных строк не найдено.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
