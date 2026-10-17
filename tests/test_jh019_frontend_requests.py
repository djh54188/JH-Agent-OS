"""JH-019 frontend request-ownership checks (behavioural, offline).

Runs `tests/js/jh019_generation_harness.mjs`, which evaluates the real
gui/components.js and gui/views.js against a DOM stub and resolves the stubbed
API promises in a controlled order, asserting that:

* a late workbench reply cannot overwrite the sessions view;
* a late task-A detail reply cannot overwrite task B;
* a stale draft-list reply cannot clear or duplicate the rendered list.

That is a behaviour test over promise-ordering, not a source-string check.
Skipped only if Node.js is unavailable.
"""
import json
import shutil
import subprocess
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
HARNESS = ROOT / "tests" / "js" / "jh019_generation_harness.mjs"
NODE = shutil.which("node")


@unittest.skipIf(NODE is None, "node is not available")
class TestFrontendRequestOwnership(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.done = subprocess.run([NODE, str(HARNESS), "--json"], cwd=str(ROOT),
                                  capture_output=True, text=True, timeout=120)
        try:
            cls.report = json.loads(cls.done.stdout)
        except ValueError:                                  # pragma: no cover
            cls.report = None

    def test_harness_ran_and_reported(self):
        self.assertIsNotNone(self.report, f"no JSON verdict\n{self.done.stdout}\n{self.done.stderr}")
        self.assertEqual(self.report["harness"], "jh019-generation")

    def test_harness_passed(self):
        self.assertEqual(self.done.returncode, 0,
                         f"harness failed: {json.dumps(self.report, ensure_ascii=False)}")
        self.assertTrue(self.report["ok"])

    def test_all_three_scenarios_are_present_and_green(self):
        scenarios = self.report["scenarios"]
        self.assertEqual(set(scenarios), {
            "workbench_then_sessions_late_workbench",
            "task_switch_late_A_after_B",
            "draft_list_stale_empty_after_save"})
        for name, result in scenarios.items():
            self.assertTrue(result["ok"], f"{name}: {result.get('error')}")

    def test_scenarios_assert_the_intended_effects(self):
        scenarios = self.report["scenarios"]
        self.assertTrue(scenarios["workbench_then_sessions_late_workbench"]["detail"]["lateIgnored"])
        self.assertTrue(scenarios["task_switch_late_A_after_B"]["detail"]["lateAIgnored"])
        draft = scenarios["draft_list_stale_empty_after_save"]["detail"]
        self.assertEqual(draft["cardsAfterSave"], 1)
        self.assertEqual(draft["cardsAfterStale"], 1)       # stale empty reply changed nothing


if __name__ == "__main__":
    unittest.main()
