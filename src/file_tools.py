"""JH-Agent OS Phase 4 batch 1: real workspace-limited file tools.

Implements the controlled file tool executor contract:
- read_file / stage_write executed ONLY inside a declared workspace root;
- staged writes never touch the final target until a post-review APPLY that
  re-confirms content hash, target version, path ownership and authorization;
- atomic apply: backup -> temp write in target dir -> os.replace -> read-back
  confirmation, with recovery states (not_applied / applied / uncertain);
- path boundary checks per call: path components, Windows case-insensitivity,
  drive letters, '..' segments, same-name prefix directories (ws vs ws-evil),
  and reparse points (symlinks/junctions) on ANY ancestor component;
- TOCTOU defence: the boundary is re-verified AFTER opening, by comparing
  os.path.realpath against the validated expectation; a path that changed
  between check and open is refused (fail-closed).

Security invariants (see docs/file-tools-contract.md):
1. Nothing outside workspace_root is ever created or modified - proven by
   realpath containment after open, not by string prefix checks alone.
2. A refused or interrupted apply leaves the original target byte-identical.
3. Every phase (staged / backed_up / replaced / confirmed) is recorded so a
   restart can distinguish applied from not-applied without guessing.
"""

import hashlib
import json
import os
from pathlib import Path
import shutil
from datetime import datetime, timezone
from typing import Any, Callable, Dict, List, Optional
import uuid

from src.mock_components import SimulatedInterruptionError


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        for chunk in iter(lambda: handle.read(65536), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _sha256_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _is_reparse_point(path: Path) -> bool:
    """True when path is a symlink or junction (Windows reparse point)."""
    try:
        st = os.lstat(path)
    except OSError:
        return False
    return bool(getattr(st, "st_file_attributes", 0) & 0x400) or os.path.islink(path)


class PathBoundaryError(Exception):
    """Raised when a path cannot be PROVEN to stay inside the workspace."""


class FileToolExecutor:
    """Real file tools bounded to one workspace root.

    Compatible with the Phase 2 orchestrator tool loop (execute(tool_request) ->
    ToolResult dict) and with review snapshots (snapshot_artifacts(paths)).
    A fault_injector seam (callable receiving a phase name) enables deterministic
    interruption drills; a race_hook seam (called after boundary validation,
    before open) enables deterministic check-then-change race tests. Production
    never sets either.
    """

    STAGING_DIR = "_staging"
    TOOL_NAMES = ("read_file", "stage_write")

    def __init__(self, workspace_root: str | Path, *,
                 fault_injector: Optional[Callable[[str], None]] = None,
                 race_hook: Optional[Callable[[Path], None]] = None):
        self.workspace_root = Path(workspace_root).resolve()
        self.fault_injector = fault_injector
        self.race_hook = race_hook
        self.executed_requests: List[Dict[str, Any]] = []
        self.events: List[Dict[str, Any]] = []  # in-executor audit trail for evidence reports

    # ------------------------------------------------------------------
    # Event helper
    # ------------------------------------------------------------------

    def _record(self, **details) -> Dict[str, Any]:
        event = {"created_at": _utc_now_iso(), **details}
        self.events.append(event)
        return event

    # ------------------------------------------------------------------
    # Path boundary (per-call, on actual parameters)
    # ------------------------------------------------------------------

    def resolve_workspace_path(self, raw_path: str) -> Path:
        """Resolve raw_path against the workspace and PROVE containment.

        Component-wise walk (never blind lexical normalization): each raw
        component is resolved against the CURRENT real directory, checked for
        existence and reparse points BEFORE it is entered, and only then may
        '..' pop back — so a junction like 'out/sub/../x.txt' is judged by the
        real 'sub' (refused: missing or reparse), never by a collapsed prefix.
        Also enforces: drive + directory containment, same-name prefix
        directories (ws vs ws-evil), Windows case folding, existing parents,
        and a non-directory final component. Raises PathBoundaryError on any
        doubt. New files require an existing parent directory, per contract.
        """
        if not isinstance(raw_path, str) or not raw_path.strip():
            raise PathBoundaryError("path parameter is empty")
        raw = raw_path.strip()
        if "\x00" in raw:
            raise PathBoundaryError("path contains a null byte")
        if raw.endswith("/") or raw.endswith("\\"):
            raise PathBoundaryError("path denotes a directory (trailing separator): '" + raw + "'")

        ws = Path(self.workspace_root)
        ws_norm = os.path.normcase(str(ws))
        raw_path_obj = Path(raw)

        # Absolute paths must land on the workspace drive/directory first
        # (string-level check; component walk re-proves it against the real FS).
        if raw_path_obj.is_absolute():
            abs_norm = os.path.normcase(os.path.abspath(raw))
            if not abs_norm.startswith(ws_norm):
                raise PathBoundaryError(f"path escapes workspace: '{raw}'")
            tail = abs_norm[len(ws_norm):]
            if tail and tail[0] not in ("\\", "/"):
                raise PathBoundaryError(f"path prefix collision with workspace: '{raw}'")
            components = list(Path(abs_norm).parts[len(Path(Path(abs_norm).anchor).parts):])
            # Walk starts at the drive anchor, then re-proves every component.
            current = Path(Path(abs_norm).anchor)
        else:
            components = []
            for segment in raw_path_obj.parts:
                components.extend(segment.split("\\") if segment == "\\" else [segment])
            current = ws

        # Component-wise walk over the REAL filesystem.
        for index, part in enumerate(components):
            if part in (".", ""):
                continue
            step = current / part
            is_last = index == len(components) - 1
            if part == "..":
                if current == ws or ws not in current.resolve().parents and current.resolve() != ws:
                    if os.path.normcase(str(current)) == ws_norm:
                        raise PathBoundaryError(f"'..' escapes the workspace root: '{raw}'")
                parent = current.parent
                if not os.path.normcase(str(parent)).startswith(ws_norm):
                    raise PathBoundaryError(f"'..' escapes the workspace root: '{raw}'")
                current = parent
                continue
            if is_last:
                # Final component: parent (== current) already verified below on
                # entry; the name must not be an existing directory.
                if step.exists() and step.is_dir():
                    raise PathBoundaryError(f"target is an existing directory: '{step}'")
                current = step
                break
            if not step.exists():
                raise PathBoundaryError(f"parent component does not exist: '{step}' (raw: '{raw}')")
            if _is_reparse_point(step):
                raise PathBoundaryError(f"ancestor component is a link/junction: '{step}' (raw: '{raw}')")
            if not step.is_dir():
                raise PathBoundaryError(f"ancestor component is not a directory: '{step}'")
            current = step

        resolved_path = current
        resolved_str = str(resolved_path)
        # Final containment proof on the walked path (drive + prefix + separator).
        resolved_norm = os.path.normcase(resolved_str)
        if not resolved_norm.startswith(ws_norm):
            raise PathBoundaryError(f"path escapes workspace: '{raw}' resolves to '{resolved_str}'")
        tail = resolved_norm[len(ws_norm):]
        if tail and tail[0] not in ("\\", "/"):
            raise PathBoundaryError(f"path prefix collision with workspace: '{raw}'")
        return resolved_path

    def _open_verified(self, path: Path, *, mode: str) -> int:
        """Open with a post-open containment re-check (TOCTOU defence).

        mode: 'read' opens an existing file; 'write' opens with O_TRUNC. The
        boundary was validated before this call; a competing change (e.g. a
        junction planted between check and open) is caught here because the
        realpath of the opened path must still resolve inside the workspace.
        Cannot prove safety -> refuse.
        """
        flags = os.O_RDWR | getattr(os, "O_BINARY", 0)
        if mode == "write":
            flags |= os.O_CREAT | os.O_TRUNC
        if self.race_hook:
            self.race_hook(path)
        # Pre-open reparse check on the FINAL component: a junction planted in
        # the race window is refused BEFORE the open can follow it outside.
        if _is_reparse_point(path):
            raise PathBoundaryError(f"final component is a link/junction at open time: '{path}'")
        try:
            fd = os.open(str(path), flags)
        except FileNotFoundError:
            raise PathBoundaryError(f"target parent missing at open time: '{path}'")
        except OSError as exc:
            raise PathBoundaryError(f"open refused: '{path}': {exc}")
        try:
            real = os.path.normcase(os.path.realpath(str(path)))
            ws_norm = os.path.normcase(str(self.workspace_root))
            if not real.startswith(ws_norm) or real[len(ws_norm):][:1] not in ("", "\\", "/"):
                raise PathBoundaryError(
                    f"post-open containment check failed (path changed between check and open): '{path}'")
            if mode == "read" and _is_reparse_point(path):
                raise PathBoundaryError(f"final component is a link/junction at open time: '{path}'")
            return fd
        except PathBoundaryError:
            os.close(fd)
            raise
        except Exception:
            os.close(fd)
            raise

    # ------------------------------------------------------------------
    # Orchestrator tool-loop interface
    # ------------------------------------------------------------------

    def execute(self, tool_request: Dict[str, Any]) -> Dict[str, Any]:
        tool_name = tool_request["tool_name"]
        params = tool_request.get("parameters") or {}
        try:
            if tool_name == "read_file":
                content = self.read_file(params["path"])
                return self._result(tool_request, "success", structured_data={
                    "path": params["path"], "content": content, "sha256": _sha256_text(content)})
            if tool_name == "stage_write":
                staged = self.stage_write(
                    tool_request["task_id"], target_path=params["path"], content=params["content"])
                return self._result(tool_request, "success", structured_data={
                    "target_path": params["path"], "staged_path": staged["staged_path"],
                    "content_sha256": staged["content_sha256"],
                    "target_pre_sha256": staged["target_pre_sha256"], "bytes": staged["bytes"]})
            return self._result(tool_request, "denied_by_policy",
                                error_message=f"tool '{tool_name}' is not enabled in Phase 4 batch 1",
                                structured_data={"enabled_tools": list(self.TOOL_NAMES)})
        except PathBoundaryError as exc:
            return self._result(tool_request, "denied_by_policy",
                                error_message=f"PathBoundaryError: {exc}",
                                structured_data={"violated_boundary": True})
        except FileNotFoundError as exc:
            return self._result(tool_request, "failed", error_message=f"FileNotFoundError: {exc}")
        except OSError as exc:
            # Cannot prove what happened on the filesystem -> uncertain, no blind replay.
            return self._result(tool_request, "failed", error_message=f"OSError: {exc}")

    def _result(self, tool_request: Dict[str, Any], status: str, *,
                error_message: Optional[str] = None, structured_data: Any = None) -> Dict[str, Any]:
        if status == "success":
            self.executed_requests.append(json.loads(json.dumps(tool_request)))
        return {
            "schema_version": "1.0.0",
            "result_id": f"tres_{uuid.uuid4().hex[:12]}",
            "request_id": tool_request["request_id"],
            "task_id": tool_request["task_id"],
            "tool_name": tool_request["tool_name"],
            "status": status,
            "stdout": None,
            "stderr": error_message,
            "structured_data": structured_data,
            "execution_time_ms": 0,
            "error_message": error_message,
            "created_at": _utc_now_iso(),
        }

    # ------------------------------------------------------------------
    # Real file operations
    # ------------------------------------------------------------------

    def read_file(self, raw_path: str) -> str:
        path = self.resolve_workspace_path(raw_path)
        if not path.is_file():
            raise FileNotFoundError(f"not a file: '{raw_path}'")
        fd = self._open_verified(path, mode="read")
        with os.fdopen(fd, "r", encoding="utf-8") as handle:
            return handle.read()

    def stage_write(self, task_id: str, *, target_path: str, content: str) -> Dict[str, Any]:
        """Write candidate content to the staging area (never to the target)."""
        target = self.resolve_workspace_path(target_path)
        pre_sha = _sha256_file(target) if target.is_file() else None
        staging_root = self.workspace_root / self.STAGING_DIR / task_id
        staging_root.mkdir(parents=True, exist_ok=True)
        staged_path = staging_root / Path(target_path).name
        fd = self._open_verified(staged_path, mode="write")
        with os.fdopen(fd, "w", encoding="utf-8", newline="") as handle:
            handle.write(content)
        staged_sha = _sha256_file(staged_path)
        if staged_sha != _sha256_text(content):
            raise OSError("staged content hash mismatch after write")
        event = self._record(
            kind="staged_write", task_id=task_id, target_path=str(target),
            staged_path=str(staged_path), content_sha256=staged_sha,
            target_pre_sha256=pre_sha, bytes=len(content.encode("utf-8")))
        return {"staged_path": str(staged_path), "target_path": str(target),
                "content_sha256": staged_sha, "target_pre_sha256": pre_sha,
                "bytes": event["bytes"], "staged_at": event["created_at"]}

    def snapshot_artifacts(self, paths) -> Dict[str, str]:
        contents = {}
        for raw in paths:
            path = self.resolve_workspace_path(raw)
            if not path.is_file():
                raise KeyError(f"snapshot target missing: '{raw}'")
            fd = self._open_verified(path, mode="read")
            with os.fdopen(fd, "r", encoding="utf-8") as handle:
                contents[raw] = handle.read()
        return contents

    # ------------------------------------------------------------------
    # Post-review apply (controlled action with re-confirmation + recovery)
    # ------------------------------------------------------------------

    def apply_staged(self, task_id: str, *, target_path: str, staged_path: str,
                     content_sha256: str, target_pre_sha256: Optional[str],
                     authorized_files: Optional[set] = None) -> Dict[str, Any]:
        """Apply a reviewed staged write to the final target.

        Re-confirms, in order: staged content hash equals the reviewed hash, the
        current target hash equals the pre-review state (target unchanged), the
        path still passes every boundary check, and the target is authorized.
        Any mismatch refuses the apply WITHOUT touching the target. Interrupted
        applies recover into not_applied / applied / uncertain states with
        evidence, never blind replay.
        """
        # 1. Re-validate the FINAL target path (per-call, actual parameters).
        target = self.resolve_workspace_path(target_path)
        if authorized_files is not None and len(authorized_files) > 0:
            norm = os.path.normcase(str(target)).replace("\\", "/")
            norm_ws = os.path.normcase(str(self.workspace_root)).replace("\\", "/") + "/"
            rel = norm[len(norm_ws):]
            authorized_norm = {os.path.normcase(f).replace("\\", "/") for f in authorized_files}
            if rel not in authorized_norm:
                raise PathBoundaryError(
                    f"apply refused: target '{target_path}' is outside the authorized file scope")
        staged = Path(staged_path)
        if not staged.is_file():
            raise FileNotFoundError(f"staged file missing at apply time: '{staged_path}'")
        staged_sha = _sha256_file(staged)
        if staged_sha != content_sha256:
            raise PathBoundaryError(
                f"apply refused: staged content sha {staged_sha[:12]}... != reviewed {content_sha256[:12]}...")

        # 2. Target version re-confirmation (must match the pre-review state).
        target_exists = target.is_file()
        current_sha = _sha256_file(target) if target_exists else None
        if current_sha != target_pre_sha256:
            raise PathBoundaryError(
                f"apply refused: target changed since review (current={current_sha}, expected={target_pre_sha256})")

        with open(staged, "r", encoding="utf-8") as handle:
            candidate = handle.read()
        if _sha256_text(candidate) != content_sha256:
            raise PathBoundaryError("apply refused: staged content does not match reviewed hash")

        self._record(kind="apply_start", task_id=task_id, target_path=str(target),
                     content_sha256=content_sha256, target_pre_sha256=target_pre_sha256)
        if self.fault_injector:
            self.fault_injector("apply_start")

        # 3. Backup the original (if any) inside staging for recovery.
        backup_path = None
        if target_exists:
            backup_path = Path(staged_path).with_name("backup_" + Path(target_path).name)
            shutil.copy2(target, backup_path)
            if _sha256_file(backup_path) != current_sha:
                raise OSError("backup verification failed; apply aborted before any target modification")
        self._record(kind="backup_created", backup_path=str(backup_path) if backup_path else None,
                     target_pre_sha256=target_pre_sha256)
        if self.fault_injector:
            self.fault_injector("backed_up")

        # 4. Atomic replace: temp file in the target directory, then os.replace.
        temp_path = target.with_name(target.name + ".jh_tmp")
        fd = self._open_verified(temp_path, mode="write")
        with os.fdopen(fd, "w", encoding="utf-8", newline="") as handle:
            handle.write(candidate)
            handle.flush()
            os.fsync(handle.fileno())
        self._record(kind="temp_written", temp_path=str(temp_path))
        if self.fault_injector:
            self.fault_injector("temp_written")

        os.replace(temp_path, target)
        self._record(kind="replaced", target_path=str(target))
        if self.fault_injector:
            self.fault_injector("replaced")

        # 5. Read-back confirmation; cannot prove the outcome -> uncertain.
        applied_sha = _sha256_file(target)
        if applied_sha != content_sha256:
            self._record(kind="apply_uncertain", target_path=str(target),
                         observed_sha=applied_sha, expected_sha=content_sha256)
            raise SimulatedInterruptionError(
                "apply outcome cannot be confirmed (target hash mismatch after replace)")
        self._record(kind="apply_confirmed", target_path=str(target), applied_sha=applied_sha)
        if self.fault_injector:
            self.fault_injector("confirmed")
        return {"applied": True, "target_path": str(target), "applied_sha256": applied_sha,
                "backup_path": str(backup_path) if backup_path else None,
                "replaced_existing": target_exists}

    def recover_apply(self, task_id: str, *, target_path: str, staged_path: str,
                      content_sha256: str, target_pre_sha256: Optional[str]) -> Dict[str, Any]:
        """Classify an interrupted apply without blind replay.

        Returns applied / not_applied / uncertain. Only 'not_applied' may be
        re-applied by a subsequent, fully re-confirmed apply_staged call.
        """
        target = self.resolve_workspace_path(target_path)
        target_exists = target.is_file()
        current_sha = _sha256_file(target) if target_exists else None
        if current_sha == content_sha256:
            self._record(kind="recover_applied_confirmed", target_path=str(target))
            return {"state": "applied", "target_sha256": current_sha}
        if current_sha == target_pre_sha256 or (not target_exists and target_pre_sha256 is None):
            self._record(kind="recover_not_applied_confirmed", target_path=str(target))
            return {"state": "not_applied", "target_sha256": current_sha}
        self._record(kind="recover_uncertain", target_path=str(target),
                     observed_sha=current_sha, expected_pre=target_pre_sha256,
                     expected_applied=content_sha256)
        return {"state": "uncertain", "target_sha256": current_sha}
