#!/bin/bash
# Re-derive every number in the Hark-派单-08 report from the raw logs in logs/.
cd "$(dirname "$0")"
for f in logs/*.txt; do
  printf '%s\t%s\t%s\t%s\n' "$(basename $f)" "$(grep -E '^# exit_code' $f | head -1)" \
    "$(grep -E '^Ran [0-9]+ tests in' $f | tail -1) $(grep -E '^(OK|FAILED \()' $f | tail -1)" \
    "$(grep -E '^  "(detected|redundant|verdict)"' $f | tr -d ' \n') js_ok=$(grep -cE '^(PASS|ok )' $f)"
done
for m in noproxy proxy; do
  ./failed_ids.sh logs/suite-baseline-$m.txt > /tmp/h08b; ./failed_ids.sh logs/suite-postcommit-$m.txt > /tmp/h08p
  echo "suite $m: unique failing ids baseline=$(wc -l </tmp/h08b) postcommit=$(wc -l </tmp/h08p) diff_lines=$(diff /tmp/h08b /tmp/h08p | wc -l)"
done
python3 compare_probe08.py
