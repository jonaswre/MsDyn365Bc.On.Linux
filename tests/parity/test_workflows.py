import unittest
from pathlib import Path

import yaml


class ParityWorkflowTests(unittest.TestCase):
    def workflow(self):
        path = Path(".github/workflows/parity-windows-linux.yml")
        return yaml.safe_load(path.read_text(encoding="utf-8"))

    def build_image_workflow(self):
        path = Path(".github/workflows/build-image.yml")
        return yaml.safe_load(path.read_text(encoding="utf-8"))

    def versions_workflow(self):
        path = Path(".github/workflows/test-versions.yml")
        return yaml.safe_load(path.read_text(encoding="utf-8"))

    def bc_test_from_source_workflow(self):
        path = Path(".github/workflows/bc-test-from-source.yml")
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
        self.assertEqual("true", env["BC_INCLUDE_TEST_TOOLKIT"])

    def test_windows_contract_uses_test_runner_only(self):
        script = Path("parity/collect-windows-contract.ps1").read_text(encoding="utf-8")

        self.assertIn("-includeTestRunnerOnly", script)
        self.assertNotIn("-includeTestToolkit", script)

    def test_windows_contract_serializes_gallery_dependent_jobs(self):
        job = self.workflow()["jobs"]["windows-contract"]

        self.assertEqual(1, job["strategy"]["max-parallel"])

    def test_windows_contract_caches_bccontainerhelper_module(self):
        steps = self.workflow()["jobs"]["windows-contract"]["steps"]
        cache = next(step for step in steps if step.get("uses") == "actions/cache@v4")

        self.assertIn("PowerShell\\Modules\\BcContainerHelper", cache["with"]["path"])
        self.assertIn("bccontainerhelper", cache["with"]["key"].lower())

    def test_windows_contract_retries_bccontainerhelper_download(self):
        script = Path("parity/collect-windows-contract.ps1").read_text(encoding="utf-8")

        self.assertIn("Invoke-WithRetry", script)
        self.assertIn("${Attempts}:", script)
        self.assertNotIn("$Attempts:", script)
        self.assertIn("User-Agent", script)
        self.assertIn("BcContainerHelper.nupkg", script)

    def test_report_smoke_tests_cover_primary_export_formats(self):
        source = Path("extensions/smoke-test/src/SmokeReportTest.Codeunit.al").read_text(encoding="utf-8")

        self.assertIn("TestCustomerListPdfExport", source)
        self.assertIn("ReportFormat::Pdf", source)
        self.assertIn("TestCustomerListWordExport", source)
        self.assertIn("ReportFormat::Word", source)
        self.assertIn("TestCustomerListExcelExport", source)
        self.assertIn("ReportFormat::Excel", source)

    def test_windows_contract_expects_all_smoke_tests(self):
        script = Path("parity/collect-windows-contract.ps1").read_text(encoding="utf-8")

        self.assertIn("$expectedTests = 7", script)
        self.assertIn("$summary.Total -ne $expectedTests", script)

    def test_windows_contract_removes_permissions_mock_before_collection(self):
        script = Path("parity/collect-windows-contract.ps1").read_text(encoding="utf-8")

        self.assertIn("Permissions Mock", script)
        self.assertIn("UnPublish-BcContainerApp", script)

    def test_windows_contract_maps_legacy_management_to_management_api(self):
        script = Path("parity/collect-windows-contract.ps1").read_text(encoding="utf-8")

        self.assertIn('"7045:7086"', script)
        self.assertNotIn('"7045:7045"', script)

    def test_contract_collectors_pass_service_specific_surface_urls(self):
        linux = Path("parity/collect-linux-contract.sh").read_text(encoding="utf-8")
        windows = Path("parity/collect-windows-contract.ps1").read_text(encoding="utf-8")

        for script in (linux, windows):
            self.assertIn("--management-url", script)
            self.assertIn("--management-api-url", script)
            self.assertIn("--soap-url", script)
            self.assertIn("--web-client-url", script)
            self.assertIn("--client-websocket-url", script)
            self.assertIn("http://localhost:7085/BC/", script)

    def test_linux_contract_uses_hosted_runner_memory_budget(self):
        env = self.linux_contract_job()["env"]

        self.assertEqual("12G", env["BC_MEMORY_LIMIT"])
        self.assertEqual(1024, env["MSSQL_MEMORY_LIMIT_MB"])
        self.assertNotIn("DOTNET_GCConserveMemory", env)
        self.assertNotIn("DOTNET_GCHeapCount", env)
        self.assertNotIn("DOTNET_GCNoAffinitize", env)

    def test_test_versions_defaults_to_bc28_only(self):
        workflow = self.versions_workflow()
        versions = workflow[True]["workflow_dispatch"]["inputs"]["versions"]["default"]
        prepare_script = workflow["jobs"]["prepare"]["steps"][0]["run"]

        self.assertEqual("28.0,28.1,28.2", versions)
        self.assertNotIn("27.", versions)
        self.assertIn("This workflow supports BC 28 only", prepare_script)

    def test_test_versions_has_bc28_container_capability_jobs(self):
        workflow = self.versions_workflow()
        webclient = workflow["jobs"]["test-webclient"]
        container_download = workflow["jobs"]["test-container-download"]
        macos = workflow["jobs"]["test-macos-overlay"]
        scripts = "\n".join(
            step.get("run", "")
            for job in (webclient, container_download, macos)
            for step in job["steps"]
        )
        container_download_start = None
        for step in container_download["steps"]:
            if step.get("name") == "Start BC with in-container artifact download and no test toolkit":
                container_download_start = step
                break
        self.assertIsNotNone(container_download_start)
        webclient_start = next(step for step in webclient["steps"] if step.get("name") == "Start BC with web client")

        self.assertNotIn("test-no-test-toolkit", workflow["jobs"])
        self.assertEqual("Web client opt-in (BC 28.2)", webclient["name"])
        self.assertEqual("Container download without test toolkit (BC 28.2)", container_download["name"])
        self.assertEqual("macOS overlay test (BC 28.2)", macos["name"])
        self.assertEqual("false", container_download_start["env"]["BC_INCLUDE_TEST_TOOLKIT"])
        self.assertEqual("1", webclient_start["env"]["BC_WEBCLIENT"])
        self.assertIn("BC_INCLUDE_TEST_TOOLKIT=false: skipped test toolkit publishing", scripts)
        self.assertIn("BC_WEBCLIENT=1: starting web client", scripts)
        self.assertNotIn('BC_VERSION: "27.', str(workflow["jobs"]))

    def test_primary_workflows_cancel_stale_runs(self):
        for workflow in (
            self.versions_workflow(),
            self.build_image_workflow(),
            self.workflow(),
        ):
            concurrency = workflow["concurrency"]
            self.assertEqual("${{ github.workflow }}-${{ github.ref }}", concurrency["group"])
            self.assertTrue(concurrency["cancel-in-progress"])

    def test_test_versions_uses_current_sha_image_on_push(self):
        workflow = self.versions_workflow()
        job = workflow["jobs"]["build-image"]
        steps = job["steps"]
        publish_step = next(step for step in steps if step.get("id") == "publish")
        push_step = next(step for step in steps if step.get("name") == "Build and push SHA image")
        select_step = next(step for step in steps if step.get("id") == "select-image")
        test_job = workflow["jobs"]["test"]

        self.assertEqual("write", workflow["permissions"]["packages"])
        self.assertEqual("${{ steps.select-image.outputs.runner_image }}", job["outputs"]["runner_image"])
        self.assertIn("StefanMaron/MsDyn365Bc.On.Linux", publish_step["run"])
        self.assertEqual("steps.publish.outputs.enabled == 'true'", push_step["if"])
        self.assertTrue(push_step["with"]["push"])
        self.assertIn("${{ env.IMAGE }}:${{ github.sha }}", push_step["with"]["tags"])
        self.assertIn("runner_image=${{ env.IMAGE }}:${{ github.sha }}", select_step["run"])
        self.assertIn("runner_image=${{ env.IMAGE }}:latest", select_step["run"])
        self.assertEqual("${{ needs.build-image.outputs.runner_image }}", test_job["with"]["runner_image"])

    def test_test_versions_promotes_latest_after_validation(self):
        workflow = self.versions_workflow()
        promote = workflow["jobs"]["publish-latest"]

        self.assertEqual(
            ["test", "test-container-download", "test-webclient", "test-macos-overlay"],
            promote["needs"],
        )
        self.assertIn("github.event_name != 'pull_request'", promote["if"])
        self.assertIn("StefanMaron/MsDyn365Bc.On.Linux", promote["if"])
        script = "\n".join(step.get("run", "") for step in promote["steps"])
        self.assertIn("docker buildx imagetools create", script)
        self.assertIn("${IMAGE}:${{ github.sha }}", script)
        self.assertIn("${IMAGE}:latest", script)

    def test_parity_workflow_defaults_to_bc28_only(self):
        workflow = self.workflow()
        versions = workflow[True]["workflow_dispatch"]["inputs"]["versions"]["default"]
        prepare_script = workflow["jobs"]["prepare"]["steps"][0]["run"]
        build_script = "\n".join(
            step.get("run", "")
            for step in workflow["jobs"]["build-smoke-app"]["steps"]
        )

        self.assertEqual("28.1,28.2", versions)
        self.assertNotIn("27.", versions)
        self.assertNotIn("27) AL_TOOL=", build_script)
        self.assertIn("This parity workflow supports BC 28 only", prepare_script)

    def test_build_job_uploads_keep_app_ids_for_linux_startup(self):
        steps = self.workflow()["jobs"]["build-smoke-app"]["steps"]
        script = "\n".join(step.get("run", "") for step in steps)
        upload = next(step for step in steps if step.get("uses") == "actions/upload-artifact@v4")

        self.assertIn("scripts/resolve-keep-app-ids.py", script)
        self.assertIn("--app-json extensions/smoke-test/app.json", script)
        self.assertNotIn("extensions/TestRunnerExtension", script)
        self.assertNotIn("patched-test-runner", script)
        self.assertIn("keep-app-ids-${{ matrix.bc_version }}.txt", upload["with"]["path"])

    def test_build_job_keeps_microsoft_automation_api_v2_app(self):
        steps = self.workflow()["jobs"]["build-smoke-app"]["steps"]
        script = "\n".join(step.get("run", "") for step in steps)

        self.assertIn("AUTOMATION_API_V2_APP_ID=10cb69d9-bc8a-4d27-970a-9e110e9db2a5", script)
        self.assertIn("--extra-ids \"$AUTOMATION_API_V2_APP_ID\"", script)

    def test_build_job_does_not_upload_custom_test_runner_extension(self):
        steps = self.workflow()["jobs"]["build-smoke-app"]["steps"]
        script = "\n".join(step.get("run", "") for step in steps)
        upload = next(step for step in steps if step.get("uses") == "actions/upload-artifact@v4")

        self.assertNotIn("extensions/TestRunnerExtension", script)
        self.assertNotIn("patched-test-runner", script)
        self.assertNotIn("test-runner-extension-${{ matrix.bc_version }}.app", upload["with"]["path"])

    def test_linux_contract_does_not_use_custom_test_runner_extension(self):
        steps = self.linux_contract_job()["steps"]
        collect = next(step for step in steps if step.get("name") == "Collect Linux contract")
        script = collect["run"]

        self.assertNotIn("TestRunnerExtension", script)
        self.assertNotIn("patched-test-runner", script)

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

    def test_linux_contract_uses_standard_al_tooling_when_available(self):
        script = Path("parity/collect-linux-contract.sh").read_text(encoding="utf-8")

        self.assertIn("run-tests-altool.py", script)
        self.assertIn("Publishing smoke test app", script)
        self.assertNotIn("patched_test_runner_app", script)
        self.assertNotIn("load_artifact_apps", script)
        self.assertNotIn("Business Foundation Test Libraries", script)

    def test_linux_contract_records_smoke_app_publish_failure_as_contract_data(self):
        script = Path("parity/collect-linux-contract.sh").read_text(encoding="utf-8")

        self.assertIn("tests.runnerSetup=smoke app publish failed", script)
        self.assertIn("total=0 passed=0 failed=1 skipped=0", script)
        self.assertIn("exit 0", script)

    def test_custom_run_tests_script_is_not_shipped(self):
        self.assertFalse(Path("scripts/run-tests.sh").exists())

    def test_reusable_workflow_uses_runtime_checkout_for_standard_test_helpers(self):
        steps = self.bc_test_from_source_workflow()["jobs"]["test"]["steps"]
        test_step = next(step for step in steps if step.get("name") == "Run AL tests with standard tooling")
        script = test_step["run"]

        self.assertIn('ALTOOL_TEST_SCRIPT="bc-runtime/scripts/run-tests-altool.py"', script)
        self.assertIn('SUMMARY_SCRIPT="bc-runtime/scripts/workflow-summary.sh"', script)
        self.assertIn('python3 "$ALTOOL_TEST_SCRIPT" --probe', script)
        self.assertIn('python3 "$ALTOOL_TEST_SCRIPT" \\', script)
        self.assertIn('bash "$SUMMARY_SCRIPT" begin TESTS', script)
        self.assertNotIn("python3 scripts/run-tests-altool.py", script)
        self.assertNotIn("bash scripts/workflow-summary.sh", script)


    def test_linux_diagnostics_are_captured_after_contract_collection(self):
        names = self.step_names()

        self.assertLess(names.index("Collect Linux contract"), names.index("Capture Linux diagnostics"))

    def test_linux_diagnostics_capture_healthcheck_state(self):
        steps = self.linux_contract_job()["steps"]
        diagnostics = next(step for step in steps if step.get("name") == "Capture Linux diagnostics")

        self.assertIn("docker inspect \"$(docker compose ps -q bc)\"", diagnostics["run"])
        self.assertIn("bc-inspect.json", diagnostics["run"])

    def test_build_job_does_not_build_patched_test_runner(self):
        steps = self.workflow()["jobs"]["build-smoke-app"]["steps"]
        script = "\n".join(step.get("run", "") for step in steps)

        self.assertNotIn("scripts/build-patched-test-runner.sh", script)
        self.assertNotIn("patched-test-runner-$BC_VERSION.app", script)

    def test_build_job_retries_al_compiler_download(self):
        steps = self.workflow()["jobs"]["build-smoke-app"]["steps"]
        script = "\n".join(step.get("run", "") for step in steps)

        self.assertIn("--retry 5", script)
        self.assertIn("--retry-all-errors", script)
        self.assertIn("--continue-at -", script)

    def test_compare_waits_for_both_contract_jobs_to_succeed(self):
        compare = self.workflow()["jobs"]["compare-contracts"]

        self.assertIn("needs.linux-contract.result == 'success'", compare["if"])
        self.assertIn("needs.windows-contract.result == 'success'", compare["if"])

    def test_build_image_push_is_explicitly_opt_in(self):
        workflow = self.build_image_workflow()
        dispatch = workflow[True]["workflow_dispatch"]
        push_input = dispatch["inputs"]["push_image"]

        self.assertEqual("false", push_input["default"])
        self.assertIn("steps.publish.outputs.enabled == 'true'", self._build_image_step("Log in to GHCR")["if"])
        self.assertIn("steps.publish.outputs.enabled == 'true'", self._build_image_step("Build and push image")["if"])

    def test_build_image_push_fallback_builds_without_registry_write(self):
        build_only = self._build_image_step("Build image")

        self.assertFalse(build_only["with"]["push"])
        self.assertEqual("type=gha", build_only["with"]["cache-from"])
        self.assertEqual("type=gha,mode=max", build_only["with"]["cache-to"])

    def test_build_image_workflow_is_manual_only_to_avoid_duplicate_push_builds(self):
        workflow = self.build_image_workflow()

        self.assertIn("workflow_dispatch", workflow[True])
        self.assertNotIn("push", workflow[True])

    def _build_image_step(self, name):
        steps = self.build_image_workflow()["jobs"]["build-push"]["steps"]
        return next(step for step in steps if step.get("name") == name)


if __name__ == "__main__":
    unittest.main()
