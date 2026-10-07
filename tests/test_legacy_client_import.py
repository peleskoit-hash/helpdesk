from __future__ import annotations

from datetime import datetime, timedelta
import uuid

import pytest
from cryptography.fernet import Fernet
from openpyxl import Workbook
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker

from api.app.db import Base
from api.app.models import (
    Client,
    ClientCardFieldConfig,
    ClientCardRuleConfig,
    ClientProfile,
    Company,
    IntegrationLog,
    PendingTaskIntent,
    TelegramContact,
    Ticket,
    TicketFeedback,
    TicketFeedbackInput,
    TicketMessage,
    TicketReview,
    TicketReviewCycle,
    User,
)
from api.app.services import legacy_client_import as importer
from api.app.services.encryption import decrypt_secret, encrypt_secret
from api.app.services.internal_tasks import INTERNAL_TASK_CLIENT_UUID_NAMESPACE, INTERNAL_TASK_SYSTEM_CLIENT_NAME


HEADERS = [
    "helpdeskeddy_id",
    "matched_bitrix_id",
    "matched_bitrix_login",
    "match_type",
    "alias",
    "date_created",
    "date_updated",
    "department",
    "email",
    "group.disable",
    "group.id",
    "group.name",
    "group.type",
    "id",
    "language",
    "lastname",
    "ldap_username",
    "name",
    "notifications",
    "organization",
    "organization.address",
    "organization.domains",
    "organization.email",
    "organization.id",
    "organization.name",
    "organization.phone",
    "organization.web",
    "phone",
    "skype",
    "status",
    "user_status",
    "website",
    "Верификация пройдена",
    "Anydesk",
    "Руководитель",
    "Chat_id сотрудника",
    "Last Message_id",
    "Имя сотрудника",
    "{'ru': 'Отдел'}",
    "Тип сотрудника",
    "Внутренний номер",
    "ID пользователя",
    "Логин Bitrix",
    "Пароль Bitrix",
]

OLD_HEADERS = [
    "Chat_id сотрудника",
    "Имя",
    "Фамилия",
    "Имя сущности в HelpDeskEddy",
    "Доп. поле: ID Telegram",
    "Доп. поле: Статус верификации",
    "Доп. поле: Отдел",
    "Доп. поле: Руководитель",
    "Доп. поле: Тип сотрудника",
    "Доп. поле: AnyDesk",
    "Доп. поле: Номер для сайта",
    "matched_bitrix_id",
    "matched_bitrix_login",
    "matched_bitrix_password",
]


@pytest.fixture()
def session(monkeypatch):
    engine = create_engine("sqlite:///:memory:", future=True)
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine, autoflush=False, autocommit=False)
    db = Session()
    monkeypatch.setattr(importer.settings, "bitrix_password_encryption_key", Fernet.generate_key().decode("utf-8"))
    monkeypatch.setattr(importer.settings, "app_env", "dev")
    db.add(Company(id="company-1", name="ES", is_active=True, created_at=datetime.utcnow()))
    _seed_derivation_config(db)
    db.commit()
    try:
        yield db
    finally:
        db.close()


def _seed_derivation_config(db):
    now = datetime.utcnow()
    for key in ("destination", "sales_group", "sand_owner", "department", "first_department", "group_name"):
        db.add(
            ClientCardFieldConfig(
                id=str(uuid.uuid4()),
                key=key,
                label=key,
                field_type="text",
                block_key="main",
                sort_order=10,
                is_visible=True,
                is_visible_in_editor=True,
                is_editable=True,
                is_required=False,
                is_system=True,
                created_at=now,
                updated_at=now,
            )
        )
    db.add_all(
        [
            ClientCardRuleConfig(
                id=str(uuid.uuid4()),
                technical_key="sales",
                name="sales",
                is_active=True,
                is_system=True,
                sort_order=1,
                condition_field_key="destination",
                condition_operator="equals",
                condition_value="Отдел Продаж",
                actions_json=[
                    {"action": "set_value", "scope": "save", "target_type": "field", "target_key": "department", "value_type": "literal", "value": "Отдел продаж"},
                    {
                        "action": "set_value",
                        "scope": "save",
                        "target_type": "field",
                        "target_key": "first_department",
                        "value_type": "map",
                        "source_key": "sales_group",
                        "map": {
                            "группа Власова": "Первый департамент",
                            "группа Скрылёвой": "Первый департамент",
                            "группа Макаренко": "Первый департамент",
                            "группа Калинина": "Третий отдел",
                        },
                    },
                    {"action": "set_value", "scope": "save", "target_type": "field", "target_key": "group_name", "value_type": "field", "source_key": "sales_group"},
                ],
                created_at=now,
                updated_at=now,
            ),
            ClientCardRuleConfig(
                id=str(uuid.uuid4()),
                technical_key="sand",
                name="sand",
                is_active=True,
                is_system=True,
                sort_order=2,
                condition_field_key="destination",
                condition_operator="equals",
                condition_value="Песок",
                actions_json=[
                    {"action": "set_value", "scope": "save", "target_type": "field", "target_key": "department", "value_type": "literal", "value": "Стажировка"},
                    {"action": "set_value", "scope": "save", "target_type": "field", "target_key": "group_name", "value_type": "field", "source_key": "sand_owner"},
                ],
                created_at=now,
                updated_at=now,
            ),
        ]
    )


def _xlsx(tmp_path, rows, *, headers=HEADERS):
    path = tmp_path / "legacy.xlsx"
    workbook = Workbook()
    sheet = workbook.active
    sheet.title = "matched"
    sheet.append(headers)
    for row in rows:
        sheet.append([row.get(header) for header in headers])
    other = workbook.create_sheet("ignored")
    other.append(OLD_HEADERS)
    other.append(["this sheet must not be read"])
    workbook.save(path)
    return path


def _base_row(**overrides):
    row = {
        "helpdeskeddy_id": "hde-1",
        "matched_bitrix_id": "11702",
        "matched_bitrix_login": "anna@example.test",
        "lastname": "Иванова",
        "name": "ignored source name",
        "Верификация пройдена": "1",
        "Anydesk": "ad-1",
        "Руководитель": "0",
        "Chat_id сотрудника": "1001",
        "Имя сотрудника": "Анна",
        "{'ru': 'Отдел'}": "Отдел продаж; Первый департамент; Группа Скрылевой",
        "Тип сотрудника": "Штатный сотрудник",
        "Внутренний номер": "700",
        "ID пользователя": "ignored-11702",
        "Логин Bitrix": "ignored-login@example.test",
        "Пароль Bitrix": "secret",
    }
    row.update(overrides)
    return row


def test_real_matched_headers_are_accepted_and_first_sheet_only(tmp_path):
    rows = importer.read_legacy_client_rows(_xlsx(tmp_path, [_base_row()]))

    assert len(rows) == 1
    assert rows[0].chat_id == "1001"


def test_old_extra_field_headers_are_not_the_contract(tmp_path):
    path = _xlsx(tmp_path, [_base_row()], headers=OLD_HEADERS)

    with pytest.raises(importer.LegacyClientImportError, match="Missing required columns"):
        importer.read_legacy_client_rows(path)


def test_fields_are_read_from_actual_columns(tmp_path):
    row = importer.read_legacy_client_rows(_xlsx(tmp_path, [_base_row()]))[0]

    assert row.first_name == "Анна"
    assert row.last_name == "Иванова"
    assert row.is_verified is True
    assert row.anydesk == "ad-1"
    assert row.department_raw == "Отдел продаж; Первый департамент; Группа Скрылевой"
    assert row.internal_number == "700"
    assert row.bitrix_id == "11702"
    assert row.bitrix_login == "anna@example.test"
    assert row.bitrix_password == "secret"


def test_dry_run_summary_and_password_from_bitrix_password_column(session, tmp_path):
    rows = importer.read_legacy_client_rows(_xlsx(tmp_path, [_base_row()]))

    result = importer.import_legacy_clients(session, rows, apply=False)

    assert result["mode"] == "dry-run"
    assert result["source_rows"] == 1
    assert result["valid_rows"] == 1
    assert result["create"] == 1
    assert result["update"] == 0
    assert result["noop"] == 0
    assert result["errors"] == []
    assert result["distribution"]["destination"] == {"Отдел Продаж": 1}
    assert result["distribution"]["sales_group"] == {"группа Скрылёвой": 1}
    assert "bitrix_password" in result["items"][0]["changed_fields"]
    assert "secret" not in str(result)
    assert session.execute(select(Client)).scalars().all() == []


def test_apply_creates_client_contact_profile_and_derived_fields(session, tmp_path):
    rows = importer.read_legacy_client_rows(_xlsx(tmp_path, [_base_row()]))

    result = importer.import_legacy_clients(session, rows, apply=True)

    assert result["create"] == 1
    contact = session.execute(select(TelegramContact).where(TelegramContact.external_chat_id == "1001")).scalar_one()
    profile = session.execute(select(ClientProfile).where(ClientProfile.client_id == contact.client_id)).scalar_one()
    assert contact.external_user_id == "1001"
    assert profile.contact_name == "Анна"
    assert profile.last_name == "Иванова"
    assert profile.is_verified is True
    assert profile.destination == "Отдел Продаж"
    assert profile.sales_group == "группа Скрылёвой"
    assert profile.department == "Отдел продаж"
    assert profile.first_department == "Первый департамент"
    assert profile.group_name == "группа Скрылёвой"
    assert profile.location_type == "Офисный"
    assert profile.employee_type == "Сотрудник"
    assert profile.external_user_id == "11702"
    assert profile.bitrix_login == "anna@example.test"
    assert profile.bitrix_id == "11702"
    assert decrypt_secret(profile.bitrix_password) == "secret"


def test_fresh_blank_employee_type_does_not_guess_office(session, tmp_path):
    rows = importer.read_legacy_client_rows(_xlsx(tmp_path, [_base_row(**{"Тип сотрудника": None})]))

    importer.import_legacy_clients(session, rows, apply=True)

    profile = session.execute(select(ClientProfile)).scalar_one()
    assert profile.employee_type is None
    assert profile.location_type is None


def test_existing_remote_employee_type_survives_blank_source(session, tmp_path):
    importer.import_legacy_clients(session, importer.read_legacy_client_rows(_xlsx(tmp_path, [_base_row(**{"Тип сотрудника": "Удаленный сотрудник"})])), apply=True)
    rows = importer.read_legacy_client_rows(_xlsx(tmp_path, [_base_row(**{"Тип сотрудника": None})]))

    importer.import_legacy_clients(session, rows, apply=True)

    profile = session.execute(select(ClientProfile)).scalar_one()
    assert profile.employee_type == "Сотрудник"
    assert profile.location_type == "Удалённый"


def test_employee_type_mappings_for_office_and_remote(session, tmp_path):
    importer.import_legacy_clients(session, importer.read_legacy_client_rows(_xlsx(tmp_path, [_base_row(**{"Тип сотрудника": "Штатный сотрудник"})])), apply=True)
    office_profile = session.execute(select(ClientProfile)).scalar_one()
    assert office_profile.employee_type == "Сотрудник"
    assert office_profile.location_type == "Офисный"

    second = importer.read_legacy_client_rows(
        _xlsx(
            tmp_path,
            [
                _base_row(
                    **{
                        "Chat_id сотрудника": "2002",
                        "matched_bitrix_id": "2002",
                        "matched_bitrix_login": "remote@example.test",
                        "Тип сотрудника": "Удаленный сотрудник",
                    }
                )
            ],
        )
    )
    importer.import_legacy_clients(session, second, apply=True)
    remote_profile = session.execute(select(ClientProfile).where(ClientProfile.external_user_id == "2002")).scalar_one()
    assert remote_profile.employee_type == "Сотрудник"
    assert remote_profile.location_type == "Удалённый"


def test_lunev_leader_with_blank_employee_type_stays_leadership_without_guessing_office(session, tmp_path):
    rows = importer.read_legacy_client_rows(
        _xlsx(
            tmp_path,
            [
                _base_row(
                    **{
                        "Chat_id сотрудника": "6075253581",
                        "Имя сотрудника": "Станислав",
                        "lastname": "Лунев",
                        "matched_bitrix_id": "6075253581",
                        "matched_bitrix_login": "lunev@example.test",
                        "Руководитель": "1",
                        "Тип сотрудника": None,
                    }
                )
            ],
        )
    )

    importer.import_legacy_clients(session, rows, apply=True)

    profile = session.execute(select(ClientProfile)).scalar_one()
    assert profile.destination == "Руководство"
    assert profile.employee_type is None
    assert profile.location_type is None


def test_second_apply_is_idempotent_and_does_not_reencrypt_password(session, tmp_path):
    rows = importer.read_legacy_client_rows(_xlsx(tmp_path, [_base_row()]))
    importer.import_legacy_clients(session, rows, apply=True)
    profile = session.execute(select(ClientProfile)).scalar_one()
    encrypted = profile.bitrix_password

    result = importer.import_legacy_clients(session, rows, apply=True)

    session.refresh(profile)
    assert result["noop"] == 1
    assert profile.bitrix_password == encrypted


def test_blank_ordinary_fields_do_not_erase_existing_values(session, tmp_path):
    importer.import_legacy_clients(session, importer.read_legacy_client_rows(_xlsx(tmp_path, [_base_row()])), apply=True)
    rows = importer.read_legacy_client_rows(
        _xlsx(
            tmp_path,
            [
                _base_row(
                    **{
                        "Имя сотрудника": None,
                        "lastname": None,
                        "Anydesk": None,
                        "Внутренний номер": None,
                        "matched_bitrix_id": None,
                        "matched_bitrix_login": None,
                        "Пароль Bitrix": None,
                    }
                )
            ],
        )
    )

    importer.import_legacy_clients(session, rows, apply=True)

    profile = session.execute(select(ClientProfile)).scalar_one()
    assert profile.contact_name == "Анна"
    assert profile.last_name == "Иванова"
    assert profile.anydesk == "ad-1"
    assert profile.internal_number == "700"
    assert profile.external_user_id == "11702"
    assert profile.bitrix_login == "anna@example.test"
    assert profile.bitrix_id == "11702"
    assert decrypt_secret(profile.bitrix_password) == "secret"


def test_existing_display_name_uses_effective_profile_name_when_last_name_source_blank(session, tmp_path):
    importer.import_legacy_clients(session, importer.read_legacy_client_rows(_xlsx(tmp_path, [_base_row(**{"Имя сотрудника": "Иван", "lastname": "Иванов"})])), apply=True)
    rows = importer.read_legacy_client_rows(_xlsx(tmp_path, [_base_row(**{"Имя сотрудника": "Иван", "lastname": None})]))

    importer.import_legacy_clients(session, rows, apply=True)

    contact = session.execute(select(TelegramContact).where(TelegramContact.external_chat_id == "1001")).scalar_one()
    client = session.get(Client, contact.client_id)
    profile = session.execute(select(ClientProfile).where(ClientProfile.client_id == client.id)).scalar_one()
    assert profile.contact_name == "Иван"
    assert profile.last_name == "Иванов"
    assert client.display_name == "Иван Иванов"
    assert contact.external_display_name == "Иван Иванов"


def test_existing_display_name_uses_effective_profile_name_when_first_name_source_blank(session, tmp_path):
    importer.import_legacy_clients(session, importer.read_legacy_client_rows(_xlsx(tmp_path, [_base_row(**{"Имя сотрудника": "Иван", "lastname": "Иванов"})])), apply=True)
    rows = importer.read_legacy_client_rows(_xlsx(tmp_path, [_base_row(**{"Имя сотрудника": None, "lastname": "Иванов"})]))

    importer.import_legacy_clients(session, rows, apply=True)

    contact = session.execute(select(TelegramContact).where(TelegramContact.external_chat_id == "1001")).scalar_one()
    client = session.get(Client, contact.client_id)
    assert client.display_name == "Иван Иванов"
    assert contact.external_display_name == "Иван Иванов"


def test_existing_display_name_updates_when_new_last_name_is_nonblank(session, tmp_path):
    importer.import_legacy_clients(session, importer.read_legacy_client_rows(_xlsx(tmp_path, [_base_row(**{"Имя сотрудника": "Иван", "lastname": "Иванов"})])), apply=True)
    rows = importer.read_legacy_client_rows(_xlsx(tmp_path, [_base_row(**{"Имя сотрудника": "Иван", "lastname": "Петров"})]))

    importer.import_legacy_clients(session, rows, apply=True)

    contact = session.execute(select(TelegramContact).where(TelegramContact.external_chat_id == "1001")).scalar_one()
    client = session.get(Client, contact.client_id)
    profile = session.execute(select(ClientProfile).where(ClientProfile.client_id == client.id)).scalar_one()
    assert profile.last_name == "Петров"
    assert client.display_name == "Иван Петров"
    assert contact.external_display_name == "Иван Петров"


def test_existing_display_name_does_not_become_telegram_fallback_when_both_source_names_blank(session, tmp_path):
    importer.import_legacy_clients(session, importer.read_legacy_client_rows(_xlsx(tmp_path, [_base_row(**{"Имя сотрудника": "Иван", "lastname": "Иванов"})])), apply=True)
    rows = importer.read_legacy_client_rows(_xlsx(tmp_path, [_base_row(**{"Имя сотрудника": None, "lastname": None})]))

    importer.import_legacy_clients(session, rows, apply=True)

    contact = session.execute(select(TelegramContact).where(TelegramContact.external_chat_id == "1001")).scalar_one()
    client = session.get(Client, contact.client_id)
    assert client.display_name == "Иван Иванов"
    assert contact.external_display_name == "Иван Иванов"


def test_fresh_client_without_names_gets_telegram_fallback_display_name(session, tmp_path):
    rows = importer.read_legacy_client_rows(_xlsx(tmp_path, [_base_row(**{"Имя сотрудника": None, "lastname": None})]))

    importer.import_legacy_clients(session, rows, apply=True)

    contact = session.execute(select(TelegramContact).where(TelegramContact.external_chat_id == "1001")).scalar_one()
    client = session.get(Client, contact.client_id)
    assert client.display_name == "Telegram 1001"
    assert contact.external_display_name == "Telegram 1001"


def test_anydesk_over_100_is_omitted_with_warning_and_dry_run_summary(session, tmp_path):
    dirty_note = "x" * 118
    rows = importer.read_legacy_client_rows(_xlsx(tmp_path, [_base_row(**{"Anydesk": dirty_note})]))

    result = importer.import_legacy_clients(session, rows, apply=False)

    assert result["create"] == 1
    assert result["errors"] == []
    assert {
        "type": "field_omitted",
        "row": 2,
        "chat_id": "1001",
        "field": "anydesk",
        "reason": "value_length_exceeds_100",
        "length": 118,
        "max_length": 100,
    } in result["warnings"]
    assert dirty_note not in str(result)
    assert session.execute(select(Client)).scalars().all() == []


def test_anydesk_over_100_does_not_erase_existing_anydesk(session, tmp_path):
    importer.import_legacy_clients(session, importer.read_legacy_client_rows(_xlsx(tmp_path, [_base_row(**{"Anydesk": "keep-42"})])), apply=True)
    rows = importer.read_legacy_client_rows(_xlsx(tmp_path, [_base_row(**{"Anydesk": "x" * 118})]))

    result = importer.import_legacy_clients(session, rows, apply=True)

    profile = session.execute(select(ClientProfile)).scalar_one()
    assert result["update"] == 0
    assert result["noop"] == 1
    assert profile.anydesk == "keep-42"


def test_anydesk_exactly_100_is_imported(session, tmp_path):
    value = "7" * 100
    rows = importer.read_legacy_client_rows(_xlsx(tmp_path, [_base_row(**{"Anydesk": value})]))

    importer.import_legacy_clients(session, rows, apply=True)

    profile = session.execute(select(ClientProfile)).scalar_one()
    assert profile.anydesk == value


def test_changed_password_is_reencrypted(session, tmp_path):
    importer.import_legacy_clients(session, importer.read_legacy_client_rows(_xlsx(tmp_path, [_base_row()])), apply=True)

    changed = importer.read_legacy_client_rows(_xlsx(tmp_path, [_base_row(**{"Пароль Bitrix": "new-secret"})]))
    result = importer.import_legacy_clients(session, changed, apply=True)

    profile = session.execute(select(ClientProfile)).scalar_one()
    assert result["update"] == 1
    assert decrypt_secret(profile.bitrix_password) == "new-secret"


@pytest.mark.parametrize(
    ("department", "destination", "sales_group", "sand_owner"),
    [
        ("Отдел продаж; Первый департамент; Группа Власова", "Отдел Продаж", "группа Власова", None),
        ("Отдел продаж; Третий отдел", "Отдел Продаж", "группа Калинина", None),
        ("Колл центр", "Колл Центр", None, None),
        ("С" + "тажировка ОП; " + "С" + "тажировка " + "С" + "крылевой", "Песок", None, "песок " + "С" + "крылёвой"),
        ("Стажировка ОП; Начало Стажировки", "Песок", None, None),
    ],
)
def test_department_mapping(department, destination, sales_group, sand_owner):
    row = importer.LegacyClientRow(2, "1", None, None, False, department, False, "Офисный", "Сотрудник", None, None, None, None, None)

    payload = importer._row_profile_payload(row)

    assert payload["destination"] == destination
    assert payload["sales_group"] == sales_group
    assert payload["sand_owner"] == sand_owner


def test_vlasov_and_skryleva_leader_mapping_stays_leadership(session, tmp_path):
    rows = importer.read_legacy_client_rows(
        _xlsx(
            tmp_path,
            [
                _base_row(
                    **{
                        "Chat_id сотрудника": "2001",
                        "Имя сотрудника": "Власов",
                        "lastname": "",
                        "matched_bitrix_id": "20001",
                        "matched_bitrix_login": "vlasov@example.test",
                        "{'ru': 'Отдел'}": "Отдел продаж; Первый департамент; Группа Власова",
                        "Руководитель": "1",
                    }
                ),
                _base_row(
                    **{
                        "Chat_id сотрудника": "2002",
                        "Имя сотрудника": "Скрылёва",
                        "lastname": "",
                        "matched_bitrix_id": "20002",
                        "matched_bitrix_login": "skryleva@example.test",
                        "{'ru': 'Отдел'}": "Отдел продаж; Первый департамент; Группа Скрылевой",
                        "Руководитель": "1",
                    }
                ),
            ],
        )
    )

    result = importer.import_legacy_clients(session, rows, apply=True)

    assert result["leadership_count"] == 2
    profiles = session.execute(select(ClientProfile).order_by(ClientProfile.contact_name)).scalars().all()
    assert [profile.destination for profile in profiles] == ["Руководство", "Руководство"]
    assert [profile.sales_group for profile in profiles] == [None, None]


def test_explicit_leader_chat_id_wins_even_when_legacy_flag_is_zero(session, tmp_path):
    rows = importer.read_legacy_client_rows(
        _xlsx(
            tmp_path,
            [
                _base_row(
                    **{
                        "Chat_id сотрудника": "969283110",
                        "Имя сотрудника": "Субхиддин",
                        "lastname": "Руководитель",
                        "matched_bitrix_id": None,
                        "matched_bitrix_login": None,
                        "Руководитель": "0",
                        "{'ru': 'Отдел'}": "Unknown legacy value",
                    }
                )
            ],
        )
    )

    result = importer.import_legacy_clients(session, rows, apply=True)

    profile = session.execute(select(ClientProfile)).scalar_one()
    assert result["create"] == 1
    assert result["leadership_count"] == 1
    assert profile.destination == "Руководство"
    assert profile.sales_group is None
    assert profile.sand_owner is None


def test_saydaraliev_confirmed_leader_chat_id_maps_to_leadership(session, tmp_path):
    rows = importer.read_legacy_client_rows(
        _xlsx(
            tmp_path,
            [
                _base_row(
                    **{
                        "Chat_id сотрудника": "969283110",
                        "helpdeskeddy_id": "1121",
                        "matched_bitrix_id": None,
                        "matched_bitrix_login": None,
                        "Имя сотрудника": "Субхиддин",
                        "lastname": "Сайдаралиев",
                        "Руководитель": "1",
                        "{'ru': 'Отдел'}": "Отдел продаж; Первый департамент; Группа Сайдаралиева",
                        "Тип сотрудника": "Штатный сотрудник",
                    }
                )
            ],
        )
    )

    result = importer.import_legacy_clients(session, rows, apply=True)

    profile = session.execute(select(ClientProfile)).scalar_one()
    assert result["create"] == 1
    assert result["leadership_count"] == 1
    assert profile.destination == "Руководство"
    assert profile.sales_group is None
    assert profile.sand_owner is None
    assert profile.location_type == "Офисный"
    assert profile.employee_type == "Сотрудник"


def test_mohamed_saydaraliev_is_not_current_leader_override(session, tmp_path):
    rows = importer.read_legacy_client_rows(
        _xlsx(
            tmp_path,
            [
                _base_row(
                    **{
                        "Chat_id сотрудника": "517013086",
                        "matched_bitrix_id": "517013086",
                        "matched_bitrix_login": "mohamed@example.test",
                        "Руководитель": "0",
                        "{'ru': 'Отдел'}": "Отдел продаж; Первый департамент; Группа Сайдаралиева",
                    }
                )
            ],
        )
    )

    result = importer.import_legacy_clients(session, rows, apply=True)

    profile = session.execute(select(ClientProfile)).scalar_one()
    assert result["leadership_count"] == 0
    assert profile.destination == "Отдел Продаж"
    assert profile.sales_group == "группа Сайдаралиева"
    assert profile.sand_owner is None


def test_makar_leader_chat_id_uses_source_name_without_hardcoded_override(session, tmp_path):
    rows = importer.read_legacy_client_rows(
        _xlsx(
            tmp_path,
            [
                _base_row(
                    **{
                        "Chat_id сотрудника": "1476152160",
                        "Имя сотрудника": "Макар",
                        "lastname": "Надеждин",
                        "matched_bitrix_id": "1476152160",
                        "matched_bitrix_login": "makar@example.test",
                        "Руководитель": "0",
                    }
                )
            ],
        )
    )

    importer.import_legacy_clients(session, rows, apply=True)

    profile = session.execute(select(ClientProfile)).scalar_one()
    assert profile.destination == "Руководство"
    assert profile.contact_name == "Макар"
    assert profile.last_name == "Надеждин"


@pytest.mark.parametrize("chat_id", ["1197577312", "7545713567", "922502736", "1085928274", "799332654"])
def test_each_explicit_hr_chat_id_sets_hr_regardless_of_legacy_department(session, tmp_path, chat_id):
    rows = importer.read_legacy_client_rows(
        _xlsx(
            tmp_path,
            [
                _base_row(
                    **{
                        "Chat_id сотрудника": chat_id,
                        "matched_bitrix_id": chat_id,
                        "matched_bitrix_login": f"hr-{chat_id}@example.test",
                        "{'ru': 'Отдел'}": "Old unrelated value",
                    }
                )
            ],
        )
    )

    result = importer.import_legacy_clients(session, rows, apply=True)

    profile = session.execute(select(ClientProfile)).scalar_one()
    assert result["distribution"]["destination"] == {"HR": 1}
    assert profile.destination == "HR"
    assert profile.sales_group is None
    assert profile.sand_owner is None


def test_hr_mikaberidze_remote_employee_type_stays_source_driven(session, tmp_path):
    rows = importer.read_legacy_client_rows(
        _xlsx(
            tmp_path,
            [
                _base_row(
                    **{
                        "Chat_id сотрудника": "1197577312",
                        "matched_bitrix_id": "1197577312",
                        "matched_bitrix_login": "mikaberidze@example.test",
                        "Тип сотрудника": "Удаленный сотрудник",
                        "{'ru': 'Отдел'}": "Old unrelated value",
                    }
                )
            ],
        )
    )

    importer.import_legacy_clients(session, rows, apply=True)

    profile = session.execute(select(ClientProfile)).scalar_one()
    assert profile.destination == "HR"
    assert profile.location_type == "Удалённый"
    assert profile.employee_type == "Сотрудник"


@pytest.mark.parametrize("chat_id", ["7545713567", "922502736", "1085928274", "799332654"])
def test_other_hr_chat_ids_office_employee_type_stays_source_driven(session, tmp_path, chat_id):
    rows = importer.read_legacy_client_rows(
        _xlsx(
            tmp_path,
            [
                _base_row(
                    **{
                        "Chat_id сотрудника": chat_id,
                        "matched_bitrix_id": chat_id,
                        "matched_bitrix_login": f"hr-office-{chat_id}@example.test",
                        "Тип сотрудника": "Штатный сотрудник",
                        "{'ru': 'Отдел'}": "Old unrelated value",
                    }
                )
            ],
        )
    )

    importer.import_legacy_clients(session, rows, apply=True)

    profile = session.execute(select(ClientProfile)).scalar_one()
    assert profile.destination == "HR"
    assert profile.location_type == "Офисный"
    assert profile.employee_type == "Сотрудник"


def test_second_import_can_move_existing_client_from_sales_to_leadership_without_duplicate(session, tmp_path, monkeypatch):
    monkeypatch.setattr(importer, "CURRENT_LEADER_CHAT_IDS", importer.CURRENT_LEADER_CHAT_IDS - {"969283110"})
    first_rows = importer.read_legacy_client_rows(
        _xlsx(
            tmp_path,
            [
                _base_row(
                    **{
                        "Chat_id сотрудника": "969283110",
                        "matched_bitrix_id": "old-id",
                        "matched_bitrix_login": "old@example.test",
                        "{'ru': 'Отдел'}": "Отдел продаж; Первый департамент; Группа Сайдаралиева",
                    }
                )
            ],
        )
    )
    importer.import_legacy_clients(session, first_rows, apply=True)
    profile = session.execute(select(ClientProfile)).scalar_one()
    assert profile.destination == "Отдел Продаж"
    assert profile.sales_group == "группа Сайдаралиева"
    first_client_ids = [client.id for client in session.execute(select(Client)).scalars().all()]
    first_contact_ids = [contact.id for contact in session.execute(select(TelegramContact)).scalars().all()]
    monkeypatch.setattr(importer, "CURRENT_LEADER_CHAT_IDS", importer.CURRENT_LEADER_CHAT_IDS | {"969283110"})
    second_rows = importer.read_legacy_client_rows(
        _xlsx(
            tmp_path,
            [
                _base_row(
                    **{
                        "Chat_id сотрудника": "969283110",
                        "matched_bitrix_id": "new-id",
                        "matched_bitrix_login": "new@example.test",
                        "Имя сотрудника": "Новый",
                        "lastname": "Лидер",
                    }
                )
            ],
        )
    )

    result = importer.import_legacy_clients(session, second_rows, apply=True)

    session.refresh(profile)
    assert result["update"] == 1
    assert [client.id for client in session.execute(select(Client)).scalars().all()] == first_client_ids
    assert [contact.id for contact in session.execute(select(TelegramContact)).scalars().all()] == first_contact_ids
    assert profile.destination == "Руководство"
    assert profile.sales_group is None
    assert profile.sand_owner is None


def test_second_import_can_move_existing_client_to_hr_without_duplicate(session, tmp_path, monkeypatch):
    monkeypatch.setitem(importer.CURRENT_HR_CHAT_OVERRIDES, "3003", {})
    monkeypatch.delitem(importer.CURRENT_HR_CHAT_OVERRIDES, "3003")
    importer.import_legacy_clients(session, importer.read_legacy_client_rows(_xlsx(tmp_path, [_base_row(**{"Chat_id сотрудника": "3003", "matched_bitrix_id": "3003", "matched_bitrix_login": "old-hr@example.test"})])), apply=True)
    monkeypatch.setitem(importer.CURRENT_HR_CHAT_OVERRIDES, "3003", {})
    rows = importer.read_legacy_client_rows(
        _xlsx(
            tmp_path,
            [
                _base_row(
                    **{
                        "Chat_id сотрудника": "3003",
                        "matched_bitrix_id": "3003-new",
                        "matched_bitrix_login": "new-hr@example.test",
                        "{'ru': 'Отдел'}": "Old unrelated value",
                    }
                )
            ],
        )
    )

    result = importer.import_legacy_clients(session, rows, apply=True)

    profile = session.execute(select(ClientProfile)).scalar_one()
    assert result["update"] == 1
    assert len(session.execute(select(Client)).scalars().all()) == 1
    assert len(session.execute(select(TelegramContact)).scalars().all()) == 1
    assert profile.destination == "HR"
    assert profile.sales_group is None
    assert profile.sand_owner is None
    assert profile.external_user_id == "3003-new"
    assert profile.bitrix_login == "new-hr@example.test"


def test_excluded_makhatadze_and_shaburov_are_skipped(session, tmp_path):
    rows = importer.read_legacy_client_rows(
        _xlsx(
            tmp_path,
            [
                _base_row(**{"Chat_id сотрудника": "814689011", "matched_bitrix_id": "287", "Имя сотрудника": "Давид", "lastname": "Махатадзе"}),
                _base_row(**{"Chat_id сотрудника": "606742675", "matched_bitrix_id": "11468", "Имя сотрудника": "Максим", "lastname": "Шабуров"}),
            ],
        )
    )

    result = importer.import_legacy_clients(session, rows, apply=False)

    assert result["source_rows"] == 2
    assert result["valid_rows"] == 0
    assert result["skipped_excluded"] == 2
    assert [item["status"] for item in result["items"]] == ["skipped_excluded", "skipped_excluded"]


def test_exclusion_requires_bitrix_id_and_chat_id_pair(session, tmp_path):
    rows = importer.read_legacy_client_rows(_xlsx(tmp_path, [_base_row(**{"Chat_id сотрудника": "814689011", "matched_bitrix_id": "999"})]))

    result = importer.import_legacy_clients(session, rows, apply=False)

    assert result["skipped_excluded"] == 0
    assert result["create"] == 1


def test_duplicate_bitrix_values_are_warnings_only(session, tmp_path):
    rows = importer.read_legacy_client_rows(
        _xlsx(
            tmp_path,
            [
                _base_row(**{"Chat_id сотрудника": "1001"}),
                _base_row(**{"Chat_id сотрудника": "1002", "Имя сотрудника": "Борис"}),
            ],
        )
    )

    result = importer.import_legacy_clients(session, rows, apply=True)

    assert result["create"] == 2
    assert {warning["type"] for warning in result["warnings"]} == {"duplicate_matched_bitrix_id", "duplicate_matched_bitrix_login"}


def test_all_validation_errors_are_collected_before_dry_run(session, tmp_path):
    rows = importer.read_legacy_client_rows(
        _xlsx(
            tmp_path,
            [
                _base_row(**{"Chat_id сотрудника": "1001", "{'ru': 'Отдел'}": "Unknown A"}),
                _base_row(**{"Chat_id сотрудника": "1002", "{'ru': 'Отдел'}": "Unknown B"}),
                _base_row(**{"Chat_id сотрудника": "1002", "{'ru': 'Отдел'}": "Колл центр"}),
                _base_row(**{"Chat_id сотрудника": "1003", "matched_bitrix_id": "1003", "matched_bitrix_login": "valid@example.test", "{'ru': 'Отдел'}": "Колл центр"}),
            ],
        )
    )

    result = importer.import_legacy_clients(session, rows, apply=False)

    assert [error["code"] for error in result["errors"]] == ["unknown_department", "unknown_department", "duplicate_chat_id"]
    assert result["create"] == 1
    assert session.execute(select(Client)).scalars().all() == []


def test_apply_refuses_before_mutation_if_validation_errors_exist(session, tmp_path):
    rows = importer.read_legacy_client_rows(_xlsx(tmp_path, [_base_row(**{"{'ru': 'Отдел'}": "Unknown"})]))

    with pytest.raises(importer.LegacyClientImportError, match="Cannot apply"):
        importer.import_legacy_clients(session, rows, apply=True)

    session.rollback()
    assert session.execute(select(Client)).scalars().all() == []


def _client_with_contact(db, chat_id: str) -> Client:
    client = Client(id=str(uuid.uuid4()), company_id="company-1", display_name=f"client {chat_id}")
    db.add(client)
    db.flush()
    db.add(TelegramContact(id=str(uuid.uuid4()), company_id="company-1", client_id=client.id, external_user_id=chat_id, external_chat_id=chat_id))
    db.add(ClientProfile(id=str(uuid.uuid4()), client_id=client.id, custom_fields={}, bitrix_password=encrypt_secret("keep-secret")))
    return client


def test_cleanup_dev_dry_run_and_apply_preserves_allowed_chats(session):
    preserved = _client_with_contact(session, "453920478")
    doomed = _client_with_contact(session, "999")
    user = User(id="agent-1", company_id="company-1", full_name="Agent", role="agent")
    session.add(user)
    session.flush()
    ticket = Ticket(id=str(uuid.uuid4()), company_id="company-1", client_id=doomed.id, subject="old")
    session.add(ticket)
    session.flush()
    session.add(TicketMessage(id=str(uuid.uuid4()), ticket_id=ticket.id, message_type="client", body="hello"))
    session.add(IntegrationLog(id=str(uuid.uuid4()), source="bitrix", event_type="task", result="success", code="ok", description="deleted", task_id=ticket.id))
    session.add(PendingTaskIntent(id=str(uuid.uuid4()), company_id="company-1", client_id=doomed.id, external_chat_id="999", subject="draft", expires_at=datetime.utcnow() + timedelta(days=1)))
    session.commit()

    dry_run = importer.cleanup_dev_clients(session, apply=False)
    assert dry_run["deleted"]["clients"] == 1
    assert dry_run["deleted"]["integration_logs"] == 1
    assert "ticket_attachments" in dry_run["deleted"]
    assert "stats_work_intervals" in dry_run["deleted"]
    assert session.get(Client, doomed.id) is not None

    applied = importer.cleanup_dev_clients(session, apply=True)

    assert applied["deleted"]["clients"] == 1
    assert session.get(Client, preserved.id) is not None
    assert session.get(Client, doomed.id) is None
    assert session.get(User, "agent-1") is not None
    assert session.execute(select(Ticket)).scalars().all() == []
    assert session.execute(select(IntegrationLog)).scalars().all() == []


def test_cleanup_preserves_preserved_client_history_and_integration_logs(session):
    preserved = _client_with_contact(session, "453920478")
    doomed = _client_with_contact(session, "999")
    preserved_ticket = Ticket(id=str(uuid.uuid4()), company_id="company-1", client_id=preserved.id, subject="preserved")
    doomed_ticket = Ticket(id=str(uuid.uuid4()), company_id="company-1", client_id=doomed.id, subject="doomed")
    session.add_all([preserved_ticket, doomed_ticket])
    session.flush()
    preserved_message = TicketMessage(id=str(uuid.uuid4()), ticket_id=preserved_ticket.id, message_type="client", body="keep")
    doomed_message = TicketMessage(id=str(uuid.uuid4()), ticket_id=doomed_ticket.id, message_type="client", body="drop")
    preserved_log = IntegrationLog(id=str(uuid.uuid4()), source="bitrix", event_type="task", result="success", code="ok", description="keep", task_id=preserved_ticket.id)
    doomed_log = IntegrationLog(id=str(uuid.uuid4()), source="bitrix", event_type="task", result="success", code="ok", description="drop", task_id=doomed_ticket.id)
    session.add_all([preserved_message, doomed_message, preserved_log, doomed_log])
    session.commit()
    preserved_id = preserved.id
    doomed_id = doomed.id
    doomed_ticket_id = doomed_ticket.id
    doomed_message_id = doomed_message.id
    doomed_log_id = doomed_log.id

    importer.cleanup_dev_clients(session, apply=True)

    assert session.get(Client, preserved_id) is not None
    assert session.get(TelegramContact, session.execute(select(TelegramContact.id).where(TelegramContact.client_id == preserved_id)).scalar_one()) is not None
    assert session.get(ClientProfile, session.execute(select(ClientProfile.id).where(ClientProfile.client_id == preserved_id)).scalar_one()) is not None
    assert session.get(Ticket, preserved_ticket.id) is not None
    assert session.get(TicketMessage, preserved_message.id) is not None
    assert session.get(IntegrationLog, preserved_log.id) is not None
    assert session.get(Client, doomed_id) is None
    assert session.get(Ticket, doomed_ticket_id) is None
    assert session.get(TicketMessage, doomed_message_id) is None
    assert session.get(IntegrationLog, doomed_log_id) is None


def test_cleanup_deletes_rows_that_reference_doomed_client_even_with_preserved_ticket(session):
    preserved = _client_with_contact(session, "453920478")
    doomed = _client_with_contact(session, "999")
    agent = User(id="agent-cleanup", company_id="company-1", full_name="Agent", role="agent")
    session.add(agent)
    preserved_ticket = Ticket(id=str(uuid.uuid4()), company_id="company-1", client_id=preserved.id, subject="preserved")
    session.add(preserved_ticket)
    session.flush()
    doomed_feedback = TicketFeedback(
        id=str(uuid.uuid4()),
        ticket_id=preserved_ticket.id,
        client_id=doomed.id,
        client_name_snapshot="doomed",
        ticket_title_snapshot="preserved",
        text="drop",
        submitted_at=datetime.utcnow(),
    )
    doomed_feedback_input = TicketFeedbackInput(
        id=str(uuid.uuid4()),
        ticket_id=preserved_ticket.id,
        client_id=doomed.id,
        state="awaiting_text",
    )
    doomed_cycle = TicketReviewCycle(
        id=str(uuid.uuid4()),
        ticket_id=preserved_ticket.id,
        agent_id=agent.id,
        client_id=doomed.id,
        state="completed",
    )
    session.add_all([doomed_feedback, doomed_feedback_input, doomed_cycle])
    session.flush()
    doomed_review = TicketReview(
        id=str(uuid.uuid4()),
        ticket_id=preserved_ticket.id,
        agent_id=agent.id,
        client_id=doomed.id,
        cycle_id=doomed_cycle.id,
        previous_version_id=None,
        version_number=1,
        agent_name_snapshot="Agent",
        client_name_snapshot="doomed",
        ticket_subject_snapshot="preserved",
        rating=5,
        review_text=None,
        rated_at=datetime.utcnow(),
        is_current=True,
    )
    session.add(doomed_review)
    session.commit()
    doomed_id = doomed.id
    preserved_ticket_id = preserved_ticket.id
    doomed_feedback_id = doomed_feedback.id
    doomed_feedback_input_id = doomed_feedback_input.id
    doomed_review_id = doomed_review.id
    doomed_cycle_id = doomed_cycle.id

    dry_run = importer.cleanup_dev_clients(session, apply=False)

    assert dry_run["deleted"]["ticket_feedback"] == 1
    assert dry_run["deleted"]["ticket_feedback_inputs"] == 1
    assert dry_run["deleted"]["ticket_reviews"] == 1
    assert dry_run["deleted"]["ticket_review_cycles"] == 1

    applied = importer.cleanup_dev_clients(session, apply=True)

    assert applied["deleted"]["ticket_feedback"] == dry_run["deleted"]["ticket_feedback"]
    assert applied["deleted"]["ticket_feedback_inputs"] == dry_run["deleted"]["ticket_feedback_inputs"]
    assert applied["deleted"]["ticket_reviews"] == dry_run["deleted"]["ticket_reviews"]
    assert applied["deleted"]["ticket_review_cycles"] == dry_run["deleted"]["ticket_review_cycles"]
    session.expire_all()
    assert session.get(Client, doomed_id) is None
    assert session.get(Ticket, preserved_ticket_id) is not None
    assert session.get(TicketFeedback, doomed_feedback_id) is None
    assert session.get(TicketFeedbackInput, doomed_feedback_input_id) is None
    assert session.get(TicketReview, doomed_review_id) is None
    assert session.get(TicketReviewCycle, doomed_cycle_id) is None
    assert session.get(User, "agent-cleanup") is not None


def test_cleanup_preserves_canonical_system_client_and_deletes_ordinary_orphan_client(session):
    company_id = "company-1"
    system_client_id = str(uuid.uuid5(INTERNAL_TASK_CLIENT_UUID_NAMESPACE, company_id))
    system_client = Client(id=system_client_id, company_id=company_id, display_name=INTERNAL_TASK_SYSTEM_CLIENT_NAME)
    orphan_client = Client(id=str(uuid.uuid4()), company_id=company_id, display_name="orphan test")
    session.add_all([system_client, orphan_client])
    session.flush()
    system_ticket = Ticket(id=str(uuid.uuid4()), company_id=company_id, client_id=system_client.id, subject="system")
    orphan_ticket = Ticket(id=str(uuid.uuid4()), company_id=company_id, client_id=orphan_client.id, subject="orphan")
    session.add_all([system_ticket, orphan_ticket])
    session.flush()
    system_message = TicketMessage(id=str(uuid.uuid4()), ticket_id=system_ticket.id, message_type="system", body="keep")
    orphan_message = TicketMessage(id=str(uuid.uuid4()), ticket_id=orphan_ticket.id, message_type="client", body="drop")
    session.add_all([system_message, orphan_message])
    session.commit()
    orphan_client_id = orphan_client.id
    orphan_ticket_id = orphan_ticket.id
    orphan_message_id = orphan_message.id

    dry_run = importer.cleanup_dev_clients(session, apply=False)
    assert dry_run["deleted"]["preserved_system_clients"] == 1
    assert dry_run["deleted"]["clients"] == 1

    importer.cleanup_dev_clients(session, apply=True)

    assert session.get(Client, system_client_id) is not None
    assert session.get(Ticket, system_ticket.id) is not None
    assert session.get(TicketMessage, system_message.id) is not None
    assert session.get(Client, orphan_client_id) is None
    assert session.get(Ticket, orphan_ticket_id) is None
    assert session.get(TicketMessage, orphan_message_id) is None


def test_cleanup_rolls_back_on_mid_cleanup_failure(session, monkeypatch):
    doomed = _client_with_contact(session, "999")
    ticket = Ticket(id=str(uuid.uuid4()), company_id="company-1", client_id=doomed.id, subject="doomed")
    session.add(ticket)
    session.flush()
    log = IntegrationLog(id=str(uuid.uuid4()), source="bitrix", event_type="task", result="success", code="ok", description="drop", task_id=ticket.id)
    session.add(log)
    session.commit()
    original = importer._execute_cleanup_deletes

    def failing_cleanup(db, scope):
        db.execute(delete(IntegrationLog).where(IntegrationLog.task_id.in_(scope["ticket_ids"])))
        raise RuntimeError("boom")

    from sqlalchemy import delete

    monkeypatch.setattr(importer, "_execute_cleanup_deletes", failing_cleanup)

    with pytest.raises(RuntimeError, match="boom"):
        importer.cleanup_dev_clients(session, apply=True)

    assert session.get(Client, doomed.id) is not None
    assert session.get(Ticket, ticket.id) is not None
    assert session.get(IntegrationLog, log.id) is not None
    monkeypatch.setattr(importer, "_execute_cleanup_deletes", original)


def test_cleanup_refuses_non_dev(session, monkeypatch):
    monkeypatch.setattr(importer.settings, "app_env", "prod")

    with pytest.raises(importer.LegacyClientImportError, match="APP_ENV=dev"):
        importer.cleanup_dev_clients(session, apply=True)
