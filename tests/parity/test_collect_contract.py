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

    def test_ci_test_runner_extension_is_excluded_from_user_app_footprint(self):
        extensions = [
            {
                "publisher": "ALDirectCompile",
                "displayName": "Test Runner Extension",
                "appId": "bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb",
            },
            {
                "publisher": "BCContainer",
                "displayName": "BC Container Smoke Test",
                "appId": "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee",
            },
        ]

        _, custom_apps, test_framework_present = split_apps(extensions)

        self.assertEqual(
            [
                {
                    "publisher": "BCContainer",
                    "name": "BC Container Smoke Test",
                    "id": "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee",
                    "version": "",
                }
            ],
            custom_apps,
        )
        self.assertFalse(test_framework_present)

    def test_extension_normalization_uses_stable_app_identity(self):
        extension = {
            "publisher": "Microsoft",
            "displayName": "Application",
            "appId": "stable-app-id",
            "packageId": "environment-package-id",
            "appVersion": "27.5.0.0",
        }

        self.assertEqual(
            {
                "publisher": "Microsoft",
                "name": "Application",
                "id": "stable-app-id",
                "version": "27.5.0.0",
            },
            normalize_extension(extension),
        )

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
            management_url=None,
            management_api_url=None,
            soap_url=None,
            web_client_url=None,
            client_websocket_url=None,
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

    def test_collect_surface_uses_explicit_service_urls(self):
        args = SimpleNamespace(
            base_url="http://localhost:7046/BC",
            management_url="http://localhost:7045/BC/Management",
            management_api_url="http://localhost:7086/BC/managementApi/v1.0/companies",
            soap_url="http://localhost:7047/BC/WS/Services",
            web_client_url="http://localhost:7085/BC/",
            client_websocket_url="http://localhost:7085/BC/client/csh",
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
            return {"tcpOpen": True, "httpClass": "4xx", "requiresAuth": True, "websocketUpgrade": False}

        original_surface_probe = collect_contract.surface_probe
        original_websocket_probe = collect_contract.websocket_probe
        try:
            collect_contract.surface_probe = fake_surface_probe
            collect_contract.websocket_probe = fake_websocket_probe
            collect_contract.collect_surface(args, {})
        finally:
            collect_contract.surface_probe = original_surface_probe
            collect_contract.websocket_probe = original_websocket_probe

        self.assertEqual("http://localhost:7045/BC/Management", probed["management"])
        self.assertEqual("http://localhost:7047/BC/WS/Services", probed["soap"])
        self.assertEqual("http://localhost:7085/BC/", probed["webClient"])
        self.assertEqual("http://localhost:7085/BC/", probed["clientSignIn"])
        self.assertEqual("http://localhost:7085/BC/client/csh", probed["clientWebSocket"])
        self.assertEqual("http://localhost:7086/BC/managementApi/v1.0/companies", probed["managementApi"])

    def test_collect_web_client_records_html_sign_in_signature(self):
        args = SimpleNamespace(
            base_url="http://localhost:7046/BC",
            web_client_url="http://localhost:7085/BC/",
        )

        def fake_fetch_text(url, auth=None, headers=None, timeout=15):
            del auth, headers, timeout
            if url == "http://localhost:7085/BC/client/csrf":
                return 200, {"Content-Type": "application/json"}, '{"csrfToken":"token"}'
            self.assertEqual("http://localhost:7085/BC/", url)
            return (
                200,
                {"Content-Type": "text/html; charset=utf-8", "Set-Cookie": "BCAuth=shim"},
                '<html><body><form><input name="__RequestVerificationToken"/>'
                '<input name="UserName"/><input name="Password" type="password"/>'
                '<button>Sign in</button></form></body></html>',
            )

        original_fetch_text = collect_contract.fetch_text
        try:
            collect_contract.fetch_text = fake_fetch_text
            result = collect_contract.collect_web_client(args, {})
        finally:
            collect_contract.fetch_text = original_fetch_text

        self.assertEqual(
            {
                "httpClass": "2xx",
                "contentTypeClass": "html",
                "payloadClass": "html",
                "hasHtmlRoot": True,
                "hasForm": True,
                "hasRequestVerificationToken": True,
                "hasUserNameInput": True,
                "hasPasswordInput": True,
                "hasSignInText": True,
                "setCookiePresent": True,
            },
            result["root"],
        )

    def test_collect_web_client_records_csrf_bootstrap_signature(self):
        args = SimpleNamespace(
            base_url="http://localhost:7046/BC",
            web_client_url="http://localhost:7085/BC/",
        )

        def fake_fetch_text(url, auth=None, headers=None, timeout=15):
            del auth, headers, timeout
            if url == "http://localhost:7085/BC/":
                return 200, {"Content-Type": "text/html"}, "<html></html>"
            self.assertEqual("http://localhost:7085/BC/client/csrf", url)
            return 200, {"Content-Type": "application/json", "Set-Cookie": "BCAuth=shim"}, '{"csrfToken":"token"}'

        original_fetch_text = collect_contract.fetch_text
        try:
            collect_contract.fetch_text = fake_fetch_text
            result = collect_contract.collect_web_client(args, {})
        finally:
            collect_contract.fetch_text = original_fetch_text

        self.assertEqual(
            {
                "httpClass": "2xx",
                "contentTypeClass": "json",
                "payloadClass": "json",
                "hasCsrfToken": True,
                "setCookiePresent": True,
            },
            result["csrf"],
        )

    def test_collect_headers_records_fingerprint_headers_for_user_surfaces(self):
        args = SimpleNamespace(
            base_url="http://localhost:7046/BC",
            management_api_url="http://localhost:7086/BC/managementApi/v1.0/companies",
            soap_url="http://localhost:7047/BC/WS/Services",
            web_client_url="http://localhost:7085/BC/",
            dev_url="http://localhost:7049/BC/dev",
            odata_url="http://localhost:7048/BC/ODataV4",
            api_url="http://localhost:7052/BC/api/v2.0",
            auth="admin:admin",
        )
        calls = []

        def fake_fetch_status(url, auth=None, headers=None, timeout=15):
            del headers, timeout
            calls.append((url, auth))
            return (
                200,
                {
                    "Server": "Microsoft-HTTPAPI/2.0",
                    "X-Powered-By": "ASP.NET",
                    "Date": "volatile",
                },
            )

        original_fetch_status = collect_contract.fetch_status
        try:
            collect_contract.fetch_status = fake_fetch_status
            result = collect_contract.collect_headers(args, {})
        finally:
            collect_contract.fetch_status = original_fetch_status

        self.assertEqual(
            {
                "httpClass": "2xx",
                "server": "Microsoft-HTTPAPI/2.0",
                "xPoweredBy": "ASP.NET",
                "xAspNetVersion": "",
                "xAspNetMvcVersion": "",
                "fingerprintHeaderNames": ["server", "x-powered-by"],
            },
            result["apiCompanies"],
        )
        self.assertNotIn("Date", result["apiCompanies"])
        self.assertIn(("http://localhost:7052/BC/api/v2.0/companies", "admin:admin"), calls)
        self.assertIn(("http://localhost:7085/BC/", None), calls)
        self.assertIn(("http://localhost:7085/BC/client/csrf", None), calls)

    def test_collect_company_records_company_counts_and_first_ids(self):
        args = SimpleNamespace(
            api_url="http://localhost:7052/BC/api/v2.0",
            odata_url="http://localhost:7048/BC/ODataV4",
            auth="admin:admin",
        )
        payloads = {
            "http://localhost:7052/BC/api/v2.0/companies": {
                "value": [{"id": "api-company-id", "name": "CRONUS International Ltd."}]
            },
            "http://localhost:7048/BC/ODataV4/Company": {
                "value": [{"Name": "CRONUS International Ltd.", "SystemId": "odata-company-id"}]
            },
        }

        def fake_fetch_json(url, auth, timeout=15):
            del auth, timeout
            return 200, payloads[url]

        original_fetch_json = collect_contract.fetch_json
        try:
            collect_contract.fetch_json = fake_fetch_json
            result = collect_contract.collect_company(args, {})
        finally:
            collect_contract.fetch_json = original_fetch_json

        self.assertEqual(1, result["apiCompanyCount"])
        self.assertEqual(1, result["odataCompanyCount"])
        self.assertTrue(result["apiFirstCompanyIdPresent"])
        self.assertTrue(result["odataFirstCompanyIdPresent"])

    def test_collect_integration_classifies_api_odata_and_soap_payloads(self):
        args = SimpleNamespace(
            base_url="http://localhost:7046/BC",
            soap_url="http://localhost:7047/BC/WS/Services",
            api_url="http://localhost:7052/BC/api/v2.0",
            odata_url="http://localhost:7048/BC/ODataV4",
            auth="admin:admin",
        )

        def fake_fetch_json(url, auth, timeout=15):
            del auth, timeout
            if url.endswith("/companies"):
                return 200, {"value": [{"id": "api-company-id", "name": "CRONUS International Ltd."}]}
            return 200, {"value": [{"Name": "CRONUS International Ltd.", "SystemId": "odata-company-id"}]}

        def fake_fetch_text(url, auth, headers=None, timeout=15):
            del auth, headers, timeout
            self.assertEqual("http://localhost:7047/BC/WS/Services", url)
            return 200, {"Content-Type": "text/xml; charset=utf-8"}, "<Services><Service>Company</Service></Services>"

        original_fetch_json = collect_contract.fetch_json
        original_fetch_text = collect_contract.fetch_text
        try:
            collect_contract.fetch_json = fake_fetch_json
            collect_contract.fetch_text = fake_fetch_text
            result = collect_contract.collect_integration(args, {})
        finally:
            collect_contract.fetch_json = original_fetch_json
            collect_contract.fetch_text = original_fetch_text

        self.assertEqual(
            {
                "httpClass": "2xx",
                "readSucceeded": True,
                "itemCount": 1,
                "firstItemKeys": ["id", "name"],
            },
            result["apiCompanies"],
        )
        self.assertEqual(
            {
                "httpClass": "2xx",
                "readSucceeded": True,
                "itemCount": 1,
                "firstItemKeys": ["Name", "SystemId"],
            },
            result["odataCompany"],
        )
        self.assertEqual(
            {
                "httpClass": "2xx",
                "readSucceeded": True,
                "contentTypeClass": "xml",
                "payloadClass": "xml",
                "xmlRoot": "Services",
            },
            result["soapServices"],
        )

    def test_customer_crud_probe_creates_updates_deletes_and_verifies_cleanup(self):
        args = SimpleNamespace(api_url="http://localhost:7052/BC/api/v2.0", auth="admin:admin", bc_version="28.1")
        calls = []
        deleted = False

        def fake_request_json(method, url, auth, payload=None, headers=None, timeout=15):
            nonlocal deleted
            del auth, headers, timeout
            calls.append((method, url, payload))
            if method == "POST":
                return 201, {"id": "customer-id", "displayName": payload["displayName"]}
            if method == "PATCH":
                return 200, {"id": "customer-id", "displayName": payload["displayName"]}
            if method == "DELETE":
                deleted = True
                return 204, {}
            raise AssertionError(f"unexpected request_json call: {method} {url}")

        def fake_fetch_json(url, auth, timeout=15):
            del auth, timeout
            if deleted:
                return 404, {}
            if url.endswith("/customers(customer-id)"):
                return 200, {"id": "customer-id", "displayName": "BC Parity 281"}
            return 404, {}

        original_request_json = collect_contract.request_json
        original_fetch_json = collect_contract.fetch_json
        try:
            collect_contract.request_json = fake_request_json
            collect_contract.fetch_json = fake_fetch_json
            result = collect_contract.customer_crud_probe(args, {}, "company-id")
        finally:
            collect_contract.request_json = original_request_json
            collect_contract.fetch_json = original_fetch_json

        self.assertTrue(result["roundTripSucceeded"])
        self.assertEqual("2xx", result["createHttpClass"])
        self.assertEqual("2xx", result["readHttpClass"])
        self.assertEqual("2xx", result["updateHttpClass"])
        self.assertEqual("2xx", result["deleteHttpClass"])
        self.assertEqual("4xx", result["readAfterDeleteHttpClass"])
        self.assertTrue(result["createdIdPresent"])
        self.assertTrue(result["updateEchoed"])
        self.assertTrue(result["deleteVerified"])
        self.assertEqual(["POST", "PATCH", "DELETE"], [call[0] for call in calls])

    def test_customer_crud_probe_attempts_cleanup_after_update_failure(self):
        args = SimpleNamespace(api_url="http://localhost:7052/BC/api/v2.0", auth="admin:admin", bc_version="27.5")
        calls = []

        def fake_request_json(method, url, auth, payload=None, headers=None, timeout=15):
            del url, auth, payload, headers, timeout
            calls.append(method)
            if method == "POST":
                return 201, {"id": "customer-id", "displayName": "BC Parity"}
            if method == "PATCH":
                return 500, {}
            if method == "DELETE":
                return 204, {}
            raise AssertionError(f"unexpected request_json call: {method}")

        def fake_fetch_json(url, auth, timeout=15):
            del url, auth, timeout
            return 200, {"id": "customer-id", "displayName": "BC Parity"}

        original_request_json = collect_contract.request_json
        original_fetch_json = collect_contract.fetch_json
        try:
            collect_contract.request_json = fake_request_json
            collect_contract.fetch_json = fake_fetch_json
            result = collect_contract.customer_crud_probe(args, {}, "company-id")
        finally:
            collect_contract.request_json = original_request_json
            collect_contract.fetch_json = original_fetch_json

        self.assertFalse(result["roundTripSucceeded"])
        self.assertEqual("5xx", result["updateHttpClass"])
        self.assertEqual("2xx", result["deleteHttpClass"])
        self.assertEqual(["POST", "PATCH", "DELETE"], calls)

    def test_uppercase_id_is_treated_as_stable_item_id(self):
        self.assertTrue(collect_contract.item_id_present({"Id": "odata-company-id"}))

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
