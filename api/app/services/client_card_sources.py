from __future__ import annotations

import ipaddress
import http.client
import json
import os
import re
import socket
import ssl
import time
from dataclasses import dataclass
from typing import Any, Callable
from urllib.parse import urlencode, urlparse, urlunparse
from urllib.request import HTTPRedirectHandler

from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..config import settings
from ..models import ClientCardFieldConfig, ClientCardSourceConfig
from .client_card_derivation import SYSTEM_PROFILE_FIELDS, normalize_client_card_derived_value


MISSING = object()
FIELD_TEMPLATE_RE = re.compile(r"\{\{([a-zA-Z0-9_]+)\}\}")
SECRET_TEMPLATE_RE = re.compile(r"\{\{secret:([A-Z0-9_]+)\}\}")
FIELD_TEMPLATE_NAME_RE = re.compile(r"^[a-zA-Z0-9_]+$")
DEV_LOCAL_HTTP_HOSTS = frozenset({"host.docker.internal", "localhost", "127.0.0.1", "::1"})
SYSTEM_SOURCE_REQUIRED_MAPPINGS = (
    {"response_path": "data.first_name", "target_key": "contact_name"},
    {"response_path": "data.last_name", "target_key": "last_name"},
    {"response_path": "data.id", "target_key": "bitrix_id"},
    {"response_path": "data.id", "target_key": "external_user_id"},
    {"response_path": "data.login", "target_key": "bitrix_login"},
)


class ClientCardSourceError(Exception):
    pass


class ClientCardSourceConfigError(ClientCardSourceError):
    pass


class ClientCardSourceHttpError(ClientCardSourceError):
    def __init__(self, message: str, *, status: int | None = None):
        super().__init__(message)
        self.status = status


@dataclass(frozen=True)
class SourceHttpRequest:
    method: str
    url: str
    headers: dict[str, str]
    body: bytes | None
    timeout: float
    tls_hostname: str
    port: int
    validated_addresses: tuple[str, ...]


@dataclass(frozen=True)
class SourceHttpResponse:
    status: int
    headers: dict[str, str]
    body: bytes


@dataclass(frozen=True)
class SourceExecutionResult:
    http_status: int
    payload: Any
    duration_ms: int


@dataclass(frozen=True)
class ValidatedSourceUrl:
    url: str
    scheme: str
    hostname: str
    port: int
    addresses: tuple[str, ...]


class NoRedirectHandler(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def _error(status_code: int, code: str, message: str):
    raise HTTPException(status_code=status_code, detail={"error": {"code": code, "message": message, "details": {}}})


def allowed_hosts() -> set[str]:
    raw = settings.client_card_source_allowed_hosts or ""
    return {item.strip().lower() for item in raw.split(",") if item.strip()}


def persistable_client_card_keys(db: Session) -> set[str]:
    configured = set(db.execute(select(ClientCardFieldConfig.key)).scalars().all())
    custom = set(
        db.execute(
            select(ClientCardFieldConfig.key).where(ClientCardFieldConfig.is_system.is_(False))
        ).scalars().all()
    )
    return (set(SYSTEM_PROFILE_FIELDS) | custom) & configured


def source_context_field_keys(db: Session) -> set[str]:
    custom = set(
        db.execute(
            select(ClientCardFieldConfig.key).where(ClientCardFieldConfig.is_system.is_(False))
        ).scalars().all()
    )
    return set(SYSTEM_PROFILE_FIELDS) | custom


def parse_template_refs(template: str, *, field_keys: set[str] | None = None, allow_secrets: bool = False) -> tuple[set[str], set[str]]:
    fields: set[str] = set()
    secrets: set[str] = set()
    position = 0
    while position < len(template):
        open_index = template.find("{{", position)
        close_index = template.find("}}", position)
        if close_index != -1 and (open_index == -1 or close_index < open_index):
            raise ClientCardSourceConfigError("Malformed template token")
        if open_index == -1:
            break
        close_index = template.find("}}", open_index + 2)
        if close_index == -1:
            raise ClientCardSourceConfigError("Malformed template token")
        token = template[open_index + 2:close_index]
        if not token or token != token.strip():
            raise ClientCardSourceConfigError("Malformed template token")
        if token.startswith("secret:"):
            name = token.removeprefix("secret:")
            if not allow_secrets or not re.fullmatch(r"[A-Z0-9_]+", name or ""):
                raise ClientCardSourceConfigError("Invalid secret reference")
            secrets.add(name)
        else:
            if not FIELD_TEMPLATE_NAME_RE.fullmatch(token):
                raise ClientCardSourceConfigError("Malformed template token")
            if field_keys is not None and token not in field_keys:
                raise ClientCardSourceConfigError("Unknown source template field")
            fields.add(token)
        position = close_index + 2
    return fields, secrets


def validate_response_path(path: str) -> None:
    parse_response_path(path)


def parse_response_path(path: str) -> list[str | int]:
    if not isinstance(path, str) or not path.strip():
        raise ClientCardSourceConfigError("Invalid response path")
    parts: list[str | int] = []
    for segment in path.split("."):
        if not segment:
            raise ClientCardSourceConfigError("Invalid response path")
        position = 0
        match = re.match(r"[A-Za-z_][A-Za-z0-9_]*", segment)
        if not match:
            raise ClientCardSourceConfigError("Invalid response path")
        parts.append(match.group(0))
        position = match.end()
        while position < len(segment):
            index_match = re.match(r"\[(\d+)\]", segment[position:])
            if not index_match:
                raise ClientCardSourceConfigError("Invalid response path")
            parts.append(int(index_match.group(1)))
            position += len(index_match.group(0))
    return parts


def read_response_path(payload: Any, path: str) -> Any:
    current = payload
    for part in parse_response_path(path):
        if isinstance(part, int):
            if not isinstance(current, list) or part >= len(current):
                return MISSING
            current = current[part]
            continue
        if not isinstance(current, dict) or part not in current:
            return MISSING
        current = current[part]
    return current


def render_template_string(value: str, context: dict[str, Any], *, allow_secrets: bool) -> str:
    parse_template_refs(value, allow_secrets=allow_secrets)

    def field_replacer(match: re.Match) -> str:
        return _normalize_template_value(context.get(match.group(1)))

    rendered = FIELD_TEMPLATE_RE.sub(field_replacer, value)
    if not allow_secrets:
        return rendered

    def secret_replacer(match: re.Match) -> str:
        name = match.group(1)
        env_name = f"CLIENT_CARD_SOURCE_SECRET_{name}"
        if env_name not in os.environ:
            raise ClientCardSourceConfigError("Configured source secret is missing")
        return os.environ[env_name]

    return SECRET_TEMPLATE_RE.sub(secret_replacer, rendered)


def render_json_templates(value: Any, context: dict[str, Any], *, allow_secrets: bool = False) -> Any:
    if isinstance(value, str):
        return render_template_string(value, context, allow_secrets=allow_secrets)
    if isinstance(value, list):
        return [render_json_templates(item, context, allow_secrets=allow_secrets) for item in value]
    if isinstance(value, dict):
        return {
            str(key): render_json_templates(item, context, allow_secrets=allow_secrets)
            for key, item in value.items()
        }
    return value


def validate_template_tree(value: Any, field_keys: set[str], *, allow_secrets_in_values: bool = False, validate_keys: bool = False) -> None:
    if isinstance(value, str):
        parse_template_refs(value, field_keys=field_keys, allow_secrets=allow_secrets_in_values)
        return
    if isinstance(value, list):
        for item in value:
            validate_template_tree(item, field_keys, allow_secrets_in_values=allow_secrets_in_values, validate_keys=validate_keys)
        return
    if isinstance(value, dict):
        for key, item in value.items():
            if validate_keys and ("{{" in str(key) or "}}" in str(key)):
                raise ClientCardSourceConfigError("Source template keys are not supported")
            validate_template_tree(item, field_keys, allow_secrets_in_values=allow_secrets_in_values, validate_keys=validate_keys)


def collect_template_tree_field_refs(value: Any) -> set[str]:
    refs: set[str] = set()
    if isinstance(value, str):
        fields, _secrets = parse_template_refs(value, allow_secrets=True)
        refs.update(fields)
    elif isinstance(value, list):
        for item in value:
            refs.update(collect_template_tree_field_refs(item))
    elif isinstance(value, dict):
        for item in value.values():
            refs.update(collect_template_tree_field_refs(item))
    return refs


def source_template_field_refs(source: ClientCardSourceConfig) -> set[str]:
    if source.kind == "system" or source.technical_key == "call_center_employee_by_internal_number":
        return {"internal_number"}
    refs, _secrets = parse_template_refs(source.url_template or "", allow_secrets=False)
    refs.update(collect_template_tree_field_refs(source.query_template_json or {}))
    refs.update(collect_template_tree_field_refs(source.headers_template_json or {}))
    refs.update(collect_template_tree_field_refs(source.body_template_json))
    return refs


def ensure_system_source_mapping_json(mapping_json: list[dict[str, Any]] | None) -> list[dict[str, Any]]:
    merged = [dict(item) for item in (mapping_json or []) if isinstance(item, dict)]
    seen_targets = {
        str(item.get("target_key") or "").strip()
        for item in merged
        if str(item.get("target_key") or "").strip()
    }
    for item in SYSTEM_SOURCE_REQUIRED_MAPPINGS:
        if item["target_key"] in seen_targets:
            continue
        merged.append(dict(item))
        seen_targets.add(item["target_key"])
    return merged


def validate_mapping(db: Session, mapping: list[dict[str, Any]]) -> None:
    keys = persistable_client_card_keys(db)
    seen_targets: set[str] = set()
    for item in mapping or []:
        response_path = item.get("response_path")
        target_key = item.get("target_key")
        try:
            validate_response_path(response_path)
        except ClientCardSourceConfigError:
            _error(422, "source_response_path_invalid", "Invalid source response path")
        if target_key not in keys:
            _error(422, "source_mapping_target_invalid", "Mapping target is not a persistable client-card field")
        if target_key in seen_targets:
            _error(422, "source_mapping_target_duplicate", "Source mapping target cannot be repeated")
        seen_targets.add(target_key)


def validate_source_payload(db: Session, *, method: str, kind: str, on_error: str, url_template: str, mapping: list[dict[str, Any]], query: Any, headers: Any, body: Any) -> None:
    if kind != "http":
        _error(422, "source_kind_invalid", "Unsupported source kind")
    if method not in {"GET", "POST"}:
        _error(422, "source_method_invalid", "Unsupported source HTTP method")
    if on_error not in {"ignore", "block_save"}:
        _error(422, "source_on_error_invalid", "Unsupported source error policy")
    if headers is not None and not isinstance(headers, dict):
        _error(422, "source_headers_invalid", "Source headers template must be an object")
    field_keys = source_context_field_keys(db)
    try:
        validate_url_template_static(url_template)
        parse_template_refs(url_template, field_keys=field_keys, allow_secrets=False)
        validate_template_tree(query, field_keys, allow_secrets_in_values=False, validate_keys=True)
        validate_template_tree(body, field_keys, allow_secrets_in_values=False, validate_keys=True)
        validate_template_tree(headers, field_keys, allow_secrets_in_values=True, validate_keys=True)
    except ClientCardSourceConfigError as exc:
        _error(422, "source_config_invalid", str(exc))
    validate_mapping(db, mapping)


def execute_source(
    source: ClientCardSourceConfig,
    context: dict[str, Any],
    *,
    transport: Callable[[SourceHttpRequest], SourceHttpResponse] | None = None,
    resolver: Callable[[str], list[str]] | None = None,
) -> SourceExecutionResult:
    if source.kind == "system" or source.technical_key == "call_center_employee_by_internal_number":
        return _execute_system_source(source, context)
    if source.kind != "http":
        raise ClientCardSourceConfigError("Unsupported source kind")
    method = (source.method or "GET").upper()
    if method not in {"GET", "POST"}:
        raise ClientCardSourceConfigError("Unsupported source HTTP method")

    rendered_url = render_template_string(source.url_template, context, allow_secrets=False)
    validated_url = resolve_and_validate_url(source.url_template, rendered_url, resolver=resolver)
    url = validated_url.url
    query = render_json_templates(source.query_template_json or {}, context, allow_secrets=False)
    if query:
        if not isinstance(query, dict):
            raise ClientCardSourceConfigError("Query template must render to an object")
        url = _append_query(url, query)
    headers = render_json_templates(source.headers_template_json or {}, context, allow_secrets=True)
    if not isinstance(headers, dict):
        raise ClientCardSourceConfigError("Headers template must render to an object")
    request_headers = {str(key): str(value) for key, value in headers.items()}
    body = None
    if method == "POST":
        body = json.dumps(render_json_templates(source.body_template_json, context, allow_secrets=False)).encode("utf-8")
        request_headers.setdefault("content-type", "application/json")
    request_headers.setdefault("accept", "application/json")

    request = SourceHttpRequest(
        method=method,
        url=url,
        headers=request_headers,
        body=body,
        timeout=(source.timeout_ms or 3000) / 1000,
        tls_hostname=validated_url.hostname,
        port=validated_url.port,
        validated_addresses=validated_url.addresses,
    )
    started = time.monotonic()
    response = (transport or default_transport)(request)
    duration_ms = int((time.monotonic() - started) * 1000)
    if 300 <= response.status < 400:
        raise ClientCardSourceHttpError("Source redirects are disabled", status=response.status)
    if response.status >= 400:
        raise ClientCardSourceHttpError(f"Source HTTP request failed with status {response.status}", status=response.status)
    content_type = next((value for key, value in response.headers.items() if key.lower() == "content-type"), "")
    if "json" not in content_type.lower():
        raise ClientCardSourceHttpError("Source returned non-JSON response", status=response.status)
    try:
        payload = json.loads(response.body.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ClientCardSourceHttpError("Source returned invalid JSON response", status=response.status) from exc
    return SourceExecutionResult(http_status=response.status, payload=payload, duration_ms=duration_ms)


def _execute_system_source(source: ClientCardSourceConfig, context: dict[str, Any]) -> SourceExecutionResult:
    if source.technical_key != "call_center_employee_by_internal_number":
        raise ClientCardSourceConfigError("Unsupported source kind")
    from . import call_center_employee_resolver

    started = time.monotonic()
    try:
        result = call_center_employee_resolver.resolve(None, context.get("internal_number"))
    except Exception as exc:
        raise ClientCardSourceError(f"System source failed: {type(exc).__name__}") from exc
    if result.status in {
        call_center_employee_resolver.STATUS_TECHNICAL_ERROR,
        call_center_employee_resolver.STATUS_REFRESH_TIMEOUT,
        call_center_employee_resolver.STATUS_NOT_CONFIGURED,
    }:
        raise ClientCardSourceError(f"System source failed: {result.status}")
    data = result.employee_payload()
    resolution_status = data.get("resolution_status") or result.status
    if result.status in {
        call_center_employee_resolver.STATUS_NOT_FOUND,
        call_center_employee_resolver.STATUS_AMBIGUOUS,
    }:
        payload_data = {
            "first_name": "",
            "last_name": "",
            "id": "",
            "bitrix_id": "",
            "login": "",
            "full_name": "",
            "department_ids": [],
            "resolution_status": resolution_status,
            "used_stale_fallback": False,
        }
    else:
        payload_data = {
            "first_name": data.get("first_name") or "",
            "last_name": data.get("last_name") or "",
            "id": data.get("bitrix_id") or "",
            "bitrix_id": data.get("bitrix_id") or "",
            "full_name": data.get("full_name") or "",
            "department_ids": data.get("department_ids") or [],
            "resolution_status": resolution_status,
            "used_stale_fallback": bool(data.get("used_stale_fallback")),
        }
        if data.get("bitrix_login"):
            payload_data["login"] = data.get("bitrix_login")
    payload = {"data": payload_data}
    return SourceExecutionResult(
        http_status=200,
        payload=payload,
        duration_ms=int((time.monotonic() - started) * 1000),
    )


class ValidatedHTTPSConnection(http.client.HTTPSConnection):
    def __init__(self, host: str, port: int, *, connect_address: str, timeout: float):
        super().__init__(
            host,
            port=port,
            timeout=timeout,
            context=ssl.create_default_context(),
        )
        self._connect_address = connect_address

    def connect(self) -> None:
        raw_socket = _connect_validated_ip(self._connect_address, self.port, self.timeout)
        self.sock = self._context.wrap_socket(raw_socket, server_hostname=self.host)


class ValidatedHTTPConnection(http.client.HTTPConnection):
    def __init__(self, host: str, port: int, *, connect_address: str, timeout: float):
        super().__init__(host, port=port, timeout=timeout)
        self._connect_address = connect_address

    def connect(self) -> None:
        self.sock = _connect_validated_ip(self._connect_address, self.port, self.timeout)


def default_transport(request: SourceHttpRequest) -> SourceHttpResponse:
    parsed = urlparse(request.url)
    path = urlunparse(("", "", parsed.path or "/", parsed.params, parsed.query, ""))
    headers = dict(request.headers)
    headers.setdefault("Host", _host_header(request.tls_hostname, request.port, scheme=parsed.scheme.lower()))
    if not request.validated_addresses:
        raise ClientCardSourceHttpError("Source HTTP request failed")

    last_connect_error: Exception | None = None
    for index, connect_address in enumerate(request.validated_addresses):
        if parsed.scheme.lower() == "https":
            connection = ValidatedHTTPSConnection(
                request.tls_hostname,
                request.port,
                connect_address=connect_address,
                timeout=request.timeout,
            )
        elif parsed.scheme.lower() == "http":
            connection = ValidatedHTTPConnection(
                request.tls_hostname,
                request.port,
                connect_address=connect_address,
                timeout=request.timeout,
            )
        else:
            raise ClientCardSourceConfigError("Unsupported source URL scheme")
        try:
            connection.connect()
        except TimeoutError as exc:
            connection.close()
            last_connect_error = exc
            if index < len(request.validated_addresses) - 1:
                continue
            raise ClientCardSourceHttpError("Source HTTP request timed out") from exc
        except (OSError, ssl.SSLError) as exc:
            connection.close()
            last_connect_error = exc
            if index < len(request.validated_addresses) - 1:
                continue
            raise ClientCardSourceHttpError("Source HTTP request failed") from exc

        try:
            connection.request(request.method, path, body=request.body, headers=headers)
            response = connection.getresponse()
            body = response.read()
            if 300 <= response.status < 400:
                raise ClientCardSourceHttpError("Source redirects are disabled", status=response.status)
            if response.status >= 400:
                raise ClientCardSourceHttpError(f"Source HTTP request failed with status {response.status}", status=response.status)
            return SourceHttpResponse(
                status=response.status,
                headers=dict(response.getheaders()),
                body=body,
            )
        except TimeoutError as exc:
            raise ClientCardSourceHttpError("Source HTTP request timed out") from exc
        except OSError as exc:
            raise ClientCardSourceHttpError("Source HTTP request failed") from exc
        finally:
            connection.close()
    if isinstance(last_connect_error, TimeoutError):
        raise ClientCardSourceHttpError("Source HTTP request timed out") from last_connect_error
    raise ClientCardSourceHttpError("Source HTTP request failed") from last_connect_error


def mapped_preview(db: Session, source: ClientCardSourceConfig, payload: Any) -> tuple[dict[str, Any], list[str]]:
    mapped: dict[str, Any] = {}
    missing: list[str] = []
    field_configs = {field.key: field for field in db.execute(select(ClientCardFieldConfig)).scalars().all()}
    custom_keys = {key for key, field in field_configs.items() if not field.is_system}
    for item in source.mapping_json or []:
        path = item.get("response_path")
        target = item.get("target_key")
        value = read_response_path(payload, path)
        if value is MISSING:
            missing.append(path)
            continue
        normalize_client_card_derived_value(target, value, field_configs, custom_keys)
        mapped[target] = value
    return mapped, missing


def validate_rendered_url(configured_template: str, rendered_url: str, *, resolver: Callable[[str], list[str]] | None = None) -> str:
    return resolve_and_validate_url(configured_template, rendered_url, resolver=resolver).url


def resolve_and_validate_url(configured_template: str, rendered_url: str, *, resolver: Callable[[str], list[str]] | None = None) -> ValidatedSourceUrl:
    configured = urlparse(configured_template)
    rendered = urlparse(rendered_url)
    _validate_url_components(configured)
    _validate_url_components(rendered)
    _validate_url_scheme_policy(configured)
    _validate_url_scheme_policy(rendered)
    if not configured.hostname or not rendered.hostname:
        raise ClientCardSourceConfigError("Source URL hostname is required")
    configured_port = configured.port or _default_port(configured.scheme)
    rendered_port = rendered.port or _default_port(rendered.scheme)
    if (
        configured.scheme.lower(),
        configured.hostname.lower(),
        configured_port,
    ) != (
        rendered.scheme.lower(),
        rendered.hostname.lower(),
        rendered_port,
    ):
        raise ClientCardSourceConfigError("Source URL templates cannot change scheme, host, or port")
    addresses = validate_hostname(
        rendered.hostname,
        scheme=rendered.scheme.lower(),
        resolver=resolver,
    )
    if settings.is_prod and rendered.hostname.lower() not in allowed_hosts():
        raise ClientCardSourceConfigError("Source hostname is not allowed")
    return ValidatedSourceUrl(
        url=urlunparse(rendered),
        scheme=rendered.scheme.lower(),
        hostname=rendered.hostname,
        port=rendered_port,
        addresses=tuple(addresses),
    )


def validate_url_template_static(url_template: str) -> None:
    parsed = urlparse(url_template)
    _validate_url_components(parsed)
    _validate_url_scheme_policy(parsed)
    if not parsed.hostname:
        raise ClientCardSourceConfigError("Source URL hostname is required")
    netloc = parsed.netloc
    if "{{" in parsed.scheme or "{{" in netloc or "}}" in netloc:
        raise ClientCardSourceConfigError("Source URL templates cannot change scheme, host, or port")


def validate_hostname(
    hostname: str,
    *,
    scheme: str = "https",
    resolver: Callable[[str], list[str]] | None = None,
) -> list[str]:
    host = hostname.strip().lower().rstrip(".")
    if _is_dev_local_http_host(host, scheme=scheme):
        return _validate_dev_local_http_host(host, resolver=resolver)
    if host in {"localhost", "localhost.localdomain"} or host.endswith(".localhost"):
        raise ClientCardSourceConfigError("Source hostname is not allowed")
    addresses: list[str]
    try:
        ipaddress.ip_address(host)
        addresses = [host]
    except ValueError:
        try:
            addresses = (resolver or resolve_hostname)(host)
        except OSError as exc:
            raise ClientCardSourceConfigError("Source hostname could not be resolved") from exc
    for address in addresses:
        ip = ipaddress.ip_address(address)
        if (
            ip.is_loopback
            or ip.is_private
            or ip.is_link_local
            or ip.is_multicast
            or ip.is_unspecified
            or ip.is_reserved
        ):
            raise ClientCardSourceConfigError("Source hostname resolves to a disallowed IP address")
    return addresses


def _default_port(scheme: str) -> int:
    return 443 if scheme.lower() == "https" else 80


def _validate_url_components(parsed) -> None:
    if parsed.username is not None or parsed.password is not None:
        raise ClientCardSourceConfigError("Source URL credentials are not allowed")


def _validate_url_scheme_policy(parsed) -> None:
    scheme = parsed.scheme.lower()
    host = (parsed.hostname or "").strip().lower().rstrip(".")
    if scheme == "https":
        return
    if scheme == "http" and _is_dev_local_http_host(host, scheme=scheme):
        return
    raise ClientCardSourceConfigError("Source URL must use HTTPS")


def _is_dev_local_http_host(host: str, *, scheme: str) -> bool:
    return scheme.lower() == "http" and not settings.is_prod and host in DEV_LOCAL_HTTP_HOSTS


def _validate_dev_local_http_host(host: str, *, resolver: Callable[[str], list[str]] | None = None) -> list[str]:
    normalized = host.strip().lower().rstrip(".")
    if normalized not in DEV_LOCAL_HTTP_HOSTS:
        raise ClientCardSourceConfigError("Source hostname is not allowed")
    if normalized == "localhost":
        return ["127.0.0.1", "::1"]
    if normalized in {"127.0.0.1", "::1"}:
        return [normalized]
    try:
        addresses = (resolver or resolve_hostname)(normalized)
    except OSError as exc:
        raise ClientCardSourceConfigError("Source hostname could not be resolved") from exc
    if not addresses:
        raise ClientCardSourceConfigError("Source hostname could not be resolved")
    validated: list[str] = []
    for address in addresses:
        ip = ipaddress.ip_address(address)
        if not (ip.is_loopback or ip.is_private):
            raise ClientCardSourceConfigError("Source hostname resolves to a disallowed IP address")
        validated.append(address)
    return validated


def resolve_hostname(hostname: str) -> list[str]:
    addresses: list[str] = []
    seen: set[str] = set()
    for item in socket.getaddrinfo(hostname, None, type=socket.SOCK_STREAM):
        address = item[4][0]
        if address in seen:
            continue
        seen.add(address)
        addresses.append(address)
    return addresses


def _connect_validated_ip(address: str, port: int, timeout: float) -> socket.socket:
    ip = ipaddress.ip_address(address)
    family = socket.AF_INET6 if ip.version == 6 else socket.AF_INET
    sock = socket.socket(family, socket.SOCK_STREAM)
    sock.settimeout(timeout)
    try:
        target = (address, port, 0, 0) if ip.version == 6 else (address, port)
        sock.connect(target)
        return sock
    except Exception:
        sock.close()
        raise


def _host_header(hostname: str, port: int, *, scheme: str = "https") -> str:
    host = hostname
    try:
        if ipaddress.ip_address(hostname).version == 6:
            host = f"[{hostname}]"
    except ValueError:
        pass
    default_port = _default_port(scheme)
    if port == default_port:
        return host
    return f"{host}:{port}"


def _append_query(url: str, query: dict[str, Any]) -> str:
    parsed = urlparse(url)
    existing = parsed.query
    rendered = urlencode(
        {
            key: json.dumps(value, ensure_ascii=False) if isinstance(value, (dict, list)) else value
            for key, value in query.items()
            if value is not None
        },
        doseq=True,
    )
    query_string = "&".join(part for part in [existing, rendered] if part)
    return urlunparse(parsed._replace(query=query_string))


def _normalize_template_value(value: Any) -> str:
    if value is None:
        return ""
    if value is True:
        return "true"
    if value is False:
        return "false"
    return str(value).strip()
