"""Hark-派单-09 assertion layer over the FROZEN 49a7d1e probe_workspace_binding.py output.

The frozen probe's exit 0 only means its observation finished, and its
`workspace_matches` is the simulator's own truth. This script re-reads, READ-ONLY,
the probe registry each case left under --out (status() never prepares,
dispatches or writes) and asserts what the PRODUCT now reports:

* absolute / relative / ./ / space+CJK: protocol cwd supplied absolute, equal to
  the planned workspace and to the simulated effective cwd; workspace.state
  verified; files [] with verified_empty true.
* parent segment (..): refused probe_dir_parent_segment, zero offline dispatches.
* native mismatch / missing / foreign-only / conflict: the matching
  workspace.state, files None, verified_empty None, echo still matched.

Exit 0 = every assertion held; exit 1 = at least one failed (printed).
Usage: python check_probe09.py --repo <candidate tree> --out <frozen probe out dir>
"""
import argparse
import json
import os
import sys
from pathlib import Path

EXPECTED = {
    "absolute": "verified", "relative": "verified", "relative_dot": "verified",
    "absolute_space": "verified", "relative_space": "verified",
    "relative_dotdot": "refused:probe_dir_parent_segment",
    "absolute_native_mismatch": "native_mismatch", "absolute_native_missing": "native_missing",
    "absolute_foreign_native": "native_foreign_only", "absolute_native_conflict": "native_conflict",
}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--repo", type=Path, required=True)
    ap.add_argument("--out", type=Path, required=True)
    args = ap.parse_args()
    repo, out = args.repo.resolve(), args.out.resolve()
    sys.path[:0] = [str(repo)]
    from src.app_api import AppInterface
    from src.session_probe import ProbeConfig
    if os.name != "nt":
        # Same test-only POSIX evidence seam the frozen probe used to WRITE these
        # files; without it POSIX absolute evidence paths read as "root-relative".
        sys.path.insert(0, str(repo / "tests"))
        import test_hark06_session_probe as fx
        import src.agent_session_codex as codex
        codex.is_special_path = fx._posix_evidence_seam
    frozen = json.loads((out / "results.json").read_text(encoding="utf-8"))
    rows, failures = [], []
    for row in frozen["rows"]:
        case = row["case"]
        want = EXPECTED[case]
        got = {"case": case, "expected": want}
        if want.startswith("refused:"):
            ok = row.get("refused") == want.split(":", 1)[1] and row.get("offline_dispatches") == 0
            got.update(refused=row.get("refused"), offline_dispatches=row.get("offline_dispatches"))
        else:
            probe_dir = Path(row["expected_workspace"]).parents[1]      # <probe>/ws/<id>
            api = AppInterface(db_path=out / "absent.sqlite3", runs_path=out / "readonly-history.jsonl",
                               session_probe=ProbeConfig(enabled=True, probe_dir=str(probe_dir)),
                               probe_runtime_check=lambda: None, probe_runner=lambda job: None)
            latest = api.session_probe_status()["latest"] or {}
            ws = latest.get("workspace") or {}          # absent on a pre-09 candidate -> fails
            got.update(state=ws.get("state"), files=latest.get("workspace_files"),
                       verified_empty=ws.get("verified_empty"), echo=latest.get("echo"),
                       outcome=latest.get("outcome"), planned=ws.get("planned"), dispatched=ws.get("dispatched"))
            ok = ws.get("state") == want and latest.get("echo") == "matched" \
                and ws.get("planned") == row["expected_workspace"]
            if want == "verified":
                protocol_ok = bool(row["protocol"]) and all(
                    os.path.isabs(p["supplied_cwd"]) and p["supplied_cwd"] == row["expected_workspace"]
                    == p["effective_native_cwd"] == p["process_cwd"] for p in row["protocol"])
                ok = ok and protocol_ok and latest.get("workspace_files") == [] and ws.get("verified_empty") is True
                got["protocol_consistent"] = protocol_ok
            else:
                ok = ok and latest.get("workspace_files") is None and "verified_empty" in ws \
                    and ws.get("verified_empty") is None and ws.get("files") is None
        got["ok"] = ok
        rows.append(got)
        if not ok:
            failures.append(case)
    missing = sorted(set(EXPECTED) - {r["case"] for r in frozen["rows"]})
    report = {"cases": len(rows), "failures": failures, "missing_cases": missing,
              "verdict": "pass" if not failures and not missing else "fail", "rows": rows}
    print(json.dumps(report, indent=2, ensure_ascii=False))
    return 0 if report["verdict"] == "pass" else 1


if __name__ == "__main__":
    sys.exit(main())
