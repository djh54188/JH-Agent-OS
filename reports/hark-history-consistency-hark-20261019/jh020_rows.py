"""Run each accepted JH-020 run-history mutation row in isolation (no control gate)."""
import sys, json
from contextlib import ExitStack
sys.path.insert(0, "."); sys.path.insert(0, "tests"); sys.argv = ["x"]
import scripts.verify_jh020_run_history_guards as g
rows = []
for name, patchers, test in g.MUTATIONS:
    ctrl = g.run_case(test)
    with ExitStack() as st:
        for p in patchers: st.enter_context(p)
        r = g.run_case(test)
    rows.append({"case": name, "control_ok": ctrl.wasSuccessful(), "mut_failures": len(r.failures),
                 "mut_errors": len(r.errors), "detected": bool(r.failures) and not r.errors})
print(json.dumps(rows, indent=1))
