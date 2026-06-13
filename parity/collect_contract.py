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
    versions = metadata.get("supportedVersions")
    if not isinstance(versions, list):
        return None
    majors = []
    for item in versions:
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
            return response.status, json.loads(response.read().decode("utf-8"))
    except error.HTTPError as exc:
        return exc.code, {}
    except Exception:
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
            return response.status, dict(response.headers.items())
    except error.HTTPError as exc:
        return exc.code, dict(exc.headers.items())
    except Exception:
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
        diagnostics[key] = f"request failed: {url}"


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


def collect_auth(args: argparse.Namespace, diagnostics: dict[str, str]) -> dict[str, Any]:
    url = join_url(args.api_url, "companies")
    valid_status, _ = fetch_status(url, args.auth)
    invalid_status, invalid_headers = fetch_status(url, args.invalid_auth)
    record_zero_status(diagnostics, "auth.valid", url, valid_status)
    record_zero_status(diagnostics, "auth.invalid", url, invalid_status)
    return {
        "validCredentialsAccepted": 200 <= valid_status <= 299,
        "invalidCredentialsRejected": invalid_status in (401, 403),
        "authSchemeClass": auth_scheme_class(invalid_headers),
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
    return {
        "schemaVersion": 1,
        "platform": args.platform,
        "bcVersionInput": args.bc_version,
        "surface": collect_surface(args, diagnostics),
        "auth": auth,
        "company": collect_company(args, diagnostics),
        "dev": collect_dev(args, diagnostics),
        "tests": collect_tests(args.test_output, args.runner_kind, diagnostics),
        "apps": {
            "microsoftApps": [],
            "customApps": [],
            "testFrameworkPresent": False,
        },
        "users": {
            "authUserName": args.auth.split(":", 1)[0],
            "enabledSuperUserCount": 1 if auth["validCredentialsAccepted"] else 0,
            "knownUserNames": [args.auth.split(":", 1)[0].upper()],
        },
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
    parser.add_argument("--runner-kind", required=True, choices=("websocket", "bccontainerhelper"))
    parser.add_argument("--diagnostic", action="append", default=[])
    parser.add_argument("--out", required=True, type=Path)
    args = parser.parse_args(argv)

    contract = build_contract(args)
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(contract, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
