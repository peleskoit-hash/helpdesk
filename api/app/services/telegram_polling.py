import asyncio

from ..config import settings
from ..db import SessionLocal
from ..models import TicketStatus, TicketTaskKind, TicketWorkflowType
from .telegram_bot import get_updates
from .telegram_bot import send_message


async def _notify_if_still_unassigned(ticket_id: str, chat_id: str):
    await asyncio.sleep(3)
    try:
        with SessionLocal() as db:
            from ..models import Ticket

            ticket = db.get(Ticket, ticket_id)
            if ticket is None or ticket.status != TicketStatus.UNASSIGNED.value:
                return
            workflow_type = ticket.workflow_type or TicketWorkflowType.TICKET.value
            if workflow_type != TicketWorkflowType.TICKET.value:
                return
            task_kind = ticket.task_kind or TicketTaskKind.NORMAL.value
            if task_kind in {TicketTaskKind.GROUP_PARENT.value, TicketTaskKind.GROUP_CHILD.value}:
                return
            send_message(chat_id, "Ваша заявка находится в очереди на распределение, ожидайте")
    except Exception as exc:
        print(f"Telegram delayed queue notification error: {exc}")




def _process_update_sync(update: dict) -> dict:
    with SessionLocal() as db:
        from ..routers.telegram import process_telegram_update

        return process_telegram_update(db, update, notify_client=True)


async def telegram_polling_loop():
    offset: int | None = None

    while True:
        try:
            with SessionLocal() as db:
                from ..routers.telegram import cleanup_expired_task_intents
                from .auto_close import cleanup_auto_close_tickets

                cleanup_expired_task_intents(db)
                cleanup_auto_close_tickets(db)

            updates = await asyncio.to_thread(get_updates, offset, settings.telegram_polling_timeout)
            for update in updates:
                update_id = update.get("update_id")
                if isinstance(update_id, int):
                    offset = update_id + 1

                result = await asyncio.to_thread(_process_update_sync, update)
                if result.get("status") == TicketStatus.UNASSIGNED.value and result.get("ticket_id") and result.get("external_chat_id"):
                    asyncio.create_task(
                        _notify_if_still_unassigned(
                            result["ticket_id"],
                            result["external_chat_id"],
                        )
                    )
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            print(f"Telegram polling error: {exc}")
            await asyncio.sleep(3)
