"""GUI real session probe (Hark-派单-06): one fixed-token round trip, opt-in.

An application service, not a scheduler. It lets the GUI ask for exactly one
thing: after the server has enabled it AND the user has explicitly confirmed,
create a NEW isolated read-only Codex worker session through the existing real
`CodexAgentSessionAdapter`, send a fixed echo instruction carrying a
server-generated token, collect the reply bound to that run's thread and turn,
and show the reply, the usage and the raw evidence.

Boundaries (pinned by tests/test_hark06_session_probe.py):

* Default off. Opening, refreshing or querying never dispatches. A start needs
  the server switch, a server-issued single-use probe id and `confirm: true`.
* The browser supplies nothing but `{probe_id, confirm}`. Model, workspace,
  registry, evidence location and instruction are server-side; any other field
  is refused, so no free task, command, path or existing session can ride in.
* At most one dispatch per confirmation. The probe id is the adapter's
  idempotency key; a repeated, concurrent, timed-out or post-restart start never
  dispatches again. One registry source holds at most one unresolved probe:
  reserved / dispatched / dispatch_uncertain / collect_timeout block new probes
  and stay `unknown — 待人工处理`; nothing is cancelled or completed on its behalf.
* Refuses before any external call when the registry is unreadable, corrupt or
  inconsistent, when the probe registry would be the read-only history source,
  when the runtime is missing, or when the server demands a finite token budget
  (the adapter cannot enforce one; it is refused, not ignored).
* Reply, usage and evidence are read back from the probe's own registry with the
  accepted validators and the digest check against disk bytes. Usage that is not
  bound to this run's thread and turn is not adopted, and unknown usage is never
  written as zero.
* A completed probe is a completed ROUND TRIP. It grants no task success, and the
  static session registry stays `connected=false`.

Only `src.app_api` wires this service; core state machine, review and budget
modules never import it.
"""
from __future__ import annotations

import contextlib
import json
import os
import re
import secrets
import shutil
import stat
import tempfile
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Tuple

from src.agent_session import RunState, SessionAddress, SubmitRequest
from src.agent_session_codex import CodexAgentSessionAdapter, default_codex_argv
import src.agent_session_codex as _codex_module

PROBE_SCHEMA = "jh020-gui-session-probe/v1"
PROBE_APP_KIND = "codex"
PROBE_INSTANCE = "codex-app-server/local"
PROBE_ROLE = "worker"
DEFAULT_PROBE_MODEL = "gpt-6.1-sol"
DEFAULT_COLLECT_TIMEOUT = 240.0
CONFIRM_TTL_SECONDS = 600.0

REGISTRY_NAME = "probe-runs.jsonl"
RAW_DIR_NAME = "raw"
WORKSPACE_DIR_NAME = "ws"
#: Hark-派单-07: the durable source claim and the cross-process source lock.
CLAIM_NAME = "probe-claim.json"
LOCK_NAME = "probe.lock"
CLAIM_SCHEMA = "jh020-gui-session-probe-claim/v1"
CLAIM_STATES = ("accepted", "finished")
SOURCE_LOCK_TIMEOUT = 5.0
FINALIZE_LOCK_TIMEOUT = 30.0
_REPARSE_FLAG = getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400)

#: The only fields a start request may carry.
ALLOWED_START_FIELDS = frozenset({"probe_id", "confirm"})
PROBE_ID_PATTERN = re.compile(r"^[0-9a-f]{16}$")

#: Last events after which a probe is resolved. Everything else is unresolved:
#: unfinished prefixes, and the terminal-but-unknown dispatch_uncertain /
#: collect_timeout, which stay with a human.
RESOLVED_EVENTS = frozenset({"collected", "dispatch_failed"})
UNKNOWN_EVENTS = frozenset({"dispatch_uncertain", "collect_timeout"})

#: Native item types that mean a tool ran. A probe must observe none.
TOOL_ITEM_TYPES = frozenset({"commandExecution", "fileChange", "mcpToolCall", "webSearch",
                             "dynamicToolCall", "collabAgentToolCall", "imageView"})


class ProbeRefused(Exception):
    """A probe request refused before any dispatch."""

    def __init__(self, code: str, message: str, http_status: int = 409):
        super().__init__(message)
        self.code = code
        self.message = message
        self.http_status = http_status


@dataclass(frozen=True)
class ProbeConfig:
    """Server-side probe settings. The browser can change none of them."""

    enabled: bool = False
    probe_dir: Optional[str] = None
    model_id: Optional[str] = DEFAULT_PROBE_MODEL
    budget_tokens: Optional[int] = None
    collect_timeout: float = DEFAULT_COLLECT_TIMEOUT


# --- guard predicates (each protects one claim; each can be weakened alone) ---

def confirmation_given(payload: Dict[str, Any]) -> bool:
    """Only the literal boolean true is a confirmation."""
    return payload.get("confirm") is True


def payload_fields_allowed(payload: Dict[str, Any]) -> bool:
    """A start carries nothing but the probe id and the confirmation."""
    return set(payload) <= ALLOWED_START_FIELDS


def probe_enabled(config: "ProbeConfig") -> bool:
    """The server switch; off by default."""
    return config.enabled is True


def registry_usable(state: Any) -> bool:
    """Only an absent, empty or fully consistent registry may be extended."""
    return state in ("absent", "empty", "ok")


def run_is_resolved(events: List[str]) -> bool:
    """A probe is resolved only after `collected` or `dispatch_failed`."""
    return bool(events) and events[-1] in RESOLVED_EVENTS


def inflight_blocks(inflight: Optional[Dict[str, Any]]) -> bool:
    """A probe already accepted by this process blocks any other."""
    return inflight is not None


def already_used(probe_id: str, consumed: set, recorded: bool) -> bool:
    """A probe id that was consumed here or is in the registry is never dispatched again."""
    return probe_id in consumed or recorded


def budget_satisfiable(budget_tokens: Optional[int]) -> bool:
    """The adapter cannot enforce a finite token budget; such a demand is refused."""
    return budget_tokens is None


def usage_bound_to_run(usage: Dict[str, Any], thread_id: Optional[str],
                       turn_id: Optional[str]) -> bool:
    """Usage is adopted only when its source is exactly this run's thread and turn."""
    source = (usage or {}).get("source") or {}
    return (bool(thread_id) and bool(turn_id)
            and source.get("thread_id") == thread_id and source.get("turn_id") == turn_id)


def echo_matches(texts: List[str], token: str) -> bool:
    """The reply is the fixed token, alone."""
    return len(texts) == 1 and isinstance(texts[0], str) and texts[0].strip() == token


def probe_token(probe_id: str) -> str:
    return f"JH-PROBE-{probe_id.upper()}"


def probe_instruction(token: str) -> str:
    return (f"Reply with exactly this token and nothing else: {token}. "
            "Do not use any tools, do not read or write files.")


def codex_runtime_problem() -> Optional[str]:
    """Why the local Codex runtime cannot be started, or None. No process is spawned."""
    argv = default_codex_argv()
    node, script = argv[0], argv[1]
    if not (os.path.isfile(node) or shutil.which(node)):
        return f"node runtime not found: {node}"
    if not os.path.isfile(script):
        return f"codex app-server script not found: {script}"
    return None


def default_adapter_factory(registry_path: str, workspace: str):
    """The real adapter: a fresh instance that reloads the probe registry."""
    return CodexAgentSessionAdapter(cwd=workspace, registry_path=registry_path,
                                    instance_ids=(PROBE_INSTANCE,))


# --- Hark-派单-09: workspace binding and native workspace observation ----------

NATIVE_CAPTURE_PREFIX = "probe-native-"


def resolve_probe_dir(configured: Optional[str], *, cwd: Optional[str] = None,
                      default: Optional[Path] = None) -> Tuple[Path, str, Optional[Tuple[str, str]]]:
    """Turn the configured probe directory into ONE absolute path, once.

    Returns (path, resolution, problem). A relative spelling is anchored to the
    server's working directory at construction, so the planned workspace, the
    directory handed to the adapter (process cwd AND thread/turn cwd) and the
    directory the native session should report are the same absolute string;
    nothing is re-anchored later. `.` segments are dropped lexically (Path does
    this); a `..` segment is refused (problem), never collapsed, so normalising
    cannot step around an ancestor link the layout check would have refused.
    """
    if not configured:
        return Path(default), "default", None
    text = os.fspath(configured)
    if "\0" in text:
        return Path("."), "invalid", ("probe_dir_invalid", "试运行目录含 NUL 字节")
    resolution = "configured_absolute" if os.path.isabs(text) else "relative_to_server_cwd"
    joined = Path(os.path.join(cwd or os.getcwd(), text))
    if any(part == ".." for part in joined.parts):
        return joined, resolution, ("probe_dir_parent_segment",
                                    f"试运行目录含父级片段 '..'（{text}）；拒绝在任何外部调用前，避免规范化绕过链接检查")
    if not joined.is_absolute() or (os.name == "nt" and not (joined.drive and joined.root)):
        return joined, resolution, ("probe_dir_not_absolute",
                                    f"试运行目录无法确定为带盘符的绝对路径：{text}")
    return joined, resolution, None


def _same_dir(a: Optional[str], b: Optional[str]) -> bool:
    """Exact comparison after case/separator folding only. No '..' collapsing,
    no realpath: a native cwd spelled differently is NOT silently accepted."""
    if not isinstance(a, str) or not isinstance(b, str) or not a or not b:
        return False
    fold = lambda v: os.path.normcase(v.rstrip("\\/") or v)
    return fold(a) == fold(b)


class _NativeCwdObserver:
    """Wraps the real stdio transport (looked up at call time) and records
    what the app-server's thread/start RESPONSE says the session cwd is."""

    def __init__(self, inner: Any, record: Callable[[Dict[str, Any]], None]):
        self._inner = inner
        self._record = record

    def call(self, method, params, timeout=60):
        result = self._inner.call(method, params, timeout)
        if method == "thread/start":
            body = result.get("result") if isinstance(result, dict) else None
            body = body if isinstance(body, dict) else {}
            thread = body.get("thread") if isinstance(body.get("thread"), dict) else {}
            fact = {"source": "thread/start.response", "thread_id": thread.get("id"),
                    "supplied_cwd": (params or {}).get("cwd")}
            if "cwd" in body:
                fact["cwd"] = body.get("cwd")
            if "cwd" in thread:
                fact["thread_cwd"] = thread.get("cwd")
            try:
                self._record(fact)
            except Exception:
                pass                   # observation only; never changes the dispatch
        return result

    def __getattr__(self, name):
        return getattr(self._inner, name)


def native_cwd_facts(frames: List[dict]) -> List[Dict[str, Any]]:
    """thread/started notifications (the native session's own statement)."""
    out = []
    for frame in frames:
        if not isinstance(frame, dict) or frame.get("method") != "thread/started":
            continue
        thread = (frame.get("params") or {}).get("thread")
        if not isinstance(thread, dict):
            continue
        out.append({"source": "thread/started.notification", "thread_id": thread.get("id"),
                    "cwd": thread.get("cwd") if "cwd" in thread else None,
                    "has_cwd": "cwd" in thread})
    return out


def same_thread_facts(facts: List[Dict[str, Any]], thread_id: Optional[str]) -> List[Dict[str, Any]]:
    """Only the bound thread's own statements count for its workspace."""
    return [f for f in facts if thread_id and f.get("thread_id") == thread_id]


def distinct_dirs(values: List[str]) -> set:
    return {os.path.normcase(v.rstrip("\\/") or v) for v in values}


def classify_workspace(planned: Optional[str], dispatched: Any, thread_id: Optional[str],
                       facts: List[Dict[str, Any]], capture_state: str,
                       evidence_state: Optional[str]) -> Tuple[str, str]:
    """(state, reason). Only `verified` lets a listing speak for the native
    workspace; every other state means 'not verified', never 'empty'."""
    if not planned:
        return "unverifiable", "无固定标识，无法确定计划工作区"
    if not isinstance(dispatched, str) or not os.path.isabs(dispatched):
        return "dispatch_not_absolute", f"派发给适配器的工作区不是绝对路径：{dispatched!r}"
    if not _same_dir(dispatched, planned):
        return "dispatch_mismatch", "派发工作区与计划工作区不一致"
    if capture_state == "invalid":
        return "native_evidence_invalid", "服务端捕获的原生目录记录不可读或损坏"
    if evidence_state != "verified":
        # The service capture alone cannot verify: without the verified raw
        # stream a contradicting native notification could go unseen.
        return "native_evidence_unverified", f"原始证据未核实（{evidence_state}）"
    if not thread_id:
        return "native_thread_unknown", "登记未给出原生 thread，无法确定同线程目录"
    own = same_thread_facts(facts, thread_id)
    values = [f.get(k) for f in own for k in ("cwd", "thread_cwd") if isinstance(f.get(k), str) and f.get(k)]
    bad = [f for f in own for k in ("cwd", "thread_cwd") if k in f and f.get(k) is not None
           and not isinstance(f.get(k), str)]
    foreign = [f for f in facts if f not in own
               and any(isinstance(f.get(k), str) for k in ("cwd", "thread_cwd"))]
    if bad:
        return "native_conflict", "同线程原生目录字段类型无效"
    if not values:
        if foreign:
            return "native_foreign_only", "只有外来线程的原生目录，本线程无依据"
        return "native_missing", "原生目录缺失"
    if len(distinct_dirs(values)) > 1:
        return "native_conflict", "同线程原生目录相互矛盾"
    if not _same_dir(values[0], planned):
        return "native_mismatch", f"原生目录与计划工作区不一致：{values[0]}"
    return "verified", "计划、派发与原生目录一致"


def _thread_runner(job: Callable[[], None]) -> None:
    threading.Thread(target=job, name="jh-session-probe", daemon=True).start()


def _listing(root: Path) -> Optional[List[str]]:
    if not root.is_dir():
        return None
    out = []
    for current, _dirs, files in os.walk(root):
        for name in files:
            out.append(str((Path(current) / name).relative_to(root)))
    return sorted(out)


def _tool_items(path: Optional[str]) -> Optional[List[str]]:
    """Tool item types seen in a VERIFIED raw evidence file; None when unreadable."""
    if not path:
        return None
    try:
        lines = Path(path).read_bytes().decode("utf-8").splitlines()
    except (OSError, UnicodeDecodeError):
        return None
    seen = []
    for line in lines:
        try:
            frame = json.loads(line)
        except ValueError:
            continue
        item = ((frame.get("params") or {}).get("item") or {}) if isinstance(frame, dict) else {}
        kind = item.get("type") if isinstance(item, dict) else None
        if kind in TOOL_ITEM_TYPES:
            seen.append(kind)
    return seen


# --- Hark-派单-07: source identity, static link layout, source lock, claim ----

def _is_link_entry(entry: os.stat_result) -> bool:
    """A symlink, a Windows junction or any other reparse point."""
    return stat.S_ISLNK(entry.st_mode) or bool(getattr(entry, "st_file_attributes", 0) & _REPARSE_FLAG)


def link_component(path: Path) -> Optional[str]:
    """The first EXISTING component of `path`, from the filesystem root down,
    that is a link/junction/reparse point; None when the whole chain is plain.

    Every ancestor is inspected, not only the last component: a junction above
    the probe directory redirects every write below it just the same. A path
    whose canonical form differs from its written form also reached somewhere
    else and is refused.
    """
    absolute = Path(os.path.abspath(path))
    chain = list(reversed(absolute.parents)) + [absolute]
    deepest = None
    for component in chain:
        try:
            entry = os.lstat(component)
        except FileNotFoundError:
            break
        except OSError as exc:
            return f"{component}（无法检查：{exc}）"
        if _is_link_entry(entry):
            return str(component)
        deepest = component
    if deepest is not None:
        try:
            if os.path.normcase(os.path.realpath(deepest)) != os.path.normcase(str(deepest)):
                return f"{deepest}（规范路径不同：{os.path.realpath(deepest)}）"
        except (OSError, ValueError) as exc:
            return f"{deepest}（无法规范化：{exc}）"
    return None


def file_identity_problem(path: Path) -> Optional[Tuple[str, str]]:
    """(code, reason) when an existing file the probe would write is not a plain,
    singly-linked regular file; None when it is absent or plain.

    A hard link has its own directory entry and is invisible to islink/realpath,
    so the link count is what exposes a second name for the same bytes.
    """
    try:
        entry = os.lstat(path)
    except FileNotFoundError:
        return None
    except OSError as exc:
        return "probe_path_invalid", f"{path} 无法检查：{exc}"
    if _is_link_entry(entry):
        return "probe_path_is_link", f"{path} 是链接"
    if not stat.S_ISREG(entry.st_mode):
        return "probe_path_invalid", f"{path} 不是普通文件"
    if entry.st_nlink != 1:
        return "probe_path_hardlinked", f"{path} 有 {entry.st_nlink} 个硬链接名称，拒绝写入"
    return None


def dir_problem(path: Path) -> Optional[Tuple[str, str]]:
    """(code, reason) when an existing directory the probe would write into is a
    link or not a directory; None when absent or a plain directory."""
    try:
        entry = os.lstat(path)
    except FileNotFoundError:
        return None
    except OSError as exc:
        return "probe_path_invalid", f"{path} 无法检查：{exc}"
    if _is_link_entry(entry):
        return "probe_path_is_link", f"{path} 是链接/联接"
    if not stat.S_ISDIR(entry.st_mode):
        return "probe_dir_invalid", f"{path} 不是目录"
    return None


def _norm(path: Path) -> str:
    return os.path.normcase(os.path.abspath(path))


def _norm_real(path: Path) -> str:
    return os.path.normcase(os.path.realpath(path))


def _inside(child: str, parent: str) -> bool:
    return child == parent or child.startswith(parent.rstrip(os.sep) + os.sep)


def history_conflict(registry: Path, probe_dir: Path, history: Optional[Path]) -> Optional[str]:
    """Why the probe's writable source would alias the read-only history, or None.

    Covers the same written path, the same canonical path, the same file under
    another name (hard link or any alias `samefile` sees) and a history file that
    lives inside the probe directory the probe writes into.
    """
    if history is None:
        return None
    try:
        if _norm(registry) == _norm(history) or _norm_real(registry) == _norm_real(history):
            return "试运行登记与只读历史来源是同一路径"
        if os.path.exists(registry) and os.path.exists(history) and os.path.samefile(registry, history):
            return "试运行登记与只读历史来源是同一文件（硬链接或别名）"
        if _inside(_norm(history), _norm(probe_dir)) or _inside(_norm_real(history), _norm_real(probe_dir)):
            return "只读历史来源位于试运行可写目录内"
    except (OSError, ValueError) as exc:
        return f"无法确认试运行登记与历史来源相互独立：{exc}"
    return None


_SOURCE_THREAD_LOCKS: Dict[str, threading.Lock] = {}
_SOURCE_THREAD_LOCKS_GUARD = threading.Lock()


def _thread_lock_for(key: str) -> threading.Lock:
    with _SOURCE_THREAD_LOCKS_GUARD:
        lock = _SOURCE_THREAD_LOCKS.get(key)
        if lock is None:
            lock = threading.Lock()
            _SOURCE_THREAD_LOCKS[key] = lock
        return lock


def _os_lock(fd: int) -> None:
    if os.name == "nt":                                  # pragma: no cover - Windows only
        import msvcrt
        os.lseek(fd, 0, os.SEEK_SET)
        msvcrt.locking(fd, msvcrt.LK_NBLCK, 1)
    else:
        import fcntl
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)


def _os_unlock(fd: int) -> None:
    try:
        if os.name == "nt":                              # pragma: no cover - Windows only
            import msvcrt
            os.lseek(fd, 0, os.SEEK_SET)
            msvcrt.locking(fd, msvcrt.LK_UNLCK, 1)
        else:
            import fcntl
            fcntl.flock(fd, fcntl.LOCK_UN)
    except OSError:
        pass


def _identity(path: Path) -> Optional[Tuple[int, int]]:
    """(st_dev, st_ino) of the entry itself (lstat), or None when absent/unreadable."""
    try:
        entry = os.lstat(path)
    except OSError:
        return None
    return entry.st_dev, entry.st_ino


def source_dir_problem(probe_dir: Path) -> Optional[Tuple[str, str]]:
    """(code, reason) when the probe directory ITSELF (or any ancestor) cannot be
    trusted as the place to open a lock or write a claim; None when it can."""
    linked = link_component(probe_dir)
    if linked:
        return "probe_path_is_link", f"试运行目录或其祖先含链接/联接：{linked}"
    problem = dir_problem(probe_dir)
    if problem:
        return problem
    return None


@contextlib.contextmanager
def acquire_source_lock(probe_dir: Path, timeout: Optional[float] = None, *, create: bool = True):
    """Exclusive lock on ONE probe source, across service objects AND processes.

    A per-path thread lock serialises objects inside one process; an OS file
    lock on `<probe_dir>/probe.lock` serialises processes. A source that stays
    busy past `timeout` is refused (safe rejection of a second user), never
    waited on forever and never bypassed.

    Hark-派单-08: the directory is re-checked here, so no caller can open (let
    alone create) a lock file through a link/junction the probe has refused.
    With `create=False` an absent lock file is refused instead of created.
    """
    if timeout is None:
        timeout = SOURCE_LOCK_TIMEOUT
    key = _norm(probe_dir)
    deadline = time.monotonic() + timeout
    thread_lock = _thread_lock_for(key)
    if not thread_lock.acquire(timeout=max(timeout, 0.0)):
        raise ProbeRefused("probe_source_busy", "同一试运行来源正被本进程另一使用者占用；未派发")
    fd = None
    try:
        lock_path = Path(probe_dir) / LOCK_NAME
        problem = source_dir_problem(probe_dir)
        if problem:
            raise ProbeRefused(problem[0], f"试运行来源目录不可信：{problem[1]}；不打开锁文件")
        problem = file_identity_problem(lock_path)
        if problem:
            raise ProbeRefused(problem[0], f"试运行来源锁文件不可信：{problem[1]}；未派发")
        if not create and not os.path.lexists(lock_path):
            raise ProbeRefused("probe_lock_missing", f"来源锁文件已不存在：{lock_path}；不重新创建")
        flags = os.O_RDWR | (os.O_CREAT if create else 0) | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_BINARY", 0)
        fd = os.open(lock_path, flags, 0o600)
        while True:
            try:
                _os_lock(fd)
                break
            except OSError:
                if time.monotonic() >= deadline:
                    raise ProbeRefused("probe_source_busy",
                                       "同一试运行来源正被另一进程占用；安全拒绝第二使用者，未派发")
                time.sleep(0.05)
        try:
            yield
        finally:
            _os_unlock(fd)
    finally:
        if fd is not None:
            os.close(fd)
        thread_lock.release()


def read_claim(path: Path) -> Tuple[str, Optional[Dict[str, Any]], str]:
    """(state, claim, message) of the durable source claim; never writes.

    absent -> no probe ever accepted here; ok -> a validated claim; corrupt ->
    anything else, which blocks rather than being read as absent.
    """
    try:
        raw = Path(path).read_bytes()
    except FileNotFoundError:
        return "absent", None, ""
    except OSError as exc:
        return "corrupt", None, f"认领文件不可读：{exc}"
    try:
        data = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, ValueError) as exc:
        return "corrupt", None, f"认领文件不是合法 JSON：{exc}"
    if (not isinstance(data, dict) or data.get("schema") != CLAIM_SCHEMA
            or data.get("state") not in CLAIM_STATES
            or not isinstance(data.get("probe_id"), str) or not PROBE_ID_PATTERN.match(data["probe_id"])):
        return "corrupt", None, "认领文件字段不符合格式"
    return "ok", data, ""


def claim_blocks(state: str, claim: Optional[Dict[str, Any]]) -> bool:
    """A corrupt claim, or a claim accepted but never finished, blocks the source."""
    if state == "absent":
        return False
    return state != "ok" or (claim or {}).get("state") != "finished"


def write_claim(path: Path, data: Dict[str, Any]) -> None:
    """Durably replace the claim: temp file in the same directory, fsync, rename.

    The rename replaces the directory entry, so even a pre-existing hard link
    would not receive the bytes; identity is still checked first.
    """
    problem = file_identity_problem(path)
    if problem:
        raise ProbeRefused(problem[0], f"试运行认领文件不可信：{problem[1]}；未派发")
    payload = json.dumps(data, ensure_ascii=False, sort_keys=True).encode("utf-8")
    handle, tmp_name = tempfile.mkstemp(dir=str(Path(path).parent), prefix=".probe-claim-", suffix=".tmp")
    try:
        with os.fdopen(handle, "wb") as stream:
            stream.write(payload)
            stream.flush()
            os.fsync(stream.fileno())
        for attempt in range(5):
            try:
                os.replace(tmp_name, path)
                break
            except PermissionError:
                if attempt == 4:
                    raise
                time.sleep(0.02 * (attempt + 1))
    except BaseException:
        try:
            os.unlink(tmp_name)
        except OSError:
            pass
        raise


class SessionProbeService:
    """One opt-in probe source: one registry, one raw-evidence root, one claim.

    Mutual exclusion is per SOURCE (the probe directory), not per object: the
    source lock plus a durable claim written before a start is accepted keep a
    second object or process from accepting while a probe is unresolved.
    """

    def __init__(self, config: ProbeConfig, *, history_runs_path: Optional[Path],
                 reader_factory: Callable[[Path, Path], Any],
                 adapter_factory: Optional[Callable[[str, str], Any]] = None,
                 runtime_check: Optional[Callable[[], Optional[str]]] = None,
                 runner: Optional[Callable[[Callable[[], None]], None]] = None,
                 clock: Callable[[], float] = time.time):
        self.config = config
        base, resolution, path_problem = resolve_probe_dir(
            config.probe_dir, default=Path(__file__).resolve().parents[1] / "data" / "gui" / "session-probe")
        #: Hark-派单-09: resolved ONCE to an absolute path; every later use
        #: (layout checks, lock, planned/dispatched workspace) is this string.
        self.probe_dir_configured = config.probe_dir
        self.probe_dir_resolution = resolution
        self._path_problem = path_problem
        self.probe_dir = base
        self.registry_path = base / REGISTRY_NAME
        self.raw_root = base / RAW_DIR_NAME
        self.workspace_root = base / WORKSPACE_DIR_NAME
        self.claim_path = base / CLAIM_NAME
        self.history_runs_path = Path(history_runs_path) if history_runs_path else None
        self._reader_factory = reader_factory
        self._adapter_factory = adapter_factory or self._observed_default_factory
        # Shown on every status so an offline/synthetic preview can never be
        # mistaken for a real model result.
        self.adapter_source = "real_codex_adapter" if adapter_factory is None else "injected_offline_adapter"
        self._runtime_check = runtime_check or codex_runtime_problem
        self._runner = runner or _thread_runner
        self._clock = clock
        self._lock = threading.Lock()
        self._prepared: Dict[str, float] = {}
        self._consumed: set = set()
        self._inflight: Optional[Dict[str, Any]] = None
        self._last_error: Optional[Dict[str, Any]] = None
        self.submit_calls = 0            # adapter.submit calls made by this service
        #: Test seam: called inside the source lock, after the blockers were read
        #: and before the claim is written. Production leaves it None.
        self._claim_hook: Optional[Callable[[], None]] = None
        #: Hark-派单-08: identity of the source directory and of the claim this
        #: object wrote at acceptance; the cleanup only writes back to THOSE.
        self._accepted_identity: Dict[str, Dict[str, Any]] = {}

    # -- reading ---------------------------------------------------------------
    def _reader(self):
        return self._reader_factory(self.registry_path, self.raw_root)

    def _registry_view(self) -> Dict[str, Any]:
        """The probe registry through the accepted read-only history reader."""
        return self._reader().list_agent_runs()

    def _layout_blockers(self) -> List[Dict[str, str]]:
        """Static isolation of the writable source; reads metadata only."""
        out: List[Dict[str, str]] = []
        if self._path_problem:
            # Refused before ANY other check touches the path.
            return [{"code": self._path_problem[0], "message": self._path_problem[1]}]
        conflict = history_conflict(self.registry_path, self.probe_dir, self.history_runs_path)
        if conflict:
            out.append({"code": "probe_registry_is_history_source",
                        "message": conflict + "；拒绝把历史来源变成可写来源"})
        linked = link_component(self.probe_dir)
        if linked:
            out.append({"code": "probe_path_is_link",
                        "message": f"试运行目录或其祖先含链接/联接，拒绝写入：{linked}"})
        for directory in (self.probe_dir, self.raw_root, self.workspace_root):
            problem = dir_problem(directory)
            if problem:
                out.append({"code": problem[0], "message": f"试运行目录不可信：{problem[1]}"})
        for name in (self.registry_path, self.claim_path, self.probe_dir / LOCK_NAME):
            problem = file_identity_problem(name)
            if problem:
                out.append({"code": problem[0], "message": f"试运行文件不可信：{problem[1]}"})
        return out

    def _blockers(self, view: Dict[str, Any]) -> List[Dict[str, str]]:
        out: List[Dict[str, str]] = []

        def block(code: str, message: str) -> None:
            out.append({"code": code, "message": message})

        if not probe_enabled(self.config):
            block("probe_disabled", "服务端未启用会话试运行（默认关闭）")
        if not budget_satisfiable(self.config.budget_tokens):
            block("probe_budget_unenforceable",
                  f"服务端要求有限 token 预算 {self.config.budget_tokens}，适配器无法强制；拒发")
        problem = self._runtime_check()
        if problem:
            block("probe_runtime_missing", f"本地 Codex 运行时不可用：{problem}")
        state = view.get("state")
        if not registry_usable(state):
            if state in ("corrupt", "unreadable"):
                block(f"probe_registry_{state}", f"试运行登记不可用：{view.get('message')}")
            else:
                block("probe_registry_unknown", f"试运行登记状态未知：{state!r}")
        out.extend(self._layout_blockers())
        for run in view.get("runs") or []:
            events = run.get("events") or []
            last = events[-1] if events else None
            if not run_is_resolved(events):
                block("probe_unresolved",
                      f"存在未解决的试运行 {run.get('run_id')}（最后事件 {last}）；结果未知，待人工处理，不会自动重发")
        if inflight_blocks(self._inflight):
            block("probe_in_flight", f"试运行 {self._inflight['probe_id']} 正在进行")
        claim_state, claim, claim_message = read_claim(self.claim_path)
        if claim_blocks(claim_state, claim):
            if claim_state == "ok":
                block("probe_claim_unresolved",
                      f"同一来源的试运行 {claim['probe_id']} 已受理但尚未登记结束"
                      "（可能在另一服务对象/进程中进行，或受理后进程退出）；待人工处理，不会自动重发")
            else:
                block("probe_claim_corrupt", f"试运行认领不可用：{claim_message}；拒发")
        return out

    # -- views -----------------------------------------------------------------
    def _plan(self) -> Dict[str, Any]:
        """What a probe WILL call. Shown before confirmation; nothing here is sent."""
        return {
            "app_kind": PROBE_APP_KIND, "instance_id": PROBE_INSTANCE,
            "session": "new（新建隔离会话；不接受既有会话）", "role": PROBE_ROLE,
            "model": self.config.model_id,
            "sandbox": "read-only（thread 与 turn 两级）", "network": "off",
            "approval_policy": "never（审批请求一律拒绝）",
            "instruction_template": probe_instruction("JH-PROBE-<服务端生成>"),
            "workspace_root": str(self.workspace_root),
            "workspace_binding": {"configured": self.probe_dir_configured,
                                  "resolution": self.probe_dir_resolution,
                                  "resolved_probe_dir": str(self.probe_dir),
                                  "note": "计划、派发（进程 cwd 与 thread/turn cwd）使用同一绝对路径"},
            "registry": str(self.registry_path), "evidence_root": str(self.raw_root),
            "budget": ("适配器不支持硬 token 预算；真实调用会使用模型额度"
                       if self.config.budget_tokens is None else
                       f"服务端要求 {self.config.budget_tokens} tokens 上限，无法强制，拒发"),
            "collect_timeout_seconds": self.config.collect_timeout,
        }

    def _run_view(self, run: Dict[str, Any], reader) -> Dict[str, Any]:
        events = run.get("events") or []
        last = events[-1] if events else None
        probe_id = run.get("idempotency_key")
        token = probe_token(probe_id) if isinstance(probe_id, str) and PROBE_ID_PATTERN.match(probe_id) else None
        detail = reader.agent_run_detail(run["run_id"])
        records = detail.get("records") or []
        collected = next((r for r in reversed(records) if r.get("event") == "collected"), None)
        snapshot = {}
        try:
            snapshot = json.loads(records[0].get("request_snapshot") or "{}") if records else {}
        except ValueError:
            snapshot = {}
        instruction_bound = bool(token) and snapshot.get("instructions") == probe_instruction(token)
        texts = list((collected or {}).get("assistant_texts") or [])
        turns = run.get("native_turns") or []
        thread_id = run.get("native_session_id")
        turn_id = turns[-1] if turns else None
        usage = run.get("usage") or {}
        bound = usage_bound_to_run(usage, thread_id, turn_id)
        evidence = run.get("evidence") or {}
        tools = _tool_items(evidence.get("path")) if evidence.get("state") == "verified" else None
        if collected is None:
            echo = "unknown"
        else:
            echo = "matched" if (token and instruction_bound and echo_matches(texts, token)) else "mismatch"
        inflight = self._inflight is not None and self._inflight.get("probe_id") == probe_id
        if inflight:
            outcome = "in_progress"
        elif last == "collected":
            ok = (run.get("record_state") == RunState.DECLARED_COMPLETE and echo == "matched"
                  and evidence.get("state") == "verified" and tools == [])
            outcome = "round_trip_complete" if ok else "round_trip_not_proven"
        elif last == "dispatch_failed":
            outcome = "dispatch_failed"
        else:
            outcome = "unknown_manual"
        workspace = self.workspace_root / probe_id if token else None
        observation = self._workspace_observation(probe_id if token else None, workspace,
                                                  snapshot.get("workspace"), thread_id, evidence)
        return {
            "run_id": run["run_id"], "probe_id": probe_id, "token": token,
            "events": events, "record_state": run.get("record_state"),
            "native_state": run.get("native_state"), "native_model": run.get("native_model"),
            "requested_model": run.get("requested_model"),
            "native_session_id": thread_id, "native_turn_id": turn_id,
            "first_recorded_at": run.get("first_recorded_at"),
            "last_recorded_at": run.get("last_recorded_at"),
            "instruction_bound": instruction_bound,
            "reply_texts": texts, "echo": echo,
            "usage": ({"turn": usage.get("turn"), "thread_total": usage.get("thread_total"),
                       "source": usage.get("source")} if bound else None),
            "usage_state": "bound" if bound else ("unbound" if usage.get("turn") or usage.get("thread_total")
                                                  else "unknown"),
            "evidence": evidence, "tool_items": tools,
            # Hark-派单-09: a listing only when the native workspace is VERIFIED
            # to be the planned one; otherwise None ("not verified", never "empty").
            "workspace_files": observation["files"],
            "workspace": observation,
            "unverified": run.get("unverified") or [],
            "declared_status": run.get("declared_status"),
            "outcome": outcome,
            "note": "试运行往返完成只表示固定标识往返；不授予任何任务成功（Task succeeded）",
        }

    # -- Hark-派单-09: native workspace capture and observation ---------------
    def _capture_path(self, probe_id: str) -> Path:
        return self.raw_root / f"{NATIVE_CAPTURE_PREFIX}{probe_id}.json"

    def _observed_default_factory(self, registry_path: str, workspace: str):
        """The real adapter, with ONLY its stdio transport wrapped so the
        thread/start response cwd is recorded. Same argv, same process cwd."""
        probe_id = Path(workspace).name
        capture = self._capture_path(probe_id)

        def record(fact: Dict[str, Any]) -> None:
            if source_dir_problem(self.probe_dir) or dir_problem(self.raw_root) or file_identity_problem(capture):
                return                                    # never write through a refused layout
            self.raw_root.mkdir(exist_ok=True)
            if dir_problem(self.raw_root) or source_dir_problem(self.probe_dir):
                return
            payload = json.dumps({"probe_id": probe_id, "facts": [fact]}, ensure_ascii=False,
                                 sort_keys=True).encode("utf-8")
            handle, tmp = tempfile.mkstemp(dir=str(self.raw_root), prefix=".probe-native-", suffix=".tmp")
            try:
                with os.fdopen(handle, "wb") as stream:
                    stream.write(payload)
                    stream.flush()
                    os.fsync(stream.fileno())
                os.replace(tmp, capture)
            finally:
                if os.path.exists(tmp):
                    os.unlink(tmp)

        def transport():
            inner = _codex_module.CodexStdioTransport(default_codex_argv(), workspace)
            return _NativeCwdObserver(inner, record)

        return CodexAgentSessionAdapter(cwd=workspace, registry_path=registry_path,
                                        instance_ids=(PROBE_INSTANCE,), transport_factory=transport)

    def _read_capture(self, probe_id: str) -> Tuple[str, List[Dict[str, Any]]]:
        path = self._capture_path(probe_id)
        if not os.path.lexists(path):
            return "absent", []
        if file_identity_problem(path):
            return "invalid", []
        try:
            data = json.loads(path.read_bytes().decode("utf-8"))
        except (OSError, UnicodeDecodeError, ValueError):
            return "invalid", []
        facts = data.get("facts") if isinstance(data, dict) else None
        if data.get("probe_id") != probe_id or not isinstance(facts, list) \
                or not all(isinstance(f, dict) for f in facts):
            return "invalid", []
        return "present", facts

    def _workspace_observation(self, probe_id: Optional[str], workspace: Optional[Path],
                               dispatched: Any, thread_id: Optional[str],
                               evidence: Dict[str, Any]) -> Dict[str, Any]:
        planned = str(workspace) if workspace else None
        frames: List[dict] = []
        if evidence.get("state") == "verified" and evidence.get("path"):
            try:
                for line in Path(evidence["path"]).read_bytes().decode("utf-8").splitlines():
                    try:
                        frames.append(json.loads(line))
                    except ValueError:
                        continue
            except (OSError, UnicodeDecodeError):
                frames = []
        facts = native_cwd_facts(frames)
        capture_state, captured = self._read_capture(probe_id) if probe_id else ("absent", [])
        facts = facts + [dict(f, source=f.get("source") or "thread/start.response") for f in captured]
        state, reason = classify_workspace(planned, dispatched, thread_id, facts, capture_state,
                                           evidence.get("state"))
        files = _listing(workspace) if (state == "verified" and workspace) else None
        return {
            "state": state, "verified": state == "verified", "reason": reason,
            "planned": planned, "dispatched": dispatched if isinstance(dispatched, str) else None,
            "native_sources": [{"source": f.get("source"), "thread_id": f.get("thread_id"),
                                "same_thread": bool(thread_id) and f.get("thread_id") == thread_id,
                                "cwd": f.get("cwd"), "thread_cwd": f.get("thread_cwd")}
                               for f in facts],
            "capture": capture_state,
            "files": files,
            "verified_empty": (files == []) if state == "verified" else None,
            "scope": ("原生会话目录（已与计划、派发一致核实）" if state == "verified"
                      else "未核实：不对实际工作区内容作任何断言"),
        }

    def _claim_view(self) -> Dict[str, Any]:
        state, claim, message = read_claim(self.claim_path)
        return {"state": state, "message": message,
                "probe_id": (claim or {}).get("probe_id"), "claim_state": (claim or {}).get("state"),
                "outcome": (claim or {}).get("outcome"), "path": str(self.claim_path)}

    def status(self) -> Dict[str, Any]:
        """Read-only. Never prepares, never dispatches, never writes."""
        view = self._registry_view()
        with self._lock:
            blockers = self._blockers(view)
            inflight = dict(self._inflight) if self._inflight else None
            last_error = dict(self._last_error) if self._last_error else None
        runs = view.get("runs") or []
        latest = self._run_view(runs[-1], self._reader()) if runs else None
        return {
            "ok": True, "read_only": True, "schema": PROBE_SCHEMA,
            "enabled": bool(self.config.enabled), "available": not blockers,
            "adapter_source": self.adapter_source,
            "blockers": blockers, "plan": self._plan(),
            "registry": {"state": view.get("state"), "message": view.get("message"),
                         "count": view.get("count"), "path": str(self.registry_path)},
            "inflight": inflight, "last_error": last_error, "latest": latest,
            "history_count": len(runs), "live_session_registry_changed": False,
            "claim": self._claim_view(),
            "note": ("真实调用会使用模型额度；试运行往返完成不等于任务验收成功；"
                     "静态会话登记仍为 connected=false。"),
        }

    # -- actions ---------------------------------------------------------------
    def prepare(self, payload: Any) -> Dict[str, Any]:
        """Issue a single-use probe id. Nothing is sent and nothing is written."""
        if payload not in (None, {}):
            raise ProbeRefused("probe_field_forbidden", "准备请求不接受任何字段", 400)
        with self._lock:
            blockers = self._blockers(self._registry_view())
            if blockers:
                raise ProbeRefused(blockers[0]["code"], blockers[0]["message"],
                                   403 if blockers[0]["code"] == "probe_disabled" else 409)
            now = self._clock()
            self._prepared = {k: v for k, v in self._prepared.items() if v > now}
            probe_id = secrets.token_hex(8)
            self._prepared[probe_id] = now + CONFIRM_TTL_SECONDS
        token = probe_token(probe_id)
        return {"ok": True, "read_only": True, "probe_id": probe_id, "token": token,
                "instruction": probe_instruction(token), "expires_in_seconds": CONFIRM_TTL_SECONDS,
                "plan": self._plan(), "dispatched": False,
                "note": "仅生成一次性试运行编号；确认后才会派发"}

    def start(self, payload: Any) -> Dict[str, Any]:
        """Dispatch at most once for a confirmed, prepared probe id."""
        if not isinstance(payload, dict):
            raise ProbeRefused("probe_malformed", "请求必须是对象", 400)
        if not payload_fields_allowed(payload):
            extra = sorted(set(payload) - ALLOWED_START_FIELDS)
            raise ProbeRefused("probe_field_forbidden",
                               f"试运行只接受 probe_id 与 confirm；拒绝字段 {extra}", 400)
        if not confirmation_given(payload):
            raise ProbeRefused("probe_not_confirmed", "未明确确认（confirm 必须为 true）；未派发", 400)
        probe_id = payload.get("probe_id")
        if not isinstance(probe_id, str) or not PROBE_ID_PATTERN.match(probe_id):
            raise ProbeRefused("probe_id_invalid", "试运行编号无效；未派发", 400)
        refused = self._pre_start(probe_id)
        if refused is not None:
            return refused
        # Nothing has been written yet. Create the source directory only after
        # the static layout passed, then re-check it: creating parents cannot
        # have introduced a link, but a pre-existing one would be caught here.
        try:
            self.probe_dir.mkdir(parents=True, exist_ok=True)
        except OSError as exc:
            raise ProbeRefused("probe_dir_invalid", f"无法建立试运行目录：{exc}；未派发")
        layout = self._layout_blockers()
        if layout:
            raise ProbeRefused(layout[0]["code"], layout[0]["message"])
        with acquire_source_lock(self.probe_dir):
            with self._lock:
                # Decide on the SOURCE, inside the source lock: the registry and
                # the claim another object or process may have written.
                view = self._registry_view()
                recorded = any(run.get("idempotency_key") == probe_id for run in view.get("runs") or [])
                if already_used(probe_id, self._consumed, recorded):
                    return self._replay(probe_id)
                expiry = self._prepared.get(probe_id)
                if expiry is None or expiry <= self._clock():
                    self._prepared.pop(probe_id, None)
                    raise ProbeRefused("probe_not_prepared",
                                       "试运行编号未签发或已过期（服务重开后需重新准备）；未派发", 409)
                blockers = self._blockers(view)
                if blockers:
                    raise ProbeRefused(blockers[0]["code"], blockers[0]["message"],
                                       403 if blockers[0]["code"] == "probe_disabled" else 409)
                if self._claim_hook is not None:
                    self._claim_hook()
                accepted_at = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
                try:
                    # Durable BEFORE acceptance: a crash after this line leaves
                    # an unfinished claim that blocks the source for a human.
                    write_claim(self.claim_path, {"schema": CLAIM_SCHEMA, "probe_id": probe_id,
                                                  "state": "accepted", "accepted_at": accepted_at,
                                                  "pid": os.getpid()})
                except OSError as exc:
                    raise ProbeRefused("probe_claim_unwritable", f"无法持久化试运行认领：{exc}；未派发")
                self._accepted_identity[probe_id] = {"dir": _identity(self.probe_dir),
                                                     "claim": _identity(self.claim_path)}
                del self._prepared[probe_id]
                self._consumed.add(probe_id)
                self._inflight = {"probe_id": probe_id, "phase": "accepted", "accepted_at": accepted_at}
                self._last_error = None
        self._runner(lambda: self._execute(probe_id))
        return {"ok": True, "read_only": False, "probe_id": probe_id, "accepted": True,
                "replay": False, "dispatched_now": True,
                "note": "已受理一次真实试运行；结果以登记与证据为准"}

    def _replay(self, probe_id: str) -> Dict[str, Any]:
        return {"ok": True, "read_only": False, "probe_id": probe_id,
                "accepted": False, "replay": True, "dispatched_now": False,
                "note": "该试运行编号已使用；不会重发"}

    def _pre_start(self, probe_id: str) -> Optional[Dict[str, Any]]:
        """Checks that need no write: replay, prepared id, every blocker."""
        view = self._registry_view()
        with self._lock:
            recorded = any(run.get("idempotency_key") == probe_id for run in view.get("runs") or [])
            if already_used(probe_id, self._consumed, recorded):
                return self._replay(probe_id)
            expiry = self._prepared.get(probe_id)
            if expiry is None or expiry <= self._clock():
                self._prepared.pop(probe_id, None)
                raise ProbeRefused("probe_not_prepared",
                                   "试运行编号未签发或已过期（服务重开后需重新准备）；未派发", 409)
            blockers = self._blockers(view)
        if blockers:
            raise ProbeRefused(blockers[0]["code"], blockers[0]["message"],
                               403 if blockers[0]["code"] == "probe_disabled" else 409)
        return None

    def _build_request(self, probe_id: str, workspace: Path) -> SubmitRequest:
        token = probe_token(probe_id)
        session = SessionAddress(app_kind=PROBE_APP_KIND, instance_id=PROBE_INSTANCE,
                                 session_id=f"new:gui-probe-{probe_id}",
                                 model_id=self.config.model_id, role=PROBE_ROLE)
        return SubmitRequest(task_id=f"gui-probe-{probe_id}", attempt=1, session=session,
                             instructions=probe_instruction(token), workspace=str(workspace),
                             allowed_scope=(), idempotency_key=probe_id, artifacts=(),
                             budget_tokens=self.config.budget_tokens)

    def _set_phase(self, phase: str) -> None:
        with self._lock:
            if self._inflight is not None:
                self._inflight["phase"] = phase

    def _precheck_before_submit(self, probe_id: Optional[str] = None) -> Optional[Dict[str, str]]:
        """The static layout again, immediately before anything is created in the
        workspace or handed to the adapter (which writes before any external call).
        Hark-派单-08: also the SOURCE identity recorded at acceptance, so a plain
        directory swapped in after acceptance is not written to either."""
        layout = self._layout_blockers()
        if layout:
            return layout[0]
        if probe_id is not None and probe_id in self._accepted_identity:
            replaced = self._identity_problem(probe_id)
            if replaced:
                return {"code": replaced[0], "message": replaced[1] + "；未派发"}
        return None

    def _identity_problem(self, probe_id: str) -> Optional[Tuple[str, str]]:
        expected = self._accepted_identity.get(probe_id) or {}
        if expected.get("dir") is None or _identity(self.probe_dir) != expected.get("dir"):
            return "probe_source_replaced", "试运行目录已不是受理时的同一目录"
        if expected.get("claim") is None or _identity(self.claim_path) != expected.get("claim"):
            return "probe_claim_replaced", "认领文件已不是受理时写入的同一文件"
        return None

    def _execute(self, probe_id: str) -> None:
        adapter = None
        outcome = "error"
        try:
            problem = self._precheck_before_submit(probe_id)
            if problem:
                outcome = "refused_before_dispatch"
                with self._lock:
                    self._last_error = {"probe_id": probe_id, "type": "ProbeRefused",
                                        "code": problem["code"], "message": problem["message"]}
                return
            workspace = self.workspace_root / probe_id
            workspace.mkdir(parents=True, exist_ok=False)
            request = self._build_request(probe_id, workspace)
            adapter = self._adapter_factory(str(self.registry_path), str(workspace))
            self._set_phase("dispatching")
            self.submit_calls += 1
            status = adapter.submit(request)
            outcome = "submitted"
            if status.state == RunState.RUNNING and status.native_turn is not None:
                self._set_phase("collecting")
                adapter.collect(request.session, status.run_id, timeout=self.config.collect_timeout)
                outcome = "collected_or_timed_out"
        except Exception as exc:                       # recorded, never retried
            outcome = "error"
            with self._lock:
                self._last_error = {"probe_id": probe_id, "type": type(exc).__name__,
                                    "message": str(exc)}
        finally:
            if adapter is not None:
                try:
                    adapter.close()
                except Exception:
                    pass
            self._finish_claim(probe_id, outcome)
            with self._lock:
                self._inflight = None

    def _cleanup_problem(self, probe_id: str) -> Optional[Tuple[str, str]]:
        """Why the claim must NOT be written back now, or None.

        The cleanup honours every refusal already made about the SOURCE: the
        probe directory (and its ancestors) must still be plain, still be the
        same directory and hold the same claim file this object wrote; the
        claim and lock files must still be plain singly-linked files. raw/ws
        links do not affect where the claim lives and are reported by the
        pre-submit refusal instead.
        """
        problem = source_dir_problem(self.probe_dir)
        if problem:
            return problem
        for name in (self.claim_path, self.probe_dir / LOCK_NAME):
            problem = file_identity_problem(name)
            if problem:
                return problem
        return self._identity_problem(probe_id)

    def _report_cleanup_refused(self, probe_id: str, code: str, message: str) -> None:
        detail = {"code": code, "message": message + "；认领未标记结束，来源保持阻断，待人工处理"}
        with self._lock:
            if self._last_error and self._last_error.get("probe_id") == probe_id:
                self._last_error = dict(self._last_error, cleanup_refused=detail)
            else:
                self._last_error = {"probe_id": probe_id, "type": "CleanupRefused", "code": code,
                                    "message": detail["message"], "cleanup_refused": detail}

    def _finish_claim(self, probe_id: str, outcome: str) -> None:
        """Mark the claim finished, only where it is safe to do so.

        The registry, not this mark, says whether the run itself is resolved.
        When the source can no longer be trusted the claim is left untouched
        (blocking) and the refusal is reported; nothing is created or written.
        """
        try:
            problem = self._cleanup_problem(probe_id)
            if problem:
                self._report_cleanup_refused(probe_id, *problem)
                return
            with acquire_source_lock(self.probe_dir, timeout=FINALIZE_LOCK_TIMEOUT, create=False):
                problem = self._cleanup_problem(probe_id)
                if problem:
                    self._report_cleanup_refused(probe_id, *problem)
                    return
                state, claim, _ = read_claim(self.claim_path)
                if state != "ok" or claim.get("probe_id") != probe_id or claim.get("state") != "accepted":
                    self._report_cleanup_refused(probe_id, "probe_claim_unexpected",
                                                 "认领内容已不是本次受理的记录")
                    return
                claim = dict(claim, state="finished", outcome=outcome,
                             finished_at=time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()))
                write_claim(self.claim_path, claim)
        except ProbeRefused as exc:
            self._report_cleanup_refused(probe_id, exc.code, str(exc))
        except Exception as exc:
            self._report_cleanup_refused(probe_id, "probe_cleanup_failed",
                                         f"{type(exc).__name__}: {exc}")
        finally:
            self._accepted_identity.pop(probe_id, None)
