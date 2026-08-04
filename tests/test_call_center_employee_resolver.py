from __future__ import annotations

from datetime import datetime, timedelta
from types import SimpleNamespace
import uuid

import pytest
from fastapi import HTTPException
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker

from api.app.db import Base
from api.app.models import CallCenterEmployeeResolverState, ClientCardRuleConfig, ClientCardSourceConfig
from api.app.routers import settings_client_card
from api.app.schemas import ClientCardSourceCreate, ClientCardSourceUpdate
from api.app.services import bitrix_client, call_center_employee_resolver as resolver, seed_data
from api.app.services import client_card_sources
from api.app.services.client_card_config_transfer import export_client_card_config, import_client_card_config


def _session():
    engine = create_engine("sqlite:///:memory:", future=True)
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine, autoflush=False, autocommit=False)
    resolver.SessionLocal = Session
    return Session()


def _admin():
    return SimpleNamespace(id="admin-1", role="admin")


def _system_source() -> ClientCardSourceConfig:
    now = datetime.utcnow()
    return ClientCardSourceConfig(
        id=str(uuid.uuid4()),
        technical_key=resolver.SYSTEM_SOURCE_KEY,
        name="System",
        kind="system",
        is_active=True,
        method="POST",
        url_template="system://call-center-employee-resolver",
        timeout_ms=5000,
        mapping_json=client_card_sources.ensure_system_source_mapping_json(None),
        on_error="ignore",
        created_at=now,
        updated_at=now,
    )


def _state(
    db,
    *,
    fetched_at: datetime,
    employees: dict[str, dict] | None = None,
    ambiguous: list[str] | None = None,
    metadata: dict | None = None,
):
    db.add(
        CallCenterEmployeeResolverState(
            key=resolver.STATE_KEY,
            snapshot_json={
                "fetched_at": fetched_at.isoformat(),
                "employees_by_number": employees or {},
                "ambiguous_internal_numbers": ambiguous or [],
            },
            metadata_json=metadata or {},
            fetched_at=fetched_at,
            last_success_at=fetched_at,
            updated_at=fetched_at,
        )
    )
    db.commit()


def _employee(bitrix_id: str = "10", internal_number: str = "700") -> dict:
    return {
        "internal_number": internal_number,
        "bitrix_id": bitrix_id,
        "first_name": "Ivan",
        "last_name": "Test",
        "full_name": "Test Ivan",
        "department_ids": [60],
    }


def _finished_event() -> SimpleNamespace:
    return SimpleNamespace(wait=lambda timeout: None, is_set=lambda: True)


def _timed_out_event() -> SimpleNamespace:
    return SimpleNamespace(wait=lambda timeout: None, is_set=lambda: False)


def test_settings_keep_two_bitrix_webhooks_independent(monkeypatch):
    monkeypatch.setattr(resolver.settings, "bitrix_kc_webhook_base", "https://kc.example.test/rest/")
    monkeypatch.setattr(bitrix_client.settings, "bitrix_webhook_base", "https://old.example.test/rest/")

    assert resolver.configured() is True
    assert bitrix_client._webhook_base() == "https://old.example.test/rest/"


def test_parse_bitrix_department_response_filters_empty_and_ambiguous_numbers():
    snapshot, counts = resolver._build_snapshot(
        {
            "result": {
                "60": [
                    {
                        "id": 10,
                        "name": "Ivan Test",
                        "first_name": "Ivan",
                        "last_name": "Test",
                        "departments": [60],
                        "phones": {"inner_phone": "700"},
                    },
                    {"id": 11, "first_name": "No", "phones": {"inner_phone": ""}},
                    {"id": 12, "first_name": "Dupe", "phones": {"inner_phone": "701"}},
                    {"id": 13, "first_name": "Dupe2", "phones": {"inner_phone": "701"}},
                ]
            }
        },
        [60],
    )

    assert snapshot["department_ids"] == [60]
    assert set(snapshot["employees_by_number"]) == {"700"}
    assert snapshot["ambiguous_internal_numbers"] == ["701"]
    assert counts == {"total": 4, "assigned": 3, "ambiguous": 1}


def test_resolve_uses_fresh_snapshot_without_external_refresh(monkeypatch):
    db = _session()
    fetched_at = datetime.utcnow()
    db.add(
        CallCenterEmployeeResolverState(
            key=resolver.STATE_KEY,
            snapshot_json={
                "fetched_at": fetched_at.isoformat(),
                "employees_by_number": {
                    "700": {
                        "internal_number": "700",
                        "bitrix_id": "10",
                        "first_name": "Ivan",
                        "last_name": "Test",
                        "full_name": "Test Ivan",
                        "department_ids": [60],
                    }
                },
                "ambiguous_internal_numbers": [],
            },
            metadata_json={},
            fetched_at=fetched_at,
            last_success_at=fetched_at,
            updated_at=fetched_at,
        )
    )
    db.commit()
    monkeypatch.setattr(resolver.settings, "bitrix_kc_webhook_base", "https://kc.example.test/rest/")
    monkeypatch.setattr(resolver, "_ensure_refresh_started", lambda force: pytest.fail("refresh must not start"))

    result = resolver.resolve(db, "700")

    assert result.status == resolver.STATUS_FOUND
    assert result.employee.bitrix_id == "10"


def test_resolve_fresh_missing_starts_one_refresh_and_waits(monkeypatch):
    db = _session()
    fetched_at = datetime.utcnow()
    db.add(
        CallCenterEmployeeResolverState(
            key=resolver.STATE_KEY,
            snapshot_json={"fetched_at": fetched_at.isoformat(), "employees_by_number": {}, "ambiguous_internal_numbers": []},
            metadata_json={},
            fetched_at=fetched_at,
            last_success_at=fetched_at,
            updated_at=fetched_at,
        )
    )
    db.commit()
    starts = []

    class Event:
        def wait(self, timeout):
            starts.append(("wait", timeout))
            now = datetime.utcnow()
            with resolver.SessionLocal() as other:
                state = other.get(CallCenterEmployeeResolverState, resolver.STATE_KEY)
                state.snapshot_json = {
                    "fetched_at": now.isoformat(),
                    "employees_by_number": {
                        "700": {
                            "internal_number": "700",
                            "bitrix_id": "77",
                            "first_name": "Anna",
                            "last_name": "Test",
                            "full_name": "Test Anna",
                            "department_ids": [60],
                        }
                    },
                    "ambiguous_internal_numbers": [],
                }
                state.metadata_json = {"last_error_code": None}
                state.fetched_at = now
                state.last_success_at = now
                other.commit()

        def is_set(self):
            return True

    monkeypatch.setattr(resolver.settings, "bitrix_kc_webhook_base", "https://kc.example.test/rest/")
    monkeypatch.setattr(resolver.settings, "bitrix_kc_final_wait_seconds", 0.25)
    monkeypatch.setattr(resolver, "_ensure_refresh_started", lambda force: starts.append(("start", force)) or Event())

    result = resolver.resolve(db, "700")

    assert result.status == resolver.STATUS_FOUND
    assert result.employee.bitrix_id == "77"
    assert starts == [("start", True), ("wait", 0.25)]


def test_resolve_fresh_ambiguous_returns_without_refresh(monkeypatch):
    db = _session()
    fetched_at = datetime.utcnow()
    db.add(
        CallCenterEmployeeResolverState(
            key=resolver.STATE_KEY,
            snapshot_json={"fetched_at": fetched_at.isoformat(), "employees_by_number": {}, "ambiguous_internal_numbers": ["700"]},
            metadata_json={},
            fetched_at=fetched_at,
            last_success_at=fetched_at,
            updated_at=fetched_at,
        )
    )
    db.commit()
    monkeypatch.setattr(resolver.settings, "bitrix_kc_webhook_base", "https://kc.example.test/rest/")
    monkeypatch.setattr(resolver, "_ensure_refresh_started", lambda force: pytest.fail("refresh must not start"))

    assert resolver.resolve(db, "700").status == resolver.STATUS_AMBIGUOUS


def test_resolve_reads_state_outside_request_session_identity_map(monkeypatch):
    db_a = _session()
    fetched_at = datetime.utcnow()
    db_a.add(
        CallCenterEmployeeResolverState(
            key=resolver.STATE_KEY,
            snapshot_json={"fetched_at": fetched_at.isoformat(), "employees_by_number": {}, "ambiguous_internal_numbers": []},
            metadata_json={},
            fetched_at=fetched_at,
            last_success_at=fetched_at,
            updated_at=fetched_at,
        )
    )
    db_a.commit()
    assert db_a.get(CallCenterEmployeeResolverState, resolver.STATE_KEY).snapshot_json["employees_by_number"] == {}
    with resolver.SessionLocal() as db_b:
        state = db_b.get(CallCenterEmployeeResolverState, resolver.STATE_KEY)
        state.snapshot_json = {
            "fetched_at": fetched_at.isoformat(),
            "employees_by_number": {
                "700": {
                    "internal_number": "700",
                    "bitrix_id": "99",
                    "first_name": "Fresh",
                    "last_name": "Worker",
                    "full_name": "Worker Fresh",
                    "department_ids": [60],
                }
            },
            "ambiguous_internal_numbers": [],
        }
        db_b.commit()
    monkeypatch.setattr(resolver.settings, "bitrix_kc_webhook_base", "https://kc.example.test/rest/")
    monkeypatch.setattr(resolver, "_ensure_refresh_started", lambda force: pytest.fail("refresh must not start"))

    result = resolver.resolve(db_a, "700")

    assert result.status == resolver.STATUS_FOUND
    assert result.employee.bitrix_id == "99"


def test_not_configured_returns_empty_status(monkeypatch):
    db = _session()
    monkeypatch.setattr(resolver.settings, "bitrix_kc_webhook_base", None)

    result = resolver.resolve(db, "700")

    assert result.status == resolver.STATUS_NOT_CONFIGURED
    assert result.employee is None


def test_stale_fallback_is_used_only_within_max_age(monkeypatch):
    db = _session()
    fetched_at = datetime.utcnow() - timedelta(hours=2)
    db.add(
        CallCenterEmployeeResolverState(
            key=resolver.STATE_KEY,
            snapshot_json={
                "fetched_at": fetched_at.isoformat(),
                "employees_by_number": {
                    "700": {
                        "internal_number": "700",
                        "bitrix_id": "10",
                        "first_name": "Ivan",
                        "last_name": "Test",
                        "full_name": "Test Ivan",
                        "department_ids": [60],
                    }
                },
                "ambiguous_internal_numbers": [],
            },
            metadata_json={"last_error_code": "RuntimeError"},
            fetched_at=fetched_at,
            last_success_at=fetched_at,
            updated_at=fetched_at,
        )
    )
    db.commit()
    event = SimpleNamespace(wait=lambda timeout: None, is_set=lambda: True)
    monkeypatch.setattr(resolver.settings, "bitrix_kc_webhook_base", "https://kc.example.test/rest/")
    monkeypatch.setattr(resolver.settings, "bitrix_kc_refresh_interval_seconds", 3600)
    monkeypatch.setattr(resolver.settings, "bitrix_kc_max_stale_seconds", 28800)
    monkeypatch.setattr(resolver, "_ensure_refresh_started", lambda force: event)

    result = resolver.resolve(db, "700")

    assert result.status == resolver.STATUS_STALE_FALLBACK
    assert result.used_stale_fallback is True


def test_fresh_missing_network_failure_returns_technical_error_without_stale_not_found(monkeypatch):
    db = _session()
    fetched_at = datetime.utcnow()
    _state(db, fetched_at=fetched_at, employees={}, metadata={"last_success_refresh_id": "before"})
    monkeypatch.setattr(resolver.settings, "bitrix_kc_webhook_base", "https://kc.example.test/rest/")
    monkeypatch.setattr(resolver, "_ensure_refresh_started", lambda force: _finished_event())
    monkeypatch.setattr(resolver, "_refresh_id_for_event", lambda event: "refresh-1")

    with resolver.SessionLocal() as other:
        state = other.get(CallCenterEmployeeResolverState, resolver.STATE_KEY)
        state.metadata_json = {
            "last_attempt_refresh_id": "refresh-1",
            "last_error_refresh_id": "refresh-1",
            "last_error_code": resolver.ERROR_NETWORK,
        }
        other.commit()

    result = resolver.resolve(db, "700")

    assert result.status == resolver.STATUS_TECHNICAL_ERROR
    assert result.error_code == resolver.ERROR_NETWORK


def test_fresh_missing_final_wait_timeout_returns_refresh_timeout(monkeypatch):
    db = _session()
    _state(db, fetched_at=datetime.utcnow(), employees={})
    monkeypatch.setattr(resolver.settings, "bitrix_kc_webhook_base", "https://kc.example.test/rest/")
    monkeypatch.setattr(resolver, "_ensure_refresh_started", lambda force: _timed_out_event())
    monkeypatch.setattr(resolver, "_refresh_id_for_event", lambda event: "refresh-1")

    result = resolver.resolve(db, "700", final_wait_seconds=0)

    assert result.status == resolver.STATUS_REFRESH_TIMEOUT


def test_timeout_uses_stale_found_fallback_but_not_stale_not_found_or_ambiguous(monkeypatch):
    fetched_at = datetime.utcnow() - timedelta(hours=2)
    monkeypatch.setattr(resolver.settings, "bitrix_kc_webhook_base", "https://kc.example.test/rest/")
    monkeypatch.setattr(resolver.settings, "bitrix_kc_refresh_interval_seconds", 3600)
    monkeypatch.setattr(resolver.settings, "bitrix_kc_max_stale_seconds", 28800)
    monkeypatch.setattr(resolver, "_ensure_refresh_started", lambda force: _timed_out_event())
    monkeypatch.setattr(resolver, "_refresh_id_for_event", lambda event: "refresh-1")

    found_db = _session()
    _state(found_db, fetched_at=fetched_at, employees={"700": _employee()})
    assert resolver.resolve(found_db, "700", final_wait_seconds=0).status == resolver.STATUS_STALE_FALLBACK

    missing_db = _session()
    _state(missing_db, fetched_at=fetched_at, employees={})
    assert resolver.resolve(missing_db, "700", final_wait_seconds=0).status == resolver.STATUS_REFRESH_TIMEOUT

    ambiguous_db = _session()
    _state(ambiguous_db, fetched_at=fetched_at, employees={}, ambiguous=["700"])
    assert resolver.resolve(ambiguous_db, "700", final_wait_seconds=0).status == resolver.STATUS_REFRESH_TIMEOUT


def test_successful_new_response_missing_forbids_stale_fallback(monkeypatch):
    db = _session()
    old_at = datetime.utcnow() - timedelta(hours=2)
    _state(db, fetched_at=old_at, employees={"700": _employee()}, metadata={"last_success_refresh_id": "old"})
    now = datetime.utcnow()
    with resolver.SessionLocal() as other:
        state = other.get(CallCenterEmployeeResolverState, resolver.STATE_KEY)
        state.snapshot_json = {"fetched_at": now.isoformat(), "refresh_id": "refresh-1", "employees_by_number": {}, "ambiguous_internal_numbers": []}
        state.metadata_json = {"last_attempt_refresh_id": "refresh-1", "last_success_refresh_id": "refresh-1", "last_error_code": None}
        state.fetched_at = now
        state.last_success_at = now
        other.commit()
    monkeypatch.setattr(resolver.settings, "bitrix_kc_webhook_base", "https://kc.example.test/rest/")
    monkeypatch.setattr(resolver.settings, "bitrix_kc_refresh_interval_seconds", 3600)
    monkeypatch.setattr(resolver.settings, "bitrix_kc_max_stale_seconds", 28800)
    monkeypatch.setattr(resolver, "_ensure_refresh_started", lambda force: _finished_event())
    monkeypatch.setattr(resolver, "_refresh_id_for_event", lambda event: "refresh-1")

    result = resolver.resolve(db, "700")

    assert result.status == resolver.STATUS_NOT_FOUND
    assert result.used_stale_fallback is False


def test_refresh_retries_only_retryable_errors_and_stores_typed_code(monkeypatch):
    db = _session()
    monkeypatch.setattr(resolver.settings, "bitrix_kc_webhook_base", "https://kc.example.test/rest/")
    monkeypatch.setattr(resolver.settings, "bitrix_kc_retry_count", 3)
    calls = []

    def fake_call(_deps):
        calls.append(1)
        raise resolver.CallCenterEmployeeResolverError(resolver.ERROR_INVALID_PAYLOAD, "bad payload", retryable=False)

    monkeypatch.setattr(resolver, "_call_bitrix", fake_call)

    resolver._refresh_once(force=True)

    with resolver.SessionLocal() as check:
        state = check.get(CallCenterEmployeeResolverState, resolver.STATE_KEY)
        assert state.metadata_json["last_error_code"] == resolver.ERROR_INVALID_PAYLOAD
    assert len(calls) == 1


def test_refresh_retries_retryable_network_error_then_succeeds(monkeypatch):
    db = _session()
    monkeypatch.setattr(resolver.settings, "bitrix_kc_webhook_base", "https://kc.example.test/rest/")
    monkeypatch.setattr(resolver.settings, "bitrix_kc_retry_count", 2)
    calls = []

    def fake_call(_deps):
        calls.append(1)
        if len(calls) == 1:
            raise resolver.CallCenterEmployeeResolverError(resolver.ERROR_NETWORK, "network", retryable=True)
        return {"result": {"60": [{"id": 10, "first_name": "Anna", "phones": {"inner_phone": "700"}}]}}

    monkeypatch.setattr(resolver, "_call_bitrix", fake_call)

    resolver._refresh_once(force=True)

    with resolver.SessionLocal() as check:
        state = check.get(CallCenterEmployeeResolverState, resolver.STATE_KEY)
        assert state.metadata_json["last_error_code"] is None
    assert state.metadata_json["assigned_employee_count"] == 1
    assert len(calls) == 2


def test_invalid_result_payload_does_not_replace_previous_snapshot(monkeypatch):
    db = _session()
    fetched_at = datetime.utcnow() - timedelta(minutes=10)
    _state(db, fetched_at=fetched_at, employees={"700": _employee()}, metadata={"last_success_refresh_id": "old"})
    monkeypatch.setattr(resolver.settings, "bitrix_kc_webhook_base", "https://kc.example.test/rest/")
    monkeypatch.setattr(resolver.settings, "bitrix_kc_retry_count", 3)
    monkeypatch.setattr(resolver, "_call_bitrix", lambda _deps: {"result": None})

    resolver._refresh_once(force=True, refresh_id="bad")

    with resolver.SessionLocal() as check:
        state = check.get(CallCenterEmployeeResolverState, resolver.STATE_KEY)
        assert state.snapshot_json["employees_by_number"]["700"]["bitrix_id"] == "10"
        assert state.metadata_json["last_error_code"] == resolver.ERROR_INVALID_PAYLOAD
        assert state.metadata_json["last_error_refresh_id"] == "bad"


def test_empty_bitrix_result_list_is_valid_successful_snapshot(monkeypatch):
    db = _session()
    monkeypatch.setattr(resolver.settings, "bitrix_kc_webhook_base", "https://kc.example.test/rest/")
    monkeypatch.setattr(resolver, "_call_bitrix", lambda _deps: {"result": []})

    resolver._refresh_once(force=True, refresh_id="empty")

    with resolver.SessionLocal() as check:
        state = check.get(CallCenterEmployeeResolverState, resolver.STATE_KEY)
        assert state.snapshot_json["employees_by_number"] == {}
        assert state.metadata_json["last_success_refresh_id"] == "empty"
        assert state.metadata_json["last_error_code"] is None


def test_duplicate_same_bitrix_id_is_not_ambiguous_but_different_id_is():
    snapshot, counts = resolver._build_snapshot(
        {
            "result": [
                {"id": 10, "first_name": "A", "phones": {"inner_phone": "700"}},
                {"id": 10, "first_name": "A duplicate", "phones": {"inner_phone": "700"}},
                {"id": 11, "first_name": "B", "phones": {"inner_phone": "701"}},
                {"id": 12, "first_name": "C", "phones": {"inner_phone": "701"}},
                {"id": 13, "first_name": "D", "phones": {"inner_phone": "702"}},
            ]
        },
        [60],
    )

    assert set(snapshot["employees_by_number"]) == {"700", "702"}
    assert snapshot["ambiguous_internal_numbers"] == ["701"]
    assert counts == {"total": 5, "assigned": 4, "ambiguous": 1}


def test_storage_failure_sets_event_and_allows_stale_found_fallback(monkeypatch):
    db = _session()
    fetched_at = datetime.utcnow() - timedelta(hours=2)
    _state(db, fetched_at=fetched_at, employees={"700": _employee()}, metadata={"last_success_refresh_id": "old"})
    monkeypatch.setattr(resolver.settings, "bitrix_kc_webhook_base", "https://kc.example.test/rest/")
    monkeypatch.setattr(resolver.settings, "bitrix_kc_refresh_interval_seconds", 3600)
    monkeypatch.setattr(resolver.settings, "bitrix_kc_max_stale_seconds", 28800)

    event = _finished_event()
    monkeypatch.setattr(resolver, "_ensure_refresh_started", lambda force: event)
    monkeypatch.setattr(resolver, "_refresh_id_for_event", lambda _event: "storage-failed")

    result = resolver.resolve(db, "700")

    assert result.status == resolver.STATUS_STALE_FALLBACK
    assert event.is_set() is True


def test_status_treats_zero_assigned_successful_snapshot_as_available(monkeypatch):
    db = _session()
    fetched_at = datetime.utcnow()
    db.add(
        CallCenterEmployeeResolverState(
            key=resolver.STATE_KEY,
            snapshot_json={"fetched_at": fetched_at.isoformat(), "employees_by_number": {}, "ambiguous_internal_numbers": []},
            metadata_json={"assigned_employee_count": 0, "total_employee_count": 3, "ambiguous_internal_number_count": 2},
            fetched_at=fetched_at,
            last_success_at=fetched_at,
            updated_at=fetched_at,
        )
    )
    db.commit()

    status = resolver.get_status(db)

    assert status["snapshot_available"] is True
    assert status["assigned_employee_count"] == 0
    assert status["ambiguous_internal_number_count"] == 2


def test_periodic_lifecycle_is_idempotent_stoppable_and_restartable(monkeypatch):
    _session()
    resolver.stop_periodic_refresh_loop(join_timeout=0.1)
    monkeypatch.setattr(resolver.settings, "bitrix_kc_webhook_base", None)
    monkeypatch.setattr(resolver, "_ensure_refresh_started", lambda force: pytest.fail("not configured must not start refresh"))

    first = resolver.start_periodic_refresh_loop()
    second = resolver.start_periodic_refresh_loop()
    assert second is first
    resolver.stop_periodic_refresh_loop(join_timeout=0.5)
    assert not first.is_alive()
    restarted = resolver.start_periodic_refresh_loop()
    assert restarted is not first
    resolver.stop_periodic_refresh_loop(join_timeout=0.5)
    assert not restarted.is_alive()


def test_system_source_execute_uses_resolver_payload(monkeypatch):
    source = _system_source()
    captured = []

    def fake_resolve(db, internal_number, final_wait_seconds=None):
        captured.append((db, internal_number))
        return resolver.ResolveResult(
            status=resolver.STATUS_FOUND,
            internal_number=internal_number,
            employee=resolver.CallCenterEmployee(
                internal_number=internal_number,
                bitrix_id="42",
                first_name="Anna",
                last_name="Ivanova",
                full_name="Ivanova Anna",
                department_ids=[60, 61],
                bitrix_login="a.ivanova",
            ),
        )

    monkeypatch.setattr(resolver, "resolve", fake_resolve)

    result = client_card_sources.execute_source(source, {"internal_number": "700"})

    assert captured == [(None, "700")]
    assert result.http_status == 200
    assert result.payload["data"] == {
        "first_name": "Anna",
        "last_name": "Ivanova",
        "id": "42",
        "login": "a.ivanova",
        "bitrix_id": "42",
        "full_name": "Ivanova Anna",
        "department_ids": [60, 61],
        "resolution_status": resolver.STATUS_FOUND,
        "used_stale_fallback": False,
    }


def test_system_source_declares_internal_number_dependency():
    assert client_card_sources.source_template_field_refs(_system_source()) == {"internal_number"}


def test_system_source_payload_omits_login_when_bitrix_login_missing(monkeypatch):
    source = _system_source()

    monkeypatch.setattr(
        resolver,
        "resolve",
        lambda db, internal_number, final_wait_seconds=None: resolver.ResolveResult(
            status=resolver.STATUS_FOUND,
            internal_number=internal_number,
            employee=resolver.CallCenterEmployee(
                internal_number=internal_number,
                bitrix_id="42",
                first_name="Anna",
                last_name="Ivanova",
                full_name="Ivanova Anna",
                department_ids=[60],
                bitrix_login=None,
            ),
        ),
    )

    result = client_card_sources.execute_source(source, {"internal_number": "700"})

    assert "login" not in result.payload["data"]


@pytest.mark.parametrize(
    "status",
    [
        resolver.STATUS_TECHNICAL_ERROR,
        resolver.STATUS_REFRESH_TIMEOUT,
        resolver.STATUS_NOT_CONFIGURED,
    ],
)
def test_system_source_technical_statuses_raise_safe_source_error(monkeypatch, status):
    source = _system_source()
    monkeypatch.setattr(
        resolver,
        "resolve",
        lambda db, internal_number, final_wait_seconds=None: resolver.ResolveResult(
            status=status,
            internal_number=internal_number,
            error_code="secret-backend-detail",
        ),
    )

    with pytest.raises(client_card_sources.ClientCardSourceError) as exc:
        client_card_sources.execute_source(source, {"internal_number": "700"})

    assert status in str(exc.value)
    assert "secret-backend-detail" not in str(exc.value)


@pytest.mark.parametrize(
    ("status", "internal_number"),
    [
        (resolver.STATUS_NOT_FOUND, "700"),
        (resolver.STATUS_AMBIGUOUS, "701"),
    ],
)
def test_system_source_success_statuses_return_explicit_empty_employee_payload(monkeypatch, status, internal_number):
    source = _system_source()
    monkeypatch.setattr(
        resolver,
        "resolve",
        lambda db, current_number, final_wait_seconds=None: resolver.ResolveResult(
            status=status,
            internal_number=current_number,
        ),
    )

    result = client_card_sources.execute_source(source, {"internal_number": internal_number})

    assert result.http_status == 200
    assert result.payload["data"] == {
        "first_name": "",
        "last_name": "",
        "id": "",
        "bitrix_id": "",
        "login": "",
        "full_name": "",
        "department_ids": [],
        "resolution_status": status,
        "used_stale_fallback": False,
    }


def test_system_source_wraps_resolver_exception_as_source_error(monkeypatch):
    source = _system_source()
    monkeypatch.setattr(resolver, "resolve", lambda *args, **kwargs: (_ for _ in ()).throw(ValueError("bad resolver")))

    with pytest.raises(client_card_sources.ClientCardSourceError) as exc:
        client_card_sources.execute_source(source, {"internal_number": "700"})

    assert "ValueError" in str(exc.value)
    assert "bad resolver" not in str(exc.value)


def test_seed_upserts_system_source_without_duplicates_and_preserves_existing_id(monkeypatch):
    db = _session()
    monkeypatch.setattr(seed_data, "SessionLocal", resolver.SessionLocal)

    seed_data.ensure_client_card_defaults()
    first = db.execute(select(ClientCardSourceConfig).where(ClientCardSourceConfig.technical_key == resolver.SYSTEM_SOURCE_KEY)).scalar_one()
    first_id = first.id
    first.mapping_json = [{"response_path": "data.bitrix_id", "target_key": "bitrix_id"}]
    db.commit()

    seed_data.ensure_client_card_defaults()
    seed_data.ensure_client_card_defaults()
    sources = db.execute(select(ClientCardSourceConfig).where(ClientCardSourceConfig.technical_key == resolver.SYSTEM_SOURCE_KEY)).scalars().all()

    assert len(sources) == 1
    assert sources[0].id == first_id
    assert sources[0].kind == "system"
    mapping_by_target = {item["target_key"]: item["response_path"] for item in sources[0].mapping_json}
    assert mapping_by_target["bitrix_id"] == "data.bitrix_id"
    assert mapping_by_target["contact_name"] == "data.first_name"
    assert mapping_by_target["last_name"] == "data.last_name"
    assert mapping_by_target["external_user_id"] == "data.id"
    assert mapping_by_target["bitrix_login"] == "data.login"
    assert len(mapping_by_target) == len(sources[0].mapping_json)


def test_config_export_preserves_mixed_rule_actions_for_system_source():
    db = _session()
    source = _system_source()
    rule = ClientCardRuleConfig(
        id=str(uuid.uuid4()),
        technical_key="mixed",
        name="Mixed",
        is_active=True,
        is_system=False,
        sort_order=10,
        condition_field_key="",
        condition_operator="always",
        actions_json=[
            {"action": "lookup_source", "scope": "save", "source_id": source.id},
            {"action": "show", "scope": "editor", "target_type": "field", "target_key": "contact_name"},
        ],
        created_at=datetime.utcnow(),
        updated_at=datetime.utcnow(),
    )
    db.add_all([source, rule])
    db.commit()

    exported = export_client_card_config(db)

    assert exported["sources"] == []
    assert exported["rules"][0]["actions"] == [
        {"action": "lookup_source", "scope": "save", "source_key": resolver.SYSTEM_SOURCE_KEY},
        {"action": "show", "scope": "editor", "target_type": "field", "target_key": "contact_name"},
    ]
    assert exported["warnings"] == []


def test_system_source_crud_and_config_transfer_are_protected():
    db = _session()
    source = _system_source()
    db.add(source)
    db.commit()

    with pytest.raises(HTTPException) as create_exc:
        settings_client_card.create_client_card_source(
            ClientCardSourceCreate(
                technical_key=resolver.SYSTEM_SOURCE_KEY,
                name="Duplicate",
                kind="http",
                method="GET",
                url_template="https://api.example.test/{{internal_number}}",
                mapping_json=[],
            ),
            db=db,
            user=_admin(),
        )
    assert create_exc.value.detail["error"]["code"] == "system_source_create_forbidden"

    with pytest.raises(HTTPException) as update_exc:
        settings_client_card.update_client_card_source(
            source.id,
            ClientCardSourceUpdate(url_template="https://api.example.test/changed"),
            db=db,
            user=_admin(),
        )
    assert update_exc.value.detail["error"]["code"] == "system_source_update_forbidden"

    with pytest.raises(HTTPException) as delete_exc:
        settings_client_card.delete_client_card_source(source.id, db=db, user=_admin())
    assert delete_exc.value.detail["error"]["code"] == "system_source_delete_forbidden"

    exported = export_client_card_config(db)
    assert exported["sources"] == []
    imported = import_client_card_config(db, {"format_version": exported["format_version"], "scope": exported["scope"], "sources": [], "rules": [], "blocks": [], "fields": []}, apply=True)
    assert imported["applied"] is True
    assert db.execute(select(ClientCardSourceConfig).where(ClientCardSourceConfig.technical_key == resolver.SYSTEM_SOURCE_KEY)).scalar_one().id == source.id
