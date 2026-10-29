from copy import deepcopy
from datetime import datetime, timezone
from concurrent.futures import ThreadPoolExecutor
from threading import Event
from types import SimpleNamespace
from uuid import uuid4

import pytest

from api.app.models import BackgroundContour, User
from api.app.routers import users
from api.app.routers.background_contours import router
from api.app.services import background_contours
from test_user_preferences import appearance_context, preferences_client, _appearance_patch, _current_user


URL = '/api/v1/users/me/preferences'


@pytest.fixture
def case(appearance_context, preferences_client, monkeypatch, tmp_path):
    factory, actor, writer, other = appearance_context
    monkeypatch.setattr(background_contours.settings, 'background_contour_storage_dir', str(tmp_path / 'contours'))
    preferences_client.app.include_router(router)
    ids = [str(uuid4()), str(uuid4())]
    with factory() as db:
        for contour_id in ids:
            row = BackgroundContour(id=contour_id, name='Contour', author_user_id=actor.real_user_id, author_name='Author',
                                    created_at=datetime.now(timezone.utc), storage_key=contour_id.replace('-', '') + '.png',
                                    format='png', file_size=3, rotation=45, rings=2, distance=20, base_scale=100)
            db.add(row)
            path, _ = background_contours._paths(row)
            path.parent.mkdir(exist_ok=True)
            path.write_bytes(b'png')
        db.commit()
    yield SimpleNamespace(factory=factory, actor=actor, writer=writer, other=other, ids=ids, client=preferences_client)


def patch(case, sequence, patterns, **extra):
    return case.client.patch(URL, json={'background_patterns': patterns, 'appearance_write': {
        'writer_id': case.writer, 'sequence': sequence}, **extra})


def test_uuid_selection_and_per_contour_parameters_merge_in_both_themes(case):
    first, second = case.ids
    response = patch(case, 1, {
        'light': {'selected': first, 'patterns': {first: {'scale': 150, 'intensity': 73, 'variable': True}}},
        'dark': {'selected': second, 'patterns': {second: {'scale': 65, 'seed': 65535}}},
    })
    assert response.status_code == 200, response.text
    saved = response.json()['background_patterns']
    seed = saved['light']['patterns'][first]['seed']
    assert 1 <= seed <= 65535
    assert saved['dark']['patterns'][second]['seed'] == 65535
    patch(case, 2, {'light': {'selected': second, 'patterns': {second: {'scale': 180}}}})
    patch(case, 3, {'light': {'patterns': {first: {'intensity': 42}}}})
    patch(case, 4, {'light': {'selected': 'waves'}})
    loaded = patch(case, 5, {'light': {'selected': first}}).json()['background_patterns']
    assert loaded['light']['patterns'][first] == {**saved['light']['patterns'][first], 'intensity': 42}
    assert loaded['light']['patterns'][second]['scale'] == 180
    assert loaded['dark'] == saved['dark']
    assert case.client.get(URL).json()['background_patterns'] == loaded


@pytest.mark.parametrize('kind', ['selected', 'parameters', 'malformed', 'server-marker'])
def test_invalid_or_absent_uuid_cannot_be_persisted_atomically(case, kind):
    missing = str(uuid4())
    payload = {'light': {'selected': missing}}
    if kind == 'parameters':
        payload = {'light': {'patterns': {missing: {'scale': 150}}}}
    elif kind == 'malformed':
        payload = {'light': {'selected': 'not-a-uuid'}}
    elif kind == 'server-marker':
        payload = {'light': {'selected': missing, 'fallback_grid': True}}
    before = case.client.get(URL).json()
    response = patch(case, 1, payload, send_by_enter=True)
    assert response.status_code == 422
    assert case.client.get(URL).json() == before
    with case.factory() as db:
        assert db.get(User, case.actor.real_user_id).appearance_write_state_json[case.writer]['sequence'] == 0


def test_deleted_contour_falls_back_for_all_readers_preserving_other_parameters_and_revision(case):
    contour_id, second = case.ids
    patterns = {'light': {'selected': contour_id, 'patterns': {
        contour_id: {'scale': 150, 'intensity': 80, 'variable': True, 'seed': 17},
        'grid': {'scale': 175, 'intensity': 91, 'variable': True, 'seed': 11},
    }}, 'dark': {'selected': second, 'patterns': {second: {'seed': 23}}}}
    saved = patch(case, 1, patterns).json()
    with case.factory() as db:
        other = db.get(User, case.other)
        other.background_patterns_json = deepcopy(saved['background_patterns'])
        other.theme_preference = 'dark'
        other.accent_hue_dark = 123
        db.commit()
    assert case.client.delete('/api/v1/background-contours/' + contour_id).status_code == 204
    read = case.client.get(URL).json()
    assert read['background_patterns']['light']['selected'] == 'grid'
    assert read['background_patterns']['light']['fallback_grid'] is True
    assert read['background_patterns']['light']['patterns'] == saved['background_patterns']['light']['patterns']
    assert read['background_patterns']['dark'] == saved['background_patterns']['dark']
    assert read['appearance_revision'] != saved['appearance_revision']
    assert case.client.get(URL).json() == read
    with case.factory() as db:
        other = users.get_my_preferences(db=db, current_user=_current_user(effective_user_id=case.other, real_user_id=case.other)).model_dump(mode='json')
        assert other['background_patterns']['light']['fallback_grid'] is True
        assert other['theme_preference'] == 'dark' and other['accent_hue_dark'] == 123
    # Selecting the grid explicitly restores that pattern's own saved parameters.
    explicit = patch(case, 2, {'light': {'selected': 'grid'}}).json()['background_patterns']['light']
    assert 'fallback_grid' not in explicit
    assert explicit['patterns']['grid']['scale'] == 175


def test_deleted_uuid_rejected_in_patch_and_initial_session_and_cannot_restore_stale_snapshot(case):
    contour_id = case.ids[0]
    snapshot = patch(case, 1, {'light': {'selected': contour_id}}).json()
    assert case.client.delete('/api/v1/background-contours/' + contour_id).status_code == 204
    response = case.client.post(URL + '/appearance-session', json={
        'preferences': {'background_patterns': {'light': {'selected': contour_id}}},
        'expected_revision': snapshot['appearance_revision'], 'sequence': 10,
    })
    assert response.status_code == 200
    assert response.json()['preferences']['background_patterns']['light']['fallback_grid'] is True
    before = case.client.get(URL).json()
    assert patch(case, 2, {'dark': {'selected': contour_id}}).status_code == 422
    assert case.client.get(URL).json() == before
    response = case.client.post(URL + '/appearance-session', json={
        'preferences': {'background_patterns': {'light': {'selected': contour_id}}},
        'expected_revision': before['appearance_revision'], 'sequence': 11,
    })
    assert response.status_code == 422
    assert case.client.get(URL).json() == before


def test_old_sequences_cannot_overwrite_uuid_selection_and_commit_failure_rolls_back(case, monkeypatch):
    first, second = case.ids
    saved = patch(case, 3, {'light': {'selected': first, 'patterns': {first: {'seed': 19}}}}).json()
    assert patch(case, 2, {'light': {'selected': second}}).json() == saved
    with case.factory() as db:
        def fail():
            db.flush()
            raise RuntimeError('commit failed')
        monkeypatch.setattr(db, 'commit', fail)
        from api.app.schemas import UserPreferencesUpdate
        with pytest.raises(RuntimeError):
            users.update_my_preferences(UserPreferencesUpdate(
                background_patterns={'light': {'selected': second}},
                appearance_write={'writer_id': case.writer, 'sequence': 4},
            ), db=db, current_user=case.actor)
        db.rollback()
    assert case.client.get(URL).json() == saved


def test_read_repair_refreshes_stale_orm_state_instead_of_overwriting_a_new_choice(case):
    first = case.ids[0]
    patch(case, 1, {'light': {'selected': first}})
    with case.factory() as db:
        stale = db.get(User, case.actor.real_user_id)
        _appearance_patch(case.factory, case.actor, case.writer, 2, background_patterns={'light': {'selected': 'waves'}})
        result = users.get_my_preferences(db=db, current_user=case.actor)
        assert result.model_dump(mode='json')['background_patterns']['light']['selected'] == 'waves'


def test_selection_validation_and_delete_cannot_race_past_the_same_transaction(case):
    locked, release, deleting = Event(), Event(), Event()
    contour_id = case.ids[0]
    def choose():
        with case.factory() as db:
            commit = db.commit
            def pause():
                locked.set()
                assert release.wait(5)
                commit()
            db.commit = pause
            from api.app.schemas import UserPreferencesUpdate
            users.update_my_preferences(UserPreferencesUpdate(
                background_patterns={'light': {'selected': contour_id}},
                appearance_write={'writer_id': case.writer, 'sequence': 1},
            ), db=db, current_user=case.actor)
    def delete():
        deleting.set()
        with case.factory() as db:
            background_contours.delete_contour(db, case.actor, contour_id)
    with ThreadPoolExecutor(max_workers=2) as pool:
        first = pool.submit(choose)
        assert locked.wait(5)
        second = pool.submit(delete)
        try:
            assert deleting.wait(5) and not second.done()
        finally:
            release.set()
        first.result(timeout=5); second.result(timeout=5)
    assert case.client.get(URL).json()['background_patterns']['light']['fallback_grid'] is True
