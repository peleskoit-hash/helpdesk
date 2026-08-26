from __future__ import annotations

import asyncio
import logging

from ..config import settings
from ..db import SessionLocal
from .telegram_bot import get_updates

logger = logging.getLogger(__name__)


def _process_employee_update_sync(update: dict) -> dict:
    with SessionLocal() as db:
        from .employee_telegram_bot import process_employee_telegram_update

        return process_employee_telegram_update(db, update)


async def employee_telegram_polling_loop():
    offset: int | None = None

    while True:
        try:
            updates = await asyncio.to_thread(
                get_updates,
                offset,
                settings.employee_telegram_polling_timeout,
                bot_token=settings.employee_telegram_bot_token,
            )
            for update in updates:
                update_id = update.get("update_id")
                try:
                    await asyncio.to_thread(_process_employee_update_sync, update)
                except Exception:
                    logger.exception("Employee Telegram update processing failed")
                    continue
                if isinstance(update_id, int):
                    offset = update_id + 1
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            print(f"Employee Telegram polling error: {exc}")
            await asyncio.sleep(3)
