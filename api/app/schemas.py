from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field


class TicketListItem(BaseModel):
    id: str
    ticket_number: int | None = None
    subject: str
    workflow_type: str = "ticket"
    intake_state: str = "ready"
    status: str
    is_frozen: bool
    seen_by_agent: bool
    client_name: str
    requester_display_name: str = ""
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
    message_type: str
    body: str
    author_user_id: str | None
    author_name: str | None = None
    telegram_message_id: str | None = None
    message_status: str = "normal"
    status_note: str | None = None
    replaced_message_id: str | None = None
    created_at: datetime
    attachments: list[TicketAttachmentOut] = Field(default_factory=list)


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
    custom_fields: dict = Field(default_factory=dict)


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
    custom_fields: dict | None = None


class TicketCard(BaseModel):
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
    client_name: str
    client_profile: ClientProfileOut
    active_attachment_preloads: list[dict] = Field(default_factory=list)
    messages: list[TicketMessageOut]


class TextPayload(BaseModel):
    text: str = Field(min_length=1)
    quote_message_id: str | None = None


class FreezePayload(BaseModel):
    freeze_until: datetime | None = None


class TicketAutoClosePayload(BaseModel):
    auto_close_disabled: bool


class AssignTicketsPayload(BaseModel):
    ticket_ids: list[str] = Field(min_length=1)
    assignee_id: str | None = None


class AdminBulkTicketsPayload(BaseModel):
    ticket_ids: list[str] = Field(min_length=1)


class AdminCloseResult(BaseModel):
    ok: bool = True
    closed: int = 0
    already_closed: int = 0
    not_found: list[str] = Field(default_factory=list)


class AdminDeleteResult(BaseModel):
    ok: bool = True
    deleted: int = 0
    not_found: list[str] = Field(default_factory=list)
    file_delete_warnings: list[str] = Field(default_factory=list)


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


class UserPreferencesOut(BaseModel):
    send_by_enter: bool = False


class UserPreferencesUpdate(BaseModel):
    send_by_enter: bool


class AgentOut(BaseModel):
    id: str
    full_name: str
    role: str = "agent"
    role_label: str | None = None
    availability_status: AvailabilityStatus
    updated_at: datetime | None = None


class SettingsUserOut(BaseModel):
    id: str
    full_name: str
    login: str | None = None
    role: str
    role_label: str
    is_active: bool
    availability_status: AvailabilityStatus
    legacy_ticket_count: int = 0
    created_at: datetime | None = None
    updated_at: datetime | None = None


class SettingsUserCreate(BaseModel):
    full_name: str = Field(min_length=1)
    login: str = Field(min_length=1)
    password: str = Field(min_length=1)
    role: UserRole
    is_active: bool = True
    availability_status: AvailabilityStatus = "offline"
    legacy_ticket_count: int = 0


class SettingsUserUpdate(BaseModel):
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
    legacy: int | None = None


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
    is_editable: bool
    is_required: bool = False
    is_system: bool
    options: list[str] | None = None


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
    is_editable: bool | None = None
    is_required: bool | None = None
    options: list[str] | None = None


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
    is_editable: bool = True
    is_required: bool = False
    options: list[str] | None = None
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


class BotButtonStructureLeafOut(BaseModel):
    label: str
    workflow_type: Literal["ticket", "task", "auto", "group_task_child"]
    full_path: str
    technical_key: str
    source_button_key: str
    has_instruction: bool = False
    removed: bool = False
    is_enabled: bool = True
    stable_button_id: str | None = None
    is_db_created: bool = False
    is_archived: bool = False
    status: Literal["actual", "removed_changed"] = "actual"


class BotButtonGroupOut(BaseModel):
    label: str
    full_path: str
    technical_key: str
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
    label_override: str | None = None
    label_overridden: bool = False
    button_label_effective: str | None = None
    button_label_from: Literal["txt", "db"] = "txt"
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
    confirmation_enabled: bool | None = None
    confirmation_text: str | None = None
    confirmation_overridden: bool | None = None
    files_overridden: bool | None = None
    files: list[str] | None = None
    conditional_files_overridden: bool | None = None
    conditional_files: list[dict] | None = None
    automation_type: str | None = None
    automation_key: str | None = None
    automation_overridden: bool | None = None
    is_enabled: bool | None = None
    visibility_overridden: bool | None = None
    label_override: str | None = None
    label_overridden: bool | None = None



