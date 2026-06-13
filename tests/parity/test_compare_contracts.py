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

    def test_unexpected_value_difference_is_reported(self):
        linux = base_contract("linux")
        windows = base_contract("windows")
        linux["auth"]["invalidCredentialsRejected"] = False

        result = compare_contracts(linux, windows, [])

        self.assertEqual(1, len(result.unexpected))
        self.assertEqual("auth.invalidCredentialsRejected", result.unexpected[0]["path"])

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

    def test_known_delta_file_loads_json(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "known-deltas.json"
            path.write_text(json.dumps([{"path": "apps.customApps[]", "match": {}, "reason": "test"}]), encoding="utf-8")

            self.assertEqual(1, len(load_known_deltas(path)))


if __name__ == "__main__":
    unittest.main()
