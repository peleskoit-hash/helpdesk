from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from ..db import SessionLocal
from ..models import Client, ClientCardBlockConfig, ClientCardFieldConfig, ClientCardRuleConfig, ClientCardSourceConfig, Company, Ticket, TicketStatus, User
from .call_center_employee_resolver import SYSTEM_SOURCE_KEY as CALL_CENTER_EMPLOYEE_SOURCE_KEY
from .client_card_sources import ensure_system_source_mapping_json

SORT_STEP = 10

def ensure_test_agents() -> None:
    agent_specs = [
        ("00000000-0000-0000-0000-000000000001", "agent1@test.local", "Агент 1"),
        ("00000000-0000-0000-0000-000000000002", "agent2@test.local", "Агент 2"),
        ("00000000-0000-0000-0000-000000000003", "agent3@test.local", "Агент 3"),
    ]

    with SessionLocal() as db:
        company_id = db.execute(select(Company.id).order_by(Company.created_at.asc()).limit(1)).scalar_one_or_none()
        if company_id is None:
            return

        for agent_id, email, full_name in agent_specs:
            user = db.execute(select(User).where(User.id == agent_id)).scalar_one_or_none()
            if user is None:
                db.add(
                    User(
                        id=agent_id,
                        company_id=company_id,
                        email=email,
                        login=email,
                        full_name=full_name,
                        role="agent",
                        is_active=True,
                        availability_status="offline",
                    )
                )
                continue

            # Keep user-customized profile data. Only backfill missing technical fields.
            user.email = user.email or email
            user.login = user.login or email
            user.full_name = user.full_name or full_name
            user.role = user.role or "agent"
            if user.is_active is None:
                user.is_active = True
            if not user.availability_status:
                user.availability_status = "offline"
        db.commit()


def ensure_client_card_defaults() -> None:
    system_field_editor_visibility_defaults = {
        "telegram_username": True,
        "telegram_display_name": True,
        "last_name": True,
        "contact_name": True,
        "destination": True,
        "sand_owner": False,
        "sales_group": False,
        "department": False,
        "first_department": False,
        "group_name": False,
        "location_type": True,
        "anydesk": False,
        "internal_number": True,
        "bitrix_login": True,
        "bitrix_password": True,
        "bitrix_id": True,
        "is_verified": True,
    }
    system_field_visibility_upgrades = {
        "anydesk": {"is_visible": False, "is_visible_in_editor": False},
        "department": {"is_visible": False},
        "first_department": {"is_visible": False},
        "group_name": {"is_visible": False},
    }
    legacy_anydesk_rule_actions = [{"target_type": "field", "target_key": "anydesk", "action": "show", "scope": "editor"}]
    effective_anydesk_rule_actions = [{"target_type": "field", "target_key": "anydesk", "action": "show", "scope": "both"}]
    default_blocks = [
        {"key": "tg", "title": "Telegram", "color_scheme": "blue", "sort_order": 10, "is_active": True},
        {"key": "profile", "title": "Профиль", "color_scheme": "green", "sort_order": 20, "is_active": True},
        {"key": "department", "title": "Отдел", "color_scheme": "blue", "sort_order": 30, "is_active": True},
        {"key": "work", "title": "Рабочие данные", "color_scheme": "yellow", "sort_order": 40, "is_active": True},
        {"key": "verification", "title": "Верификация", "color_scheme": "red", "sort_order": 50, "is_active": True},
    ]
    default_fields = [
        ("telegram_username", "@username", "readonly", "tg", 10, True, False, True),
        ("telegram_display_name", "Имя в TG", "readonly", "tg", 20, True, False, True),
        ("last_name", "Фамилия", "text", "profile", 10, True, True, True),
        ("contact_name", "Имя", "text", "profile", 20, True, True, True),
        ("destination", "Куда..?", "select", "department", 10, True, True, True),
        ("sand_owner", "Чей песок", "select", "department", 20, True, True, True),
        ("sales_group", "Группа отдела продаж", "select", "department", 30, True, True, True),
        ("department", "Отдел", "readonly", "department", 40, False, False, True),
        ("first_department", "Департамент", "readonly", "department", 50, False, False, True),
        ("group_name", "Группа", "readonly", "department", 60, False, False, True),
        ("location_type", "Расположение", "select", "department", 70, True, True, True),
        ("anydesk", "Anydesk", "text", "department", 80, False, True, True),
        ("internal_number", "Внутренний номер", "text", "work", 10, True, True, True),
        ("bitrix_login", "Логин Bitrix", "text", "work", 20, True, True, True),
        ("bitrix_password", "Пароль Bitrix", "password", "work", 30, True, True, True),
        ("bitrix_id", "ID в Bitrix", "text", "work", 40, True, True, True),
        ("is_verified", "Верификация", "checkbox", "verification", 10, True, True, True),
    ]

    default_rules = [
        {
            "technical_key": "show_sand_owner_for_sand",
            "name": "\u041f\u043e\u043a\u0430\u0437\u044b\u0432\u0430\u0442\u044c \u0432\u043b\u0430\u0434\u0435\u043b\u044c\u0446\u0430 \u043f\u0435\u0441\u043a\u0430",
            "sort_order": 10,
            "condition_field_key": "destination",
            "condition_operator": "equals",
            "condition_value": "\u041f\u0435\u0441\u043e\u043a",
            "actions_json": [{"target_type": "field", "target_key": "sand_owner", "action": "show", "scope": "editor"}],
        },
        {
            "technical_key": "show_sales_group_for_sales",
            "name": "\u041f\u043e\u043a\u0430\u0437\u044b\u0432\u0430\u0442\u044c \u0433\u0440\u0443\u043f\u043f\u0443 \u043f\u0440\u043e\u0434\u0430\u0436",
            "sort_order": 20,
            "condition_field_key": "destination",
            "condition_operator": "equals",
            "condition_value": "\u041e\u0442\u0434\u0435\u043b \u041f\u0440\u043e\u0434\u0430\u0436",
            "actions_json": [{"target_type": "field", "target_key": "sales_group", "action": "show", "scope": "editor"}],
        },
        {
            "technical_key": "show_anydesk_for_remote",
            "name": "\u041f\u043e\u043a\u0430\u0437\u044b\u0432\u0430\u0442\u044c AnyDesk \u0434\u043b\u044f \u0443\u0434\u0430\u043b\u0451\u043d\u043d\u044b\u0445",
            "sort_order": 30,
            "condition_field_key": "location_type",
            "condition_operator": "equals",
            "condition_value": "\u0423\u0434\u0430\u043b\u0451\u043d\u043d\u044b\u0439",
            "actions_json": effective_anydesk_rule_actions,
        },
        {
            "technical_key": "derive_department_meta_fallback",
            "name": "\u0420\u0430\u0441\u0447\u0451\u0442 department/first_department/group_name: fallback",
            "sort_order": 130,
            "condition_field_key": "",
            "condition_operator": "always",
            "condition_value": None,
            "actions_json": [
                {"target_type": "field", "target_key": "department", "action": "set_value", "scope": "save", "value_type": "field", "source_key": "destination"},
                {"target_type": "field", "target_key": "first_department", "action": "set_value", "scope": "save", "value_type": "null"},
                {"target_type": "field", "target_key": "group_name", "action": "set_value", "scope": "save", "value_type": "null"},
            ],
        },
        {
            "technical_key": "derive_department_meta_sand",
            "name": "\u0420\u0430\u0441\u0447\u0451\u0442 department/first_department/group_name: \u041f\u0435\u0441\u043e\u043a",
            "sort_order": 100,
            "condition_field_key": "destination",
            "condition_operator": "equals",
            "condition_value": "\u041f\u0435\u0441\u043e\u043a",
            "actions_json": [
                {"target_type": "field", "target_key": "department", "action": "set_value", "scope": "save", "value_type": "literal", "value": "\u0421\u0442\u0430\u0436\u0438\u0440\u043e\u0432\u043a\u0430"},
                {"target_type": "field", "target_key": "first_department", "action": "set_value", "scope": "save", "value_type": "null"},
                {"target_type": "field", "target_key": "group_name", "action": "set_value", "scope": "save", "value_type": "field", "source_key": "sand_owner"},
            ],
        },
        {
            "technical_key": "derive_department_meta_sales",
            "name": "\u0420\u0430\u0441\u0447\u0451\u0442 department/first_department/group_name: \u041e\u0442\u0434\u0435\u043b \u041f\u0440\u043e\u0434\u0430\u0436",
            "sort_order": 110,
            "condition_field_key": "destination",
            "condition_operator": "equals",
            "condition_value": "\u041e\u0442\u0434\u0435\u043b \u041f\u0440\u043e\u0434\u0430\u0436",
            "actions_json": [
                {"target_type": "field", "target_key": "department", "action": "set_value", "scope": "save", "value_type": "literal", "value": "\u041e\u0442\u0434\u0435\u043b \u043f\u0440\u043e\u0434\u0430\u0436"},
                {
                    "target_type": "field",
                    "target_key": "first_department",
                    "action": "set_value",
                    "scope": "save",
                    "value_type": "map",
                    "source_key": "sales_group",
                    "map": {
                        "\u0433\u0440\u0443\u043f\u043f\u0430 \u0412\u043b\u0430\u0441\u043e\u0432\u0430": "\u041f\u0435\u0440\u0432\u044b\u0439 \u0434\u0435\u043f\u0430\u0440\u0442\u0430\u043c\u0435\u043d\u0442",
                        "\u0433\u0440\u0443\u043f\u043f\u0430 \u0428\u0430\u0431\u0443\u0440\u043e\u0432\u0430": "\u041f\u0435\u0440\u0432\u044b\u0439 \u0434\u0435\u043f\u0430\u0440\u0442\u0430\u043c\u0435\u043d\u0442",
                        "\u0433\u0440\u0443\u043f\u043f\u0430 \u0421\u0430\u0439\u0434\u0430\u0440\u0430\u043b\u0438\u0435\u0432\u0430": "\u041f\u0435\u0440\u0432\u044b\u0439 \u0434\u0435\u043f\u0430\u0440\u0442\u0430\u043c\u0435\u043d\u0442",
                        "\u0433\u0440\u0443\u043f\u043f\u0430 \u041c\u0430\u043a\u0430\u0440\u0435\u043d\u043a\u043e": "\u041f\u0435\u0440\u0432\u044b\u0439 \u0434\u0435\u043f\u0430\u0440\u0442\u0430\u043c\u0435\u043d\u0442",
                        "\u0433\u0440\u0443\u043f\u043f\u0430 \u0428\u0430\u0445\u0430\u043b\u0438\u0435\u0432\u043e\u0439": "\u0412\u0442\u043e\u0440\u043e\u0439 \u043e\u0442\u0434\u0435\u043b",
                        "\u0433\u0440\u0443\u043f\u043f\u0430 \u041a\u043e\u0447\u0443\u0440\u044b": "\u0412\u0442\u043e\u0440\u043e\u0439 \u043e\u0442\u0434\u0435\u043b",
                        "\u0433\u0440\u0443\u043f\u043f\u0430 \u041c\u043e\u0441\u043a\u0432\u0438\u0447\u0435\u043d\u043a\u043e": "\u0422\u0440\u0435\u0442\u0438\u0439 \u043e\u0442\u0434\u0435\u043b",
                    },
                },
                {"target_type": "field", "target_key": "group_name", "action": "set_value", "scope": "save", "value_type": "field", "source_key": "sales_group"},
            ],
        },
    ]

    with SessionLocal() as db:
        has_blocks = db.execute(select(ClientCardBlockConfig.id).limit(1)).first() is not None
        has_fields = db.execute(select(ClientCardFieldConfig.id).limit(1)).first() is not None

        now = datetime.utcnow()
        if not has_blocks:
            for block in default_blocks:
                db.add(
                    ClientCardBlockConfig(
                        id=str(uuid.uuid4()),
                        key=block["key"],
                        title=block["title"],
                        color_scheme=block["color_scheme"],
                        sort_order=block["sort_order"],
                        is_active=block["is_active"],
                        show_in_card=True,
                        show_in_editor=True,
                        show_title=True,
                        created_at=now,
                        updated_at=now,
                    )
                )
        if not has_fields:
            for key, label, field_type, block_key, sort_order, is_visible, is_editable, is_system in default_fields:
                db.add(
                    ClientCardFieldConfig(
                        id=str(uuid.uuid4()),
                        key=key,
                        label=label,
                        field_type=field_type,
                        block_key=block_key,
                        sort_order=sort_order,
                        is_visible=is_visible,
                        is_visible_in_editor=system_field_editor_visibility_defaults.get(key, not is_system),
                        is_editable=is_editable,
                        is_system=is_system,
                        created_at=now,
                        updated_at=now,
                    )
                )
        if not has_blocks or not has_fields:
            db.commit()
        has_rules = db.execute(select(ClientCardRuleConfig.id).limit(1)).first() is not None
        next_system_rule_sort_order = (
            db.execute(select(ClientCardRuleConfig.sort_order).order_by(ClientCardRuleConfig.sort_order.desc()).limit(1)).scalar_one_or_none()
            or 0
        )
        for rule in default_rules:
            existing = db.execute(
                select(ClientCardRuleConfig).where(ClientCardRuleConfig.technical_key == rule["technical_key"])
            ).scalar_one_or_none()
            if existing is not None:
                continue
            existing_without_key = db.execute(
                select(ClientCardRuleConfig).where(
                    ClientCardRuleConfig.is_system.is_(True),
                    ClientCardRuleConfig.technical_key.is_(None),
                    ClientCardRuleConfig.condition_field_key == rule["condition_field_key"],
                    ClientCardRuleConfig.condition_operator == rule["condition_operator"],
                    ClientCardRuleConfig.condition_value == rule["condition_value"],
                )
            ).scalars().all()
            matched = next(
                (
                    item
                    for item in existing_without_key
                    if item.actions_json == rule["actions_json"]
                ),
                None,
            )
            if matched is not None:
                matched.technical_key = rule["technical_key"]
                if (
                    matched.technical_key == "show_anydesk_for_remote"
                    and matched.actions_json == legacy_anydesk_rule_actions
                ):
                    matched.actions_json = effective_anydesk_rule_actions
                matched.updated_at = now
                continue
            if has_rules:
                next_system_rule_sort_order += SORT_STEP
                sort_order = next_system_rule_sort_order
            else:
                sort_order = rule["sort_order"]
            db.add(
                ClientCardRuleConfig(
                    id=str(uuid.uuid4()),
                    technical_key=rule["technical_key"],
                    name=rule["name"],
                    is_active=True,
                    is_system=True,
                    sort_order=sort_order,
                    condition_field_key=rule["condition_field_key"],
                    condition_operator=rule["condition_operator"],
                    condition_value=rule["condition_value"],
                    actions_json=rule["actions_json"],
                    created_at=now,
                    updated_at=now,
                )
            )
        for field in db.execute(
            select(ClientCardFieldConfig).where(
                ClientCardFieldConfig.is_system.is_(True),
                ClientCardFieldConfig.key.in_(tuple(system_field_editor_visibility_defaults)),
            )
        ).scalars():
            target = dict(system_field_visibility_upgrades.get(field.key, {}))
            target.setdefault("is_visible_in_editor", system_field_editor_visibility_defaults[field.key])
            changed = False
            if "is_visible" in target and field.is_visible != target["is_visible"]:
                field.is_visible = target["is_visible"]
                changed = True
            if "is_visible_in_editor" in target and field.is_visible_in_editor != target["is_visible_in_editor"]:
                field.is_visible_in_editor = target["is_visible_in_editor"]
                changed = True
            if changed:
                field.updated_at = now
        for rule in db.execute(
            select(ClientCardRuleConfig).where(ClientCardRuleConfig.technical_key == "show_anydesk_for_remote")
        ).scalars():
            if rule.is_system and rule.actions_json == legacy_anydesk_rule_actions:
                rule.actions_json = effective_anydesk_rule_actions
                rule.updated_at = now
        source = db.execute(
            select(ClientCardSourceConfig).where(ClientCardSourceConfig.technical_key == CALL_CENTER_EMPLOYEE_SOURCE_KEY)
        ).scalar_one_or_none()
        if source is None:
            source = ClientCardSourceConfig(
                id=str(uuid.uuid4()),
                technical_key=CALL_CENTER_EMPLOYEE_SOURCE_KEY,
                name="Сотрудник КЦ по внутреннему номеру",
                kind="system",
                is_active=True,
                method="POST",
                url_template="system://call-center-employee-resolver",
                query_template_json=None,
                headers_template_json=None,
                body_template_json=None,
                timeout_ms=5000,
                mapping_json=ensure_system_source_mapping_json(None),
                on_error="ignore",
                created_at=now,
                updated_at=now,
            )
            db.add(source)
        if source is not None:
            source.name = source.name or "Сотрудник КЦ по внутреннему номеру"
            source.kind = "system"
            source.is_active = True
            source.method = "POST"
            source.url_template = "system://call-center-employee-resolver"
            source.query_template_json = None
            source.headers_template_json = None
            source.body_template_json = None
            source.timeout_ms = 5000
            source.mapping_json = ensure_system_source_mapping_json(source.mapping_json)
            source.on_error = source.on_error or "ignore"
            source.updated_at = now
        db.commit()


def ensure_initial_seed_data() -> None:
    with SessionLocal() as db:
        exists = db.execute(select(Company.id).limit(1)).first()
        if exists:
            return

        company_id = str(uuid.uuid4())
        admin_id = str(uuid.uuid4())
        agent_id = "00000000-0000-0000-0000-000000000001"
        client_id = str(uuid.uuid4())
        ticket_id = str(uuid.uuid4())

        try:
            db.add(Company(id=company_id, name="Test Company"))
            db.commit()

            db.add(User(id=admin_id, company_id=company_id, email="admin@test.local", login="admin", full_name="Admin", role="admin", availability_status="online"))
            db.add(User(id=agent_id, company_id=company_id, email="agent1@test.local", login="agent1", full_name="Агент 1", role="agent", availability_status="online"))
            db.add(Client(id=client_id, company_id=company_id, display_name="Тест Клиент"))
            db.commit()

            db.add(
                Ticket(
                    id=ticket_id,
                    company_id=company_id,
                    client_id=client_id,
                    assignee_id=agent_id,
                    subject="Тестовая заявка",
                    status=TicketStatus.NEW.value,
                    is_frozen=False,
                    created_at=datetime.utcnow(),
                    updated_at=datetime.utcnow(),
                )
            )
            db.commit()
        except IntegrityError:
            db.rollback()
