#!/usr/bin/env python3
from __future__ import annotations

import argparse
import base64
import json
import re
import socket
import sys
import xml.etree.ElementTree as ET
from pathlib import Path
from typing import Any
from urllib import error, parse, request


LAST_FETCH_ERRORS: dict[str, str] = {}
RUNNER_KINDS = ("websocket", "bccontainerhelper", "startup-debug")
HTTP_ERROR_BODY_LIMIT = 800
HTTP_TEXT_BODY_LIMIT = 65536
ERROR_BODY_EXCERPT_LIMIT = 240
GUID_PATTERN = re.compile(r"\b[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}\b")
CI_HARNESS_APPS = {
    ("ALDirectCompile", "Test Runner Extension", "bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb"),
}


def http_class(status: int) -> str:
    if 200 <= status <= 299:
        return "2xx"
    if 300 <= status <= 399:
        return "3xx"
    if 400 <= status <= 499:
        return "4xx"
    if 500 <= status <= 599:
        return "5xx"
    return "000"


def normalize_company_name(value: str) -> str:
    return " ".join((value or "").split())


def parse_dev_api_major(metadata: dict) -> int | None:
    if not isinstance(metadata, dict):
        return None
    versions = metadata.get("supportedVersions")
    if not isinstance(versions, list):
        return None
    majors = []
    for item in versions:
        if not isinstance(item, dict):
            continue
        raw = str(item.get("apiVersion", ""))
        head = raw.split(".", 1)[0]
        if head.isdigit():
            majors.append(int(head))
    return max(majors) if majors else None


def summarize_test_output(output: str, runner_kind: str) -> dict:
    import re

    codeunit_count = 0
    match = re.search(r"^Test codeunits:[ \t]*([^\n]*)", output, re.MULTILINE)
    if match:
        codeunit_count = len([part for part in match.group(1).replace("|", ",").split(",") if part.strip()])

    totals = re.search(r"total=(\d+)\s+passed=(\d+)\s+failed=(\d+)\s+skipped=(\d+)", output)
    if not totals:
        totals = re.search(r"(\d+)\s+total,\s+(\d+)\s+passed,\s+(\d+)\s+failed", output)
        if totals:
            return {
                "testCodeunitCount": codeunit_count,
                "total": int(totals.group(1)),
                "passed": int(totals.group(2)),
                "failed": int(totals.group(3)),
                "skipped": 0,
                "runnerKind": runner_kind,
            }
    if totals:
        return {
            "testCodeunitCount": codeunit_count,
            "total": int(totals.group(1)),
            "passed": int(totals.group(2)),
            "failed": int(totals.group(3)),
            "skipped": int(totals.group(4)),
            "runnerKind": runner_kind,
        }
    return {
        "testCodeunitCount": codeunit_count,
        "total": 0,
        "passed": 0,
        "failed": 1,
        "skipped": 0,
        "runnerKind": runner_kind,
    }


def basic_header(auth: str) -> str:
    return "Basic " + base64.b64encode(auth.encode("utf-8")).decode("ascii")


def fetch_json(url: str, auth: str, timeout: int = 15) -> tuple[int, dict]:
    req = request.Request(url, headers={"Authorization": basic_header(auth)})
    try:
        with request.urlopen(req, timeout=timeout) as response:
            status = response.status
            payload = json.loads(response.read().decode("utf-8"))
            LAST_FETCH_ERRORS.pop(url, None)
            return status, payload
    except json.JSONDecodeError as exc:
        LAST_FETCH_ERRORS[url] = f"{type(exc).__name__}: {exc}"
        return 0, {}
    except error.HTTPError as exc:
        body = exc.read().decode("utf-8", errors="replace").strip()
        if body:
            LAST_FETCH_ERRORS[url] = f"HTTP {exc.code}: {body[:HTTP_ERROR_BODY_LIMIT]}"
        else:
            LAST_FETCH_ERRORS.pop(url, None)
        return exc.code, {}
    except Exception as exc:
        LAST_FETCH_ERRORS[url] = f"{type(exc).__name__}: {exc}"
        return 0, {}


def request_json(
    method: str,
    url: str,
    auth: str,
    payload: dict[str, Any] | None = None,
    headers: dict[str, str] | None = None,
    timeout: int = 15,
) -> tuple[int, dict]:
    request_headers = {
        "Authorization": basic_header(auth),
        "Accept": "application/json",
    }
    if payload is not None:
        request_headers["Content-Type"] = "application/json"
    request_headers.update(headers or {})
    body = None if payload is None else json.dumps(payload).encode("utf-8")
    req = request.Request(url, data=body, headers=request_headers, method=method)
    try:
        with request.urlopen(req, timeout=timeout) as response:
            raw_body = response.read().decode("utf-8", errors="replace").strip()
            LAST_FETCH_ERRORS.pop(url, None)
            return response.status, json.loads(raw_body) if raw_body else {}
    except json.JSONDecodeError as exc:
        LAST_FETCH_ERRORS[url] = f"{type(exc).__name__}: {exc}"
        return 0, {}
    except error.HTTPError as exc:
        body_text = exc.read().decode("utf-8", errors="replace").strip()
        if body_text:
            LAST_FETCH_ERRORS[url] = f"HTTP {exc.code}: {body_text[:HTTP_ERROR_BODY_LIMIT]}"
        else:
            LAST_FETCH_ERRORS.pop(url, None)
        try:
            return exc.code, json.loads(body_text) if body_text else {}
        except json.JSONDecodeError:
            return exc.code, {}
    except Exception as exc:
        LAST_FETCH_ERRORS[url] = f"{type(exc).__name__}: {exc}"
        return 0, {}


def join_url(base_url: str, path: str) -> str:
    return base_url.rstrip("/") + "/" + path.lstrip("/")


def tcp_open(url: str, timeout: int = 3) -> bool:
    parts = parse.urlparse(url)
    port = parts.port or (443 if parts.scheme == "https" else 80)
    if not parts.hostname:
        return False
    try:
        with socket.create_connection((parts.hostname, port), timeout=timeout):
            return True
    except OSError:
        return False


def fetch_status(url: str, auth: str | None = None, headers: dict[str, str] | None = None, timeout: int = 15) -> tuple[int, dict[str, str]]:
    request_headers = dict(headers or {})
    if auth is not None:
        request_headers["Authorization"] = basic_header(auth)
    req = request.Request(url, headers=request_headers)
    try:
        with request.urlopen(req, timeout=timeout) as response:
            LAST_FETCH_ERRORS.pop(url, None)
            return response.status, dict(response.headers.items())
    except error.HTTPError as exc:
        LAST_FETCH_ERRORS.pop(url, None)
        return exc.code, dict(exc.headers.items())
    except Exception as exc:
        LAST_FETCH_ERRORS[url] = f"{type(exc).__name__}: {exc}"
        return 0, {}


def fetch_text(
    url: str, auth: str | None = None, headers: dict[str, str] | None = None, timeout: int = 15
) -> tuple[int, dict[str, str], str]:
    request_headers = dict(headers or {})
    if auth is not None:
        request_headers["Authorization"] = basic_header(auth)
    req = request.Request(url, headers=request_headers)
    try:
        with request.urlopen(req, timeout=timeout) as response:
            LAST_FETCH_ERRORS.pop(url, None)
            body = response.read(HTTP_TEXT_BODY_LIMIT).decode("utf-8", errors="replace")
            return response.status, dict(response.headers.items()), body
    except error.HTTPError as exc:
        body = exc.read(HTTP_TEXT_BODY_LIMIT).decode("utf-8", errors="replace")
        LAST_FETCH_ERRORS.pop(url, None)
        return exc.code, dict(exc.headers.items()), body
    except Exception as exc:
        LAST_FETCH_ERRORS[url] = f"{type(exc).__name__}: {exc}"
        return 0, {}, ""


def auth_scheme_class(headers: dict[str, str]) -> str:
    value = " ".join(header_value for key, header_value in headers.items() if key.lower() == "www-authenticate").lower()
    if "basic" in value:
        return "basic"
    if "userpassword" in value or "navuserpassword" in value:
        return "userpassword"
    return "unknown"


def has_header(headers: dict[str, str], name: str) -> bool:
    return any(key.lower() == name for key in headers)


def extract_items(payload: Any) -> list[dict[str, Any]]:
    if isinstance(payload, list):
        return [item for item in payload if isinstance(item, dict)]
    if not isinstance(payload, dict):
        return []
    for key in ("value", "companies"):
        value = payload.get(key)
        if isinstance(value, list):
            return [item for item in value if isinstance(item, dict)]
    return []


def company_name(company: dict[str, Any]) -> str:
    for key in ("name", "displayName", "Name"):
        if key in company:
            return normalize_company_name(str(company.get(key, "")))
    return ""


def item_id_present(item: dict[str, Any]) -> bool:
    return any(str(item.get(key, "")).strip() for key in ("id", "Id", "SystemId", "systemId"))


def automation_base_url(api_url: str) -> str:
    trimmed = api_url.rstrip("/")
    suffix = "/api/v2.0"
    if trimmed.lower().endswith(suffix):
        return trimmed[: -len(suffix)] + "/api/microsoft/automation/v2.0"
    return trimmed + "/microsoft/automation/v2.0"


def company_segment(company_id: Any) -> str:
    return f"companies({parse.quote(str(company_id), safe='')})"


def normalize_extension(item: dict[str, Any]) -> dict[str, str]:
    return {
        "publisher": str(item.get("publisher", "")),
        "name": str(item.get("displayName") or item.get("name") or ""),
        "id": str(item.get("appId") or item.get("id") or item.get("packageId") or ""),
        "version": str(item.get("version") or item.get("appVersion") or item.get("packageVersion") or ""),
    }


def app_sort_key(item: dict[str, str]) -> tuple[str, str, str, str]:
    return (item.get("publisher", ""), item.get("name", ""), item.get("id", ""), item.get("version", ""))


def has_test_framework_signal(app: dict[str, str]) -> bool:
    name = normalize_company_name(app.get("name", "")).lower()
    substring_signals = ("test runner", "library assert", "library variable storage", "permissions mock")
    return name == "any" or any(signal in name for signal in substring_signals)


def is_ci_harness_app(app: dict[str, str]) -> bool:
    return (
        app.get("publisher", ""),
        normalize_company_name(app.get("name", "")),
        app.get("id", "").lower(),
    ) in CI_HARNESS_APPS


def split_apps(items: list[dict[str, Any]]) -> tuple[list[dict[str, str]], list[dict[str, str]], bool]:
    normalized = [normalize_extension(item) for item in items]
    user_visible_apps = [item for item in normalized if not is_ci_harness_app(item)]
    microsoft_apps = sorted((item for item in user_visible_apps if item["publisher"] == "Microsoft"), key=app_sort_key)
    custom_apps = sorted((item for item in user_visible_apps if item["publisher"] != "Microsoft"), key=app_sort_key)
    return microsoft_apps, custom_apps, any(has_test_framework_signal(item) for item in user_visible_apps)


def user_name(item: dict[str, Any]) -> str:
    for key in ("userName", "name", "displayName", "fullName"):
        value = normalize_company_name(str(item.get(key, "")))
        if value:
            return value
    return ""


def user_security_id(item: dict[str, Any]) -> str:
    for key in ("userSecurityId", "securityId", "id"):
        value = str(item.get(key, ""))
        if value:
            return value
    return ""


def user_enabled(item: dict[str, Any]) -> bool:
    enabled = item.get("enabled")
    if isinstance(enabled, bool):
        return enabled
    state = str(item.get("state") or item.get("status") or "").lower()
    if state in ("disabled", "inactive"):
        return False
    return True


def is_super_permission(item: dict[str, Any]) -> bool:
    for key in ("roleId", "permissionSetId"):
        if str(item.get(key, "")).upper() == "SUPER":
            return True
    return False


def parse_diagnostics(values: list[str]) -> dict[str, str]:
    diagnostics: dict[str, str] = {}
    for index, value in enumerate(values):
        if "=" in value:
            key, diagnostic_value = value.split("=", 1)
            diagnostics[key] = diagnostic_value
        else:
            diagnostics[f"diagnostic{index + 1}"] = value
    return diagnostics


def record_zero_status(diagnostics: dict[str, str], key: str, url: str, status: int) -> None:
    if status == 0:
        error_message = LAST_FETCH_ERRORS.get(url)
        diagnostics[key] = f"request failed: {url}: {error_message}" if error_message else f"request failed: {url}"


def add_diagnostic_context(diagnostics: dict[str, str], key: str, context: str) -> None:
    existing = diagnostics.get(key)
    if existing:
        if context not in existing:
            diagnostics[key] = f"{existing}; {context}"
    else:
        diagnostics[key] = context


def collection_failure_message(label: str, status: int, url: str) -> str:
    message = f"{label}: {http_class(status)} {url}"
    error_message = LAST_FETCH_ERRORS.get(url)
    if error_message:
        return f"{message}: {error_message}"
    return message


def optional_url(value: str | None, fallback: str) -> str:
    return value if value else fallback


def content_type_class(headers: dict[str, str]) -> str:
    value = " ".join(header_value for key, header_value in headers.items() if key.lower() == "content-type").lower()
    if "json" in value:
        return "json"
    if "xml" in value or "soap" in value:
        return "xml"
    if "html" in value:
        return "html"
    if "text" in value:
        return "text"
    return "unknown"


def xml_local_name(tag: str) -> str:
    return tag.rsplit("}", 1)[-1]


def payload_signature(body: str) -> dict[str, str]:
    text = body.strip()
    if not text:
        return {"payloadClass": "empty", "xmlRoot": ""}
    if text.startswith("{") or text.startswith("["):
        try:
            json.loads(text)
            return {"payloadClass": "json", "xmlRoot": ""}
        except json.JSONDecodeError:
            pass
    if text.startswith("<"):
        try:
            return {"payloadClass": "xml", "xmlRoot": xml_local_name(ET.fromstring(text).tag)}
        except ET.ParseError:
            return {"payloadClass": "text", "xmlRoot": ""}
    return {"payloadClass": "text", "xmlRoot": ""}


def html_signature(body: str) -> dict[str, bool]:
    text = body.lower()
    return {
        "hasHtmlRoot": "<html" in text,
        "hasForm": "<form" in text,
        "hasRequestVerificationToken": "__requestverificationtoken" in text,
        "hasUserNameInput": "username" in text,
        "hasPasswordInput": "password" in text and "type=\"password\"" in text,
        "hasSignInText": "sign in" in text or "signin" in text,
    }


def web_payload_class(headers: dict[str, str], body: str) -> str:
    if content_type_class(headers) == "html":
        return "html"
    text = body.lstrip().lower()
    if text.startswith("<!doctype html") or text.startswith("<html"):
        return "html"
    return payload_signature(body)["payloadClass"]


def web_client_content_probe(url: str, diagnostics: dict[str, str]) -> dict[str, Any]:
    status, headers, body = fetch_text(url)
    record_zero_status(diagnostics, "webClient.root", url, status)
    return {
        "httpClass": http_class(status),
        "contentTypeClass": content_type_class(headers),
        "payloadClass": web_payload_class(headers, body),
        **html_signature(body),
        "setCookiePresent": any(key.lower() == "set-cookie" for key in headers),
    }


def json_body_has_key(body: str, key: str) -> bool:
    try:
        payload = json.loads(body)
    except json.JSONDecodeError:
        return False
    return isinstance(payload, dict) and key in payload and bool(str(payload.get(key, "")).strip())


def web_client_csrf_probe(url: str, diagnostics: dict[str, str]) -> dict[str, Any]:
    status, headers, body = fetch_text(url)
    record_zero_status(diagnostics, "webClient.csrf", url, status)
    return {
        "httpClass": http_class(status),
        "contentTypeClass": content_type_class(headers),
        "payloadClass": payload_signature(body)["payloadClass"],
        "hasCsrfToken": json_body_has_key(body, "csrfToken"),
        "setCookiePresent": any(key.lower() == "set-cookie" for key in headers),
    }


def header_value(headers: dict[str, str], name: str) -> str:
    values = [value.strip() for key, value in headers.items() if key.lower() == name]
    return ", ".join(value for value in values if value)


def header_fingerprint(headers: dict[str, str]) -> dict[str, Any]:
    fingerprint_headers = {
        "server": header_value(headers, "server"),
        "xPoweredBy": header_value(headers, "x-powered-by"),
        "xAspNetVersion": header_value(headers, "x-aspnet-version"),
        "xAspNetMvcVersion": header_value(headers, "x-aspnetmvc-version"),
    }
    header_names = {
        key.lower()
        for key, value in headers.items()
        if value.strip()
        and key.lower()
        in {
            "server",
            "x-powered-by",
            "x-aspnet-version",
            "x-aspnetmvc-version",
        }
    }
    return {
        **fingerprint_headers,
        "fingerprintHeaderNames": sorted(header_names),
    }


def header_fingerprint_probe(
    url: str, auth: str | None, diagnostics: dict[str, str], name: str
) -> dict[str, Any]:
    status, headers = fetch_status(url, auth)
    record_zero_status(diagnostics, f"headers.{name}", url, status)
    return {
        "httpClass": http_class(status),
        **header_fingerprint(headers),
    }


def collect_headers(args: argparse.Namespace, diagnostics: dict[str, str]) -> dict[str, dict[str, Any]]:
    web_client_base_url = optional_url(args.web_client_url, join_url(args.base_url, "client/SignIn"))
    endpoints: dict[str, tuple[str, str | None]] = {
        "apiCompanies": (join_url(args.api_url, "companies"), args.auth),
        "odataCompany": (join_url(args.odata_url, "Company"), args.auth),
        "soapServices": (optional_url(args.soap_url, join_url(args.base_url, "WS/Services")), args.auth),
        "devMetadata": (join_url(args.dev_url, "metadata"), args.auth),
        "managementApiCompanies": (
            optional_url(args.management_api_url, join_url(args.base_url, "managementApi/v1.0/companies")),
            args.auth,
        ),
        "webClientRoot": (web_client_base_url, None),
        "webClientCsrf": (join_url(web_client_base_url, "client/csrf"), None),
    }
    return {
        name: header_fingerprint_probe(url, auth, diagnostics, name)
        for name, (url, auth) in endpoints.items()
    }


def response_error_signature(status: int, headers: dict[str, str], body: str) -> dict[str, Any]:
    signature = payload_signature(body)
    return {
        "httpClass": http_class(status),
        "contentTypeClass": content_type_class(headers),
        "payloadClass": web_payload_class(headers, body),
        "xmlRoot": signature["xmlRoot"],
        "bodyExcerpt": normalized_body_excerpt(status, body),
    }


def missing_route_probe(
    url: str, auth: str | None, diagnostics: dict[str, str], name: str
) -> dict[str, Any]:
    status, headers, body = fetch_text(url, auth)
    record_zero_status(diagnostics, f"missingRoutes.{name}", url, status)
    return response_error_signature(status, headers, body)


def collect_missing_routes(args: argparse.Namespace, diagnostics: dict[str, str]) -> dict[str, dict[str, Any]]:
    web_client_base_url = optional_url(args.web_client_url, join_url(args.base_url, "client/SignIn"))
    endpoints: dict[str, tuple[str, str | None]] = {
        "api": (join_url(args.api_url, "missing-parity-endpoint"), args.auth),
        "odata": (join_url(args.odata_url, "MissingParityEntity"), args.auth),
        "soap": (
            optional_url(args.soap_url, join_url(args.base_url, "WS/Services")).rsplit("/", 1)[0]
            + "/MissingParityService",
            args.auth,
        ),
        "dev": (join_url(args.dev_url, "missing-parity-endpoint"), args.auth),
        "webClient": (join_url(web_client_base_url, "missing-parity-route"), None),
    }
    return {
        name: missing_route_probe(url, auth, diagnostics, name)
        for name, (url, auth) in endpoints.items()
    }


def collect_web_client(args: argparse.Namespace, diagnostics: dict[str, str]) -> dict[str, Any]:
    web_client_base_url = optional_url(args.web_client_url, join_url(args.base_url, "client/SignIn"))
    return {
        "root": web_client_content_probe(web_client_base_url, diagnostics),
        "csrf": web_client_csrf_probe(join_url(web_client_base_url, "client/csrf"), diagnostics),
    }


def surface_probe(url: str, valid_auth: str, invalid_auth: str, diagnostics: dict[str, str], name: str) -> dict[str, Any]:
    valid_status, _ = fetch_status(url, valid_auth)
    invalid_status, _ = fetch_status(url, invalid_auth)
    record_zero_status(diagnostics, f"surface.{name}.valid", url, valid_status)
    record_zero_status(diagnostics, f"surface.{name}.invalid", url, invalid_status)
    return {
        "tcpOpen": tcp_open(url),
        "httpClass": http_class(valid_status),
        "requiresAuth": invalid_status in (401, 403),
    }


def websocket_probe(url: str, valid_auth: str, invalid_auth: str, diagnostics: dict[str, str]) -> dict[str, Any]:
    headers = {
        "Connection": "Upgrade",
        "Upgrade": "websocket",
        "Sec-WebSocket-Key": "dGhlIHNhbXBsZSBub25jZQ==",
        "Sec-WebSocket-Version": "13",
    }
    valid_status, _ = fetch_status(url, valid_auth, headers)
    invalid_status, _ = fetch_status(url, invalid_auth, headers)
    record_zero_status(diagnostics, "surface.clientWebSocket.valid", url, valid_status)
    record_zero_status(diagnostics, "surface.clientWebSocket.invalid", url, invalid_status)
    return {
        "tcpOpen": tcp_open(url),
        "httpClass": http_class(valid_status),
        "requiresAuth": invalid_status in (401, 403),
        "websocketUpgrade": valid_status == 101,
    }


def collect_surface(args: argparse.Namespace, diagnostics: dict[str, str]) -> dict[str, dict[str, Any]]:
    endpoints = {
        "management": optional_url(args.management_url, join_url(args.base_url, "Management")),
        "webClient": optional_url(args.web_client_url, join_url(args.base_url, "client/SignIn")),
        "clientSignIn": optional_url(args.web_client_url, join_url(args.base_url, "client/SignIn")),
        "soap": optional_url(args.soap_url, join_url(args.base_url, "WS/Services")),
        "odata": join_url(args.odata_url, "Company"),
        "api": join_url(args.api_url, "companies"),
        "dev": join_url(args.dev_url, "metadata"),
        "managementApi": optional_url(args.management_api_url, join_url(args.base_url, "managementApi/v1.0/companies")),
    }
    surface = {name: surface_probe(url, args.auth, args.invalid_auth, diagnostics, name) for name, url in endpoints.items()}
    surface["clientWebSocket"] = websocket_probe(
        optional_url(args.client_websocket_url, join_url(args.base_url, "client/csh")),
        args.auth,
        args.invalid_auth,
        diagnostics,
    )
    return surface


def auth_endpoints(args: argparse.Namespace) -> dict[str, str]:
    return {
        "api": join_url(args.api_url, "companies"),
        "odata": join_url(args.odata_url, "Company"),
        "dev": join_url(args.dev_url, "metadata"),
    }


def collect_auth(args: argparse.Namespace, diagnostics: dict[str, str]) -> dict[str, Any]:
    endpoint_details: dict[str, dict[str, Any]] = {}
    auth_scheme = "unknown"
    for name, url in auth_endpoints(args).items():
        valid_status, _ = fetch_status(url, args.auth)
        invalid_status, invalid_headers = fetch_status(url, args.invalid_auth)
        record_zero_status(diagnostics, f"auth.{name}.valid", url, valid_status)
        record_zero_status(diagnostics, f"auth.{name}.invalid", url, invalid_status)
        scheme = auth_scheme_class(invalid_headers)
        if auth_scheme == "unknown" and scheme != "unknown":
            auth_scheme = scheme
        endpoint_details[name] = {
            "validHttpClass": http_class(valid_status),
            "invalidHttpClass": http_class(invalid_status),
            "validAccepted": 200 <= valid_status <= 299,
            "invalidRejected": invalid_status in (401, 403),
        }
    return {
        "validCredentialsAccepted": all(detail["validAccepted"] for detail in endpoint_details.values()),
        "invalidCredentialsRejected": all(detail["invalidRejected"] for detail in endpoint_details.values()),
        "authSchemeClass": auth_scheme,
        "endpoints": endpoint_details,
        "invalidResponses": collect_auth_invalid_responses(args, diagnostics),
    }


def normalized_body_excerpt(status: int, body: str) -> str:
    if not 400 <= status <= 599:
        return ""
    text = " ".join(body.split())
    text = GUID_PATTERN.sub("<GUID>", text)
    return text[:ERROR_BODY_EXCERPT_LIMIT]


def auth_invalid_response_probe(url: str, invalid_auth: str, diagnostics: dict[str, str], name: str) -> dict[str, Any]:
    status, headers, body = fetch_text(url, invalid_auth)
    record_zero_status(diagnostics, f"auth.invalidResponses.{name}", url, status)
    signature = payload_signature(body)
    return {
        "httpClass": http_class(status),
        "contentTypeClass": content_type_class(headers),
        "payloadClass": signature["payloadClass"],
        "xmlRoot": signature["xmlRoot"],
        "authSchemeClass": auth_scheme_class(headers),
        "challengePresent": has_header(headers, "www-authenticate"),
        "bodyExcerpt": normalized_body_excerpt(status, body),
    }


def collect_auth_invalid_responses(args: argparse.Namespace, diagnostics: dict[str, str]) -> dict[str, dict[str, Any]]:
    return {
        name: auth_invalid_response_probe(url, args.invalid_auth, diagnostics, name)
        for name, url in auth_endpoints(args).items()
    }


def collect_company(args: argparse.Namespace, diagnostics: dict[str, str]) -> dict[str, Any]:
    api_url = join_url(args.api_url, "companies")
    odata_url = join_url(args.odata_url, "Company")
    api_status, api_payload = fetch_json(api_url, args.auth)
    odata_status, odata_payload = fetch_json(odata_url, args.auth)
    record_zero_status(diagnostics, "company.api", api_url, api_status)
    record_zero_status(diagnostics, "company.odata", odata_url, odata_status)
    api_companies = extract_items(api_payload)
    odata_companies = extract_items(odata_payload)
    first_company = api_companies[0] if api_companies else (odata_companies[0] if odata_companies else {})
    first_api_company = api_companies[0] if api_companies else {}
    first_odata_company = odata_companies[0] if odata_companies else {}
    return {
        "companyCountAtLeastOne": bool(api_companies or odata_companies),
        "apiCompanyCount": len(api_companies),
        "odataCompanyCount": len(odata_companies),
        "firstCompanyName": company_name(first_company),
        "apiFirstCompanyIdPresent": item_id_present(first_api_company),
        "odataFirstCompanyIdPresent": item_id_present(first_odata_company),
        "apiCompanyShape": sorted(api_companies[0].keys()) if api_companies else [],
        "odataCompanyShape": sorted(odata_companies[0].keys()) if odata_companies else [],
    }


def collection_probe(url: str, auth: str, diagnostics: dict[str, str], name: str) -> dict[str, Any]:
    status, payload = fetch_json(url, auth)
    record_zero_status(diagnostics, f"integration.{name}", url, status)
    items = extract_items(payload)
    return {
        "httpClass": http_class(status),
        "readSucceeded": 200 <= status <= 299,
        "itemCount": len(items),
        "firstItemKeys": sorted(items[0].keys()) if items else [],
    }


def soap_services_probe(url: str, auth: str, diagnostics: dict[str, str]) -> dict[str, Any]:
    status, headers, body = fetch_text(url, auth)
    record_zero_status(diagnostics, "integration.soapServices", url, status)
    return {
        "httpClass": http_class(status),
        "readSucceeded": 200 <= status <= 299,
        "contentTypeClass": content_type_class(headers),
        **payload_signature(body),
    }


def metadata_text_signature(body: str) -> dict[str, bool]:
    text = body.lower()
    return {
        "hasEdmx": "edmx:" in text or "<edmx" in text,
        "hasEntityContainer": "entitycontainer" in text,
        "hasCompany": "company" in text or "companies" in text,
        "hasCustomer": "customer" in text or "customers" in text,
    }


def metadata_probe(url: str, auth: str, diagnostics: dict[str, str], name: str) -> dict[str, Any]:
    status, headers, body = fetch_text(url, auth)
    record_zero_status(diagnostics, f"metadata.{name}", url, status)
    return {
        "httpClass": http_class(status),
        "readSucceeded": 200 <= status <= 299,
        "contentTypeClass": content_type_class(headers),
        **payload_signature(body),
        **metadata_text_signature(body),
    }


def collect_metadata(args: argparse.Namespace, diagnostics: dict[str, str]) -> dict[str, dict[str, Any]]:
    return {
        "api": metadata_probe(join_url(args.api_url, "$metadata"), args.auth, diagnostics, "api"),
        "odata": metadata_probe(join_url(args.odata_url, "$metadata"), args.auth, diagnostics, "odata"),
    }


def customer_crud_probe(args: argparse.Namespace, diagnostics: dict[str, str], company_id: Any | None) -> dict[str, Any]:
    result = {
        "roundTripSucceeded": False,
        "createHttpClass": "000",
        "readHttpClass": "000",
        "updateHttpClass": "000",
        "deleteHttpClass": "000",
        "readAfterDeleteHttpClass": "000",
        "createdIdPresent": False,
        "readBackSucceeded": False,
        "updateEchoed": False,
        "deleteVerified": False,
    }
    if company_id is None:
        diagnostics["integration.customerCrud"] = "customer CRUD skipped: company id unavailable"
        return result

    customers_url = join_url(args.api_url, f"{company_segment(company_id)}/customers")
    version_token = "".join(part for part in str(args.bc_version) if part.isalnum()) or "unknown"
    create_payload = {
        "displayName": f"BC Parity Probe {version_token}",
        "type": "Company",
    }
    create_status, create_payload_response = request_json("POST", customers_url, args.auth, create_payload)
    record_zero_status(diagnostics, "integration.customerCrud.create", customers_url, create_status)
    result["createHttpClass"] = http_class(create_status)

    customer_id = str(create_payload_response.get("id", "")).strip()
    result["createdIdPresent"] = bool(customer_id)
    if not customer_id:
        return result

    customer_url = join_url(args.api_url, f"{company_segment(company_id)}/customers({parse.quote(customer_id, safe='')})")
    read_status, read_payload = fetch_json(customer_url, args.auth)
    record_zero_status(diagnostics, "integration.customerCrud.read", customer_url, read_status)
    result["readHttpClass"] = http_class(read_status)
    result["readBackSucceeded"] = 200 <= read_status <= 299 and str(read_payload.get("id", "")).strip() == customer_id

    updated_name = f"BC Parity Probe {version_token} Updated"
    update_status, update_payload = request_json(
        "PATCH",
        customer_url,
        args.auth,
        {"displayName": updated_name},
        headers={"If-Match": "*"},
    )
    record_zero_status(diagnostics, "integration.customerCrud.update", customer_url, update_status)
    result["updateHttpClass"] = http_class(update_status)
    result["updateEchoed"] = 200 <= update_status <= 299 and update_payload.get("displayName") == updated_name

    delete_status, _ = request_json("DELETE", customer_url, args.auth, headers={"If-Match": "*"})
    record_zero_status(diagnostics, "integration.customerCrud.delete", customer_url, delete_status)
    result["deleteHttpClass"] = http_class(delete_status)

    read_after_delete_status, _ = fetch_json(customer_url, args.auth)
    record_zero_status(diagnostics, "integration.customerCrud.readAfterDelete", customer_url, read_after_delete_status)
    result["readAfterDeleteHttpClass"] = http_class(read_after_delete_status)
    result["deleteVerified"] = 400 <= read_after_delete_status <= 499
    result["roundTripSucceeded"] = (
        200 <= create_status <= 299
        and result["readBackSucceeded"]
        and result["updateEchoed"]
        and 200 <= delete_status <= 299
        and result["deleteVerified"]
    )
    return result


def collect_integration(args: argparse.Namespace, diagnostics: dict[str, str], company_id: Any | None = None) -> dict[str, Any]:
    return {
        "apiCompanies": collection_probe(join_url(args.api_url, "companies"), args.auth, diagnostics, "apiCompanies"),
        "odataCompany": collection_probe(join_url(args.odata_url, "Company"), args.auth, diagnostics, "odataCompany"),
        "soapServices": soap_services_probe(optional_url(args.soap_url, join_url(args.base_url, "WS/Services")), args.auth, diagnostics),
        "apiCustomerCrud": customer_crud_probe(args, diagnostics, company_id),
    }


def first_company_id(args: argparse.Namespace, diagnostics: dict[str, str]) -> Any | None:
    api_url = join_url(args.api_url, "companies")
    api_status, api_payload = fetch_json(api_url, args.auth)
    record_zero_status(diagnostics, "automation.company", api_url, api_status)
    if not 200 <= api_status <= 299:
        add_diagnostic_context(
            diagnostics,
            "automation.company",
            collection_failure_message("company id collection failed", api_status, api_url),
        )
        return None
    companies = extract_items(api_payload)
    if not companies:
        diagnostics["automation.company"] = "company id collection failed: no companies returned"
        return None
    company_id = companies[0].get("id")
    if not company_id:
        diagnostics["automation.company"] = "company id collection failed: first company has no id"
        return None
    return company_id


def collect_apps(args: argparse.Namespace, diagnostics: dict[str, str], company_id: Any | None) -> dict[str, Any]:
    if company_id is None:
        diagnostics["apps.collection"] = "extension collection skipped: company id unavailable"
        return {
            "microsoftApps": [],
            "customApps": [],
            "testFrameworkPresent": False,
            "collectionSucceeded": False,
            "httpClass": "000",
        }
    url = join_url(automation_base_url(args.api_url), f"{company_segment(company_id)}/extensions")
    status, payload = fetch_json(url, args.auth)
    record_zero_status(diagnostics, "apps.collection", url, status)
    items = extract_items(payload)
    collection_succeeded = 200 <= status <= 299
    if not collection_succeeded:
        add_diagnostic_context(diagnostics, "apps.collection", collection_failure_message("extension collection failed", status, url))
    microsoft_apps, custom_apps, test_framework_present = split_apps(items if collection_succeeded else [])
    return {
        "microsoftApps": microsoft_apps,
        "customApps": custom_apps,
        "testFrameworkPresent": test_framework_present,
        "collectionSucceeded": collection_succeeded,
        "httpClass": http_class(status),
    }


def collect_user_permissions(
    args: argparse.Namespace, diagnostics: dict[str, str], company_id: Any, users: list[dict[str, Any]]
) -> tuple[int, bool]:
    super_count = 0
    permission_collection_succeeded = True
    base_url = automation_base_url(args.api_url)
    for item in users:
        if not user_enabled(item):
            continue
        security_id = user_security_id(item)
        if not security_id:
            diagnostics["users.permissions"] = "permission collection failed: enabled user has no security id"
            permission_collection_succeeded = False
            break
        url = join_url(base_url, f"{company_segment(company_id)}/users({parse.quote(security_id, safe='')})/userPermissions")
        status, payload = fetch_json(url, args.auth)
        record_zero_status(diagnostics, f"users.permissions.{security_id}", url, status)
        if not 200 <= status <= 299:
            add_diagnostic_context(
                diagnostics,
                "users.permissions",
                collection_failure_message("permission collection failed", status, url),
            )
            permission_collection_succeeded = False
            break
        if any(is_super_permission(permission) for permission in extract_items(payload)):
            super_count += 1
    return super_count, permission_collection_succeeded


def collect_users(args: argparse.Namespace, diagnostics: dict[str, str], company_id: Any | None) -> dict[str, Any]:
    auth_user_name = args.auth.split(":", 1)[0]
    if company_id is None:
        diagnostics["users.collection"] = "user collection skipped: company id unavailable"
        return {
            "authUserName": auth_user_name,
            "enabledSuperUserCount": 0,
            "knownUserNames": [],
            "collectionSucceeded": False,
            "httpClass": "000",
            "permissionCollectionSucceeded": False,
        }

    url = join_url(automation_base_url(args.api_url), f"{company_segment(company_id)}/users")
    status, payload = fetch_json(url, args.auth)
    record_zero_status(diagnostics, "users.collection", url, status)
    collection_succeeded = 200 <= status <= 299
    users = extract_items(payload) if collection_succeeded else []
    if collection_succeeded:
        enabled_super_count, permission_collection_succeeded = collect_user_permissions(args, diagnostics, company_id, users)
    else:
        add_diagnostic_context(diagnostics, "users.collection", collection_failure_message("user collection failed", status, url))
        enabled_super_count = 0
        permission_collection_succeeded = False

    if not permission_collection_succeeded:
        enabled_super_count = 0

    known_user_names = sorted({name for name in (user_name(item) for item in users) if name})
    return {
        "authUserName": auth_user_name,
        "enabledSuperUserCount": enabled_super_count,
        "knownUserNames": known_user_names,
        "collectionSucceeded": collection_succeeded,
        "httpClass": http_class(status),
        "permissionCollectionSucceeded": permission_collection_succeeded,
    }


def collect_dev(args: argparse.Namespace, diagnostics: dict[str, str]) -> dict[str, Any]:
    metadata_url = join_url(args.dev_url, "metadata")
    packages_url = join_url(args.dev_url, "packages")
    metadata_status, metadata = fetch_json(metadata_url, args.auth)
    packages_status, _ = fetch_status(packages_url, args.auth)
    record_zero_status(diagnostics, "dev.metadata", metadata_url, metadata_status)
    record_zero_status(diagnostics, "dev.packages", packages_url, packages_status)
    dev_api_major = parse_dev_api_major(metadata)
    return {
        "metadataReachable": 200 <= metadata_status <= 299,
        "packagesEndpointReachable": 200 <= packages_status <= 299,
        "devApiMajor": dev_api_major,
        "supportsTestRunnerHub": bool(dev_api_major and dev_api_major >= 7),
    }


def collect_tests(test_output_path: Path, runner_kind: str, diagnostics: dict[str, str]) -> dict[str, Any]:
    try:
        output = test_output_path.read_text(encoding="utf-8")
    except OSError as exc:
        diagnostics["testOutputError"] = str(exc)
        output = ""
    return summarize_test_output(output, runner_kind)


def build_contract(args: argparse.Namespace) -> dict[str, Any]:
    diagnostics = parse_diagnostics(args.diagnostic)
    auth = collect_auth(args, diagnostics)
    company_id = first_company_id(args, diagnostics)
    return {
        "schemaVersion": 1,
        "platform": args.platform,
        "bcVersionInput": args.bc_version,
        "surface": collect_surface(args, diagnostics),
        "auth": auth,
        "company": collect_company(args, diagnostics),
        "headers": collect_headers(args, diagnostics),
        "missingRoutes": collect_missing_routes(args, diagnostics),
        "webClient": collect_web_client(args, diagnostics),
        "integration": collect_integration(args, diagnostics, company_id),
        "metadata": collect_metadata(args, diagnostics),
        "dev": collect_dev(args, diagnostics),
        "tests": collect_tests(args.test_output, args.runner_kind, diagnostics),
        "apps": collect_apps(args, diagnostics, company_id),
        "users": collect_users(args, diagnostics, company_id),
        "diagnostics": diagnostics,
    }


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(description="Collect a normalized Business Central parity contract")
    parser.add_argument("--platform", required=True, choices=("linux", "windows"))
    parser.add_argument("--bc-version", required=True)
    parser.add_argument("--base-url", required=True)
    parser.add_argument("--management-url")
    parser.add_argument("--management-api-url")
    parser.add_argument("--soap-url")
    parser.add_argument("--web-client-url")
    parser.add_argument("--client-websocket-url")
    parser.add_argument("--dev-url", required=True)
    parser.add_argument("--odata-url", required=True)
    parser.add_argument("--api-url", required=True)
    parser.add_argument("--auth", required=True)
    parser.add_argument("--invalid-auth", required=True)
    parser.add_argument("--test-output", required=True, type=Path)
    parser.add_argument("--runner-kind", required=True, choices=RUNNER_KINDS)
    parser.add_argument("--diagnostic", action="append", default=[])
    parser.add_argument("--out", required=True, type=Path)
    args = parser.parse_args(argv)

    contract = build_contract(args)
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(contract, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
