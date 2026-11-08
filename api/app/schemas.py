from datetime import date, datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, StrictBool, field_validator, model_serializer


TICKET_LIST_COLUMN_DEFAULTS = {
    "subject": 0,
    "requester": 150,
    "assignee": 170,
    "age": 120,
    "created_at": 140,
    "last_reply": 150,
}
TICKET_LIST_COLUMN_KEYS = set(TICKET_LIST_COLUMN_DEFAULTS) | {"department", "type", "location"}


class TicketListColumnPreference(BaseModel):
    key: str
    width: int

    @field_validator("key")
    @classmethod
    def validate_key(cls, value: str) -> str:
        if value not in TICKET_LIST_COLUMN_KEYS:
            raise ValueError("unknown ticket list column")
        return value

    @field_validator("width")
    @classmethod
    def validate_width(cls, value: int) -> int:
        if value < 0 or 0 < value < 60:
            raise ValueError("ticket list column width must be 0 or at least 60")
        return value


def default_ticket_list_columns() -> list[TicketListColumnPreference]:
    return [
        TicketListColumnPreference(key=key, width=width)
        for key, width in TICKET_LIST_COLUMN_DEFAULTS.items()
    ]


def normalize_ticket_list_columns(value) -> list[TicketListColumnPreference]:
    if value is None:
        return default_ticket_list_columns()
    if not isinstance(value, list) or not value:
        raise ValueError("ticket_list_columns must be a non-empty list")
    columns = [item if isinstance(item, TicketListColumnPreference) else TicketListColumnPreference.model_validate(item) for item in value]
    keys = [item.key for item in columns]
    if len(set(keys)) != len(keys):
        raise ValueError("ticket_list_columns cannot contain duplicate keys")
    if "subject" not in set(keys):
        raise ValueError("ticket_list_columns must contain subject")
    return columns


def dump_ticket_list_columns(value) -> list[dict]:
    return [item.model_dump() for item in normalize_ticket_list_columns(value)]


class AssigneeTicketCounts(BaseModel):
    active: int = 0
    frozen: int = 0


class TicketListItem(BaseModel):
    id: str
    channel: Literal['telegram', 'web']
    ticket_number: int | None = None
    subject: str
    workflow_type: str = "ticket"
    task_kind: str | None = None
    is_internal_task: bool = False
    intake_state: str = "ready"
    status: str
    is_frozen: bool
    seen_by_agent: bool
    unread_count: int = 0
    client_name: str
    requester_display_name: str = ""
    requester_department: str = ""
    requester_location_type: str = ""
    assignee_id: str | None = None
    assignee_name: str | None = None
    is_special: bool = False
    special_assignee_id: str | None = None
    special_assignee_name: str | None = None
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


class TicketCountsOut(BaseModel):
    unassigned: int
    active: int
    frozen: int
    closed: int
    special: int = 0
    by_assignee: dict[str, AssigneeTicketCounts] | None = None


class TaskPanelItem(BaseModel):
    id: str
    subject: str
    has_unread: bool = False


class TicketAttachmentOut(BaseModel):
    id: str
    original_filename: str | None = None
    mime_type: str | None = None
    file_size: int | None = None
    telegram_delivery_status: str | None = None
    download_url: str
    is_image: bool = False
    is_unsupported_preview: bool = False
    unsupported_preview_text: str | None = None
    created_at: datetime


class TicketMessageOut(BaseModel):
    id: str
    is_initial_task_description: bool = False
    is_initial_task_message: bool = False
    is_initial_task_assignment: bool = False
    message_type: str
    body: str
    author_user_id: str | None
    author_name: str | None = None
    telegram_message_id: str | None = None
    message_status: str = "normal"
    status_note: str | None = None
    replaced_message_id: str | None = None
    quote_message_id: str | None = None
    created_at: datetime
    attachments: list[TicketAttachmentOut] = Field(default_factory=list)


class ClientProfileOut(BaseModel):
    web_login: str | None = None
    web_password_set: bool = False
    telegram_avatar_present: bool = False
    telegram_avatar_updated_at: datetime | None = None
    telegram_username: str | None = None
    telegram_display_name: str | None = None
    display_name: str | None = None
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
    bitrix_password_set: bool = False
    bitrix_id: str | None = None
    custom_fields: dict = Field(default_factory=dict)


class BitrixPasswordRevealOut(BaseModel):
    bitrix_password: str | None = None


class WebCredentialsGeneratePayload(BaseModel):
    web_login: str | None = Field(default=None, max_length=255)
    web_password_set: bool = False
    regenerate: bool = False
    bitrix_login: str | None = Field(default=None, max_length=255)
    contact_name: str | None = Field(default=None, max_length=200)
    last_name: str | None = Field(default=None, max_length=200)


class ClientProfileUpdate(BaseModel):
    clear_web_password: bool = Field(default=False, strict=True)
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
    custom_fields: dict | None = None


class TicketReadPayload(BaseModel):
    message_id: str = Field(min_length=1, max_length=36)
    message_ids: list[str] = Field(min_length=1)


class TicketCard(BaseModel):
    unread_message_ids: list[str] = Field(default_factory=list)
    has_active_attachment_uploads: bool = False
    id: str
    ticket_number: int | None = None
    subject: str
    workflow_type: str = "ticket"
    task_kind: str | None = None
    parent_ticket_id: str | None = None
    intake_state: str = "ready"
    status: str
    is_frozen: bool
    freeze_until: datetime | None
    closed_at: datetime | None
    assignee_id: str | None = None
    assignee_name: str | None = None
    is_special: bool = False
    special_assignee_id: str | None = None
    special_assignee_name: str | None = None
    instruction_title: str | None = None
    instruction_text: str | None = None
    wrong_problem_used: bool = False
    wrong_problem_used_at: datetime | None = None
    wrong_problem_label: str | None = None
    quick_actions_used: dict[str, bool] = Field(default_factory=dict)
    auto_close_disabled: bool = False
    auto_close_started_at: datetime | None = None
    auto_close_due_at: datetime | None = None
    auto_closed_at: datetime | None = None
    closed_by_user_id: str | None = None
    onsite_triggered: bool = False
    call_center_employee_lookup_at: datetime | None = None
    call_center_internal_number: str | None = None
    call_center_employee_first_name: str | None = None
    call_center_employee_last_name: str | None = None
    call_center_employee_id: str | None = None
    call_center_employee_bitrix_login: str | None = None
    draft_text: str = ""
    draft_mode: str = "reply"
    draft_revision: int = 0
    client_name: str
    client_profile: ClientProfileOut
    active_attachment_preloads: list[dict] = Field(default_factory=list)
    messages: list[TicketMessageOut]


class TextPayload(BaseModel):
    text: str = Field(min_length=1)
    quote_message_id: str | None = None
    quick_action: str | None = None


class TicketDraftPayload(BaseModel):
    text: str = ""
    mode: str = "reply"


class TicketDraftOut(BaseModel):
    text: str = ""
    mode: str = "reply"
    revision: int = 0


class FreezePayload(BaseModel):
    freeze_until: datetime | None = None


class TicketAutoClosePayload(BaseModel):
    auto_close_disabled: bool


class AssignTicketsPayload(BaseModel):
    ticket_ids: list[str] = Field(min_length=1)
    assignee_id: str | None = None


class AdminBulkTicketsPayload(BaseModel):
    ticket_ids: list[str] = Field(min_length=1)
    skip_client_notification: bool = False


class AdminCloseResult(BaseModel):
    ok: bool = True
    closed: int = 0
    already_closed: int = 0
    not_found: list[str] = Field(default_factory=list)


class AdminFreezeResult(BaseModel):
    ok: bool = True
    frozen: int = 0
    already_frozen: int = 0
    invalid: int = 0
    closed: int = 0
    not_found: list[str] = Field(default_factory=list)


class AdminUnfreezeResult(BaseModel):
    ok: bool = True
    unfrozen: int = 0
    already_unfrozen: int = 0
    closed: int = 0
    not_found: list[str] = Field(default_factory=list)


class AdminDeleteResult(BaseModel):
    ok: bool = True
    deleted: int = 0
    not_found: list[str] = Field(default_factory=list)
    file_delete_warnings: list[str] = Field(default_factory=list)


class IntegrationTaskCreatePayload(BaseModel):
    model_config = ConfigDict(extra="forbid")

    deal_id: str
    deal_url: str | None = None
    client_phone: str | None = None

    @field_validator("deal_id", mode="before")
    @classmethod
    def normalize_deal_id(cls, value):
        if value is None:
            return ""
        return str(value).strip()

    @field_validator("deal_id")
    @classmethod
    def require_deal_id(cls, value: str) -> str:
        if not value:
            raise ValueError("deal_id is required")
        return value

    @field_validator("deal_url", "client_phone", mode="before")
    @classmethod
    def normalize_optional_string(cls, value):
        if value is None:
            return None
        return str(value).strip() or None


class IntegrationTaskCreateResponse(BaseModel):
    result: str
    code: str
    deal_id: str
    task_id: str | None = None


class IntegrationTelegramPayload(BaseModel):
    model_config = ConfigDict(extra="forbid")

    user_id: str
    call_time: datetime
    message: str

    @field_validator("call_time", mode="before")
    @classmethod
    def require_iso_call_time(cls, value):
        if not isinstance(value, str):
            raise ValueError("call_time must be an ISO datetime string")
        return value

    @field_validator("user_id", mode="before")
    @classmethod
    def normalize_user_id(cls, value):
        if isinstance(value, bool) or isinstance(value, float):
            raise ValueError("user_id must be a string or integer")
        if isinstance(value, int):
            return str(value)
        if isinstance(value, str):
            return value.strip()
        raise ValueError("user_id must be a string or integer")

    @field_validator("user_id")
    @classmethod
    def require_user_id(cls, value: str) -> str:
        if not value:
            raise ValueError("user_id is required")
        return value

    @field_validator("message")
    @classmethod
    def require_message(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("message is required")
        return value


class IntegrationTelegramResponse(BaseModel):
    result: str
    code: str
    user_id: str
    call_time: str
    telegram_message_id: str | None = None


class WrongProblemPayload(BaseModel):
    button_key: str = Field(min_length=1)


class ErrorOut(BaseModel):
    error: dict


SortField = Literal["created_at", "updated_at"]
SortOrder = Literal["asc", "desc"]


AvailabilityStatus = Literal["offline", "online", "on_ticket", "waiting", "lunch"]
UserRole = Literal["admin", "agent", "trainee", "chiki_piki"]


class UserStatusOut(BaseModel):
    id: str
    role: str
    role_label: str | None = None
    full_name: str
    availability_status: AvailabilityStatus


class UserStatusUpdate(BaseModel):
    availability_status: AvailabilityStatus


AccentPreference = Literal["purple", "blue", "red", "green", "orange", "teal", "mono"]


BackgroundPatternKind = Literal["grid", "dots", "diagonals", "waves", "relief"]


class BackgroundPatternParameters(BaseModel):
    model_config = ConfigDict(extra="forbid")

    scale: int = Field(default=100, ge=50, le=200, strict=True)
    intensity: int = Field(default=50, ge=0, le=100, strict=True)
    variable: StrictBool = False
    seed: int | None = Field(default=None, ge=1, le=65535, strict=True)

    @field_validator("seed")
    @classmethod
    def reject_null_seed(cls, value):
        if value is None:
            raise ValueError("seed cannot be null")
        return value


class BackgroundPatternTheme(BaseModel):
    model_config = ConfigDict(extra="forbid")

    selected: Literal["none", "grid", "dots", "diagonals", "waves", "relief"] | UUID = "grid"
    patterns: dict[BackgroundPatternKind | UUID, BackgroundPatternParameters] = Field(default_factory=dict)


class BackgroundPatternThemeOut(BackgroundPatternTheme):
    fallback_grid: bool = False

    @model_serializer(mode="wrap")
    def serialize_theme(self, handler):
        data = handler(self)
        if not self.fallback_grid:
            data.pop("fallback_grid", None)
        return data


class BackgroundContourComposition(BaseModel):
    rotation: float = Field(default=0, ge=0, le=360, allow_inf_nan=False)
    rings: int = Field(default=0, ge=0, le=2)
    distance: float = Field(default=20, ge=0, le=200, allow_inf_nan=False,
                            description="Ring spacing as a percentage of the contour size")
    base_scale: float = Field(default=100, ge=50, le=200, allow_inf_nan=False,
                              description="Base scale in percent")


class BackgroundContourOut(BackgroundContourComposition):
    id: str
    name: str
    author_user_id: str
    author_name: str
    created_at: datetime
    format: Literal["png", "webp"]
    file_size: int
    image_url: str
    can_delete: bool
    can_edit: bool


class BackgroundContourListOut(BaseModel):
    items: list[BackgroundContourOut]


class UserPreferencesOut(BaseModel):
    appearance_revision: str = ""
    background_patterns: dict[Literal["light", "dark"], BackgroundPatternThemeOut] | None = None
    send_by_enter: bool = False
    color_enabled: bool = True
    task_panel_enabled: bool = False
    ticket_list_auto_width: bool = False
    theme_preference: Literal["light", "dark"] = "light"
    accent_preference: AccentPreference = "purple"
    accent_intensity_light: int = Field(default=50, ge=0, le=100, strict=True)
    accent_intensity_dark: int = Field(default=50, ge=0, le=100, strict=True)
    interface_hue_light: int = Field(default=259, ge=0, le=360, strict=True)
    interface_intensity_light: int = Field(default=50, ge=0, le=100, strict=True)
    accent_hue_light: int = Field(default=259, ge=0, le=360, strict=True)
    accent_strength_light: int = Field(default=50, ge=0, le=100, strict=True)
    interface_hue_dark: int = Field(default=225, ge=0, le=360, strict=True)
    interface_intensity_dark: int = Field(default=50, ge=0, le=100, strict=True)
    accent_hue_dark: int = Field(default=259, ge=0, le=360, strict=True)
    accent_strength_dark: int = Field(default=50, ge=0, le=100, strict=True)
    client_card_collapsed_blocks: dict[str, StrictBool] = Field(default_factory=dict)
    ticket_list_columns: list[TicketListColumnPreference] = Field(default_factory=default_ticket_list_columns)


class AppearanceWrite(BaseModel):
    writer_id: UUID
    sequence: int = Field(ge=1, le=9007199254740991, strict=True)


class AppearanceSessionOut(BaseModel):
    writer_id: UUID
    preferences: UserPreferencesOut | None = None


class UserPreferencesUpdate(BaseModel):
    background_patterns: dict[Literal["light", "dark"], BackgroundPatternTheme] | None = None
    send_by_enter: bool | None = None
    color_enabled: bool | None = None
    task_panel_enabled: bool | None = None
    ticket_list_auto_width: StrictBool | None = None
    theme_preference: Literal["light", "dark"] | None = None
    accent_preference: AccentPreference | None = None
    accent_intensity_light: int | None = Field(default=None, ge=0, le=100, strict=True)
    accent_intensity_dark: int | None = Field(default=None, ge=0, le=100, strict=True)
    interface_hue_light: int | None = Field(default=None, ge=0, le=360, strict=True)
    interface_intensity_light: int | None = Field(default=None, ge=0, le=100, strict=True)
    accent_hue_light: int | None = Field(default=None, ge=0, le=360, strict=True)
    accent_strength_light: int | None = Field(default=None, ge=0, le=100, strict=True)
    interface_hue_dark: int | None = Field(default=None, ge=0, le=360, strict=True)
    interface_intensity_dark: int | None = Field(default=None, ge=0, le=100, strict=True)
    accent_hue_dark: int | None = Field(default=None, ge=0, le=360, strict=True)
    accent_strength_dark: int | None = Field(default=None, ge=0, le=100, strict=True)
    ticket_list_columns: list[TicketListColumnPreference] | None = None
    client_card_collapsed_blocks: dict[str, StrictBool] = Field(default_factory=dict)
    appearance_write: AppearanceWrite | None = None

    @field_validator("background_patterns", "theme_preference", "accent_preference", "accent_intensity_light", "accent_intensity_dark",
                     "interface_hue_light", "interface_intensity_light", "accent_hue_light", "accent_strength_light", "interface_hue_dark", "interface_intensity_dark", "accent_hue_dark", "accent_strength_dark")
    @classmethod
    def validate_appearance_preference(cls, value):
        if value is None:
            raise ValueError("appearance preference cannot be null")
        return value

    @field_validator("ticket_list_columns")
    @classmethod
    def validate_ticket_list_columns(cls, value):
        if value is None:
            return value
        return normalize_ticket_list_columns(value)


class AppearanceSessionCreate(BaseModel):
    expected_revision: str
    preferences: UserPreferencesUpdate
    sequence: int = Field(ge=1, le=9007199254740991, strict=True)


class TelegramNotificationSummaryOut(BaseModel):
    bound: bool = False
    display: str | None = None


class SelfProfileOut(BaseModel):
    id: str
    full_name: str
    login: str | None = None
    role: str
    legacy_ticket_count: int = 0
    avatar_path: str | None = None
    avatar_present: bool = False
    avatar_url: str | None = None
    telegram_notification: TelegramNotificationSummaryOut


class UserAvatarOut(BaseModel):
    ok: bool = True
    avatar_present: bool
    avatar_url: str | None = None


class SelfLoginUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    login: str


class SelfAccountChangeOut(BaseModel):
    ok: bool = True
    message: str


class SelfPasswordUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    current_password: str
    new_password: str
    new_password_confirmation: str


class AgentOut(BaseModel):
    id: str
    full_name: str
    role: str = "agent"
    role_label: str | None = None
    availability_status: AvailabilityStatus
    updated_at: datetime | None = None


class StatusBoardAgentOut(AgentOut):
    avatar_present: bool = False
    avatar_url: str | None = None


class SettingsUserOut(BaseModel):
    id: str
    full_name: str
    login: str | None = None
    role: str
    role_label: str
    is_active: bool
    availability_status: AvailabilityStatus
    legacy_ticket_count: int = 0
    telegram_notification_bound: bool = False
    telegram_notification_display: str | None = None
    last_login_at: datetime | None = None
    created_at: datetime | None = None
    updated_at: datetime | None = None


class SettingsUserCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    full_name: str = Field(min_length=1)
    login: str = Field(min_length=1)
    password: str = Field(min_length=1)
    role: UserRole
    is_active: bool = True
    availability_status: AvailabilityStatus = "offline"
    legacy_ticket_count: int = 0


class SettingsUserUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    full_name: str | None = None
    login: str | None = None
    password: str | None = None
    role: UserRole | None = None
    is_active: bool | None = None
    availability_status: AvailabilityStatus | None = None
    legacy_ticket_count: int | None = None


class CompletionPeriodStats(BaseModel):
    total: int = 0
    tasks: int = 0
    tickets: int = 0


class CompletionStatsOut(BaseModel):
    display_name: str
    role: str
    total: CompletionPeriodStats | None = None
    calendar: dict[str, CompletionPeriodStats] | None = None
    rolling: dict[str, CompletionPeriodStats] | None = None
    legacy: int | None = None


class ManagementCompletedStatsItem(BaseModel):
    user_id: str
    full_name: str | None = None
    is_active: bool | None = None
    tickets: int = 0
    tasks: int = 0
    total: int = 0


class ManagementCompletedStatsOut(BaseModel):
    items: list[ManagementCompletedStatsItem]
    total: CompletionPeriodStats


class ManagementWorkTimeStatsTotal(BaseModel):
    tickets: int = 0
    tasks: int = 0
    objects: int = 0
    average_seconds: int | float = 0
    median_seconds: int | float = 0


class ManagementWorkTimeStatsItem(ManagementWorkTimeStatsTotal):
    user_id: str
    full_name: str | None = None
    is_active: bool | None = None


class ManagementWorkTimeStatsOut(BaseModel):
    items: list[ManagementWorkTimeStatsItem]
    total: ManagementWorkTimeStatsTotal


class ManagementStatusStatsTotal(BaseModel):
    offline_seconds: int = 0
    online_seconds: int = 0
    on_ticket_seconds: int = 0
    waiting_seconds: int = 0
    lunch_seconds: int = 0
    total_seconds: int = 0


class ManagementStatusStatsItem(ManagementStatusStatsTotal):
    user_id: str
    full_name: str | None = None
    is_active: bool | None = None


class ManagementStatusStatsOut(BaseModel):
    items: list[ManagementStatusStatsItem]
    total: ManagementStatusStatsTotal


class ManagementStatusTransitionItem(BaseModel):
    changed_by_admin: bool | None = None
    user_id: str
    full_name: str | None = None
    is_active: bool | None = None
    from_status: str
    to_status: str
    changed_at: datetime


class ManagementStatusTransitionsOut(BaseModel):
    items: list[ManagementStatusTransitionItem]
    next_cursor: str | None = None
    has_more: bool = False
    total_count: int | None = None


class ReviewEmployeeOut(BaseModel):
    employee_id: str
    display_name: str
    role: str
    is_active: bool


class ReviewCurrentRowOut(BaseModel):
    agent_full_name: str | None = None
    review_id: str
    ticket_id: str
    agent_id: str
    rated_at: datetime
    client_name_snapshot: str
    ticket_subject_snapshot: str
    rating: int
    review_text: str | None = None
    version_number: int
    has_history: bool


class ReviewHistoryRowOut(BaseModel):
    review_id: str
    rated_at: datetime
    rating: int
    review_text: str | None = None
    version_number: int


class ReviewListResponse(BaseModel):
    items: list[ReviewCurrentRowOut] = Field(default_factory=list)
    total_count: int
    average_rating: float | None = None
    min_review_date: date | None = None
    page: int
    pages: int
    page_size: int


class ReviewHistoryResponse(BaseModel):
    items: list[ReviewHistoryRowOut] = Field(default_factory=list)


class ManagementFeedbackRowOut(BaseModel):
    feedback_id: str
    submitted_at: datetime
    client_name_snapshot: str
    ticket_title_snapshot: str
    text: str
    ticket_available: bool
    ticket_id: str
    ticket_number: int | None = None
    workflow_type: str | None = None


class ManagementFeedbackListResponse(BaseModel):
    items: list[ManagementFeedbackRowOut] = Field(default_factory=list)
    total_count: int
    min_feedback_date: date | None = None
    page: int
    pages: int
    page_size: int


ClientCardColorScheme = str


class ClientCardFieldConfigOut(BaseModel):
    id: str
    key: str
    label: str
    field_type: str
    block_key: str
    sort_order: int
    is_visible: bool
    is_visible_in_editor: bool = True
    show_when_false: bool = True
    is_editable: bool
    admin_only_edit: bool = False
    is_required: bool = False
    is_system: bool
    options: list[str] | None = None
    admin_only_values: list[str] | None = None


class ClientCardBlockConfigOut(BaseModel):
    id: str
    key: str
    title: str
    color_scheme: str
    sort_order: int
    is_active: bool
    show_in_card: bool = True
    show_in_editor: bool = True
    show_title: bool = True
    fields: list[ClientCardFieldConfigOut] = Field(default_factory=list)


ClientCardRuleOperator = Literal["always", "equals", "not_equals", "is_empty", "is_not_empty"]
ClientCardRuleActionType = Literal["show", "hide", "readonly", "editable", "required", "optional", "set_value", "lookup_source"]
ClientCardRuleTargetType = Literal["field", "block"]
ClientCardRuleScope = Literal["card", "editor", "both", "save"]
ClientCardRuleValueType = Literal["literal", "field", "template", "map", "null"]
ClientCardSourceKind = Literal["http", "system"]
ClientCardSourceMethod = Literal["GET", "POST"]
ClientCardSourceOnError = Literal["ignore", "block_save"]


class ClientCardRuleAction(BaseModel):
    target_type: ClientCardRuleTargetType = "field"
    target_key: str | None = None
    action: ClientCardRuleActionType
    scope: ClientCardRuleScope = "editor"
    value_type: ClientCardRuleValueType | None = None
    value: str | None = None
    source_key: str | None = None
    source_id: str | None = None
    map: dict[str, str | None] | None = None


class ClientCardRuleConfigOut(BaseModel):
    id: str
    technical_key: str | None = None
    name: str
    is_active: bool
    is_system: bool = False
    sort_order: int
    condition_field_key: str | None = None
    condition_operator: ClientCardRuleOperator
    condition_value: str | None = None
    actions: list[ClientCardRuleAction] = Field(default_factory=list)


class ClientCardConfigResponse(BaseModel):
    blocks: list[ClientCardBlockConfigOut] = Field(default_factory=list)
    rules: list[ClientCardRuleConfigOut] = Field(default_factory=list)
    sources: list["ClientCardSourceConfigOut"] = Field(default_factory=list)


class ClientCardSourceRuntimeOut(BaseModel):
    id: str
    technical_key: str | None = None
    name: str
    is_active: bool


class ClientCardRuntimeConfigResponse(BaseModel):
    blocks: list[ClientCardBlockConfigOut] = Field(default_factory=list)
    rules: list[ClientCardRuleConfigOut] = Field(default_factory=list)
    sources: list[ClientCardSourceRuntimeOut] = Field(default_factory=list)


class ClientCardRuleCreate(BaseModel):
    name: str = Field(min_length=1)
    is_active: bool = True
    sort_order: int = 1000
    condition_field_key: str | None = None
    condition_operator: ClientCardRuleOperator
    condition_value: str | None = None
    actions: list[ClientCardRuleAction] = Field(min_length=1)


class ClientCardRuleUpdate(BaseModel):
    name: str | None = None
    is_active: bool | None = None
    sort_order: int | None = None
    condition_field_key: str | None = None
    condition_operator: ClientCardRuleOperator | None = None
    condition_value: str | None = None
    actions: list[ClientCardRuleAction] | None = None


class ClientCardSourceMapping(BaseModel):
    response_path: str = Field(min_length=1)
    target_key: str = Field(min_length=1)


class ClientCardSourceConfigBase(BaseModel):
    technical_key: str | None = None
    name: str = Field(min_length=1)
    kind: ClientCardSourceKind = "http"
    is_active: bool = True
    method: ClientCardSourceMethod = "GET"
    url_template: str = Field(min_length=1)
    query_template_json: dict | None = None
    headers_template_json: dict | None = None
    body_template_json: dict | list | str | int | float | bool | None = None
    timeout_ms: int = Field(default=3000, ge=100, le=30000)
    mapping_json: list[ClientCardSourceMapping] = Field(default_factory=list)
    on_error: ClientCardSourceOnError = "ignore"


class ClientCardSourceCreate(ClientCardSourceConfigBase):
    pass


class ClientCardSourceUpdate(BaseModel):
    technical_key: str | None = None
    name: str | None = None
    kind: ClientCardSourceKind | None = None
    is_active: bool | None = None
    method: ClientCardSourceMethod | None = None
    url_template: str | None = None
    query_template_json: dict | None = None
    headers_template_json: dict | None = None
    body_template_json: dict | list | str | int | float | bool | None = None
    timeout_ms: int | None = Field(default=None, ge=100, le=30000)
    mapping_json: list[ClientCardSourceMapping] | None = None
    on_error: ClientCardSourceOnError | None = None


class ClientCardSourceConfigOut(ClientCardSourceConfigBase):
    id: str
    created_at: datetime
    updated_at: datetime


class ClientCardSourceTestRequest(BaseModel):
    context: dict = Field(default_factory=dict)


class ClientCardSourceTestResponse(BaseModel):
    http_status: int | None = None
    mapped_values: dict = Field(default_factory=dict)
    missing_response_paths: list[str] = Field(default_factory=list)
    duration_ms: int


class ClientCardSourceRunRequest(BaseModel):
    source_id: str = Field(min_length=1)


class ClientCardBlockUpdate(BaseModel):
    title: str | None = None
    color_scheme: ClientCardColorScheme | None = None
    sort_order: int | None = None
    is_active: bool | None = None
    show_in_card: bool | None = None
    show_in_editor: bool | None = None
    show_title: bool | None = None


class ClientCardFieldUpdate(BaseModel):
    label: str | None = None
    field_type: str | None = None
    block_key: str | None = None
    sort_order: int | None = None
    is_visible: bool | None = None
    is_visible_in_editor: bool | None = None
    show_when_false: bool | None = None
    is_editable: bool | None = None
    admin_only_edit: bool | None = None
    is_required: bool | None = None
    options: list[str] | None = None
    admin_only_values: list[str] | None = None
    option_renames: list[dict[str, str]] | None = None


class ClientCardMovePayload(BaseModel):
    direction: Literal["up", "down"]


class ClientCardBlockCreate(BaseModel):
    title: str = Field(min_length=1)
    color_scheme: ClientCardColorScheme = "gray"
    sort_order: int = 1000
    is_active: bool = True
    show_in_card: bool = True
    show_in_editor: bool = True
    show_title: bool = True


class ClientCardFieldCreate(BaseModel):
    label: str = Field(min_length=1)
    field_type: Literal["text", "textarea", "number", "date", "checkbox", "select", "phone", "url", "password"]
    block_key: str = Field(min_length=1)
    is_visible: bool = True
    is_visible_in_editor: bool = True
    show_when_false: bool = True
    is_editable: bool = True
    admin_only_edit: bool = False
    is_required: bool = False
    options: list[str] | None = None
    admin_only_values: list[str] | None = None
    sort_order: int | None = None


class MassNotificationRecipientOut(BaseModel):
    client_id: str
    client_name: str
    chat_id: str
    destination: str | None = None
    telegram_username: str | None = None
    telegram_display_name: str | None = None


class MassNotificationPayload(BaseModel):
    text: str = Field(min_length=1, max_length=4000)
    destination: str | None = None


class MassNotificationSendResult(BaseModel):
    ok: bool = True
    total: int = 0
    sent: int = 0
    failed: int = 0
    failed_recipients: list[str] = Field(default_factory=list)


class BotButtonMenuTypeOut(BaseModel):
    menu_name: str
    display_name: str | None = None
    destinations: list[str] = Field(default_factory=list)
    source_file: str
    ok: bool = True
    error: str | None = None
    buttons_count: int = 0
    menus_count: int = 0


class BotMenuCreatePayload(BaseModel):
    display_name: str = Field(min_length=1)
    destinations: list[str] = Field(default_factory=list)


class BotMenuDestinationPayload(BaseModel):
    destination: str = Field(min_length=1)


class BotButtonMovePayload(BaseModel):
    button_id: str = Field(min_length=1)
    menu_type: str = Field(min_length=1)
    parent_path: str = ""
    target_row: int | None = Field(default=None, ge=0, strict=True)
    target_index: int = Field(default=0, ge=0, strict=True)
    new_row: bool = True


class BotButtonStructureLeafOut(BaseModel):
    label: str
    workflow_type: Literal["ticket", "task", "auto", "group_task_child"]
    full_path: str
    technical_key: str
    source_button_key: str
    button_id: str | None = None
    parent_path: str = ""
    has_instruction: bool = False
    removed: bool = False
    is_enabled: bool = True
    stable_button_id: str | None = None
    is_archived: bool = False
    status: Literal["actual", "removed_changed"] = "actual"


class BotButtonGroupOut(BaseModel):
    label: str
    full_path: str
    technical_key: str
    button_id: str | None = None
    parent_path: str = ""
    children: list[BotButtonStructureLeafOut] = Field(default_factory=list)


class BotButtonMenuStructureOut(BaseModel):
    menu_name: str
    ok: bool = True
    error: str | None = None
    root_level_key: str | None = None
    tickets: list[BotButtonStructureLeafOut] = Field(default_factory=list)
    tasks: list[BotButtonStructureLeafOut] = Field(default_factory=list)
    autos: list[BotButtonStructureLeafOut] = Field(default_factory=list)
    group_tasks: list[BotButtonGroupOut] = Field(default_factory=list)
    removed_items: list[BotButtonStructureLeafOut] = Field(default_factory=list)
    tree_rows: list[dict] = Field(default_factory=list)


class BotButtonItemOut(BaseModel):
    button_key: str
    stable_button_id: str | None = None
    legacy_button_key: str | None = None
    menu_type: str
    workflow_type: str
    button_path: str
    button_label: str
    button_style: str | None = None
    label_override: str | None = None
    label_overridden: bool = False
    button_label_effective: str | None = None
    button_label_from: Literal["db"] = "db"
    instruction_text: str | None = None
    removed: bool = False
    currently_present: bool = True


class BotButtonItemsResponse(BaseModel):
    items: list[BotButtonItemOut] = Field(default_factory=list)


class BotButtonInstructionUpdate(BaseModel):
    instruction_text: str | None = None
    client_text: str | None = None
    client_text_overridden: bool | None = None
    system_text: str | None = None
    system_text_overridden: bool | None = None
    fixed_assignee_enabled: bool | None = None
    fixed_assignee_user_id: str | None = None
    freeze_on_create: bool | None = None
    suppress_client_answer: bool | None = None
    confirmation_enabled: bool | None = None
    confirmation_text: str | None = None
    confirmation_overridden: bool | None = None
    files_overridden: bool | None = None
    files: list[str] | None = None
    conditional_files_overridden: bool | None = None
    conditional_files: list[dict] | None = None
    automation_type: str | None = None
    telegram_group_id: str | None = None
    automation_key: str | None = None
    automation_overridden: bool | None = None
    is_enabled: bool | None = None
    visibility_overridden: bool | None = None
    label_override: str | None = None
    label_overridden: bool | None = None
    button_style: str | None = None



