"""Hark-派单-24: thin user entry for ONE real Gemini CLI task01 run (candidate; not admitted).

Two modes, both driven by scripts/win/hark24-gemini-task01.ps1:

  prepare   (default of the .ps1) - offline only. Exports the fixed src/ (B's exporter,
            unchanged) into a NEW work root, records a non-sensitive run context and
            prints PREPARED / NEEDS_SETUP / REFUSED. It never reads a credential value
            (the key variable is not even looked up), never starts Node, the CLI, a model
            or a proxy, and never dispatches. Static existence of node / gemini.js is
            recorded as static only: version and authentication are NOT verified here.

  run-real  review TEMPLATE. Not to be run before the architect admits it and the user
            accepts the real-call terms. Needs an explicit prepared context, the
            --authorize-real-model-call flag AND the exact --accept-boundary phrase; it is
            never reached from prepare, from a missing setting or from a test mode. It
            verifies Node (exact version) and the CLI (the adapter's own version probe),
            then makes AT MOST ONE submit through the existing
            GeminiCliAgentSessionAdapter, one bounded collect, runs B's passive checker
            (unchanged) and builds a small whitelisted evidence commit candidate (never
            pushed).

Facts this entry relies on (verified samples, not convention):
  * CLI 0.65.0 API-key path: env GEMINI_API_KEY + settings security.auth.selectedType
    "gemini-api-key"; base address from env GOOGLE_GEMINI_BASE_URL; the host then POSTs
    <base>/v1beta/models/<model>:streamGenerateContent?alt=sse (Gemini native protocol)
    - reports/hark-gemini-bridge-20261021/host (real CLI 0.65.0, synthetic endpoint).
    The header that carries the key was not recorded there and is not claimed.
  * A destination that speaks only OpenAI or Anthropic Messages (e.g. the project's
    localhost:8787 proxy of ADR-0002) is NOT this protocol: no translation layer is
    added; such a setup is a blocker, reported, not worked around.
  * Tested host combination: CLI 0.65.0 + Node v22.23.4 (Node 24 exit crash kept).

Boundaries kept: one submit is not one HTTP request; maxSessionTurns 40 is not a request,
token or cost cap; budget_tokens is unsupported - there is NO hard cost limit. A bounded
wait that times out does not prove the process ended or billing stopped. On a dispatched
or unknown run everything is frozen: no new root / key / attempt, no retry, continue,
cancel, kill or registry repair. The checker's mechanical result is never a task verdict
(semantic_review stays required).
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

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(os.path.dirname(HERE))
PKG_DIR = os.path.join(ROOT, "scripts", "gemini_task01")
for _p in (ROOT, PKG_DIR):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import check_run  # noqa: E402  (B, unchanged, read-only use)
import export_source  # noqa: E402  (B, unchanged, read-only use)
import pins  # noqa: E402  (B, unchanged)
from src import agent_session_gemini as gem  # noqa: E402  (A, unchanged)
from src.agent_session import SessionAddress, SubmitRequest  # noqa: E402

SCHEMA = "hark24/gemini-task01/run-context/v1"
RESULT_SCHEMA = "hark24/gemini-task01/entry-result/v1"
BASELINE_COMMIT = "95d941ec1025470dad4fc1d00e9f4775361e3b91"
PACKAGE_PATH = os.path.join(PKG_DIR, "package.json")
TASK_PATH = os.path.join(PKG_DIR, "task.txt")

#: Accepted dependencies, by the sha256 of their bytes at BASELINE_COMMIT.
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
#: sha256 of bundle/gemini.js of @google/gemini-cli@0.65.0 as installed in the Hark-11 host
#: probe (reports/hark-gemini-bridge-20261021/host/cli-install.txt). Recorded as a STATIC
#: comparison only; the real gate is the adapter's own --version probe at run-real.
KNOWN_0650_BUNDLE_SHA256 = "41847af2158ced805c4a3e098a05b9a2c26fb1a49b12583009b2eaf6a4059a59"
ENDPOINT_ENV = "GOOGLE_GEMINI_BASE_URL"
VERIFIED_KEY_ENV_NAMES = ("GEMINI_API_KEY",)
AUTH_TYPE = "gemini-api-key"
PROXY_ENV_NAMES = ("HTTPS_PROXY", "HTTP_PROXY", "NO_PROXY")
MODEL_ALIASES = frozenset({"pro", "flash", "flash-lite", "auto"})
_MODEL = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")
ACCEPT_PHRASE = ("one-submit-is-not-one-request;maxSessionTurns-40-is-not-a-cost-cap;"
                 "no-hard-cost-limit;timeout-does-not-prove-stop")
COLLECT_TIMEOUT_DEFAULT = 900.0
COLLECT_TIMEOUT_MAX = 3600.0
DISPATCH_MARKER = "task01-real-dispatch.marker"      # in the PARENT of the work root
LOCAL_MARKER = "run-real.started"                    # in the work root
CANDIDATE_FILES = ("context.json", "prepare-result.json", "run-real-result.json", "check-report.json",
                   "evidence-digests.json")

EXIT = {"PREPARED": 0, "COLLECTED": 0, "REFUSED": 2, "NEEDS_SETUP": 4, "FROZEN": 5,
        "TIMEOUT_UNKNOWN": 6, "DISPATCH_UNKNOWN": 6, "DISPATCH_REFUSED": 7}


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
    """Create a NEW file (never overwrite) and verify its bytes on disk."""
    with open(path, "xb") as fh:
        fh.write(data)
    with open(path, "rb") as fh:
        if fh.read() != data:
            raise OSError(f"{path}: bytes on disk differ after write")


def git(repo: str, *args: str) -> str:
    done = subprocess.run(["git", "-c", "core.autocrlf=false", "-c", "core.quotepath=false", "-C", repo,
                           *args], capture_output=True, check=True)
    return done.stdout.decode("utf-8", "replace").strip()


def endpoint_problems(value: Optional[str]) -> List[str]:
    """String-only checks of the destination; the value itself is never echoed back."""
    if not value:
        return ["endpoint missing: give the API destination explicitly (no default endpoint is used)"]
    if any(c.isspace() or ord(c) < 32 for c in value):
        return ["endpoint contains whitespace or control characters"]
    try:
        parts = urlsplit(value)
    except ValueError:
        return ["endpoint is not a URL"]
    out = []
    if parts.scheme not in ("https", "http"):
        out.append("endpoint scheme must be https or http")
    if not parts.hostname:
        out.append("endpoint has no host")
    if "@" in parts.netloc:
        out.append("endpoint carries user information (credentials); refused, value not shown")
    if parts.query or "?" in value:
        out.append("endpoint carries a query string (keys are never accepted in the URL); value not shown")
    if parts.fragment or "#" in value:
        out.append("endpoint carries a fragment")
    return out


def model_problems(model: Optional[str]) -> List[str]:
    if not model:
        return ["model missing: an explicit model name is required"]
    if model.lower().startswith("auto") or model.lower() in MODEL_ALIASES:
        return [f"model {model!r} is a host alias / router, not an explicit model"]
    if not _MODEL.match(model):
        return ["model name has unexpected characters"]
    return []


def key_name_problems(name: Optional[str]) -> List[str]:
    if not name:
        return ["key variable name missing"]
    if name not in VERIFIED_KEY_ENV_NAMES:
        return [f"key variable {name!r} is not the name verified for CLI 0.65.0's API-key path "
                f"({', '.join(VERIFIED_KEY_ENV_NAMES)})"]
    return []


def proxy_name_problems(names: List[str]) -> List[str]:
    bad = [n for n in names if n not in PROXY_ENV_NAMES]
    return [f"proxy variable names {bad} not in the whitelist {list(PROXY_ENV_NAMES)}"] if bad else []


def pinned_file_problems(repo: str) -> List[str]:
    out = []
    for rel, digest in PINNED_FILES.items():
        path = os.path.join(repo, *rel.split("/"))
        try:
            _, got = sha256_file(path)
        except OSError as exc:
            out.append(f"{rel}: unreadable ({type(exc).__name__})")
            continue
        if got != digest:
            out.append(f"{rel}: bytes differ from the accepted {digest[:12]}")
    return out


def repo_identity(repo: str) -> Dict[str, Any]:
    head = git(repo, "rev-parse", "HEAD")
    status = git(repo, "status", "--porcelain", "--untracked-files=all")
    try:
        git(repo, "merge-base", "--is-ancestor", BASELINE_COMMIT, head)
        descends = True
    except subprocess.CalledProcessError:
        descends = False
    return {"head": head, "clean": status == "", "descends_from_baseline": descends}


def export_problems(workspace: str, manifest_path: str) -> List[str]:
    """The export on disk is exactly the manifest's file set and bytes (and the pins)."""
    with open(manifest_path, "rb") as fh:
        manifest = json.loads(fh.read().decode("utf-8"))
    files = manifest.get("files") or {}
    out = []
    if set(files) != set(pins.SOURCE_FILES):
        out.append("manifest file set is not the pinned src/ set")
    found = set()
    for dirpath, dirnames, filenames in os.walk(workspace, followlinks=False):
        for d in dirnames:
            if os.path.islink(os.path.join(dirpath, d)):
                out.append("export contains a link")
        for f in filenames:
            rel = os.path.relpath(os.path.join(dirpath, f), workspace).replace(os.sep, "/")
            found.add(rel)
    if found != set(files):
        out.append("export file set differs from its manifest")
    for rel, meta in files.items():
        try:
            size, digest = sha256_file(os.path.join(workspace, *rel.split("/")))
        except OSError:
            out.append(f"{rel}: unreadable")
            continue
        if (size, digest) != (meta.get("size"), meta.get("sha256")) or \
                digest != pins.SOURCE_FILES.get(rel, (None, None, None))[2]:
            out.append(f"{rel}: bytes differ")
    return out


def emit(result: Dict[str, Any], path: Optional[str]) -> int:
    data = canon(result)
    if path:
        write_new(path, data)
    sys.stdout.write(data.decode("utf-8"))
    print(f"HARK24 {result['outcome']}: {result.get('summary', '')}", file=sys.stderr)
    if result["outcome"] == "COLLECTED" and "check_exit" in result:
        return result["check_exit"]          # 0 all_pass / 1 fail / 3 undetermined - never a task verdict
    return EXIT[result["outcome"]]


# ---------------------------------------------------------------- prepare
def prepare(args: argparse.Namespace) -> int:
    work_root = os.path.abspath(args.work_root)
    base = {"schema": RESULT_SCHEMA, "mode": "prepare", "dispatched": False, "real_cli_processes": 0,
            "model_calls": 0, "credential_values_read": False, "at": now()}
    if os.path.lexists(work_root):
        base.update(outcome="REFUSED", summary="work root already exists; prepare never reuses or overwrites one")
        return emit(base, None)
    repo = os.path.abspath(args.repo)
    try:
        ident = repo_identity(repo)
    except (OSError, subprocess.CalledProcessError) as exc:
        base.update(outcome="REFUSED", summary=f"repo identity unavailable: {type(exc).__name__}")
        return emit(base, None)
    problems = []
    if args.entry_commit and ident["head"] != args.entry_commit:
        problems.append("repo HEAD is not the given entry commit")
    if not ident["clean"]:
        problems.append("repo checkout is not clean")
    if not ident["descends_from_baseline"]:
        problems.append("repo HEAD does not descend from the accepted baseline")
    problems += pinned_file_problems(repo)
    if os.path.abspath(repo) != ROOT:
        problems.append("this driver must run from the repo it validates")
    if problems:
        base.update(outcome="REFUSED", summary="; ".join(problems), repo=ident)
        return emit(base, None)

    os.makedirs(work_root)
    workspace = os.path.join(work_root, "target")
    manifest = os.path.join(work_root, "export-manifest.json")
    with open(PACKAGE_PATH, "rb") as fh:
        package = json.loads(fh.read().decode("utf-8"))
    try:
        exported = export_source.export(repo, workspace, manifest, package)
    except SystemExit as exc:
        base.update(outcome="REFUSED", summary=f"export refused: {exc}")
        return emit(base, os.path.join(work_root, "prepare-result.json"))
    cli_home = os.path.join(work_root, "cli-home")
    os.makedirs(cli_home)
    evidence_root = os.path.join(work_root, "evidence")

    missing: List[str] = []
    missing += endpoint_problems(args.endpoint)
    missing += model_problems(args.model)
    missing += key_name_problems(args.key_env_name)
    missing += proxy_name_problems(args.proxy_env_name)
    node = {"path": args.node, "static_exists": bool(args.node) and os.path.isfile(args.node),
            "required_version": PINNED_NODE_VERSION, "version_verified": False}
    cli: Dict[str, Any] = {"js_path": args.cli_js, "static_exists": bool(args.cli_js) and os.path.isfile(args.cli_js),
                           "required_version": gem.PINNED_CLI_VERSION, "version_verified": False}
    if not node["static_exists"]:
        missing.append("node path missing or not a file")
    if cli["static_exists"]:
        cli["bytes"], cli["sha256"] = sha256_file(args.cli_js)
        cli["sha256_equals_known_0650_bundle"] = cli["sha256"] == KNOWN_0650_BUNDLE_SHA256
    else:
        missing.append("gemini.js path missing or not a file")
    host_problems = gem.workspace_preflight(workspace, evidence_root, cli_home)
    missing += [f"host preflight: {p}" for p in host_problems]
    with open(TASK_PATH, "rb") as fh:
        task_bytes = fh.read()
    ep = urlsplit(args.endpoint) if args.endpoint and not endpoint_problems(args.endpoint) else None
    context_id = uuid.uuid4().hex[:12]
    context = {
        "schema": SCHEMA, "context_id": context_id, "created_at": now(),
        "status": "NEEDS_SETUP" if missing else "PREPARED_NOT_ADMITTED",
        "missing": missing,
        "entry_commit": ident["head"], "baseline_commit": BASELINE_COMMIT, "repo": repo,
        "work_root": work_root, "workspace": workspace, "export_manifest": manifest,
        "export_manifest_sha256": exported["manifest_sha256"], "export_files": exported["files"],
        "package_sha256": pins.PACKAGE_SHA256, "task_id": pins.TASK_ID, "task_sha256": sha256_bytes(task_bytes),
        "model": args.model,
        "endpoint": {"env_name": ENDPOINT_ENV, "value": args.endpoint if ep else None,
                     "scheme": ep.scheme if ep else None, "host": ep.hostname if ep else None,
                     "protocol_required": "Gemini native generateContent (v1beta streamGenerateContent)",
                     "protocol_verified": False},
        "auth_type": AUTH_TYPE, "key_env_name": args.key_env_name, "key_value_checked": False,
        "proxy_env_names": list(args.proxy_env_name),
        "node": node, "cli": cli,
        "evidence_root": evidence_root, "cli_home": cli_home,
        "idempotency_key": f"hark24-task01-{context_id}", "attempt": 1,
        "dispatch_marker": os.path.join(os.path.dirname(work_root), DISPATCH_MARKER),
        "limits": {"submits": 1, "maxSessionTurns": 40, "hard_cost_cap": None,
                   "note": "one submit is not one HTTP request; maxSessionTurns is not a cost cap"},
    }
    data = canon(context)
    write_new(os.path.join(work_root, "context.json"), data)
    write_new(os.path.join(work_root, "context.sha256"), (sha256_bytes(data) + "\n").encode("ascii"))
    base.update(outcome="NEEDS_SETUP" if missing else "PREPARED",
                summary=("not dispatched; fix: " + "; ".join(missing)) if missing else
                "prepared, not dispatched; real run needs architect admission and the user's cost acceptance",
                context=os.path.join(work_root, "context.json"), context_sha256=sha256_bytes(data),
                version_or_auth_verified=False)
    return emit(base, os.path.join(work_root, "prepare-result.json"))


# ---------------------------------------------------------------- run-real
def _load_context(path: str) -> Dict[str, Any]:
    with open(path, "rb") as fh:
        data = fh.read()
    side = os.path.join(os.path.dirname(path), "context.sha256")
    with open(side, "rb") as fh:
        want = fh.read().decode("ascii").strip()
    if sha256_bytes(data) != want:
        raise Refused("context.json does not match context.sha256")
    ctx = json.loads(data.decode("utf-8"))
    if ctx.get("schema") != SCHEMA:
        raise Refused("not a hark24 run context")
    if os.path.abspath(path) != os.path.join(ctx["work_root"], "context.json"):
        raise Refused("context path is not the context's own work root")
    return ctx


def _registry_records(evidence_root: str) -> Optional[List[Any]]:
    path = os.path.join(evidence_root, "registry.jsonl")
    if not os.path.exists(path):
        return None
    with open(path, "rb") as fh:
        return [json.loads(l) for l in fh.read().decode("utf-8").splitlines() if l.strip()]


def _digests(evidence_root: str) -> Dict[str, Any]:
    out = {}
    for dirpath, _, filenames in os.walk(evidence_root, followlinks=False):
        for f in sorted(filenames):
            p = os.path.join(dirpath, f)
            size, digest = sha256_file(p)
            out[os.path.relpath(p, evidence_root).replace(os.sep, "/")] = {"bytes": size, "sha256": digest}
    return out


def run_real(args: argparse.Namespace) -> int:
    base: Dict[str, Any] = {"schema": RESULT_SCHEMA, "mode": "run-real", "dispatched": False, "at": now(),
                            "task_outcome": "not decided by this entry", "semantic_review": "required"}
    if not args.authorize_real_model_call or args.accept_boundary != ACCEPT_PHRASE:
        base.update(outcome="REFUSED", summary="run-real needs --authorize-real-model-call and the exact "
                    "--accept-boundary phrase; nothing started")
        return emit(base, None)
    timeout = float(args.collect_timeout)
    if not 0 < timeout <= COLLECT_TIMEOUT_MAX:
        base.update(outcome="REFUSED", summary=f"--collect-timeout must be in (0, {COLLECT_TIMEOUT_MAX}]")
        return emit(base, None)
    try:
        ctx = _load_context(os.path.abspath(args.context))
    except (OSError, ValueError, KeyError, Refused) as exc:
        base.update(outcome="REFUSED", summary=f"context not usable: {exc}")
        return emit(base, None)
    work_root = ctx["work_root"]
    result_path = os.path.join(work_root, "run-real-result.json")
    marker = ctx["dispatch_marker"]
    local_marker = os.path.join(work_root, LOCAL_MARKER)
    base["context_id"] = ctx["context_id"]
    if any(os.path.lexists(p) for p in (marker, local_marker, ctx["evidence_root"], result_path)):
        base.update(outcome="FROZEN", summary="a real dispatch was already started for this task/context; "
                    "frozen: no re-dispatch, retry, new key, attempt or root")
        return emit(base, None)
    if ctx.get("status") != "PREPARED_NOT_ADMITTED":
        base.update(outcome="NEEDS_SETUP", summary="context is not prepared: " + "; ".join(ctx.get("missing", [])))
        return emit(base, None)
    # ---- re-verify the binding (nothing dispatched yet)
    problems: List[str] = []
    try:
        ident = repo_identity(ctx["repo"])
        if ident["head"] != ctx["entry_commit"] or not ident["clean"]:
            problems.append("repo HEAD/status no longer the prepared entry commit")
    except (OSError, subprocess.CalledProcessError):
        problems.append("repo identity unavailable")
    if os.path.abspath(ctx["repo"]) != ROOT:
        problems.append("driver is not running from the prepared repo")
    problems += pinned_file_problems(ctx["repo"])
    try:
        if sha256_file(ctx["export_manifest"])[1] != ctx["export_manifest_sha256"]:
            problems.append("export manifest changed")
        problems += export_problems(ctx["workspace"], ctx["export_manifest"])
    except (OSError, ValueError) as exc:
        problems.append(f"export not verifiable: {type(exc).__name__}")
    with open(TASK_PATH, "rb") as fh:
        task_bytes = fh.read()
    if sha256_bytes(task_bytes) != pins.TASK_SHA256 or ctx.get("task_sha256") != pins.TASK_SHA256:
        problems.append("task bytes are not the pinned task")
    problems += endpoint_problems(ctx["endpoint"]["value"])
    problems += model_problems(ctx.get("model"))
    problems += key_name_problems(ctx.get("key_env_name"))
    problems += proxy_name_problems(ctx.get("proxy_env_names", []))
    if problems:
        base.update(outcome="REFUSED", summary="; ".join(problems))
        return emit(base, None)
    # ---- the user's environment (presence only; values never printed or stored)
    setup: List[str] = []
    key_name = ctx["key_env_name"]
    if not os.environ.get(key_name, "").strip():
        setup.append(f"{key_name} is not set in this process")
    for name in ctx.get("proxy_env_names", []):
        val = os.environ.get(name)
        if not val:
            setup.append(f"{name} is not set in this process")
        elif name != "NO_PROXY" and endpoint_problems(val):
            setup.append(f"{name} value is not an acceptable proxy URL (value not shown)")
    if setup:
        base.update(outcome="NEEDS_SETUP", summary="; ".join(setup))
        return emit(base, None)
    config = gem.GeminiCliConfig(
        argv_prefix=(ctx["node"]["path"], ctx["cli"]["js_path"]), cli_home=ctx["cli_home"],
        evidence_root=ctx["evidence_root"], env_names=(key_name,) + tuple(ctx.get("proxy_env_names", [])),
        extra_env=((ENDPOINT_ENV, ctx["endpoint"]["value"]),), auth_type=AUTH_TYPE)
    # ---- host versions (no model): Node exact, CLI by the adapter's own probe
    try:
        node_env = {k: os.environ[k] for k in ("PATH", "SYSTEMROOT", "SystemRoot") if k in os.environ}
        node_v = subprocess.run([ctx["node"]["path"], "--version"], env=node_env, capture_output=True,
                                timeout=30).stdout.decode("utf-8", "replace").strip()
        cli_v = gem.default_version_probe(config)
    except (OSError, subprocess.SubprocessError) as exc:
        base.update(outcome="REFUSED", summary=f"host version probe failed: {type(exc).__name__}")
        return emit(base, None)
    base["host_versions"] = {"node": node_v, "cli": cli_v}
    if node_v != PINNED_NODE_VERSION or not gem.version_is_pinned(cli_v):
        base.update(outcome="REFUSED", summary=f"host is not Node {PINNED_NODE_VERSION} + CLI "
                    f"{gem.PINNED_CLI_VERSION}; no implicit version change")
        return emit(base, None)
    # ---- single-dispatch markers (never removed by this entry)
    try:
        write_new(marker, canon({"context_id": ctx["context_id"], "at": now(), "task_id": pins.TASK_ID}))
    except FileExistsError:
        base.update(outcome="FROZEN", summary="dispatch marker appeared; another entry is running or ran")
        return emit(base, None)
    write_new(local_marker, canon({"context_id": ctx["context_id"], "at": now()}))
    adapter = gem.GeminiCliAgentSessionAdapter(config)        # the existing adapter, default launcher
    address = SessionAddress(app_kind="gemini-cli", instance_id="gemini-cli/local", session_id="new",
                             model_id=ctx["model"])
    request = SubmitRequest(task_id=pins.TASK_ID, attempt=1, session=address,
                            instructions=task_bytes.decode("utf-8"), workspace=ctx["workspace"],
                            idempotency_key=ctx["idempotency_key"])
    try:
        status = adapter.submit(request)
    except Exception as exc:  # report, never mask; decide from the registry what happened
        try:
            records = _registry_records(ctx["evidence_root"]) or []
        except (OSError, ValueError):
            records = None
        events = [r.get("event") for r in records or [] if isinstance(r, dict)]
        if records is None or "dispatched" in events or ("reserved" in events and "dispatch_failed" not in events):
            outcome, dispatched, why = "DISPATCH_UNKNOWN", None, "registry unreadable or a reservation " \
                "without a recorded launch failure: outcome unknown, frozen"
        elif "dispatch_failed" in events:
            outcome, dispatched, why = "DISPATCH_REFUSED", False, "launch failure recorded by the adapter; frozen"
        else:
            outcome, dispatched, why = "DISPATCH_REFUSED", False, "nothing reserved"
        base.update(outcome=outcome, dispatched=dispatched, summary=f"submit raised {type(exc).__name__}; {why}",
                    error_class=type(exc).__name__)
        return emit(base, result_path)
    base.update(dispatched=True, run_id=status.run_id, submit_state=status.state)
    collected = adapter.collect(address, status.run_id, timeout=timeout)
    after = adapter.status(address, status.run_id)
    base.update(declared_status=collected.declared_status, state=after.state, native_state=after.native_state,
                unverified=list(collected.unverified))
    if collected.declared_status == "unreported":
        base.update(outcome="TIMEOUT_UNKNOWN",
                    summary=f"not collected within {timeout}s; the host process may still be running and "
                    "billing is not proven stopped; frozen, not killed, not re-sent")
        return emit(base, result_path)
    report = check_run.run_checks(PACKAGE_PATH, ctx["export_manifest"], ctx["workspace"], ctx["evidence_root"],
                                  status.run_id)
    write_new(os.path.join(work_root, "check-report.json"), canon(report))
    write_new(os.path.join(work_root, "evidence-digests.json"),
              canon({"evidence_root": ctx["evidence_root"], "files": _digests(ctx["evidence_root"]),
                     "note": "raw evidence stays on this machine; only digests are proposed for commit"}))
    mech = report["mechanical"]["overall"]
    base.update(outcome="COLLECTED", mechanical=mech, check_exit=check_run.EXIT[mech],
                summary=f"collected; mechanical {mech}; semantic review required; this is not a task verdict")
    return emit(base, result_path)


# ---------------------------------------------------------------- evidence candidate
def candidate(args: argparse.Namespace) -> int:
    """Commit the whitelisted small files on a NEW local branch and verify the commit.
    Never pushes. Raw stdout / chat / registry stay on this machine."""
    ctx = _load_context(os.path.abspath(args.context))
    repo, work_root = ctx["repo"], ctx["work_root"]
    rel_dir = f"reports/hark24-gemini-task01-run/{ctx['context_id']}"
    branch = f"evidence-hark24-task01-{ctx['context_id']}"
    res: Dict[str, Any] = {"schema": RESULT_SCHEMA, "mode": "candidate", "pushed": False, "verified": False,
                           "branch": branch, "at": now()}
    try:
        if git(repo, "rev-parse", "HEAD") != ctx["entry_commit"] or git(repo, "status", "--porcelain"):
            raise Refused("repo is not the clean prepared entry commit")
        files = [f for f in CANDIDATE_FILES if os.path.isfile(os.path.join(work_root, f))]
        snap = {}
        for f in files:
            with open(os.path.join(work_root, f), "rb") as fh:
                snap[f] = fh.read()
        secret_names = [ctx.get("key_env_name")] + list(ctx.get("proxy_env_names", []))
        for name in secret_names:
            val = os.environ.get(name or "")
            if val and len(val) >= 8 and any(val.encode("utf-8") in b for b in snap.values()):
                raise Refused(f"a value of {name} appears in a candidate file; nothing committed")
        git(repo, "checkout", "-q", "-b", branch)
        dest = os.path.join(repo, *rel_dir.split("/"))
        os.makedirs(dest)
        for f, data in snap.items():
            write_new(os.path.join(dest, f), data)
        git(repo, "add", "--", rel_dir)
        git(repo, "-c", "user.name=hark24-entry", "-c", "user.email=hark24-entry@invalid", "commit", "-q",
            "-m", f"evidence(hark24): task01 entry context {ctx['context_id']} (candidate, not pushed)")
        commit = git(repo, "rev-parse", "HEAD")
        parents = git(repo, "rev-list", "--parents", "-n", "1", commit).split()[1:]
        names = git(repo, "diff", "--name-only", ctx["entry_commit"], commit).splitlines()
        ok = (git(repo, "rev-parse", f"refs/heads/{branch}") == commit and parents == [ctx["entry_commit"]]
              and sorted(names) == sorted(f"{rel_dir}/{f}" for f in files)
              and all(git(repo, "rev-parse", f"{commit}:{rel_dir}/{f}") == export_source.git_blob_id(d)
                      for f, d in snap.items())
              and git(repo, "status", "--porcelain") == "")
        res.update(commit=commit, parents=parents, files=names, verified=ok,
                   push_command=f"git -C \"{repo}\" push origin {branch}" if ok else None,
                   outcome="COLLECTED" if ok else "REFUSED",
                   summary="candidate commit verified; NOT pushed - send the result back first" if ok
                   else "candidate commit did not verify; not delivered")
    except (Refused, OSError, subprocess.CalledProcessError, ValueError) as exc:
        res.update(outcome="REFUSED", summary=f"candidate not created/verified: {type(exc).__name__}: {exc}")
    return emit(res, None)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Hark-派单-24 task01 entry (prepare / run-real / candidate)")
    sub = ap.add_subparsers(dest="mode", required=True)
    p = sub.add_parser("prepare")
    p.add_argument("--work-root", required=True)
    p.add_argument("--repo", default=ROOT)
    p.add_argument("--entry-commit", default=None)
    p.add_argument("--node", default=None)
    p.add_argument("--cli-js", default=None)
    p.add_argument("--endpoint", default=None)
    p.add_argument("--model", default=None)
    p.add_argument("--key-env-name", default=None)
    p.add_argument("--proxy-env-name", action="append", default=[])
    r = sub.add_parser("run-real")
    r.add_argument("--context", required=True)
    r.add_argument("--authorize-real-model-call", action="store_true")
    r.add_argument("--accept-boundary", default="")
    r.add_argument("--collect-timeout", default=COLLECT_TIMEOUT_DEFAULT)
    c = sub.add_parser("candidate")
    c.add_argument("--context", required=True)
    args = ap.parse_args(argv)
    if args.mode == "prepare":
        return prepare(args)
    if args.mode == "run-real":
        return run_real(args)
    return candidate(args)


if __name__ == "__main__":
    sys.exit(main())
