#!/usr/bin/env bash
# usage: run_checks.sh <repo> <label>
REPO="$1"; LABEL="$2"; OUT=/workspace/work/h09-logs; PY=/workspace/work/venv-h05/bin/python
cd "$REPO"
run() { local name="$1"; shift; local f="$OUT/$LABEL-$name.txt"
  { echo "# label: $LABEL"; echo "# commit: $(git rev-parse HEAD)  tree-clean: $([ -z "$(git status --porcelain)" ] && echo yes || echo no)"
    echo "# command: $*"; echo "# python: $($PY -c 'import sys,platform;print(sys.version.split()[0], platform.platform())')  node: $(node --version)"
    echo "# started: $(date)"; echo "# ---- raw output below ----"; } > "$f"
  env -u HTTP_PROXY -u HTTPS_PROXY -u http_proxy -u https_proxy "$@" >> "$f" 2>&1; local rc=$?
  { echo "# ---- end raw output ----"; echo "# exit_code: $rc"; } >> "$f"; }
run jh019_harness node tests/js/jh019_generation_harness.mjs
run jh020_harness node tests/js/jh020_run_history_harness.mjs
run hark05_harness node tests/js/hark05_run_selection_harness.mjs
run jh020_frontend_guards $PY scripts/verify_jh020_run_history_frontend_guards.py
run hark05_selection_guards $PY scripts/verify_hark05_selection_guards.py
run jh020_run_history_guards $PY scripts/verify_jh020_run_history_guards.py
run jh020_session_guards $PY scripts/verify_jh020_session_guards.py
run jh018_contract $PY -m unittest -v tests.test_jh018_agent_session_contract
if [ -f tests/test_hark06_session_probe.py ]; then
  run hark06_tests $PY -m unittest -v tests.test_hark06_session_probe
  run hark06_harness node tests/js/hark06_session_probe_harness.mjs
  run hark06_guards $PY scripts/verify_hark06_session_probe_guards.py
fi
if [ -f tests/test_hark07_source_isolation.py ]; then
  run hark07_source_isolation $PY -m unittest -v tests.test_hark07_source_isolation
  run hark07_verifier_classification $PY -m unittest -v tests.test_hark07_verifier_classification
  run hark06_guards_repeat2 $PY scripts/verify_hark06_session_probe_guards.py
  run hark06_guards_repeat3 $PY scripts/verify_hark06_session_probe_guards.py
fi
run frozen_probe_a $PY /workspace/work/h07-frozen-copy/probe_boundaries.py --repo . --out /workspace/work/h09-probe07-$LABEL-a
run frozen_probe_b $PY /workspace/work/h07-frozen-copy/probe_boundaries.py --repo . --out /workspace/work/h09-probe07-$LABEL-b
if [ -f tests/test_hark08_cleanup_isolation.py ]; then
  run hark08_cleanup_isolation $PY -m unittest -v tests.test_hark08_cleanup_isolation
fi
run frozen08_probe_a $PY /workspace/work/h08-frozen/probe_cleanup.py --repo . --out /workspace/work/h09-probe08-$LABEL-a
run frozen08_probe_b $PY /workspace/work/h08-frozen/probe_cleanup.py --repo . --out /workspace/work/h09-probe08-$LABEL-b
if [ -f tests/test_hark09_workspace_binding.py ]; then
  run hark09_workspace_binding $PY -m unittest -v tests.test_hark09_workspace_binding
fi
export PYTHONDONTWRITEBYTECODE=1
run frozen09_probe_a $PY -X utf8 /workspace/work/h09-frozen/probe_workspace_binding.py --repo . --out $REPO/data/workspace-review-fresh-a
run frozen09_probe_b $PY -X utf8 /workspace/work/h09-frozen/probe_workspace_binding.py --repo . --out $REPO/data/workspace-review-fresh-b
run check09_a $PY /workspace/work/h09-check/check_probe09.py --repo . --out $REPO/data/workspace-review-fresh-a
run check09_b $PY /workspace/work/h09-check/check_probe09.py --repo . --out $REPO/data/workspace-review-fresh-b
unset PYTHONDONTWRITEBYTECODE
