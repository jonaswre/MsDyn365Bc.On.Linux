#!/usr/bin/env python3
from __future__ import annotations

import argparse
import base64
import json
import socket
import sys
from pathlib import Path
from typing import Any
from urllib import error, parse, request


LAST_FETCH_ERRORS: dict[str, str] = {}
RUNNER_KINDS = ("websocket", "bccontainerhelper", "startup-debug")


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
    match = re.search(r"Test codeunits:\s*([^\n]+)", output)
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
        LAST_FETCH_ERRORS.pop(url, None)
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


def auth_scheme_class(headers: dict[str, str]) -> str:
    value = " ".join(header_value for key, header_value in headers.items() if key.lower() == "www-authenticate").lower()
    if "basic" in value:
        return "basic"
    if "userpassword" in value or "navuserpassword" in value:
        return "userpassword"
    return "unknown"


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
        "name": str(item.get("name", "")),
        "id": str(item.get("packageId") or item.get("id") or item.get("appId") or ""),
        "version": str(item.get("version") or item.get("appVersion") or item.get("packageVersion") or ""),
    }


def app_sort_key(item: dict[str, str]) -> tuple[str, str, str, str]:
    return (item.get("publisher", ""), item.get("name", ""), item.get("id", ""), item.get("version", ""))


def has_test_framework_signal(app: dict[str, str]) -> bool:
    name = normalize_company_name(app.get("name", "")).lower()
    substring_signals = ("test runner", "library assert", "library variable storage", "permissions mock")
    return name == "any" or any(signal in name for signal in substring_signals)


def split_apps(items: list[dict[str, Any]]) -> tuple[list[dict[str, str]], list[dict[str, str]], bool]:
    normalized = [normalize_extension(item) for item in items]
    microsoft_apps = sorted((item for item in normalized if item["publisher"] == "Microsoft"), key=app_sort_key)
    custom_apps = sorted((item for item in normalized if item["publisher"] != "Microsoft"), key=app_sort_key)
    return microsoft_apps, custom_apps, any(has_test_framework_signal(item) for item in normalized)


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
    if status == 0 and error_message:
        return f"{message}: {error_message}"
    return message


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
        "management": join_url(args.base_url, "Management"),
        "webClient": join_url(args.base_url, "client/SignIn"),
        "clientSignIn": join_url(args.base_url, "client/SignIn"),
        "soap": join_url(args.base_url, "WS/Services"),
        "odata": join_url(args.odata_url, "Company"),
        "api": join_url(args.api_url, "companies"),
        "dev": join_url(args.dev_url, "metadata"),
        "managementApi": join_url(args.base_url, "managementApi/v1.0/companies"),
    }
    surface = {name: surface_probe(url, args.auth, args.invalid_auth, diagnostics, name) for name, url in endpoints.items()}
    surface["clientWebSocket"] = websocket_probe(join_url(args.base_url, "client/csh"), args.auth, args.invalid_auth, diagnostics)
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
    return {
        "companyCountAtLeastOne": bool(api_companies or odata_companies),
        "firstCompanyName": company_name(first_company),
        "apiCompanyShape": sorted(api_companies[0].keys()) if api_companies else [],
        "odataCompanyShape": sorted(odata_companies[0].keys()) if odata_companies else [],
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
