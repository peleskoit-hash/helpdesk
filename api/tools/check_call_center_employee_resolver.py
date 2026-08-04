from __future__ import annotations

import json
import sys
import time
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.db import SessionLocal  # noqa: E402
from app.services import call_center_employee_resolver as resolver  # noqa: E402


def main() -> int:
    started = time.monotonic()
    if not resolver.configured():
        print(json.dumps({"status": "not_configured", "configured": False}, ensure_ascii=False))
        return 0

    ok = resolver.refresh(force=True)
    duration_ms = int((time.monotonic() - started) * 1000)
    with SessionLocal() as db:
        status = resolver.get_status(db)
    print(
        json.dumps(
            {
                "status": "ok" if ok else "failed",
                "configured": bool(status.get("configured")),
                "duration_ms": duration_ms,
                "employee_count": status.get("employee_count"),
                "assigned_employee_count": status.get("assigned_employee_count"),
                "department_ids": status.get("department_ids"),
                "last_error_code": status.get("last_error_code"),
            },
            ensure_ascii=False,
            sort_keys=True,
        )
    )
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
