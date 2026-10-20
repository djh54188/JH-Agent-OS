#!/usr/bin/env bash
# Recompute every count in the report from the raw logs in this directory.
cd "$(dirname "$0")/logs"
for f in *.txt; do
  printf '%s | %s | %s | pass_lines=%s\n' "$f" "$(grep -E '^# exit_code' "$f")" \
    "$(grep -E '^Ran [0-9]+ tests|^OK|^FAILED' "$f" | tr '\n' ' ')" "$(grep -cE '^(PASS|ok )' "$f")"
done
for m in noproxy proxy; do
  b=$(grep -E '^(FAIL|ERROR): ' suite-baseline-$m.txt | sort); p=$(grep -E '^(FAIL|ERROR): ' suite-postcommit-$m.txt | sort)
  echo "suite $m failing ids: baseline=$(echo "$b" | grep -c .) postcommit=$(echo "$p" | grep -c .) identical=$([ "$b" = "$p" ] && echo yes || echo no)"
done
grep -hE '"(detected|redundant|verdict)":' hark06_guards.txt postcommit-hark06_guards.txt 2>/dev/null | head -3
