#!/usr/bin/env python3
from __future__ import annotations

import importlib.util
import pathlib
import tempfile
import unittest
import zipfile


SCRIPT_DIR = pathlib.Path(__file__).resolve().parent
MODULE_PATH = SCRIPT_DIR / "resolve-test-runner-app.py"
spec = importlib.util.spec_from_file_location("resolve_test_runner_app", MODULE_PATH)
resolver = importlib.util.module_from_spec(spec)
assert spec.loader is not None
spec.loader.exec_module(resolver)


def write_app(
    path: pathlib.Path,
    *,
    app_id: str,
    name: str,
    publisher: str,
    version: str,
    runtime: str,
) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    manifest = f"""<Package xmlns="http://schemas.microsoft.com/navx/2015/manifest">
  <App Id="{app_id}" Name="{name}" Publisher="{publisher}" Version="{version}" Runtime="{runtime}" />
  <Dependencies />
</Package>
"""
    with zipfile.ZipFile(path, "w") as archive:
        archive.writestr("NavxManifest.xml", manifest)


class ResolveTestRunnerAppTests(unittest.TestCase):
    def test_resolves_microsoft_test_runner_from_artifacts(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            artifact_dir = pathlib.Path(tmp)
            expected = artifact_dir / "app" / "test" / "Microsoft_Test Runner_27.app"
            write_app(
                expected,
                app_id=resolver.TEST_RUNNER_APP_ID,
                name="Test Runner",
                publisher="Microsoft",
                version="27.0.0.0",
                runtime="16.0",
            )
            write_app(
                artifact_dir / "app" / "custom" / "ALDirectCompile_Test Runner Extension.app",
                app_id="bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb",
                name="Test Runner Extension",
                publisher="ALDirectCompile",
                version="2.0.0.0",
                runtime="16.0",
            )

            self.assertEqual(
                resolver.resolve_test_runner_app(str(artifact_dir)),
                str(expected),
            )

    def test_rejects_wrong_publisher_with_test_runner_id(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            artifact_dir = pathlib.Path(tmp)
            write_app(
                artifact_dir / "app" / "Wrong_Test Runner.app",
                app_id=resolver.TEST_RUNNER_APP_ID,
                name="Test Runner",
                publisher="Contoso",
                version="27.0.0.0",
                runtime="16.0",
            )

            self.assertIsNone(resolver.resolve_test_runner_app(str(artifact_dir)))

    def test_prefers_publishable_app_over_symbols_package(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            artifact_dir = pathlib.Path(tmp)
            publishable = artifact_dir / "app" / "Microsoft_Test Runner_27.0.app"
            symbols = (
                artifact_dir
                / "app"
                / "Microsoft_Microsoft.TestRunner.symbols.23de40a6.app"
            )
            write_app(
                publishable,
                app_id=resolver.TEST_RUNNER_APP_ID,
                name="Test Runner",
                publisher="Microsoft",
                version="27.0.0.0",
                runtime="16.0",
            )
            write_app(
                symbols,
                app_id=resolver.TEST_RUNNER_APP_ID,
                name="Test Runner",
                publisher="Microsoft",
                version="27.2.0.0",
                runtime="16.0",
            )

            self.assertEqual(
                resolver.resolve_test_runner_app(str(artifact_dir)),
                str(publishable),
            )

    def test_returns_none_when_test_runner_is_missing(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            artifact_dir = pathlib.Path(tmp)
            write_app(
                artifact_dir / "app" / "Microsoft_Library Assert.app",
                app_id="dd0be2ea-f733-4d65-bb34-a28f4624fb14",
                name="Library Assert",
                publisher="Microsoft",
                version="27.0.0.0",
                runtime="16.0",
            )

            self.assertIsNone(resolver.resolve_test_runner_app(str(artifact_dir)))


if __name__ == "__main__":
    unittest.main()
