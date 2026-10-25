"""Hark-派单-21 task01: MECHANICAL consistency check of one adapter run's raw evidence.

Inputs (all read-only): the fixed task package, the export manifest and exported
directory, and one evidence root written by GeminiCliAgentSessionAdapter.

What it checks (each item: pass / fail / unknown):
  package     task text and review baseline bytes match the package digests
  export      the directory holds exactly the manifest's files and bytes, from the
              pinned commit, with the pinned target blob
  registry    the registry is a legal adapter history (adapter's own validator)
  binding     the run's records: reserved -> dispatched -> collected, request
              snapshot = this package's task text / task id / new session /
              explicit model; the actual stdin echoed by the host = prefix + text
  workspace   the registered before/after manifests describe exactly the export
  evidence    stdout / stderr / host chat record bytes = registered size + sha256
  adapter     the adapter's own gates as recorded (exit 0, host success,
              declared_complete, model honoured, workspace unchanged, no
              unconfirmed tool request) and a re-parse of stdout agrees
  tools       every successful grep_search / read_file is paired with the host
              chat record and its content is compared with the exported bytes,
              line by line, in the host output format observed for CLI 0.65.0;
              unknown formats are reported as unknown, never as matched
  required    at least one verified grep hit and one verified read of the target
  refs        every path:line in the delivery exists in the export; for each core
              point B2/B3/B4/B6/B7 at least one reference falls in its range

What it does NOT do: judge whether the explanation is right. `semantic_review`
is always "required", and no field of the report says the task succeeded or was
accepted. It never re-dispatches, retries, changes keys, attempts or evidence
roots, starts a process, or reads a file outside the export / evidence root.

Exit codes: 0 every mechanical item passed (SEMANTIC REVIEW STILL REQUIRED);
1 at least one mechanical item failed; 3 none failed but at least one is
unknown; 2 could not run.
"""
from __future__ import annotations

import argparse
import ast
import hashlib
import json
import ntpath
import os
import posixpath
import re
import stat as _stat
import sys
from typing import Any, Dict, List, Optional, Tuple

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(os.path.dirname(HERE))
sys.path.insert(0, ROOT)

from src.agent_session_gemini import (  # noqa: E402  (read-only use of the adapter's parsers)
    associate, parse_stream_with_host_preamble, validate_registry_records)

SCHEMA = "hark21/gemini-task01/mechanical-check/v1"
PINNED_COMMIT = "f6bf47c59688c43d927dede01b59f772520815d8"
PINNED_BLOB = "31d3cd7c111dfdd0d33a65ad097f9cbe3d0fac38"
EVIDENCE_NAMES = ("stdout.jsonl", "stderr.txt", "host-chat-record.jsonl")
CORE_POINTS = ("B2", "B3", "B4", "B6", "B7")

PASS, FAIL, UNKNOWN = "pass", "fail", "unknown"

# Guard-weakening hook for tests ONLY (tests/test_hark21_gemini_task01.py adds names
# to it, one at a time, after a control run). Production commands never set it.
WEAKEN: set = set()

# Host output formats observed for the pinned CLI 0.65.0 (real tools, synthetic
# model endpoint; reports/hark-gemini-bridge-20261021/host-probe). Anything else
# is "unknown format".
GREP_HEAD = re.compile(r'^Found (\d+) (match|matches) for pattern "(.*)" in path "(.*)"'
                       r'(?: \(filter: "(.*)"\))?:$')
GREP_LINE = re.compile(r"^L(\d+)([:-]) (.*)$", re.S)
READ_TRUNC = re.compile(r"^\nIMPORTANT: The file content has been truncated\.\n"
                        r"Status: Showing lines (\d+)-(\d+) of (\d+) total lines\.\n"
                        r"Action: [^\n]*\n\n--- FILE CONTENT \(truncated\) ---\n(.*)$", re.S)
READ_SUMMARY = re.compile(r"^Read lines (\d+)-(\d+) of (\d+) from (.+)$")
SOURCE_REF = re.compile(r"(?<![\w/\\.-])((?:[A-Za-z]:)?(?:[\w.-]+[/\\])*[\w.-]+\.[A-Za-z0-9]{1,8})"
                        r":(\d+)(?:-(\d+))?")


def sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def git_blob_id(data: bytes) -> str:
    return hashlib.sha1(b"blob %d\0" % len(data) + data).hexdigest()


def read_bytes(path: str) -> bytes:
    with open(path, "rb") as fh:
        return fh.read()


class Report:
    def __init__(self) -> None:
        self.items: List[Dict[str, Any]] = []

    def add(self, check: str, status: str, detail: str, **extra: Any) -> None:
        item = {"check": check, "status": status, "detail": detail}
        item.update(extra)
        self.items.append(item)

    def overall(self) -> str:
        statuses = {i["status"] for i in self.items}
        if FAIL in statuses:
            return "fail"
        if UNKNOWN in statuses or not self.items:
            return "undetermined"
        return "all_pass"


# ---------------------------------------------------------------- gates -----
# Each gate is a module-level function so the guard tests can weaken one at a
# time (control first, then weakened) and show the corresponding case is missed.

def gate_package(ctx: Dict[str, Any], rep: Report) -> None:
    pkg = ctx["package"]
    if pkg.get("source_commit") != PINNED_COMMIT or pkg.get("target_blob") != PINNED_BLOB:
        rep.add("package.pins", FAIL, "package does not pin the fixed commit/blob")
    for key, file_key in (("task_sha256", "task_file"), ("review_baseline_sha256", "review_baseline_file")):
        path = os.path.join(ctx["package_dir"], pkg[file_key])
        try:
            data = read_bytes(path)
        except OSError as exc:
            rep.add(f"package.{file_key}", UNKNOWN, f"unreadable: {exc}")
            continue
        if sha256(data) == pkg[key] or "package_digest" in WEAKEN:
            rep.add(f"package.{file_key}", PASS, "bytes match package digest")
        else:
            hint = " (CRLF conversion of an LF file)" if sha256(data.replace(b"\r\n", b"\n")) == pkg[key] else ""
            rep.add(f"package.{file_key}", FAIL, "bytes differ from package digest" + hint)
        if file_key == "task_file":
            ctx["task_text"] = data.decode("utf-8", "replace")


def scan_dir(root: str) -> Dict[str, Any]:
    """Every entry under root, not following links (same shape as the adapter's manifest)."""
    entries: Dict[str, Any] = {}

    def onerror(exc: OSError) -> None:
        raise exc

    for dirpath, dirnames, filenames in os.walk(root, onerror=onerror, followlinks=False):
        dirnames.sort()
        rel_dir = os.path.relpath(dirpath, root)
        for name in list(dirnames) + list(filenames):
            full = os.path.join(dirpath, name)
            rel = os.path.normpath(os.path.join(rel_dir, name)).replace(os.sep, "/")
            mode = os.lstat(full).st_mode
            if _stat.S_ISLNK(mode):
                entries[rel] = {"kind": "link"}
            elif _stat.S_ISDIR(mode):
                entries[rel] = {"kind": "dir"}
            elif _stat.S_ISREG(mode):
                data = read_bytes(full)
                entries[rel] = {"kind": "file", "size": len(data), "sha256": sha256(data),
                                "blob": git_blob_id(data)}
            else:
                entries[rel] = {"kind": "special"}
    return entries


def expected_entries(files: Dict[str, Any]) -> Dict[str, Any]:
    out: Dict[str, Any] = {}
    for path, meta in files.items():
        parts = path.split("/")
        for i in range(1, len(parts)):
            out["/".join(parts[:i])] = {"kind": "dir"}
        out[path] = {"kind": "file", "size": meta["size"], "sha256": meta["sha256"]}
    return out


def gate_export(ctx: Dict[str, Any], rep: Report) -> None:
    man, pkg = ctx["manifest"], ctx["package"]
    if (man.get("source_commit") != pkg["source_commit"] or man.get("target_blob") != pkg["target_blob"]
            or man.get("source_prefix") != pkg["source_prefix"]):
        rep.add("export.manifest_pins", FAIL, "manifest is not for the package's commit/prefix/blob")
        return
    files = man.get("files")
    if not isinstance(files, dict) or not files:
        rep.add("export.manifest_files", FAIL, "manifest has no file table")
        return
    if any(not p.startswith(pkg["source_prefix"]) for p in files):
        rep.add("export.manifest_files", FAIL, "manifest lists a path outside the source prefix")
        return
    try:
        actual = scan_dir(ctx["export_dir"])
    except OSError as exc:
        rep.add("export.scan", UNKNOWN, f"export directory could not be fully read: {exc}")
        return
    want = expected_entries(files)
    problems = []
    for path in sorted(set(actual) | set(want)):
        a, w = actual.get(path), want.get(path)
        if a is None:
            problems.append(f"missing {path}")
        elif w is None:
            problems.append(f"extra {path}")
        elif a["kind"] != w["kind"]:
            problems.append(f"{path}: kind {a['kind']} != {w['kind']}")
        elif a["kind"] == "file" and (a["size"] != w["size"] or a["sha256"] != w["sha256"]
                                      or a["blob"] != files[path]["blob"]):
            problems.append(f"{path}: bytes differ from manifest")
    if problems and "export_tree" not in WEAKEN:
        rep.add("export.tree", FAIL, "; ".join(problems[:8]), count=len(problems))
        return
    if actual[pkg["target_file"]]["blob"] != PINNED_BLOB:
        rep.add("export.target_blob", FAIL, "target file is not the pinned blob")
        return
    rep.add("export.tree", PASS, f"{len(files)} files equal the manifest; target blob pinned")
    ctx["export_ok"] = True


def gate_registry(ctx: Dict[str, Any], rep: Report) -> None:
    path = os.path.join(ctx["evidence_root"], "registry.jsonl")
    try:
        data = read_bytes(path)
    except OSError as exc:
        rep.add("registry.read", UNKNOWN, f"registry unreadable: {exc}")
        return
    try:
        lines = data.decode("utf-8").split("\n")
        if lines[-1] != "":
            raise ValueError("last record is not newline-terminated")
        records = [json.loads(line) for line in lines[:-1]]
    except (ValueError, UnicodeDecodeError) as exc:
        rep.add("registry.parse", FAIL, f"registry is not adapter JSONL: {exc}")
        return
    problems = validate_registry_records(records, None)
    if problems:
        rep.add("registry.legal", FAIL, "; ".join(problems[:6]))
        return
    rep.add("registry.legal", PASS, f"{len(records)} records, legal adapter history",
            registry_sha256=sha256(data))
    ctx["records"] = records


def gate_binding(ctx: Dict[str, Any], rep: Report) -> None:
    records, run_id, pkg = ctx.get("records"), ctx["run_id"], ctx["package"]
    if records is None:
        rep.add("binding.run", UNKNOWN, "no legal registry to bind against")
        return
    mine = [r for r in records if r.get("run_id") == run_id]
    events = [r["event"] for r in mine]
    if not mine:
        rep.add("binding.run", FAIL, f"run {run_id!r} not in this registry")
        return
    if events != ["reserved", "dispatched", "collected"]:
        status = FAIL if "dispatch_failed" in events else UNKNOWN
        rep.add("binding.run", status, f"run events {events}: not one dispatched-and-collected run "
                                       "(an uncollected run stays unknown; nothing is re-sent)")
        return
    try:
        snap = json.loads(mine[0]["request_snapshot"])
    except ValueError:
        rep.add("binding.request", FAIL, "request snapshot is not JSON")
        return
    problems = []
    if snap.get("instructions") != ctx.get("task_text"):
        problems.append("instructions != package task text")
    if snap.get("task_id") != pkg["task_id"]:
        problems.append(f"task_id {snap.get('task_id')!r} != {pkg['task_id']!r}")
    if snap.get("session_id") not in ("new", "", None) and not str(snap.get("session_id")).startswith("new:"):
        problems.append("not a new session")
    if not snap.get("model_id"):
        problems.append("no explicit model requested")
    if snap.get("allowed_scope") or snap.get("budget_tokens") is not None:
        problems.append("scope/budget not as the adapter supports")
    if mine[0].get("cli_version") != pkg["pinned_cli_version"]:
        problems.append(f"cli_version {mine[0].get('cli_version')!r}")
    if problems and "binding_request" not in WEAKEN:
        rep.add("binding.request", FAIL, "; ".join(problems))
        return
    rep.add("binding.request", PASS, "run is reserved->dispatched->collected for this task text",
            run_id=run_id, native_session_id=mine[0]["native_session_id"],
            requested_model=mine[0]["requested_model"], recorded_workspace=mine[0]["workspace"])
    ctx["run"] = {"reserved": mine[0], "dispatched": mine[1], "collected": mine[2]}


def run_dir(ctx: Dict[str, Any]) -> str:
    return os.path.join(ctx["evidence_root"], "runs", ctx["run_id"])


def gate_workspace(ctx: Dict[str, Any], rep: Report) -> None:
    run = ctx.get("run")
    if run is None or "manifest" not in ctx:
        rep.add("workspace.manifest", UNKNOWN, "no bound run")
        return
    want = expected_entries(ctx["manifest"]["files"])
    for name, digest in (("workspace-before.json", run["reserved"].get("manifest_before_sha256")),
                         ("workspace-after.json", None)):
        try:
            data = read_bytes(os.path.join(run_dir(ctx), name))
        except OSError as exc:
            rep.add(f"workspace.{name}", UNKNOWN, f"unreadable: {exc}")
            continue
        if digest is not None and sha256(data) != digest:
            rep.add(f"workspace.{name}", FAIL, "bytes differ from the registered digest")
            continue
        try:
            observed = json.loads(data.decode("utf-8"))
        except (ValueError, UnicodeDecodeError):
            rep.add(f"workspace.{name}", FAIL, "not JSON")
            continue
        if observed != want and "workspace_manifest" not in WEAKEN:
            extra = sorted(set(observed) - set(want))[:5] if isinstance(observed, dict) else []
            rep.add(f"workspace.{name}", FAIL, "the run's workspace was not exactly the export",
                    extra_entries=extra)
        else:
            rep.add(f"workspace.{name}", PASS, "run workspace == exported files")


def gate_evidence(ctx: Dict[str, Any], rep: Report) -> None:
    run = ctx.get("run")
    if run is None:
        rep.add("evidence.files", UNKNOWN, "no bound run")
        return
    registered = run["collected"].get("evidence") or {}
    ctx["evidence"] = {}
    for name in EVIDENCE_NAMES:
        meta = registered.get(name)
        if not meta:
            rep.add(f"evidence.{name}", UNKNOWN, "not registered at collect")
            continue
        path = os.path.join(run_dir(ctx), name)
        try:
            data = read_bytes(path)
        except OSError as exc:
            rep.add(f"evidence.{name}", UNKNOWN, f"missing/unreadable on disk: {exc}")
            continue
        if (len(data) != meta.get("bytes") or sha256(data) != meta.get("sha256")) and "evidence_digest" not in WEAKEN:
            rep.add(f"evidence.{name}", FAIL, "bytes differ from the registered size/sha256")
            continue
        rep.add(f"evidence.{name}", PASS, "bytes = registered", bytes=len(data), sha256=meta["sha256"],
                registered_path=meta.get("path"))
        ctx["evidence"][name] = data


def gate_adapter(ctx: Dict[str, Any], rep: Report) -> None:
    run, stdout = ctx.get("run"), ctx.get("evidence", {}).get("stdout.jsonl")
    if run is None or stdout is None:
        rep.add("adapter.gates", UNKNOWN, "no bound run or no verified stdout")
        return
    col = run["collected"]
    art = (col.get("artifacts") or [{}])[0]
    problems = []
    if col.get("declared_status") != "success" or col.get("state") != "declared_complete":
        problems.append(f"declared {col.get('declared_status')!r}/{col.get('state')!r}")
    if art.get("exit_code") != 0:
        problems.append(f"exit {art.get('exit_code')!r}")
    if art.get("workspace_unchanged") is not True:
        problems.append(f"workspace_unchanged {art.get('workspace_unchanged')!r}")
    if art.get("host_reported_model") != run["reserved"]["requested_model"]:
        problems.append("host model != requested model")
    if art.get("unconfirmed_tool_requests"):
        problems.append("unconfirmed tool requests")
    events, parse_problems, _pre = parse_stream_with_host_preamble(stdout)
    assoc = associate(events, run["reserved"]["native_session_id"]) if events else None
    if parse_problems or assoc is None or assoc["problems"]:
        problems.append("stdout re-parse: " + "; ".join((parse_problems + (assoc or {"problems": ["no events"]})
                                                         ["problems"])[:4]))
    elif assoc["tool_calls"] != art.get("tool_calls") or assoc["assistant_text"] != art.get("assistant_text"):
        problems.append("re-parsed stdout differs from the recorded artifact")
    if problems and "adapter_gates" not in WEAKEN:
        rep.add("adapter.gates", FAIL, "; ".join(problems))
        return
    rep.add("adapter.gates", PASS, "recorded adapter gates hold and stdout re-parses to the same artifact",
            usage=col.get("usage"), backend_models=art.get("backend_models"),
            denied_tool_requests=art.get("denied_tool_requests"))
    ctx["events"], ctx["assoc"] = events, assoc


def gate_stdin(ctx: Dict[str, Any], rep: Report) -> None:
    events, pkg = ctx.get("events"), ctx["package"]
    if events is None or "task_text" not in ctx:
        rep.add("binding.stdin", UNKNOWN, "no verified stream")
        return
    users = [e for e in events if e["type"] == "message" and e["role"] == "user"]
    if len(users) != 1:
        rep.add("binding.stdin", UNKNOWN, f"{len(users)} user message events; cannot bind the prompt")
        return
    expected = pkg["stdin_prefix"] + ctx["task_text"] + "\n\n" + pkg["p_text"]
    if users[0]["content"] == expected or "stdin" in WEAKEN:
        rep.add("binding.stdin", PASS, "host echoed exactly prefix + package task text + -p text")
    else:
        rep.add("binding.stdin", FAIL, "the prompt the host received is not this package's task text")


# ------------------------------------------------------------ tool content --

def chat_tool_calls(data: bytes) -> Tuple[Optional[Dict[str, Any]], List[str]]:
    """toolCalls of the host chat record by id (last write wins, as the host rewrites records)."""
    calls: Dict[str, Any] = {}
    notes = []
    for n, line in enumerate(data.decode("utf-8").splitlines(), 1):
        if not line.strip():
            continue
        try:
            rec = json.loads(line)
        except ValueError:
            return None, [f"chat record line {n} is not JSON"]
        if not isinstance(rec, dict):
            return None, [f"chat record line {n} is not an object"]
        for call in rec.get("toolCalls") or []:
            if not isinstance(call, dict) or not isinstance(call.get("id"), str):
                notes.append(f"line {n}: toolCall without id")
                continue
            calls[call["id"]] = call
    return calls, notes


def chat_output(call: Dict[str, Any]) -> Optional[str]:
    for part in call.get("result") or []:
        fr = part.get("functionResponse") if isinstance(part, dict) else None
        if isinstance(fr, dict) and fr.get("id") == call.get("id"):
            resp = fr.get("response")
            if isinstance(resp, dict) and isinstance(resp.get("output"), str):
                return resp["output"]
    return None


def workspace_flavor(workspace: str):
    return ntpath if re.match(r"^[A-Za-z]:[\\/]", workspace or "") or "\\" in (workspace or "") else posixpath


def map_to_export(path: str, workspace: str) -> Optional[str]:
    """Relative export path for an absolute path under the recorded workspace, or a
    relative path; None when it is outside (never resolved against the real disk)."""
    flavor = workspace_flavor(workspace)
    norm = path.replace("\\", "/") if flavor is ntpath else path
    if flavor.isabs(path):
        ws = workspace.replace("\\", "/").rstrip("/") if flavor is ntpath else workspace.rstrip("/")
        cand = norm
        if flavor is ntpath:
            if not cand.lower().startswith(ws.lower() + "/"):
                return None
        elif not cand.startswith(ws + "/"):
            return None
        rel = cand[len(ws) + 1:]
    else:
        rel = norm.replace("\\", "/")
    parts = [p for p in rel.split("/") if p not in ("", ".")]
    if not parts or ".." in parts:
        return None
    return "/".join(parts)


def export_lines(ctx: Dict[str, Any], rel: str) -> Optional[List[str]]:
    if rel not in ctx["manifest"]["files"]:
        return None
    cache = ctx.setdefault("_lines", {})
    if rel not in cache:
        cache[rel] = read_bytes(os.path.join(ctx["export_dir"], *rel.split("/"))).decode("utf-8").split("\n")
    return cache[rel]


def check_grep(ctx: Dict[str, Any], output: str) -> Tuple[str, str, int]:
    lines = output.split("\n")
    head = GREP_HEAD.match(lines[0]) if lines else None
    if not head:
        if output.startswith("No matches found"):
            return PASS, "no matches reported", 0
        return UNKNOWN, "unrecognised grep output header", 0
    search_path = head.group(4)
    if search_path not in (".", ""):
        base = map_to_export(search_path, ctx["run"]["reserved"]["workspace"])
        if base is None:
            return UNKNOWN, f"search path {search_path!r} cannot be mapped into the export", 0
    current, matches, target_hits, checked = None, 0, 0, 0
    for line in lines[1:]:
        if line == "---" or line == "":
            continue
        if line.startswith("File: "):
            current = map_to_export(line[6:], ctx["run"]["reserved"]["workspace"])
            if current is None or export_lines(ctx, current) is None:
                return FAIL, f"grep names a file not in the export: {line[6:]!r}", target_hits
            continue
        m = GREP_LINE.match(line)
        if not m or current is None:
            return UNKNOWN, f"unrecognised grep line {line[:60]!r}", target_hits
        n, sep, text = int(m.group(1)), m.group(2), m.group(3)
        src = export_lines(ctx, current)
        if (not 1 <= n <= len(src) or src[n - 1].rstrip("\r") != text.rstrip("\r")) and "tool_content" not in WEAKEN:
            return FAIL, f"grep line {current}:{n} does not equal the exported source", target_hits
        checked += 1
        if sep == ":":
            matches += 1
            if current == ctx["package"]["target_file"]:
                target_hits += 1
    if matches != int(head.group(1)):
        return UNKNOWN, f"header says {head.group(1)} matches, saw {matches} match lines", target_hits
    return PASS, f"{checked} lines equal the exported source", target_hits


def check_read(ctx: Dict[str, Any], params: Dict[str, Any], output: str,
               stream_output: Any) -> Tuple[str, str, bool]:
    rel = map_to_export(str(params.get("file_path", "")), ctx["run"]["reserved"]["workspace"])
    if rel is None:
        return FAIL, "read_file path is outside the recorded workspace (not read)", False
    src = export_lines(ctx, rel)
    if src is None:
        return FAIL, f"read_file path {rel!r} is not an exported file", False
    m = READ_TRUNC.match(output)
    if not m:
        return UNKNOWN, "unrecognised read_file output format (e.g. untruncated whole file)", False
    a, b, total, content = int(m.group(1)), int(m.group(2)), int(m.group(3)), m.group(4)
    if total != len(src) or not 1 <= a <= b <= total:
        return FAIL, f"line range {a}-{b} of {total} does not fit {rel} ({len(src)} lines)", False
    if content != "\n".join(src[a - 1:b]) and "tool_content" not in WEAKEN:
        return FAIL, f"read content {rel}:{a}-{b} does not equal the exported source", False
    s = READ_SUMMARY.match(stream_output) if isinstance(stream_output, str) else None
    if not s:
        return UNKNOWN, "stream summary for this read is absent or unrecognised", False
    if (int(s.group(1)), int(s.group(2)), int(s.group(3))) != (a, b, total) or \
            map_to_export(s.group(4), ctx["run"]["reserved"]["workspace"]) != rel:
        return FAIL, "stream summary disagrees with the chat record content", False
    return PASS, f"{rel}:{a}-{b} equals the exported source", rel == ctx["package"]["target_file"]


def gate_tools(ctx: Dict[str, Any], rep: Report) -> None:
    assoc, chat = ctx.get("assoc"), ctx.get("evidence", {}).get("host-chat-record.jsonl")
    if assoc is None or chat is None or not ctx.get("export_ok"):
        rep.add("tools.content", UNKNOWN, "needs a verified stream, chat record and export")
        return
    calls, notes = chat_tool_calls(chat)
    if calls is None:
        rep.add("tools.content", UNKNOWN, "; ".join(notes))
        return
    grep_hits, reads = 0, 0
    for call in assoc["tool_calls"]:
        name, tid, status = call["tool_name"], call["tool_id"], (call["result"] or {}).get("status")
        if status != "success":
            rep.add(f"tools.{tid}", PASS if name not in ("grep_search", "read_file") or status == "error" else UNKNOWN,
                    f"{name} status {status!r}: not a source of evidence", tool=name)
            continue
        if name not in ("grep_search", "read_file"):
            rep.add(f"tools.{tid}", PASS, f"{name}: recorded, content not compared", tool=name)
            continue
        chat_call = calls.get(tid)
        if chat_call is None:
            ctx["tool_unknown"] = True
            rep.add(f"tools.{tid}", UNKNOWN, "no matching call in the host chat record", tool=name)
            continue
        args = chat_call.get("args") if isinstance(chat_call.get("args"), dict) else None
        if chat_call.get("name") != name or args is None or chat_call.get("status") != "success" or \
                any(args.get(k) != v for k, v in call["parameters"].items()):
            rep.add(f"tools.{tid}", FAIL, "stream request/result and chat record disagree", tool=name)
            continue
        output = chat_output(chat_call)
        if output is None:
            ctx["tool_unknown"] = True
            rep.add(f"tools.{tid}", UNKNOWN, "chat record carries no tool output", tool=name)
            continue
        if name == "grep_search":
            status_, detail, hits = check_grep(ctx, output)
            if status_ == PASS:
                grep_hits += hits
        else:
            status_, detail, on_target = check_read(ctx, call["parameters"], output,
                                                    (call["result"] or {}).get("output"))
            if status_ == PASS and on_target:
                reads += 1
        if status_ == UNKNOWN:
            ctx["tool_unknown"] = True
        rep.add(f"tools.{tid}", status_, detail, tool=name, parameters=call["parameters"],
                output_sha256=sha256(output.encode("utf-8")))
    ctx["grep_hits"], ctx["target_reads"] = grep_hits, reads


def gate_required(ctx: Dict[str, Any], rep: Report) -> None:
    if "grep_hits" not in ctx:
        rep.add("required.tools", UNKNOWN, "tool content not checked")
        return
    if (ctx["grep_hits"] >= 1 and ctx["target_reads"] >= 1) or "required_tools" in WEAKEN:
        rep.add("required.tools", PASS, f"{ctx['grep_hits']} verified grep hit(s) and "
                                        f"{ctx['target_reads']} verified read(s) of the target")
    elif ctx.get("tool_unknown"):
        rep.add("required.tools", UNKNOWN, "required grep/read not verified because some tool output "
                                           "could not be checked")
    else:
        rep.add("required.tools", FAIL, "needs >=1 verified grep_search hit and >=1 verified read_file "
                                        "of the target file")


def function_ranges(source: str) -> Dict[str, Tuple[int, int]]:
    out: Dict[str, Tuple[int, int]] = {}
    tree = ast.parse(source)
    for node in tree.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            out[node.name] = (node.lineno, node.end_lineno)
        elif isinstance(node, ast.ClassDef):
            for sub in node.body:
                if isinstance(sub, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    out[f"{node.name}.{sub.name}"] = (sub.lineno, sub.end_lineno)
    return out


def gate_refs(ctx: Dict[str, Any], rep: Report) -> None:
    assoc, pkg = ctx.get("assoc"), ctx["package"]
    if assoc is None or not ctx.get("export_ok"):
        rep.add("refs.exist", UNKNOWN, "needs a verified stream and export")
        return
    text = assoc["assistant_text"]
    target = pkg["target_file"]
    src_lines = export_lines(ctx, target)
    ranges = function_ranges("\n".join(src_lines))
    for fname, (a, b) in pkg["function_ranges"].items():
        if tuple(ranges.get(fname, ())) != (a, b):
            rep.add("refs.ranges", FAIL, f"package range for {fname} does not match the source AST")
            return
    refs, bad = [], []
    for m in SOURCE_REF.finditer(text):
        rel = map_to_export(m.group(1), ctx["run"]["reserved"]["workspace"])
        start = int(m.group(2))
        end = int(m.group(3)) if m.group(3) else start
        lines = export_lines(ctx, rel) if rel else None
        if lines is None or not 1 <= start <= end <= len(lines):
            bad.append(m.group(0))
        else:
            refs.append((rel, start, end))
    if not refs and not bad:
        rep.add("refs.exist", FAIL, "delivery carries no path:line reference")
        return
    if bad and "refs_exist" not in WEAKEN:
        rep.add("refs.exist", FAIL, f"references that do not exist in the export: {bad[:6]}")
    else:
        rep.add("refs.exist", PASS, f"{len(refs)} references exist in the export")
    for point in CORE_POINTS:
        spec = pkg["core_points"][point]
        if "focus" in spec:
            f = spec["focus"]
            if sha256("\n".join(src_lines[f["start"] - 1:f["end"]]).encode("utf-8")) != f["sha256"]:
                rep.add(f"refs.{point}", FAIL, "package focus lines do not match the source")
                continue
            spans = [(f["start"], f["end"])]
        else:
            spans = [tuple(pkg["function_ranges"][fn]) for fn in spec["functions"]]
        hit = [r for r in refs if r[0] == target and any(r[1] <= e and r[2] >= s for s, e in spans)]
        if hit or "refs_core" in WEAKEN:
            rep.add(f"refs.{point}", PASS, "a reference falls in the point's range (location only, "
                                           "not meaning)", refs=[f"{r[0]}:{r[1]}-{r[2]}" for r in hit[:4]])
        else:
            rep.add(f"refs.{point}", FAIL, "no reference falls in this core point's range")


GATES = [gate_package, gate_export, gate_registry, gate_binding, gate_workspace, gate_evidence,
         gate_adapter, gate_stdin, gate_tools, gate_required, gate_refs]


def run_checks(package_path: str, manifest_path: str, export_dir: str, evidence_root: str,
               run_id: str) -> Dict[str, Any]:
    ctx: Dict[str, Any] = {"package_dir": os.path.dirname(os.path.abspath(package_path)),
                           "export_dir": os.path.abspath(export_dir),
                           "evidence_root": os.path.abspath(evidence_root), "run_id": run_id}
    rep = Report()
    with open(package_path, "rb") as fh:
        ctx["package"] = json.loads(fh.read().decode("utf-8"))
    try:
        manifest_bytes = read_bytes(manifest_path)
        ctx["manifest"] = json.loads(manifest_bytes.decode("utf-8"))
    except (OSError, ValueError, UnicodeDecodeError) as exc:
        rep.add("export.manifest", UNKNOWN, f"manifest unreadable: {exc}")
        manifest_bytes = b""
    for gate in GATES:
        if gate is gate_export and "manifest" not in ctx:
            continue
        gate(ctx, rep)
    assoc = ctx.get("assoc") or {}
    delivery = assoc.get("assistant_text")
    return {
        "schema": SCHEMA,
        "kind": "mechanical consistency check only",
        "inputs": {"package_sha256": sha256(read_bytes(package_path)),
                   "manifest_sha256": sha256(manifest_bytes) if manifest_bytes else None,
                   "run_id": run_id},
        "mechanical": {"overall": rep.overall(), "items": rep.items},
        "semantic_review": {
            "status": "required",
            "by": "architect (independent review)",
            "baseline": ctx["package"].get("review_baseline_file"),
            "points": [{"point": p, "location_check": next((i["status"] for i in rep.items
                                                            if i["check"] == f"refs.{p}"), UNKNOWN),
                        "meaning": "not judged by this tool"} for p in CORE_POINTS],
            "delivery_text": delivery,
            "delivery_text_sha256": sha256(delivery.encode("utf-8")) if isinstance(delivery, str) else None,
        },
        "task_outcome": "not decided by this tool",
        "on_unknown_or_fail": "freeze this evidence root; no re-dispatch, retry, new key/attempt or new root",
    }


EXIT = {"all_pass": 0, "fail": 1, "undetermined": 3}


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--package", default=os.path.join(HERE, "package.json"))
    ap.add_argument("--manifest", required=True)
    ap.add_argument("--export-dir", required=True)
    ap.add_argument("--evidence-root", required=True)
    ap.add_argument("--run-id", required=True)
    ap.add_argument("--out", help="report path (new file, outside the export and evidence root)")
    args = ap.parse_args(argv)
    if args.out:
        out = os.path.realpath(args.out)
        for root in (args.export_dir, args.evidence_root):
            r = os.path.realpath(root)
            if out == r or out.startswith(r.rstrip(os.sep) + os.sep):
                print("--out must be outside the export and the evidence root", file=sys.stderr)
                return 2
    try:
        result = run_checks(args.package, args.manifest, args.export_dir, args.evidence_root, args.run_id)
    except (OSError, ValueError, KeyError) as exc:
        print(f"could not run: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 2
    data = (json.dumps(result, ensure_ascii=False, indent=1, sort_keys=True) + "\n").encode("utf-8")
    if args.out:
        with open(args.out, "xb") as fh:
            fh.write(data)
    else:
        sys.stdout.buffer.write(data)
    overall = result["mechanical"]["overall"]
    print(f"MECHANICAL {overall.upper()} - semantic review required; this is not a task verdict",
          file=sys.stderr)
    return EXIT[overall]


if __name__ == "__main__":
    sys.exit(main())
