from enum import Enum
from datetime import datetime

from sqlalchemy import Boolean, DateTime, ForeignKey, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from .db import Base


class TicketStatus(str, Enum):
    UNASSIGNED = "unassigned"
    NEW = "new"
    PENDING = "pending"
    CLOSED = "closed"


class TicketWorkflowType(str, Enum):
    TICKET = "ticket"
    TASK = "task"
    AUTO = "auto"


class TicketTaskKind(str, Enum):
    NORMAL = "normal"
    GROUP_PARENT = "group_parent"
    GROUP_CHILD = "group_child"


class TicketIntakeState(str, Enum):
    COLLECTING = "collecting"
    READY = "ready"
    CANCELLED = "cancelled"


class MessageType(str, Enum):
    CLIENT = "client"
    AGENT = "agent"
    INTERNAL = "internal"
    SYSTEM = "system"


class Company(Base):
    __tablename__ = "companies"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    name: Mapped[str] = mapped_column(String(200))
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=datetime.utcnow)


class User(Base):
    __tablename__ = "users"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    company_id: Mapped[str] = mapped_column(ForeignKey("companies.id"))
    email: Mapped[str] = mapped_column(String(255))
    full_name: Mapped[str] = mapped_column(String(200))
    role: Mapped[str] = mapped_column(String(20))
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    availability_status: Mapped[str] = mapped_column(String(40), default="online")
    last_assigned_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class Client(Base):
    __tablename__ = "clients"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    company_id: Mapped[str] = mapped_column(ForeignKey("companies.id"))
    display_name: Mapped[str] = mapped_column(String(200))


class ClientProfile(Base):
    __tablename__ = "client_profiles"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    client_id: Mapped[str] = mapped_column(ForeignKey("clients.id"), unique=True)
    contact_name: Mapped[str | None] = mapped_column(String(200), nullable=True)
    last_name: Mapped[str | None] = mapped_column(String(200), nullable=True)
    email: Mapped[str | None] = mapped_column(String(255), nullable=True)
    phone: Mapped[str | None] = mapped_column(String(100), nullable=True)
    verification_status: Mapped[str | None] = mapped_column(String(100), nullable=True)
    is_verified: Mapped[bool] = mapped_column(Boolean, default=False)
    destination: Mapped[str | None] = mapped_column(String(200), nullable=True)
    sand_owner: Mapped[str | None] = mapped_column(String(200), nullable=True)
    sales_group: Mapped[str | None] = mapped_column(String(200), nullable=True)
    department: Mapped[str | None] = mapped_column(String(200), nullable=True)
    first_department: Mapped[str | None] = mapped_column(String(200), nullable=True)
    group_name: Mapped[str | None] = mapped_column(String(200), nullable=True)
    employee_type: Mapped[str | None] = mapped_column(String(200), nullable=True)
    location_type: Mapped[str | None] = mapped_column(String(100), nullable=True)
    anydesk: Mapped[str | None] = mapped_column(String(100), nullable=True)
    internal_number: Mapped[str | None] = mapped_column(String(100), nullable=True)
    external_user_id: Mapped[str | None] = mapped_column(String(100), nullable=True)
    bitrix_login: Mapped[str | None] = mapped_column(String(255), nullable=True)
    bitrix_password: Mapped[str | None] = mapped_column(String(255), nullable=True)
    bitrix_id: Mapped[str | None] = mapped_column(String(100), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=datetime.utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=datetime.utcnow)


class Ticket(Base):
    __tablename__ = "tickets"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    company_id: Mapped[str] = mapped_column(ForeignKey("companies.id"))
    client_id: Mapped[str] = mapped_column(ForeignKey("clients.id"))
    assignee_id: Mapped[str | None] = mapped_column(ForeignKey("users.id"), nullable=True)
    subject: Mapped[str] = mapped_column(String(255))
    workflow_type: Mapped[str] = mapped_column(String(20), default=TicketWorkflowType.TICKET.value)
    intake_state: Mapped[str] = mapped_column(String(20), default=TicketIntakeState.READY.value)
    task_kind: Mapped[str] = mapped_column(String(30), default=TicketTaskKind.NORMAL.value)
    parent_ticket_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    agent_visible: Mapped[bool] = mapped_column(Boolean, default=True)
    load_counted: Mapped[bool] = mapped_column(Boolean, default=True)
    button_config_key: Mapped[str | None] = mapped_column(String(100), nullable=True)
    wrong_problem_hint: Mapped[str | None] = mapped_column(Text, nullable=True)
    client_close_message_id: Mapped[str | None] = mapped_column(String(100), nullable=True)
    status: Mapped[str] = mapped_column(String(20), default=TicketStatus.NEW.value)
    is_frozen: Mapped[bool] = mapped_column(Boolean, default=False)
    freeze_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    closed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=datetime.utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=datetime.utcnow)

    client: Mapped[Client] = relationship()
    messages: Mapped[list["TicketMessage"]] = relationship(back_populates="ticket", cascade="all, delete-orphan")


class TicketMessage(Base):
    __tablename__ = "ticket_messages"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    ticket_id: Mapped[str] = mapped_column(ForeignKey("tickets.id"))
    message_type: Mapped[str] = mapped_column(String(20))
    author_user_id: Mapped[str | None] = mapped_column(ForeignKey("users.id"), nullable=True)
    body: Mapped[str] = mapped_column(Text)
    agent_visible: Mapped[bool] = mapped_column(Boolean, default=True)
    telegram_message_id: Mapped[str | None] = mapped_column(String(100), nullable=True)
    message_status: Mapped[str] = mapped_column(String(20), default="normal")
    status_note: Mapped[str | None] = mapped_column(String(255), nullable=True)
    replaced_message_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=datetime.utcnow)

    ticket: Mapped[Ticket] = relationship(back_populates="messages")


class TicketView(Base):
    __tablename__ = "ticket_views"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    ticket_id: Mapped[str] = mapped_column(ForeignKey("tickets.id"))
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id"))
    first_opened_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=datetime.utcnow)
    last_opened_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=datetime.utcnow)


class TelegramContact(Base):
    __tablename__ = "telegram_contacts"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    company_id: Mapped[str] = mapped_column(ForeignKey("companies.id"))
    client_id: Mapped[str] = mapped_column(ForeignKey("clients.id"))
    external_user_id: Mapped[str] = mapped_column(String(100))
    external_chat_id: Mapped[str] = mapped_column(String(100), unique=True)
    external_username: Mapped[str | None] = mapped_column(String(100), nullable=True)
    external_display_name: Mapped[str | None] = mapped_column(String(200), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=datetime.utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=datetime.utcnow)


class TelegramUpdate(Base):
    __tablename__ = "telegram_updates"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    update_id: Mapped[str] = mapped_column(String(100), unique=True)
    external_chat_id: Mapped[str | None] = mapped_column(String(100), nullable=True)
    payload_json: Mapped[str] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=datetime.utcnow)


class PendingTaskIntent(Base):
    __tablename__ = "pending_task_intents"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    company_id: Mapped[str] = mapped_column(ForeignKey("companies.id"))
    client_id: Mapped[str] = mapped_column(ForeignKey("clients.id"))
    external_chat_id: Mapped[str] = mapped_column(String(100), unique=True)
    subject: Mapped[str] = mapped_column(String(255))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=datetime.utcnow)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
