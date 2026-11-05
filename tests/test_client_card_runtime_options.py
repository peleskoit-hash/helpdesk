"""Configured choices on synthetic SQLite; no application startup or data migration."""
import uuid
from types import SimpleNamespace

import pytest

from api.app.models import BotButtonSetting, Client, ClientCardRuleConfig, ClientProfile, Company, Ticket, User
from fastapi import HTTPException
from api.app.routers import mass_notifications, settings_bot_button_items, settings_client_card, tickets
from api.app.schemas import ClientCardFieldUpdate, ClientProfileUpdate
from api.app.services import client_card
from test_settings_client_card import _session, _block, _field, _admin


@pytest.mark.parametrize('key', ['destination', 'sand_owner', 'sales_group', 'location_type', 'custom_team'])
def test_saved_options_are_the_only_runtime_and_consumer_choices(key):
    db = _session()
    block = _block('department', 'Department', 10)
    field = _field(key, 'Configured label', block.key, 10, is_system=not key.startswith('custom_'))
    field.field_type = 'select'
    field.options_json = ['Old', 'Admin']
    field.is_editable = True
    field.is_visible_in_editor = True
    field.admin_only_values_json = ['Admin']
    profile = ClientProfile(id=str(uuid.uuid4()), client_id=str(uuid.uuid4()), custom_fields={})
    if field.is_system:
        setattr(profile, key, 'Legacy')
    else:
        profile.custom_fields = {key: 'Legacy'}
    db.add_all([block, field, profile])
    db.commit()
    settings_client_card.update_client_card_field(field.id, ClientCardFieldUpdate(options=['Current', 'Admin']), db=db, user=_admin())
    runtime = client_card._client_card_view_config(db).blocks[0].fields[0]
    assert runtime.options == ['Current', 'Admin']
    assert runtime.label == 'Configured label' and runtime.admin_only_values == ['Admin']
    assert runtime.field_type == 'select' and runtime.is_editable and runtime.is_visible_in_editor
    assert client_card._field_options_out(field) == runtime.options
    assert (getattr(profile, key) if field.is_system else profile.custom_fields[key]) == 'Legacy'
    catalog = settings_bot_button_items._build_conditional_field_catalog(db, 'Песок')
    catalog_key = key if field.is_system else f'custom_fields.{key}'
    if catalog_key in catalog:
        assert catalog[catalog_key]['values'] == runtime.options
    if field.is_system:
        result = mass_notifications.list_filter_options(filter_field=key, destination=None, db=db, current_user=_admin())
        assert result['values'] == runtime.options
    # Legacy empty system lists use defaults; custom fields have no built-in choices.
    field.options_json = []
    db.commit()
    expected = client_card.SYSTEM_SELECT_FIELD_OPTIONS[key] if field.is_system else []
    assert client_card._client_card_view_config(db).blocks[0].fields[0].options == expected
    db.close()


@pytest.mark.parametrize('key', list(client_card.SYSTEM_SELECT_FIELD_OPTIONS))
@pytest.mark.parametrize('raw_options', [None, [], 'null', '[]', ['Individual']])
def test_effective_system_options_are_shared_without_changing_stored_config(key, raw_options):
    db = _session()
    block = _block('department', 'Department', 10)
    field = _field(key, key, block.key, 10, is_system=True)
    field.field_type = 'select'
    field.options_json = raw_options
    expected = ['Individual'] if raw_options == ['Individual'] else client_card.SYSTEM_SELECT_FIELD_OPTIONS[key]
    field.admin_only_values_json = [expected[0]]
    db.add_all([block, field])
    db.commit()
    runtime = client_card._client_card_view_config(db).blocks[0].fields[0]
    settings = settings_client_card._field_out(field)
    assert runtime.options == settings.options == expected
    assert runtime.admin_only_values == settings.admin_only_values == [expected[0]]
    catalog = settings_bot_button_items._build_conditional_field_catalog(db, 'Песок')
    if key != 'destination':
        assert catalog[key]['values'] == expected
    filters = mass_notifications.list_filter_options(filter_field=key, destination=None, db=db, current_user=_admin())
    assert filters['values'] == expected
    db.refresh(field)
    assert field.options_json == raw_options
    db.close()


@pytest.mark.parametrize('is_system,key', [(False, 'sales_group'), (True, 'employee_type')])
@pytest.mark.parametrize('raw_options', [None, []])
def test_fields_without_system_defaults_do_not_acquire_choices(is_system, key, raw_options):
    field = _field(key, key, 'department', 10, is_system=is_system)
    field.field_type = 'select'
    field.options_json = raw_options
    assert not client_card._field_options_out(field)


@pytest.mark.parametrize('key', ['destination', 'sand_owner', 'sales_group', 'location_type', 'custom_team'])
def test_saving_another_field_keeps_unlisted_select_value(key):
    db = _session()
    block = _block('department', 'Department', 10)
    field = _field(key, key, block.key, 10, is_system=not key.startswith('custom_'))
    field.field_type = 'select'
    field.options_json = ['Current']
    field.is_editable = True
    company = Company(id=str(uuid.uuid4()), name='Synthetic')
    agent = User(id=str(uuid.uuid4()), company_id=company.id, role='agent', full_name='Agent')
    client = Client(id=str(uuid.uuid4()), company_id=company.id, display_name='Client')
    profile = ClientProfile(id=str(uuid.uuid4()), client_id=client.id, contact_name='Before', custom_fields={})
    if field.is_system:
        setattr(profile, key, 'Legacy')
    else:
        profile.custom_fields = {key: 'Legacy'}
    ticket = Ticket(id=str(uuid.uuid4()), company_id=company.id, client_id=client.id,
                    assignee_id=agent.id, subject='Synthetic', ticket_number=1, intake_state='ready', status='new')
    db.add_all([block, field, company, agent, client, profile, ticket])
    db.commit()
    tickets.update_client_profile(ticket.id, ClientProfileUpdate(contact_name='After'), db=db,
                                   user=SimpleNamespace(id=agent.id, role='agent'))
    db.refresh(profile)
    assert profile.contact_name == 'After'
    assert (getattr(profile, key) if field.is_system else profile.custom_fields[key]) == 'Legacy'
    db.close()


@pytest.mark.parametrize('key', ['sales_group', 'custom_team'])
def test_explicit_rename_updates_bot_conditions_and_rule_map_outputs(key):
    db = _session()
    field = _field(key, key, 'department', 10, is_system=not key.startswith('custom_'))
    field.field_type = 'select'
    field.options_json = ['Old', 'Keep']
    button = BotButtonSetting(id=str(uuid.uuid4()), button_key='button', menu_type='Песок',
                              workflow_type='ticket', button_path='Button', button_label='Button',
                              conditional_files_json=[{'conditions': [{'field': key if field.is_system else f'custom_fields.{key}',
                                                                       'operator': 'eq', 'value': 'Old'}], 'files': ['keep.txt']}])
    rule = ClientCardRuleConfig(id=str(uuid.uuid4()), name='Mapping', condition_operator='always', condition_field_key='',
                               actions_json=[{'target_key': key, 'value_type': 'map', 'source_key': 'contact_name',
                                              'map': {'Someone': 'Old', 'Other': 'Keep'}}])
    db.add_all([_block('department', 'Department', 10), field, button, rule])
    db.commit()
    with pytest.raises(HTTPException) as exc:
        settings_client_card.update_client_card_field(field.id, ClientCardFieldUpdate(options=['New', 'Keep']), db=db, user=_admin())
    assert exc.value.status_code == 409
    db.rollback()
    settings_client_card.update_client_card_field(field.id, ClientCardFieldUpdate(options=['New', 'Keep'],
        option_renames=[{'old_value': 'Old', 'new_value': 'New'}]), db=db, user=_admin())
    assert button.conditional_files_json[0]['conditions'][0]['value'] == 'New'
    assert button.conditional_files_json[0]['files'] == ['keep.txt']
    assert rule.actions_json[0]['map'] == {'Someone': 'New', 'Other': 'Keep'}
    db.close()
