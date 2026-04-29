from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field


class TicketListItem(BaseModel):
    id: str
    subject: str
    workflow_type: str = "ticket"
    intake_state: str = "ready"
    status: str
    is_frozen: bool
    seen_by_agent: bool
    client_name: str
    assignee_id: str | None = None
    assignee_name: str | None = None
    last_reply_by: str | None
    last_reply_at: datetime | None
    created_at: datetime
    updated_at: datetime


class TicketListResponse(BaseModel):
    items: list[TicketListItem]
    total: int
    page: int
    pages: int
    limit: int


class TicketMessageOut(BaseModel):
    id: str
    message_type: str
    body: str
    author_user_id: str | None
    author_name: str | None = None
    telegram_message_id: str | None = None
    message_status: str = "normal"
    status_note: str | None = None
    replaced_message_id: str | None = None
    created_at: datetime


class ClientProfileOut(BaseModel):
    telegram_username: str | None = None
    telegram_display_name: str | None = None
    contact_name: str | None = None
    last_name: str | None = None
    email: str | None = None
    phone: str | None = None
    verification_status: str | None = None
    is_verified: bool = False
    destination: str | None = None
    sand_owner: str | None = None
    sales_group: str | None = None
    department: str | None = None
    first_department: str | None = None
    group_name: str | None = None
    employee_type: str | None = None
    location_type: str | None = None
    anydesk: str | None = None
    internal_number: str | None = None
    external_user_id: str | None = None
    bitrix_login: str | None = None
    bitrix_password: str | None = None
    bitrix_id: str | None = None


class ClientProfileUpdate(BaseModel):
    contact_name: str | None = None
    last_name: str | None = None
    email: str | None = None
    phone: str | None = None
    verification_status: str | None = None
    is_verified: bool = False
    destination: str | None = None
    sand_owner: str | None = None
    sales_group: str | None = None
    department: str | None = None
    first_department: str | None = None
    group_name: str | None = None
    employee_type: str | None = None
    location_type: str | None = None
    anydesk: str | None = None
    internal_number: str | None = None
    external_user_id: str | None = None
    bitrix_login: str | None = None
    bitrix_password: str | None = None
    bitrix_id: str | None = None


class TicketCard(BaseModel):
    id: str
    subject: str
    workflow_type: str = "ticket"
    intake_state: str = "ready"
    status: str
    is_frozen: bool
    freeze_until: datetime | None
    closed_at: datetime | None
    assignee_id: str | None = None
    assignee_name: str | None = None
    client_name: str
    client_profile: ClientProfileOut
    messages: list[TicketMessageOut]


class TextPayload(BaseModel):
    text: str = Field(min_length=1)
    quote_message_id: str | None = None


class FreezePayload(BaseModel):
    freeze_until: datetime | None = None


class AssignTicketsPayload(BaseModel):
    ticket_ids: list[str] = Field(min_length=1)
    assignee_id: str | None = None


class WrongProblemPayload(BaseModel):
    button_key: str = Field(min_length=1)


class ErrorOut(BaseModel):
    error: dict


SortField = Literal["created_at", "updated_at"]
SortOrder = Literal["asc", "desc"]


AvailabilityStatus = Literal["offline", "online", "on_ticket", "waiting", "lunch"]


class UserStatusOut(BaseModel):
    id: str
    role: str
    full_name: str
    availability_status: AvailabilityStatus


class UserStatusUpdate(BaseModel):
    availability_status: AvailabilityStatus


class AgentOut(BaseModel):
    id: str
    full_name: str
    availability_status: AvailabilityStatus
