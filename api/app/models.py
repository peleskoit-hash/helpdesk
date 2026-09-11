from enum import Enum
from datetime import datetime

from sqlalchemy import JSON, Boolean, CheckConstraint, DateTime, ForeignKey, Index, Integer, String, Text, UniqueConstraint, text
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


class TicketReviewCycleState(str, Enum):
    OFFERED = "offered"
    AWAITING_RATING = "awaiting_rating"
    AWAITING_TEXT = "awaiting_text"
    AWAITING_TEXT_AFTER_CAPTION = "awaiting_text_after_caption"
    COMPLETED = "completed"
    CANCELLED = "cancelled"


class TicketFeedbackState(str, Enum):
    AWAITING_TEXT = "awaiting_text"
    COMPLETED = "completed"
    CANCELLED = "cancelled"


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
    email: Mapped[str | None] = mapped_column(String(255), nullable=True)
    login: Mapped[str | None] = mapped_column(String(100), unique=True, nullable=True)
    password_hash: Mapped[str | None] = mapped_column(String(255), nullable=True)
    avatar_path: Mapped[str | None] = mapped_column(String(500), nullable=True)
    full_name: Mapped[str] = mapped_column(String(200))
    role: Mapped[str] = mapped_column(String(20))
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    availability_status: Mapped[str] = mapped_column(String(40), default="online")
    send_by_enter: Mapped[bool] = mapped_column(Boolean, default=False, server_default=text("false"), nullable=False)
    legacy_ticket_count: Mapped[int] = mapped_column(default=0)
    last_assigned_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=datetime.utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=datetime.utcnow)


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
    custom_fields: Mapped[dict] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=datetime.utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=datetime.utcnow)


class Ticket(Base):
    __tablename__ = "tickets"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    ticket_number: Mapped[int | None] = mapped_column(nullable=True, unique=True)
    company_id: Mapped[str] = mapped_column(ForeignKey("companies.id"))
    client_id: Mapped[str] = mapped_column(ForeignKey("clients.id"))
    assignee_id: Mapped[str | None] = mapped_column(ForeignKey("users.id"), nullable=True)
    closed_by_user_id: Mapped[str | None] = mapped_column(ForeignKey("users.id"), nullable=True)
    completed_by_user_id: Mapped[str | None] = mapped_column(ForeignKey("users.id"), nullable=True)
    completed_counted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    call_center_employee_lookup_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    call_center_internal_number: Mapped[str | None] = mapped_column(String(100), nullable=True)
    call_center_employee_first_name: Mapped[str | None] = mapped_column(String(200), nullable=True)
    call_center_employee_last_name: Mapped[str | None] = mapped_column(String(200), nullable=True)
    call_center_employee_id: Mapped[str | None] = mapped_column(String(100), nullable=True)
    call_center_employee_bitrix_login: Mapped[str | None] = mapped_column(String(255), nullable=True)
    call_center_profile_snapshot_json: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    subject: Mapped[str] = mapped_column(String(255))
    workflow_type: Mapped[str] = mapped_column(String(20), default=TicketWorkflowType.TICKET.value)
    intake_state: Mapped[str] = mapped_column(String(20), default=TicketIntakeState.READY.value)
    task_kind: Mapped[str] = mapped_column(String(30), default=TicketTaskKind.NORMAL.value)
    parent_ticket_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    agent_visible: Mapped[bool] = mapped_column(Boolean, default=True)
    load_counted: Mapped[bool] = mapped_column(Boolean, default=True)
    button_config_key: Mapped[str | None] = mapped_column(String(100), nullable=True)
    instruction_button_key: Mapped[str | None] = mapped_column(String(400), nullable=True)
    instruction_title: Mapped[str | None] = mapped_column(String(255), nullable=True)
    instruction_text: Mapped[str | None] = mapped_column(Text, nullable=True)
    wrong_problem_hint: Mapped[str | None] = mapped_column(Text, nullable=True)
    wrong_problem_used_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    wrong_problem_button_key: Mapped[str | None] = mapped_column(String(100), nullable=True)
    wrong_problem_label: Mapped[str | None] = mapped_column(String(255), nullable=True)
    onsite_triggered: Mapped[bool] = mapped_column(Boolean, default=False)
    client_close_message_id: Mapped[str | None] = mapped_column(String(100), nullable=True)
    menu_message_id: Mapped[str | None] = mapped_column(String(100), nullable=True)
    is_special: Mapped[bool] = mapped_column(Boolean, default=False)
    special_assignee_id: Mapped[str | None] = mapped_column(ForeignKey("users.id"), nullable=True)
    special_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    auto_close_disabled: Mapped[bool] = mapped_column(Boolean, default=False)
    auto_close_started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    auto_close_due_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    auto_closed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    distribution_notice_sent_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    status: Mapped[str] = mapped_column(String(20), default=TicketStatus.NEW.value)
    is_frozen: Mapped[bool] = mapped_column(Boolean, default=False)
    freeze_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    closed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=datetime.utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=datetime.utcnow)

    client: Mapped[Client] = relationship()
    messages: Mapped[list["TicketMessage"]] = relationship(back_populates="ticket", cascade="all, delete-orphan")


class TicketReviewCycle(Base):
    __tablename__ = "ticket_review_cycles"
    __table_args__ = (
        Index("ix_ticket_review_cycles_ticket_id", "ticket_id"),
        Index("ix_ticket_review_cycles_agent_id", "agent_id"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    ticket_id: Mapped[str] = mapped_column(ForeignKey("tickets.id"), nullable=False)
    agent_id: Mapped[str] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    client_id: Mapped[str] = mapped_column(ForeignKey("clients.id"), nullable=False)
    state: Mapped[str] = mapped_column(String(40), nullable=False)
    review_current_message_id: Mapped[str | None] = mapped_column(String(100), nullable=True)
    review_id: Mapped[str | None] = mapped_column(
        ForeignKey("ticket_reviews.id", use_alter=True, name="fk_ticket_review_cycles_review_id"),
        nullable=True,
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=datetime.utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=datetime.utcnow)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    cancelled_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class TicketReview(Base):
    __tablename__ = "ticket_reviews"
    __table_args__ = (
        CheckConstraint("rating >= 1 AND rating <= 5", name="ck_ticket_reviews_rating_1_5"),
        UniqueConstraint("ticket_id", "agent_id", "version_number", name="uq_ticket_reviews_pair_version"),
        Index("uq_ticket_reviews_cycle_id", "cycle_id", unique=True),
        Index(
            "uq_ticket_reviews_current_pair",
            "ticket_id",
            "agent_id",
            unique=True,
            sqlite_where=text("is_current = 1"),
            postgresql_where=text("is_current IS TRUE"),
        ),
        Index("ix_ticket_reviews_ticket_id", "ticket_id"),
        Index("ix_ticket_reviews_agent_id", "agent_id"),
        Index("ix_ticket_reviews_rated_at", "rated_at"),
        Index("ix_ticket_reviews_agent_current_rated", "agent_id", "is_current", "rated_at"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    ticket_id: Mapped[str] = mapped_column(ForeignKey("tickets.id"), nullable=False)
    agent_id: Mapped[str] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    client_id: Mapped[str] = mapped_column(ForeignKey("clients.id"), nullable=False)
    cycle_id: Mapped[str] = mapped_column(ForeignKey("ticket_review_cycles.id"), nullable=False)
    previous_version_id: Mapped[str | None] = mapped_column(ForeignKey("ticket_reviews.id"), nullable=True)
    version_number: Mapped[int] = mapped_column(Integer, nullable=False)
    agent_name_snapshot: Mapped[str] = mapped_column(Text, nullable=False)
    client_name_snapshot: Mapped[str] = mapped_column(Text, nullable=False)
    ticket_subject_snapshot: Mapped[str] = mapped_column(Text, nullable=False)
    rating: Mapped[int] = mapped_column(Integer, nullable=False)
    review_text: Mapped[str | None] = mapped_column(Text, nullable=True)
    rated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    text_updated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    is_current: Mapped[bool] = mapped_column(Boolean, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=datetime.utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=datetime.utcnow)


class TicketFeedback(Base):
    __tablename__ = "ticket_feedback"
    __table_args__ = (
        UniqueConstraint("ticket_id", name="uq_ticket_feedback_ticket_id"),
        Index("ix_ticket_feedback_ticket_id", "ticket_id"),
        Index("ix_ticket_feedback_client_id", "client_id"),
        Index("ix_ticket_feedback_submitted_at", "submitted_at"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    ticket_id: Mapped[str] = mapped_column(ForeignKey("tickets.id"), nullable=False)
    client_id: Mapped[str] = mapped_column(ForeignKey("clients.id"), nullable=False)
    client_name_snapshot: Mapped[str] = mapped_column(Text, nullable=False)
    ticket_title_snapshot: Mapped[str] = mapped_column(Text, nullable=False)
    text: Mapped[str] = mapped_column(Text, nullable=False)
    submitted_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=datetime.utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=datetime.utcnow)


class TicketFeedbackInput(Base):
    __tablename__ = "ticket_feedback_inputs"
    __table_args__ = (
        Index("ix_ticket_feedback_inputs_ticket_id", "ticket_id"),
        Index("ix_ticket_feedback_inputs_client_id", "client_id"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    ticket_id: Mapped[str] = mapped_column(ForeignKey("tickets.id"), nullable=False)
    client_id: Mapped[str] = mapped_column(ForeignKey("clients.id"), nullable=False)
    state: Mapped[str] = mapped_column(String(40), nullable=False)
    current_message_id: Mapped[str | None] = mapped_column(String(100), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=datetime.utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=datetime.utcnow)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    cancelled_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


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
    attachments: Mapped[list["TicketAttachment"]] = relationship(
        back_populates="message",
        cascade="all, delete-orphan",
    )


class TicketAttachment(Base):
    __tablename__ = "ticket_attachments"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    ticket_id: Mapped[str] = mapped_column(ForeignKey("tickets.id"))
    message_id: Mapped[str] = mapped_column(ForeignKey("ticket_messages.id"))
    source: Mapped[str] = mapped_column(String(40))
    telegram_file_id: Mapped[str | None] = mapped_column(String(255), nullable=True)
    telegram_file_unique_id: Mapped[str | None] = mapped_column(String(255), nullable=True)
    telegram_file_path: Mapped[str | None] = mapped_column(String(500), nullable=True)
    telegram_delivery_status: Mapped[str | None] = mapped_column(String(20), nullable=True)
    telegram_message_id: Mapped[str | None] = mapped_column(String(100), nullable=True)
    telegram_archive_message_id: Mapped[str | None] = mapped_column(String(100), nullable=True)
    telegram_archive_info_message_id: Mapped[str | None] = mapped_column(String(100), nullable=True)
    telegram_media_group_id: Mapped[str | None] = mapped_column(String(255), nullable=True)
    original_filename: Mapped[str | None] = mapped_column(String(255), nullable=True)
    mime_type: Mapped[str | None] = mapped_column(String(255), nullable=True)
    file_size: Mapped[int | None] = mapped_column(nullable=True)
    local_path: Mapped[str] = mapped_column(String(1000))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=datetime.utcnow)
    message: Mapped[TicketMessage] = relationship(back_populates="attachments")


class TicketAttachmentPreload(Base):
    __tablename__ = "ticket_attachment_preloads"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    ticket_id: Mapped[str] = mapped_column(ForeignKey("tickets.id"))
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id"))
    original_filename: Mapped[str | None] = mapped_column(String(255), nullable=True)
    mime_type: Mapped[str | None] = mapped_column(String(255), nullable=True)
    file_size: Mapped[int | None] = mapped_column(nullable=True)
    local_path: Mapped[str] = mapped_column(String(1000))
    telegram_file_id: Mapped[str | None] = mapped_column(String(255), nullable=True)
    telegram_file_unique_id: Mapped[str | None] = mapped_column(String(255), nullable=True)
    archive_group_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    status: Mapped[str] = mapped_column(String(20), default="uploading")
    final_attachment_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    reply_to_message_id: Mapped[str | None] = mapped_column(String(100), nullable=True)
    prefer_caption: Mapped[bool] = mapped_column(Boolean, default=False)
    telegram_staging_info_message_id: Mapped[str | None] = mapped_column(String(100), nullable=True)
    telegram_staging_file_message_id: Mapped[str | None] = mapped_column(String(100), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=datetime.utcnow)


class IntegrationLog(Base):
    __tablename__ = "integration_logs"
    __table_args__ = (
        Index("ix_integration_logs_created_at", "created_at"),
        Index("ix_integration_logs_task_dedup", "source", "event_type", "result", "deal_id", "created_at"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=datetime.utcnow)
    source: Mapped[str] = mapped_column(String(100))
    event_type: Mapped[str] = mapped_column(String(100))
    result: Mapped[str] = mapped_column(String(30))
    code: Mapped[str] = mapped_column(String(100))
    description: Mapped[str] = mapped_column(Text)
    deal_id: Mapped[str | None] = mapped_column(String(255), nullable=True)
    task_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    bitrix_id: Mapped[str | None] = mapped_column(String(100), nullable=True)
    call_time: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    message_text: Mapped[str | None] = mapped_column(Text, nullable=True)
    telegram_message_id: Mapped[str | None] = mapped_column(String(100), nullable=True)


class TicketView(Base):
    __tablename__ = "ticket_views"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    ticket_id: Mapped[str] = mapped_column(ForeignKey("tickets.id"))
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id"))
    first_opened_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=datetime.utcnow)
    last_opened_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=datetime.utcnow)


class TelegramContact(Base):
    __tablename__ = "telegram_contacts"
    __table_args__ = (
        UniqueConstraint(
            "company_id",
            "external_user_id",
            name="uq_telegram_contacts_company_external_user",
        ),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    company_id: Mapped[str] = mapped_column(ForeignKey("companies.id"))
    client_id: Mapped[str] = mapped_column(ForeignKey("clients.id"))
    external_user_id: Mapped[str] = mapped_column(String(100))
    external_chat_id: Mapped[str] = mapped_column(String(100), unique=True)
    external_username: Mapped[str | None] = mapped_column(String(100), nullable=True)
    external_display_name: Mapped[str | None] = mapped_column(String(200), nullable=True)
    is_blocked: Mapped[bool] = mapped_column(Boolean, default=False, server_default="false", nullable=False)
    active_menu_message_id: Mapped[str | None] = mapped_column(String(100), nullable=True)
    menu_message_ids_json: Mapped[list | None] = mapped_column(JSON, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=datetime.utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=datetime.utcnow)


class TelegramNotificationAccount(Base):
    __tablename__ = "telegram_notification_accounts"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    telegram_user_id: Mapped[str] = mapped_column(String(100), unique=True)
    telegram_chat_id: Mapped[str | None] = mapped_column(String(100), nullable=True)
    telegram_username: Mapped[str | None] = mapped_column(String(100), nullable=True)
    telegram_display_name: Mapped[str | None] = mapped_column(String(200), nullable=True)
    bound_user_id: Mapped[str | None] = mapped_column(ForeignKey("users.id"), unique=True, nullable=True)
    request_status: Mapped[str] = mapped_column(String(30), default="pending", server_default=text("'pending'"), nullable=False)
    delivery_available: Mapped[bool] = mapped_column(Boolean, default=True, server_default=text("true"), nullable=False)
    is_blocked: Mapped[bool] = mapped_column(Boolean, default=False, server_default=text("false"), nullable=False)
    is_notification_bot_admin: Mapped[bool] = mapped_column(Boolean, default=False, server_default=text("false"), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=datetime.utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=datetime.utcnow)


class EmployeeNotificationPreference(Base):
    __tablename__ = "employee_notification_preferences"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id"), unique=True, nullable=False)
    use_base_new_assignment_settings: Mapped[bool] = mapped_column(Boolean, default=True, server_default=text("true"), nullable=False)
    use_base_new_message_settings: Mapped[bool] = mapped_column(Boolean, default=True, server_default=text("true"), nullable=False)
    personal_new_assignment_template: Mapped[str | None] = mapped_column(Text, nullable=True)
    personal_new_assignment_html_enabled: Mapped[bool] = mapped_column(Boolean, default=True, server_default=text("true"), nullable=False)
    personal_new_assignment_disable_link_preview: Mapped[bool] = mapped_column(Boolean, default=False, server_default=text("false"), nullable=False)
    personal_new_assignment_send_silently: Mapped[bool] = mapped_column(Boolean, default=False, server_default=text("false"), nullable=False)
    personal_new_message_template: Mapped[str | None] = mapped_column(Text, nullable=True)
    personal_new_message_html_enabled: Mapped[bool] = mapped_column(Boolean, default=True, server_default=text("true"), nullable=False)
    personal_new_message_disable_link_preview: Mapped[bool] = mapped_column(Boolean, default=False, server_default=text("false"), nullable=False)
    personal_new_message_send_silently: Mapped[bool] = mapped_column(Boolean, default=False, server_default=text("false"), nullable=False)
    receive_transfer_to_agent: Mapped[bool] = mapped_column(Boolean, default=True, server_default=text("true"), nullable=False)
    receive_transfer_from_agent: Mapped[bool] = mapped_column(Boolean, default=True, server_default=text("true"), nullable=False)
    receive_new_message: Mapped[bool] = mapped_column(Boolean, default=True, server_default=text("true"), nullable=False)
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


class ClientCardBlockConfig(Base):
    __tablename__ = "client_card_block_configs"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    key: Mapped[str] = mapped_column(String(100), unique=True)
    title: Mapped[str] = mapped_column(String(200))
    color_scheme: Mapped[str] = mapped_column(String(40), default="gray")
    sort_order: Mapped[int] = mapped_column(default=0)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    show_in_card: Mapped[bool] = mapped_column(Boolean, default=True)
    show_in_editor: Mapped[bool] = mapped_column(Boolean, default=True)
    show_title: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=datetime.utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=datetime.utcnow)


class ClientCardFieldConfig(Base):
    __tablename__ = "client_card_field_configs"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    key: Mapped[str] = mapped_column(String(100), unique=True)
    label: Mapped[str] = mapped_column(String(200))
    field_type: Mapped[str] = mapped_column(String(40), default="text")
    block_key: Mapped[str] = mapped_column(String(100))
    sort_order: Mapped[int] = mapped_column(default=0)
    is_visible: Mapped[bool] = mapped_column(Boolean, default=True)
    is_visible_in_editor: Mapped[bool] = mapped_column(Boolean, default=True)
    is_editable: Mapped[bool] = mapped_column(Boolean, default=True)
    is_required: Mapped[bool] = mapped_column(Boolean, default=False)
    is_system: Mapped[bool] = mapped_column(Boolean, default=True)
    options_json: Mapped[list | None] = mapped_column(JSON, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=datetime.utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=datetime.utcnow)


class ClientCardRuleConfig(Base):
    __tablename__ = "client_card_rule_configs"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    technical_key: Mapped[str | None] = mapped_column(String(100), unique=True, nullable=True)
    name: Mapped[str] = mapped_column(String(200))
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    is_system: Mapped[bool] = mapped_column(Boolean, default=False)
    sort_order: Mapped[int] = mapped_column(default=0)
    condition_field_key: Mapped[str] = mapped_column(String(100))
    condition_operator: Mapped[str] = mapped_column(String(40))
    condition_value: Mapped[str | None] = mapped_column(String(500), nullable=True)
    actions_json: Mapped[list | None] = mapped_column(JSON, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=datetime.utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=datetime.utcnow)


class ClientCardSourceConfig(Base):
    __tablename__ = "client_card_source_configs"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    technical_key: Mapped[str | None] = mapped_column(String(100), unique=True, nullable=True)
    name: Mapped[str] = mapped_column(String(200))
    kind: Mapped[str] = mapped_column(String(40), default="http")
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    method: Mapped[str] = mapped_column(String(10), default="GET")
    url_template: Mapped[str] = mapped_column(String(2000))
    query_template_json: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    headers_template_json: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    body_template_json: Mapped[dict | list | str | int | float | bool | None] = mapped_column(JSON, nullable=True)
    timeout_ms: Mapped[int] = mapped_column(default=3000)
    mapping_json: Mapped[list | None] = mapped_column(JSON, nullable=True)
    on_error: Mapped[str] = mapped_column(String(40), default="ignore")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=datetime.utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=datetime.utcnow)


class CallCenterEmployeeResolverState(Base):
    __tablename__ = "call_center_employee_resolver_state"

    key: Mapped[str] = mapped_column(String(100), primary_key=True)
    snapshot_json: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    metadata_json: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    fetched_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_attempt_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_success_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=datetime.utcnow)


class BotButtonSetting(Base):
    __tablename__ = "bot_button_settings"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    stable_button_id: Mapped[str | None] = mapped_column(String(36), unique=True, nullable=True)
    button_key: Mapped[str] = mapped_column(String(500), unique=True)
    legacy_button_key: Mapped[str | None] = mapped_column(String(500), nullable=True)
    menu_type: Mapped[str] = mapped_column(String(200))
    workflow_type: Mapped[str] = mapped_column(String(50))
    button_path: Mapped[str] = mapped_column(String(1000))
    button_label: Mapped[str] = mapped_column(String(255))
    label_override: Mapped[str | None] = mapped_column(String(255), nullable=True)
    label_overridden: Mapped[bool] = mapped_column(Boolean, default=False)
    currently_present: Mapped[bool] = mapped_column(Boolean, default=True)
    instruction_text: Mapped[str | None] = mapped_column(Text, nullable=True)
    client_text: Mapped[str | None] = mapped_column(Text, nullable=True)
    client_text_overridden: Mapped[bool] = mapped_column(Boolean, default=False)
    system_text: Mapped[str | None] = mapped_column(Text, nullable=True)
    system_text_overridden: Mapped[bool] = mapped_column(Boolean, default=False)
    fixed_assignee_enabled: Mapped[bool] = mapped_column(Boolean, default=False)
    fixed_assignee_user_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    freeze_on_create: Mapped[bool] = mapped_column(Boolean, default=False)
    confirmation_enabled: Mapped[bool] = mapped_column(Boolean, default=False)
    confirmation_text: Mapped[str | None] = mapped_column(Text, nullable=True)
    confirmation_overridden: Mapped[bool] = mapped_column(Boolean, default=False)
    files_overridden: Mapped[bool] = mapped_column(Boolean, default=False)
    files_json: Mapped[list | None] = mapped_column(JSON, nullable=True)
    conditional_files_overridden: Mapped[bool] = mapped_column(Boolean, default=False)
    conditional_files_json: Mapped[list | None] = mapped_column(JSON, nullable=True)
    automation_key: Mapped[str | None] = mapped_column(String(120), nullable=True)
    automation_overridden: Mapped[bool] = mapped_column(Boolean, default=False)
    is_enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    visibility_overridden: Mapped[bool] = mapped_column(Boolean, default=False)
    is_db_created: Mapped[bool] = mapped_column(Boolean, default=False)
    button_kind: Mapped[str] = mapped_column(String(30), default="terminal")
    parent_path: Mapped[str] = mapped_column(String(1000), default="")
    is_archived: Mapped[bool] = mapped_column(Boolean, default=False)
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    deleted_by_user_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=datetime.utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=datetime.utcnow)


class BotMenuLevelLayout(Base):
    __tablename__ = "bot_menu_level_layouts"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    menu_type: Mapped[str] = mapped_column(String(200), index=True)
    level_key: Mapped[str] = mapped_column(String(600), unique=True, index=True)
    layout_json: Mapped[list | None] = mapped_column(JSON, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=datetime.utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=datetime.utcnow)


class BotMenuProfile(Base):
    __tablename__ = "bot_menu_profiles"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    menu_type: Mapped[str] = mapped_column(String(200), unique=True, index=True)
    display_name: Mapped[str] = mapped_column(String(200))
    is_fallback: Mapped[bool] = mapped_column(Boolean, default=False)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=datetime.utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=datetime.utcnow)


class BotMenuDestinationBinding(Base):
    __tablename__ = "bot_menu_destination_bindings"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    destination_value: Mapped[str] = mapped_column(String(200), unique=True, index=True)
    menu_type: Mapped[str] = mapped_column(String(200), index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=datetime.utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=datetime.utcnow)


class AutomaticMessageTemplate(Base):
    __tablename__ = "automatic_message_templates"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    key: Mapped[str] = mapped_column(String(150), unique=True, index=True)
    body: Mapped[str | None] = mapped_column(Text, nullable=True)
    is_overridden: Mapped[bool] = mapped_column(Boolean, default=False)
    html_enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    disable_link_preview: Mapped[bool] = mapped_column(Boolean, default=False)
    send_silently: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=datetime.utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=datetime.utcnow)
