"""Persistent ticket numbering on isolated SQLite and PostgreSQL schemas."""
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from pathlib import Path
from threading import Barrier
from types import SimpleNamespace
from uuid import uuid4

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import event, inspect, select
from sqlalchemy.orm import Session

from api.app.auth import get_current_user
from api.app.db import get_db
from api.app.models import BotButtonSetting, Client, Company, InternalTaskDefinition, Ticket, TicketNumberCounter, User
from api.app.routers import telegram, tickets
from api.app.services import integration_tasks, internal_tasks, schema_evolution
from api.app.services.automatic_messages import NOTIFICATION_BOT_RECIPIENT_TG_USERNAME_TAG
from api.app.services.button_config import ButtonSpec
from api.app.services.ticket_numbering import ensure_ticket_number
from test_web_auth import engine


@pytest.fixture
def case(engine):
    with Session(engine, autoflush=False) as db:
        db.add(Company(id='company', name='Synthetic'))
        db.flush()
        db.add(Client(id='client', company_id='company', display_name='Synthetic'))
        db.commit()
        yield SimpleNamespace(db=db, engine=engine)


def ticket(db, **values):
    fields = dict(id=str(uuid4()), company_id='company', client_id='client', subject='Synthetic',
                  intake_state='ready', status='unassigned')
    fields.update(values)
    row = Ticket(**fields)
    db.add(row)
    return row


def last_number(db):
    return db.scalar(select(TicketNumberCounter.last_number).where(TicketNumberCounter.id == 1))


def test_migration_preserves_numbers_and_does_not_reset_after_deletion(case, monkeypatch):
    db = case.db
    rows = [ticket(db, ticket_number=n) for n in (4, 41)]
    draft = ticket(db, intake_state='collecting')
    db.commit()
    TicketNumberCounter.__table__.drop(case.engine)
    monkeypatch.setattr(schema_evolution, 'engine', case.engine)
    schema_evolution.ensure_ticket_number_counter()
    assert 'ticket_number_counter' in inspect(case.engine).get_table_names()
    assert last_number(db) == 41
    assert [row.ticket_number for row in rows] == [4, 41]
    assert draft.ticket_number is None
    next_row = ticket(db)
    assert ensure_ticket_number(db, next_row) == 42
    db.commit()
    db.delete(next_row)
    db.commit()
    schema_evolution.ensure_ticket_number_counter()
    assert last_number(db) == 42
    assert [row.ticket_number for row in rows] == [4, 41]
    assert ensure_ticket_number(db, ticket(db)) == 43


def test_empty_migration_is_idempotent_and_starts_at_one(case, monkeypatch):
    monkeypatch.setattr(schema_evolution, 'engine', case.engine)
    schema_evolution.ensure_ticket_number_counter()
    schema_evolution.ensure_ticket_number_counter()
    assert last_number(case.db) == 0
    assert ensure_ticket_number(case.db, ticket(case.db)) == 1


def test_metadata_created_database_bootstraps_from_max_only_once(case):
    db = case.db
    legacy = ticket(db, ticket_number=100)
    db.commit()
    first = ticket(db)
    assert ensure_ticket_number(db, first) == 101
    db.commit()
    db.delete(first)
    db.delete(legacy)
    db.commit()
    assert not db.scalars(select(Ticket.id)).all()
    assert ensure_ticket_number(db, ticket(db)) == 102


@pytest.mark.parametrize('initialized', [False, True])
def test_allocation_and_ticket_rollback_together(case, initialized):
    db = case.db
    if initialized:
        db.add(TicketNumberCounter(id=1, last_number=0))
        db.commit()
    row = ticket(db)
    row_id = row.id
    assert ensure_ticket_number(db, row) == 1
    db.rollback()
    assert db.get(Ticket, row_id) is None
    assert last_number(db) == (0 if initialized else None)
    assert ensure_ticket_number(db, ticket(db)) == 1
    db.commit()
    assert last_number(db) == 1


def test_existing_number_is_unchanged_without_allocating(case):
    db = case.db
    row = ticket(db, ticket_number=123)
    db.commit()
    assert ensure_ticket_number(db, row) == 123
    assert last_number(db) is None


def test_one_counter_for_all_channels_types_and_companies(case):
    db = case.db
    db.add(Company(id='other', name='Other'))
    db.flush()
    db.add(Client(id='peer', company_id='other', display_name='Peer'))
    db.commit()
    numbers = []
    for channel in ('telegram', 'web'):
        for workflow in ('ticket', 'task', 'auto'):
            row = ticket(db, channel=channel, workflow_type=workflow,
                         company_id='company' if channel == 'telegram' else 'other',
                         client_id='client' if channel == 'telegram' else 'peer')
            numbers.append(ensure_ticket_number(db, row))
    db.commit()
    assert numbers == list(range(1, 7))
    assert last_number(db) == 6


def test_concurrent_allocations_are_serialized_in_callers_transactions(case):
    gate = Barrier(4)
    def allocate(_):
        with Session(case.engine, autoflush=False) as db:
            row = ticket(db)
            gate.wait(timeout=15)
            number = ensure_ticket_number(db, row)
            db.commit()
            return number
    with ThreadPoolExecutor(max_workers=4) as pool:
        numbers = list(pool.map(allocate, range(4)))
    assert sorted(numbers) == [1, 2, 3, 4]
    assert last_number(case.db) == 4


def test_allocator_locks_before_atomic_update(case):
    statements = []
    def executed(_conn, _cursor, statement, _parameters, _context, _many):
        statements.append(statement.lower())
    event.listen(case.engine, 'before_cursor_execute', executed)
    try:
        ensure_ticket_number(case.db, ticket(case.db))
    finally:
        event.remove(case.engine, 'before_cursor_execute', executed)
    writes = [sql for sql in statements if sql.startswith('update ticket_number_counter')]
    assert len(writes) == 1 and 'returning' in writes[0]
    if case.engine.dialect.name == 'postgresql':
        assert 'pg_advisory_xact_lock' in statements[0]
    else:
        assert not any('pg_advisory' in sql for sql in statements)


def test_group_parent_and_each_child_have_separate_numbers_and_rollback(case, monkeypatch):
    db = case.db
    parent = ticket(db, workflow_type='task', task_kind='group_parent', status='pending')
    db.commit()
    monkeypatch.setattr(telegram, '_menu_name_for_ticket_client', lambda *_: 'Synthetic')
    group = ButtonSpec(key='parent', label='Parent', kind='group_task', subtasks=(
        ButtonSpec(key='child-a', label='Child A', kind='task'),
        ButtonSpec(key='child-b', label='Child B', kind='task'),
    ))
    def create():
        telegram._create_group_children_from_collecting(db, parent, group, 'Description', None)
        return db.scalars(select(Ticket).where(Ticket.parent_ticket_id == parent.id).order_by(Ticket.subject)).all()
    assert [row.ticket_number for row in create()] == [2, 3]
    assert parent.ticket_number == 1
    db.rollback()
    assert parent.ticket_number is None
    assert last_number(db) is None
    children = create()
    db.commit()
    assert [parent.ticket_number, *[row.ticket_number for row in children]] == [1, 2, 3]
    assert last_number(db) == 3
    assert [row.id for row in create()] == [row.id for row in children]
    assert last_number(db) == 3


@pytest.fixture(params=[False, True], ids=['without_agent', 'with_agent'])
def incoming(case, request, monkeypatch):
    case.agent_id = 'incoming-agent' if request.param else None
    if case.agent_id:
        case.db.add(User(id=case.agent_id, company_id='company', full_name='Incoming Agent', role='agent',
                         availability_status='online', is_active=True))
        case.db.commit()
    case.observed_numbers = []
    def observe(_db, row, *args, **kwargs):
        case.observed_numbers.append(row.ticket_number)
    for name in ['apply_call_center_employee_snapshot_on_ticket_create', 'schedule_avatar_refresh',
                 'schedule_employee_assignment_notification']:
        monkeypatch.setattr(telegram, name, observe)
    return case


def test_incoming_ticket_has_number_before_notifications(incoming):
    db = incoming.db
    row = telegram._ensure_ticket_for_incoming(db, 'company', 'client', 'Incoming request')
    assert row.ticket_number == 1 and last_number(db) == 1
    assert row.channel == 'telegram' and row.intake_state == 'ready'
    assert row.assignee_id == incoming.agent_id
    assert row.status == ('new' if incoming.agent_id else 'unassigned')
    assert incoming.observed_numbers and set(incoming.observed_numbers) == {1}
    db.commit()
    db.refresh(row)
    assert row.ticket_number == 1


def test_incoming_existing_ticket_does_not_allocate_again(incoming):
    db = incoming.db
    row = telegram._ensure_ticket_for_incoming(db, 'company', 'client', 'Incoming request')
    row_id = row.id
    db.commit()
    db.expire_all()
    incoming.observed_numbers.clear()
    existing = telegram._ensure_ticket_for_incoming(db, 'company', 'client', 'Another message')
    assert existing.id == row_id and existing.ticket_number == 1
    assert last_number(db) == 1
    assert db.scalars(select(Ticket.id)).all() == [row_id]
    assert incoming.observed_numbers == []


@pytest.mark.parametrize('initialized', [False, True])
def test_incoming_rollback_does_not_consume_number(incoming, initialized):
    db = incoming.db
    if initialized:
        db.add(TicketNumberCounter(id=1, last_number=7))
        db.commit()
    expected = 8 if initialized else 1
    row = telegram._ensure_ticket_for_incoming(db, 'company', 'client', 'Rolled back')
    row_id = row.id
    assert row.ticket_number == expected
    db.rollback()
    assert db.get(Ticket, row_id) is None
    assert last_number(db) == (7 if initialized else None)
    retry = telegram._ensure_ticket_for_incoming(db, 'company', 'client', 'Retry')
    assert retry.ticket_number == expected
    db.commit()
    assert last_number(db) == expected


@pytest.mark.parametrize('workflow,kind', [('ticket', 'normal'), ('task', 'normal'), ('task', 'group_parent')])
def test_ready_assigns_number_without_an_agent_and_rolls_back(case, monkeypatch, workflow, kind):
    db = case.db
    row = ticket(db, workflow_type=workflow, task_kind=kind, intake_state='collecting')
    db.commit()
    monkeypatch.setattr(telegram, 'apply_call_center_employee_snapshot_on_ticket_create', lambda *_: None)
    assert row.ticket_number is None
    assert telegram._ready_ticket_for_distribution(db, row) is None
    assert row.ticket_number == 1 and row.intake_state == 'ready'
    assert row.assignee_id is None and row.status == 'unassigned'
    assert not db.scalars(select(Ticket).where(Ticket.parent_ticket_id == row.id)).all()
    assert telegram._ready_ticket_for_distribution(db, row) is None
    assert last_number(db) == 1
    db.rollback()
    assert row.ticket_number is None and row.intake_state == 'collecting'
    assert last_number(db) is None
    telegram._ready_ticket_for_distribution(db, row)
    db.commit()
    assert row.ticket_number == 1


def test_group_numbers_are_deferred_until_real_parent_assignment(case, monkeypatch):
    db = case.db
    for path, workflow, kind, parent in [('Parent', 'group_task', 'group_parent', ''),
                                        ('Parent/A', 'group_task_child', 'terminal', 'Parent'),
                                        ('Parent/B', 'group_task_child', 'terminal', 'Parent')]:
        db.add(BotButtonSetting(id=str(uuid4()), button_key=f'Synthetic|{workflow}|{path}',
            menu_type='Synthetic', button_path=path, parent_path=parent, button_label=path.split('/')[-1],
            workflow_type=workflow, button_kind=kind, currently_present=True, is_archived=False))
    parent = ticket(db, workflow_type='task', task_kind='group_parent', intake_state='collecting',
                    instruction_button_key='Synthetic|group_task|Parent')
    db.commit()
    monkeypatch.setattr(telegram, 'apply_call_center_employee_snapshot_on_ticket_create', lambda *_: None)
    monkeypatch.setattr(telegram, '_menu_name_for_ticket_client', lambda *_: 'Synthetic')
    assert telegram._ready_ticket_for_distribution(db, parent) is None
    db.commit()
    assert parent.ticket_number == 1
    def children():
        return db.scalars(select(Ticket).where(Ticket.parent_ticket_id == parent.id).order_by(Ticket.subject)).all()
    assert children() == []
    # Another request consumes a number while the parent waits for distribution.
    assert ensure_ticket_number(db, ticket(db)) == 2
    agent = User(id='agent', company_id='company', full_name='Synthetic Agent', role='agent',
                 availability_status='online', is_active=True)
    db.add(agent)
    db.commit()
    assert telegram.assign_ticket_to_agent(db, parent, agent, notify_client=False) is True
    first_numbers = [child.ticket_number for child in children()]
    assert len(first_numbers) == 2 and len(set(first_numbers)) == 2
    assert all(number > 2 for number in first_numbers)
    db.rollback()
    assert children() == [] and parent.ticket_number == 1 and parent.assignee_id is None
    assert last_number(db) == 2
    assert telegram.assign_ticket_to_agent(db, parent, agent, notify_client=False) is True
    db.commit()
    rows = children()
    assert [child.ticket_number for child in rows] == first_numbers
    ids = [child.id for child in rows]
    counter = last_number(db)
    telegram._on_group_parent_assigned(db, parent, agent)
    db.commit()
    assert [child.id for child in children()] == ids and last_number(db) == counter
    assert parent.ticket_number == 1


def test_internal_number_on_creation_without_agent_idempotency_and_rollback(case):
    db = case.db
    admin = User(id='admin', company_id='company', full_name='Synthetic Admin', role='admin')
    db.add(admin)
    db.flush()
    definition = InternalTaskDefinition(id=str(uuid4()), company_id='company', title='Internal', description='Body',
        creator_admin_id=admin.id, responsible_admin_id=admin.id, display_author=False,
        assignment_mode='distribution', schedule_kind='immediate', notify_on_completion=False,
        timezone_name='Europe/Moscow')
    db.add(definition)
    db.commit()
    occurrence_at = datetime(2026, 11, 4, 9, tzinfo=timezone.utc)
    if case.engine.dialect.name == 'sqlite':
        # SQLite legacy transaction mode needs BEGIN before the service's SAVEPOINT.
        db.connection().exec_driver_sql('BEGIN')
    result = internal_tasks.materialize_internal_task(db, definition, occurrence_at)
    assert result.created and result.ticket.ticket_number == 1
    assert result.ticket.assignee_id is None and result.ticket.status == 'unassigned'
    repeated = internal_tasks.materialize_internal_task(db, definition, occurrence_at)
    assert not repeated.created and repeated.ticket.id == result.ticket.id and last_number(db) == 1
    db.rollback()
    assert not db.scalars(select(Ticket)).all() and last_number(db) is None
    result = internal_tasks.materialize_internal_task(db, definition, occurrence_at)
    db.commit()
    assert result.ticket.ticket_number == 1
    repeated = internal_tasks.materialize_internal_task(db, definition, occurrence_at)
    assert not repeated.created and repeated.ticket.id == result.ticket.id and last_number(db) == 1


def test_integration_number_without_agent_deduplication_and_failure_rollback(case, monkeypatch):
    db = case.db
    original = integration_tasks._append_description_message
    def fail(*args, **kwargs):
        assert last_number(db) == 1
        raise RuntimeError('Synthetic creation failure')
    monkeypatch.setattr(integration_tasks, '_append_description_message', fail)
    with pytest.raises(RuntimeError, match='Synthetic creation failure'):
        integration_tasks.create_integration_task(db, deal_id='numbering')
    assert not db.scalars(select(Ticket)).all() and last_number(db) is None
    monkeypatch.setattr(integration_tasks, '_append_description_message', original)
    result = integration_tasks.create_integration_task(db, deal_id='numbering')
    row = db.get(Ticket, result.task_id)
    assert result.result == 'success' and row.ticket_number == 1
    assert row.assignee_id is None and row.status == 'unassigned'
    repeated = integration_tasks.create_integration_task(db, deal_id='numbering')
    assert repeated.result == 'duplicate' and repeated.task_id == row.id and last_number(db) == 1


@pytest.mark.parametrize('intake', ['collecting', 'cancelled', 'ready'])
@pytest.mark.parametrize('endpoint', ['unassigned', 'special', 'card'])
def test_reads_only_allocate_for_ready_intakes(case, intake, endpoint):
    db = case.db
    row = ticket(db, workflow_type='task', intake_state=intake, is_special=endpoint == 'special')
    db.commit()
    app = FastAPI()
    app.include_router(tickets.router)
    app.dependency_overrides[get_db] = lambda: db
    app.dependency_overrides[get_current_user] = lambda: SimpleNamespace(id='admin', role='admin')
    path = row.id if endpoint == 'card' else endpoint
    with TestClient(app) as http:
        response = http.get('/api/v1/tickets/' + path)
    assert response.status_code == 200, response.text
    db.expire_all()
    assert db.get(Ticket, row.id).ticket_number == (1 if intake == 'ready' else None)
    assert last_number(db) == (1 if intake == 'ready' else None)


def test_employee_notification_runtime_does_not_duplicate_numbering_or_tg_registry():
    source = Path("api/app/services/employee_telegram_notifications.py").read_text(encoding="utf-8")
    tg_tag = NOTIFICATION_BOT_RECIPIENT_TG_USERNAME_TAG

    assert "func.max(Ticket.ticket_number)" not in source
    assert "select(func.max(Ticket.ticket_number))" not in source
    assert tg_tag.admin_label not in source
    assert tg_tag.personal_label not in source
