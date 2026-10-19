"""JH-020 run-history frontend behaviour checks (offline).

Runs `tests/js/jh020_run_history_harness.mjs`, which evaluates the real
gui/components.js and gui/views.js against a DOM stub and a stubbed JH.api. It
asserts how real history is rendered next to the simulated registry: the run
card fields, the live=false / browser_supplied=false source notice, the
"declared_complete is not success" wording, the distinct evidence states, and
run selection by explicit button id.

That is a behaviour test over the rendered DOM, not a source-string check.
Skipped only if Node.js is unavailable.
"""
import json
import shutil
import subprocess
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
HARNESS = ROOT / "tests" / "js" / "jh020_run_history_harness.mjs"
NODE = shutil.which("node")

EXPECTED_SCENARIOS = {
    "history_card_fields",
    "declared_complete_is_not_success",
    "simulated_distinct_from_history",
    "evidence_states_render_differently",
    "selecting_a_run_uses_its_id",
    "corrupt_source_shows_its_message",
    "absent_source_is_not_an_error",
    "detail_reverse_replies",
    "detail_same_id_repeat",
    "detail_late_error",
    "detail_unrequested_run_id",
    "detail_leave_view",
    "detail_reenter_view",
}


@unittest.skipIf(NODE is None, "node is not available")
class TestRunHistoryFrontend(unittest.TestCase):
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
        self.assertEqual(self.report["harness"], "jh020-run-history")

    def test_harness_passed(self):
        self.assertEqual(self.done.returncode, 0,
                         f"harness failed: {json.dumps(self.report, ensure_ascii=False)}")
        self.assertTrue(self.report["ok"])

    def test_every_scenario_is_present_and_green(self):
        scenarios = self.report["scenarios"]
        self.assertEqual(set(scenarios), EXPECTED_SCENARIOS)
        for name, result in scenarios.items():
            self.assertTrue(result["ok"], f"{name}: {result.get('error')}")


if __name__ == "__main__":
    unittest.main()
