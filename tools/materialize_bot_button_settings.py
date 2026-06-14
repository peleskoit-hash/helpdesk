from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from api.app.db import SessionLocal
from api.app.services.bot_button_materialization import materialize_bot_button_settings


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Materialize current TXT button settings into BotButtonSetting overrides."
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Preview changes without writing to the database (default mode).",
    )
    parser.add_argument(
        "--apply",
        action="store_true",
        help="Apply changes to the database in one transaction.",
    )
    return parser


def main() -> int:
    parser = build_parser()
    args = parser.parse_args()
    apply = bool(args.apply)

    db = SessionLocal()
    try:
        summary = materialize_bot_button_settings(db, apply=apply)
    finally:
        db.close()

    mode = "apply" if apply else "dry-run"
    print(f"mode: {mode}")
    print(f"buttons_found: {summary.buttons_found}")
    print(f"rows_to_create: {summary.rows_to_create}")
    print(f"rows_created: {summary.rows_created}")
    print(f"rows_updated: {summary.rows_updated}")
    for field_name, count in summary.field_changes.items():
        print(f"field_changes.{field_name}: {count}")
    print(f"skipped_overrides: {summary.skipped_overrides}")
    print(f"skipped_db_created_or_archived: {summary.skipped_db_created_or_archived}")
    print(f"skipped_ambiguous: {summary.skipped_ambiguous}")
    print(f"apply_blocked: {summary.apply_blocked}")
    print(f"warnings: {len(summary.warnings)}")
    for warning in summary.warnings:
        print(f"warning: {warning}")
    if apply and summary.apply_blocked:
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
