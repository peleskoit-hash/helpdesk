"""Message eligibility shared by list counts and explicit read acknowledgements."""
from sqlalchemy import or_

from ..models import MessageType, TicketMessage


def readable_message_filters(user_id: str):
    return (
        TicketMessage.message_type.in_([MessageType.CLIENT.value, MessageType.AGENT.value, MessageType.INTERNAL.value]),
        TicketMessage.agent_visible.is_(True),
        or_(TicketMessage.author_user_id.is_(None), TicketMessage.author_user_id != user_id),
        or_(TicketMessage.message_status.is_(None), TicketMessage.message_status != "deleted"),
    )
