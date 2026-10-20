#!/bin/bash
# usage: run_suite.sh <repo> <label>   -> writes suite-<label>-{proxy,noproxy}.txt (header + raw output)
REPO="$1"; LABEL="$2"; OUT=/workspace/work/h07-logs
PY=/workspace/work/venv-h05/bin/python
cd "$REPO"
for MODE in proxy noproxy; do
  F="$OUT/suite-$LABEL-$MODE.txt"
  if [ "$MODE" = proxy ]; then PREFIX=""; else PREFIX="env -u HTTP_PROXY -u HTTPS_PROXY -u http_proxy -u https_proxy"; fi
  {
    echo "# label: $LABEL / $MODE"
    echo "# commit: $(git rev-parse HEAD)  tree-clean: $(test -z "$(git status --porcelain --untracked-files=no)" && echo yes || echo no)"
    echo "# command: $PREFIX python -m unittest discover -s tests -p \"test_*.py\""
    echo "# python: $($PY -c 'import sys,platform;print(sys.version.split()[0], platform.platform())')"
    echo "# jsonschema: $($PY -c 'import importlib.metadata as m;print(m.version("jsonschema"))')"
    echo "# HTTP(S)_PROXY set: $( [ "$MODE" = proxy ] && [ -n "$HTTPS_PROXY" ] && echo yes || echo no)"
    echo "# started_local: $(date)"
    echo "# ---- raw output below ----"
  } > "$F"
  $PREFIX $PY -m unittest discover -s tests -p "test_*.py" >> "$F" 2>&1
  RC=$?
  { echo "# ---- end raw output ----"; echo "# exit_code: $RC"; echo "# finished_local: $(date)"; } >> "$F"
done
