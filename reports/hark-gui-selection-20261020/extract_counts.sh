#!/usr/bin/env bash
# Re-extract every count quoted in the Hark-派单-05 report from the committed raw logs.
# Usage: bash reports/hark-gui-selection-20261020/extract_counts.sh   (run from repo root)
set -u
D=reports/hark-gui-selection-20261020
L=$D/logs
echo "## frontend harnesses (pass lines / exit)"
for f in $L/baseline-jh019_generation_harness.txt $L/postcommit-jh019_generation_harness.txt; do
  echo "$(basename $f): PASS=$(grep -c '^PASS' $f) FAIL=$(grep -c '^FAIL' $f) $(grep exit_code $f)"; done
for f in $L/baseline-jh020_run_history_harness.txt $L/postcommit-jh020_run_history_harness.txt \
         $L/baseline-hark05_run_selection_harness.txt $L/postcommit-hark05_run_selection_harness.txt; do
  echo "$(basename $f): ok=$(grep -c '^ok ' $f) FAIL=$(grep -c '^FAIL' $f) $(grep exit_code $f)"; done
echo "## mutation verifiers"
for f in $L/baseline-verify_jh020_run_history_frontend_guards.txt $L/postcommit-verify_jh020_run_history_frontend_guards.txt \
         $L/postcommit-verify_hark05_selection_guards.txt; do
  echo "$(basename $f): $(grep -E '"(detected|redundant|verdict|control_ok)":' $f | grep -v '"detected": \(true\|false\)' | tr -d ' \n') $(grep exit_code $f)"; done
python3 - "$L/postcommit-verify_hark05_selection_guards.txt" <<'PY'
import json, sys
t = open(sys.argv[1], encoding="utf-8").read()
d = json.loads(t[t.index("{"):t.rindex("}") + 1])
for r in d["rows"]:
    print(" ", r["case"], "->", r["scenario"], "outcome=" + r["outcome"], "anchor_count=%d" % r["anchor_count"])
PY
echo "## full Python suite"
for f in $L/suite-*.txt; do
  echo "$(basename $f): $(grep -E '^Ran [0-9]+ tests' $f | tail -1 | cut -d' ' -f1-3) | $(grep -E '^(FAILED|OK)' $f | tail -1) | $(grep '^# exit_code' $f)"; done
for v in noproxy proxy; do
  diff <(grep -oE '^(ERROR|FAIL): .*' $L/suite-baseline-$v.txt | sort) \
       <(grep -oE '^(ERROR|FAIL): .*' $L/suite-postcommit-$v.txt | sort) >/dev/null \
    && echo "$v: failing id set identical (baseline vs postcommit), n=$(grep -cE '^(ERROR|FAIL): ' $L/suite-postcommit-$v.txt)" \
    || echo "$v: failing id sets DIFFER"; done
echo "## browser (postcommit)"
python3 - "$D/browser/postcommit-browser-results.json" <<'PY'
import json, sys
d = json.load(open(sys.argv[1], encoding="utf-8"))
print(" ", d["browser"], "|", d["platform"], "| python", d["python"], "|", d["notice"])
for w, r in d["widths"].items():
    for c in r["clicks"]:
        a, b = c["after"], c["before"]
        only = [x[0] for x in a["pressed"] if x[1] == "true" and x[2] == "true"]
        print("  %5s cols=%d %s scrollY %d->%d title_top=%d visible=%s overflowX=%d selected=%s requests=%d" % (
            w, a["layoutColumns"], c["run_id"][-5:], b["scrollY"], a["scrollY"], a["title"]["top"],
            a["title"]["visible"], a["overflowX"], only, len(r["click_requests"])))
PY
