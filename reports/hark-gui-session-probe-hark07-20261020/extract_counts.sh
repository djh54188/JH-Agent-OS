#!/bin/bash
# Re-derive every number in the Hark-派单-07 report from the raw logs in logs/.
cd "$(dirname "$0")"
for f in logs/*.txt; do
  printf '%s\t%s\t%s\t%s\n' "$(basename $f)" "$(grep -E '^# exit_code' $f | head -1)" \
    "$(grep -E '^Ran [0-9]+ tests in' $f | tail -1) $(grep -E '^(OK|FAILED \()' $f | tail -1)" \
    "$(grep -E '^  "(detected|redundant|verdict)"' $f | tr -d ' \n') js_ok=$(grep -cE '^(PASS|ok )' $f)"
done
for m in noproxy proxy; do
  ./failed_ids.sh logs/suite-baseline-$m.txt > /tmp/h07b; ./failed_ids.sh logs/suite-postcommit-$m.txt > /tmp/h07p
  echo "suite $m: unique failing ids baseline=$(wc -l </tmp/h07b) postcommit=$(wc -l </tmp/h07p) diff_lines=$(diff /tmp/h07b /tmp/h07p | wc -l)"
done
cat logs/frozen-probe-*-summary.txt
