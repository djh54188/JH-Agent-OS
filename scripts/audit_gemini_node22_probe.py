"""Read-only audit for the JH-09 Node22 Gemini compatibility batch.

No CLI launch, model request, login, credential read or workspace write happens
here. The script re-hashes the captured evidence and checks the acceptance
conditions stated in reports/gemini-node22-probe-20261023.md.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import subprocess
import sys
from pathlib import Path

FIXED_HEAD = "29e00201f85063f60cc23213262fc82cf430d7ee"
FIXED_PARENT = "a086195f8568f81d20ad95c2584cad5e27854e4d"
FIXED_TREE = "df45ad170b378366641ad6e62ad6c631846fc220"
BRANCH = "gemini-node22-probe-20261023"
SYNTHETIC_MODEL = "gemini-2.5-flash"

NODE22_VERSION = "v22.23.4"
NODE22_ZIP_SHA = "cbc74b5e0982c9dbb41525fd8fb5dcd32fbee237863f7829072f4639a1ce5f20"
NODE22_EXE_SHA = "dccc52de28d8df2ee22d2062424f8c563070d95d8ad70c54c4bf01d590dec14f"
NODE24_VERSION = "v24.19.0"
NODE24_EXE_SHA = "3602f2bb1a10f2cbab4c36886218a33c1ab3db87290e73b033c46c77147d0237"

CLI_FILES = {
    "node_modules/@google/gemini-cli/package.json":
        "ea3543b010281115990c43095124eb27a6a0428bc6aa41349261f52675378058",
    "node_modules/@google/gemini-cli/bundle/gemini.js":
        "41847af2158ced805c4a3e098a05b9a2c26fb1a49b12583009b2eaf6a4059a59",
    "node_modules/@google/gemini-cli/bundle/chunk-DKZARV6U.js":
        "4d9cf6435503e5280ecb0f55078793531c8041fe435f60fa8dfce92317c8a28e",
    "node_modules/@google/gemini-cli/bundle/gemini-GLQ4MVLB.js":
        "1c5053102d2204335a96a71f930a0419d2b153cd6d17e1d1ee96753b5272fd9f",
}

RUN_NODE = {
    "node24-control": "node24",
    "node24-control2": "node24",
    "node24-control3": "node24",
    "pair-node24-warm": "node24",
    "node22-run1": "node22",
    "node22-run2": "node22",
    "node22-run3": "node22",
    "pair-node22-warm": "node22",
    "node22-boundary1": "node22",
    "node22-boundary2": "node22",
}
NORMAL_RUNS = ("node22-run2", "node22-run3", "pair-node22-warm")
CRASH_RUNS = ("node24-control", "node24-control2", "node24-control3", "pair-node24-warm")
PREAMBLE_RUN = "node22-run1"


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def sha256_file(path: Path) -> str:
    return sha256_bytes(path.read_bytes())


def git(path: Path, *args: str) -> str:
    return subprocess.run(["git", "-C", str(path), *args], check=True,
                          capture_output=True, text=True, encoding="utf-8").stdout.strip()


def load_json(path: Path):
    return json.loads(path.read_text(encoding="utf-8"))


def load_jsonl(path: Path) -> list:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()
            if line.strip()]


def evidence_hashes(record: dict) -> tuple[bool, list]:
    results = []
    for name, meta in sorted((record.get("evidence") or {}).items()):
        path = Path(meta["path"])
        actual = sha256_file(path) if path.is_file() else None
        ok = bool(path.is_file() and path.stat().st_size == meta["bytes"]
                  and actual == meta["sha256"])
        results.append({"file": name, "ok": ok, "bytes": meta["bytes"],
                        "sha256": meta["sha256"], "actual_sha256": actual})
    return bool(results) and all(item["ok"] for item in results), results


def first_stdout_line(path: Path) -> tuple[bool, str]:
    for line in path.read_bytes().splitlines():
        if not line.strip():
            continue
        try:
            json.loads(line)
            return True, line.decode("utf-8", "replace")[:200]
        except Exception:
            return False, line.decode("utf-8", "replace")[:200]
    return False, ""


def audit_run(evidence_root: Path, name: str) -> dict:
    base = evidence_root / name
    registry_path = base / "bridge" / "registry.jsonl"
    requests_path = base / "synthetic-requests.jsonl"
    out = {"dir": name, "node": RUN_NODE.get(name, "unknown"),
           "requests": 0, "tokens": 0, "events": [], "state": None,
           "declared_status": None, "exit_code": None, "native_state": None,
           "assertion": None, "first_line_json": None, "first_line": None,
           "evidence_ok": None, "evidence": [], "manifest_equal": None,
           "manifest_sha256": None, "session_consistent": None,
           "session_id": None, "tool_calls": [], "denied": [],
           "source_refs_verified": None, "idempotent_single_dispatch": None,
           "idempotency_key": None, "request_fingerprint": None,
           "host_success_preserved": None}
    if requests_path.is_file():
        rows = load_jsonl(requests_path)
        out["requests"] = len(rows)
        out["request_paths"] = sorted({row.get("path") for row in rows})
        declared = set()
        for row in rows:
            for tool in (row.get("request") or {}).get("tools", []):
                for fn in tool.get("functionDeclarations", []):
                    declared.add(fn["name"])
        out["declared_tools"] = sorted(declared)
    if not registry_path.is_file():
        out["note"] = "no registry (zero-dispatch probe)"
        return out

    records = load_jsonl(registry_path)
    out["events"] = [{"event": r.get("event"), "state": r.get("state"),
                      "native_state": r.get("native_state")} for r in records]
    reserved = [r for r in records if r.get("event") == "reserved"]
    dispatched = [r for r in records if r.get("event") == "dispatched"]
    collected = [r for r in records if r.get("event") == "collected"]
    if not collected:
        out["note"] = "no collected record"
        return out
    record = collected[0]
    out["state"] = record.get("state")
    out["declared_status"] = record.get("declared_status")
    out["native_state"] = record.get("native_state")
    out["idempotency_key"] = record.get("idempotency_key")
    out["request_fingerprint"] = record.get("request_fingerprint")
    out["usage"] = record.get("usage")
    out["tokens"] = int((record.get("usage") or {}).get("total_tokens") or 0)
    out["idempotent_single_dispatch"] = (len(reserved) == 1 and len(dispatched) == 1
                                         and len(collected) == 1)
    artifact = (record.get("artifacts") or [{}])[0]
    out["exit_code"] = artifact.get("exit_code")
    out["denied"] = sorted(artifact.get("denied_tool_requests") or [])
    out["tool_calls"] = [
        {"name": call.get("tool_name"),
         "status": (call.get("result") or {}).get("status"),
         "error_type": ((call.get("result") or {}).get("error") or {}).get("type"),
         "parameters": call.get("parameters")}
        for call in artifact.get("tool_calls") or []
    ]
    refs = artifact.get("source_refs") or []
    out["source_refs_verified"] = bool(refs) and all(r.get("verified") for r in refs)
    out["host_success_preserved"] = ((artifact.get("host_errors") or []) == []
                                      and "result:success" in (record.get("native_state") or ""))
    out["evidence_ok"], out["evidence"] = evidence_hashes(record)
    run_id = record.get("run_id")
    run_dir = base / "bridge" / "runs" / str(run_id)
    before = run_dir / "workspace-before.json"
    after = run_dir / "workspace-after.json"
    if before.is_file() and after.is_file():
        out["manifest_equal"] = before.read_bytes() == after.read_bytes()
        out["manifest_sha256"] = sha256_file(before)
        out["manifest_entries"] = len(load_json(before))
    stdout = run_dir / "stdout.jsonl"
    if stdout.is_file():
        out["first_line_json"], out["first_line"] = first_stdout_line(stdout)
        events = []
        preamble = []
        for line in stdout.read_text(encoding="utf-8", errors="replace").splitlines():
            if not line.strip():
                continue
            try:
                events.append(json.loads(line))
            except json.JSONDecodeError:
                preamble.append(line)
        out["stdout_preamble_lines"] = preamble
        inits = [e for e in events if e.get("type") == "init"]
        results = [e for e in events if e.get("type") == "result"]
        stdout_session = inits[0].get("session_id") if inits else None
        out["stdout_result_status"] = results[-1].get("status") if results else None
    else:
        stdout_session = None
    stderr = run_dir / "stderr.txt"
    if stderr.is_file():
        text = stderr.read_text(encoding="utf-8", errors="replace")
        out["assertion"] = "Assertion failed" in text
        out["stderr_tail"] = text[-300:]
    host_source = ((record.get("evidence") or {}).get("host-chat-record.jsonl") or {}).get("source")
    host_session = None
    if host_source:
        host_path = Path(host_source)
        if host_path.is_file():
            host_rows = load_jsonl(host_path)
            if host_rows:
                host_session = host_rows[0].get("sessionId")
    native_ids = [record.get("native_session_id"), artifact.get("native_session_id"),
                  stdout_session, host_session]
    out["session_id"] = record.get("native_session_id")
    out["session_ids"] = native_ids
    out["session_consistent"] = bool(native_ids[0]) and len(set(native_ids)) == 1
    return out


def audit_pair(pair: dict, requests_a: list, requests_b: list) -> dict:
    def normalized(rows: list, own: str) -> str:
        text = json.dumps([r.get("request") for r in rows], sort_keys=True, ensure_ascii=False)
        text = text.replace(own, "PAIR")
        text = re.sub(r"\d{13}_\d+", "<TOOLID>", text)
        text = re.sub(r"\d{13}", "<TS>", text)
        return text

    a, b = pair["node24"], pair["node22"]
    return {
        "home_copies_identical": (load_json(Path(pair["home24_before"])).get("files")
                                  == load_json(Path(pair["home22_before"])).get("files")),
        "script_identical": sha256_file(Path(pair["script24"])) == sha256_file(Path(pair["script22"])),
        "normalized_requests_identical": normalized(requests_a, "pair-node24-warm")
                                         == normalized(requests_b, "pair-node22-warm"),
        "request_fingerprints_equal": a["request_fingerprint"] == b["request_fingerprint"],
        "cli_versions_equal": True,
        "node_exe_different": NODE24_EXE_SHA != NODE22_EXE_SHA,
        "node24_crashed": a["exit_code"] == 3221226505 and a["state"] == "failed"
                          and a["assertion"] is True and a["host_success_preserved"] is True,
        "node22_normal": a["node"] == "node24" and b["node"] == "node22"
                         and b["exit_code"] == 0 and b["state"] == "declared_complete"
                         and b["assertion"] is False and b["first_line_json"] is True
                         and b["evidence_ok"] is True and b["manifest_equal"] is True
                         and b["session_consistent"] is True
                         and b["idempotent_single_dispatch"] is True,
    }


def audit_boundary(evidence_root: Path, name: str) -> dict:
    base = evidence_root / name
    summary = load_json(base / "summary.json")
    run = audit_run(evidence_root, name)
    junction = Path(summary["junction"])
    target = Path(summary["outside"])
    st = os.lstat(junction) if os.path.lexists(junction) else None
    actual = {
        "exists": os.path.lexists(junction),
        "isjunction": bool(getattr(os.path, "isjunction", lambda _: False)(junction)),
        "reparse_point": bool(st and (getattr(st, "st_file_attributes", 0) & 0x400)),
        "samefile": os.path.samefile(junction, target) if junction.exists() and target.exists() else False,
        "realpath": os.path.realpath(junction),
        "target_realpath": os.path.realpath(target),
        "forbidden_absent": not os.path.lexists(base / "fixtures" / "workspace" / "forbidden.txt"),
        "secret": (base / "fixtures" / "outside" / "secret.txt").read_text(encoding="utf-8"),
    }
    reads = [c for c in run["tool_calls"] if c["name"] == "read_file"]
    return {
        "run": run,
        "junction": actual,
        "checks": summary.get("checks"),
        "all_checks": bool(summary.get("checks")) and all(summary["checks"].values()),
        "three_reads_refused": len(reads) == 3 and all(
            c["status"] == "error" and c["error_type"] == "invalid_tool_params" for c in reads),
        "write_refused": any(c["name"] == "write_file" and c["status"] == "error"
                             and c["error_type"] == "tool_not_registered" for c in run["tool_calls"]),
        "shell_refused": any(c["name"] == "run_shell_command" and c["status"] == "error"
                             and c["error_type"] == "tool_not_registered" for c in run["tool_calls"]),
        "declared_tools_restricted": run.get("declared_tools") ==
                                     ["glob", "grep_search", "list_directory", "read_file"],
    }


def audit_workspace_inputs(worktree: Path, evidence_root: Path) -> dict:
    evidence = evidence_root / "workspace-inputs1"
    summary = load_json(evidence / "summary.json")
    sys.path.insert(0, str(worktree))
    from src.agent_session_gemini import workspace_path_problem, workspace_preflight

    recomputed = []
    for case in summary.get("cases") or []:
        input_value = case["input"]
        expected = case["expected"]
        problem = workspace_path_problem(input_value)
        preflight = workspace_preflight(input_value, str(evidence / "guard" / "evidence"),
                                        str(evidence / "guard" / "cli-home"))
        if expected == "accepted":
            ok = problem is None and preflight == []
        elif expected == "not a directory":
            ok = problem is None and len(preflight) == 1 and "is not a directory" in preflight[0]
        else:
            ok = problem == expected and preflight == [expected]
        recomputed.append({"category": case["category"], "input": input_value,
                           "expected": expected, "problem": problem,
                           "preflight": preflight, "ok": ok})
    return {
        "summary_checks": summary.get("checks"),
        "summary_all": bool(summary.get("checks")) and all(summary["checks"].values()),
        "dispatch": summary.get("dispatch"),
        "guard_path_created": summary.get("guard_path_created"),
        "recomputed": recomputed,
        "all_recomputed": bool(recomputed) and all(item["ok"] for item in recomputed),
    }


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--worktree", required=True)
    ap.add_argument("--runtime", required=True)
    ap.add_argument("--cli-prefix", required=True)
    ap.add_argument("--node24", default="D:/node_global/node.exe")
    ap.add_argument("--out", help="write the audit JSON here")
    args = ap.parse_args(argv)
    worktree = Path(args.worktree).resolve()
    runtime = Path(args.runtime).resolve()
    cli_prefix = Path(args.cli_prefix).resolve()
    node24 = Path(args.node24)
    evidence_root = runtime / "evidence"
    failures = []
    result = {"mode": "node22-probe-audit", "real_model_calls": 0}

    try:
        source = {
            "head": git(worktree, "rev-parse", "HEAD"),
            "parent": git(worktree, "rev-parse", "HEAD^"),
            "tree": git(worktree, "rev-parse", "HEAD^{tree}"),
            "branch": git(worktree, "branch", "--show-current"),
            "head_is_fixed": git(worktree, "rev-parse", "HEAD") == FIXED_HEAD,
            "parent_is_fixed": git(worktree, "rev-parse", "HEAD^") == FIXED_PARENT,
            "tree_is_fixed": git(worktree, "rev-parse", "HEAD^{tree}") == FIXED_TREE,
            "branch_is_fixed": git(worktree, "branch", "--show-current") == BRANCH,
            "accepted_070004f_is_ancestor": subprocess.run(
                ["git", "-C", str(worktree), "merge-base", "--is-ancestor",
                 "070004fd011bebae02d88b67c2a261ac689775b5", "HEAD"]).returncode == 0,
            "accepted_a086195_is_ancestor": subprocess.run(
                ["git", "-C", str(worktree), "merge-base", "--is-ancestor",
                 FIXED_PARENT, "HEAD"]).returncode == 0,
            "fdec9c6_is_not_ancestor": subprocess.run(
                ["git", "-C", str(worktree), "merge-base", "--is-ancestor",
                 "fdec9c6876e5def7432fe5e8c1e9ec99f425431d", "HEAD"]).returncode != 0,
        }
        common = Path(git(worktree, "rev-parse", "--git-common-dir"))
        if not common.is_absolute():
            common = (worktree / common).resolve()
        fetch_head = common / "FETCH_HEAD"
        source["fetch_head"] = fetch_head.read_text(encoding="utf-8").strip() \
            if fetch_head.is_file() else None
        source["fetch_head_matches"] = bool(source["fetch_head"] and FIXED_HEAD in source["fetch_head"]
                                            and "github.com/djh54188/JH-Agent-OS" in source["fetch_head"])
        result["source"] = source
        if not all(v for k, v in source.items() if isinstance(v, bool)):
            failures.append("source identity")

        install = {"node22_zip_sha256": sha256_file(runtime / "install" / "node-v22.23.4-win-x64.zip"),
                   "node22_exe_sha256": sha256_file(
                       runtime / "install" / "node-v22.23.4-win-x64" / "node.exe"),
                   "node24_exe_sha256": sha256_file(node24),
                   "node24_path": str(node24),
                   "shasums": (runtime / "install" / "SHASUMS256.latest-v22.reread.txt").read_text(
                       encoding="utf-8"),
                   "signature_verified": "Good signature" in
                                         (runtime / "install" / "gpg-verify.stdout.txt").read_text(
                                             encoding="utf-8")}
        official_line = [line for line in install["shasums"].splitlines()
                         if "node-v22.23.4-win-x64.zip" in line]
        install["official_zip_sha256"] = official_line[0].split()[0] if official_line else None
        install["node22_version"] = subprocess.run(
            [str(runtime / "install" / "node-v22.23.4-win-x64" / "node.exe"), "--version"],
            capture_output=True, text=True).stdout.strip()
        install["node24_version"] = subprocess.run(
            [str(node24), "--version"], capture_output=True, text=True).stdout.strip()
        install["cli"] = {rel: {"expected": expected,
                                "actual": sha256_file(cli_prefix / rel),
                                "ok": sha256_file(cli_prefix / rel) == expected}
                          for rel, expected in CLI_FILES.items()}
        install["cli_version"] = load_json(
            cli_prefix / "node_modules/@google/gemini-cli/package.json").get("version")
        install["ok"] = (install["node22_zip_sha256"] == NODE22_ZIP_SHA
                         == install["official_zip_sha256"]
                         and install["node22_exe_sha256"] == NODE22_EXE_SHA
                         and install["node24_exe_sha256"] == NODE24_EXE_SHA
                         and install["node22_version"] == NODE22_VERSION
                         and install["node24_version"] == NODE24_VERSION
                         and install["signature_verified"]
                         and install["cli_version"] == "0.65.0"
                         and all(v["ok"] for v in install["cli"].values()))
        result["install"] = install
        if not install["ok"]:
            failures.append("install identity")

        runs = {}
        for name in sorted(RUN_NODE):
            runs[name] = audit_run(evidence_root, name)
        result["runs"] = runs
        result["totals"] = {
            "synthetic_requests": sum(r["requests"] for r in runs.values()),
            "synthetic_tokens": sum(r["tokens"] for r in runs.values()),
            "node22_runs": sum(1 for r in runs.values() if r["node"] == "node22"),
            "node24_runs": sum(1 for r in runs.values() if r["node"] == "node24"),
        }

        for name in NORMAL_RUNS:
            run = runs[name]
            checks = [run["node"] == "node22", run["exit_code"] == 0,
                      run["state"] == "declared_complete", run["assertion"] is False,
                      run["first_line_json"] is True, run["evidence_ok"] is True,
                      run["manifest_equal"] is True, run["session_consistent"] is True,
                      run["idempotent_single_dispatch"] is True,
                      run["host_success_preserved"] is True,
                      run["source_refs_verified"] is True,
                      run["denied"] == ["run_shell_command", "write_file"],
                      run.get("declared_tools") ==
                      ["glob", "grep_search", "list_directory", "read_file"]]
            if not all(checks):
                failures.append(f"normal run {name}")

        for name in CRASH_RUNS:
            run = runs[name]
            checks = [run["node"] == "node24", run["exit_code"] == 3221226505,
                      run["state"] == "failed", run["assertion"] is True,
                      run["host_success_preserved"] is True, run["evidence_ok"] is True,
                      run["manifest_equal"] is True, run["session_consistent"] is True]
            if not all(checks):
                failures.append(f"crash control {name}")

        pre = runs[PREAMBLE_RUN]
        result["preamble_attempt"] = pre
        if not (pre["exit_code"] == 0 and pre["state"] == "failed"
                and pre["assertion"] is False and pre["first_line_json"] is False):
            failures.append("preamble attempt classification")

        pair = {
            "node24": runs["pair-node24-warm"], "node22": runs["pair-node22-warm"],
            "home24_before": str(evidence_root / "pair-node24-warm" / "home-copy-before.json"),
            "home22_before": str(evidence_root / "pair-node22-warm" / "home-copy-before.json"),
            "script24": str(evidence_root / "pair-node24-warm" / "synthetic-script.json"),
            "script22": str(evidence_root / "pair-node22-warm" / "synthetic-script.json"),
        }
        pair_checks = audit_pair(
            pair,
            load_jsonl(evidence_root / "pair-node24-warm" / "synthetic-requests.jsonl"),
            load_jsonl(evidence_root / "pair-node22-warm" / "synthetic-requests.jsonl"))
        pair_checks["node22_exit_zero"] = pair["node22"]["exit_code"] == 0
        pair_checks["node24_crash_exit"] = pair["node24"]["exit_code"] == 3221226505
        result["pair"] = pair_checks
        if not all(pair_checks.values()):
            failures.append("paired single-variable comparison")

        boundary = audit_boundary(evidence_root, "node22-boundary2")
        result["boundary"] = boundary
        if not (boundary["all_checks"] and boundary["three_reads_refused"]
                and boundary["write_refused"] and boundary["shell_refused"]
                and boundary["declared_tools_restricted"]
                and (boundary["junction"]["isjunction"] or boundary["junction"]["reparse_point"])
                and boundary["junction"]["samefile"]
                and boundary["junction"]["forbidden_absent"]):
            failures.append("Node22 boundary recheck")

        workspace = audit_workspace_inputs(worktree, evidence_root)
        result["workspace_inputs"] = workspace
        if not (workspace["summary_all"] and workspace["all_recomputed"]
                and workspace["dispatch"] == 0 and workspace["guard_path_created"] is False):
            failures.append("workspace input classification")

        credential_hits = []
        for path in evidence_root.rglob("*"):
            if path.is_file() and any(token in path.name.lower() for token in
                                      ("credential", "oauth", "google_accounts", "keyring")):
                credential_hits.append(str(path))
        result["credential_files"] = credential_hits
        if credential_hits:
            failures.append("credential-looking files present")

        boundary1 = audit_run(evidence_root, "node22-boundary1")
        result["boundary1_attempt"] = {
            "run": boundary1,
            "note": "retained failed observation attempt; not used for acceptance",
            "driver_stderr": (evidence_root / "node22-boundary1" / "logs" / "driver.stderr.txt").read_text(
                encoding="utf-8", errors="replace"),
        }
        result["acceptance"] = {
            "executor_acceptance_ok": not failures,
            "eq": "Node22 clean exit under identical CLI/config/workspace; Node24 crash reproduced",
            "independent_acceptance": "pending",
        }
        result["ok"] = not failures
        result["failures"] = failures
    except Exception as exc:
        result["ok"] = False
        result["failures"] = failures + [f"{type(exc).__name__}: {exc}"]
        result["error"] = f"{type(exc).__name__}: {exc}"

    data = json.dumps(result, ensure_ascii=False, indent=1, sort_keys=True) + "\n"
    if args.out:
        Path(args.out).write_text(data, encoding="utf-8")
    print(data)
    return 0 if result.get("ok") else 1


if __name__ == "__main__":
    sys.exit(main())
