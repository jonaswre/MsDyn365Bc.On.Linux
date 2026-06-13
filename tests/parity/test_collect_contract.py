import unittest
from types import SimpleNamespace

import parity.collect_contract as collect_contract
from parity.collect_contract import (
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

    def test_summarize_test_output_parses_current_run_tests_format(self):
        output = "Test codeunits: 70000,70001\ntotal=4 passed=4 failed=0 skipped=0\n"
        summary = summarize_test_output(output, "websocket")
        self.assertEqual(2, summary["testCodeunitCount"])
        self.assertEqual(4, summary["total"])
        self.assertEqual(4, summary["passed"])
        self.assertEqual(0, summary["failed"])
        self.assertEqual(0, summary["skipped"])
        self.assertEqual("websocket", summary["runnerKind"])


if __name__ == "__main__":
    unittest.main()
