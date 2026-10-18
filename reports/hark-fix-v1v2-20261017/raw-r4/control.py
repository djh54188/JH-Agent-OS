"""Run the R1/R2 counter-examples from tests/test_verifier_mutation_harness.py
against the meta control harness (pre-fix) to record what it concluded."""
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
                              ("bool_on_self_attribute", "bool_in_self_dict", "bool_wrapped_text_check")])
    out[name] = {"R3_wording_only": {"verdicts": {r["guard"]: r["verdict"] for r in r1["rows"]}, "evidence": {r["guard"]: r.get("mutant_evidence") for r in r1["rows"]},
                                     "verdict": r1["verdict"], "exit_code": r1["exit_code"]}}
print(json.dumps(out, indent=2))
