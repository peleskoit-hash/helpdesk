from enum import Enum
from datetime import date, datetime

from sqlalchemy import JSON, BigInteger, Boolean, CheckConstraint, Date, DateTime, Float, ForeignKey, Index, Integer, LargeBinary, String, Text, UniqueConstraint, text
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


class InternalTaskAssignmentMode(str, Enum):
    DIRECT = "direct"
    DISTRIBUTION = "distribution"


class InternalTaskScheduleKind(str, Enum):
    IMMEDIATE = "immediate"
    SCHEDULED = "scheduled"
    RECURRING = "recurring"


class InternalTaskRecurrenceMode(str, Enum):
    DAILY = "daily"
    SELECTED_WEEKDAYS = "selected_weekdays"
    WEEKLY = "weekly"
    MONTHLY = "monthly"
    EVERY_N_DAYS = "every_n_days"


class InternalTaskCycleEndMode(str, Enum):
    NEVER = "never"
    AT = "at"
    COUNT = "count"


class InternalTaskDefinitionState(str, Enum):
    ACTIVE = "active"
    PAUSED = "paused"
    FINISHED = "finished"
    DISABLED = "disabled"


class TicketIntakeState(str, Enum):
    TELEGRAM_GROUP_COPYING = "telegram_group_copy"
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
    color_enabled: Mapped[bool] = mapped_column(Boolean, default=True, server_default=text("true"), nullable=False)
    task_panel_enabled: Mapped[bool] = mapped_column(Boolean, default=False, server_default=text("false"), nullable=False)
    theme_preference: Mapped[str] = mapped_column(String(10), default="light", server_default=text("'light'"), nullable=False)
    accent_preference: Mapped[str] = mapped_column(String(16), default="purple", server_default=text("'purple'"), nullable=False)
    accent_intensity_light: Mapped[int] = mapped_column(default=50, server_default=text("50"), nullable=False)
    accent_intensity_dark: Mapped[int] = mapped_column(default=50, server_default=text("50"), nullable=False)
    interface_hue_light: Mapped[int | None] = mapped_column(nullable=True)
    interface_intensity_light: Mapped[int | None] = mapped_column(nullable=True)
    accent_hue_light: Mapped[int | None] = mapped_column(nullable=True)
    accent_strength_light: Mapped[int | None] = mapped_column(nullable=True)
    interface_hue_dark: Mapped[int | None] = mapped_column(nullable=True)
    interface_intensity_dark: Mapped[int | None] = mapped_column(nullable=True)
    accent_hue_dark: Mapped[int | None] = mapped_column(nullable=True)
    accent_strength_dark: Mapped[int | None] = mapped_column(nullable=True)
    appearance_write_state_json: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    background_patterns_json: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    client_card_collapsed_blocks_json: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    ticket_list_columns_json: Mapped[list | None] = mapped_column(JSON, nullable=True)
    ticket_list_auto_width: Mapped[bool] = mapped_column(default=False, server_default=text("FALSE"), nullable=False)
    legacy_ticket_count: Mapped[int] = mapped_column(default=0)
    last_login_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_assigned_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=datetime.utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=datetime.utcnow)


class Client(Base):
    __tablename__ = "clients"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    company_id: Mapped[str] = mapped_column(ForeignKey("companies.id"))
    display_name: Mapped[str] = mapped_column(String(200))


class WebChatMessage(Base):
    """Technical WEB chat only; independent of tickets and login sessions."""
    __tablename__ = "web_chat_messages"
    __table_args__ = (
        UniqueConstraint("client_id", "event_key", name="uq_web_chat_messages_client_event"),
        CheckConstraint("message_type IN ('client', 'bot', 'system')", name="ck_web_chat_messages_type"),
        Index("ix_web_chat_messages_client_order", "client_id", "created_at", "id"),
    )

    id: Mapped[int] = mapped_column(BigInteger().with_variant(Integer, "sqlite"), primary_key=True, autoincrement=True)
    client_id: Mapped[str] = mapped_column(ForeignKey("clients.id"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    message_type: Mapped[str] = mapped_column(String(20))
    author: Mapped[str] = mapped_column(String(200))
    text: Mapped[str] = mapped_column(Text)
    event_key: Mapped[str | None] = mapped_column(String(200), nullable=True)


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
    web_login: Mapped[str | None] = mapped_column(String(255), nullable=True)
    web_login_normalized: Mapped[str | None] = mapped_column(String(255), nullable=True, unique=True)
    web_password_encrypted: Mapped[str | None] = mapped_column(Text, nullable=True)
    web_password_hash: Mapped[str | None] = mapped_column(Text, nullable=True)
    web_credentials_version: Mapped[int] = mapped_column(Integer, default=0, server_default=text("0"), nullable=False)
    bitrix_id: Mapped[str | None] = mapped_column(String(100), nullable=True)
    custom_fields: Mapped[dict] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=datetime.utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=datetime.utcnow)


class WebClientDraft(Base):
    __tablename__ = "web_client_drafts"
    __table_args__ = (CheckConstraint("version >= 0", name="ck_web_client_drafts_version"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    client_id: Mapped[str] = mapped_column(ForeignKey("clients.id"), unique=True, nullable=False)
    text: Mapped[str] = mapped_column(Text, nullable=False)
    version: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class WebClientSession(Base):
    __tablename__ = "web_client_sessions"

    token_hash: Mapped[str] = mapped_column(String(64), primary_key=True)
    client_id: Mapped[str] = mapped_column(ForeignKey("clients.id", ondelete="CASCADE"), index=True)
    credentials_version: Mapped[int] = mapped_column(Integer)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    normal_expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    last_activity_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class WebLoginLimit(Base):
    __tablename__ = "web_login_limits"

    login_normalized: Mapped[str] = mapped_column(String(255), primary_key=True)
    source_ip: Mapped[str] = mapped_column(String(100), primary_key=True)
    failure_count: Mapped[int] = mapped_column(Integer, default=0)
    last_failure_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    blocked_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class StaffLoginThrottleBucket(Base):
    __tablename__ = "login_throttle_buckets"

    # Keep the existing DEV schema; STAFF always uses realm="staff".
    realm: Mapped[str] = mapped_column(String(8), primary_key=True, default="staff")
    scope: Mapped[str] = mapped_column(String(8), primary_key=True)
    key_hash: Mapped[str] = mapped_column(String(64), primary_key=True)
    failure_count: Mapped[int] = mapped_column(Integer, default=0)
    last_failure_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    blocked_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    # IP counters may be cleared by a success only if all failures concerned that login.
    identity_hash: Mapped[str | None] = mapped_column(String(64), nullable=True)


class TicketNumberCounter(Base):
    __tablename__ = "ticket_number_counter"
    __table_args__ = (
        CheckConstraint("id = 1", name="ck_ticket_number_counter_singleton"),
        CheckConstraint("last_number >= 0", name="ck_ticket_number_counter_nonnegative"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=False)
    last_number: Mapped[int] = mapped_column(Integer, nullable=False)


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
    channel: Mapped[str] = mapped_column(
        String(20), CheckConstraint("channel IN ('telegram', 'web')", name="ck_tickets_channel"),
        default="telegram", server_default=text("'telegram'"), nullable=False,
    )
    workflow_type: Mapped[str] = mapped_column(String(20), default=TicketWorkflowType.TICKET.value)
    intake_state: Mapped[str] = mapped_column(String(20), default=TicketIntakeState.READY.value)
    task_kind: Mapped[str] = mapped_column(String(30), default=TicketTaskKind.NORMAL.value)
    parent_ticket_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    suppress_client_answer: Mapped[bool] = mapped_column(Boolean, default=False)
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
    quick_action_state_json: Mapped[dict | None] = mapped_column(JSON, nullable=True)
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
    draft_text: Mapped[str | None] = mapped_column(Text, nullable=True)
    draft_mode: Mapped[str | None] = mapped_column(String(20), nullable=True)
    draft_quote_message_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    draft_revision: Mapped[int] = mapped_column(default=0)
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
    quote_message_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
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
    telegram_upload_state_json: Mapped[dict | None] = mapped_column(JSON, nullable=True)
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


class WebTaskResult(Base):
    __tablename__ = "web_task_results"
    __table_args__ = (Index("ix_web_task_results_owner_read", "company_id", "client_id", "read_at"),)

    # No FK to mutable/deletable correspondence: this is a publication snapshot.
    task_id: Mapped[str] = mapped_column(String(36), primary_key=True)
    company_id: Mapped[str] = mapped_column(String(36))
    client_id: Mapped[str] = mapped_column(String(36))
    message_id: Mapped[str] = mapped_column(String(36))
    text: Mapped[str] = mapped_column(Text)
    closed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    read_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class WebTaskResultFile(Base):
    __tablename__ = "web_task_result_files"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    result_task_id: Mapped[str] = mapped_column(ForeignKey("web_task_results.task_id"), index=True)
    filename: Mapped[str] = mapped_column(String(255))
    mime_type: Mapped[str] = mapped_column(String(255))
    size: Mapped[int] = mapped_column(Integer)
    position: Mapped[int] = mapped_column(Integer)
    data: Mapped[bytes] = mapped_column(LargeBinary, deferred=True)


class TicketAttachmentPreload(Base):
    __tablename__ = "ticket_attachment_preloads"
    __table_args__ = (
        CheckConstraint('(user_id IS NOT NULL AND client_id IS NULL) OR '
                        '(user_id IS NULL AND client_id IS NOT NULL)', name='ck_attachment_preload_owner'),
        Index('ix_ticket_attachment_preloads_ticket_user', 'ticket_id', 'user_id'),
        Index('ix_ticket_attachment_preloads_ticket_client', 'ticket_id', 'client_id'),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    ticket_id: Mapped[str | None] = mapped_column(ForeignKey("tickets.id"), nullable=True)
    composer_draft_id: Mapped[str | None] = mapped_column(ForeignKey("web_client_drafts.id"), nullable=True)
    user_id: Mapped[str | None] = mapped_column(ForeignKey("users.id"), nullable=True)
    client_id: Mapped[str | None] = mapped_column(ForeignKey("clients.id"), nullable=True)
    original_filename: Mapped[str | None] = mapped_column(String(255), nullable=True)
    mime_type: Mapped[str | None] = mapped_column(String(255), nullable=True)
    file_size: Mapped[int | None] = mapped_column(nullable=True)
    local_path: Mapped[str] = mapped_column(String(1000))
    telegram_file_id: Mapped[str | None] = mapped_column(String(255), nullable=True)
    telegram_file_unique_id: Mapped[str | None] = mapped_column(String(255), nullable=True)
    telegram_upload_state_json: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    archive_group_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    status: Mapped[str] = mapped_column(String(20), default="uploading")
    final_attachment_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    reply_to_message_id: Mapped[str | None] = mapped_column(String(100), nullable=True)
    prefer_caption: Mapped[bool] = mapped_column(Boolean, default=False)
    telegram_staging_info_message_id: Mapped[str | None] = mapped_column(String(100), nullable=True)
    telegram_staging_file_message_id: Mapped[str | None] = mapped_column(String(100), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=datetime.utcnow)


class QuickReply(Base):
    __tablename__ = "quick_replies"
    __table_args__ = (
        Index("ix_quick_replies_author_user_id", "author_user_id"),
        Index("ix_quick_replies_updated_at", "updated_at"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    short_title: Mapped[str] = mapped_column(String(255), nullable=False)
    body: Mapped[str] = mapped_column(Text, nullable=False)
    author_user_id: Mapped[str] = mapped_column(ForeignKey("users.id"), nullable=False)
    saved_file_ids_json: Mapped[list | None] = mapped_column(JSON, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=datetime.utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=datetime.utcnow)


class QuickReplyUsage(Base):
    __tablename__ = "quick_reply_usage"
    __table_args__ = (
        UniqueConstraint("user_id", "quick_reply_id", name="uq_quick_reply_usage_user_reply"),
        Index("ix_quick_reply_usage_user_used", "user_id", "last_used_at"),
    )

    user_id: Mapped[str] = mapped_column(ForeignKey("users.id"), primary_key=True)
    quick_reply_id: Mapped[str] = mapped_column(ForeignKey("quick_replies.id"), primary_key=True)
    last_used_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class BackgroundContour(Base):
    __tablename__ = "background_contours"
    __table_args__ = (
        Index("ix_background_contours_created", "created_at", "id"),
        CheckConstraint("rotation >= 0 AND rotation <= 360", name="ck_background_contours_rotation"),
        CheckConstraint("rings >= 0 AND rings <= 2", name="ck_background_contours_rings"),
        CheckConstraint("distance >= 0 AND distance <= 200", name="ck_background_contours_distance"),
        CheckConstraint("base_scale >= 50 AND base_scale <= 200", name="ck_background_contours_scale"),
        CheckConstraint("file_size > 0 AND file_size <= 2097152", name="ck_background_contours_size"),
        CheckConstraint("format IN ('png', 'webp')", name="ck_background_contours_format"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    name: Mapped[str] = mapped_column(String(120), nullable=False)
    # Immutable attribution snapshots; deleting a user must retain both ID and name.
    author_user_id: Mapped[str] = mapped_column(String(36), nullable=False)
    author_name: Mapped[str] = mapped_column(String(255), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    storage_key: Mapped[str] = mapped_column(String(40), unique=True, nullable=False)
    format: Mapped[str] = mapped_column(String(4), nullable=False)
    file_size: Mapped[int] = mapped_column(Integer, nullable=False)
    rotation: Mapped[float] = mapped_column(Float, nullable=False)
    rings: Mapped[int] = mapped_column(Integer, nullable=False)
    distance: Mapped[float] = mapped_column(Float, nullable=False)
    base_scale: Mapped[float] = mapped_column(Float, nullable=False)


class SavedFile(Base):
    __tablename__ = "saved_files"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    display_filename: Mapped[str] = mapped_column(String(255), nullable=False)
    storage_key: Mapped[str] = mapped_column(String(255), unique=True, nullable=False)
    mime_type: Mapped[str | None] = mapped_column(String(255), nullable=True)
    file_size: Mapped[int] = mapped_column(Integer, nullable=False)
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


class StatsCompletion(Base):
    __tablename__ = "stats_completions"
    __table_args__ = (
        UniqueConstraint("company_id", "object_type", "object_id", name="uq_stats_completions_company_object"),
        Index("ix_stats_completions_company_completed_at", "company_id", "completed_at"),
        Index("ix_stats_completions_company_user_completed_at", "company_id", "completed_by_user_id", "completed_at"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    company_id: Mapped[str] = mapped_column(String(36), nullable=False)
    object_id: Mapped[str] = mapped_column(String(36), nullable=False)
    object_type: Mapped[str] = mapped_column(String(20), nullable=False)
    task_kind: Mapped[str | None] = mapped_column(String(30), nullable=True)
    completed_by_user_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    is_completed: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=datetime.utcnow, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=datetime.utcnow, nullable=False)


class StatsWorkInterval(Base):
    __tablename__ = "stats_work_intervals"
    __table_args__ = (
        Index("ix_stats_work_intervals_company_user_started", "company_id", "user_id", "started_at"),
        Index("ix_stats_work_intervals_company_object", "company_id", "object_type", "object_id"),
        Index("ix_stats_work_intervals_company_ended", "company_id", "ended_at"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    company_id: Mapped[str] = mapped_column(String(36), nullable=False)
    object_id: Mapped[str] = mapped_column(String(36), nullable=False)
    object_type: Mapped[str] = mapped_column(String(20), nullable=False)
    task_kind: Mapped[str | None] = mapped_column(String(30), nullable=True)
    user_id: Mapped[str] = mapped_column(String(36), nullable=False)
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    ended_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    end_reason: Mapped[str | None] = mapped_column(String(40), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=datetime.utcnow, nullable=False)


class StatsUserStatusInterval(Base):
    __tablename__ = "stats_user_status_intervals"
    __table_args__ = (
        Index("ix_stats_user_status_intervals_company_user_started", "company_id", "user_id", "started_at"),
        Index("ix_stats_user_status_intervals_company_started_id", "company_id", "started_at", "id"),
        Index("ix_stats_user_status_intervals_company_ended", "company_id", "ended_at"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    company_id: Mapped[str] = mapped_column(String(36), nullable=False)
    user_id: Mapped[str] = mapped_column(String(36), nullable=False)
    changed_by_admin: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    status: Mapped[str] = mapped_column(String(40), nullable=False)
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    ended_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=datetime.utcnow, nullable=False)


class TelegramAvatarCleanupState(Base):
    __tablename__ = "telegram_avatar_cleanup_state"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    last_success_on: Mapped[date | None] = mapped_column(Date, nullable=True)
    next_run_on: Mapped[date] = mapped_column(Date, nullable=False)


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
    avatar_thumbnail: Mapped[bytes | None] = mapped_column(LargeBinary, nullable=True)
    avatar_file_unique_id: Mapped[str | None] = mapped_column(String(255), nullable=True)
    avatar_path: Mapped[str | None] = mapped_column(String(500), nullable=True)
    avatar_updated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
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


class EmployeeNotificationPeriod(Base):
    __tablename__ = "employee_notification_periods"
    __table_args__ = (UniqueConstraint("user_id", "work_date", name="uq_employee_notification_period_user_date"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id"), nullable=False)
    work_date: Mapped[date] = mapped_column(Date, nullable=False, index=True)
    chat_id: Mapped[str] = mapped_column(String(100), nullable=False)
    message_id: Mapped[str | None] = mapped_column(String(100), nullable=True)
    offered: Mapped[bool] = mapped_column(Boolean, default=False, server_default=text("false"), nullable=False)


class EmployeeNotificationBacklog(Base):
    __tablename__ = "employee_notification_backlog"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    period_id: Mapped[str] = mapped_column(ForeignKey("employee_notification_periods.id"), nullable=False, index=True)
    text: Mapped[str] = mapped_column(Text, nullable=False)
    parse_mode: Mapped[str | None] = mapped_column(String(30), nullable=True)
    disable_link_preview: Mapped[bool] = mapped_column(Boolean, nullable=False)
    send_silently: Mapped[bool] = mapped_column(Boolean, nullable=False)


class EmployeeNotificationPreference(Base):
    __tablename__ = "employee_notification_preferences"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id"), unique=True, nullable=False)
    use_base_new_assignment_settings: Mapped[bool] = mapped_column(Boolean, default=True, server_default=text("true"), nullable=False)
    use_base_new_message_settings: Mapped[bool] = mapped_column(Boolean, default=True, server_default=text("true"), nullable=False)
    use_base_internal_task_completion_settings: Mapped[bool] = mapped_column(Boolean, default=True, server_default=text("true"), nullable=False)
    personal_new_assignment_template: Mapped[str | None] = mapped_column(Text, nullable=True)
    personal_new_assignment_html_enabled: Mapped[bool] = mapped_column(Boolean, default=True, server_default=text("true"), nullable=False)
    personal_new_assignment_disable_link_preview: Mapped[bool] = mapped_column(Boolean, default=False, server_default=text("false"), nullable=False)
    personal_new_assignment_send_silently: Mapped[bool] = mapped_column(Boolean, default=False, server_default=text("false"), nullable=False)
    personal_new_message_template: Mapped[str | None] = mapped_column(Text, nullable=True)
    personal_new_message_html_enabled: Mapped[bool] = mapped_column(Boolean, default=True, server_default=text("true"), nullable=False)
    personal_new_message_disable_link_preview: Mapped[bool] = mapped_column(Boolean, default=False, server_default=text("false"), nullable=False)
    personal_new_message_send_silently: Mapped[bool] = mapped_column(Boolean, default=False, server_default=text("false"), nullable=False)
    personal_internal_task_completion_template: Mapped[str | None] = mapped_column(Text, nullable=True)
    personal_internal_task_completion_html_enabled: Mapped[bool] = mapped_column(Boolean, default=True, server_default=text("true"), nullable=False)
    personal_internal_task_completion_disable_link_preview: Mapped[bool] = mapped_column(Boolean, default=False, server_default=text("false"), nullable=False)
    personal_internal_task_completion_send_silently: Mapped[bool] = mapped_column(Boolean, default=False, server_default=text("false"), nullable=False)
    receive_transfer_to_agent: Mapped[bool] = mapped_column(Boolean, default=True, server_default=text("true"), nullable=False)
    receive_transfer_from_agent: Mapped[bool] = mapped_column(Boolean, default=True, server_default=text("true"), nullable=False)
    receive_new_message: Mapped[bool] = mapped_column(Boolean, default=True, server_default=text("true"), nullable=False)
    notify_internal_task_completion: Mapped[bool] = mapped_column(Boolean, default=True, server_default=text("true"), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=datetime.utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=datetime.utcnow)


class InternalTaskDefinition(Base):
    __tablename__ = "internal_task_definitions"
    __table_args__ = (
        CheckConstraint(
            "assignment_mode IN ('direct', 'distribution')",
            name="ck_internal_task_definitions_assignment_mode",
        ),
        CheckConstraint(
            "schedule_kind IN ('immediate', 'scheduled', 'recurring')",
            name="ck_internal_task_definitions_schedule_kind",
        ),
        CheckConstraint(
            "recurrence_mode IS NULL OR recurrence_mode IN "
            "('daily', 'selected_weekdays', 'weekly', 'monthly', 'every_n_days')",
            name="ck_internal_task_definitions_recurrence_mode",
        ),
        CheckConstraint(
            "cycle_end_mode IS NULL OR cycle_end_mode IN ('never', 'at', 'count')",
            name="ck_internal_task_definitions_cycle_end_mode",
        ),
        CheckConstraint(
            "state IN ('active', 'paused', 'finished', 'disabled')",
            name="ck_internal_task_definitions_state",
        ),
        CheckConstraint(
            "cycle_max_occurrences IS NULL OR cycle_max_occurrences > 0",
            name="ck_internal_task_definitions_cycle_max_occurrences_positive",
        ),
        Index("ix_internal_task_definitions_company_schedule_state", "company_id", "schedule_kind", "state"),
        Index("ix_internal_task_definitions_due_once", "schedule_kind", "state", "scheduled_for_utc"),
        Index("ix_internal_task_definitions_active_recurring", "schedule_kind", "state", "generation_not_before_utc"),
        Index("ix_internal_task_definitions_creator", "company_id", "creator_admin_id"),
        Index("ix_internal_task_definitions_responsible", "company_id", "responsible_admin_id"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    company_id: Mapped[str] = mapped_column(ForeignKey("companies.id"), nullable=False)
    title: Mapped[str] = mapped_column(String(255), nullable=False)
    description: Mapped[str] = mapped_column(Text, nullable=False)
    creator_admin_id: Mapped[str] = mapped_column(ForeignKey("users.id"), nullable=False)
    responsible_admin_id: Mapped[str] = mapped_column(ForeignKey("users.id"), nullable=False)
    display_author: Mapped[bool] = mapped_column(Boolean, nullable=False)
    assignment_mode: Mapped[str] = mapped_column(String(30), nullable=False)
    assignee_user_id: Mapped[str | None] = mapped_column(ForeignKey("users.id"), nullable=True)
    notify_on_completion: Mapped[bool] = mapped_column(Boolean, nullable=False)
    deadline_at_utc: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    schedule_kind: Mapped[str] = mapped_column(String(30), nullable=False)
    timezone_name: Mapped[str] = mapped_column(String(100), nullable=False)
    scheduled_for_utc: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    recurrence_mode: Mapped[str | None] = mapped_column(String(30), nullable=True)
    recurrence_config: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    cycle_start_at_utc: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    cycle_end_mode: Mapped[str | None] = mapped_column(String(30), nullable=True)
    cycle_end_at_utc: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    cycle_max_occurrences: Mapped[int | None] = mapped_column(Integer, nullable=True)
    state: Mapped[str] = mapped_column(
        String(30),
        default=InternalTaskDefinitionState.ACTIVE.value,
        server_default=text("'active'"),
        nullable=False,
    )
    generation_not_before_utc: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=datetime.utcnow, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=datetime.utcnow, nullable=False)


class InternalTaskOccurrence(Base):
    __tablename__ = "internal_task_occurrences"
    __table_args__ = (
        CheckConstraint(
            "effective_assignment_mode IN ('direct', 'distribution')",
            name="ck_internal_task_occurrences_effective_assignment_mode",
        ),
        UniqueConstraint(
            "definition_id",
            "occurrence_at_utc",
            name="uq_internal_task_occurrences_definition_time",
        ),
        Index("ux_internal_task_occurrences_ticket_id", "ticket_id", unique=True),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    definition_id: Mapped[str] = mapped_column(ForeignKey("internal_task_definitions.id"), nullable=False)
    occurrence_at_utc: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    ticket_id: Mapped[str] = mapped_column(String(36), nullable=False)
    title_snapshot: Mapped[str] = mapped_column(String(255), nullable=False)
    description_snapshot: Mapped[str] = mapped_column(Text, nullable=False)
    creator_admin_id: Mapped[str] = mapped_column(ForeignKey("users.id"), nullable=False)
    responsible_admin_id: Mapped[str] = mapped_column(ForeignKey("users.id"), nullable=False)
    display_author: Mapped[bool] = mapped_column(Boolean, nullable=False)
    effective_assignment_mode: Mapped[str] = mapped_column(String(30), nullable=False)
    effective_assignee_user_id: Mapped[str | None] = mapped_column(ForeignKey("users.id"), nullable=True)
    notify_on_completion: Mapped[bool] = mapped_column(Boolean, nullable=False)
    deadline_at_utc: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    materialized_at_utc: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=datetime.utcnow, nullable=False)


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
    show_when_false: Mapped[bool] = mapped_column(Boolean, default=True)
    is_editable: Mapped[bool] = mapped_column(Boolean, default=True)
    admin_only_edit: Mapped[bool] = mapped_column(Boolean, default=False)
    is_required: Mapped[bool] = mapped_column(Boolean, default=False)
    is_system: Mapped[bool] = mapped_column(Boolean, default=True)
    options_json: Mapped[list | None] = mapped_column(JSON, nullable=True)
    admin_only_values_json: Mapped[list | None] = mapped_column(JSON, nullable=True)
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


class TelegramAutomationGroup(Base):
    __tablename__ = "telegram_automation_groups"
    __table_args__ = (UniqueConstraint("environment", "bot_id", "chat_id", name="uq_telegram_automation_group"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    company_id: Mapped[str] = mapped_column(ForeignKey("companies.id"))
    environment: Mapped[str] = mapped_column(String(10))
    bot_id: Mapped[str] = mapped_column(String(30))
    chat_id: Mapped[str] = mapped_column(String(30))
    title: Mapped[str] = mapped_column(String(255))
    registered_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    checked_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    status: Mapped[str] = mapped_column(String(30), default="unavailable")
    last_error: Mapped[str | None] = mapped_column(String(100), nullable=True)


class TelegramGroupRegistrationCode(Base):
    __tablename__ = "telegram_group_registration_codes"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    code_hash: Mapped[str] = mapped_column(String(64), unique=True)
    environment: Mapped[str] = mapped_column(String(10))
    company_id: Mapped[str] = mapped_column(ForeignKey("companies.id"))
    created_by_user_id: Mapped[str] = mapped_column(ForeignKey("users.id"))
    bot_id: Mapped[str] = mapped_column(String(30))
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    expires_at: Mapped[datetime] = mapped_column(DateTime)
    used_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)


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
    button_style: Mapped[str | None] = mapped_column(String(20), nullable=True)
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
    suppress_client_answer: Mapped[bool] = mapped_column(Boolean, default=False)
    confirmation_enabled: Mapped[bool] = mapped_column(Boolean, default=False)
    confirmation_text: Mapped[str | None] = mapped_column(Text, nullable=True)
    confirmation_overridden: Mapped[bool] = mapped_column(Boolean, default=False)
    files_overridden: Mapped[bool] = mapped_column(Boolean, default=False)
    files_json: Mapped[list | None] = mapped_column(JSON, nullable=True)
    conditional_files_overridden: Mapped[bool] = mapped_column(Boolean, default=False)
    conditional_files_json: Mapped[list | None] = mapped_column(JSON, nullable=True)
    automation_key: Mapped[str | None] = mapped_column(String(120), nullable=True)
    telegram_group_id: Mapped[str | None] = mapped_column(ForeignKey("telegram_automation_groups.id"), nullable=True)
    automation_overridden: Mapped[bool] = mapped_column(Boolean, default=False)
    is_enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    visibility_overridden: Mapped[bool] = mapped_column(Boolean, default=False)
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
