from __future__ import annotations

from ..config import settings
from ..models import Ticket


def public_ticket_url(ticket: Ticket) -> str | None:
    base_url = str(settings.app_public_base_url or "").strip()
    if not base_url or ticket.ticket_number is None:
        return None
    return f"{base_url.rstrip('/')}/tickets/{ticket.ticket_number}"
