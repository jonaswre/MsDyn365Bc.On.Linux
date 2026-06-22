#!/usr/bin/env python3
from __future__ import annotations

import pathlib
import unittest


REPO_DIR = pathlib.Path(__file__).resolve().parents[1]
ENTRYPOINT = REPO_DIR / "scripts" / "entrypoint.sh"
DOCKERFILE = REPO_DIR / "src" / "Dockerfile"
COMPOSE_FILE = REPO_DIR / "docker-compose.yml"
WAIT_FOR_BC_HEALTHY = REPO_DIR / "scripts" / "wait-for-bc-healthy.sh"


class EntrypointTestRunnerPublishTests(unittest.TestCase):
    def test_entrypoint_publishes_test_runner_from_artifacts(self) -> None:
        text = ENTRYPOINT.read_text(encoding="utf-8")

        self.assertIn("resolve-test-runner-app.py", text)
        self.assertNotIn(
            'publish_required_app "/bc/testrunner/MicrosoftTestRunnerPatched.app"',
            text,
        )

    def test_image_does_not_copy_static_patched_test_runner(self) -> None:
        text = DOCKERFILE.read_text(encoding="utf-8")

        self.assertNotIn("MicrosoftTestRunnerPatched.app /bc/testrunner", text)
        self.assertNotIn("TestRunnerExtension.app /bc/testrunner", text)
        self.assertNotIn("/bc/testrunner/TestRunner.app", text)

    def test_entrypoint_does_not_require_test_runner_extension(self) -> None:
        text = ENTRYPOINT.read_text(encoding="utf-8")

        self.assertNotIn("Publishing Test Runner Extension", text)
        self.assertNotIn("/bc/testrunner/TestRunner.app", text)

    def test_entrypoint_normalizes_manifest_paths(self) -> None:
        text = ENTRYPOINT.read_text(encoding="utf-8")

        self.assertIn('DB_FILE="${DB_FILE//\\\\//}"', text)
        self.assertIn('LICENSE_FILE="${LICENSE_FILE//\\\\//}"', text)

    def test_compose_defaults_to_hosted_runner_memory_limit(self) -> None:
        text = COMPOSE_FILE.read_text(encoding="utf-8")

        self.assertIn("mem_limit: ${BC_MEMORY_LIMIT:-12G}", text)
        self.assertIn("BC_MEMORY_LIMIT: ${BC_MEMORY_LIMIT:-12G}", text)

    def test_wrapper_uses_public_test_toolkit_surface(self) -> None:
        self.assertFalse((REPO_DIR / "extensions" / "TestRunnerExtension").exists())

        text = ENTRYPOINT.read_text(encoding="utf-8")
        self.assertIn("resolve-test-runner-app.py", text)
        self.assertNotIn("TestRunnerExtension", text)

    def test_default_test_toolkit_includes_application_test_library(self) -> None:
        text = ENTRYPOINT.read_text(encoding="utf-8")

        self.assertIn('"Application Test Library"', text)

    def test_wait_for_health_fails_fast_on_required_publish_failures(self) -> None:
        text = WAIT_FOR_BC_HEALTHY.read_text(encoding="utf-8")

        self.assertIn("required publish failed", text)
        self.assertIn("fatal entrypoint error", text)


if __name__ == "__main__":
    unittest.main()
