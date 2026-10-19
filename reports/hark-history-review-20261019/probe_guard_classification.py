"""Independent outcome-class matrix; synthetic summaries, no test/model runs.

Usage: --repo CHECKOUT --out NEW_DIRECTORY. Never reuses an output directory.
"""
import argparse
import itertools
import json
import runpy
from pathlib import Path


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--repo", required=True)
    parser.add_argument("--out", required=True)
    args = parser.parse_args()
    repo, out = Path(args.repo).resolve(), Path(args.out).resolve()
    if out.exists():
        raise SystemExit("Refusing existing --out")
    guard = runpy.run_path(str(repo / "scripts/verify_jh020_run_history_guards.py"),
                          run_name="classification_probe")
    def summary(run=1, failures=0, errors=0, skipped=0):
        return dict(run=run, failures=failures, errors=errors, skipped=skipped)
    variants = {"pass": summary(), "assertion": summary(failures=1),
                "error": summary(errors=1), "mixed": summary(failures=1, errors=1),
                "skip": summary(skipped=1), "assertion_skip": summary(failures=1, skipped=1),
                "zero": summary(run=0), "extra_tests": summary(run=2, failures=1), "missing": None}
    rows = []
    for declared, control, mutant, diagnostic in itertools.product([False, True], variants, variants, variants):
        expected = ("control_failed" if control != "pass" else
                    "no_tests" if mutant in ("zero", "missing") else
                    "error" if mutant in ("error", "mixed") else
                    "skipped" if mutant in ("skip", "assertion_skip") else
                    "detected" if mutant == "assertion" else
                    "redundant" if declared and mutant == "pass" and diagnostic == "assertion" else
                    "survived")
        name = "registry_field_validation_skipped" if declared else "evidence_state_ignored"
        actual = guard["classify"](name, variants[control], variants[mutant], variants[diagnostic])
        verdict = guard["verdict"]([dict(classification=actual)])
        passed = actual == expected and verdict == ("pass" if expected in ("detected", "redundant") else "fail")
        rows.append(dict(declared=declared, control=control, mutant=mutant, diagnostic=diagnostic,
                         expected=expected, observed=actual, passed=passed))
    out.mkdir(parents=True)
    result = dict(synthetic=True, total=len(rows), all_expected=all(r["passed"] for r in rows),
                  model_calls=0, rows=rows)
    (out / "results.json").write_bytes(json.dumps(result, indent=2).encode("utf-8"))
    print(json.dumps({k: v for k, v in result.items() if k != "rows"}))
    return 0 if result["all_expected"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
