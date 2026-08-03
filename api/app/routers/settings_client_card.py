import re
import unicodedata
import uuid
from datetime import datetime
from types import SimpleNamespace

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..auth import CurrentUser, get_current_user, is_admin
from ..db import get_db
from ..models import ClientCardBlockConfig, ClientCardFieldConfig, ClientCardRuleConfig, ClientCardSourceConfig
from ..schemas import (
    ClientCardBlockConfigOut,
    ClientCardBlockCreate,
    ClientCardBlockUpdate,
    ClientCardConfigResponse,
    ClientCardFieldConfigOut,
    ClientCardFieldCreate,
    ClientCardFieldUpdate,
    ClientCardMovePayload,
    ClientCardRuleConfigOut,
    ClientCardRuleCreate,
    ClientCardRuleUpdate,
    ClientCardSourceConfigOut,
    ClientCardSourceCreate,
    ClientCardSourceTestRequest,
    ClientCardSourceTestResponse,
    ClientCardSourceUpdate,
)
from ..services.client_card_derivation import SYSTEM_PROFILE_FIELDS
from ..services.client_card_derivation import _save_rule_dependencies
from ..services.client_card import _field_editor_visibility_default, _field_options_out
from ..services.client_card_sources import (
    ClientCardSourceConfigError,
    ClientCardSourceError,
    execute_source,
    mapped_preview,
    source_template_field_refs,
    validate_source_payload,
)


router = APIRouter(prefix="/api/v1/settings", tags=["settings"])
SYSTEM_BLOCK_KEYS = {"tg", "profile", "department", "work", "verification"}
SORT_STEP = 10
NAMED_COLOR_SCHEMES = {"blue", "green", "yellow", "red", "gray", "purple"}
HEX_COLOR_RE = re.compile(r"^#[0-9a-fA-F]{6}$")
RULE_OPERATORS = {"always", "equals", "not_equals", "is_empty", "is_not_empty"}
RULE_ACTIONS = {"show", "hide", "readonly", "editable", "required", "optional", "set_value", "lookup_source"}
RULE_SCOPES = {"card", "editor", "both", "save"}
RULE_TARGET_TYPES = {"field", "block"}
RULE_VALUE_TYPES = {"literal", "field", "template", "map", "null"}
TEMPLATE_FIELD_RE = re.compile(r"\{\{([a-zA-Z0-9_]+)\}\}")
EDITOR_RUNTIME_SYSTEM_FIELD_KEYS = {
    "contact_name",
    "last_name",
    "destination",
    "sand_owner",
    "sales_group",
    "location_type",
    "anydesk",
    "internal_number",
    "bitrix_login",
    "bitrix_password",
    "bitrix_id",
    "is_verified",
}

CYRILLIC_TO_LATIN = {
    "а": "a", "б": "b", "в": "v", "г": "g", "д": "d", "е": "e", "ё": "e", "ж": "zh", "з": "z", "и": "i",
    "й": "y", "к": "k", "л": "l", "м": "m", "н": "n", "о": "o", "п": "p", "р": "r", "с": "s", "т": "t",
    "у": "u", "ф": "f", "х": "h", "ц": "ts", "ч": "ch", "ш": "sh", "щ": "sch", "ъ": "", "ы": "y", "ь": "",
    "э": "e", "ю": "yu", "я": "ya",
}


def _error(status_code: int, code: str, message: str):
    raise HTTPException(status_code=status_code, detail={"error": {"code": code, "message": message, "details": {}}})


def _error_details(status_code: int, code: str, message: str, details: dict):
    raise HTTPException(status_code=status_code, detail={"error": {"code": code, "message": message, "details": details}})


def _require_admin(user: CurrentUser) -> None:
    if not is_admin(user):
        _error(403, "admin_only", "Настройки доступны только администратору")


def _slugify_title(value: str) -> str:
    lowered = value.strip().lower()
    transliterated = "".join(CYRILLIC_TO_LATIN.get(ch, ch) for ch in lowered)
    transliterated = unicodedata.normalize("NFKD", transliterated)
    transliterated = transliterated.encode("ascii", "ignore").decode("ascii")
    transliterated = re.sub(r"[^a-z0-9]+", "_", transliterated).strip("_")
    return transliterated or "block"


def _generate_custom_field_key(db: Session, label: str) -> str:
    base_slug = _slugify_title(label)
    if not base_slug:
        base_slug = "field"
    base_key = f"custom_{base_slug}"
    key = base_key
    index = 2
    while db.execute(select(ClientCardFieldConfig.id).where(ClientCardFieldConfig.key == key)).first() is not None:
        key = f"{base_key}_{index}"
        index += 1
    return key


def _normalize_color_scheme(value: str) -> str:
    color = (value or "").strip()
    named = color.lower()
    if named in NAMED_COLOR_SCHEMES:
        return named
    if HEX_COLOR_RE.fullmatch(color):
        return color.lower()
    _error(422, "color_scheme_invalid", "Color must be a named client card color or #RRGGBB")


def _block_out(block: ClientCardBlockConfig, fields: list[ClientCardFieldConfigOut] | None = None) -> ClientCardBlockConfigOut:
    return ClientCardBlockConfigOut(
        id=block.id,
        key=block.key,
        title=block.title,
        color_scheme=block.color_scheme,
        sort_order=block.sort_order,
        is_active=bool(block.is_active),
        show_in_card=block.show_in_card is not False,
        show_in_editor=block.show_in_editor is not False,
        show_title=block.show_title is not False,
        fields=fields or [],
    )


def _field_visible_in_editor(field: ClientCardFieldConfig) -> bool:
    value = getattr(field, "is_visible_in_editor", None)
    if value is None:
        return _field_editor_visibility_default(field)
    return bool(value)


def _field_out(field: ClientCardFieldConfig) -> ClientCardFieldConfigOut:
    return ClientCardFieldConfigOut(
        id=field.id,
        key=field.key,
        label=field.label,
        field_type=field.field_type,
        block_key=field.block_key,
        sort_order=field.sort_order,
        is_visible=bool(field.is_visible),
        is_visible_in_editor=_field_visible_in_editor(field),
        is_editable=bool(field.is_editable),
        is_required=bool(field.is_required),
        is_system=bool(field.is_system),
        options=_field_options_out(field),
    )


def _rule_out(rule: ClientCardRuleConfig) -> ClientCardRuleConfigOut:
    return ClientCardRuleConfigOut(
        id=rule.id,
        technical_key=rule.technical_key,
        name=rule.name,
        is_active=bool(rule.is_active),
        is_system=bool(rule.is_system),
        sort_order=rule.sort_order,
        condition_field_key=rule.condition_field_key or None,
        condition_operator=rule.condition_operator,
        condition_value=rule.condition_value,
        actions=rule.actions_json or [],
    )


def _source_mapping_for_storage(mapping: list | None) -> list[dict]:
    stored = []
    for item in mapping or []:
        if hasattr(item, "response_path"):
            stored.append({"response_path": item.response_path, "target_key": item.target_key})
        else:
            stored.append({"response_path": item.get("response_path"), "target_key": item.get("target_key")})
    return stored


def _source_out(source: ClientCardSourceConfig) -> ClientCardSourceConfigOut:
    return ClientCardSourceConfigOut(
        id=source.id,
        technical_key=source.technical_key,
        name=source.name,
        kind=source.kind,
        is_active=bool(source.is_active),
        method=source.method,
        url_template=source.url_template,
        query_template_json=source.query_template_json,
        headers_template_json=source.headers_template_json,
        body_template_json=source.body_template_json,
        timeout_ms=source.timeout_ms,
        mapping_json=source.mapping_json or [],
        on_error=source.on_error,
        created_at=source.created_at,
        updated_at=source.updated_at,
    )


def _is_custom_block(block: ClientCardBlockConfig) -> bool:
    return block.key not in SYSTEM_BLOCK_KEYS and block.key.startswith("custom_block_")


def _reindex_blocks(blocks: list[ClientCardBlockConfig]) -> None:
    now = datetime.utcnow()
    for index, block in enumerate(blocks, start=1):
        block.sort_order = index * SORT_STEP
        block.updated_at = now


def _reindex_fields(fields: list[ClientCardFieldConfig]) -> None:
    now = datetime.utcnow()
    for index, field in enumerate(fields, start=1):
        field.sort_order = index * SORT_STEP
        field.updated_at = now


def _ordered_blocks(db: Session) -> list[ClientCardBlockConfig]:
    return db.execute(
        select(ClientCardBlockConfig).order_by(ClientCardBlockConfig.sort_order.asc(), ClientCardBlockConfig.created_at.asc())
    ).scalars().all()


def _ordered_fields_for_block(db: Session, block_key: str) -> list[ClientCardFieldConfig]:
    return db.execute(
        select(ClientCardFieldConfig)
        .where(ClientCardFieldConfig.block_key == block_key)
        .order_by(ClientCardFieldConfig.sort_order.asc(), ClientCardFieldConfig.created_at.asc())
    ).scalars().all()


def _ordered_rules(db: Session, *, active_only: bool = False) -> list[ClientCardRuleConfig]:
    stmt = select(ClientCardRuleConfig)
    if active_only:
        stmt = stmt.where(ClientCardRuleConfig.is_active.is_(True))
    return db.execute(stmt.order_by(ClientCardRuleConfig.sort_order.asc(), ClientCardRuleConfig.id.asc())).scalars().all()


def _ordered_sources(db: Session) -> list[ClientCardSourceConfig]:
    return db.execute(
        select(ClientCardSourceConfig).order_by(ClientCardSourceConfig.created_at.asc())
    ).scalars().all()


def _source_technical_key(value: str | None) -> str | None:
    cleaned = (value or "").strip()
    return cleaned or None


def _source_payload_data(payload) -> dict:
    return payload.model_dump(exclude_unset=True)


def _reject_null_source_required_fields(data: dict) -> None:
    required = {"name", "kind", "is_active", "method", "url_template", "timeout_ms", "on_error"}
    for key in sorted(required & data.keys()):
        if data[key] is None:
            _error(422, "source_required_field_null", f"Source field '{key}' cannot be null")


def _validate_rule_payload(
    db: Session,
    *,
    condition_field_key: str | None,
    condition_operator: str,
    condition_value: str | None,
    actions: list,
) -> None:
    if condition_operator not in RULE_OPERATORS:
        _error(422, "rule_operator_invalid", "Unsupported client card rule operator")
    if condition_operator != "always" and not (condition_field_key or "").strip():
        _error(422, "rule_condition_field_required", "Rule condition field is required for this operator")
    if condition_operator in {"equals", "not_equals"} and not (condition_value or "").strip():
        _error(422, "rule_condition_value_required", "Rule condition value is required for this operator")
    if not actions:
        _error(422, "rule_actions_required", "Rule must contain at least one action")
    fields_by_key = {field.key: field for field in db.execute(select(ClientCardFieldConfig)).scalars().all()}
    field_keys = set(fields_by_key.keys())
    custom_field_keys = {field.key for field in fields_by_key.values() if not field.is_system}
    save_context_keys = set(SYSTEM_PROFILE_FIELDS) | custom_field_keys
    block_keys = set(db.execute(select(ClientCardBlockConfig.key)).scalars().all())
    has_save_action = any(
        (action.action if hasattr(action, "action") else action.get("action")) in {"set_value", "lookup_source"}
        and (action.scope if hasattr(action, "scope") else action.get("scope")) == "save"
        for action in actions
    )
    if has_save_action and condition_operator != "always" and condition_field_key not in save_context_keys:
        _error(422, "rule_save_condition_field_unsupported", "Condition field is not available to save-time derivation")
    if not has_save_action and condition_operator != "always" and db.execute(select(ClientCardFieldConfig.id).where(ClientCardFieldConfig.key == condition_field_key)).first() is None:
        _error(422, "rule_condition_field_unknown", "Condition field is not configured")
    save_trigger_deps: set[str] = set()
    if has_save_action and condition_operator != "always" and condition_field_key:
        save_trigger_deps.add(condition_field_key)
    for action in actions:
        target_type = action.target_type if hasattr(action, "target_type") else action.get("target_type")
        target_key = action.target_key if hasattr(action, "target_key") else action.get("target_key")
        action_name = action.action if hasattr(action, "action") else action.get("action")
        scope = action.scope if hasattr(action, "scope") else action.get("scope")
        value_type = action.value_type if hasattr(action, "value_type") else action.get("value_type")
        source_key = action.source_key if hasattr(action, "source_key") else action.get("source_key")
        source_id = action.source_id if hasattr(action, "source_id") else action.get("source_id")
        value_map = action.map if hasattr(action, "map") else action.get("map")
        if action_name != "lookup_source" and target_type not in RULE_TARGET_TYPES:
            _error(422, "rule_target_type_invalid", "Unsupported client card rule target type")
        if action_name not in RULE_ACTIONS:
            _error(422, "rule_action_invalid", "Unsupported client card rule action")
        if action_name == "lookup_source":
            if scope != "save":
                _error(422, "rule_lookup_source_scope_invalid", "lookup_source actions support only save scope")
            if not (source_id or "").strip():
                _error(422, "rule_source_id_required", "lookup_source action requires source_id")
            source = db.get(ClientCardSourceConfig, source_id)
            if source is None:
                _error(422, "rule_source_unknown", "lookup_source source does not exist")
            try:
                refs = source_template_field_refs(source)
            except ClientCardSourceConfigError as exc:
                _error(422, "source_config_invalid", str(exc))
            for ref in refs:
                if ref not in save_context_keys:
                    _error(422, "rule_save_template_ref_unsupported", "Template field is not available to save-time derivation")
                save_trigger_deps.add(ref)
            continue
        if action_name == "set_value":
            if target_type != "field" or scope != "save":
                _error(422, "rule_set_value_scope_invalid", "set_value actions support only field targets with save scope")
            if value_type not in RULE_VALUE_TYPES:
                _error(422, "rule_value_type_invalid", "Unsupported set_value type")
            if target_key not in save_context_keys:
                _error(422, "rule_save_target_unsupported", "Target field is not persistable by save-time derivation")
            if value_type in {"field", "map"}:
                if not (source_key or "").strip():
                    _error(422, "rule_source_field_required", "Source field is required for this value type")
                if source_key not in save_context_keys:
                    _error(422, "rule_save_source_unsupported", "Source field is not available to save-time derivation")
                save_trigger_deps.add(source_key)
            if value_type == "template":
                for ref in TEMPLATE_FIELD_RE.findall(str(_rule_action_value(action, "value") or "")):
                    if ref not in save_context_keys:
                        _error(422, "rule_save_template_ref_unsupported", "Template field is not available to save-time derivation")
                    save_trigger_deps.add(ref)
            if value_type == "map" and not isinstance(value_map, dict):
                _error(422, "rule_map_required", "Map value type requires a mapping")
            if target_key not in field_keys:
                _error(422, "rule_target_field_unknown", "Target field is not configured")
            continue
        if scope == "save":
            _error(422, "rule_save_scope_action_invalid", "Save scope supports only set_value actions")
        if target_type == "block" and action_name not in {"show", "hide"}:
            _error(422, "rule_block_action_invalid", "Block rules support only show and hide actions")
        if target_type == "field" and action_name not in {"show", "hide"} and scope != "editor":
            _error(422, "rule_field_action_scope_invalid", "This field action is supported only in editor scope")
        if scope not in RULE_SCOPES:
            _error(422, "rule_scope_invalid", "Unsupported client card rule scope")
        if target_type == "field" and target_key not in field_keys:
            _error(422, "rule_target_field_unknown", "Target field is not configured")
        if target_type == "field" and scope in {"editor", "both"}:
            target_field = fields_by_key.get(target_key)
            if target_field is not None and target_field.is_system and target_key not in EDITOR_RUNTIME_SYSTEM_FIELD_KEYS:
                _error(422, "rule_target_editor_unsupported", "Target field cannot be controlled in the editor runtime")
        if target_type == "block" and target_key not in block_keys:
            _error(422, "rule_target_block_unknown", "Target block is not configured")
    if has_save_action and not save_trigger_deps:
        _error(422, "rule_save_trigger_required", "Save-time derivation rule requires a condition field, source field, or template reference")


def _rule_actions_for_storage(actions: list) -> list[dict]:
    stored = []
    for action in actions:
        if hasattr(action, "target_key"):
            item = {
                "target_type": action.target_type,
                "target_key": action.target_key,
                "action": action.action,
                "scope": action.scope,
            }
            if action.action == "set_value":
                item["value_type"] = action.value_type
                if action.value is not None:
                    item["value"] = action.value
                if action.source_key is not None:
                    item["source_key"] = action.source_key
                if action.map is not None:
                    item["map"] = action.map
            if action.action == "lookup_source":
                item.pop("target_key", None)
                item.pop("target_type", None)
                item["source_id"] = action.source_id
            stored.append(item)
        else:
            item = {
                "target_type": action.get("target_type", "field"),
                "target_key": action.get("target_key"),
                "action": action.get("action"),
                "scope": action.get("scope", "editor"),
            }
            if item["action"] == "set_value":
                item["value_type"] = action.get("value_type")
                if "value" in action:
                    item["value"] = action.get("value")
                if "source_key" in action:
                    item["source_key"] = action.get("source_key")
                if "map" in action:
                    item["map"] = action.get("map")
            if item["action"] == "lookup_source":
                item.pop("target_key", None)
                item.pop("target_type", None)
                item["source_id"] = action.get("source_id")
            stored.append(item)
    return stored


def _rule_condition_value_for_storage(operator: str, value: str | None) -> str | None:
    if operator in {"always", "is_empty", "is_not_empty"}:
        return None
    return (value or "").strip() or None


def _rule_action_value(action, key: str):
    return getattr(action, key) if hasattr(action, key) else action.get(key)


def _rules_using_field(db: Session, field_key: str) -> list[ClientCardRuleConfig]:
    matches = []
    for rule in _ordered_rules(db):
        if rule.condition_field_key == field_key:
            matches.append(rule)
            continue
        for action in rule.actions_json or []:
            if action.get("target_type", "field") == "field" and action.get("target_key") == field_key:
                matches.append(rule)
                break
            if action.get("action") == "set_value" and action.get("source_key") == field_key:
                matches.append(rule)
                break
            if action.get("action") == "set_value" and f"{{{{{field_key}}}}}" in str(action.get("value") or ""):
                matches.append(rule)
                break
            if action.get("action") == "lookup_source":
                source = db.get(ClientCardSourceConfig, action.get("source_id") or "")
                if source is not None and field_key in source_template_field_refs(source):
                    matches.append(rule)
                    break
    return matches


def _rules_using_block(db: Session, block_key: str) -> list[ClientCardRuleConfig]:
    matches = []
    for rule in _ordered_rules(db):
        for action in rule.actions_json or []:
            if action.get("target_type") == "block" and action.get("target_key") == block_key:
                matches.append(rule)
                break
    return matches


def _rule_refs(rules: list[ClientCardRuleConfig]) -> list[dict]:
    return [{"id": rule.id, "name": rule.name, "technical_key": rule.technical_key} for rule in rules]


def _source_refs(sources: list[ClientCardSourceConfig]) -> list[dict]:
    return [{"id": source.id, "name": source.name, "technical_key": source.technical_key} for source in sources]


def _sources_mapping_to_field(db: Session, field_key: str) -> list[ClientCardSourceConfig]:
    matches = []
    for source in _ordered_sources(db):
        for mapping in source.mapping_json or []:
            if mapping.get("target_key") == field_key:
                matches.append(source)
                break
    return matches


def _sources_template_using_field(db: Session, field_key: str) -> list[ClientCardSourceConfig]:
    matches = []
    for source in _ordered_sources(db):
        if field_key in source_template_field_refs(source):
            matches.append(source)
    return matches


def _rules_using_source(db: Session, source_id: str) -> list[ClientCardRuleConfig]:
    matches = []
    for rule in _ordered_rules(db):
        for action in rule.actions_json or []:
            if action.get("action") == "lookup_source" and action.get("source_id") == source_id:
                matches.append(rule)
                break
    return matches


def _source_clone(source: ClientCardSourceConfig, **overrides) -> SimpleNamespace:
    data = {
        "id": source.id,
        "technical_key": source.technical_key,
        "name": source.name,
        "kind": source.kind,
        "is_active": bool(source.is_active),
        "method": source.method,
        "url_template": source.url_template,
        "query_template_json": source.query_template_json,
        "headers_template_json": source.headers_template_json,
        "body_template_json": source.body_template_json,
        "timeout_ms": source.timeout_ms,
        "mapping_json": source.mapping_json or [],
        "on_error": source.on_error,
    }
    data.update(overrides)
    return SimpleNamespace(**data)


def _validate_referencing_rules_keep_dependencies(db: Session, source: ClientCardSourceConfig, prospective: SimpleNamespace) -> None:
    rules = _rules_using_source(db, source.id)
    if not rules:
        return
    sources_by_id = {item.id: item for item in _ordered_sources(db)}
    sources_by_id[source.id] = prospective
    for rule in rules:
        if rule.is_active and not _save_rule_dependencies(rule, sources_by_id=sources_by_id):
            _error(422, "rule_save_trigger_required", "Source update would leave a lookup_source save rule without trigger dependencies")


def _serialize_client_card_config(db: Session) -> ClientCardConfigResponse:
    blocks = db.execute(
        select(ClientCardBlockConfig).order_by(ClientCardBlockConfig.sort_order.asc(), ClientCardBlockConfig.created_at.asc())
    ).scalars().all()
    fields = db.execute(
        select(ClientCardFieldConfig).order_by(ClientCardFieldConfig.sort_order.asc(), ClientCardFieldConfig.created_at.asc())
    ).scalars().all()
    fields_by_block: dict[str, list[ClientCardFieldConfigOut]] = {}
    for field in fields:
        fields_by_block.setdefault(field.block_key, []).append(_field_out(field))
    return ClientCardConfigResponse(
        blocks=[_block_out(block, fields_by_block.get(block.key, [])) for block in blocks],
        rules=[_rule_out(rule) for rule in _ordered_rules(db)],
        sources=[_source_out(source) for source in _ordered_sources(db)],
    )


@router.get("/client-card", response_model=ClientCardConfigResponse)
def get_client_card_config(db: Session = Depends(get_db), user: CurrentUser = Depends(get_current_user)):
    _require_admin(user)
    return _serialize_client_card_config(db)


@router.get("/client-card/rules", response_model=list[ClientCardRuleConfigOut])
def list_client_card_rules(db: Session = Depends(get_db), user: CurrentUser = Depends(get_current_user)):
    _require_admin(user)
    return [_rule_out(rule) for rule in _ordered_rules(db)]


@router.get("/client-card/sources", response_model=list[ClientCardSourceConfigOut])
def list_client_card_sources(db: Session = Depends(get_db), user: CurrentUser = Depends(get_current_user)):
    _require_admin(user)
    return [_source_out(source) for source in _ordered_sources(db)]


@router.post("/client-card/sources", response_model=ClientCardSourceConfigOut)
def create_client_card_source(
    payload: ClientCardSourceCreate,
    db: Session = Depends(get_db),
    user: CurrentUser = Depends(get_current_user),
):
    _require_admin(user)
    name = payload.name.strip()
    if not name:
        _error(422, "source_name_required", "Source name is required")
    technical_key = _source_technical_key(payload.technical_key)
    if technical_key and db.execute(select(ClientCardSourceConfig.id).where(ClientCardSourceConfig.technical_key == technical_key)).first():
        _error(409, "source_technical_key_exists", "Source technical key already exists")
    mapping = _source_mapping_for_storage(payload.mapping_json)
    validate_source_payload(
        db,
        method=payload.method,
        kind=payload.kind,
        on_error=payload.on_error,
        url_template=payload.url_template,
        mapping=mapping,
        query=payload.query_template_json,
        headers=payload.headers_template_json,
        body=payload.body_template_json,
    )
    now = datetime.utcnow()
    source = ClientCardSourceConfig(
        id=str(uuid.uuid4()),
        technical_key=technical_key,
        name=name,
        kind=payload.kind,
        is_active=payload.is_active,
        method=payload.method,
        url_template=payload.url_template.strip(),
        query_template_json=payload.query_template_json,
        headers_template_json=payload.headers_template_json,
        body_template_json=payload.body_template_json,
        timeout_ms=payload.timeout_ms,
        mapping_json=mapping,
        on_error=payload.on_error,
        created_at=now,
        updated_at=now,
    )
    db.add(source)
    db.commit()
    db.refresh(source)
    return _source_out(source)


@router.patch("/client-card/sources/{source_id}", response_model=ClientCardSourceConfigOut)
def update_client_card_source(
    source_id: str,
    payload: ClientCardSourceUpdate,
    db: Session = Depends(get_db),
    user: CurrentUser = Depends(get_current_user),
):
    _require_admin(user)
    source = db.get(ClientCardSourceConfig, source_id)
    if source is None:
        _error(404, "source_not_found", "Source not found")
    data = _source_payload_data(payload)
    _reject_null_source_required_fields(data)
    next_name = source.name if "name" not in data else str(data["name"]).strip()
    if not next_name:
        _error(422, "source_name_required", "Source name is required")
    next_technical_key = source.technical_key
    if "technical_key" in data:
        next_technical_key = _source_technical_key(data["technical_key"])
    if next_technical_key and next_technical_key != source.technical_key:
        exists = db.execute(select(ClientCardSourceConfig.id).where(ClientCardSourceConfig.technical_key == next_technical_key)).scalar_one_or_none()
        if exists:
            _error(409, "source_technical_key_exists", "Source technical key already exists")
    next_mapping = source.mapping_json or []
    if "mapping_json" in data:
        next_mapping = _source_mapping_for_storage(payload.mapping_json or [])
    validate_source_payload(
        db,
        method=data.get("method", source.method),
        kind=data.get("kind", source.kind),
        on_error=data.get("on_error", source.on_error),
        url_template=data.get("url_template", source.url_template),
        mapping=next_mapping,
        query=data.get("query_template_json", source.query_template_json),
        headers=data.get("headers_template_json", source.headers_template_json),
        body=data.get("body_template_json", source.body_template_json),
    )
    prospective = _source_clone(
        source,
        kind=data.get("kind", source.kind),
        is_active=data.get("is_active", source.is_active),
        method=data.get("method", source.method),
        url_template=data.get("url_template", source.url_template),
        query_template_json=data.get("query_template_json", source.query_template_json),
        headers_template_json=data.get("headers_template_json", source.headers_template_json),
        body_template_json=data.get("body_template_json", source.body_template_json),
        timeout_ms=data.get("timeout_ms", source.timeout_ms),
        mapping_json=next_mapping,
        on_error=data.get("on_error", source.on_error),
    )
    _validate_referencing_rules_keep_dependencies(db, source, prospective)
    source.name = next_name
    source.technical_key = next_technical_key
    if "kind" in data:
        source.kind = data["kind"]
    if "is_active" in data:
        source.is_active = data["is_active"]
    if "method" in data:
        source.method = data["method"]
    if "url_template" in data:
        source.url_template = str(data["url_template"]).strip()
    if "query_template_json" in data:
        source.query_template_json = data["query_template_json"]
    if "headers_template_json" in data:
        source.headers_template_json = data["headers_template_json"]
    if "body_template_json" in data:
        source.body_template_json = data["body_template_json"]
    if "timeout_ms" in data:
        source.timeout_ms = data["timeout_ms"]
    if "mapping_json" in data:
        source.mapping_json = next_mapping
    if "on_error" in data:
        source.on_error = data["on_error"]
    source.updated_at = datetime.utcnow()
    db.commit()
    db.refresh(source)
    return _source_out(source)


@router.delete("/client-card/sources/{source_id}", response_model=list[ClientCardSourceConfigOut])
def delete_client_card_source(
    source_id: str,
    db: Session = Depends(get_db),
    user: CurrentUser = Depends(get_current_user),
):
    _require_admin(user)
    source = db.get(ClientCardSourceConfig, source_id)
    if source is None:
        _error(404, "source_not_found", "Source not found")
    rules = _rules_using_source(db, source_id)
    if rules:
        _error_details(
            409,
            "source_rule_reference_in_use",
            "Source is used by client card rules",
            {"rules": _rule_refs(rules)},
        )
    db.delete(source)
    db.commit()
    return [_source_out(item) for item in _ordered_sources(db)]


@router.post("/client-card/sources/{source_id}/test", response_model=ClientCardSourceTestResponse)
def test_client_card_source(
    source_id: str,
    payload: ClientCardSourceTestRequest,
    db: Session = Depends(get_db),
    user: CurrentUser = Depends(get_current_user),
):
    _require_admin(user)
    source = db.get(ClientCardSourceConfig, source_id)
    if source is None:
        _error(404, "source_not_found", "Source not found")
    try:
        result = execute_source(source, payload.context or {})
        mapped, missing = mapped_preview(db, source, result.payload)
    except ClientCardSourceConfigError as exc:
        _error(422, "source_config_invalid", str(exc))
    except ClientCardSourceError as exc:
        _error(502, "source_test_failed", str(exc))
    return ClientCardSourceTestResponse(
        http_status=result.http_status,
        mapped_values=mapped,
        missing_response_paths=missing,
        duration_ms=result.duration_ms,
    )


@router.post("/client-card/rules", response_model=ClientCardRuleConfigOut)
def create_client_card_rule(
    payload: ClientCardRuleCreate,
    db: Session = Depends(get_db),
    user: CurrentUser = Depends(get_current_user),
):
    _require_admin(user)
    name = payload.name.strip()
    if not name:
        _error(422, "rule_name_required", "Rule name is required")
    condition_field_key = (payload.condition_field_key or "").strip()
    _validate_rule_payload(
        db,
        condition_field_key=condition_field_key if payload.condition_operator != "always" else "",
        condition_operator=payload.condition_operator,
        condition_value=payload.condition_value,
        actions=payload.actions,
    )
    now = datetime.utcnow()
    rule = ClientCardRuleConfig(
        id=str(uuid.uuid4()),
        name=name,
        is_active=payload.is_active,
        is_system=False,
        sort_order=payload.sort_order,
        condition_field_key=condition_field_key if payload.condition_operator != "always" else "",
        condition_operator=payload.condition_operator,
        condition_value=_rule_condition_value_for_storage(payload.condition_operator, payload.condition_value),
        actions_json=_rule_actions_for_storage(payload.actions),
        created_at=now,
        updated_at=now,
    )
    db.add(rule)
    db.commit()
    db.refresh(rule)
    return _rule_out(rule)


@router.patch("/client-card/rules/{rule_id}", response_model=ClientCardRuleConfigOut)
def update_client_card_rule(
    rule_id: str,
    payload: ClientCardRuleUpdate,
    db: Session = Depends(get_db),
    user: CurrentUser = Depends(get_current_user),
):
    _require_admin(user)
    rule = db.get(ClientCardRuleConfig, rule_id)
    if rule is None:
        _error(404, "rule_not_found", "Rule not found")
    next_name = rule.name if payload.name is None else payload.name.strip()
    if not next_name:
        _error(422, "rule_name_required", "Rule name is required")
    next_condition_field_key = rule.condition_field_key if payload.condition_field_key is None else payload.condition_field_key.strip()
    next_condition_operator = rule.condition_operator if payload.condition_operator is None else payload.condition_operator
    payload_data = payload.model_dump(exclude_unset=True)
    next_condition_value = (
        payload_data["condition_value"]
        if "condition_value" in payload_data
        else rule.condition_value
    )
    next_actions = payload.actions if payload.actions is not None else (rule.actions_json or [])
    _validate_rule_payload(
        db,
        condition_field_key=next_condition_field_key,
        condition_operator=next_condition_operator,
        condition_value=next_condition_value,
        actions=next_actions,
    )
    rule.name = next_name
    if payload.is_active is not None:
        rule.is_active = payload.is_active
    if payload.sort_order is not None:
        rule.sort_order = payload.sort_order
    rule.condition_field_key = next_condition_field_key if next_condition_operator != "always" else ""
    rule.condition_operator = next_condition_operator
    if "condition_value" in payload_data or "condition_operator" in payload_data:
        rule.condition_value = _rule_condition_value_for_storage(next_condition_operator, next_condition_value)
    if payload.actions is not None:
        rule.actions_json = _rule_actions_for_storage(payload.actions)
    rule.updated_at = datetime.utcnow()
    db.commit()
    db.refresh(rule)
    return _rule_out(rule)


@router.delete("/client-card/rules/{rule_id}", response_model=ClientCardConfigResponse)
def delete_client_card_rule(
    rule_id: str,
    db: Session = Depends(get_db),
    user: CurrentUser = Depends(get_current_user),
):
    _require_admin(user)
    rule = db.get(ClientCardRuleConfig, rule_id)
    if rule is None:
        _error(404, "rule_not_found", "Rule not found")
    if rule.is_system:
        _error(403, "system_rule_delete_forbidden", "System rule cannot be deleted")
    db.delete(rule)
    db.commit()
    return _serialize_client_card_config(db)


@router.post("/client-card/rules/{rule_id}/move", response_model=ClientCardConfigResponse)
def move_client_card_rule(
    rule_id: str,
    payload: ClientCardMovePayload,
    db: Session = Depends(get_db),
    user: CurrentUser = Depends(get_current_user),
):
    _require_admin(user)
    rules = _ordered_rules(db)
    index = next((i for i, item in enumerate(rules) if item.id == rule_id), None)
    if index is None:
        _error(404, "rule_not_found", "Rule not found")
    target_index = index - 1 if payload.direction == "up" else index + 1
    if target_index < 0 or target_index >= len(rules):
        now = datetime.utcnow()
        for item_index, rule in enumerate(rules, start=1):
            rule.sort_order = item_index * SORT_STEP
            rule.updated_at = now
        db.commit()
        return _serialize_client_card_config(db)
    rules[index], rules[target_index] = rules[target_index], rules[index]
    now = datetime.utcnow()
    for item_index, rule in enumerate(rules, start=1):
        rule.sort_order = item_index * SORT_STEP
        rule.updated_at = now
    db.commit()
    return _serialize_client_card_config(db)


@router.patch("/client-card/blocks/{block_id}", response_model=ClientCardBlockConfigOut)
def update_client_card_block(
    block_id: str,
    payload: ClientCardBlockUpdate,
    db: Session = Depends(get_db),
    user: CurrentUser = Depends(get_current_user),
):
    _require_admin(user)
    block = db.get(ClientCardBlockConfig, block_id)
    if block is None:
        _error(404, "block_not_found", "Блок не найден")
    if payload.title is not None:
        title = payload.title.strip()
        if not title:
            _error(422, "title_required", "Название блока обязательно")
        block.title = title
    if payload.color_scheme is not None:
        block.color_scheme = _normalize_color_scheme(payload.color_scheme)
    if payload.sort_order is not None:
        block.sort_order = payload.sort_order
    if payload.is_active is not None:
        block.is_active = payload.is_active
    if payload.show_in_card is not None:
        block.show_in_card = payload.show_in_card
    if payload.show_in_editor is not None:
        block.show_in_editor = payload.show_in_editor
    if payload.show_title is not None:
        block.show_title = payload.show_title
    block.updated_at = datetime.utcnow()
    db.commit()
    db.refresh(block)
    return _block_out(block)


@router.post("/client-card/blocks/{block_id}/move", response_model=ClientCardConfigResponse)
def move_client_card_block(
    block_id: str,
    payload: ClientCardMovePayload,
    db: Session = Depends(get_db),
    user: CurrentUser = Depends(get_current_user),
):
    _require_admin(user)
    blocks = _ordered_blocks(db)
    index = next((i for i, block in enumerate(blocks) if block.id == block_id), None)
    if index is None:
        _error(404, "block_not_found", "Block not found")
    target_index = index - 1 if payload.direction == "up" else index + 1
    if target_index < 0 or target_index >= len(blocks):
        _reindex_blocks(blocks)
        db.commit()
        return _serialize_client_card_config(db)
    blocks[index], blocks[target_index] = blocks[target_index], blocks[index]
    _reindex_blocks(blocks)
    db.commit()
    return _serialize_client_card_config(db)


@router.delete("/client-card/blocks/{block_id}", response_model=ClientCardConfigResponse)
def delete_client_card_block(
    block_id: str,
    db: Session = Depends(get_db),
    user: CurrentUser = Depends(get_current_user),
):
    _require_admin(user)
    block = db.get(ClientCardBlockConfig, block_id)
    if block is None:
        _error(404, "block_not_found", "Block not found")
    if not _is_custom_block(block):
        _error(403, "system_block_delete_forbidden", "System block cannot be deleted")
    block_rules = _rules_using_block(db, block.key)
    if block_rules:
        _error_details(
            409,
            "rule_target_in_use",
            "Block is used by client card rules",
            {"rules": _rule_refs(block_rules)},
        )
    fields = _ordered_fields_for_block(db, block.key)
    if any(field.is_system for field in fields):
        _error(403, "system_block_fields_delete_forbidden", "Block contains system fields")
    field_rules = []
    field_sources = []
    for field in fields:
        field_rules.extend(_rules_using_field(db, field.key))
        field_sources.extend(_sources_mapping_to_field(db, field.key))
        field_sources.extend(_sources_template_using_field(db, field.key))
    if field_rules:
        _error_details(
            409,
            "rule_condition_in_use",
            "Block contains fields used by client card rules",
            {"rules": _rule_refs(list({rule.id: rule for rule in field_rules}.values()))},
        )
    if field_sources:
        _error_details(
            409,
            "source_mapping_target_in_use",
            "Block contains fields used by client card sources",
            {"sources": _source_refs(list({source.id: source for source in field_sources}.values()))},
        )
    for field in fields:
        db.delete(field)
    db.delete(block)
    db.flush()
    _reindex_blocks(_ordered_blocks(db))
    db.commit()
    return _serialize_client_card_config(db)


@router.patch("/client-card/fields/{field_id}", response_model=ClientCardFieldConfigOut)
def update_client_card_field(
    field_id: str,
    payload: ClientCardFieldUpdate,
    db: Session = Depends(get_db),
    user: CurrentUser = Depends(get_current_user),
):
    _require_admin(user)
    field = db.get(ClientCardFieldConfig, field_id)
    if field is None:
        _error(404, "field_not_found", "Поле не найдено")
    if field.is_system:
        if payload.field_type is not None and payload.field_type != field.field_type:
            _error(403, "readonly_system_field_type", "Тип системного поля нельзя изменять")
        if payload.is_editable is not None and payload.is_editable != field.is_editable:
            _error(403, "readonly_system_field_editable", "System field editable flag cannot be changed")
        if payload.is_required is not None and payload.is_required != field.is_required:
            _error(403, "readonly_system_field_required", "System field required flag cannot be changed")
    if payload.label is not None:
        label = payload.label.strip()
        if not label:
            _error(422, "label_required", "Название поля обязательно")
        field.label = label
    if payload.field_type is not None and not field.is_system:
        field.field_type = payload.field_type
    if payload.block_key is not None:
        if payload.block_key != field.block_key:
            _error(400, "field_block_change_not_supported", "Moving an existing field to another block is not supported in this release")
    if payload.sort_order is not None:
        field.sort_order = payload.sort_order
    if payload.is_visible is not None:
        field.is_visible = payload.is_visible
    if payload.is_visible_in_editor is not None:
        field.is_visible_in_editor = payload.is_visible_in_editor
    if payload.is_editable is not None:
        field.is_editable = payload.is_editable
    if payload.is_required is not None:
        field.is_required = payload.is_required
    if payload.options is not None:
        options = [item.strip() for item in payload.options if item and item.strip()]
        if field.field_type == "select" and not options:
            _error(422, "select_options_required", "Для поля select укажите варианты")
        field.options_json = options or None
    field.updated_at = datetime.utcnow()
    db.commit()
    db.refresh(field)
    return _field_out(field)


@router.post("/client-card/fields/{field_id}/move", response_model=ClientCardConfigResponse)
def move_client_card_field(
    field_id: str,
    payload: ClientCardMovePayload,
    db: Session = Depends(get_db),
    user: CurrentUser = Depends(get_current_user),
):
    _require_admin(user)
    field = db.get(ClientCardFieldConfig, field_id)
    if field is None:
        _error(404, "field_not_found", "Field not found")
    fields = _ordered_fields_for_block(db, field.block_key)
    index = next((i for i, item in enumerate(fields) if item.id == field_id), None)
    if index is None:
        _error(404, "field_not_found", "Field not found")
    target_index = index - 1 if payload.direction == "up" else index + 1
    if target_index < 0 or target_index >= len(fields):
        _reindex_fields(fields)
        db.commit()
        return _serialize_client_card_config(db)
    fields[index], fields[target_index] = fields[target_index], fields[index]
    _reindex_fields(fields)
    db.commit()
    return _serialize_client_card_config(db)


@router.delete("/client-card/fields/{field_id}", response_model=ClientCardConfigResponse)
def delete_client_card_field(
    field_id: str,
    db: Session = Depends(get_db),
    user: CurrentUser = Depends(get_current_user),
):
    _require_admin(user)
    field = db.get(ClientCardFieldConfig, field_id)
    if field is None:
        _error(404, "field_not_found", "Field not found")
    if field.is_system:
        _error(403, "system_field_delete_forbidden", "System field cannot be deleted")
    rules = _rules_using_field(db, field.key)
    if rules:
        code = "rule_condition_in_use" if any(rule.condition_field_key == field.key for rule in rules) else "rule_target_in_use"
        _error_details(
            409,
            code,
            "Field is used by client card rules",
            {"rules": _rule_refs(rules)},
        )
    sources = _sources_mapping_to_field(db, field.key)
    sources.extend(_sources_template_using_field(db, field.key))
    if sources:
        sources = list({source.id: source for source in sources}.values())
        _error_details(
            409,
            "source_mapping_target_in_use",
            "Field is used by client card sources",
            {"sources": _source_refs(sources)},
        )
    block_key = field.block_key
    db.delete(field)
    db.flush()
    _reindex_fields(_ordered_fields_for_block(db, block_key))
    db.commit()
    return _serialize_client_card_config(db)


@router.post("/client-card/blocks", response_model=ClientCardBlockConfigOut)
def create_client_card_block(
    payload: ClientCardBlockCreate,
    db: Session = Depends(get_db),
    user: CurrentUser = Depends(get_current_user),
):
    _require_admin(user)
    title = payload.title.strip()
    if not title:
        _error(422, "title_required", "Название блока обязательно")
    base = f"custom_block_{_slugify_title(title)}"
    key = base
    index = 2
    while db.execute(select(ClientCardBlockConfig.id).where(ClientCardBlockConfig.key == key)).first() is not None:
        key = f"{base}_{index}"
        index += 1
    now = datetime.utcnow()
    block = ClientCardBlockConfig(
        id=str(uuid.uuid4()),
        key=key,
        title=title,
        color_scheme=_normalize_color_scheme(payload.color_scheme),
        sort_order=payload.sort_order,
        is_active=payload.is_active,
        show_in_card=payload.show_in_card,
        show_in_editor=payload.show_in_editor,
        show_title=payload.show_title,
        created_at=now,
        updated_at=now,
    )
    db.add(block)
    db.commit()
    db.refresh(block)
    return _block_out(block)


@router.post("/client-card/fields", response_model=ClientCardFieldConfigOut)
def create_client_card_field(
    payload: ClientCardFieldCreate,
    db: Session = Depends(get_db),
    user: CurrentUser = Depends(get_current_user),
):
    _require_admin(user)
    block = db.execute(select(ClientCardBlockConfig).where(ClientCardBlockConfig.key == payload.block_key)).scalar_one_or_none()
    if block is None:
        _error(422, "block_key_invalid", "Указан неизвестный блок")
    label = payload.label.strip()
    if not label:
        _error(422, "label_required", "Название поля обязательно")
    options = [item.strip() for item in (payload.options or []) if item and item.strip()]
    if payload.field_type == "select" and not options:
        _error(422, "select_options_required", "Для поля select укажите варианты")

    max_sort = db.execute(
        select(ClientCardFieldConfig.sort_order)
        .where(ClientCardFieldConfig.block_key == payload.block_key)
        .order_by(ClientCardFieldConfig.sort_order.desc())
        .limit(1)
    ).scalar_one_or_none()
    sort_order = payload.sort_order if payload.sort_order is not None else (max_sort + 10 if max_sort is not None else 10)

    now = datetime.utcnow()
    field = ClientCardFieldConfig(
        id=str(uuid.uuid4()),
        key=_generate_custom_field_key(db, label),
        label=label,
        field_type=payload.field_type,
        block_key=payload.block_key,
        sort_order=sort_order,
        is_visible=payload.is_visible,
        is_visible_in_editor=payload.is_visible_in_editor,
        is_editable=payload.is_editable,
        is_required=payload.is_required,
        is_system=False,
        options_json=options or None,
        created_at=now,
        updated_at=now,
    )
    db.add(field)
    db.commit()
    db.refresh(field)
    return _field_out(field)
