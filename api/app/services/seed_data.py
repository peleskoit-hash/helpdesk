from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from ..db import SessionLocal
from ..models import Client, ClientCardBlockConfig, ClientCardFieldConfig, ClientCardRuleConfig, Company, Ticket, TicketStatus, User


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
        ("department", "Отдел", "readonly", "department", 40, True, False, True),
        ("first_department", "Департамент", "readonly", "department", 50, True, False, True),
        ("group_name", "Группа", "readonly", "department", 60, True, False, True),
        ("location_type", "Расположение", "select", "department", 70, True, True, True),
        ("anydesk", "Anydesk", "text", "department", 80, True, True, True),
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
            "actions_json": [{"target_type": "field", "target_key": "anydesk", "action": "show", "scope": "editor"}],
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
                        is_visible_in_editor=not is_system,
                        is_editable=is_editable,
                        is_system=is_system,
                        created_at=now,
                        updated_at=now,
                    )
                )
        if not has_blocks or not has_fields:
            db.commit()
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
                matched.updated_at = now
                continue
            db.add(
                ClientCardRuleConfig(
                    id=str(uuid.uuid4()),
                    technical_key=rule["technical_key"],
                    name=rule["name"],
                    is_active=True,
                    is_system=True,
                    sort_order=rule["sort_order"],
                    condition_field_key=rule["condition_field_key"],
                    condition_operator=rule["condition_operator"],
                    condition_value=rule["condition_value"],
                    actions_json=rule["actions_json"],
                    created_at=now,
                    updated_at=now,
                )
            )
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
