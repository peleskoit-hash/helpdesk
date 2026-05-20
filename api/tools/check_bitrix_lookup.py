from __future__ import annotations

import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.services.bitrix_client import (  # noqa: E402
    BitrixLookupError,
    extract_deal_id,
    get_deal_phone_lookup,
    validate_deal_id,
)


def flag(value: object) -> str:
    return "present" if value else "missing"


def main() -> int:
    if len(sys.argv) != 2:
        print("usage: python tools/check_bitrix_lookup.py DEAL_ID_OR_DEAL_URL")
        return 2

    deal_id = extract_deal_id(sys.argv[1])
    if not deal_id or not validate_deal_id(deal_id):
        print("deal_id: invalid")
        print("deal ok/error: error")
        return 1

    print(f"deal_id: {deal_id}")
    try:
        result = get_deal_phone_lookup(deal_id)
    except BitrixLookupError as exc:
        print("deal ok/error: error")
        print(f"error: {exc}")
        return 1

    print("deal ok/error: ok")
    print(f"contact_id: {flag(result.contact_id)}")
    print(f"lead_id: {flag(result.lead_id)}")
    print(f"phone: {flag(result.phone)}")
    print(f"email: {flag(result.email)}")
    print(f"source: {result.source or 'missing'}")
    if result.errors:
        print(f"errors: {', '.join(result.errors)}")
    if result.warnings:
        print(f"warnings: {', '.join(result.warnings)}")
    return 0 if result.phone else 1


if __name__ == "__main__":
    raise SystemExit(main())
