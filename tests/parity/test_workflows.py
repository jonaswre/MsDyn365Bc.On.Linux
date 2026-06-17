import unittest
from pathlib import Path

import yaml


class ParityWorkflowTests(unittest.TestCase):
    def workflow(self):
        path = Path(".github/workflows/parity-windows-linux.yml")
        return yaml.safe_load(path.read_text(encoding="utf-8"))

    def linux_contract_job(self):
        return self.workflow()["jobs"]["linux-contract"]

    def windows_contract_script(self):
        steps = self.workflow()["jobs"]["windows-contract"]["steps"]
        collect = next(step for step in steps if step.get("name") == "Collect Windows contract")
        return collect["run"]

    def step_names(self):
        return [step.get("name") or step.get("uses") for step in self.linux_contract_job()["steps"]]

    def test_linux_contract_uses_full_stock_app_footprint(self):
        env = self.linux_contract_job()["env"]

        self.assertEqual("false", env["BC_CLEAR_ALL_APPS"])
        self.assertEqual("false", env["BC_INCLUDE_TEST_TOOLKIT"])

    def test_windows_contract_uses_test_runner_only(self):
        script = Path("parity/collect-windows-contract.ps1").read_text(encoding="utf-8")

        self.assertIn("-includeTestRunnerOnly", script)
        self.assertNotIn("-includeTestToolkit", script)

    def test_linux_contract_uses_hosted_runner_memory_budget(self):
        env = self.linux_contract_job()["env"]

        self.assertEqual("12G", env["BC_MEMORY_LIMIT"])
        self.assertEqual(1024, env["MSSQL_MEMORY_LIMIT_MB"])
        self.assertNotIn("DOTNET_GCConserveMemory", env)
        self.assertNotIn("DOTNET_GCHeapCount", env)
        self.assertNotIn("DOTNET_GCNoAffinitize", env)

    def test_build_job_uploads_keep_app_ids_for_linux_startup(self):
        steps = self.workflow()["jobs"]["build-smoke-app"]["steps"]
        script = "\n".join(step.get("run", "") for step in steps)
        upload = next(step for step in steps if step.get("uses") == "actions/upload-artifact@v4")

        self.assertIn("scripts/resolve-keep-app-ids.py", script)
        self.assertIn("--app-json extensions/smoke-test/app.json", script)
        self.assertIn("--app-json extensions/TestRunnerExtension/app.json", script)
        self.assertIn("--app-file \"patched-test-runner-$BC_VERSION.app\"", script)
        self.assertIn("--app-file \"test-runner-extension-$BC_VERSION.app\"", script)
        self.assertIn("ids.discard(test_runner_id)", script)
        self.assertIn("keep-app-ids-${{ matrix.bc_version }}.txt", upload["with"]["path"])

    def test_build_job_keeps_microsoft_automation_api_v2_app(self):
        steps = self.workflow()["jobs"]["build-smoke-app"]["steps"]
        script = "\n".join(step.get("run", "") for step in steps)

        self.assertIn("AUTOMATION_API_V2_APP_ID=10cb69d9-bc8a-4d27-970a-9e110e9db2a5", script)
        self.assertIn("--extra-ids \"$AUTOMATION_API_V2_APP_ID\"", script)

    def test_build_job_uploads_version_matched_test_runner_extension(self):
        steps = self.workflow()["jobs"]["build-smoke-app"]["steps"]
        script = "\n".join(step.get("run", "") for step in steps)
        upload = next(step for step in steps if step.get("uses") == "actions/upload-artifact@v4")

        self.assertIn("extensions/TestRunnerExtension/.alpackages", script)
        self.assertIn("patched-test-runner-$BC_VERSION.app", script)
        self.assertIn("/project:extensions/TestRunnerExtension", script)
        self.assertIn("/out:test-runner-extension-$BC_VERSION.app", script)
        self.assertIn("test-runner-extension-${{ matrix.bc_version }}.app", upload["with"]["path"])

    def test_linux_contract_uses_version_matched_test_runner_extension(self):
        steps = self.linux_contract_job()["steps"]
        collect = next(step for step in steps if step.get("name") == "Collect Linux contract")
        script = collect["run"]

        self.assertIn("build/test-runner-extension-$BC_VERSION.app", script)

    def test_linux_entrypoint_does_not_seed_visible_service_user(self):
        script = Path("scripts/entrypoint.sh").read_text(encoding="utf-8")

        self.assertNotIn("YOURBC-SERVICEUSER", script)

    def test_linux_contract_loads_keep_app_ids_before_startup(self):
        steps = self.linux_contract_job()["steps"]
        start_index = self.step_names().index("Start Linux BC")
        prior_scripts = "\n".join(step.get("run", "") for step in steps[:start_index])

        self.assertIn("BC_KEEP_APP_IDS", prior_scripts)
        self.assertIn("keep-app-ids-$BC_VERSION.txt", prior_scripts)
        self.assertIn("$GITHUB_ENV", prior_scripts)

    def test_linux_contract_publishes_only_smoke_test_runner_dependency(self):
        script = Path("parity/collect-linux-contract.sh").read_text(encoding="utf-8")

        self.assertIn("patched_test_runner_app", script)
        self.assertIn("Publishing version-matched patched Microsoft Test Runner", script)
        self.assertNotIn("load_artifact_apps", script)
        self.assertNotIn("Business Foundation Test Libraries", script)

    def test_linux_contract_records_test_runner_setup_failure_as_contract_data(self):
        script = Path("parity/collect-linux-contract.sh").read_text(encoding="utf-8")

        self.assertIn("tests.runnerSetup=patched Microsoft Test Runner publish failed", script)
        self.assertIn("total=0 passed=0 failed=1 skipped=0", script)
        self.assertIn("exit 0", script)

    def test_run_tests_uses_api_port_candidate_before_custom_api_exists(self):
        script = Path("scripts/run-tests.sh").read_text(encoding="utf-8")

        self.assertIn('API_PORT_BASE="$(derive_api_base_candidates | sed -n', script)
        self.assertIn('[ -z "$API_PORT_BASE" ] && API_PORT_BASE="$BASE_URL"', script)
        self.assertIn("TestRunner API not available at ${API_BASE}/codeunitRunRequests", script)

    def test_linux_diagnostics_are_captured_after_contract_collection(self):
        names = self.step_names()

        self.assertLess(names.index("Collect Linux contract"), names.index("Capture Linux diagnostics"))

    def test_linux_diagnostics_capture_healthcheck_state(self):
        steps = self.linux_contract_job()["steps"]
        diagnostics = next(step for step in steps if step.get("name") == "Capture Linux diagnostics")

        self.assertIn("docker inspect \"$(docker compose ps -q bc)\"", diagnostics["run"])
        self.assertIn("bc-inspect.json", diagnostics["run"])

    def test_build_job_uploads_version_matched_patched_test_runner(self):
        steps = self.workflow()["jobs"]["build-smoke-app"]["steps"]
        script = "\n".join(step.get("run", "") for step in steps)

        self.assertIn("scripts/build-patched-test-runner.sh", script)
        self.assertIn("patched-test-runner-$BC_VERSION.app", script)

    def test_compare_waits_for_both_contract_jobs_to_succeed(self):
        compare = self.workflow()["jobs"]["compare-contracts"]

        self.assertIn("needs.linux-contract.result == 'success'", compare["if"])
        self.assertIn("needs.windows-contract.result == 'success'", compare["if"])


if __name__ == "__main__":
    unittest.main()
