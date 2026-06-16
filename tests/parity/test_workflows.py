import unittest
from pathlib import Path

import yaml


class ParityWorkflowTests(unittest.TestCase):
    def workflow(self):
        path = Path(".github/workflows/parity-windows-linux.yml")
        return yaml.safe_load(path.read_text(encoding="utf-8"))

    def linux_contract_job(self):
        return self.workflow()["jobs"]["linux-contract"]

    def step_names(self):
        return [step.get("name") or step.get("uses") for step in self.linux_contract_job()["steps"]]

    def test_linux_contract_uses_deps_only_startup_path(self):
        env = self.linux_contract_job()["env"]

        self.assertEqual("deps-only", env["BC_CLEAR_ALL_APPS"])
        self.assertEqual("true", env["BC_INCLUDE_TEST_TOOLKIT"])

    def test_linux_diagnostics_are_captured_after_contract_collection(self):
        names = self.step_names()

        self.assertLess(names.index("Collect Linux contract"), names.index("Capture Linux diagnostics"))


if __name__ == "__main__":
    unittest.main()
