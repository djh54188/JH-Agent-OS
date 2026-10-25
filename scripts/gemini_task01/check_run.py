"""Hark-派单-21/22 task01: MECHANICAL consistency check of one adapter run's raw evidence.

Inputs (all read-only): the fixed task package, the export manifest and exported
directory, and one evidence root written by GeminiCliAgentSessionAdapter.

Trusted anchors (Hark-派单-22, R1): the package bytes, the task text / review
baseline digests, the task id and the complete src/ file set of the fixed commit
are pinned in pins.py (computed from Git objects), never taken from the package or
manifest that an altered input could rewrite together with the files.

What it checks (each item: pass / fail / unknown):
  package     package bytes = pinned digest; task text / baseline bytes = pinned
              digests; their names are plain file names in the package directory
  export      manifest = the pinned src/ file set of the fixed commit; the export
              root is a plain directory (no link / junction); it holds exactly
              those files and bytes
  registry    the registry is a legal adapter history (adapter's own validator)
  binding     the run's records: reserved -> dispatched -> collected, request
              snapshot = this package's task text / task id / new session /
              explicit, non-automatic model; argv tail = session / model / -p;
              the actual stdin echoed by the host = prefix + text
  workspace   the registered before/after manifests describe exactly the export
  evidence    stdout / stderr / host chat record: plain files inside the evidence
              root, bytes = registered size + sha256
  adapter     the adapter's own gates as recorded, and a re-parse of stdout agrees
  native      (R2) recomputed from the native bytes, not from the collected
              summary: host result status, declared status / native state, init
              model vs request vs artifact, usage (missing = unknown), the known
              host stdout preamble with the stderr final save failure, host
              errors / denied / unconfirmed lists
  chat        (R2) the host chat record is one session of the supported shape
              (header, message records, `$set` updates; a rewrite keeps id, type,
              model and earlier tool calls); every tool call is well formed, its
              functionResponse id/name agree, and the stream and the record name
              the same tool calls
  tools       (R3) every successful grep_search / read_file is paired with the
              chat record; request, known default, record and response agree on
              path / range / pattern / context; content equals the exported bytes
              in the host output format observed for CLI 0.65.0; unknown formats
              or unexplained defaults are unknown, never matched
  required    at least one verified grep hit and one verified read of the target
  refs        every path:line in the delivery exists in the export; for each core
              point B2/B3/B4/B6/B7 at least one reference falls in its range

Boundaries (R4): every file is reached by walking from its root (the package
directory, the manifest's directory, the export root, the evidence root) one
component at a time with lstat; a link, junction / reparse point, non-plain
component, '..', absolute or drive / root-relative name is refused BEFORE anything
is opened. Components ABOVE those roots are not checked: this is not a sandbox and
proves nothing about the roots' ancestors.
Input JSON of an unsupported shape gives a stable non-pass diagnostic.

What it does NOT do: judge whether the explanation is right. `semantic_review`
is always "required", and no field of the report says the task succeeded or was
accepted. It never re-dispatches, retries, changes keys, attempts or evidence
roots, starts a process, or touches the network.

Read-only shared component (disclosed): the adapter's own parsers
validate_registry_records / parse_stream_with_host_preamble / associate /
usage_from_result and the HOST_SAVE_FINAL_FAILURE marker are imported from
src.agent_session_gemini. The cross-evidence comparisons here are this file's own;
this is not a fully independent implementation.

Exit codes: 0 every mechanical item passed (SEMANTIC REVIEW STILL REQUIRED);
1 at least one mechanical item failed; 3 none failed but at least one is
unknown; 2 could not run.
"""
from __future__ import annotations

import argparse
import ast
import hashlib
import json
import os
import re
import stat as _stat
import sys
from typing import Any, Dict, List, Optional, Tuple

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(os.path.dirname(HERE))
sys.path.insert(0, ROOT)
sys.path.insert(0, HERE)

import pins  # noqa: E402  (trusted anchors)
from src.agent_session_gemini import (  # noqa: E402  (read-only use of the adapter's parsers)
    HOST_SAVE_FINAL_FAILURE, associate, parse_stream_with_host_preamble, usage_from_result,
    validate_registry_records)

SCHEMA = "hark21/gemini-task01/mechanical-check/v2"
PINNED_COMMIT = pins.SOURCE_COMMIT
PINNED_BLOB = pins.TARGET_BLOB
EVIDENCE_NAMES = ("stdout.jsonl", "stderr.txt", "host-chat-record.jsonl")
CORE_POINTS = ("B2", "B3", "B4", "B6", "B7")
RUN_ID = re.compile(r"^gemini-run-[0-9a-f]{12}$")

PASS, FAIL, UNKNOWN = "pass", "fail", "unknown"

# Guard-weakening hook for tests ONLY (tests/test_hark21_gemini_task01.py adds names
# to it after a control run). Production commands never set it; main() refuses to
# run when it is not empty.
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
# A path:line reference; a leading drive, separator or UNC prefix is captured too so
# that such a reference is judged (and refused) instead of silently skipped.
SOURCE_REF = re.compile(r"(?<![\w/\\.:-])((?:[A-Za-z]:)?[\\/]{0,2}(?:[\w.-]+[/\\])*[\w.-]+\.[A-Za-z0-9]{1,8})"
                        r":(\d+)(?:-(\d+))?")
# Hark-派单-23 R4: reference tokens. Any non-space token (CJK / ASCII punctuation and
# brackets end a token) followed by :<line>[-<line>] whose text contains '.', '/' or
# '\\' is a path reference and is judged by map_to_export; device (\\?\, \\.\),
# UNC, drive-relative, root-relative and ':'-bearing (ADS) tokens are therefore
# reported as references that do not exist, never dropped at extraction.
REF_TOKEN = re.compile(r"([^\s，。；：、（）【】「」《》“”‘’\"'`()\[\]{}<>,;|]+):(\d+)(?:-(\d+))?(?!\d)")
# Observed host default: a grep_search request without `context` is recorded by the
# host with context 50 (host-probe: stream parameters {pattern}, chat args
# {pattern, context: 50}, 50 lines either side of the single match).
GREP_DEFAULTS = {"context": 50}
GREP_KEYS = ("pattern", "context")
READ_KEYS = ("file_path", "start_line", "end_line")
CHAT_HEADER_KEYS = ("sessionId", "projectHash", "startTime", "lastUpdated", "kind")
CHAT_SET_KEYS = ("lastUpdated",)
CHAT_TYPES = ("user", "gemini")
AUTO_MODEL = re.compile(r"^\s*auto(\b|[-_])", re.I)
FILE_ATTRIBUTE_REPARSE_POINT = 0x400


def sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def git_blob_id(data: bytes) -> str:
    return hashlib.sha1(b"blob %d\0" % len(data) + data).hexdigest()


# ------------------------------------------------------------ boundaries -----

class Refused(Exception):
    """A path was refused before it was opened (link, junction, escape, wrong kind)."""


def link_like(st: os.stat_result) -> bool:
    """Symlink, or any Windows reparse point (junction, mount point, symlink)."""
    if _stat.S_ISLNK(st.st_mode):
        return True
    return bool(getattr(st, "st_file_attributes", 0) & FILE_ATTRIBUTE_REPARSE_POINT)


def name_problem(part: Any) -> Optional[str]:
    """Why `part` is not one plain path component (checked on the text, before use)."""
    if not isinstance(part, str) or part in ("", ".", ".."):
        return f"component {part!r} is not a plain name"
    if any(c in part for c in ("/", "\\", ":", "\x00")):
        return f"component {part!r} contains a separator, drive or NUL"
    if part != part.rstrip(". "):
        return f"component {part!r} ends in a dot or space"
    return None


def safe_path(root: str, parts: List[str], want: str, check_root: bool = True) -> Tuple[str, os.stat_result]:
    """Walk root/parts with lstat, refusing links / junctions / non-plain components
    BEFORE anything is opened. FileNotFoundError propagates (absent, not refused)."""
    for part in parts:
        problem = name_problem(part)
        if problem:
            raise Refused(problem)
    st = os.lstat(root)
    if check_root and link_like(st):
        raise Refused(f"root {root!r} is a link or junction")
    if not _stat.S_ISDIR(os.stat(root).st_mode if not check_root else st.st_mode):
        raise Refused(f"root {root!r} is not a directory")
    cur = root
    for i, part in enumerate(parts):
        cur = os.path.join(cur, part)
        st = os.lstat(cur)
        if link_like(st):
            raise Refused(f"{cur!r} is a link or junction")
        last = i == len(parts) - 1
        if (not last or want == "dir") and not _stat.S_ISDIR(st.st_mode):
            raise Refused(f"{cur!r} is not a plain directory")
        if last and want == "file" and not _stat.S_ISREG(st.st_mode):
            raise Refused(f"{cur!r} is not a regular file")
    return cur, st


def safe_read(root: str, parts: List[str], check_root: bool = True) -> bytes:
    path, st = safe_path(root, parts, "file", check_root)
    flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_BINARY", 0)
    fd = os.open(path, flags)
    try:
        fst = os.fstat(fd)
        if not _stat.S_ISREG(fst.st_mode) or (fst.st_dev, fst.st_ino) != (st.st_dev, st.st_ino):
            raise Refused(f"{path!r} changed between check and open")
        chunks = []
        while True:
            chunk = os.read(fd, 1 << 20)
            if not chunk:
                break
            chunks.append(chunk)
        return b"".join(chunks)
    finally:
        os.close(fd)


def plain_read(path: str) -> bytes:
    """Weakened-guard reader (tests only): follows links, no component checks."""
    with open(path, "rb") as fh:
        return fh.read()


def split_file_arg(path: str) -> Tuple[str, str]:
    full = os.path.abspath(path)
    return os.path.dirname(full), os.path.basename(full)


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


class Stop(Exception):
    """Later gates must not run (their inputs are not trusted / not usable)."""


# ---------------------------------------------------------------- gates -----
# Each gate is a module-level function so the guard tests can weaken one at a
# time (control first, then weakened) and show the corresponding case is missed.

_PKG_SHAPE = {"source_commit": str, "source_prefix": str, "target_file": str, "target_blob": str,
              "task_file": str, "task_sha256": str, "task_id": str, "review_baseline_file": str,
              "review_baseline_sha256": str, "stdin_prefix": str, "p_text": str,
              "pinned_cli_version": str, "function_ranges": dict, "core_points": dict}


def gate_package_anchor(ctx: Dict[str, Any], rep: Report) -> None:
    data = ctx["package_bytes"]
    if sha256(data) != pins.PACKAGE_SHA256 and "package_anchor" not in WEAKEN:
        rep.add("package.anchor", FAIL, "package bytes are not the pinned approved package "
                                        "(its self-described digests are not trusted)", sha256=sha256(data))
        raise Stop()
    try:
        pkg = json.loads(data.decode("utf-8"))
    except (ValueError, UnicodeDecodeError, RecursionError) as exc:
        rep.add("package.shape", FAIL, f"package is not JSON: {exc}")
        raise Stop()
    if not isinstance(pkg, dict):
        rep.add("package.shape", FAIL, f"package is {type(pkg).__name__}, not a JSON object")
        raise Stop()
    bad = [k for k, t in _PKG_SHAPE.items() if not isinstance(pkg.get(k), t)]
    if bad:
        rep.add("package.shape", FAIL, f"package fields missing or of the wrong type: {sorted(bad)}")
        raise Stop()
    ctx["package"] = pkg
    if "package_anchor" not in WEAKEN:
        mism = [k for k, v in (("task_id", pins.TASK_ID), ("task_sha256", pins.TASK_SHA256),
                               ("review_baseline_sha256", pins.REVIEW_BASELINE_SHA256))
                if pkg[k] != v]
        if mism:
            rep.add("package.anchor", FAIL, f"package fields differ from the pinned anchors: {mism}")
            raise Stop()
    rep.add("package.anchor", PASS, "package bytes = pinned approved package", sha256=sha256(data))
    if (pkg["source_commit"] != PINNED_COMMIT or pkg["target_blob"] != PINNED_BLOB
            or pkg["target_file"] != pins.TARGET_FILE or pkg["source_prefix"] != pins.SOURCE_PREFIX):
        rep.add("package.pins", FAIL, "package does not pin the fixed commit/prefix/target/blob")
        raise Stop()


def gate_package(ctx: Dict[str, Any], rep: Report) -> None:
    pkg = ctx["package"]
    for key, file_key in (("task_sha256", "task_file"), ("review_baseline_sha256", "review_baseline_file")):
        name = pkg[file_key]
        try:
            if "input_paths" in WEAKEN:
                data = plain_read(os.path.join(ctx["package_dir"], name))
            else:
                data = safe_read(ctx["package_dir"], [name])
        except Refused as exc:
            rep.add(f"package.{file_key}", FAIL, f"refused before reading: {exc}")
            continue
        except OSError as exc:
            rep.add(f"package.{file_key}", UNKNOWN, f"unreadable: {exc}")
            continue
        # pkg[key] equals the pinned digest unless the package anchor is weakened (tests).
        if sha256(data) == pkg[key] or "package_digest" in WEAKEN:
            rep.add(f"package.{file_key}", PASS, "bytes match the pinned digest")
        else:
            hint = " (CRLF conversion of an LF file)" if sha256(data.replace(b"\r\n", b"\n")) == pkg[key] else ""
            rep.add(f"package.{file_key}", FAIL, "bytes differ from the pinned digest" + hint)
        if file_key == "task_file":
            ctx["task_text"] = data.decode("utf-8", "replace")


def rel_problem(path: Any) -> Optional[str]:
    if not isinstance(path, str):
        return f"path {path!r} is not a string"
    for part in path.split("/"):
        problem = name_problem(part)
        if problem:
            return f"{path!r}: {problem}"
    return None


def load_manifest(ctx: Dict[str, Any], rep: Report) -> None:
    try:
        d, b = split_file_arg(ctx["manifest_path"])
        data = safe_read(d, [b], check_root=False)
    except Refused as exc:
        rep.add("export.manifest", FAIL, f"manifest refused before reading: {exc}")
        return
    except OSError as exc:
        rep.add("export.manifest", UNKNOWN, f"manifest unreadable: {exc}")
        return
    ctx["manifest_sha256"] = sha256(data)
    try:
        man = json.loads(data.decode("utf-8"))
    except (ValueError, UnicodeDecodeError, RecursionError) as exc:
        rep.add("export.manifest", FAIL, f"manifest is not JSON: {exc}")
        return
    if not isinstance(man, dict) or not isinstance(man.get("files"), dict) or not man["files"]:
        rep.add("export.manifest", FAIL, "manifest is not an object with a non-empty file table")
        return
    for path, meta in man["files"].items():
        problem = rel_problem(path)
        if problem or not isinstance(meta, dict) or not isinstance(meta.get("blob"), str) \
                or not isinstance(meta.get("sha256"), str) or not isinstance(meta.get("size"), int) \
                or isinstance(meta.get("size"), bool):
            rep.add("export.manifest", FAIL, f"manifest entry not usable: {problem or path!r}")
            return
    ctx["manifest"] = man


def gate_export(ctx: Dict[str, Any], rep: Report) -> None:
    man, pkg = ctx["manifest"], ctx["package"]
    if (man.get("source_commit") != pkg["source_commit"] or man.get("target_blob") != pkg["target_blob"]
            or man.get("source_prefix") != pkg["source_prefix"]):
        rep.add("export.manifest_pins", FAIL, "manifest is not for the package's commit/prefix/blob")
        return
    files = man["files"]
    if any(not p.startswith(pkg["source_prefix"]) for p in files):
        rep.add("export.manifest_files", FAIL, "manifest lists a path outside the source prefix")
        return
    pinned = {p: {"blob": b, "size": s, "sha256": h} for p, (b, s, h) in pins.SOURCE_FILES.items()}
    observed = {p: {"blob": m["blob"], "size": m["size"], "sha256": m["sha256"]} for p, m in files.items()}
    if observed != pinned and "src_anchor" not in WEAKEN:
        diff = sorted(set(pinned) ^ set(observed)) + sorted(p for p in set(pinned) & set(observed)
                                                             if pinned[p] != observed[p])
        rep.add("export.src_anchor", FAIL, "manifest is not the pinned src/ file set of the fixed commit",
                differing=diff[:8], count=len(diff))
        return
    rep.add("export.src_anchor", PASS, f"manifest = pinned src/ set ({len(pinned)} files)")
    try:
        actual = scan_dir(ctx["export_dir"], check_root="export_root" not in WEAKEN)
    except Refused as exc:
        rep.add("export.root", FAIL, f"refused before scanning: {exc}")
        return
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
    target = actual.get(pkg["target_file"]) or {}
    if target.get("blob") != PINNED_BLOB:
        rep.add("export.target_blob", FAIL, "target file is not the pinned blob")
        return
    rep.add("export.tree", PASS, f"{len(files)} files equal the manifest; target blob pinned")
    ctx["export_ok"] = True


def scan_dir(root: str, check_root: bool = True) -> Dict[str, Any]:
    """Every entry under root; links / junctions are recorded, never followed or opened."""
    safe_path(root, [], "dir", check_root)
    entries: Dict[str, Any] = {}

    def walk(parts: List[str]) -> None:
        with os.scandir(os.path.join(root, *parts)) as it:
            items = sorted(it, key=lambda e: e.name)
        for entry in items:
            rel_parts = parts + [entry.name]
            rel = "/".join(rel_parts)
            st = entry.stat(follow_symlinks=False)
            if link_like(st) or name_problem(entry.name):
                entries[rel] = {"kind": "link" if link_like(st) else "unsupported-name"}
            elif _stat.S_ISDIR(st.st_mode):
                entries[rel] = {"kind": "dir"}
                walk(rel_parts)
            elif _stat.S_ISREG(st.st_mode):
                data = safe_read(root, rel_parts, check_root)
                entries[rel] = {"kind": "file", "size": len(data), "sha256": sha256(data),
                                "blob": git_blob_id(data)}
            else:
                entries[rel] = {"kind": "special"}
    walk([])
    return entries


def expected_entries(files: Dict[str, Any]) -> Dict[str, Any]:
    out: Dict[str, Any] = {}
    for path, meta in files.items():
        parts = path.split("/")
        for i in range(1, len(parts)):
            out["/".join(parts[:i])] = {"kind": "dir"}
        out[path] = {"kind": "file", "size": meta["size"], "sha256": meta["sha256"]}
    return out


def ev_read(ctx: Dict[str, Any], parts: List[str]) -> bytes:
    if "evidence_paths" in WEAKEN:
        return plain_read(os.path.join(ctx["evidence_root"], *parts))
    return safe_read(ctx["evidence_root"], parts)


def gate_registry(ctx: Dict[str, Any], rep: Report) -> None:
    if not isinstance(ctx["run_id"], str) or not RUN_ID.match(ctx["run_id"]):
        rep.add("registry.run_id", FAIL, f"run id {ctx['run_id']!r} is not an adapter run id; "
                                         "nothing under runs/ is opened")
        return
    try:
        data = ev_read(ctx, ["registry.jsonl"])
    except Refused as exc:
        rep.add("registry.read", FAIL, f"refused before reading: {exc}")
        return
    except OSError as exc:
        rep.add("registry.read", UNKNOWN, f"registry unreadable: {exc}")
        return
    try:
        lines = data.decode("utf-8").split("\n")
        if lines[-1] != "":
            raise ValueError("last record is not newline-terminated")
        records = [json.loads(line) for line in lines[:-1]]
    except (ValueError, UnicodeDecodeError, RecursionError) as exc:
        rep.add("registry.parse", FAIL, f"registry is not adapter JSONL: {exc}")
        return
    problems = validate_registry_records(records, None)
    if problems:
        rep.add("registry.legal", FAIL, "; ".join(problems[:6]))
        return
    rep.add("registry.legal", PASS, f"{len(records)} records, legal adapter history",
            registry_sha256=sha256(data))
    ctx["records"] = records


def model_problem(model: Any) -> Optional[str]:
    if not isinstance(model, str) or not model.strip():
        return "no explicit model requested"
    if AUTO_MODEL.match(model):
        return f"model {model!r} is automatic routing, not an explicit model"
    return None


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
    if snap.get("allowed_scope") or snap.get("budget_tokens") is not None:
        problems.append("scope/budget not as the adapter supports")
    if mine[0].get("cli_version") != pkg["pinned_cli_version"]:
        problems.append(f"cli_version {mine[0].get('cli_version')!r}")
    tail = mine[1].get("argv_tail")
    want_pairs = (("--session-id", mine[0]["native_session_id"]), ("-p", pkg["p_text"]),
                  ("-m", mine[0]["requested_model"]), ("-o", "stream-json"))
    if not isinstance(tail, list) or any(not _has_pair(tail, k, v) for k, v in want_pairs):
        problems.append("dispatched argv tail does not carry this run's session / -p text / model")
    if problems and "binding_request" not in WEAKEN:
        rep.add("binding.request", FAIL, "; ".join(problems))
        return
    mp = model_problem(snap.get("model_id"))
    if mp and "explicit_model" not in WEAKEN:
        rep.add("binding.model", FAIL, mp + " (task01 requires an explicit, non-automatic model)")
        return
    rep.add("binding.request", PASS, "run is reserved->dispatched->collected for this task text",
            run_id=run_id, native_session_id=mine[0]["native_session_id"],
            requested_model=mine[0]["requested_model"], recorded_workspace=mine[0]["workspace"])
    ctx["run"] = {"reserved": mine[0], "dispatched": mine[1], "collected": mine[2]}


def _has_pair(tail: List[Any], key: str, value: Any) -> bool:
    return any(tail[i] == key and tail[i + 1] == value for i in range(len(tail) - 1))


def gate_workspace(ctx: Dict[str, Any], rep: Report) -> None:
    run = ctx.get("run")
    if run is None or "manifest" not in ctx:
        rep.add("workspace.manifest", UNKNOWN, "no bound run")
        return
    want = expected_entries(ctx["manifest"]["files"])
    for name, digest in (("workspace-before.json", run["reserved"].get("manifest_before_sha256")),
                         ("workspace-after.json", None)):
        try:
            data = ev_read(ctx, ["runs", ctx["run_id"], name])
        except Refused as exc:
            rep.add(f"workspace.{name}", FAIL, f"refused before reading: {exc}")
            continue
        except OSError as exc:
            rep.add(f"workspace.{name}", UNKNOWN, f"unreadable: {exc}")
            continue
        if digest is not None and sha256(data) != digest:
            rep.add(f"workspace.{name}", FAIL, "bytes differ from the registered digest")
            continue
        try:
            observed = json.loads(data.decode("utf-8"))
        except (ValueError, UnicodeDecodeError, RecursionError):
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
    registered = run["collected"].get("evidence")
    ctx["evidence"] = {}
    for name in EVIDENCE_NAMES:
        meta = registered.get(name)
        if meta is None:
            rep.add(f"evidence.{name}", UNKNOWN, "not registered at collect")
            continue
        if not isinstance(meta, dict) or not isinstance(meta.get("bytes"), int) \
                or not isinstance(meta.get("sha256"), str):
            rep.add(f"evidence.{name}", FAIL, "registered evidence entry has an unsupported shape")
            continue
        try:
            data = ev_read(ctx, ["runs", ctx["run_id"], name])
        except Refused as exc:
            rep.add(f"evidence.{name}", FAIL, f"refused before reading: {exc}")
            continue
        except OSError as exc:
            rep.add(f"evidence.{name}", UNKNOWN, f"missing/unreadable on disk: {exc}")
            continue
        if (len(data) != meta["bytes"] or sha256(data) != meta["sha256"]) and "evidence_digest" not in WEAKEN:
            rep.add(f"evidence.{name}", FAIL, "bytes differ from the registered size/sha256")
            continue
        rep.add(f"evidence.{name}", PASS, "bytes = registered", bytes=len(data), sha256=meta["sha256"],
                registered_path=meta.get("path"))
        ctx["evidence"][name] = data


def _artifact(run: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    arts = run["collected"].get("artifacts")
    if isinstance(arts, list) and len(arts) == 1 and isinstance(arts[0], dict):
        return arts[0]
    return None


def native_view(ctx: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """Parse the verified stdout once (the adapter's own parser, read-only)."""
    if "native" in ctx:
        return ctx["native"]
    run, stdout = ctx.get("run"), ctx.get("evidence", {}).get("stdout.jsonl")
    if run is None or stdout is None:
        ctx["native"] = None
        return None
    events, parse_problems, preamble = parse_stream_with_host_preamble(stdout)
    assoc = associate(events, run["reserved"]["native_session_id"]) if events else None
    ctx["native"] = {"events": events, "parse_problems": parse_problems, "preamble": preamble,
                     "assoc": assoc, "stdout": stdout}
    return ctx["native"]


def gate_adapter(ctx: Dict[str, Any], rep: Report) -> None:
    nat, run = native_view(ctx), ctx.get("run")
    if nat is None:
        rep.add("adapter.gates", UNKNOWN, "no bound run or no verified stdout")
        return
    col, art = run["collected"], _artifact(run)
    if art is None:
        rep.add("adapter.gates", FAIL, "collected record does not carry exactly one artifact object")
        return
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
    assoc = nat["assoc"]
    if nat["parse_problems"] or assoc is None or assoc["problems"]:
        problems.append("stdout re-parse: " + "; ".join((nat["parse_problems"] + (assoc or {"problems": ["no events"]})
                                                         ["problems"])[:4]))
    elif assoc["tool_calls"] != art.get("tool_calls") or assoc["assistant_text"] != art.get("assistant_text"):
        problems.append("re-parsed stdout differs from the recorded artifact")
    if problems and "adapter_gates" not in WEAKEN:
        rep.add("adapter.gates", FAIL, "; ".join(problems))
        return
    rep.add("adapter.gates", PASS, "recorded adapter gates hold and stdout re-parses to the same artifact",
            backend_models=art.get("backend_models"), denied_tool_requests=art.get("denied_tool_requests"))
    if assoc is not None:
        ctx["events"], ctx["assoc"], ctx["art"] = nat["events"], assoc, art


def gate_native_status(ctx: Dict[str, Any], rep: Report) -> None:
    """R2: host result status / declared status / native state from the native bytes."""
    nat, run, art = native_view(ctx), ctx.get("run"), ctx.get("art")
    if nat is None or art is None or nat["assoc"] is None:
        rep.add("native.status", UNKNOWN, "no verified stream / artifact")
        return
    col, assoc = run["collected"], nat["assoc"]
    host_status = (assoc["result"] or {}).get("status")
    clean = (not nat["parse_problems"] and not assoc["problems"] and art.get("exit_code") == 0
             and host_status == "success")
    problems = []
    if host_status != "success":
        problems.append(f"stdout result.status is {host_status!r}")
    # The adapter writes state declared_complete only for a clean run, and declared_status
    # "success" when clean, else the host's own status (or "unattributed").
    allowed = {host_status, "unattributed"} | ({"success"} if clean else set())
    if col.get("state") == "declared_complete" and not clean:
        problems.append("collected state declared_complete but the native stream/exit does not support it")
    if col.get("declared_status") not in allowed:
        problems.append(f"declared_status {col.get('declared_status')!r} is not derivable from the native "
                        f"result {host_status!r}")
    if col.get("native_state") != f"exit:{art.get('exit_code')};result:{host_status}":
        problems.append(f"native_state {col.get('native_state')!r} disagrees with exit/result")
    if problems and "native_status" not in WEAKEN:
        rep.add("native.status", FAIL, "; ".join(problems))
    else:
        rep.add("native.status", PASS, f"result.status {host_status!r} agrees with the collected record")


def gate_native_model(ctx: Dict[str, Any], rep: Report) -> None:
    nat, run, art = native_view(ctx), ctx.get("run"), ctx.get("art")
    if nat is None or art is None or nat["assoc"] is None:
        rep.add("native.model", UNKNOWN, "no verified stream / artifact")
        return
    assoc = nat["assoc"]
    init_model = (assoc["init"] or {}).get("model")
    requested = run["reserved"]["requested_model"]
    stats = (assoc["result"] or {}).get("stats")
    problems = []
    if init_model != requested:
        problems.append(f"init.model {init_model!r} != requested {requested!r}")
    if art.get("host_reported_model") != init_model:
        problems.append("artifact host_reported_model != init.model")
    if isinstance(stats, dict) and isinstance(stats.get("models"), dict):
        if art.get("backend_models") != sorted(stats["models"]):
            problems.append("artifact backend_models != result.stats.models")
    if problems and "native_model" not in WEAKEN:
        rep.add("native.model", FAIL, "; ".join(problems))
    else:
        rep.add("native.model", PASS, "init.model = requested = recorded", init_model=init_model)


def gate_artifact_identity(ctx: Dict[str, Any], rep: Report) -> None:
    """R2 (Hark-派单-23): identity claims the collected record makes about this run."""
    run, art = ctx.get("run"), ctx.get("art")
    if run is None or art is None:
        rep.add("native.identity", UNKNOWN, "no bound run / artifact")
        return
    res, col = run["reserved"], run["collected"]
    raw = col.get("raw_refs")
    problems = []
    if art.get("native_session_id") != res["native_session_id"]:
        problems.append("artifact native_session_id != the run's native session")
    if art.get("requested_model") != res["requested_model"]:
        problems.append("artifact requested_model != the run's requested model")
    if not isinstance(raw, list) or not raw or raw[0] != res["native_session_id"]:
        problems.append("collected raw_refs do not start with the run's native session")
    if problems and "artifact_identity" not in WEAKEN:
        rep.add("native.identity", FAIL, "; ".join(problems))
    else:
        rep.add("native.identity", PASS, "artifact session/model identity = the run's")


def gate_usage(ctx: Dict[str, Any], rep: Report) -> None:
    nat, run = native_view(ctx), ctx.get("run")
    if nat is None or nat["assoc"] is None:
        rep.add("native.usage", UNKNOWN, "no verified stream")
        return
    usage, missing = usage_from_result(nat["assoc"]["result"])
    if run["collected"].get("usage") != usage and "usage" not in WEAKEN:
        rep.add("native.usage", FAIL, "collected usage differs from result.stats")
    elif missing and "usage" not in WEAKEN:
        rep.add("native.usage", UNKNOWN, "usage not fully reported by the host: " + "; ".join(missing))
    else:
        rep.add("native.usage", PASS, "usage = result.stats", usage=usage)


def gate_host_preamble(ctx: Dict[str, Any], rep: Report) -> None:
    nat, art = native_view(ctx), ctx.get("art")
    if nat is None or art is None:
        rep.add("native.host_preamble", UNKNOWN, "no verified stream / artifact")
        return
    preamble, recorded = nat["preamble"], art.get("host_diagnostics")
    weak = "host_preamble" in WEAKEN
    if not preamble:
        if recorded is not None and not weak:
            rep.add("native.host_preamble", FAIL, "artifact records host diagnostics the stream does not carry")
        else:
            rep.add("native.host_preamble", PASS, "no host stdout preamble")
        return
    size = sum(p["bytes"] for p in preamble)
    want = {"lines": preamble, "preamble_bytes": size, "preamble_sha256": sha256(nat["stdout"][:size]),
            "stdout_sha256": sha256(nat["stdout"])}
    if (not isinstance(recorded, dict) or any(recorded.get(k) != v for k, v in want.items())) and not weak:
        rep.add("native.host_preamble", FAIL, "artifact host diagnostics differ from the stdout preamble")
        return
    stderr = ctx.get("evidence", {}).get("stderr.txt")
    if stderr is None and not weak:
        rep.add("native.host_preamble", UNKNOWN, "host stdout preamble present but stderr evidence not "
                                                 "verified: final save failure cannot be ruled out")
    elif stderr is not None and HOST_SAVE_FINAL_FAILURE in stderr and not weak:
        rep.add("native.host_preamble", FAIL, "host stdout preamble and the final project registry save "
                                              "failure on stderr")
    else:
        rep.add("native.host_preamble", PASS, f"{len(preamble)} known preamble line(s); no final save failure")


def gate_host_errors(ctx: Dict[str, Any], rep: Report) -> None:
    nat, art = native_view(ctx), ctx.get("art")
    if nat is None or art is None or nat["assoc"] is None:
        rep.add("native.errors", UNKNOWN, "no verified stream / artifact")
        return
    assoc = nat["assoc"]
    problems = [name for name, key in (("host_errors", "errors"), ("denied_tool_requests", "denied_requests"),
                                       ("unconfirmed_tool_requests", "unconfirmed_requests"))
                if art.get(name) != assoc[key]]
    if problems and "host_errors" not in WEAKEN:
        rep.add("native.errors", FAIL, f"artifact lists differ from the native stream: {problems}")
    else:
        rep.add("native.errors", PASS, "error / denied / unconfirmed lists = native stream",
                host_errors=assoc["errors"])


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


# ------------------------------------------------------------ chat record --

def _fr_parts(parts: Any) -> Optional[List[Dict[str, Any]]]:
    if not isinstance(parts, list):
        return None
    out = []
    for part in parts:
        if isinstance(part, dict) and "functionResponse" in part:
            out.append(part["functionResponse"])
    return out


def part_problem(part: Any) -> Optional[Tuple[str, str]]:
    """(FAIL|UNKNOWN, why) for one content / result part, or None when it is one of the
    observed shapes: {"text": str} or {"functionResponse": {id: str, name: str,
    response: {output: str} | {error: str}}}."""
    if not isinstance(part, dict):
        return FAIL, f"part is {type(part).__name__}, not an object"
    if "functionResponse" in part:
        fr = part["functionResponse"]
        if set(part) != {"functionResponse"}:
            return UNKNOWN, "functionResponse part with extra keys (not observed)"
        if not isinstance(fr, dict) or not isinstance(fr.get("id"), str) or not isinstance(fr.get("name"), str) \
                or not isinstance(fr.get("response"), dict):
            return FAIL, "functionResponse id/name/response of the wrong type"
        resp = fr["response"]
        if set(fr) != {"id", "name", "response"} or len(resp) != 1 or set(resp) - {"output", "error"}:
            return UNKNOWN, "functionResponse of an unobserved shape"
        if not all(isinstance(v, str) for v in resp.values()):
            return FAIL, "functionResponse output/error is not a string"
        return None
    if set(part) == {"text"}:
        return None if isinstance(part["text"], str) else (FAIL, "text part is not a string")
    return UNKNOWN, "content part of an unobserved shape"


def _same_or_conflict(items: List[Any]) -> Optional[str]:
    """None for exactly one item; 'duplicate' for identical copies; 'conflict' otherwise."""
    if len(items) == 1:
        return None
    first = json.dumps(items[0], sort_keys=True)
    return "duplicate" if all(json.dumps(i, sort_keys=True) == first for i in items[1:]) else "conflict"


def parse_chat(data: bytes, session_id: str, model: Optional[str]) -> Dict[str, Any]:
    """The host chat record of CLI 0.65.0 in its observed shape.

    Line 1: session header {sessionId, projectHash, startTime, lastUpdated, kind}, all
    strings. Later lines: {"$set": {lastUpdated: str}} updates, or message records
    {id: str, type: user|gemini, ...}. user content is a list of parts; gemini content
    is a string. The host rewrites a message by appending a record with the SAME id
    (an observed rewrite): the last write is used, and it must keep the type, the model
    and every tool call of the earlier write. Inside ONE toolCalls array a tool id may
    appear once. Each tool call's result holds exactly one functionResponse with the
    call's id/name; the following user record repeats it once. Identical extra copies
    are unknown (not observed); differing copies are a conflict (fail) - no copy is
    ever chosen over another. Wrong types fail; other unobserved shapes are unknown.
    Returns calls (id -> last tool call), fail / unknown lists."""
    if "chat_record" in WEAKEN:            # weakened (tests only): the Hark-21 lenient reading
        calls_w, _ = chat_tool_calls(data)
        return {"calls": calls_w, "fail": [], "unknown": [], "user_frs": {}}
    fail: List[str] = []
    unknown: List[str] = []
    calls: Dict[str, Any] = {}
    owner: Dict[str, str] = {}
    messages: Dict[str, Dict[str, Any]] = {}
    user_frs: Dict[str, List[Any]] = {}
    try:
        text = data.decode("utf-8")
    except UnicodeDecodeError as exc:
        return {"calls": {}, "fail": [f"chat record is not UTF-8: {exc}"], "unknown": [], "user_frs": {}}
    lines = text.split("\n")
    if lines and lines[-1] == "":
        lines = lines[:-1]
    else:
        unknown.append("chat record does not end with a newline")

    def note(problem: Optional[Tuple[str, str]], where: str) -> bool:
        if problem is None:
            return False
        (fail if problem[0] == FAIL else unknown).append(f"{where}: {problem[1]}")
        return True

    for n, line in enumerate(lines, 1):
        try:
            rec = json.loads(line)
        except (ValueError, RecursionError):
            fail.append(f"line {n} is not JSON")
            continue
        if not isinstance(rec, dict):
            fail.append(f"line {n} is {type(rec).__name__}, not an object")
            continue
        if n == 1:
            if any(k not in rec for k in CHAT_HEADER_KEYS):
                fail.append("line 1 is not a session header")
            elif any(not isinstance(rec[k], str) for k in CHAT_HEADER_KEYS):
                fail.append("line 1: session header field of the wrong type")
            elif rec["sessionId"] != session_id:
                fail.append(f"header sessionId {rec['sessionId']!r} != this run's session {session_id!r}")
            elif rec.get("kind") != "main":
                unknown.append(f"header kind {rec.get('kind')!r} not observed")
            elif set(rec) != set(CHAT_HEADER_KEYS):
                unknown.append("line 1: session header with unobserved keys")
            continue
        if "sessionId" in rec:
            (fail if rec["sessionId"] != session_id else unknown).append(
                f"line {n}: sessionId on a non-header line ({rec['sessionId']!r})")
            continue
        if set(rec) == {"$set"}:
            upd = rec["$set"]
            if not isinstance(upd, dict) or any(not isinstance(v, str) for v in upd.values()):
                fail.append(f"line {n}: $set update of the wrong type")
            elif any(k not in CHAT_SET_KEYS for k in upd):
                unknown.append(f"line {n}: $set update of an unobserved shape")
            continue
        mid, mtype = rec.get("id"), rec.get("type")
        if not isinstance(mid, str) or not isinstance(mtype, str):
            (fail if "id" in rec or "type" in rec else unknown).append(f"line {n}: record id/type missing or not strings")
            continue
        if mtype not in CHAT_TYPES:
            unknown.append(f"line {n}: message type {mtype!r} not observed")
            continue
        if "timestamp" in rec and not isinstance(rec["timestamp"], str):
            fail.append(f"line {n}: timestamp is not a string")
        prev = messages.get(mid)
        tcs: Any = []
        if mtype == "user":
            if "toolCalls" in rec:
                fail.append(f"line {n}: user record carries toolCalls")
            content = rec.get("content")
            if not isinstance(content, list):
                fail.append(f"line {n}: user content is {type(content).__name__}, not a list")
            else:
                for k, part in enumerate(content):
                    if note(part_problem(part), f"line {n} content[{k}]"):
                        continue
                    if "functionResponse" in part:
                        user_frs.setdefault(part["functionResponse"]["id"], []).append(part["functionResponse"])
        else:
            if model is not None and rec.get("model") != model:
                fail.append(f"line {n}: gemini record model {rec.get('model')!r} != requested {model!r}")
            if not isinstance(rec.get("content"), str):
                fail.append(f"line {n}: gemini content is not a string")
            tcs = rec.get("toolCalls", [])
            if not isinstance(tcs, list):
                fail.append(f"line {n}: toolCalls is {type(tcs).__name__}, not a list")
                tcs = []
        this_ids: List[str] = []
        for call in tcs:
            if not (isinstance(call, dict) and isinstance(call.get("id"), str) and isinstance(call.get("name"), str)
                    and isinstance(call.get("args"), dict) and isinstance(call.get("status"), str)
                    and isinstance(call.get("result"), list)):
                fail.append(f"line {n}: tool call of an illegal shape")
                continue
            cid = call["id"]
            if cid in this_ids:
                fail.append(f"line {n}: tool call {cid!r} repeated inside one toolCalls array "
                            "(not an observed rewrite)")
                continue
            frs = []
            for k, part in enumerate(call["result"]):
                if note(part_problem(part), f"line {n} {cid!r} result[{k}]"):
                    continue
                if "functionResponse" in part:
                    frs.append(part["functionResponse"])
                else:
                    unknown.append(f"line {n} {cid!r} result[{k}]: non-functionResponse part (not observed)")
            if not frs:
                fail.append(f"line {n}: tool call {cid!r} carries no functionResponse")
                continue
            if any(fr["id"] != cid or fr["name"] != call["name"] for fr in frs):
                fail.append(f"line {n}: functionResponse id/name disagree with tool call {cid!r}")
                continue
            kind = _same_or_conflict(frs)
            if kind == "conflict":
                fail.append(f"line {n}: tool call {cid!r} has conflicting functionResponses")
                continue
            if kind == "duplicate":
                unknown.append(f"line {n}: tool call {cid!r} has repeated identical functionResponses")
            if owner.get(cid, mid) != mid:
                fail.append(f"line {n}: tool call {cid!r} appears under two messages")
                continue
            owner[cid] = mid
            this_ids.append(cid)
            calls[cid] = call
        if prev is not None:
            if prev["type"] != mtype or prev.get("model") != rec.get("model"):
                fail.append(f"line {n}: rewrite of {mid!r} changes its type or model")
            lost = [i for i in prev["ids"] if i not in this_ids]
            if lost:
                fail.append(f"line {n}: rewrite of {mid!r} drops tool calls {lost}")
            for i in prev["ids"]:
                if i in this_ids and (prev["calls"][i]["name"], prev["calls"][i]["args"]) != \
                        (calls[i]["name"], calls[i]["args"]):
                    fail.append(f"line {n}: rewrite of {mid!r} changes tool call {i!r}")
        messages[mid] = {"type": mtype, "model": rec.get("model"), "ids": this_ids,
                         "calls": {i: calls[i] for i in this_ids}}
    for cid, frs in user_frs.items():
        call = calls.get(cid)
        if call is None:
            fail.append(f"user functionResponse {cid!r} has no tool call")
            continue
        own = [p["functionResponse"] for p in call["result"] if isinstance(p, dict) and "functionResponse" in p]
        kind = _same_or_conflict(frs)
        if kind == "conflict" or any(fr != own[0] for fr in frs):
            fail.append(f"user functionResponse {cid!r} conflicts with its tool call or another copy")
        elif kind == "duplicate":
            unknown.append(f"user functionResponse {cid!r} repeated (not observed)")
    for cid in calls:
        if cid not in user_frs:
            unknown.append(f"tool call {cid!r} has no user functionResponse record")
    return {"calls": calls, "fail": fail, "unknown": unknown, "user_frs": user_frs}


def chat_tool_calls(data: bytes) -> Tuple[Optional[Dict[str, Any]], List[str]]:
    """toolCalls by id (last write wins), kept for the host-format sample test."""
    calls: Dict[str, Any] = {}
    for line in data.decode("utf-8", "replace").splitlines():
        try:
            rec = json.loads(line) if line.strip() else {}
        except (ValueError, RecursionError):
            continue
        for call in (rec.get("toolCalls") if isinstance(rec, dict) and isinstance(rec.get("toolCalls"), list) else []):
            if isinstance(call, dict) and isinstance(call.get("id"), str):
                calls[call["id"]] = call
    return calls, []


def chat_output(call: Dict[str, Any]) -> Optional[str]:
    for fr in _fr_parts(call.get("result")) or []:
        if isinstance(fr, dict) and fr.get("id") == call.get("id"):
            resp = fr.get("response")
            if isinstance(resp, dict) and isinstance(resp.get("output"), str):
                return resp["output"]
    return None


# ------------------------------------------------------------ paths ----------

def workspace_is_nt(workspace: str) -> bool:
    return bool(re.match(r"^[A-Za-z]:[\\/]", workspace or "")) or "\\" in (workspace or "")


def nt_kind(path: str) -> str:
    if path.startswith(("\\\\", "//")):
        return "unc_or_device"
    if re.match(r"^[A-Za-z]:[\\/]", path):
        return "drive_absolute"
    if re.match(r"^[A-Za-z]:", path):
        return "drive_relative"
    if path[:1] in ("\\", "/"):
        return "root_relative"
    return "relative"


def map_to_export(path: Any, workspace: str) -> Optional[str]:
    """Relative export path for a path the host / delivery used, or None.

    Decided on the text only, never resolved against the real disk. Windows-flavoured
    workspace (drive-absolute): accepted are drive-absolute paths under the workspace
    (case-insensitive prefix) and plain relative paths; root-relative (\\src, /src),
    drive-relative (C:src), UNC and device paths are refused. POSIX workspace:
    absolute paths under it and relative paths without backslashes. Every component
    must be a plain name ('..', ':', trailing dot/space refused)."""
    if not isinstance(path, str) or not path or "\x00" in path or not isinstance(workspace, str):
        return None
    if workspace_is_nt(workspace):
        if nt_kind(workspace) != "drive_absolute":
            return None
        kind = nt_kind(path)
        if kind in ("unc_or_device", "drive_relative", "root_relative"):
            if "ws_path_kinds" not in WEAKEN:
                return None
            rel = re.sub(r"^(?:[A-Za-z]:)?[\\/]*", "", path)        # weakened: the old misreading
        elif kind == "drive_absolute":
            ws = workspace.replace("\\", "/").rstrip("/")
            cand = path.replace("\\", "/")
            if not cand.lower().startswith(ws.lower() + "/"):
                return None
            rel = cand[len(ws) + 1:]
        else:
            rel = path
        rel = rel.replace("\\", "/")
    else:
        if path.startswith("/"):
            ws = workspace.rstrip("/")
            if not path.startswith(ws + "/"):
                return None
            rel = path[len(ws) + 1:]
        elif "\\" in path and "ws_path_kinds" not in WEAKEN:
            return None
        else:
            rel = path.replace("\\", "/")
    parts = [p for p in rel.split("/") if p != "."]
    if not parts or any(name_problem(p) for p in parts):
        return None
    return "/".join(parts)


def export_lines(ctx: Dict[str, Any], rel: str) -> Optional[List[str]]:
    if rel not in ctx["manifest"]["files"]:
        return None
    cache = ctx.setdefault("_lines", {})
    if rel not in cache:
        data = safe_read(ctx["export_dir"], rel.split("/"), "export_root" not in WEAKEN)
        cache[rel] = data.decode("utf-8").split("\n")
    return cache[rel]


# ------------------------------------------------------------ tool content --

def check_grep(ctx: Dict[str, Any], args: Dict[str, Any], output: str) -> Tuple[str, str, int]:
    weak = "grep_binding" in WEAKEN
    ws = ctx["run"]["reserved"]["workspace"]
    lines = output.split("\n")
    if weak and output.startswith("No matches found"):          # weakened: the Hark-21/22 shortcut
        return PASS, "no matches reported", 0
    # Request boundaries first: they hold for EVERY output branch (Hark-派单-23 R3).
    if not weak:
        extra = sorted(k for k in args if k not in GREP_KEYS)
        pattern, context = args.get("pattern"), args.get("context")
        if not isinstance(pattern, str):
            return FAIL, f"pattern {pattern!r} is not a string", 0
        if context is not None and (not isinstance(context, int) or isinstance(context, bool) or context < 0):
            return FAIL, f"context {context!r} is not a non-negative integer", 0
        if extra:
            return UNKNOWN, f"grep parameters {extra} not observed; result cannot be bound", 0
    head = GREP_HEAD.match(lines[0]) if lines else None
    if not head:
        if not weak and output.startswith("No matches found"):
            return UNKNOWN, "no-match output: format not observed for CLI 0.65.0 (cannot bind to the request)", 0
        return UNKNOWN, "unrecognised grep output header", 0
    if not weak:
        if head.group(3) != args.get("pattern"):
            return FAIL, "output header pattern differs from the requested pattern", 0
        if head.group(4) != ".":
            return FAIL, f"output header path {head.group(4)!r} but the request names no path", 0
        if head.group(5) is not None:
            return FAIL, "output header has a filter the request does not carry", 0
    context = args.get("context")
    if context is not None and (not isinstance(context, int) or isinstance(context, bool) or context < 0):
        return FAIL, f"context {context!r} is not a non-negative integer", 0
    try:
        rx = re.compile(args.get("pattern", ""), re.I)
    except (re.error, TypeError):
        rx = None
    current, matches, target_hits, checked = None, 0, 0, 0
    per_file: Dict[str, List[Tuple[int, str]]] = {}
    order: List[str] = []
    for line in lines[1:]:
        if line == "---" or line == "":
            continue
        if line.startswith("File: "):
            current = map_to_export(line[6:], ws)
            if current is None or export_lines(ctx, current) is None:
                return FAIL, f"grep names a file not in the export: {line[6:]!r}", target_hits
            if current in per_file and not weak:
                return UNKNOWN, f"file {current!r} listed twice (format not observed)", target_hits
            per_file.setdefault(current, [])
            order.append(current)
            continue
        m = GREP_LINE.match(line)
        if not m or current is None:
            return UNKNOWN, f"unrecognised grep line {line[:60]!r}", target_hits
        n, sep, text = int(m.group(1)), m.group(2), m.group(3)
        src = export_lines(ctx, current)
        if (not 1 <= n <= len(src) or src[n - 1].rstrip("\r") != text.rstrip("\r")) and "tool_content" not in WEAKEN:
            return FAIL, f"grep line {current}:{n} does not equal the exported source", target_hits
        per_file[current].append((n, sep))
        checked += 1
        if sep == ":":
            matches += 1
            if not weak and (rx is None or not rx.search(text)):
                return UNKNOWN, f"match line {current}:{n} not confirmed against the requested pattern", target_hits
            if current == ctx["package"]["target_file"]:
                target_hits += 1
    if matches != int(head.group(1)):
        return UNKNOWN, f"header says {head.group(1)} matches, saw {matches} match lines", target_hits
    if not weak:
        for rel, seen in per_file.items():
            nums = [n for n, _ in seen]
            if nums != sorted(set(nums)):
                return UNKNOWN, f"{rel}: lines not strictly ascending (format not observed)", target_hits
            hits = [n for n, s in seen if s == ":"]
            if context is None:
                if any(s == "-" for _, s in seen):
                    return UNKNOWN, "context lines without a recorded context value", target_hits
                continue
            if context == 0:
                if len(hits) != len(seen):
                    return FAIL, f"{rel}: context lines although context is 0", target_hits
                continue
            if len(hits) != 1:
                return UNKNOWN, f"{rel}: {len(hits)} matches with context {context} (merging not observed)", \
                    target_hits
            total = len(export_lines(ctx, rel))
            window = list(range(max(1, hits[0] - context), min(total, hits[0] + context) + 1))
            if nums != window:
                return FAIL, f"{rel}: printed lines are not the context window of the match", target_hits
    return PASS, f"{checked} lines equal the exported source and are bound to the request", target_hits


def check_read(ctx: Dict[str, Any], params: Dict[str, Any], output: str,
               stream_output: Any) -> Tuple[str, str, bool]:
    weak = "read_binding" in WEAKEN
    ws = ctx["run"]["reserved"]["workspace"]
    rel = map_to_export(params.get("file_path"), ws)
    if rel is None:
        return FAIL, "read_file path is outside the recorded workspace or of a refused kind (not read)", False
    src = export_lines(ctx, rel)
    if src is None:
        return FAIL, f"read_file path {rel!r} is not an exported file", False
    start, end = params.get("start_line"), params.get("end_line")
    if not weak:
        if any(v is not None and (not isinstance(v, int) or isinstance(v, bool)) for v in (start, end)):
            return FAIL, "start_line/end_line are not integers", False
        extra = sorted(k for k in params if k not in READ_KEYS)
        if extra:
            return UNKNOWN, f"read_file parameters {extra} not observed; result cannot be bound", False
    m = READ_TRUNC.match(output)
    if not m:
        return UNKNOWN, "unrecognised read_file output format (e.g. untruncated whole file)", False
    a, b, total, content = int(m.group(1)), int(m.group(2)), int(m.group(3)), m.group(4)
    if total != len(src) or not 1 <= a <= b <= total:
        return FAIL, f"line range {a}-{b} of {total} does not fit {rel} ({len(src)} lines)", False
    if content != "\n".join(src[a - 1:b]) and "tool_content" not in WEAKEN:
        return FAIL, f"read content {rel}:{a}-{b} does not equal the exported source", False
    if not weak:
        if start is None or end is None:
            return UNKNOWN, "read_file without an explicit start_line/end_line: host default range not observed", \
                False
        if (a, b) != (start, end):
            if a == start and end > total and b == total:
                return UNKNOWN, "requested end beyond the file; host clamping not observed", False
            return FAIL, f"response shows lines {a}-{b} but the request asked for {start}-{end}", False
    s = READ_SUMMARY.match(stream_output) if isinstance(stream_output, str) else None
    if not s:
        return UNKNOWN, "stream summary for this read is absent or unrecognised", False
    if (int(s.group(1)), int(s.group(2)), int(s.group(3))) != (a, b, total) or map_to_export(s.group(4), ws) != rel:
        return FAIL, "stream summary disagrees with the chat record content", False
    return PASS, f"{rel}:{a}-{b} equals the exported source and the request", rel == ctx["package"]["target_file"]


def args_relation(name: str, stream: Dict[str, Any], chat: Dict[str, Any]) -> Tuple[str, str]:
    """('same'|'default'|'extra'|'differ', detail) between stream parameters and chat args."""
    common_diff = [k for k in stream if k not in chat or chat[k] != stream[k]]
    if common_diff:
        return "differ", f"parameters {sorted(common_diff)} differ between stream and chat record"
    extra = {k: chat[k] for k in chat if k not in stream}
    if not extra:
        return "same", ""
    if name == "grep_search" and all(k in GREP_DEFAULTS and v == GREP_DEFAULTS[k] for k, v in extra.items()):
        return "default", f"host default {extra} (observed)"
    return "extra", f"chat record adds {sorted(extra)} not explained by an observed default"


def gate_tools(ctx: Dict[str, Any], rep: Report) -> None:
    assoc, chat = ctx.get("assoc"), ctx.get("evidence", {}).get("host-chat-record.jsonl")
    if assoc is None or chat is None or not ctx.get("export_ok"):
        rep.add("tools.content", UNKNOWN, "needs a verified stream, chat record and export")
        return
    run = ctx["run"]
    parsed = parse_chat(chat, run["reserved"]["native_session_id"], run["reserved"]["requested_model"])
    calls = parsed["calls"]
    stream_ids = {c["tool_id"] for c in assoc["tool_calls"]}
    extra_ids = sorted(set(calls) - stream_ids)
    fails = list(parsed["fail"])
    if extra_ids and "chat_record" not in WEAKEN:
        fails.append(f"chat record has tool calls the stream does not report: {extra_ids[:4]}")
    if fails:
        rep.add("chat.record", FAIL, "; ".join(fails[:6]), count=len(fails))
    elif parsed["unknown"]:
        rep.add("chat.record", UNKNOWN, "; ".join(parsed["unknown"][:6]))
    else:
        rep.add("chat.record", PASS, "one session, supported record shape and rewrites, tool calls agree")
    grep_hits, reads = 0, 0
    for call in assoc["tool_calls"]:
        name, tid = call["tool_name"], call["tool_id"]
        status = (call["result"] or {}).get("status")
        chat_call = calls.get(tid)
        if chat_call is None:
            ctx["tool_unknown"] = True
            rep.add(f"tools.{tid}", UNKNOWN, "no matching call in the host chat record", tool=name)
            continue
        if chat_call.get("name") != name or chat_call.get("status") != status:
            rep.add(f"tools.{tid}", FAIL, "stream request/result and chat record disagree on name/status", tool=name)
            continue
        relation, rdetail = args_relation(name, call["parameters"], chat_call["args"])
        if relation == "differ":
            rep.add(f"tools.{tid}", FAIL, rdetail, tool=name)
            continue
        if status != "success":
            rep.add(f"tools.{tid}", PASS if status == "error" else UNKNOWN,
                    f"{name} status {status!r}: not a source of evidence", tool=name)
            continue
        if name not in ("grep_search", "read_file"):
            rep.add(f"tools.{tid}", PASS, f"{name}: recorded, content not compared", tool=name)
            continue
        flag = "grep_binding" if name == "grep_search" else "read_binding"
        if relation == "extra" and flag not in WEAKEN:
            ctx["tool_unknown"] = True
            rep.add(f"tools.{tid}", UNKNOWN, rdetail, tool=name)
            continue
        output = chat_output(chat_call)
        if output is None:
            ctx["tool_unknown"] = True
            rep.add(f"tools.{tid}", UNKNOWN, "chat record carries no tool output", tool=name)
            continue
        if name == "grep_search":
            status_, detail, hits = check_grep(ctx, chat_call["args"], output)
            if status_ == PASS:
                grep_hits += hits
        else:
            status_, detail, on_target = check_read(ctx, chat_call["args"], output,
                                                    (call["result"] or {}).get("output"))
            if status_ == PASS and on_target:
                reads += 1
        if status_ == UNKNOWN:
            ctx["tool_unknown"] = True
        rep.add(f"tools.{tid}", status_, detail + (f"; {rdetail}" if rdetail else ""), tool=name,
                parameters=call["parameters"], output_sha256=sha256(output.encode("utf-8")))
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


def extract_refs(text: str) -> List[Tuple[str, int, int, str]]:
    """(path text, start, end, whole token) for every path:line reference in text."""
    out = []
    if "ref_extraction" in WEAKEN:                  # weakened: the Hark-22 extraction
        for m in SOURCE_REF.finditer(text):
            out.append((m.group(1), int(m.group(2)), int(m.group(3) or m.group(2)), m.group(0)))
        return out
    for m in REF_TOKEN.finditer(text):
        path = m.group(1)
        if not any(c in path for c in "./\\"):
            continue
        out.append((path, int(m.group(2)), int(m.group(3) or m.group(2)), m.group(0)))
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
    for fname, span in pkg["function_ranges"].items():
        if not isinstance(span, list) or tuple(ranges.get(fname, ())) != tuple(span):
            rep.add("refs.ranges", FAIL, f"package range for {fname} does not match the source AST")
            return
    refs, bad = [], []
    for path, start, end, token in extract_refs(text):
        rel = map_to_export(path, ctx["run"]["reserved"]["workspace"])
        lines = export_lines(ctx, rel) if rel else None
        if lines is None or not 1 <= start <= end <= len(lines):
            bad.append(token)
        else:
            refs.append((rel, start, end))
    if not refs and not bad:
        rep.add("refs.exist", FAIL, "delivery carries no path:line reference")
        return
    if bad and "refs_exist" not in WEAKEN:
        rep.add("refs.exist", FAIL, f"references that do not exist in the export: {bad[:6]}", bad_refs=bad)
    else:
        rep.add("refs.exist", PASS, f"{len(refs)} references exist in the export")
    for point in CORE_POINTS:
        spec = pkg["core_points"].get(point)
        if not isinstance(spec, dict):
            rep.add(f"refs.{point}", FAIL, "package has no usable spec for this core point")
            continue
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
         gate_adapter, gate_native_status, gate_native_model, gate_artifact_identity, gate_usage, gate_host_preamble,
         gate_host_errors, gate_stdin, gate_tools, gate_required, gate_refs]


def _run_all(ctx: Dict[str, Any], rep: Report) -> None:
    try:
        d, b = split_file_arg(ctx["package_path"])
        if "input_paths" in WEAKEN:
            ctx["package_bytes"] = plain_read(ctx["package_path"])
        else:
            ctx["package_bytes"] = safe_read(d, [b], check_root=False)
    except Refused as exc:
        rep.add("package.read", FAIL, f"package refused before reading: {exc}")
        return
    ctx["package_dir"] = d
    gate_package_anchor(ctx, rep)
    load_manifest(ctx, rep)
    if "manifest" not in ctx:
        raise Stop()
    for gate in GATES:
        gate(ctx, rep)


def run_checks(package_path: str, manifest_path: str, export_dir: str, evidence_root: str,
               run_id: str) -> Dict[str, Any]:
    ctx: Dict[str, Any] = {"package_path": package_path, "manifest_path": manifest_path,
                           "export_dir": os.path.abspath(export_dir),
                           "evidence_root": os.path.abspath(evidence_root), "run_id": run_id}
    rep = Report()
    try:
        _run_all(ctx, rep)
    except Stop:
        pass
    except Refused as exc:
        rep.add("boundary", FAIL, f"refused before access: {exc}")
    except (TypeError, AttributeError, KeyError, ValueError, IndexError, RecursionError, UnicodeDecodeError) as exc:
        rep.add("checker.input_shape", FAIL, f"an input has an unsupported shape: {type(exc).__name__}: {exc}")
    assoc = ctx.get("assoc") or {}
    delivery = assoc.get("assistant_text")
    pkg = ctx.get("package") or {}
    return {
        "schema": SCHEMA,
        "kind": "mechanical consistency check only",
        "inputs": {"package_sha256": sha256(ctx["package_bytes"]) if "package_bytes" in ctx else None,
                   "manifest_sha256": ctx.get("manifest_sha256"), "run_id": run_id,
                   "pinned_package_sha256": pins.PACKAGE_SHA256},
        "mechanical": {"overall": rep.overall(), "items": rep.items},
        "semantic_review": {
            "status": "required",
            "by": "architect (independent review)",
            "baseline": pkg.get("review_baseline_file"),
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
    if WEAKEN and os.environ.get("HARK21_TEST_WEAKEN") != "1":
        print("guard weakening is a test-only hook; refusing to run with it set", file=sys.stderr)
        return 2
    if args.out:
        out = os.path.realpath(args.out)
        for root in (args.export_dir, args.evidence_root):
            r = os.path.realpath(root)
            if out == r or out.startswith(r.rstrip(os.sep) + os.sep):
                print("--out must be outside the export and the evidence root", file=sys.stderr)
                return 2
    try:
        result = run_checks(args.package, args.manifest, args.export_dir, args.evidence_root, args.run_id)
    except OSError as exc:
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
