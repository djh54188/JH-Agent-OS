#!/bin/bash
# usage: failed_ids.sh <suite log>  -> sorted unique FAIL/ERROR test ids
grep -E '^(FAIL|ERROR): ' "$1" | sed -E 's/^(FAIL|ERROR): //' | sort -u
