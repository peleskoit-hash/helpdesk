from __future__ import annotations

import re
import subprocess
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
TEXT_EXTENSIONS = {
    ".py",
    ".js",
    ".css",
    ".html",
    ".md",
    ".txt",
    ".yml",
    ".yaml",
    ".json",
    ".ini",
    ".toml",
}
IGNORE_PARTS = {".git", ".venv", "__pycache__", "node_modules", ".mypy_cache", ".pytest_cache"}

# Typical mojibake fragments when UTF-8 text was decoded as cp1251/latin.
MOJIBAKE_RE = re.compile(r"(?:Р[А-яЁё]|С[А-яЁё]|Ð.|Ñ.)")


def _run_git(*args: str) -> str:
    proc = subprocess.run(
        ["git", *args],
        cwd=ROOT,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=False,
    )
    if proc.returncode != 0:
        sys.stdout.write(proc.stderr)
        raise SystemExit(proc.returncode)
    return proc.stdout


def _staged_files() -> list[Path]:
    out = _run_git("diff", "--cached", "--name-only", "--diff-filter=ACMR")
    files: list[Path] = []
    for raw in out.splitlines():
        raw = raw.strip()
        if not raw:
            continue
        path = (ROOT / raw).resolve()
        if not path.exists() or not path.is_file():
            continue
        if path.suffix.lower() not in TEXT_EXTENSIONS:
            continue
        if any(part in IGNORE_PARTS for part in path.parts):
            continue
        files.append(path)
    return files


def _looks_mojibake(line: str) -> bool:
    # Heuristic: require several suspicious fragments on one line to reduce false positives.
    return len(MOJIBAKE_RE.findall(line)) >= 3


def main() -> int:
    problems: list[str] = []
    files = _staged_files()
    for path in files:
        rel = path.relative_to(ROOT)
        data = path.read_bytes()
        if data.startswith(b"\xef\xbb\xbf"):
            problems.append(f"{rel}: UTF-8 BOM found (use UTF-8 without BOM)")
        try:
            text = data.decode("utf-8")
        except UnicodeDecodeError:
            problems.append(f"{rel}: not valid UTF-8")
            continue
        for idx, line in enumerate(text.splitlines(), start=1):
            if _looks_mojibake(line):
                problems.append(f"{rel}:{idx}: possible mojibake -> {line.strip()[:120]}")

    if problems:
        sys.stdout.write("Encoding guard failed:\n")
        for p in problems:
            sys.stdout.write(f"- {p}\n")
        sys.stdout.write(
            "\nFix encoding to UTF-8 (no BOM) and replace mojibake before commit.\n"
        )
        return 1
    sys.stdout.write("Encoding guard passed for staged text files.\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

