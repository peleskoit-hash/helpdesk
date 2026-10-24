from __future__ import annotations

from datetime import datetime
from concurrent.futures import ThreadPoolExecutor
from threading import Event
from types import SimpleNamespace
import uuid

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from api.app.db import Base
from api.app.models import Company, User
from api.app.schemas import UserPreferencesUpdate
from pydantic import ValidationError
from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient
from api.app.services.user_appearance import MAX_APPEARANCE_SESSIONS, COLOR_FIELDS

try:
    from api.app.routers import users as users_router
except Exception as exc:
    if exc.__class__.__name__ == "ZoneInfoNotFoundError":
        pytest.skip("Europe/Moscow tzdata is unavailable in the host test environment", allow_module_level=True)
    raise


def _session():
    engine = create_engine("sqlite:///:memory:", future=True)
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine, autoflush=False, autocommit=False)
    return Session()


def _company(session) -> Company:
    company = Company(id=str(uuid.uuid4()), name="ACME", is_active=True, created_at=datetime.utcnow())
    session.add(company)
    session.commit()
    return company


def _user(
    session,
    *,
    company_id: str,
    full_name: str,
    role: str = "agent",
    send_by_enter=None,
    color_enabled=None,
    theme_preference=None,
    ticket_list_columns_json=None,
) -> User:
    user = User(
        id=str(uuid.uuid4()),
        company_id=company_id,
        email=None,
        login=f"{full_name.lower().replace(' ', '.')}.{uuid.uuid4().hex[:6]}",
        password_hash=None,
        full_name=full_name,
        role=role,
        is_active=True,
        availability_status="online",
        legacy_ticket_count=0,
        created_at=datetime.utcnow(),
        updated_at=datetime.utcnow(),
    )
    if send_by_enter is not None:
        user.send_by_enter = send_by_enter
    if color_enabled is not None:
        user.color_enabled = color_enabled
    if theme_preference is not None:
        user.theme_preference = theme_preference
    if ticket_list_columns_json is not None:
        user.ticket_list_columns_json = ticket_list_columns_json
    session.add(user)
    session.commit()
    return user


def _current_user(*, effective_user_id: str, real_user_id: str, role: str = "agent"):
    return SimpleNamespace(
        id=effective_user_id,
        role=role,
        real_user_id=real_user_id,
        real_role=role,
        is_real_admin=(role == "admin"),
    )


@pytest.fixture
def appearance_context(tmp_path):
    engine = create_engine(f"sqlite:///{(tmp_path / 'appearance.sqlite').as_posix()}", future=True)
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, autoflush=False)
    with factory() as db:
        company = _company(db)
        user = _user(db, company_id=company.id, full_name="Writer A")
        other = _user(db, company_id=company.id, full_name="Writer B")
        actor = _current_user(effective_user_id=user.id, real_user_id=user.id)
        writer = str(users_router.create_my_appearance_session(db=db, current_user=actor).writer_id)
        other_id = other.id
    yield factory, actor, writer, other_id
    engine.dispose()


def _appearance_patch(factory, actor, writer, sequence, **fields):
    with factory() as db:
        return users_router.update_my_preferences(
            UserPreferencesUpdate(appearance_write={"writer_id": writer, "sequence": sequence}, **fields),
            db=db, current_user=actor,
        )


@pytest.mark.parametrize("field", ["accent_intensity_light", *COLOR_FIELDS])
@pytest.mark.parametrize("reverse", [False, True])
def test_appearance_delayed_request_order_is_persisted_atomically(appearance_context, reverse, field):
    factory, actor, writer, _ = appearance_context
    first_done = Event()
    def delayed_request():
        assert first_done.wait(5)
        return _appearance_patch(factory, actor, writer, 1 if reverse else 3,
                                 **{field: 17 if reverse else 99})
    with ThreadPoolExecutor(max_workers=1) as pool:
        delayed = pool.submit(delayed_request)
        _appearance_patch(factory, actor, writer, 3 if reverse else 1,
                          **{field: 99 if reverse else 17})
        first_done.set()
        delayed.result(timeout=5)
    # Also reject the intermediate change and duplicates with a different body.
    _appearance_patch(factory, actor, writer, 2, **{field: 49})
    _appearance_patch(factory, actor, writer, 3, **{field: 17})
    with factory() as db:
        user = db.get(User, actor.real_user_id)
        assert getattr(user, field) == 99
        assert user.appearance_write_state_json[writer]["sequence"] == 3
        assert user.accent_intensity_dark == 50
        assert user.color_enabled is True
        assert user.send_by_enter is False
        assert user.ticket_list_columns_json is None


def test_appearance_check_and_write_hold_one_database_lock(appearance_context):
    factory, actor, writer, _ = appearance_context
    locked, release, second_started, second_done = Event(), Event(), Event(), Event()
    def first_request():
        with factory() as db:
            refresh = db.refresh
            def pause_after_lock(*args, **kwargs):
                refresh(*args, **kwargs)
                if kwargs.get("attribute_names"):
                    locked.set()
                    assert release.wait(5)
            db.refresh = pause_after_lock
            return users_router.update_my_preferences(UserPreferencesUpdate(
                accent_intensity_light=17, appearance_write={"writer_id": writer, "sequence": 1},
            ), db=db, current_user=actor)
    def second_request():
        second_started.set()
        try:
            return _appearance_patch(factory, actor, writer, 2, accent_intensity_light=99)
        finally:
            second_done.set()
    with ThreadPoolExecutor(max_workers=2) as pool:
        first = pool.submit(first_request)
        assert locked.wait(5)
        second = pool.submit(second_request)
        assert second_started.wait(5)
        try:
            assert not second.done()  # It must not pass the check while the first transaction holds the lock.
            assert not second_done.wait(.1)
        finally:
            release.set()
        first.result(timeout=5)
        second.result(timeout=5)
    with factory() as db:
        assert db.get(User, actor.real_user_id).accent_intensity_light == 99


@pytest.mark.parametrize("sequence", [1, 2])
@pytest.mark.parametrize("include_appearance", [False, True])
def test_stale_mixed_patch_only_discards_appearance_fields(appearance_context, sequence, include_appearance):
    factory, actor, writer, other_id = appearance_context
    _appearance_patch(factory, actor, writer, 2, theme_preference="dark", accent_preference="blue",
                      accent_intensity_light=99, accent_intensity_dark=73)
    columns = [{"key": "subject", "width": 340}, {"key": "age", "width": 120}]
    appearance = {"theme_preference": "light", "accent_preference": "purple",
                  "accent_intensity_light": 17, "accent_intensity_dark": 0} if include_appearance else {}
    result = _appearance_patch(factory, actor, writer, sequence, **appearance,
                              send_by_enter=True, color_enabled=False,
                              task_panel_enabled=True, ticket_list_columns=columns)
    assert (result.theme_preference, result.accent_preference) == ("dark", "blue")
    assert (result.accent_intensity_light, result.accent_intensity_dark) == (99, 73)
    assert result.send_by_enter is True
    assert result.color_enabled is False
    assert result.task_panel_enabled is True
    assert [column.model_dump() for column in result.ticket_list_columns] == columns
    with factory() as db:
        user = db.get(User, actor.real_user_id)
        assert user.appearance_write_state_json[writer]["sequence"] == 2
        assert user.send_by_enter is True and user.color_enabled is False
        assert user.task_panel_enabled is True
        assert user.ticket_list_columns_json == columns
        assert user.accent_intensity_light == 99 and user.accent_intensity_dark == 73
        other = db.get(User, other_id)
        assert other.send_by_enter is False and other.color_enabled is True
        assert other.ticket_list_columns_json is None


def test_nonappearance_patch_does_not_consume_an_appearance_sequence(appearance_context):
    factory, actor, writer, _ = appearance_context
    _appearance_patch(factory, actor, writer, 10, send_by_enter=True)
    result = _appearance_patch(factory, actor, writer, 1, accent_intensity_light=99)
    assert result.send_by_enter is True
    assert result.accent_intensity_light == 99


def test_independent_appearance_sessions_follow_server_acceptance_order(appearance_context):
    factory, actor, writer_a, _ = appearance_context
    with factory() as db:
        writer_b = str(users_router.create_my_appearance_session(db=db, current_user=actor).writer_id)
    _appearance_patch(factory, actor, writer_a, 10, accent_intensity_light=17)
    _appearance_patch(factory, actor, writer_b, 1, accent_intensity_light=99)
    result = _appearance_patch(factory, actor, writer_a, 9, accent_intensity_light=35)
    assert result.accent_intensity_light == 99
    result = _appearance_patch(factory, actor, writer_a, 11, accent_intensity_light=35)
    assert result.accent_intensity_light == 35
    # A duplicate from B cannot become a new write after A's newer accepted request.
    result = _appearance_patch(factory, actor, writer_b, 1, accent_intensity_light=99)
    assert result.accent_intensity_light == 35


def test_appearance_sessions_are_bounded_and_evicted_writers_cannot_return(appearance_context):
    factory, actor, oldest, _ = appearance_context
    _appearance_patch(factory, actor, oldest, 1, accent_intensity_light=17)
    for _ in range(MAX_APPEARANCE_SESSIONS + 2):
        with factory() as db:
            latest = str(users_router.create_my_appearance_session(db=db, current_user=actor).writer_id)
    _appearance_patch(factory, actor, latest, 1, accent_intensity_light=99)
    with pytest.raises(HTTPException) as exc:
        _appearance_patch(factory, actor, oldest, 100, accent_intensity_light=17)
    assert exc.value.status_code == 409
    assert exc.value.detail["preferences"]["accent_intensity_light"] == 99
    with factory() as db:
        state = db.get(User, actor.real_user_id).appearance_write_state_json
        assert len(state) == MAX_APPEARANCE_SESSIONS
        assert oldest not in state


def test_appearance_writer_cannot_access_another_users_preferences(appearance_context):
    factory, actor, writer, other_id = appearance_context
    other_actor = _current_user(effective_user_id=actor.real_user_id, real_user_id=other_id)
    with pytest.raises(HTTPException) as exc:
        _appearance_patch(factory, other_actor, writer, 1, accent_intensity_light=99)
    assert exc.value.status_code == 409
    with factory() as db:
        other = db.get(User, other_id)
        assert other.accent_intensity_light == 50
        assert other.appearance_write_state_json is None


def test_appearance_counter_and_preferences_roll_back_together(appearance_context, monkeypatch):
    factory, actor, writer, _ = appearance_context
    def fail_columns(_):
        raise RuntimeError("failure before commit")
    with factory() as db:
        with monkeypatch.context() as patch:
            patch.setattr(users_router, "dump_ticket_list_columns", fail_columns)
            with pytest.raises(RuntimeError):
                users_router.update_my_preferences(UserPreferencesUpdate(
                    accent_intensity_light=99, ticket_list_columns=[{"key": "subject", "width": 340}],
                    appearance_write={"writer_id": writer, "sequence": 2},
                ), db=db, current_user=actor)
        db.rollback()
    with factory() as db:
        user = db.get(User, actor.real_user_id)
        assert user.accent_intensity_light == 50
        assert user.appearance_write_state_json[writer]["sequence"] == 0


def test_appearance_http_contract_and_unrelated_preferences(appearance_context):
    factory, actor, _, _ = appearance_context
    app = FastAPI()
    app.include_router(users_router.router)
    def get_test_db():
        with factory() as db:
            yield db
    app.dependency_overrides[users_router.get_db] = get_test_db
    app.dependency_overrides[users_router.get_current_user] = lambda: actor
    with TestClient(app) as client:
        token = client.post("/api/v1/users/me/preferences/appearance-session")
        assert token.status_code == 200
        writer = token.json()["writer_id"]
        for sequence, value in [(2, 99), (1, 17)]:
            response = client.patch("/api/v1/users/me/preferences", json={
                "accent_intensity_light": value, "appearance_write": {"writer_id": writer, "sequence": sequence},
            })
            assert response.status_code == 200
            assert response.json()["accent_intensity_light"] == 99
        mixed = client.patch("/api/v1/users/me/preferences", json={
            "accent_intensity_light": 17, "appearance_write": {"writer_id": writer, "sequence": 1},
            "send_by_enter": True, "color_enabled": False, "task_panel_enabled": True,
            "ticket_list_columns": [{"key": "subject", "width": 340}],
        })
        assert mixed.status_code == 200
        assert mixed.json()["accent_intensity_light"] == 99
        loaded = client.get("/api/v1/users/me/preferences").json()
        assert loaded["send_by_enter"] is True
        assert loaded["color_enabled"] is False
        assert loaded["task_panel_enabled"] is True
        assert loaded["ticket_list_columns"] == [{"key": "subject", "width": 340}]
        assert loaded["accent_intensity_light"] == 99
        assert "appearance_write_state_json" not in loaded
        invalid = client.patch("/api/v1/users/me/preferences", json={
            "accent_intensity_light": 17, "appearance_write": {"writer_id": writer, "sequence": 0},
        })
        assert invalid.status_code == 422


@pytest.fixture
def preferences_client(appearance_context):
    factory, actor, _, _ = appearance_context
    app = FastAPI()
    app.include_router(users_router.router)

    def get_test_db():
        with factory() as db:
            yield db

    app.dependency_overrides[users_router.get_db] = get_test_db
    app.dependency_overrides[users_router.get_current_user] = lambda: actor
    with TestClient(app) as client:
        yield client


@pytest.mark.parametrize("field,value", [
    ("theme_preference", "dark"), ("accent_preference", "blue"),
    ("accent_intensity_light", 17), ("accent_intensity_dark", 17),
    *((field, 17) for field in COLOR_FIELDS),
])
@pytest.mark.parametrize("metadata", [
    {}, {"appearance_write": None}, {"appearance_write": {}},
    {"appearance_write": {"writer_id": "invalid", "sequence": 1}},
    {"appearance_write": {"writer_id": str(uuid.UUID(int=1))}},
    {"appearance_write": {"sequence": 1}},
    {"appearance_write": {"writer_id": str(uuid.UUID(int=1)), "sequence": 0}},
])
def test_appearance_patch_requires_valid_write_metadata(
    appearance_context, preferences_client, field, value, metadata,
):
    factory, actor, writer, _ = appearance_context
    _appearance_patch(factory, actor, writer, 2, accent_intensity_light=99)
    before = preferences_client.get("/api/v1/users/me/preferences").json()
    response = preferences_client.patch("/api/v1/users/me/preferences", json={
        field: value, "send_by_enter": True, **metadata,
    })
    assert response.status_code == 422
    assert preferences_client.get("/api/v1/users/me/preferences").json() == before
    with factory() as db:
        user = db.get(User, actor.real_user_id)
        assert user.accent_intensity_light == 99
        assert user.send_by_enter is False
        assert user.appearance_write_state_json[writer]["sequence"] == 2


@pytest.mark.parametrize("fields", [
    {"send_by_enter": True}, {"color_enabled": False}, {"task_panel_enabled": True},
    {"ticket_list_columns": [{"key": "subject", "width": 340}]},
])
def test_nonappearance_preferences_do_not_require_appearance_write(appearance_context, preferences_client, fields):
    factory, actor, writer, _ = appearance_context
    _appearance_patch(factory, actor, writer, 2, accent_intensity_light=99)
    response = preferences_client.patch("/api/v1/users/me/preferences", json=fields)
    assert response.status_code == 200
    loaded = preferences_client.get("/api/v1/users/me/preferences").json()
    for field, value in fields.items():
        assert response.json()[field] == value
        assert loaded[field] == value
    with factory() as db:
        user = db.get(User, actor.real_user_id)
        assert user.accent_intensity_light == 99
        assert user.appearance_write_state_json[writer]["sequence"] == 2


@pytest.mark.parametrize("sequence", [0, -1, True, 1.5, 9007199254740992])
def test_appearance_sequence_validation(sequence):
    with pytest.raises(ValidationError):
        UserPreferencesUpdate(accent_intensity_light=99,
                              appearance_write={"writer_id": str(uuid.uuid4()), "sequence": sequence})


@pytest.mark.parametrize("accent", ["purple", "blue", "red", "green", "orange", "teal", "mono"])
def test_appearance_preferences_roundtrip_and_user_isolation(accent):
    session = _session()
    company = _company(session)
    user = _user(session, company_id=company.id, full_name="Appearance A")
    other = _user(session, company_id=company.id, full_name="Appearance B")
    current = _current_user(effective_user_id=other.id, real_user_id=user.id)
    writer = users_router.create_my_appearance_session(db=session, current_user=current).writer_id
    users_router.update_my_preferences(
        UserPreferencesUpdate(accent_preference=accent, accent_intensity_light=0, accent_intensity_dark=100,
                              appearance_write={"writer_id": writer, "sequence": 1}),
        db=session, current_user=current,
    )
    users_router.update_my_preferences(
        UserPreferencesUpdate(theme_preference="dark", appearance_write={"writer_id": writer, "sequence": 2}),
        db=session, current_user=current,
    )
    # A fresh ORM session represents a new login/device reading server state.
    with sessionmaker(bind=session.get_bind())() as fresh:
        loaded = users_router.get_my_preferences(db=fresh, current_user=current)
        untouched = users_router.get_my_preferences(
            db=fresh, current_user=_current_user(effective_user_id=other.id, real_user_id=other.id),
        )
    assert loaded.accent_preference == accent
    assert loaded.theme_preference == "dark"
    assert (loaded.accent_intensity_light, loaded.accent_intensity_dark) == (0, 100)
    assert loaded.color_enabled is True
    assert untouched.accent_preference == "purple"
    assert (untouched.accent_intensity_light, untouched.accent_intensity_dark) == (50, 50)


@pytest.mark.parametrize("payload", [
    {"accent_preference": "pink"}, {"accent_preference": None},
    {"accent_intensity_light": -1}, {"accent_intensity_dark": 101},
    {"accent_intensity_light": None}, {"accent_intensity_dark": None},
    {"accent_intensity_light": 1.5}, {"accent_intensity_dark": True},
])
def test_appearance_preferences_reject_invalid_values(payload):
    with pytest.raises(ValidationError):
        UserPreferencesUpdate(**payload)


def test_user_preferences_default_is_false_for_new_user():
    session = _session()
    company = _company(session)
    user = _user(session, company_id=company.id, full_name="Ivan Petrov")

    result = users_router.get_my_preferences(
        db=session,
        current_user=_current_user(effective_user_id=user.id, real_user_id=user.id),
    )

    assert result.send_by_enter is False
    assert result.color_enabled is True
    assert result.task_panel_enabled is False
    assert result.theme_preference == "light"
    assert result.accent_preference == "purple"
    assert result.accent_intensity_light == result.accent_intensity_dark == 50
    assert [column.model_dump() for column in result.ticket_list_columns] == [
        {"key": "subject", "width": 0},
        {"key": "requester", "width": 150},
        {"key": "assignee", "width": 170},
        {"key": "age", "width": 120},
        {"key": "created_at", "width": 140},
        {"key": "last_reply", "width": 150},
    ]


def test_user_preferences_patch_true_persists_and_roundtrips():
    session = _session()
    company = _company(session)
    user = _user(session, company_id=company.id, full_name="Ivan Petrov")
    current_user = _current_user(effective_user_id=user.id, real_user_id=user.id)

    updated = users_router.update_my_preferences(
        UserPreferencesUpdate(send_by_enter=True),
        db=session,
        current_user=current_user,
    )
    loaded = users_router.get_my_preferences(db=session, current_user=current_user)

    session.refresh(user)
    assert updated.send_by_enter is True
    assert updated.color_enabled is True
    assert user.send_by_enter is True
    assert user.color_enabled is True
    assert loaded.send_by_enter is True
    assert loaded.color_enabled is True


def test_user_preferences_patch_false_after_true_persists():
    session = _session()
    company = _company(session)
    user = _user(session, company_id=company.id, full_name="Ivan Petrov", send_by_enter=True)
    current_user = _current_user(effective_user_id=user.id, real_user_id=user.id)

    updated = users_router.update_my_preferences(
        UserPreferencesUpdate(send_by_enter=False),
        db=session,
        current_user=current_user,
    )

    session.refresh(user)
    assert updated.send_by_enter is False
    assert user.send_by_enter is False


def test_user_preferences_patch_color_false_persists_without_changing_send_by_enter():
    session = _session()
    company = _company(session)
    user = _user(session, company_id=company.id, full_name="Ivan Petrov", send_by_enter=True)
    current_user = _current_user(effective_user_id=user.id, real_user_id=user.id)

    updated = users_router.update_my_preferences(
        UserPreferencesUpdate(color_enabled=False),
        db=session,
        current_user=current_user,
    )

    session.refresh(user)
    assert updated.color_enabled is False
    assert updated.send_by_enter is True
    assert user.color_enabled is False
    assert user.send_by_enter is True


def test_user_preferences_patch_task_panel_persists_without_changing_other_preferences():
    session = _session()
    company = _company(session)
    columns = [{"key": "subject", "width": 0}, {"key": "created_at", "width": 140}]
    user = _user(
        session,
        company_id=company.id,
        full_name="Ivan Petrov",
        send_by_enter=True,
        color_enabled=False,
        ticket_list_columns_json=columns,
    )
    current_user = _current_user(effective_user_id=user.id, real_user_id=user.id)

    updated = users_router.update_my_preferences(
        UserPreferencesUpdate(task_panel_enabled=True),
        db=session,
        current_user=current_user,
    )

    session.refresh(user)
    assert updated.task_panel_enabled is True
    assert updated.send_by_enter is True
    assert updated.color_enabled is False
    assert [column.model_dump() for column in updated.ticket_list_columns] == columns
    assert user.task_panel_enabled is True
    assert user.send_by_enter is True
    assert user.color_enabled is False
    assert user.ticket_list_columns_json == columns


def test_user_preferences_theme_persists_and_roundtrips():
    session = _session()
    company = _company(session)
    user = _user(session, company_id=company.id, full_name="Ivan Petrov")
    current_user = _current_user(effective_user_id=user.id, real_user_id=user.id)
    writer = users_router.create_my_appearance_session(db=session, current_user=current_user).writer_id

    updated = users_router.update_my_preferences(
        UserPreferencesUpdate(theme_preference="dark", appearance_write={"writer_id": writer, "sequence": 1}),
        db=session,
        current_user=current_user,
    )
    loaded = users_router.get_my_preferences(db=session, current_user=current_user)

    session.refresh(user)
    assert updated.theme_preference == "dark"
    assert loaded.theme_preference == "dark"
    assert user.theme_preference == "dark"


def test_user_preferences_theme_rejects_invalid_value():
    with pytest.raises(ValidationError):
        UserPreferencesUpdate(theme_preference="blue")


def test_user_preferences_theme_rejects_explicit_null():
    with pytest.raises(ValidationError):
        UserPreferencesUpdate(theme_preference=None)


def test_user_preferences_patch_theme_preserves_other_preferences():
    session = _session()
    company = _company(session)
    columns = [{"key": "subject", "width": 0}, {"key": "created_at", "width": 140}]
    user = _user(
        session,
        company_id=company.id,
        full_name="Ivan Petrov",
        send_by_enter=True,
        color_enabled=False,
        ticket_list_columns_json=columns,
    )
    user.task_panel_enabled = True
    session.commit()
    current_user = _current_user(effective_user_id=user.id, real_user_id=user.id)
    writer = users_router.create_my_appearance_session(db=session, current_user=current_user).writer_id

    updated = users_router.update_my_preferences(
        UserPreferencesUpdate(theme_preference="dark", appearance_write={"writer_id": writer, "sequence": 1}),
        db=session,
        current_user=current_user,
    )

    session.refresh(user)
    assert updated.theme_preference == "dark"
    assert updated.send_by_enter is True
    assert updated.color_enabled is False
    assert updated.task_panel_enabled is True
    assert [column.model_dump() for column in updated.ticket_list_columns] == columns
    assert user.theme_preference == "dark"
    assert user.send_by_enter is True
    assert user.color_enabled is False
    assert user.task_panel_enabled is True
    assert user.ticket_list_columns_json == columns


def test_user_preferences_patch_color_true_after_false_persists():
    session = _session()
    company = _company(session)
    user = _user(session, company_id=company.id, full_name="Ivan Petrov", color_enabled=False)
    current_user = _current_user(effective_user_id=user.id, real_user_id=user.id)

    updated = users_router.update_my_preferences(
        UserPreferencesUpdate(color_enabled=True),
        db=session,
        current_user=current_user,
    )

    session.refresh(user)
    assert updated.color_enabled is True
    assert user.color_enabled is True


def test_user_preferences_patch_send_by_enter_does_not_change_color_enabled():
    session = _session()
    company = _company(session)
    user = _user(session, company_id=company.id, full_name="Ivan Petrov", color_enabled=False)
    current_user = _current_user(effective_user_id=user.id, real_user_id=user.id)

    updated = users_router.update_my_preferences(
        UserPreferencesUpdate(send_by_enter=True),
        db=session,
        current_user=current_user,
    )

    session.refresh(user)
    assert updated.send_by_enter is True
    assert updated.color_enabled is False
    assert user.send_by_enter is True
    assert user.color_enabled is False


def test_user_preferences_patch_ticket_list_columns_persists_without_changing_other_preferences():
    session = _session()
    company = _company(session)
    user = _user(session, company_id=company.id, full_name="Ivan Petrov", send_by_enter=True, color_enabled=False)
    current_user = _current_user(effective_user_id=user.id, real_user_id=user.id)
    columns = [
        {"key": "requester", "width": 150},
        {"key": "subject", "width": 0},
        {"key": "age", "width": 80},
    ]

    updated = users_router.update_my_preferences(
        UserPreferencesUpdate(ticket_list_columns=columns),
        db=session,
        current_user=current_user,
    )

    session.refresh(user)
    assert updated.send_by_enter is True
    assert updated.color_enabled is False
    assert [column.model_dump() for column in updated.ticket_list_columns] == columns
    assert user.ticket_list_columns_json == columns


def test_user_preferences_ticket_list_columns_allow_optional_profile_and_type_columns():
    columns = [
        {"key": "subject", "width": 0},
        {"key": "department", "width": 160},
        {"key": "type", "width": 130},
        {"key": "location", "width": 130},
    ]

    payload = UserPreferencesUpdate(ticket_list_columns=columns)

    assert [column.model_dump() for column in payload.ticket_list_columns] == columns


def test_user_preferences_patch_other_fields_does_not_change_ticket_list_columns():
    session = _session()
    company = _company(session)
    columns = [{"key": "subject", "width": 0}, {"key": "created_at", "width": 140}]
    user = _user(session, company_id=company.id, full_name="Ivan Petrov", ticket_list_columns_json=columns)
    current_user = _current_user(effective_user_id=user.id, real_user_id=user.id)

    updated = users_router.update_my_preferences(
        UserPreferencesUpdate(color_enabled=False),
        db=session,
        current_user=current_user,
    )

    session.refresh(user)
    assert updated.color_enabled is False
    assert [column.model_dump() for column in updated.ticket_list_columns] == columns
    assert user.ticket_list_columns_json == columns


def test_user_preferences_ticket_list_columns_reject_invalid_configs():
    invalid_payloads = [
        [{"key": "unknown", "width": 100}, {"key": "subject", "width": 0}],
        [{"key": "subject", "width": 0}, {"key": "subject", "width": 100}],
        [{"key": "requester", "width": 150}],
        [{"key": "subject", "width": -1}],
        [{"key": "subject", "width": 1}],
        [{"key": "subject", "width": 59}],
        [],
    ]

    for payload in invalid_payloads:
        with pytest.raises((ValidationError, ValueError)):
            UserPreferencesUpdate(ticket_list_columns=payload)


def test_user_preferences_ticket_list_columns_allow_width_zero():
    payload = UserPreferencesUpdate(ticket_list_columns=[{"key": "subject", "width": 0}])

    assert payload.ticket_list_columns[0].width == 0


def test_user_preferences_ticket_list_columns_allow_large_width():
    payload = UserPreferencesUpdate(ticket_list_columns=[{"key": "subject", "width": 1600}])

    assert payload.ticket_list_columns[0].width == 1600


def test_user_preferences_are_isolated_between_users():
    session = _session()
    company = _company(session)
    user_a = _user(session, company_id=company.id, full_name="User A")
    user_b = _user(session, company_id=company.id, full_name="User B")
    actor = _current_user(effective_user_id=user_a.id, real_user_id=user_a.id)
    writer = users_router.create_my_appearance_session(db=session, current_user=actor).writer_id

    users_router.update_my_preferences(
        UserPreferencesUpdate(send_by_enter=True, color_enabled=False, theme_preference="dark",
                              appearance_write={"writer_id": writer, "sequence": 1}),
        db=session,
        current_user=_current_user(effective_user_id=user_a.id, real_user_id=user_a.id),
    )

    session.refresh(user_a)
    session.refresh(user_b)
    assert user_a.send_by_enter is True
    assert user_a.color_enabled is False
    assert user_a.theme_preference == "dark"
    assert user_a.ticket_list_columns_json is None
    assert user_b.send_by_enter is False
    assert user_b.color_enabled is True
    assert user_b.theme_preference == "light"
    assert user_b.ticket_list_columns_json is None

    users_router.update_my_preferences(
        UserPreferencesUpdate(ticket_list_columns=[{"key": "subject", "width": 0}, {"key": "last_reply", "width": 150}]),
        db=session,
        current_user=_current_user(effective_user_id=user_a.id, real_user_id=user_a.id),
    )
    session.refresh(user_a)
    session.refresh(user_b)
    assert user_a.ticket_list_columns_json == [{"key": "subject", "width": 0}, {"key": "last_reply", "width": 150}]
    assert user_b.ticket_list_columns_json is None


def test_user_preferences_use_real_user_id_not_effective_user_id():
    session = _session()
    company = _company(session)
    real_user = _user(session, company_id=company.id, full_name="Real Admin", role="admin")
    effective_user = _user(session, company_id=company.id, full_name="Effective Agent", role="agent")

    users_router.update_my_preferences(
        UserPreferencesUpdate(send_by_enter=True, color_enabled=False),
        db=session,
        current_user=_current_user(
            effective_user_id=effective_user.id,
            real_user_id=real_user.id,
            role=effective_user.role,
        ),
    )

    session.refresh(real_user)
    session.refresh(effective_user)
    assert real_user.send_by_enter is True
    assert real_user.color_enabled is False
    assert effective_user.send_by_enter is False
    assert effective_user.color_enabled is True

    users_router.update_my_preferences(
        UserPreferencesUpdate(ticket_list_columns=[{"key": "subject", "width": 0}, {"key": "requester", "width": 150}]),
        db=session,
        current_user=_current_user(
            effective_user_id=effective_user.id,
            real_user_id=real_user.id,
            role=effective_user.role,
        ),
    )
    session.refresh(real_user)
    session.refresh(effective_user)
    assert real_user.ticket_list_columns_json == [{"key": "subject", "width": 0}, {"key": "requester", "width": 150}]
    assert effective_user.ticket_list_columns_json is None


@pytest.mark.parametrize("field", COLOR_FIELDS)
@pytest.mark.parametrize("value", [-1, 361, None, True, 1.5])
def test_color_preferences_reject_invalid_values(field, value):
    with pytest.raises(ValidationError):
        UserPreferencesUpdate(**{field: value})


def test_color_preferences_http_roundtrip_and_isolation(appearance_context, preferences_client):
    factory, actor, writer, other_id = appearance_context
    fields = dict(zip(COLOR_FIELDS, [0, 0, 360, 100, 360, 100, 0, 0]))
    response = preferences_client.patch("/api/v1/users/me/preferences", json={
        **fields, "appearance_write": {"writer_id": writer, "sequence": 2},
    })
    assert response.status_code == 200
    loaded = preferences_client.get("/api/v1/users/me/preferences").json()
    with factory() as db:
        user, other = db.get(User, actor.real_user_id), db.get(User, other_id)
        for field, value in fields.items():
            assert response.json()[field] == loaded[field] == getattr(user, field) == value
            assert getattr(other, field) is None
        assert user.send_by_enter is False
        assert user.color_enabled is True
    stale = preferences_client.patch("/api/v1/users/me/preferences", json={
        **dict.fromkeys(COLOR_FIELDS, 17), "send_by_enter": True,
        "appearance_write": {"writer_id": writer, "sequence": 1},
    })
    assert stale.status_code == 200
    assert stale.json()["send_by_enter"] is True
    for field, value in fields.items():
        assert stale.json()[field] == value


def test_first_appearance_is_saved_by_registration_before_any_patch(appearance_context, preferences_client):
    factory, actor, _, other_id = appearance_context
    response = preferences_client.post("/api/v1/users/me/preferences/appearance-session", json={
        "preferences": {"accent_strength_light": 99}, "sequence": 2,
        "expected_revision": preferences_client.get("/api/v1/users/me/preferences").json()["appearance_revision"],
    })
    assert response.status_code == 200
    writer = response.json()["writer_id"]
    assert response.json()["preferences"]["accent_strength_light"] == 99
    with factory() as db:
        user = db.get(User, actor.real_user_id)
        assert user.accent_strength_light == 99
        assert user.appearance_write_state_json[writer]["sequence"] == 2
        assert db.get(User, other_id).accent_strength_light is None
    for sequence in (1, 2):
        assert _appearance_patch(factory, actor, writer, sequence, accent_strength_light=17).accent_strength_light == 99
    assert _appearance_patch(factory, actor, writer, 3, accent_strength_light=42).accent_strength_light == 42


@pytest.mark.parametrize("preferences", [{}, {"send_by_enter": True}, {"appearance_write": None},
                                         {"accent_strength_light": None}, {"accent_strength_light": 101}])
def test_initial_appearance_rejects_unrelated_or_invalid_preferences(appearance_context, preferences_client, preferences):
    factory, actor, writer, _ = appearance_context
    response = preferences_client.post("/api/v1/users/me/preferences/appearance-session", json={
        "preferences": preferences, "sequence": 1,
        "expected_revision": preferences_client.get("/api/v1/users/me/preferences").json()["appearance_revision"],
    })
    assert response.status_code == 422
    with factory() as db:
        user = db.get(User, actor.real_user_id)
        assert user.send_by_enter is False
        assert user.accent_strength_light is None
        assert list(user.appearance_write_state_json) == [writer]


def test_initial_appearance_and_sequence_roll_back_together(appearance_context, monkeypatch):
    factory, actor, writer, _ = appearance_context
    with factory() as db:
        def fail_commit():
            db.flush()
            raise RuntimeError("commit failed")
        monkeypatch.setattr(db, "commit", fail_commit)
        with pytest.raises(RuntimeError, match="commit failed"):
            user = db.get(User, actor.real_user_id)
            users_router.register_appearance_session(db, user, {"accent_strength_light": 99}, 2,
                                                    users_router.appearance_revision(user))
        db.rollback()
    with factory() as db:
        user = db.get(User, actor.real_user_id)
        assert user.accent_strength_light is None
        assert user.appearance_write_state_json[writer]["sequence"] == 0
        assert len(user.appearance_write_state_json) == 1


@pytest.mark.parametrize("reverse", [False, True])
def test_delayed_initial_registration_cannot_overwrite_retry(appearance_context, preferences_client, reverse):
    factory, actor, _, _ = appearance_context
    revision = preferences_client.get("/api/v1/users/me/preferences").json()["appearance_revision"]
    def register(value, sequence):
        response = preferences_client.post("/api/v1/users/me/preferences/appearance-session", json={
            "preferences": {"accent_strength_light": value}, "sequence": sequence, "expected_revision": revision,
        })
        assert response.status_code == 200
        return response.json()
    if reverse:
        latest = register(99, 2)
        register(17, 1)  # The failed request may still finish on the server after its retry.
    else:
        register(17, 1)
        latest = register(99, 2)
        assert latest["preferences"]["accent_strength_light"] == 17
    # An acknowledged fresh writer uses the unchanged PATCH queue and sequence protocol.
    _appearance_patch(factory, actor, latest["writer_id"], 2, accent_strength_light=99)
    with factory() as db:
        assert db.get(User, actor.real_user_id).accent_strength_light == 99


def test_client_card_collapse_http_persistence_and_user_isolation(appearance_context, preferences_client):
    factory, actor, _, other_id = appearance_context
    assert preferences_client.get("/api/v1/users/me/preferences").json()["client_card_collapsed_blocks"] == {}
    for patch in ({"tg": True}, {"dynamic-key": True}, {"tg": False}):
        response = preferences_client.patch("/api/v1/users/me/preferences", json={"client_card_collapsed_blocks": patch})
        assert response.status_code == 200
    preferences_client.patch("/api/v1/users/me/preferences", json={"send_by_enter": True})
    loaded = preferences_client.get("/api/v1/users/me/preferences").json()
    assert loaded["client_card_collapsed_blocks"] == {"tg": False, "dynamic-key": True}
    with factory() as db:
        assert db.get(User, actor.real_user_id).client_card_collapsed_blocks_json == loaded["client_card_collapsed_blocks"]
        assert db.get(User, other_id).client_card_collapsed_blocks_json is None
        other = _current_user(effective_user_id=other_id, real_user_id=other_id)
        assert users_router.get_my_preferences(db=db, current_user=other).client_card_collapsed_blocks == {}
        users_router.update_my_preferences(UserPreferencesUpdate(client_card_collapsed_blocks={"tg": True}), db=db, current_user=other)
    assert preferences_client.get("/api/v1/users/me/preferences").json()["client_card_collapsed_blocks"] == loaded["client_card_collapsed_blocks"]


@pytest.mark.parametrize("value", [None, [], {"tg": "false"}, {"tg": 1}])
def test_client_card_collapse_rejects_invalid_preferences(value):
    with pytest.raises(ValidationError):
        UserPreferencesUpdate(client_card_collapsed_blocks=value)


def test_client_card_collapse_uses_real_user_preferences():
    db = _session()
    company = _company(db)
    real = _user(db, company_id=company.id, full_name="Real", role="admin")
    effective = _user(db, company_id=company.id, full_name="Effective")
    users_router.update_my_preferences(
        UserPreferencesUpdate(client_card_collapsed_blocks={"profile": True}), db=db,
        current_user=_current_user(effective_user_id=effective.id, real_user_id=real.id),
    )
    db.refresh(effective)
    assert real.client_card_collapsed_blocks_json == {"profile": True}
    assert effective.client_card_collapsed_blocks_json is None


def test_client_card_collapse_schema_upgrade_preserves_saved_state(monkeypatch):
    from sqlalchemy import inspect, text
    from api.app.services import schema_evolution

    engine = create_engine("sqlite:///:memory:", future=True)
    monkeypatch.setattr(schema_evolution, "engine", engine)
    with engine.begin() as conn:
        conn.execute(text("CREATE TABLE users (id VARCHAR(36) PRIMARY KEY, email VARCHAR(255))"))
        conn.execute(text("INSERT INTO users (id, email) VALUES ('existing', 'existing@example.test')"))
    schema_evolution.ensure_user_columns()
    assert "client_card_collapsed_blocks_json" in {c["name"] for c in inspect(engine).get_columns("users")}
    with engine.begin() as conn:
        assert conn.execute(text("SELECT client_card_collapsed_blocks_json FROM users")).scalar_one() is None
        conn.execute(text("UPDATE users SET client_card_collapsed_blocks_json = :value"), {"value": '{"tg": true}'})
    schema_evolution.ensure_user_columns()
    with engine.connect() as conn:
        assert conn.execute(text("SELECT client_card_collapsed_blocks_json FROM users")).scalar_one() == '{"tg": true}'
