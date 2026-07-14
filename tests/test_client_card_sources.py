from __future__ import annotations

from datetime import datetime
from types import SimpleNamespace
import json
import uuid
from urllib.parse import parse_qs, urlparse

import pytest
from fastapi import HTTPException
from sqlalchemy import create_engine, select, text
from sqlalchemy.orm import sessionmaker

from api.app.db import Base
from api.app.models import Client, ClientCardBlockConfig, ClientCardFieldConfig, ClientCardSourceConfig, ClientProfile
from api.app.routers import settings_client_card
from api.app.schemas import ClientCardSourceCreate, ClientCardSourceTestRequest, ClientCardSourceUpdate
from api.app.services import schema_evolution
from api.app.services import client_card_sources as sources
from api.app.services.client_card_sources import (
    ClientCardSourceConfigError,
    ClientCardSourceHttpError,
    MISSING,
    SourceHttpResponse,
)


def _session():
    engine = create_engine("sqlite:///:memory:", future=True)
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine, autoflush=False, autocommit=False)
    return Session()


def _admin():
    return SimpleNamespace(id="admin-1", role="admin")


def _block(key: str = "profile") -> ClientCardBlockConfig:
    now = datetime.utcnow()
    return ClientCardBlockConfig(
        id=str(uuid.uuid4()),
        key=key,
        title=key,
        color_scheme="gray",
        sort_order=10,
        is_active=True,
        created_at=now,
        updated_at=now,
    )


def _field(key: str, *, is_system: bool = True, field_type: str = "text") -> ClientCardFieldConfig:
    now = datetime.utcnow()
    return ClientCardFieldConfig(
        id=str(uuid.uuid4()),
        key=key,
        label=key,
        field_type=field_type,
        block_key="profile",
        sort_order=10,
        is_visible=True,
        is_visible_in_editor=not is_system,
        is_editable=not is_system,
        is_required=False,
        is_system=is_system,
        created_at=now,
        updated_at=now,
    )


def _seed_fields(session):
    session.add_all([_block(), _field("contact_name"), _field("last_name"), _field("internal_number"), _field("custom_note", is_system=False)])
    session.commit()


def _source(**overrides) -> ClientCardSourceConfig:
    now = datetime.utcnow()
    data = {
        "id": str(uuid.uuid4()),
        "technical_key": "lookup",
        "name": "Lookup",
        "kind": "http",
        "is_active": True,
        "method": "GET",
        "url_template": "https://api.example.com/users/{{internal_number}}",
        "query_template_json": {"q": "{{contact_name}}"},
        "headers_template_json": {"Accept": "application/json"},
        "body_template_json": None,
        "timeout_ms": 1000,
        "mapping_json": [{"response_path": "data.user.name", "target_key": "contact_name"}],
        "on_error": "ignore",
        "created_at": now,
        "updated_at": now,
    }
    data.update(overrides)
    return ClientCardSourceConfig(**data)


def _public_resolver(_host: str) -> list[str]:
    return ["93.184.216.34"]


def test_source_crud():
    session = _session()
    _seed_fields(session)

    created = settings_client_card.create_client_card_source(
        ClientCardSourceCreate(
            technical_key="employee_lookup",
            name="Employee lookup",
            method="GET",
            url_template="https://api.example.com/users",
            query_template_json={"number": "{{internal_number}}"},
            headers_template_json={"Accept": "application/json"},
            mapping_json=[{"response_path": "data.user.name", "target_key": "contact_name"}],
        ),
        db=session,
        user=_admin(),
    )
    assert created.technical_key == "employee_lookup"
    assert created.headers_template_json == {"Accept": "application/json"}

    listed = settings_client_card.list_client_card_sources(db=session, user=_admin())
    assert [item.id for item in listed] == [created.id]

    updated = settings_client_card.update_client_card_source(
        created.id,
        ClientCardSourceUpdate(name="Updated", method="POST", body_template_json={"id": "{{internal_number}}"}),
        db=session,
        user=_admin(),
    )
    assert updated.name == "Updated"
    assert updated.method == "POST"

    remaining = settings_client_card.delete_client_card_source(created.id, db=session, user=_admin())
    assert remaining == []
    assert session.get(ClientCardSourceConfig, created.id) is None


def test_source_patch_rejects_required_nulls_and_clears_nullable_fields():
    session = _session()
    _seed_fields(session)
    created = settings_client_card.create_client_card_source(
        ClientCardSourceCreate(
            technical_key="nullable",
            name="Nullable",
            url_template="https://api.example.com",
            query_template_json={"q": "{{internal_number}}"},
            headers_template_json={"X-Test": "1"},
            body_template_json={"body": "{{contact_name}}"},
            mapping_json=[{"response_path": "data.name", "target_key": "contact_name"}],
        ),
        db=session,
        user=_admin(),
    )

    for field, value in {"name": None, "timeout_ms": None, "is_active": None}.items():
        with pytest.raises(HTTPException) as exc:
            settings_client_card.update_client_card_source(
                created.id,
                ClientCardSourceUpdate(**{field: value}),
                db=session,
                user=_admin(),
            )
        assert exc.value.status_code == 422
        assert exc.value.detail["error"]["code"] == "source_required_field_null"

    updated = settings_client_card.update_client_card_source(
        created.id,
        ClientCardSourceUpdate(
            technical_key=None,
            query_template_json=None,
            headers_template_json=None,
            body_template_json=None,
            mapping_json=None,
        ),
        db=session,
        user=_admin(),
    )

    assert updated.technical_key is None
    assert updated.query_template_json is None
    assert updated.headers_template_json is None
    assert updated.body_template_json is None
    assert updated.mapping_json == []


def test_schema_evolution_creates_source_table(monkeypatch):
    engine = create_engine("sqlite:///:memory:", future=True)
    with engine.begin() as conn:
        conn.execute(text("CREATE TABLE client_card_field_configs (id VARCHAR(36), key VARCHAR(100), is_system BOOLEAN)"))
        conn.execute(text("CREATE TABLE client_card_block_configs (id VARCHAR(36), key VARCHAR(100))"))
    monkeypatch.setattr(schema_evolution, "engine", engine)

    schema_evolution.ensure_client_card_field_config_columns()

    with engine.connect() as conn:
        rows = conn.execute(text("PRAGMA table_info(client_card_source_configs)")).mappings().all()
    assert {"id", "technical_key", "method", "url_template", "mapping_json", "on_error"} <= {row["name"] for row in rows}


def test_get_executor_with_mocked_transport_and_recursive_field_templates(monkeypatch):
    captured = {}
    source = _source(
        query_template_json={"filters": {"number": "{{internal_number}}", "flags": ["{{is_verified}}"]}},
        headers_template_json={"Authorization": "Bearer {{secret:API_KEY}}"},
    )
    monkeypatch.setenv("CLIENT_CARD_SOURCE_SECRET_API_KEY", "token-123")

    def transport(request):
        captured["request"] = request
        return SourceHttpResponse(200, {"content-type": "application/json"}, b'{"data":{"user":{"name":"Anna"}}}')

    result = sources.execute_source(
        source,
        {"internal_number": "42", "contact_name": "Ann", "is_verified": True},
        transport=transport,
        resolver=_public_resolver,
    )

    assert result.http_status == 200
    assert "users/42" in captured["request"].url
    query = parse_qs(urlparse(captured["request"].url).query)
    assert json.loads(query["filters"][0]) == {"number": "42", "flags": ["true"]}
    assert captured["request"].headers["Authorization"] == "Bearer token-123"
    assert result.payload["data"]["user"]["name"] == "Anna"


def test_executor_uses_validated_ip_without_second_hostname_resolve(monkeypatch):
    source = _source(url_template="https://api.example.com/users")
    captured = {}

    def transport(request):
        captured["request"] = request
        return SourceHttpResponse(200, {"content-type": "application/json"}, b"{}")

    def forbidden_dns(host, *args, **kwargs):
        if host == "api.example.com":
            raise AssertionError("hostname was resolved after validation")
        return [(None, None, None, None, ("10.0.0.5", 443))]

    monkeypatch.setattr(sources.socket, "getaddrinfo", forbidden_dns)

    sources.execute_source(source, {}, transport=transport, resolver=lambda _host: ["93.184.216.34"])

    request = captured["request"]
    assert request.tls_hostname == "api.example.com"
    assert request.validated_addresses == ("93.184.216.34",)


def test_default_transport_connects_only_to_validated_ip(monkeypatch):
    connected = {}

    class FakeSocket:
        def settimeout(self, timeout):
            connected["timeout"] = timeout

        def connect(self, target):
            connected["target"] = target

        def close(self):
            connected["closed"] = True

    class FakeTlsSocket:
        def close(self):
            connected["tls_closed"] = True

    class FakeContext:
        def wrap_socket(self, raw_socket, server_hostname):
            connected["server_hostname"] = server_hostname
            return FakeTlsSocket()

    def fake_socket(family, kind):
        connected["family"] = family
        return FakeSocket()

    def forbidden_getaddrinfo(*args, **kwargs):
        raise AssertionError("default transport must not resolve DNS")

    class FakeResponse:
        status = 200

        def read(self):
            return b"{}"

        def getheaders(self):
            return [("content-type", "application/json")]

    def fake_request(self, method, path, body=None, headers=None):
        connected["method"] = method
        connected["path"] = path
        connected["headers"] = headers

    monkeypatch.setattr(sources.socket, "socket", fake_socket)
    monkeypatch.setattr(sources.socket, "getaddrinfo", forbidden_getaddrinfo)
    monkeypatch.setattr(sources.ssl, "create_default_context", lambda: FakeContext())
    monkeypatch.setattr(sources.ValidatedHTTPSConnection, "request", fake_request)
    monkeypatch.setattr(sources.ValidatedHTTPSConnection, "getresponse", lambda self: FakeResponse())

    response = sources.default_transport(
        sources.SourceHttpRequest(
            method="GET",
            url="https://api.example.com/users?q=1",
            headers={"accept": "application/json"},
            body=None,
            timeout=2.0,
            tls_hostname="api.example.com",
            port=443,
            validated_addresses=("93.184.216.34",),
        )
    )

    assert response.status == 200
    assert connected["target"] == ("93.184.216.34", 443)
    assert connected["server_hostname"] == "api.example.com"
    assert connected["headers"]["Host"] == "api.example.com"


def test_default_transport_falls_back_to_second_validated_ip_without_dns(monkeypatch):
    connected: dict[str, object] = {"targets": []}

    class FakeSocket:
        def __init__(self, address: str):
            self.address = address

        def settimeout(self, timeout):
            connected["timeout"] = timeout

        def connect(self, target):
            connected["targets"].append(target)
            if target[0] == "2001:db8::1":
                raise OSError("ipv6 route failed")

        def close(self):
            connected.setdefault("closed", 0)
            connected["closed"] += 1

    class FakeTlsSocket:
        def close(self):
            connected.setdefault("tls_closed", 0)
            connected["tls_closed"] += 1

    class FakeContext:
        def wrap_socket(self, raw_socket, server_hostname):
            connected["server_hostname"] = server_hostname
            return FakeTlsSocket()

    def fake_socket(family, kind):
        address = "2001:db8::1" if family == sources.socket.AF_INET6 else "93.184.216.34"
        return FakeSocket(address)

    def forbidden_getaddrinfo(*args, **kwargs):
        raise AssertionError("default transport must not resolve DNS")

    class FakeResponse:
        status = 200

        def read(self):
            return b"{}"

        def getheaders(self):
            return [("content-type", "application/json")]

    def fake_request(self, method, path, body=None, headers=None):
        connected["method"] = method
        connected["path"] = path
        connected["headers"] = headers

    monkeypatch.setattr(sources.socket, "socket", fake_socket)
    monkeypatch.setattr(sources.socket, "getaddrinfo", forbidden_getaddrinfo)
    monkeypatch.setattr(sources.ssl, "create_default_context", lambda: FakeContext())
    monkeypatch.setattr(sources.ValidatedHTTPSConnection, "request", fake_request)
    monkeypatch.setattr(sources.ValidatedHTTPSConnection, "getresponse", lambda self: FakeResponse())

    response = sources.default_transport(
        sources.SourceHttpRequest(
            method="GET",
            url="https://api.example.com/users",
            headers={"accept": "application/json"},
            body=None,
            timeout=2.0,
            tls_hostname="api.example.com",
            port=443,
            validated_addresses=("2001:db8::1", "93.184.216.34"),
        )
    )

    assert response.status == 200
    assert connected["targets"] == [("2001:db8::1", 443, 0, 0), ("93.184.216.34", 443)]
    assert connected["server_hostname"] == "api.example.com"


def test_default_transport_ipv6_host_header_is_bracketed(monkeypatch):
    connected = {}

    class FakeSocket:
        def settimeout(self, timeout):
            connected["timeout"] = timeout

        def connect(self, target):
            connected["target"] = target

        def close(self):
            connected["closed"] = True

    class FakeTlsSocket:
        def close(self):
            connected["tls_closed"] = True

    class FakeContext:
        def wrap_socket(self, raw_socket, server_hostname):
            connected["server_hostname"] = server_hostname
            return FakeTlsSocket()

    class FakeResponse:
        status = 200

        def read(self):
            return b"{}"

        def getheaders(self):
            return [("content-type", "application/json")]

    monkeypatch.setattr(sources.socket, "socket", lambda family, kind: FakeSocket())
    monkeypatch.setattr(sources.ssl, "create_default_context", lambda: FakeContext())
    monkeypatch.setattr(sources.ValidatedHTTPSConnection, "request", lambda self, method, path, body=None, headers=None: connected.setdefault("headers", headers))
    monkeypatch.setattr(sources.ValidatedHTTPSConnection, "getresponse", lambda self: FakeResponse())

    sources.default_transport(
        sources.SourceHttpRequest(
            method="GET",
            url="https://[2001:db8::2]:8443/users",
            headers={"accept": "application/json"},
            body=None,
            timeout=2.0,
            tls_hostname="2001:db8::2",
            port=8443,
            validated_addresses=("2001:db8::2",),
        )
    )

    assert connected["headers"]["Host"] == "[2001:db8::2]:8443"
    assert connected["server_hostname"] == "2001:db8::2"


def test_ipv6_host_header_helper_brackets_default_https_port():
    assert sources._host_header("2001:db8::2", 443) == "[2001:db8::2]"
    assert sources._host_header("2001:db8::2", 8443) == "[2001:db8::2]:8443"


def test_post_executor_sends_json_body_with_mocked_transport():
    captured = {}
    source = _source(method="POST", body_template_json={"nested": {"id": "{{internal_number}}"}})

    def transport(request):
        captured["request"] = request
        return SourceHttpResponse(200, {"Content-Type": "application/json; charset=utf-8"}, b'{"ok":true}')

    sources.execute_source(source, {"internal_number": "77"}, transport=transport, resolver=_public_resolver)

    assert captured["request"].method == "POST"
    assert json.loads(captured["request"].body.decode("utf-8")) == {"nested": {"id": "77"}}
    assert captured["request"].headers["content-type"] == "application/json"


def test_secret_refs_are_allowed_only_in_headers_and_do_not_read_arbitrary_env(monkeypatch):
    monkeypatch.setenv("API_KEY", "wrong")
    source = _source(headers_template_json={"Authorization": "Bearer {{secret:API_KEY}}"})
    captured = {}

    def transport(request):
        captured["headers"] = request.headers
        return SourceHttpResponse(200, {"content-type": "application/json"}, b"{}")

    with pytest.raises(ClientCardSourceConfigError, match="missing"):
        sources.execute_source(source, {}, transport=transport, resolver=_public_resolver)
    assert "headers" not in captured

    monkeypatch.setenv("CLIENT_CARD_SOURCE_SECRET_API_KEY", "right")
    sources.execute_source(source, {}, transport=transport, resolver=_public_resolver)
    assert captured["headers"]["Authorization"] == "Bearer right"

    with pytest.raises(ClientCardSourceConfigError):
        sources.render_json_templates({"token": "{{secret:API_KEY}}"}, {}, allow_secrets=False)
    with pytest.raises(ClientCardSourceConfigError):
        sources.render_json_templates("{{secret:../API_KEY}}", {}, allow_secrets=True)


def test_template_validation_rejects_secret_url_invalid_secret_unknown_and_malformed_refs():
    session = _session()
    _seed_fields(session)

    cases = [
        {"url_template": "https://api.example.com/{{secret:API_KEY}}"},
        {"url_template": "https://api.example.com/{{unknown_field}}"},
        {"url_template": "https://api.example.com/{{bad-token}}"},
        {"url_template": "https://api.example.com", "headers_template_json": {"Authorization": "Bearer {{secret:bad}}"}},
        {"url_template": "https://api.example.com", "query_template_json": {"q": "{{unknown_field}}"}},
        {"url_template": "https://api.example.com", "body_template_json": {"q": "{{bad-token}}"}},
    ]

    for index, overrides in enumerate(cases):
        with pytest.raises(HTTPException) as exc:
            settings_client_card.create_client_card_source(
                ClientCardSourceCreate(
                    name=f"Bad template {index}",
                    mapping_json=[{"response_path": "data.name", "target_key": "contact_name"}],
                    **overrides,
                ),
                db=session,
                user=_admin(),
            )
        assert exc.value.status_code == 422
        assert exc.value.detail["error"]["code"] == "source_config_invalid"


def test_template_keys_are_rejected_and_value_refs_define_dependencies():
    session = _session()
    _seed_fields(session)

    for field_name, value in [
        ("query_template_json", {"{{internal_number}}": "literal"}),
        ("headers_template_json", {"X-{{internal_number}}": "literal"}),
        ("body_template_json", {"{{internal_number}}": "literal"}),
    ]:
        with pytest.raises(HTTPException) as exc:
            settings_client_card.create_client_card_source(
                ClientCardSourceCreate(
                    name=f"Bad key {field_name}",
                    url_template="https://api.example.com",
                    mapping_json=[{"response_path": "data.name", "target_key": "contact_name"}],
                    **{field_name: value},
                ),
                db=session,
                user=_admin(),
            )
        assert exc.value.status_code == 422
        assert exc.value.detail["error"]["code"] == "source_config_invalid"

    source = _source(
        url_template="https://api.example.com/users/{{internal_number}}",
        query_template_json={"literal_key": "{{contact_name}}"},
        headers_template_json={"X-Static": "{{secret:API_KEY}}"},
        body_template_json={"nested": {"literal": "{{custom_note}}"}},
    )

    assert sources.source_template_field_refs(source) == {"internal_number", "contact_name", "custom_note"}


def test_missing_prefixed_secret_fails_safely_without_value(monkeypatch):
    source = _source(headers_template_json={"Authorization": "Bearer {{secret:API_KEY}}"})
    monkeypatch.delenv("CLIENT_CARD_SOURCE_SECRET_API_KEY", raising=False)

    with pytest.raises(ClientCardSourceConfigError) as exc:
        sources.execute_source(source, {}, transport=lambda _request: SourceHttpResponse(200, {"content-type": "application/json"}, b"{}"), resolver=_public_resolver)

    assert "API_KEY" not in str(exc.value)
    assert "secret-value" not in str(exc.value)


def test_secrets_are_absent_from_api_and_errors(monkeypatch):
    session = _session()
    _seed_fields(session)
    monkeypatch.setenv("CLIENT_CARD_SOURCE_SECRET_API_KEY", "super-secret")

    created = settings_client_card.create_client_card_source(
        ClientCardSourceCreate(
            name="Secret source",
            url_template="https://api.example.com",
            headers_template_json={"Authorization": "Bearer {{secret:API_KEY}}"},
            mapping_json=[{"response_path": "data.name", "target_key": "contact_name"}],
        ),
        db=session,
        user=_admin(),
    )
    assert "super-secret" not in created.model_dump_json()

    with pytest.raises(HTTPException) as exc:
        settings_client_card.test_client_card_source(created.id, ClientCardSourceTestRequest(context={}), db=session, user=_admin())
    assert "super-secret" not in str(exc.value.detail)


def test_https_only_and_static_host_enforcement():
    with pytest.raises(ClientCardSourceConfigError):
        sources.validate_url_template_static("http://api.example.com")
    with pytest.raises(ClientCardSourceConfigError):
        sources.validate_url_template_static("https://{{host}}/users")
    with pytest.raises(ClientCardSourceConfigError):
        sources.validate_rendered_url("https://api.example.com/users/{{id}}", "https://evil.example.com/users/1", resolver=_public_resolver)


def test_allowlist_is_enforced_in_production(monkeypatch):
    monkeypatch.setattr(sources.settings, "app_env", "prod")
    monkeypatch.setattr(sources.settings, "client_card_source_allowed_hosts", "allowed.example.com")

    with pytest.raises(ClientCardSourceConfigError):
        sources.validate_rendered_url("https://api.example.com", "https://api.example.com", resolver=_public_resolver)

    assert sources.validate_rendered_url("https://allowed.example.com", "https://allowed.example.com", resolver=_public_resolver) == "https://allowed.example.com"


@pytest.mark.parametrize("address", ["127.0.0.1", "::1", "10.0.0.1", "172.16.0.1", "192.168.1.1", "169.254.1.1", "ff02::1", "0.0.0.0"])
def test_private_loopback_and_special_ips_are_rejected(address):
    with pytest.raises(ClientCardSourceConfigError):
        sources.validate_hostname(address)


def test_hostname_resolving_to_private_ipv6_is_rejected():
    with pytest.raises(ClientCardSourceConfigError):
        sources.validate_hostname("api.example.com", resolver=lambda _host: ["fd00::1"])


def test_timeout_and_non_json_response():
    source = _source()

    def timeout_transport(_request):
        raise ClientCardSourceHttpError("Source HTTP request timed out")

    with pytest.raises(ClientCardSourceHttpError, match="timed out"):
        sources.execute_source(source, {}, transport=timeout_transport, resolver=_public_resolver)

    def text_transport(_request):
        return SourceHttpResponse(200, {"content-type": "text/plain"}, b"ok")

    with pytest.raises(ClientCardSourceHttpError, match="non-JSON"):
        sources.execute_source(source, {}, transport=text_transport, resolver=_public_resolver)


def test_redirects_disabled_handler_contract():
    assert sources.NoRedirectHandler().redirect_request(None, None, 302, "Found", {}, "https://example.com/next") is None


def test_safe_json_paths_array_index_and_missing_vs_null():
    payload = {"data": {"user": {"name": None}}, "items": [{"name": "First"}]}
    assert sources.read_response_path(payload, "data.user.name") is None
    assert sources.read_response_path(payload, "items[0].name") == "First"
    assert sources.read_response_path(payload, "items[1].name") is MISSING
    with pytest.raises(ClientCardSourceConfigError):
        sources.validate_response_path("data.user['name']")


def test_invalid_mapping_target_and_response_path():
    session = _session()
    _seed_fields(session)

    with pytest.raises(HTTPException) as target_exc:
        settings_client_card.create_client_card_source(
            ClientCardSourceCreate(
                name="Bad target",
                url_template="https://api.example.com",
                mapping_json=[{"response_path": "data.name", "target_key": "telegram_username"}],
            ),
            db=session,
            user=_admin(),
        )
    assert target_exc.value.detail["error"]["code"] == "source_mapping_target_invalid"

    with pytest.raises(HTTPException) as path_exc:
        settings_client_card.create_client_card_source(
            ClientCardSourceCreate(
                name="Bad path",
                url_template="https://api.example.com",
                mapping_json=[{"response_path": "data..name", "target_key": "contact_name"}],
            ),
            db=session,
            user=_admin(),
        )
    assert path_exc.value.detail["error"]["code"] == "source_response_path_invalid"


def test_source_rejects_duplicate_mapping_targets_on_create_and_patch():
    session = _session()
    _seed_fields(session)

    with pytest.raises(HTTPException) as create_exc:
        settings_client_card.create_client_card_source(
            ClientCardSourceCreate(
                name="Duplicate",
                url_template="https://api.example.com",
                mapping_json=[
                    {"response_path": "data.name", "target_key": "contact_name"},
                    {"response_path": "data.other_name", "target_key": "contact_name"},
                ],
            ),
            db=session,
            user=_admin(),
        )
    assert create_exc.value.status_code == 422
    assert create_exc.value.detail["error"]["code"] == "source_mapping_target_duplicate"

    created = settings_client_card.create_client_card_source(
        ClientCardSourceCreate(
            name="Valid",
            url_template="https://api.example.com",
            mapping_json=[{"response_path": "data.name", "target_key": "contact_name"}],
        ),
        db=session,
        user=_admin(),
    )

    with pytest.raises(HTTPException) as patch_exc:
        settings_client_card.update_client_card_source(
            created.id,
            ClientCardSourceUpdate(
                mapping_json=[
                    {"response_path": "data.name", "target_key": "contact_name"},
                    {"response_path": "data.other_name", "target_key": "contact_name"},
                ],
            ),
            db=session,
            user=_admin(),
        )
    assert patch_exc.value.status_code == 422
    assert patch_exc.value.detail["error"]["code"] == "source_mapping_target_duplicate"
    assert session.get(ClientCardSourceConfig, created.id).mapping_json == [{"response_path": "data.name", "target_key": "contact_name"}]


def test_test_endpoint_returns_preview_and_does_not_mutate_profile(monkeypatch):
    session = _session()
    _seed_fields(session)
    client = Client(id=str(uuid.uuid4()), company_id="company-1", display_name="Before")
    profile = ClientProfile(id=str(uuid.uuid4()), client_id=client.id, contact_name="Before", custom_fields={"custom_note": "old"})
    session.add_all([client, profile])
    session.commit()
    created = settings_client_card.create_client_card_source(
        ClientCardSourceCreate(
            name="Preview",
            url_template="https://api.example.com",
            mapping_json=[
                {"response_path": "data.name", "target_key": "contact_name"},
                {"response_path": "data.note", "target_key": "custom_note"},
                {"response_path": "data.missing", "target_key": "last_name"},
            ],
        ),
        db=session,
        user=_admin(),
    )

    def fake_execute(source, context):
        return SimpleNamespace(http_status=200, payload={"data": {"name": "After", "note": "raw"}}, duration_ms=12)

    monkeypatch.setattr(settings_client_card, "execute_source", fake_execute)

    preview = settings_client_card.test_client_card_source(
        created.id,
        ClientCardSourceTestRequest(context={"internal_number": "42"}),
        db=session,
        user=_admin(),
    )

    assert preview.http_status == 200
    assert preview.mapped_values == {"contact_name": "After", "custom_note": "raw"}
    assert preview.missing_response_paths == ["data.missing"]
    session.refresh(profile)
    assert profile.contact_name == "Before"
    assert profile.custom_fields == {"custom_note": "old"}


def test_delete_field_and_block_are_blocked_by_source_mapping_refs():
    session = _session()
    block = _block("custom_block_1")
    field = _field("custom_note", is_system=False)
    field.block_key = block.key
    session.add_all([block, field, _field("contact_name")])
    session.commit()
    source = settings_client_card.create_client_card_source(
        ClientCardSourceCreate(
            technical_key="mapped",
            name="Mapped",
            url_template="https://api.example.com",
            mapping_json=[{"response_path": "data.note", "target_key": "custom_note"}],
        ),
        db=session,
        user=_admin(),
    )

    with pytest.raises(HTTPException) as field_exc:
        settings_client_card.delete_client_card_field(field.id, db=session, user=_admin())
    assert field_exc.value.status_code == 409
    assert field_exc.value.detail["error"]["code"] == "source_mapping_target_in_use"
    assert field_exc.value.detail["error"]["details"]["sources"] == [{"id": source.id, "name": "Mapped", "technical_key": "mapped"}]

    with pytest.raises(HTTPException) as block_exc:
        settings_client_card.delete_client_card_block(block.id, db=session, user=_admin())
    assert block_exc.value.status_code == 409
    assert block_exc.value.detail["error"]["code"] == "source_mapping_target_in_use"
    assert block_exc.value.detail["error"]["details"]["sources"] == [{"id": source.id, "name": "Mapped", "technical_key": "mapped"}]


def test_delete_field_and_block_are_blocked_by_source_template_refs():
    session = _session()
    block = _block("custom_block_1")
    field = _field("custom_note", is_system=False)
    field.block_key = block.key
    session.add_all([block, field, _field("contact_name")])
    session.commit()
    source = settings_client_card.create_client_card_source(
        ClientCardSourceCreate(
            technical_key="templated",
            name="Templated",
            url_template="https://api.example.com/users/{{custom_note}}",
            mapping_json=[{"response_path": "data.name", "target_key": "contact_name"}],
        ),
        db=session,
        user=_admin(),
    )

    with pytest.raises(HTTPException) as field_exc:
        settings_client_card.delete_client_card_field(field.id, db=session, user=_admin())
    assert field_exc.value.status_code == 409
    assert field_exc.value.detail["error"]["details"]["sources"] == [{"id": source.id, "name": "Templated", "technical_key": "templated"}]

    with pytest.raises(HTTPException) as block_exc:
        settings_client_card.delete_client_card_block(block.id, db=session, user=_admin())
    assert block_exc.value.status_code == 409
    assert block_exc.value.detail["error"]["details"]["sources"] == [{"id": source.id, "name": "Templated", "technical_key": "templated"}]


def test_mocked_transport_http_400_is_source_error():
    source = _source()

    with pytest.raises(ClientCardSourceHttpError) as exc:
        sources.execute_source(
            source,
            {"internal_number": "42"},
            transport=lambda _request: SourceHttpResponse(404, {"content-type": "application/json"}, b'{"error":"missing"}'),
            resolver=_public_resolver,
        )

    assert exc.value.status == 404
