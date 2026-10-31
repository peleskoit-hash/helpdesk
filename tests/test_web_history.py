"""Client Ticket history and publication deletion, using isolated databases only."""
from datetime import timedelta
from uuid import uuid4

import pytest
from sqlalchemy import delete, select, update
from sqlalchemy.orm import Session

from api.app.models import Ticket, WebTaskResult, WebTaskResultFile
from api.app.services.ticket_admin_actions import _delete_ticket_hard
from test_web_task_results import case, engine, seed_task, message, file, close, BASE, ORIGIN

HISTORY = '/api/v1/web/history'


@pytest.fixture(autouse=True)
def legacy_nullable_intake(monkeypatch):
    # Only the isolated schema: legacy databases can contain NULL intake_state.
    monkeypatch.setattr(Ticket.__table__.c.intake_state, 'nullable', True)


def test_intake_filter_precedes_pagination_and_keeps_all_execution_statuses(case):
    expected = set()
    for channel in ['web', 'telegram']:
        for state in ['ready', None, 'collecting', 'cancelled', 'telegram_group_copy']:
            for status in ['unassigned', 'new', 'pending', 'closed']:
                task_id = seed_task(case, channel=channel, status=status, intake_state=state)
                if state is None:
                    with Session(case.engine) as db:
                        db.execute(update(Ticket).where(Ticket.id == task_id).values(intake_state=None))
                        db.commit()
                if state in ['ready', None]:
                    expected.add(task_id)
    first = case.http.get(HISTORY).json()
    second = case.http.get(HISTORY + '?page=2').json()
    assert first['total'] == second['total'] == 16
    assert first['page_size'] == second['page_size'] == 15
    assert len(first['items']) == 15 and len(second['items']) == 1
    assert {row['id'] for row in first['items'] + second['items']} == expected
    with Session(case.engine) as db:
        assert all(row['status'] == db.get(Ticket, row['id']).status for row in first['items'] + second['items'])
    assert case.http.get(HISTORY + '?page=3').json()['items'] == []
    assert case.http.get(HISTORY + '?page=0').status_code == 422
    assert case.http.get(HISTORY + '?page=invalid').status_code == 422


def test_owner_scope_sources_group_children_and_live_ticket_fields(case):
    normal = seed_task(case, channel='telegram', workflow_type='ticket', subject='Telegram subject')
    parent = seed_task(case, task_kind='group_parent')
    seed_task(case, task_kind='group_child', parent_ticket_id=parent)
    seed_task(case, task_kind='group_child')  # Even an orphan child is not a client row.
    seed_task(case, client_id='peer')
    seed_task(case, client_id='foreign', company_id='other')
    seed_task(case, company_id='other')  # Same client_id alone is insufficient.
    response = case.http.get(HISTORY + '?client_id=foreign&company_id=other&limit=100')
    assert response.status_code == 200 and response.headers['cache-control'] == 'no-store'
    rows = response.json()
    assert rows['total'] == 2
    assert {row['id'] for row in rows['items']} == {normal, parent}
    assert {row['channel'] for row in rows['items']} == {'telegram', 'web'}
    assert not any(row['has_web_result'] for row in rows['items'])
    created_at = case.clock[0] - timedelta(days=3)
    with Session(case.engine) as db:
        task = db.get(Ticket, normal)
        task.subject = 'Changed live subject'
        task.created_at = created_at
        db.commit()
    row = next(row for row in case.http.get(HISTORY).json()['items'] if row['id'] == normal)
    assert row['subject'] == 'Changed live subject'
    assert row['created_at'].startswith(created_at.strftime('%Y-%m-%dT%H:%M:%S'))
    assert set(row) == {'id', 'subject', 'channel', 'status', 'created_at', 'has_web_result', 'read_at'}
    assert row['status'] == 'pending'
    case.http.cookies.clear()
    assert case.http.get(HISTORY).status_code == 401
    case.http.cookies.set('esdesk_web_session', 'peer')
    assert case.http.get(HISTORY).json()['total'] == 1


def publish(case):
    task_id = seed_task(case)
    msg = message(case, task_id, 'Published answer')
    file(case, task_id, msg, 'final.pdf', b'published bytes')
    assert close(case, task_id).status_code == 200
    return case.http.get(f'{BASE}/{task_id}').json()


def test_history_result_read_and_existing_download(case):
    result = publish(case)
    row = case.http.get(HISTORY).json()['items'][0]
    assert row['id'] == result['task_id'] and row['has_web_result'] and row['read_at'] is None
    assert case.http.get(result['files'][0]['download_url']).content == b'published bytes'
    assert case.http.post(f"{BASE}/{row['id']}/read", headers=ORIGIN).status_code == 200
    assert case.http.get(HISTORY).json()['items'][0]['read_at'] is not None


def test_hard_delete_cleans_publication_and_blocks_direct_urls(case):
    result = publish(case)
    other = publish(case)
    task_id = result['task_id']
    with Session(case.engine) as db:
        assert _delete_ticket_hard(db, db.get(Ticket, task_id)) == []
        db.commit()
        assert db.get(Ticket, task_id) is None
        assert db.get(WebTaskResult, task_id) is None
        assert not db.scalars(select(WebTaskResultFile).where(WebTaskResultFile.result_task_id == task_id)).all()
    assert case.http.get(HISTORY).json()['total'] == 1
    assert case.http.get(BASE).json()['total'] == 1
    assert case.http.get(f'{BASE}/{task_id}').status_code == 404
    assert case.http.post(f'{BASE}/{task_id}/read', headers=ORIGIN).status_code == 404
    assert case.http.get(result['files'][0]['download_url']).status_code == 404
    assert case.http.get(other['files'][0]['download_url']).content == b'published bytes'


@pytest.mark.parametrize('mutation', ['missing', 'client', 'company'])
def test_existing_snapshot_never_grants_access_without_matching_live_ticket(case, mutation):
    task_id, file_id = str(uuid4()), str(uuid4())
    if mutation != 'missing':
        seed_task(case, id=task_id, client_id='peer' if mutation == 'client' else 'client',
                  company_id='other' if mutation == 'company' else 'co')
    with Session(case.engine) as db:
        db.add(WebTaskResult(task_id=task_id, company_id='co', client_id='client', message_id=str(uuid4()),
                            text='Old snapshot', closed_at=case.clock[0]))
        db.flush()
        db.add(WebTaskResultFile(id=file_id, result_task_id=task_id, filename='old.txt', mime_type='text/plain',
                                size=3, data=b'old', position=0))
        db.commit()
    assert case.http.get(f'{BASE}/{task_id}').status_code == 404
    assert case.http.post(f'{BASE}/{task_id}/read', headers=ORIGIN).status_code == 404
    assert case.http.get(f'{BASE}/{task_id}/files/{file_id}').status_code == 404
    assert case.http.get(BASE).json()['total'] == 0
    assert case.http.get(HISTORY).json()['total'] == 0


def test_group_parent_deletion_cleans_child_publications_in_same_transaction(case):
    parent = seed_task(case, task_kind='group_parent')
    child = seed_task(case, task_kind='group_child', parent_ticket_id=parent)
    with Session(case.engine) as db:
        for task_id in [parent, child]:
            db.add(WebTaskResult(task_id=task_id, company_id='co', client_id='client', message_id=str(uuid4()),
                                text='Legacy result', closed_at=case.clock[0]))
            db.flush()
            db.add(WebTaskResultFile(id=str(uuid4()), result_task_id=task_id, filename='old.txt',
                                    mime_type='text/plain', size=3, data=b'old', position=0))
        db.commit()
        _delete_ticket_hard(db, db.get(Ticket, parent))
        db.flush()
        assert not db.scalars(select(WebTaskResult)).all()
        assert not db.scalars(select(WebTaskResultFile)).all()
        db.rollback()
        assert len(db.scalars(select(WebTaskResult)).all()) == 2
        assert len(db.scalars(select(WebTaskResultFile)).all()) == 2
        _delete_ticket_hard(db, db.get(Ticket, parent))
        db.commit()
        assert db.get(Ticket, parent) is None and db.get(Ticket, child) is None
        assert not db.scalars(select(WebTaskResult)).all()
        assert not db.scalars(select(WebTaskResultFile)).all()
