import json
import tempfile
import unittest
from pathlib import Path

from parity.compare_contracts import compare_contracts, load_known_deltas


def base_contract(platform):
    return {
        "schemaVersion": 1,
        "platform": platform,
        "bcVersionInput": "28.1",
        "surface": {
            "odata": {"tcpOpen": True, "httpClass": "2xx", "requiresAuth": True},
            "api": {"tcpOpen": True, "httpClass": "2xx", "requiresAuth": True},
        },
        "auth": {
            "validCredentialsAccepted": True,
            "invalidCredentialsRejected": True,
            "authSchemeClass": "basic",
        },
        "company": {
            "companyCountAtLeastOne": True,
            "firstCompanyName": "CRONUS International Ltd.",
            "apiCompanyShape": ["id", "name", "systemVersion"],
            "odataCompanyShape": ["Name"],
        },
        "dev": {
            "metadataReachable": True,
            "packagesEndpointReachable": True,
            "devApiMajor": 7,
            "supportsTestRunnerHub": True,
        },
        "tests": {
            "testCodeunitCount": 2,
            "total": 4,
            "passed": 4,
            "failed": 0,
            "skipped": 0,
            "runnerKind": "websocket",
        },
        "apps": {
            "microsoftApps": [
                {"publisher": "Microsoft", "name": "System Application", "version": "28.1.0.0"}
            ],
            "customApps": [],
            "testFrameworkPresent": True,
        },
        "users": {
            "authUserName": "admin",
            "enabledSuperUserCount": 1,
            "knownUserNames": ["ADMIN"],
        },
        "diagnostics": {"ignored": "value"},
    }


class CompareContractsTests(unittest.TestCase):
    def test_identical_contracts_have_no_unexpected_diffs(self):
        result = compare_contracts(base_contract("linux"), base_contract("windows"), [])
        self.assertEqual([], result.unexpected)
        self.assertEqual([], result.applied_known_deltas)

    def test_diagnostics_and_platform_are_ignored(self):
        linux = base_contract("linux")
        windows = base_contract("windows")
        linux["diagnostics"]["docker"] = "linux"
        windows["diagnostics"]["docker"] = "windows"

        result = compare_contracts(linux, windows, [])

        self.assertEqual([], result.unexpected)

    def test_runner_kind_is_ignored_as_harness_detail(self):
        linux = base_contract("linux")
        windows = base_contract("windows")
        linux["tests"]["runnerKind"] = "websocket"
        windows["tests"]["runnerKind"] = "bccontainerhelper"

        result = compare_contracts(linux, windows, [])

        self.assertEqual([], result.unexpected)

    def test_unexpected_value_difference_is_reported(self):
        linux = base_contract("linux")
        windows = base_contract("windows")
        linux["auth"]["invalidCredentialsRejected"] = False

        result = compare_contracts(linux, windows, [])

        self.assertEqual(1, len(result.unexpected))
        self.assertEqual("auth.invalidCredentialsRejected", result.unexpected[0]["path"])

    def test_missing_key_is_distinguished_from_explicit_null(self):
        linux = base_contract("linux")
        windows = base_contract("windows")
        del linux["auth"]["authSchemeClass"]
        windows["auth"]["authSchemeClass"] = None

        result = compare_contracts(linux, windows, [])

        self.assertEqual(1, len(result.unexpected))
        self.assertEqual("auth.authSchemeClass", result.unexpected[0]["path"])
        self.assertIsNone(result.unexpected[0]["linux"])
        self.assertTrue(result.unexpected[0]["linuxMissing"])
        self.assertIsNone(result.unexpected[0]["windows"])
        self.assertFalse(result.unexpected[0]["windowsMissing"])

    def test_explicit_missing_key_marker_like_value_is_not_treated_as_missing(self):
        linux = base_contract("linux")
        windows = base_contract("windows")
        linux["auth"]["authSchemeClass"] = {"__missing_key__": True}
        del windows["auth"]["authSchemeClass"]

        result = compare_contracts(linux, windows, [])

        self.assertEqual(1, len(result.unexpected))
        self.assertEqual("auth.authSchemeClass", result.unexpected[0]["path"])
        self.assertEqual({"__missing_key__": True}, result.unexpected[0]["linux"])
        self.assertFalse(result.unexpected[0]["linuxMissing"])
        self.assertIsNone(result.unexpected[0]["windows"])
        self.assertTrue(result.unexpected[0]["windowsMissing"])

    def test_known_delta_suppresses_matching_custom_app_difference(self):
        linux = base_contract("linux")
        windows = base_contract("windows")
        linux["apps"]["customApps"] = [
            {
                "publisher": "ALDirectCompile",
                "name": "Test Runner Extension",
                "id": "bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb",
                "version": "3.0.0.0",
            }
        ]
        known = [
            {
                "path": "apps.customApps[]",
                "match": {"publisher": "ALDirectCompile", "name": "Test Runner Extension"},
                "reason": "Linux runner installs a custom API extension for v1 test orchestration.",
            }
        ]

        result = compare_contracts(linux, windows, known)

        self.assertEqual([], result.unexpected)
        self.assertEqual(1, len(result.applied_known_deltas))

    def test_known_delta_reports_unmatched_custom_apps(self):
        linux = base_contract("linux")
        windows = base_contract("windows")
        linux["apps"]["customApps"] = [
            {
                "publisher": "ALDirectCompile",
                "name": "Test Runner Extension",
                "id": "bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb",
                "version": "3.0.0.0",
            }
        ]
        windows["apps"]["customApps"] = [
            {
                "publisher": "Contoso",
                "name": "Windows Only Extension",
                "id": "cccccccc-cccc-cccc-cccc-cccccccccccc",
                "version": "1.0.0.0",
            }
        ]
        known = [
            {
                "path": "apps.customApps[]",
                "match": {"publisher": "ALDirectCompile", "name": "Test Runner Extension"},
                "reason": "Linux runner installs a custom API extension for v1 test orchestration.",
            }
        ]

        result = compare_contracts(linux, windows, known)

        self.assertEqual(1, len(result.applied_known_deltas))
        self.assertEqual(1, len(result.unexpected))
        self.assertEqual("apps.customApps", result.unexpected[0]["path"])
        self.assertEqual([], result.unexpected[0]["linux"])
        self.assertEqual(windows["apps"]["customApps"], result.unexpected[0]["windows"])

    def test_known_delta_preserves_duplicate_custom_app_remainders(self):
        linux = base_contract("linux")
        windows = base_contract("windows")
        app = {
            "publisher": "ALDirectCompile",
            "name": "Test Runner Extension",
            "id": "bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb",
            "version": "3.0.0.0",
        }
        linux["apps"]["customApps"] = [app, app]
        windows["apps"]["customApps"] = [app]
        known = [
            {
                "path": "apps.customApps[]",
                "match": {"publisher": "Contoso", "name": "Other Extension"},
                "reason": "Only a non-matching custom app is allowed.",
            }
        ]

        result = compare_contracts(linux, windows, known)

        self.assertEqual([], result.applied_known_deltas)
        self.assertEqual(1, len(result.unexpected))
        self.assertEqual("apps.customApps", result.unexpected[0]["path"])
        self.assertEqual([app], result.unexpected[0]["linux"])
        self.assertEqual([], result.unexpected[0]["windows"])

    def test_one_known_delta_suppresses_one_matching_duplicate_custom_app(self):
        linux = base_contract("linux")
        windows = base_contract("windows")
        app = {
            "publisher": "ALDirectCompile",
            "name": "Test Runner Extension",
            "id": "bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb",
            "version": "3.0.0.0",
        }
        linux["apps"]["customApps"] = [app, app]
        known = [
            {
                "path": "apps.customApps[]",
                "match": {"publisher": "ALDirectCompile", "name": "Test Runner Extension"},
                "reason": "Linux runner installs a custom API extension for v1 test orchestration.",
            }
        ]

        result = compare_contracts(linux, windows, known)

        self.assertEqual(1, len(result.applied_known_deltas))
        self.assertEqual(1, len(result.unexpected))
        self.assertEqual("apps.customApps", result.unexpected[0]["path"])
        self.assertEqual([app], result.unexpected[0]["linux"])
        self.assertEqual([], result.unexpected[0]["windows"])

    def test_known_delta_does_not_suppress_non_list_custom_apps_diff(self):
        linux = base_contract("linux")
        windows = base_contract("windows")
        del linux["apps"]["customApps"]
        known = [
            {
                "path": "apps.customApps[]",
                "match": {"publisher": "ALDirectCompile", "name": "Test Runner Extension"},
                "reason": "Linux runner installs a custom API extension for v1 test orchestration.",
            }
        ]

        result = compare_contracts(linux, windows, known)

        self.assertEqual(1, len(result.unexpected))
        self.assertEqual("apps.customApps", result.unexpected[0]["path"])

    def test_known_delta_file_loads_json(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "known-deltas.json"
            path.write_text(json.dumps([{"path": "apps.customApps[]", "match": {}, "reason": "test"}]), encoding="utf-8")

            self.assertEqual(1, len(load_known_deltas(path)))


if __name__ == "__main__":
    unittest.main()
