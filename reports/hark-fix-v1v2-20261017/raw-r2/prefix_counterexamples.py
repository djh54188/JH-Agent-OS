"""Run the R1/R2 counter-examples from tests/test_verifier_mutation_harness.py
against the f7e4f59 harness (pre-fix) to record what it concluded."""
import importlib.util, json, sys
from pathlib import Path
repo_root, old_dir = Path(sys.argv[1]), Path(sys.argv[2])
sys.path.insert(0, str(repo_root / "tests"))
import test_verifier_mutation_harness as t
class Maker(t.SyntheticRepoMixin):
    def addCleanup(self, *a, **k): pass
out = {}
for name in ("verify_phase4_guards", "verify_jh006_guards"):
    spec = importlib.util.spec_from_file_location("old_" + name, old_dir / f"{name}.py")
    m = importlib.util.module_from_spec(spec); spec.loader.exec_module(m)
    r1 = m.evaluate_mutations(Maker().make_repo(), [c for c in t.WORDING_CASES if c["name"] in
                              ("equal_whole_message", "equal_dict_message", "equal_exception_text_multiline", "assert_in_multiline")])
    r2 = m.evaluate_mutations(Maker().make_repo(), [t.CASES[0], t.BASELINE_CASES[0]])
    out[name] = {"R1_wording_only": {"verdicts": {r["guard"]: r["verdict"] for r in r1["rows"]},
                                     "verdict": r1["verdict"], "exit_code": r1["exit_code"]},
                 "R2_red_assert_baseline": {"verdicts": {r["guard"]: r["verdict"] for r in r2["rows"]},
                                            "verdict": r2["verdict"], "exit_code": r2["exit_code"]}}
print(json.dumps(out, indent=2))
