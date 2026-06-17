import unittest
from io import BytesIO
from types import SimpleNamespace
from urllib.error import HTTPError

import parity.collect_contract as collect_contract
from parity.collect_contract import (
    RUNNER_KINDS,
    automation_base_url,
    http_class,
    is_super_permission,
    normalize_company_name,
    normalize_extension,
    parse_dev_api_major,
    split_apps,
    summarize_test_output,
)


class CollectContractTests(unittest.TestCase):
    def test_startup_debug_is_a_supported_runner_kind(self):
        self.assertIn("startup-debug", RUNNER_KINDS)

    def test_http_class_normalizes_status_codes(self):
        self.assertEqual("2xx", http_class(200))
        self.assertEqual("3xx", http_class(302))
        self.assertEqual("4xx", http_class(401))
        self.assertEqual("5xx", http_class(503))
        self.assertEqual("000", http_class(0))

    def test_company_name_strips_whitespace(self):
        self.assertEqual("CRONUS International Ltd.", normalize_company_name("  CRONUS International Ltd.  "))

    def test_dev_api_major_reads_metadata_key(self):
        metadata = {"supportedVersions": [{"apiVersion": "7.0"}, {"apiVersion": "6.0"}]}
        self.assertEqual(7, parse_dev_api_major(metadata))

    def test_dev_api_major_returns_none_for_missing_shape(self):
        self.assertIsNone(parse_dev_api_major({"value": []}))

    def test_dev_api_major_returns_none_for_non_dict_metadata(self):
        self.assertIsNone(parse_dev_api_major([]))

    def test_dev_api_major_skips_malformed_supported_versions(self):
        metadata = {"supportedVersions": ["7.0", None, {"apiVersion": "not-a-version"}]}
        self.assertIsNone(parse_dev_api_major(metadata))

    def test_dev_api_major_uses_valid_items_when_malformed_items_exist(self):
        metadata = {"supportedVersions": ["7.0", {"apiVersion": "8.0"}]}
        self.assertEqual(8, parse_dev_api_major(metadata))

    def test_automation_base_url_replaces_standard_api_suffix(self):
        self.assertEqual(
            "http://localhost:7052/BC/api/microsoft/automation/v2.0",
            automation_base_url("http://localhost:7052/BC/api/v2.0"),
        )

    def test_extension_normalization_and_split_are_stable(self):
        extensions = [
            {"publisher": "Partner", "name": "Custom", "appId": "b", "packageVersion": "1.0.0.0"},
            {"publisher": "Microsoft", "name": "Library Assert", "packageId": "a", "version": "2.0.0.0"},
        ]
        microsoft_apps, custom_apps, test_framework_present = split_apps(extensions)

        self.assertEqual([normalize_extension(extensions[1])], microsoft_apps)
        self.assertEqual([normalize_extension(extensions[0])], custom_apps)
        self.assertTrue(test_framework_present)

    def test_test_framework_any_signal_requires_exact_app_name(self):
        _, _, company_hub_present = split_apps([{"publisher": "Microsoft", "name": "Company Hub"}])
        _, _, any_present = split_apps([{"publisher": "Microsoft", "name": " Any "}])

        self.assertFalse(company_hub_present)
        self.assertTrue(any_present)

    def test_super_permission_matches_role_or_permission_set(self):
        self.assertTrue(is_super_permission({"roleId": "super"}))
        self.assertTrue(is_super_permission({"permissionSetId": "SUPER"}))
        self.assertFalse(is_super_permission({"roleId": "D365 BASIC"}))

    def test_collect_auth_probes_api_odata_and_dev_conservatively(self):
        args = SimpleNamespace(
            api_url="http://localhost:7052/BC/api/v2.0",
            odata_url="http://localhost:7048/BC/ODataV4",
            dev_url="http://localhost:7049/BC/dev",
            auth="admin:admin",
            invalid_auth="not-admin:not-admin",
        )

        def fake_fetch_status(url, auth=None, headers=None, timeout=15):
            del headers, timeout
            if auth == args.auth:
                if url.endswith("/metadata"):
                    return 500, {}
                return 200, {}
            if url.endswith("/companies"):
                return 401, {"WWW-Authenticate": "Basic realm=\"BC\""}
            if url.endswith("/Company"):
                return 403, {}
            return 401, {}

        original_fetch_status = collect_contract.fetch_status
        try:
            collect_contract.fetch_status = fake_fetch_status
            result = collect_contract.collect_auth(args, {})
        finally:
            collect_contract.fetch_status = original_fetch_status

        self.assertFalse(result["validCredentialsAccepted"])
        self.assertTrue(result["invalidCredentialsRejected"])
        self.assertEqual("basic", result["authSchemeClass"])
        self.assertEqual({"api", "odata", "dev"}, set(result["endpoints"]))
        self.assertEqual("5xx", result["endpoints"]["dev"]["validHttpClass"])

    def test_collect_surface_includes_web_client(self):
        args = SimpleNamespace(
            base_url="http://localhost:7046/BC",
            api_url="http://localhost:7052/BC/api/v2.0",
            odata_url="http://localhost:7048/BC/ODataV4",
            dev_url="http://localhost:7049/BC/dev",
            auth="admin:admin",
            invalid_auth="not-admin:not-admin",
        )
        probed = {}

        def fake_surface_probe(url, valid_auth, invalid_auth, diagnostics, name):
            del valid_auth, invalid_auth, diagnostics
            probed[name] = url
            return {"tcpOpen": True, "httpClass": "2xx", "requiresAuth": True}

        def fake_websocket_probe(url, valid_auth, invalid_auth, diagnostics):
            del valid_auth, invalid_auth, diagnostics
            probed["clientWebSocket"] = url
            return {"tcpOpen": True, "httpClass": "2xx", "requiresAuth": True, "websocketUpgrade": False}

        original_surface_probe = collect_contract.surface_probe
        original_websocket_probe = collect_contract.websocket_probe
        try:
            collect_contract.surface_probe = fake_surface_probe
            collect_contract.websocket_probe = fake_websocket_probe
            surface = collect_contract.collect_surface(args, {})
        finally:
            collect_contract.surface_probe = original_surface_probe
            collect_contract.websocket_probe = original_websocket_probe

        self.assertIn("webClient", surface)
        self.assertEqual("http://localhost:7046/BC/client/SignIn", probed["webClient"])

    def test_failed_user_collection_returns_empty_discovered_user_fields(self):
        args = SimpleNamespace(api_url="http://localhost:7052/BC/api/v2.0", auth="admin:admin")
        diagnostics = {}

        def fake_fetch_json(url, auth, timeout=15):
            del url, auth, timeout
            return 500, {}

        original_fetch_json = collect_contract.fetch_json
        try:
            collect_contract.fetch_json = fake_fetch_json
            result = collect_contract.collect_users(args, diagnostics, "company-id")
        finally:
            collect_contract.fetch_json = original_fetch_json

        self.assertFalse(result["collectionSucceeded"])
        self.assertFalse(result["permissionCollectionSucceeded"])
        self.assertEqual(0, result["enabledSuperUserCount"])
        self.assertEqual([], result["knownUserNames"])
        self.assertEqual("admin", result["authUserName"])
        self.assertIn("users.collection", diagnostics)

    def test_permission_collection_failure_does_not_synthesize_super_count(self):
        args = SimpleNamespace(api_url="http://localhost:7052/BC/api/v2.0", auth="admin:admin")
        diagnostics = {}

        def fake_fetch_json(url, auth, timeout=15):
            del auth, timeout
            if url.endswith("/users"):
                return 200, {"value": [{"userName": "ADMIN", "userSecurityId": "user-1", "enabled": True}]}
            return 500, {}

        original_fetch_json = collect_contract.fetch_json
        try:
            collect_contract.fetch_json = fake_fetch_json
            result = collect_contract.collect_users(args, diagnostics, "company-id")
        finally:
            collect_contract.fetch_json = original_fetch_json

        self.assertTrue(result["collectionSucceeded"])
        self.assertFalse(result["permissionCollectionSucceeded"])
        self.assertEqual(0, result["enabledSuperUserCount"])
        self.assertEqual(["ADMIN"], result["knownUserNames"])
        self.assertIn("users.permissions", diagnostics)

    def test_permission_collection_failure_discards_partial_super_count(self):
        args = SimpleNamespace(api_url="http://localhost:7052/BC/api/v2.0", auth="admin:admin")
        diagnostics = {}

        def fake_fetch_json(url, auth, timeout=15):
            del auth, timeout
            if url.endswith("/users"):
                return 200, {
                    "value": [
                        {"userName": "ADMIN", "userSecurityId": "user-1", "enabled": True},
                        {"userName": "ALICE", "userSecurityId": "user-2", "enabled": True},
                    ]
                }
            if "user-1" in url:
                return 200, {"value": [{"permissionSetId": "SUPER"}]}
            return 500, {}

        original_fetch_json = collect_contract.fetch_json
        try:
            collect_contract.fetch_json = fake_fetch_json
            result = collect_contract.collect_users(args, diagnostics, "company-id")
        finally:
            collect_contract.fetch_json = original_fetch_json

        self.assertFalse(result["permissionCollectionSucceeded"])
        self.assertEqual(0, result["enabledSuperUserCount"])
        self.assertEqual(["ADMIN", "ALICE"], result["knownUserNames"])
        self.assertIn("users.permissions", diagnostics)

    def test_record_zero_status_includes_last_fetch_exception_message(self):
        url = "http://localhost:7049/BC/dev/metadata"

        def fake_urlopen(req, timeout=15):
            del req, timeout
            raise RuntimeError("connection exploded")

        original_urlopen = collect_contract.request.urlopen
        collect_contract.LAST_FETCH_ERRORS.clear()
        try:
            collect_contract.request.urlopen = fake_urlopen
            status, _ = collect_contract.fetch_status(url, "admin:admin")
        finally:
            collect_contract.request.urlopen = original_urlopen

        diagnostics = {}
        collect_contract.record_zero_status(diagnostics, "dev.metadata", url, status)

        self.assertEqual(0, status)
        self.assertIn("connection exploded", diagnostics["dev.metadata"])

    def test_fetch_json_preserves_http_error_body_diagnostic(self):
        url = "http://localhost:7052/BC/api/microsoft/automation/v2.0/companies(id)/extensions"
        body = b'{"error":{"code":"BadRequest","message":"No HTTP resource was found."}}'

        def fake_urlopen(req, timeout=15):
            del req, timeout
            raise HTTPError(url, 404, "Not Found", {}, BytesIO(body))

        original_urlopen = collect_contract.request.urlopen
        collect_contract.LAST_FETCH_ERRORS.clear()
        try:
            collect_contract.request.urlopen = fake_urlopen
            status, payload = collect_contract.fetch_json(url, "admin:admin")
        finally:
            collect_contract.request.urlopen = original_urlopen

        self.assertEqual(404, status)
        self.assertEqual({}, payload)
        self.assertIn("HTTP 404", collect_contract.LAST_FETCH_ERRORS[url])
        self.assertIn("No HTTP resource was found", collect_contract.LAST_FETCH_ERRORS[url])

    def test_collection_failure_message_includes_http_error_body(self):
        url = "http://localhost:7052/BC/api/microsoft/automation/v2.0/companies(id)/extensions"
        collect_contract.LAST_FETCH_ERRORS[url] = "HTTP 404: No HTTP resource was found."

        message = collect_contract.collection_failure_message("extension collection failed", 404, url)

        self.assertIn("4xx", message)
        self.assertIn("No HTTP resource was found", message)

    def test_collect_apps_preserves_fetch_exception_diagnostics(self):
        args = SimpleNamespace(api_url="http://localhost:7052/BC/api/v2.0", auth="admin:admin")
        diagnostics = {}

        def fake_fetch_json(url, auth, timeout=15):
            del auth, timeout
            collect_contract.LAST_FETCH_ERRORS[url] = "RuntimeError: connection exploded"
            return 0, {}

        original_fetch_json = collect_contract.fetch_json
        collect_contract.LAST_FETCH_ERRORS.clear()
        try:
            collect_contract.fetch_json = fake_fetch_json
            result = collect_contract.collect_apps(args, diagnostics, "company-id")
        finally:
            collect_contract.fetch_json = original_fetch_json

        self.assertFalse(result["collectionSucceeded"])
        self.assertIn("connection exploded", diagnostics["apps.collection"])

    def test_collect_users_preserves_fetch_exception_diagnostics(self):
        args = SimpleNamespace(api_url="http://localhost:7052/BC/api/v2.0", auth="admin:admin")
        diagnostics = {}

        def fake_fetch_json(url, auth, timeout=15):
            del auth, timeout
            collect_contract.LAST_FETCH_ERRORS[url] = "RuntimeError: connection exploded"
            return 0, {}

        original_fetch_json = collect_contract.fetch_json
        collect_contract.LAST_FETCH_ERRORS.clear()
        try:
            collect_contract.fetch_json = fake_fetch_json
            result = collect_contract.collect_users(args, diagnostics, "company-id")
        finally:
            collect_contract.fetch_json = original_fetch_json

        self.assertFalse(result["collectionSucceeded"])
        self.assertIn("connection exploded", diagnostics["users.collection"])

    def test_permission_failure_preserves_fetch_exception_diagnostics(self):
        args = SimpleNamespace(api_url="http://localhost:7052/BC/api/v2.0", auth="admin:admin")
        diagnostics = {}

        def fake_fetch_json(url, auth, timeout=15):
            del auth, timeout
            if url.endswith("/users"):
                return 200, {"value": [{"userName": "ADMIN", "userSecurityId": "user-1", "enabled": True}]}
            collect_contract.LAST_FETCH_ERRORS[url] = "RuntimeError: permission endpoint exploded"
            return 0, {}

        original_fetch_json = collect_contract.fetch_json
        collect_contract.LAST_FETCH_ERRORS.clear()
        try:
            collect_contract.fetch_json = fake_fetch_json
            result = collect_contract.collect_users(args, diagnostics, "company-id")
        finally:
            collect_contract.fetch_json = original_fetch_json

        self.assertFalse(result["permissionCollectionSucceeded"])
        self.assertIn("permission endpoint exploded", diagnostics["users.permissions"])

    def test_summarize_test_output_parses_current_run_tests_format(self):
        output = "Test codeunits: 70000,70001\ntotal=4 passed=4 failed=0 skipped=0\n"
        summary = summarize_test_output(output, "websocket")
        self.assertEqual(2, summary["testCodeunitCount"])
        self.assertEqual(4, summary["total"])
        self.assertEqual(4, summary["passed"])
        self.assertEqual(0, summary["failed"])
        self.assertEqual(0, summary["skipped"])
        self.assertEqual("websocket", summary["runnerKind"])

    def test_summarize_test_output_keeps_empty_codeunit_line_at_zero(self):
        output = "Test codeunits:\ntotal=0 passed=0 failed=0 skipped=0\n"
        summary = summarize_test_output(output, "startup-debug")
        self.assertEqual(0, summary["testCodeunitCount"])
        self.assertEqual(0, summary["total"])
        self.assertEqual("startup-debug", summary["runnerKind"])


if __name__ == "__main__":
    unittest.main()
