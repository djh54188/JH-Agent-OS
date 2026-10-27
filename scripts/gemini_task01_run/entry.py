"""Hark-派单-24/25: thin user entry for ONE real Gemini CLI task01 run (candidate; not admitted).

Run as  python -I -B <repo>/scripts/gemini_task01_run/entry.py <mode> ...  (isolated mode: the
current directory is not on sys.path; this file adds only its own repo). The Windows wrapper
scripts/win/hark24-gemini-task01.ps1 verifies the repo's fixed trees BEFORE it runs this file.

  prepare    offline only. Scrubs its own environment to a minimal whitelist first (so no Git /
             Python child can inherit a key, endpoint or proxy), exports the fixed src/ (B's
             exporter, unchanged) into a NEW work root <parent>/ctx-<stamp> whose repo must be
             <parent>/repo-<stamp> (this file's own repo), records a non-sensitive context and
             prints its sha256. Never looks up a key, never starts Node / the CLI / a model.

  run-real   REVIEW TEMPLATE; not admitted. Needs the context path, the REVIEWED context sha256
             (given on the command line, not read from a sidecar), --authorize-real-model-call and
             the exact --accept-boundary phrase. Every path is DERIVED from the context path in
             one supported layout and the context's fields must equal the derived values; shapes
             and digests are checked before any further file is opened. Then at most one submit
             through the existing GeminiCliAgentSessionAdapter, one bounded collect, B's checker.
             After submit is entered, missing / unreadable evidence never counts as "not
             dispatched"; later failures are frozen, finite, non-passing outcomes.

  candidate  commits ONE whitelisted, format-checked public summary (candidate.json) on a NEW
             local branch WITHOUT touching the worktree or HEAD; the stage (prepare archive,
             failure/unknown archive, collected) is explicit; a verified commit is not task
             success; never pushes; a push hint only when origin is the verified remote.

Hark-26: before importing anything from the repo, the bytes on disk of every importable file are
compared with HEAD's blobs (index flags ignored); the export tree is enumerated without reading
content and refused on any link / junction / reparse point / misplaced realpath; host- or
checker-derived values reach the terminal and the public candidate only as known enums or fixed
labels (the private result file keeps the raw bytes).

Facts relied on: CLI 0.65.0 API-key path = env GEMINI_API_KEY + settings auth "gemini-api-key";
base address from env GOOGLE_GEMINI_BASE_URL; host POSTs <base>/v1beta/models/<model>:
streamGenerateContent?alt=sse (reports/hark-gemini-bridge-20261021/host, synthetic endpoint). A
destination that only speaks another protocol is a blocker; no translation layer exists here.

Scope of the single-dispatch gate: one explicitly chosen <parent> (marker
<parent>/task01-real-dispatch.marker) and one context. Choosing another parent by hand does NOT
lift an unknown earlier request; that is governed by the dispatch, not by this file.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import subprocess
import sys
import time
import uuid
from typing import Any, Dict, List, Optional, Tuple
from urllib.parse import urlsplit

HERE = os.path.dirname(os.path.realpath(__file__))
ROOT = os.path.dirname(os.path.dirname(HERE))
PKG_DIR = os.path.join(ROOT, "scripts", "gemini_task01")
#: Directories whose files Python may import from this repo; every file is checked against the
#: commit before anything is imported (the repo top level is checked non-recursively).
VERIFY_DIRS = ("src", "scripts/gemini_task01", "scripts/gemini_task01_run")
_REPARSE = 0x400                                   # FILE_ATTRIBUTE_REPARSE_POINT (junctions etc.)


def _redirected(st: Any) -> bool:
    import stat as _st
    return bool(_st.S_ISLNK(st.st_mode) or (getattr(st, "st_file_attributes", 0) & _REPARSE)
                or getattr(st, "st_reparse_tag", 0))


def _git_blob_id(data: bytes) -> str:
    return hashlib.sha1(b"blob %d\0" % len(data) + data).hexdigest()


def disk_identity_problems(root: str) -> List[str]:
    """Actual on-disk bytes == the blobs of HEAD for every importable file (index flags such as
    assume-unchanged / skip-worktree are irrelevant here). Runs before any repo module is imported."""
    import stat as _st

    def git_out(*a: str) -> bytes:
        env = {k: os.environ[k] for k in ("PATH", "SYSTEMROOT", "SystemRoot", "WINDIR") if k in os.environ}
        return subprocess.run(["git", "-c", "core.quotepath=false", "-C", root, *a], capture_output=True,
                              check=True, env=env).stdout

    def tree(*a: str) -> Dict[str, Tuple[str, str, str]]:
        out = {}
        for rec in git_out("ls-tree", "-z", *a).split(b"\0"):
            if rec:
                meta, path = rec.split(b"\t", 1)
                mode, kind, oid = meta.decode().split()
                out[path.decode("utf-8")] = (mode, kind, oid)
        return out

    def disk_files(rel_dir: str) -> Dict[str, str]:
        found, stack = {}, [rel_dir]
        while stack:
            cur = stack.pop()
            for name in os.listdir(os.path.join(root, *cur.split("/"))):
                rel = f"{cur}/{name}"
                st = os.lstat(os.path.join(root, *rel.split("/")))
                if _redirected(st):
                    raise ValueError(f"{rel}: link / reparse point")
                if _st.S_ISDIR(st.st_mode):
                    stack.append(rel)
                elif _st.S_ISREG(st.st_mode):
                    found[rel] = rel
                else:
                    raise ValueError(f"{rel}: not a regular file")
        return found

    problems: List[str] = []
    try:
        expected: Dict[str, str] = {}
        for rel_dir in VERIFY_DIRS:
            for path, (mode, kind, oid) in tree("-r", "HEAD", "--", rel_dir).items():
                if kind != "blob" or mode not in ("100644", "100755"):
                    problems.append(f"{path}: not a plain file in the commit")
                expected[path] = oid
        top = tree("HEAD")
        disk: Dict[str, str] = {}
        for rel_dir in VERIFY_DIRS:
            disk.update(disk_files(rel_dir))
        if set(disk) != set(expected):
            problems.append("importable file set on disk differs from the commit "
                            f"(extra {len(set(disk) - set(expected))}, missing {len(set(expected) - set(disk))})")
        top_disk = {n for n in os.listdir(root) if n != ".git"}
        if top_disk != set(top):
            problems.append("repo top-level entries differ from the commit")
        for name, (mode, kind, oid) in top.items():
            full = os.path.join(root, name)
            st = os.lstat(full)
            if _redirected(st) or (kind == "blob") != _st.S_ISREG(st.st_mode):
                problems.append(f"{name}: top-level entry is not what the commit has")
            elif kind == "blob":
                expected[name] = oid
                disk[name] = name
        for path in sorted(set(disk) & set(expected)):
            with open(os.path.join(root, *path.split("/")), "rb") as fh:
                if _git_blob_id(fh.read()) != expected[path]:
                    problems.append(f"{path}: bytes on disk differ from the commit")
    except (OSError, ValueError, subprocess.CalledProcessError, UnicodeDecodeError) as exc:
        problems.append(f"disk identity not determinable: {exc if isinstance(exc, ValueError) else type(exc).__name__}")
    return problems


if __name__ == "__main__":
    _early = disk_identity_problems(ROOT)
    if _early:
        sys.stdout.write(json.dumps({"schema": "hark24/gemini-task01/entry-result/v2", "outcome": "REFUSED",
                                     "summary": "repo files on disk are not the commit's bytes; nothing imported: "
                                     + "; ".join(_early)[:2000]}, ensure_ascii=False, indent=1) + "\n")
        print("HARK24 REFUSED: repo files on disk are not the commit's bytes", file=sys.stderr)
        sys.exit(2)

for _p in (PKG_DIR, ROOT):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import check_run  # noqa: E402  (B, unchanged, read-only use)
import export_source  # noqa: E402  (B, unchanged, read-only use)
import pins  # noqa: E402  (B, unchanged)
from src import agent_session_gemini as gem  # noqa: E402  (A, unchanged)
from src.agent_session import SessionAddress, SubmitRequest  # noqa: E402

SCHEMA = "hark24/gemini-task01/run-context/v2"
RESULT_SCHEMA = "hark24/gemini-task01/entry-result/v2"
CANDIDATE_SCHEMA = "hark24/gemini-task01/public-candidate/v1"
BASELINE_COMMIT = "95d941ec1025470dad4fc1d00e9f4775361e3b91"
REMOTE = "https://github.com/djh54188/JH-Agent-OS.git"
PACKAGE_PATH = os.path.join(PKG_DIR, "package.json")
TASK_PATH = os.path.join(PKG_DIR, "task.txt")

PINNED_FILES = {
    "scripts/gemini_task01/check_run.py": "c88a2d718544e6a51c8d108e08d79df721b9af3ad441a6125f641e4e8d121d44",
    "scripts/gemini_task01/export_source.py": "e112bd031021ff06c09d8b6c4f1be8b0be87ea2d607d7da1b05ec9cc04bd2e48",
    "scripts/gemini_task01/pins.py": "bb774033cf248c163eb5d4da72b1a5fc20379c51f4a962581f867693acae0dbd",
    "scripts/gemini_task01/package.json": "92346a5ffd6f06e07939c269b899f57eed242ddfdfe6dbe850c6abdf3abe746c",
    "scripts/gemini_task01/task.txt": "5ed4f438c57918d7924ea00bf02134c529b20bf9556e6b01c6dffa7830e80518",
    "scripts/gemini_task01/review-baseline.md": "f5ddcbad182fa91ebd48625d59743c6c07c396db47ba828b0fdb512abd58b8c7",
    "src/agent_session_gemini.py": "dbe293f54a163b1c6400a77e579db07463adc344323e9142215da44af8b609e2",
}

PINNED_NODE_VERSION = "v22.23.4"
KNOWN_0650_BUNDLE_SHA256 = "41847af2158ced805c4a3e098a05b9a2c26fb1a49b12583009b2eaf6a4059a59"
ENDPOINT_ENV = "GOOGLE_GEMINI_BASE_URL"
VERIFIED_KEY_ENV_NAMES = ("GEMINI_API_KEY",)
AUTH_TYPE = "gemini-api-key"
PROXY_ENV_NAMES = ("HTTPS_PROXY", "HTTP_PROXY", "NO_PROXY")
#: The only variables Prepare keeps in its own environment (inherited by every child).
MIN_ENV_NAMES = ("PATH", "SYSTEMROOT", "SystemRoot", "WINDIR", "COMSPEC", "PATHEXT", "TEMP", "TMP",
                 "LANG", "LC_ALL")
MODEL_ALIASES = frozenset({"pro", "flash", "flash-lite", "auto"})
_MODEL = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")
_HEX40 = re.compile(r"^[0-9a-f]{40}$")
_HEX64 = re.compile(r"^[0-9a-f]{64}$")
_HEX12 = re.compile(r"^[0-9a-f]{12}$")
_CTX_DIR = re.compile(r"^ctx-(\d{8}-\d{6})$")
ACCEPT_PHRASE = ("one-submit-is-not-one-request;maxSessionTurns-40-is-not-a-cost-cap;"
                 "no-hard-cost-limit;timeout-does-not-prove-stop")
COLLECT_TIMEOUT_DEFAULT = 900.0
COLLECT_TIMEOUT_MAX = 3600.0
DISPATCH_MARKER = "task01-real-dispatch.marker"
LOCAL_MARKER = "run-real.started"

EXIT = {"PREPARED": 0, "COLLECTED": 0, "REFUSED": 2, "NEEDS_SETUP": 4, "FROZEN": 5, "TIMEOUT_UNKNOWN": 6,
        "DISPATCH_UNKNOWN": 6, "LAUNCH_FAILED_RECORDED": 7, "DISPATCHED_INCOMPLETE": 8,
        "RESULT_NOT_RECORDED": 9, "INTERNAL_ERROR": 9,
        "CANDIDATE_COLLECTED": 0, "CANDIDATE_PREPARE_ARCHIVE": 10, "CANDIDATE_FAILURE_ARCHIVE": 11}

#: Fields of a result that may be shown on the terminal (everything else stays in the local file).
TERMINAL_FIELDS = ("schema", "mode", "outcome", "stage", "dispatched", "run_id", "state", "declared_status",
                   "mechanical", "check_exit", "host_versions", "error_class", "context_id", "context_sha256",
                   "context", "summary", "semantic_review", "task_outcome", "at", "kind", "verified", "pushed",
                   "branch", "commit", "parents", "files", "push_command", "version_or_auth_verified")


class Refused(Exception):
    pass


# ---------------------------------------------------------------- helpers
def sha256_bytes(b: bytes) -> str:
    return hashlib.sha256(b).hexdigest()


def sha256_file(path: str) -> Tuple[int, str]:
    with open(path, "rb") as fh:
        data = fh.read()
    return len(data), sha256_bytes(data)


def now() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%S%z")


def canon(obj: Any) -> bytes:
    return (json.dumps(obj, ensure_ascii=False, indent=1, sort_keys=True) + "\n").encode("utf-8")


def write_new(path: str, data: bytes) -> None:
    with open(path, "xb") as fh:
        fh.write(data)
    with open(path, "rb") as fh:
        if fh.read() != data:
            raise OSError("bytes on disk differ after write")


def min_env(**extra: str) -> Dict[str, str]:
    env = {k: os.environ[k] for k in MIN_ENV_NAMES if k in os.environ}
    env["GIT_TERMINAL_PROMPT"] = "0"
    env.update(extra)
    return env


def scrub_environ() -> None:
    """Prepare: drop every variable outside MIN_ENV_NAMES from this process (children inherit
    only the rest). Values are never read; names are only compared."""
    for name in list(os.environ):
        if name not in MIN_ENV_NAMES:
            del os.environ[name]


def git(repo: str, *args: str, env: Optional[Dict[str, str]] = None) -> str:
    done = subprocess.run(["git", "-c", "core.autocrlf=false", "-c", "core.quotepath=false", "-C", repo, *args],
                          capture_output=True, check=True, env=env if env is not None else min_env())
    return done.stdout.decode("utf-8", "replace").strip()


def endpoint_problems(value: Any) -> List[str]:
    if not value:
        return ["endpoint missing: give the API destination explicitly (no default endpoint is used)"]
    if not isinstance(value, str):
        return ["endpoint is not text"]
    if any(c.isspace() or ord(c) < 32 for c in value):
        return ["endpoint contains whitespace or control characters"]
    try:
        parts = urlsplit(value)
        host = parts.hostname
    except ValueError:
        return ["endpoint is not a URL"]
    out = []
    if parts.scheme not in ("https", "http"):
        out.append("endpoint scheme must be https or http")
    if not host:
        out.append("endpoint has no host")
    if "@" in parts.netloc:
        out.append("endpoint carries user information (credentials); refused, value not shown")
    if parts.query or "?" in value:
        out.append("endpoint carries a query string; value not shown")
    if parts.fragment or "#" in value:
        out.append("endpoint carries a fragment")
    return out


def model_problems(model: Any) -> List[str]:
    if not model:
        return ["model missing: an explicit model name is required"]
    if not isinstance(model, str) or not _MODEL.match(model):
        return ["model name has unexpected characters or type"]
    if model.lower().startswith("auto") or model.lower() in MODEL_ALIASES:
        return ["model is a host alias / router, not an explicit model"]
    return []


def key_name_problems(name: Any) -> List[str]:
    if not name:
        return ["key variable name missing"]
    if name not in VERIFIED_KEY_ENV_NAMES:
        return [f"key variable name is not the one verified for CLI 0.65.0 ({', '.join(VERIFIED_KEY_ENV_NAMES)})"]
    return []


def proxy_name_problems(names: Any) -> List[str]:
    if not isinstance(names, list) or not all(isinstance(n, str) for n in names):
        return ["proxy variable names have the wrong shape"]
    if any(n not in PROXY_ENV_NAMES for n in names) or len(set(names)) != len(names):
        return [f"proxy variable names must be distinct names from {list(PROXY_ENV_NAMES)}"]
    return []


def pinned_file_problems(repo: str) -> List[str]:
    out = []
    for rel, digest in PINNED_FILES.items():
        try:
            _, got = sha256_file(os.path.join(repo, *rel.split("/")))
        except OSError as exc:
            out.append(f"{rel}: unreadable ({type(exc).__name__})")
            continue
        if got != digest:
            out.append(f"{rel}: bytes differ from the accepted file")
    return out


def repo_identity(repo: str) -> Dict[str, Any]:
    head = git(repo, "rev-parse", "HEAD")
    status = git(repo, "status", "--porcelain", "--ignored", "--untracked-files=all")
    try:
        git(repo, "merge-base", "--is-ancestor", BASELINE_COMMIT, head)
        descends = True
    except subprocess.CalledProcessError:
        descends = False
    return {"head": head, "clean": status == "", "descends_from_baseline": descends}


def derive(work_root: str) -> Dict[str, str]:
    """The ONE supported layout. Raises Refused for anything else."""
    work_root = os.path.abspath(work_root)
    m = _CTX_DIR.match(os.path.basename(work_root))
    if not m:
        raise Refused("work root must be <parent>/ctx-YYYYMMDD-HHMMSS")
    parent = os.path.dirname(work_root)
    d = {"work_root": work_root, "parent": parent, "stamp": m.group(1),
         "repo": os.path.join(parent, "repo-" + m.group(1)),
         "workspace": os.path.join(work_root, "target"),
         "export_manifest": os.path.join(work_root, "export-manifest.json"),
         "evidence_root": os.path.join(work_root, "evidence"),
         "cli_home": os.path.join(work_root, "cli-home"),
         "context": os.path.join(work_root, "context.json"),
         "result": os.path.join(work_root, "run-real-result.json"),
         "local_marker": os.path.join(work_root, LOCAL_MARKER),
         "dispatch_marker": os.path.join(parent, DISPATCH_MARKER)}
    if os.path.realpath(d["repo"]) != os.path.realpath(ROOT):
        raise Refused("this driver is not the repo of this work root (<parent>/repo-<same stamp>)")
    return d


_STATES = frozenset({"unknown", "queued", "accepted", "running", "declared_complete", "failed",
                      "cancel_requested", "cancelled"})
_DECLARED = frozenset({"success", "unreported", "unattributed"})
_MECH = frozenset({"all_pass", "fail", "undetermined"})
_CHECK_STATUS = frozenset({"pass", "fail", "unknown"})


def _exc_name(name: Any) -> Any:
    import builtins
    if name is None:
        return None
    known = isinstance(name, str) and (isinstance(getattr(builtins, name, None), type)
                                       or isinstance(gem.__dict__.get(name), type))
    return name if known else "other"


def public_host_fields(src: Dict[str, Any]) -> Dict[str, Any]:
    """Host / checker derived values -> known enums or fixed labels. Never the raw value."""
    out: Dict[str, Any] = {}
    if "state" in src:
        out["state"] = src["state"] if src["state"] in _STATES else "unrecognized"
    if "declared_status" in src:
        v = src["declared_status"]
        out["declared_status"] = v if v in _DECLARED else ("host-other" if v is not None else None)
    if "mechanical" in src:
        out["mechanical"] = src["mechanical"] if src["mechanical"] in _MECH else "unrecognized"
    if "check_exit" in src:
        out["check_exit"] = src["check_exit"] if src["check_exit"] in (0, 1, 3) else "unrecognized"
    if "host_versions" in src:
        hv = src["host_versions"] if isinstance(src["host_versions"], dict) else {}
        node, cli = hv.get("node"), hv.get("cli")
        out["host_versions"] = {"node": PINNED_NODE_VERSION if node == PINNED_NODE_VERSION else
                                ("absent" if not node else "mismatch"),
                                "cli": gem.PINNED_CLI_VERSION if gem.version_is_pinned(cli) else
                                ("absent" if not cli else "mismatch")}
    if "error_class" in src:
        out["error_class"] = _exc_name(src["error_class"])
    return out


def terminal_view(result: Dict[str, Any]) -> Dict[str, Any]:
    view = {k: result[k] for k in TERMINAL_FIELDS if k in result}
    view.update(public_host_fields(result))
    return view


def emit(result: Dict[str, Any], path: Optional[str]) -> int:
    code = EXIT[result["outcome"]]
    if result["outcome"] == "COLLECTED" and isinstance(result.get("check_exit"), int):
        code = result["check_exit"]
    if path:
        try:
            write_new(path, canon(result))
        except OSError as exc:
            sys.stdout.write(canon({"schema": RESULT_SCHEMA, "outcome": "RESULT_NOT_RECORDED",
                                    "observed_outcome": result["outcome"], "error_class": type(exc).__name__}
                                   ).decode("utf-8"))
            print(f"HARK24 RESULT_NOT_RECORDED (observed {result['outcome']}); non-passing", file=sys.stderr)
            return EXIT["RESULT_NOT_RECORDED"]
    sys.stdout.write(canon(terminal_view(result)).decode("utf-8"))
    print(f"HARK24 {result['outcome']}: {result.get('summary', '')}", file=sys.stderr)
    return code


# ---------------------------------------------------------------- prepare
def prepare(args: argparse.Namespace) -> int:
    scrub_environ()                              # before any child process or lookup
    base = {"schema": RESULT_SCHEMA, "mode": "prepare", "dispatched": False, "at": now()}
    try:
        d = derive(args.work_root)
    except Refused as exc:
        base.update(outcome="REFUSED", summary=str(exc))
        return emit(base, None)
    if os.path.lexists(d["work_root"]):
        base.update(outcome="REFUSED", summary="work root already exists; prepare never reuses or overwrites one")
        return emit(base, None)
    try:
        ident = repo_identity(ROOT)
    except (OSError, subprocess.CalledProcessError) as exc:
        base.update(outcome="REFUSED", summary=f"repo identity unavailable: {type(exc).__name__}")
        return emit(base, None)
    problems = []
    if not args.entry_commit or ident["head"] != args.entry_commit:
        problems.append("repo HEAD is not the given entry commit")
    if not ident["clean"]:
        problems.append("repo checkout is not clean (ignored files included)")
    if not ident["descends_from_baseline"]:
        problems.append("repo HEAD does not descend from the accepted baseline")
    problems += pinned_file_problems(ROOT)
    if problems:
        base.update(outcome="REFUSED", summary="; ".join(problems))
        return emit(base, None)

    os.makedirs(d["work_root"])
    with open(PACKAGE_PATH, "rb") as fh:
        package = json.loads(fh.read().decode("utf-8"))
    try:
        exported = export_source.export(ROOT, d["workspace"], d["export_manifest"], package)
    except SystemExit as exc:
        base.update(outcome="REFUSED", summary=f"export refused: {exc}")
        return emit(base, os.path.join(d["work_root"], "prepare-result.json"))
    os.makedirs(d["cli_home"])
    missing: List[str] = []
    missing += endpoint_problems(args.endpoint)
    missing += model_problems(args.model)
    missing += key_name_problems(args.key_env_name)
    missing += proxy_name_problems(list(args.proxy_env_name))
    node = {"path": args.node, "static_exists": bool(args.node) and os.path.isfile(args.node),
            "required_version": PINNED_NODE_VERSION, "version_verified": False}
    cli: Dict[str, Any] = {"js_path": args.cli_js,
                           "static_exists": bool(args.cli_js) and os.path.isfile(args.cli_js),
                           "required_version": gem.PINNED_CLI_VERSION, "version_verified": False}
    if not node["static_exists"]:
        missing.append("node path missing or not a file")
    if cli["static_exists"]:
        cli["bytes"], cli["sha256"] = sha256_file(args.cli_js)
        cli["sha256_equals_known_0650_bundle"] = cli["sha256"] == KNOWN_0650_BUNDLE_SHA256
    else:
        missing.append("gemini.js path missing or not a file")
    missing += [f"host preflight: {p}" for p in gem.workspace_preflight(d["workspace"], d["evidence_root"],
                                                                         d["cli_home"])]
    with open(TASK_PATH, "rb") as fh:
        task_bytes = fh.read()
    ep_ok = bool(args.endpoint) and not endpoint_problems(args.endpoint)
    ep = urlsplit(args.endpoint) if ep_ok else None
    context_id = uuid.uuid4().hex[:12]
    context = {
        "schema": SCHEMA, "context_id": context_id, "created_at": now(),
        "status": "NEEDS_SETUP" if missing else "PREPARED_NOT_ADMITTED", "missing": missing,
        "entry_commit": ident["head"], "baseline_commit": BASELINE_COMMIT,
        "repo": d["repo"], "work_root": d["work_root"], "workspace": d["workspace"],
        "export_manifest": d["export_manifest"], "export_manifest_sha256": exported["manifest_sha256"],
        "export_files": exported["files"], "package_sha256": pins.PACKAGE_SHA256, "task_id": pins.TASK_ID,
        "task_sha256": sha256_bytes(task_bytes), "model": args.model,
        "endpoint": {"env_name": ENDPOINT_ENV, "value": args.endpoint if ep_ok else None,
                     "scheme": ep.scheme if ep else None, "host": ep.hostname if ep else None,
                     "protocol_required": "Gemini native generateContent (v1beta streamGenerateContent)",
                     "protocol_verified": False},
        "auth_type": AUTH_TYPE, "key_env_name": args.key_env_name, "key_value_checked": False,
        "proxy_env_names": list(args.proxy_env_name), "node": node, "cli": cli,
        "evidence_root": d["evidence_root"], "cli_home": d["cli_home"],
        "idempotency_key": f"hark24-task01-{context_id}", "attempt": 1,
        "dispatch_marker": d["dispatch_marker"],
        "limits": {"submits": 1, "maxSessionTurns": 40, "hard_cost_cap": None,
                   "note": "one submit is not one HTTP request; maxSessionTurns is not a cost cap"},
    }
    data = canon(context)
    write_new(d["context"], data)
    base.update(outcome="NEEDS_SETUP" if missing else "PREPARED", context=d["context"],
                context_sha256=sha256_bytes(data), context_id=context_id, version_or_auth_verified=False,
                summary=("not dispatched; fix: " + "; ".join(missing)) if missing else
                "prepared, not dispatched; real run needs architect admission, this context sha256 and the "
                "user's cost acceptance")
    return emit(base, os.path.join(d["work_root"], "prepare-result.json"))


# ---------------------------------------------------------------- binding checks
_OPT = (str, type(None))
_CTX_TYPES = {"schema": str, "context_id": str, "status": str, "missing": list, "entry_commit": str,
              "repo": str, "work_root": str, "workspace": str, "export_manifest": str,
              "export_manifest_sha256": str, "task_id": str, "task_sha256": str, "model": _OPT,
              "endpoint": dict, "auth_type": str, "key_env_name": _OPT, "proxy_env_names": list,
              "node": dict, "cli": dict, "evidence_root": str, "cli_home": str, "idempotency_key": str,
              "attempt": int, "dispatch_marker": str}


def context_problems(ctx: Any, d: Dict[str, str]) -> List[str]:
    if not isinstance(ctx, dict):
        return ["context is not a JSON object"]
    out = []
    for k, t in _CTX_TYPES.items():
        v = ctx.get(k)
        if not isinstance(v, t) or isinstance(v, bool):
            out.append(f"context field {k} missing or of the wrong type")
    if out:
        return out
    if ctx["schema"] != SCHEMA:
        out.append("not a hark24 v2 run context")
    for k in ("repo", "work_root", "workspace", "export_manifest", "evidence_root", "cli_home", "dispatch_marker"):
        if ctx[k] != d[k]:
            out.append(f"context field {k} is not the derived path")
    if not _HEX12.match(ctx["context_id"]) or ctx["idempotency_key"] != f"hark24-task01-{ctx['context_id']}":
        out.append("context id / idempotency key not in the supported format")
    if not _HEX40.match(ctx["entry_commit"]) or not _HEX64.match(ctx["export_manifest_sha256"]):
        out.append("commit / manifest digest not in the supported format")
    if ctx["attempt"] != 1 or ctx["task_id"] != pins.TASK_ID or ctx["task_sha256"] != pins.TASK_SHA256:
        out.append("attempt / task binding is not the pinned task")
    ep, node, cli = ctx["endpoint"], ctx["node"], ctx["cli"]
    if ep.get("env_name") != ENDPOINT_ENV or ctx["auth_type"] != AUTH_TYPE:
        out.append("endpoint variable / auth type not the verified ones")
    if not isinstance(node.get("path"), _OPT) or not isinstance(cli.get("js_path"), _OPT):
        out.append("node / gemini.js path has the wrong type")
    return out


def manifest_problems(d: Dict[str, str], want_sha: str) -> List[str]:
    """Digest first, then shape; only then are export files opened - by pinned names only."""
    with open(d["export_manifest"], "rb") as fh:
        data = fh.read()
    if sha256_bytes(data) != want_sha:
        return ["export manifest changed"]
    try:
        manifest = json.loads(data.decode("utf-8"))
    except (UnicodeDecodeError, ValueError):
        return ["export manifest is not JSON"]
    files = manifest.get("files") if isinstance(manifest, dict) else None
    if not isinstance(files, dict) or set(files) != set(pins.SOURCE_FILES):
        return ["export manifest file set is not the pinned src/ set"]
    for rel, meta in files.items():
        if not isinstance(meta, dict) or meta.get("sha256") != pins.SOURCE_FILES[rel][2] \
                or meta.get("size") != pins.SOURCE_FILES[rel][1]:
            return [f"export manifest entry {rel} is not the pinned file"]
    problems, found = export_tree(d["workspace"], d["work_root"])
    if problems:
        return problems                                   # stop: nothing under the export is read
    if found != set(pins.SOURCE_FILES):
        return ["export file set differs from the pinned set"]
    out = []
    for rel, (_, size, digest) in pins.SOURCE_FILES.items():
        got = sha256_file(os.path.join(d["workspace"], *rel.split("/")))
        if got != (size, digest):
            out.append(f"{rel}: bytes differ")
    return out


def export_tree(workspace: str, work_root: str) -> Tuple[List[str], set]:
    """Enumerate the export WITHOUT reading any content: the root and every directory / file must
    be a plain directory / regular file at its own place (no symlink, junction or other reparse
    point; realpath equal to the joined path). Anything not determinable stops the check."""
    import stat as _st
    found: set = set()
    try:
        expect_root = os.path.join(os.path.realpath(work_root), "target")
        st = os.lstat(workspace)
        if _redirected(st) or not _st.S_ISDIR(st.st_mode) or os.path.realpath(workspace) != expect_root:
            return ["export root is redirected or not a plain directory"], found
        stack = [("", expect_root)]
        while stack:
            rel_dir, real_dir = stack.pop()
            for name in sorted(os.listdir(os.path.join(workspace, *rel_dir.split("/")) if rel_dir else workspace)):
                rel = f"{rel_dir}/{name}" if rel_dir else name
                path = os.path.join(workspace, *rel.split("/"))
                st = os.lstat(path)
                if _redirected(st) or os.path.realpath(path) != os.path.join(real_dir, name):
                    return [f"export path {rel} is redirected (link / junction / reparse point)"], found
                if _st.S_ISDIR(st.st_mode):
                    stack.append((rel, os.path.join(real_dir, name)))
                elif _st.S_ISREG(st.st_mode):
                    found.add(rel)
                else:
                    return [f"export path {rel} is not a regular file"], found
    except OSError as exc:
        return [f"export not enumerable: {type(exc).__name__}"], found
    return [], found


def _registry_view(evidence_root: str) -> Tuple[str, List[str]]:
    """('missing'|'unreadable'|'invalid'|'ok', events). Missing or bad evidence is NOT 'no dispatch'."""
    path = os.path.join(evidence_root, "registry.jsonl")
    if not os.path.lexists(path):
        return "missing", []
    try:
        with open(path, "rb") as fh:
            records = [json.loads(l) for l in fh.read().decode("utf-8").splitlines() if l.strip()]
    except (OSError, UnicodeDecodeError, ValueError):
        return "unreadable", []
    if gem.validate_registry_records(records, evidence_root):
        return "invalid", []
    return "ok", [r["event"] for r in records]


# ---------------------------------------------------------------- run-real
def run_real(args: argparse.Namespace) -> int:
    base: Dict[str, Any] = {"schema": RESULT_SCHEMA, "mode": "run-real", "dispatched": False, "at": now(),
                            "stage": "admission", "task_outcome": "not decided by this entry",
                            "semantic_review": "required"}
    if not args.authorize_real_model_call or args.accept_boundary != ACCEPT_PHRASE:
        base.update(outcome="REFUSED", summary="run-real needs --authorize-real-model-call and the exact "
                    "--accept-boundary phrase; nothing started")
        return emit(base, None)
    if not _HEX64.match(args.context_sha256 or ""):
        base.update(outcome="REFUSED", summary="--context-sha256 must be the reviewed 64-hex context digest")
        return emit(base, None)
    try:
        timeout = float(args.collect_timeout)
    except ValueError:
        timeout = -1.0
    if not 0 < timeout <= COLLECT_TIMEOUT_MAX:
        base.update(outcome="REFUSED", summary=f"--collect-timeout must be in (0, {COLLECT_TIMEOUT_MAX}]")
        return emit(base, None)
    # ---- binding: derived layout, reviewed digest, shapes (before any other file is opened)
    try:
        path = os.path.abspath(args.context)
        if os.path.basename(path) != "context.json":
            raise Refused("context must be <work root>/context.json")
        d = derive(os.path.dirname(path))
        with open(d["context"], "rb") as fh:
            data = fh.read()
        if sha256_bytes(data) != args.context_sha256:
            raise Refused("context bytes are not the reviewed context (sha256 differs)")
        try:
            ctx = json.loads(data.decode("utf-8"))
        except (UnicodeDecodeError, ValueError):
            raise Refused("context is not JSON") from None
        problems = context_problems(ctx, d)
        if problems:
            raise Refused("; ".join(problems))
    except (Refused, OSError) as exc:
        base.update(outcome="REFUSED", summary=f"context not usable: {exc}" if isinstance(exc, Refused)
                    else f"context not readable: {type(exc).__name__}")
        return emit(base, None)
    base.update(context_id=ctx["context_id"], context_sha256=args.context_sha256)
    if any(os.path.lexists(p) for p in (d["dispatch_marker"], d["local_marker"], d["evidence_root"], d["result"])):
        base.update(outcome="FROZEN", summary="a real dispatch was already started in this parent/context; "
                    "frozen: no re-dispatch, retry, new key, attempt or root")
        return emit(base, None)
    if ctx["status"] != "PREPARED_NOT_ADMITTED":
        base.update(outcome="NEEDS_SETUP", summary="context is not prepared (status " + ctx["status"][:40] + ")")
        return emit(base, None)
    problems = []
    try:
        ident = repo_identity(ROOT)
        if ident["head"] != ctx["entry_commit"] or not ident["clean"]:
            problems.append("repo HEAD/status is not the prepared, clean entry commit")
    except (OSError, subprocess.CalledProcessError):
        problems.append("repo identity unavailable")
    problems += pinned_file_problems(ROOT)
    if not problems:
        try:
            problems += manifest_problems(d, ctx["export_manifest_sha256"])
        except OSError as exc:
            problems.append(f"export not verifiable: {type(exc).__name__}")
    with open(TASK_PATH, "rb") as fh:
        task_bytes = fh.read()
    if sha256_bytes(task_bytes) != pins.TASK_SHA256:
        problems.append("task bytes are not the pinned task")
    problems += endpoint_problems(ctx["endpoint"].get("value"))
    problems += model_problems(ctx["model"])
    problems += key_name_problems(ctx["key_env_name"])
    problems += proxy_name_problems(ctx["proxy_env_names"])
    if problems:
        base.update(outcome="REFUSED", summary="; ".join(problems))
        return emit(base, None)
    # ---- the user's environment (presence only; values never printed or stored)
    setup: List[str] = []
    key_name = ctx["key_env_name"]
    if not os.environ.get(key_name, "").strip():
        setup.append(f"{key_name} is not set in this process")
    for name in ctx["proxy_env_names"]:
        val = os.environ.get(name)
        if not val:
            setup.append(f"{name} is not set in this process")
        elif name != "NO_PROXY" and endpoint_problems(val):
            setup.append(f"{name} value is not an acceptable proxy URL (value not shown)")
    if setup:
        base.update(outcome="NEEDS_SETUP", summary="; ".join(setup))
        return emit(base, None)
    config = gem.GeminiCliConfig(
        argv_prefix=(ctx["node"]["path"], ctx["cli"]["js_path"]), cli_home=d["cli_home"],
        evidence_root=d["evidence_root"], env_names=(key_name,) + tuple(ctx["proxy_env_names"]),
        extra_env=((ENDPOINT_ENV, ctx["endpoint"]["value"]),), auth_type=AUTH_TYPE)
    # ---- host versions (explicit run-real only; the CLI probe is the adapter's existing one)
    try:
        node_v = subprocess.run([ctx["node"]["path"], "--version"], env=min_env(), capture_output=True,
                                timeout=30).stdout.decode("utf-8", "replace").strip()
        cli_v = gem.default_version_probe(config)
    except (OSError, subprocess.SubprocessError) as exc:
        base.update(outcome="REFUSED", summary="host version probe failed", error_class=type(exc).__name__)
        return emit(base, None)
    base["host_versions"] = {"node": node_v[:40], "cli": cli_v[:40]}
    if node_v != PINNED_NODE_VERSION or not gem.version_is_pinned(cli_v):
        base.update(outcome="REFUSED", summary=f"host is not Node {PINNED_NODE_VERSION} + CLI "
                    f"{gem.PINNED_CLI_VERSION}; no implicit version change")
        return emit(base, None)
    # ---- single-dispatch markers (never removed by this entry)
    try:
        write_new(d["dispatch_marker"], canon({"context_id": ctx["context_id"], "at": now(), "task_id": pins.TASK_ID}))
    except FileExistsError:
        base.update(outcome="FROZEN", summary="dispatch marker appeared; another entry is running or ran")
        return emit(base, None)
    except OSError as exc:
        base.update(outcome="REFUSED", summary="dispatch marker not writable; nothing dispatched",
                    error_class=type(exc).__name__)
        return emit(base, None)
    try:
        write_new(d["local_marker"], canon({"context_id": ctx["context_id"], "at": now()}))
    except OSError as exc:
        base.update(outcome="REFUSED", summary="local marker not writable; nothing dispatched; parent frozen",
                    error_class=type(exc).__name__)
        return emit(base, None)
    return _dispatch(base, ctx, d, config, task_bytes, timeout)


def _dispatch(base, ctx, d, config, task_bytes, timeout) -> int:
    base["stage"] = "submit"
    address = SessionAddress(app_kind="gemini-cli", instance_id="gemini-cli/local", session_id="new",
                             model_id=ctx["model"])
    request = SubmitRequest(task_id=pins.TASK_ID, attempt=1, session=address,
                            instructions=task_bytes.decode("utf-8"), workspace=d["workspace"],
                            idempotency_key=ctx["idempotency_key"])
    try:
        adapter = gem.GeminiCliAgentSessionAdapter(config)        # the existing adapter, default launcher
        status = adapter.submit(request)
    except Exception as exc:  # noqa: BLE001 - classified from what is observable, never from absence
        try:
            view, events = _registry_view(d["evidence_root"])
        except Exception:  # noqa: BLE001
            view, events = "unreadable", []
        if view == "ok" and events == ["reserved", "dispatch_failed"]:
            base.update(outcome="LAUNCH_FAILED_RECORDED", dispatched=False,
                        summary="the adapter recorded a launch failure for the only reservation; frozen")
        else:
            base.update(outcome="DISPATCH_UNKNOWN", dispatched=None,
                        summary=f"submit raised; registry {view}: a dispatch cannot be ruled out; frozen, "
                        "not re-sent")
        base["error_class"] = type(exc).__name__
        return emit(base, d["result"])
    base.update(dispatched=True, run_id=status.run_id, submit_state=status.state)
    try:
        base["stage"] = "collect"
        collected = adapter.collect(address, status.run_id, timeout=timeout)
        base.update(declared_status=collected.declared_status, unverified=list(collected.unverified))
        base["stage"] = "status"
        after = adapter.status(address, status.run_id)
        base.update(state=after.state, native_state=after.native_state)
        if collected.declared_status == "unreported":
            base.update(outcome="TIMEOUT_UNKNOWN",
                        summary=f"not collected within {timeout}s; the host process may still be running and "
                        "billing is not proven stopped; frozen, not killed, not re-sent")
            return emit(base, d["result"])
        base["stage"] = "check"
        report = check_run.run_checks(PACKAGE_PATH, d["export_manifest"], d["workspace"], d["evidence_root"],
                                      status.run_id)
        mech = report["mechanical"]["overall"]
        base["stage"] = "check-write"
        write_new(os.path.join(d["work_root"], "check-report.json"), canon(report))
        base["stage"] = "digests"
        write_new(os.path.join(d["work_root"], "evidence-digests.json"),
                  canon({"files": _digests(d["evidence_root"]),
                         "note": "raw evidence stays on this machine; digests only"}))
    except Exception as exc:  # noqa: BLE001 - frozen, finite, non-passing; message not echoed
        base.update(outcome="DISPATCHED_INCOMPLETE", error_class=type(exc).__name__,
                    summary=f"dispatched; failed at stage {base['stage']}; frozen: no re-dispatch, kill or repair")
        return emit(base, d["result"])
    base.update(outcome="COLLECTED", stage="done", mechanical=mech, check_exit=check_run.EXIT[mech],
                summary=f"collected; mechanical {mech if mech in _MECH else 'unrecognized'}; semantic review required; "
                "this is not a task verdict")
    return emit(base, d["result"])        # exit = mechanical 0/1/3, never a task verdict


def _digests(evidence_root: str) -> Dict[str, Any]:
    out = {}
    for dirpath, _, filenames in os.walk(evidence_root, followlinks=False):
        for f in sorted(filenames):
            p = os.path.join(dirpath, f)
            size, digest = sha256_file(p)
            out[os.path.relpath(p, evidence_root).replace(os.sep, "/")] = {"bytes": size, "sha256": digest}
    return out


# ---------------------------------------------------------------- public candidate
_SAFE = {
    "outcome": re.compile(r"^[A-Z_]{1,40}$"), "stage": re.compile(r"^[a-z-]{1,20}$"),
    "run_id": re.compile(r"^gemini-run-[0-9a-f]{12}$"), "state": re.compile(r"^[a-z_]{1,30}$"),
    "declared_status": re.compile(r"^[a-z_]{1,30}$"), "mechanical": re.compile(r"^[a-z_]{1,20}$"),
    "error_class": re.compile(r"^[A-Za-z_][A-Za-z0-9_]{0,63}$"), "version": re.compile(r"^v?\d{1,3}\.\d{1,3}\.\d{1,3}$"),
    "check": re.compile(r"^[a-z0-9_.:-]{1,80}$"), "status": re.compile(r"^[a-z_]{1,20}$"),
    "relpath": re.compile(r"^[A-Za-z0-9._/-]{1,200}$"), "host": re.compile(r"^[A-Za-z0-9.-]{1,253}$"),
}
WITHHELD = "<withheld>"
_STAGES = frozenset({"admission", "submit", "collect", "status", "check", "check-write", "digests", "done"})
#: Check names B's checker writes (fixed by B's code); anything else is only counted.
_CHECK_NAMES = frozenset((
    "adapter.gates binding.model binding.request binding.run binding.stdin boundary chat.record "
    "checker.input_shape evidence.files export.manifest export.manifest_files export.manifest_pins export.root "
    "export.scan export.src_anchor export.target_blob export.tree native.errors native.host_preamble "
    "native.identity native.model native.status native.usage package.anchor package.pins package.read "
    "package.shape refs.exist refs.ranges registry.legal registry.parse registry.read registry.run_id "
    "required.tools tools.content workspace.manifest package.task_file package.review_baseline_file "
    "workspace.workspace-before.json workspace.workspace-after.json").split()
    + [f"refs.{p}" for p in check_run.CORE_POINTS] + [f"evidence.{n}" for n in check_run.EVIDENCE_NAMES])
#: Per-call check names carry the HOST's tool name; only the two verified tools are shown.
_TOOL_CHECK = re.compile(r"^tools\.(read_file|grep_search)__[0-9]{1,3}$")
_EVIDENCE_NAMES = frozenset(("registry.jsonl", "registry.lock", "runs", "stdout.jsonl", "stderr.txt", "admin-policy.toml",
                             "host-chat-record.jsonl", "workspace-before.json", "workspace-after.json"))


def _evidence_label(rel: Any, n: int) -> str:
    parts = rel.split("/") if isinstance(rel, str) else []
    if parts and all(p in _EVIDENCE_NAMES or _SAFE["run_id"].match(p) for p in parts):
        return rel
    return f"file-{n:02d}"                       # host-named file: name withheld, digest kept


def _safe(kind: str, value: Any) -> Any:
    if value is None or isinstance(value, bool):
        return value
    if kind == "int":
        return value if isinstance(value, int) else WITHHELD
    if kind in ("hex12", "hex40", "hex64"):
        pat = {"hex12": _HEX12, "hex40": _HEX40, "hex64": _HEX64}[kind]
        return value if isinstance(value, str) and pat.match(value) else WITHHELD
    if kind == "model":
        return value if isinstance(value, str) and _MODEL.match(value) else WITHHELD
    pat = _SAFE[kind]
    return value if isinstance(value, str) and pat.match(value) else WITHHELD


def _load_json(path: str) -> Any:
    with open(path, "rb") as fh:
        return json.loads(fh.read().decode("utf-8"))


def public_summary(d: Dict[str, str], ctx: Dict[str, Any], ctx_sha: str) -> Tuple[str, Dict[str, Any]]:
    """Build the ONE public file from whitelisted fields with fixed formats; free text never."""
    out: Dict[str, Any] = {"schema": CANDIDATE_SCHEMA, "context_id": _safe("hex12", ctx["context_id"]),
                           "context_sha256": _safe("hex64", ctx_sha), "entry_commit": _safe("hex40", ctx["entry_commit"]),
                           "task_id": ctx["task_id"] if ctx["task_id"] == pins.TASK_ID else WITHHELD,
                           "task_sha256": _safe("hex64", ctx["task_sha256"]), "model": _safe("model", ctx["model"]),
                           "endpoint_host": _safe("host", ctx["endpoint"].get("host")),
                           "context_status": ctx["status"] if ctx["status"] in ("NEEDS_SETUP", "PREPARED_NOT_ADMITTED")
                           else WITHHELD,
                           "semantic_review": "required", "task_outcome": "not decided"}
    prep_path = os.path.join(d["work_root"], "prepare-result.json")
    prep = _load_json(prep_path)
    if not isinstance(prep, dict) or prep.get("context_sha256") != ctx_sha:
        raise Refused("prepare-result does not belong to this context")
    out["prepare_outcome"] = _safe("outcome", prep.get("outcome"))
    if not os.path.lexists(d["result"]):
        if os.path.lexists(d["local_marker"]) or os.path.lexists(d["evidence_root"]):
            stage = "failure-archive"
            out["run"] = {"outcome": "NO_RESULT_RECORDED", "dispatched": None}
        else:
            stage = "prepare-archive"
    else:
        res = _load_json(d["result"])
        if not isinstance(res, dict) or res.get("context_id") != ctx["context_id"]:
            raise Refused("run-real-result does not belong to this context")
        mapped = public_host_fields(res)
        out["run"] = {"outcome": res.get("outcome") if res.get("outcome") in EXIT else "unrecognized",
                      "stage": res.get("stage") if res.get("stage") in _STAGES else "unrecognized",
                      "dispatched": res.get("dispatched") if res.get("dispatched") in (True, False, None) else WITHHELD,
                      "run_id": _safe("run_id", res.get("run_id")), "state": mapped.get("state"),
                      "declared_status": mapped.get("declared_status"), "mechanical": mapped.get("mechanical"),
                      "check_exit": mapped.get("check_exit"), "error_class": mapped.get("error_class"),
                      "host_versions": mapped.get("host_versions")}
        report_path = os.path.join(d["work_root"], "check-report.json")
        digest_path = os.path.join(d["work_root"], "evidence-digests.json")
        if res.get("outcome") == "COLLECTED" and os.path.isfile(report_path) and os.path.isfile(digest_path):
            stage = "collected"
            rep = _load_json(report_path)
            sem = rep.get("semantic_review") or {}
            mech = rep.get("mechanical") or {}
            items = [i for i in mech.get("items", []) if isinstance(i, dict)]
            listed = [i for i in items if i.get("check") in _CHECK_NAMES
                      or (isinstance(i.get("check"), str) and _TOOL_CHECK.match(i["check"]))]
            out["check"] = {"overall": mech.get("overall") if mech.get("overall") in _MECH else "unrecognized",
                            "items": [{"check": i["check"], "status": i.get("status") if i.get("status") in
                                       _CHECK_STATUS else "unrecognized"} for i in listed],
                            "unlisted_items": len(items) - len(listed),
                            "semantic_status": "required" if sem.get("status") == "required" else WITHHELD,
                            "points": [{"point": p.get("point"), "location_check": p.get("location_check")
                                        if p.get("location_check") in _CHECK_STATUS else "unrecognized"}
                                       for p in sem.get("points", []) if isinstance(p, dict)
                                       and p.get("point") in check_run.CORE_POINTS],
                            "delivery_text_sha256": _safe("hex64", sem.get("delivery_text_sha256")),
                            "full_report_sha256": sha256_file(report_path)[1],
                            "note": "full report (free text, host messages) stays on this machine"}
            dig = _load_json(digest_path).get("files", {})
            out["evidence_digests"] = {}
            for n, (k, v) in enumerate(sorted(dig.items()), 1):
                if isinstance(v, dict):
                    out["evidence_digests"][_evidence_label(k, n)] = {"bytes": _safe("int", v.get("bytes")),
                                                                      "sha256": _safe("hex64", v.get("sha256"))}
        else:
            stage = "failure-archive"
    out["stage"] = stage
    return stage, out


def candidate(args: argparse.Namespace) -> int:
    res: Dict[str, Any] = {"schema": RESULT_SCHEMA, "mode": "candidate", "kind": "evidence candidate commit",
                           "pushed": False, "verified": False, "at": now(),
                           "task_outcome": "not decided by this entry", "semantic_review": "required"}
    try:
        if not _HEX64.match(args.context_sha256 or ""):
            raise Refused("--context-sha256 must be the reviewed 64-hex context digest")
        path = os.path.abspath(args.context)
        if os.path.basename(path) != "context.json":
            raise Refused("context must be <work root>/context.json")
        d = derive(os.path.dirname(path))
        with open(d["context"], "rb") as fh:
            data = fh.read()
        if sha256_bytes(data) != args.context_sha256:
            raise Refused("context bytes are not the reviewed context")
        try:
            ctx = json.loads(data.decode("utf-8"))
        except (UnicodeDecodeError, ValueError):
            raise Refused("context is not JSON") from None
        problems = context_problems(ctx, d)
        if problems:
            raise Refused("; ".join(problems))
        if git(ROOT, "rev-parse", "HEAD") != ctx["entry_commit"]:
            raise Refused("repo HEAD is not the prepared entry commit")
        stage, summary = public_summary(d, ctx, args.context_sha256)
        payload = canon(summary)
        rel = f"reports/hark24-gemini-task01-run/{ctx['context_id']}/candidate.json"
        branch = f"evidence-hark24-task01-{ctx['context_id']}-{stage}"
        index = os.path.join(d["work_root"], f".candidate-index-{uuid.uuid4().hex[:8]}")
        env = min_env(GIT_INDEX_FILE=index, GIT_AUTHOR_NAME="hark24-entry", GIT_AUTHOR_EMAIL="hark24-entry@invalid",
                      GIT_COMMITTER_NAME="hark24-entry", GIT_COMMITTER_EMAIL="hark24-entry@invalid")
        blob = subprocess.run(["git", "-C", ROOT, "hash-object", "-w", "--stdin"], input=payload, env=env,
                              capture_output=True, check=True).stdout.decode().strip()
        try:
            git(ROOT, "read-tree", ctx["entry_commit"], env=env)
            git(ROOT, "update-index", "--add", "--cacheinfo", f"100644,{blob},{rel}", env=env)
            tree = git(ROOT, "write-tree", env=env)
        finally:
            if os.path.exists(index):
                os.remove(index)
        msg = f"evidence(hark24): task01 {stage} candidate for context {ctx['context_id']} (not pushed)"
        commit = git(ROOT, "commit-tree", tree, "-p", ctx["entry_commit"], "-m", msg, env=env)
        git(ROOT, "update-ref", f"refs/heads/{branch}", commit, "0" * 40)      # create only
        parents = git(ROOT, "rev-list", "--parents", "-n", "1", commit).split()[1:]
        names = git(ROOT, "diff", "--name-only", ctx["entry_commit"], commit).splitlines()
        ok = (git(ROOT, "rev-parse", f"refs/heads/{branch}") == commit and parents == [ctx["entry_commit"]]
              and names == [rel] and git(ROOT, "rev-parse", f"{commit}:{rel}") == export_source.git_blob_id(payload)
              and git(ROOT, "rev-parse", "HEAD") == ctx["entry_commit"]
              and git(ROOT, "status", "--porcelain", "--ignored", "--untracked-files=all") == "")
        try:
            origin = git(ROOT, "remote", "get-url", "origin")
        except subprocess.CalledProcessError:
            origin = None
        outcome = {"collected": "CANDIDATE_COLLECTED", "prepare-archive": "CANDIDATE_PREPARE_ARCHIVE",
                   "failure-archive": "CANDIDATE_FAILURE_ARCHIVE"}[stage]
        res.update(stage=stage, branch=branch, commit=commit, parents=parents, files=names, verified=ok,
                   push_command=(f"git -C \"{ROOT}\" push origin {branch}" if ok and origin == REMOTE else None),
                   outcome=outcome if ok else "REFUSED",
                   summary=(f"{stage} candidate commit verified (not a task verdict); NOT pushed - send the result "
                            "back first" + ("" if origin == REMOTE else "; origin is not the verified remote, no push hint"))
                   if ok else "candidate commit did not verify; not delivered")
    except (Refused, OSError, subprocess.CalledProcessError, ValueError, KeyError, TypeError, AttributeError) as exc:
        res.update(outcome="REFUSED", summary=(f"candidate not created/verified: {exc}" if isinstance(exc, Refused)
                                               else f"candidate not created/verified: {type(exc).__name__}"))
    return emit(res, None)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Hark-派单-24/25 task01 entry (prepare / run-real / candidate)")
    sub = ap.add_subparsers(dest="mode", required=True)
    p = sub.add_parser("prepare")
    p.add_argument("--work-root", required=True)
    p.add_argument("--entry-commit", default=None)
    p.add_argument("--node", default=None)
    p.add_argument("--cli-js", default=None)
    p.add_argument("--endpoint", default=None)
    p.add_argument("--model", default=None)
    p.add_argument("--key-env-name", default=None)
    p.add_argument("--proxy-env-name", action="append", default=[])
    r = sub.add_parser("run-real")
    r.add_argument("--context", required=True)
    r.add_argument("--context-sha256", default="")
    r.add_argument("--authorize-real-model-call", action="store_true")
    r.add_argument("--accept-boundary", default="")
    r.add_argument("--collect-timeout", default=str(COLLECT_TIMEOUT_DEFAULT))
    c = sub.add_parser("candidate")
    c.add_argument("--context", required=True)
    c.add_argument("--context-sha256", default="")
    args = ap.parse_args(argv)
    try:
        if args.mode == "prepare":
            return prepare(args)
        if args.mode == "run-real":
            return run_real(args)
        return candidate(args)
    except Exception as exc:  # noqa: BLE001 - stable non-passing outcome, no traceback, no message
        sys.stdout.write(canon({"schema": RESULT_SCHEMA, "mode": args.mode, "outcome": "INTERNAL_ERROR",
                                "error_class": type(exc).__name__}).decode("utf-8"))
        print(f"HARK24 INTERNAL_ERROR ({type(exc).__name__}); non-passing", file=sys.stderr)
        return EXIT["INTERNAL_ERROR"]


if __name__ == "__main__":
    sys.exit(main())
