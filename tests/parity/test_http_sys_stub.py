import re
import unittest
from pathlib import Path


class HttpSysStubCompatibilityTests(unittest.TestCase):
    def source(self):
        return Path("src/stubs/HttpSysStub/HttpSysStub.cs").read_text(encoding="utf-8")

    def method_body(self, name):
        source = self.source()
        match = re.search(
            rf"private static [^{{]+ {name}\([^)]*\)\s*\{{(?P<body>.*?)\n        \}}",
            source,
            re.DOTALL,
        )
        self.assertIsNotNone(match, f"{name} method not found")
        return match.group("body")

    def test_web_client_root_is_public_and_returns_sign_in_shim(self):
        public_paths = self.method_body("IsPublicWebClientCompatibilityPath")
        sign_in_shim = self.method_body("WebClientSignInCompatibility")

        for path in ('""', '"/"', '"/BC"', '"/BC/SignIn"', '"/BC/client/SignIn"'):
            self.assertIn(path, public_paths)
            self.assertIn(path, sign_in_shim)

    def test_web_client_csrf_matches_windows_error_shape(self):
        public_paths = self.method_body("IsPublicWebClientCompatibilityPath")
        sign_in_shim = self.method_body("WebClientSignInCompatibility")
        csrf_error = self.method_body("WriteWindowsCompatibleCsrfError")

        for path in ('"/csrf"', '"/BC/csrf"', '"/BC/client/csrf"'):
            self.assertNotIn(path, public_paths)
            self.assertIn(path, sign_in_shim)

        self.assertIn("Status400BadRequest", csrf_error)
        self.assertIn('"text/html; charset=utf-8"', csrf_error)
        self.assertIn("<error", csrf_error)
        self.assertNotIn("SetShimCookie", csrf_error)

    def test_windows_public_compatibility_paths_are_explicit_and_do_not_include_api_or_odata(self):
        body = self.method_body("IsWindowsPublicCompatibilityPath")

        for path in ("/BC/Management", "/BC/managementApi", "/BC/dev", "/BC/client/csh"):
            self.assertIn(path, body)

        self.assertNotIn("/BC/api", body)
        self.assertNotIn("/BC/ODataV4", body)
        self.assertIn("Windows exposes these surfaces without a Basic challenge", self.source())

    def test_client_websocket_public_compatibility_is_version_gated(self):
        body = self.method_body("IsWindowsPublicCompatibilityPath")

        self.assertIn("IsLegacyPublicClientWebSocketCompatibilityVersion()", body)
        self.assertIn('NonEmptyEnvironment("BC_VERSION", "latest")', self.source())
        self.assertIn("BC 28", self.source())


if __name__ == "__main__":
    unittest.main()
