"""JH-Agent OS Phase 4 batch 1 (JH-015 hardened): real workspace-limited file tools.

Implements the controlled file tool executor contract:
- read_file / stage_write executed ONLY inside a declared workspace root;
- staged writes never touch the final target until a post-review APPLY that
  re-confirms content hash, target version, path ownership and authorization;
- handle-pinned atomic apply: every ancestor and staging directory is verified
  against reparse points before creation/open; opens never pass O_TRUNC before
  containment and identity are proven; temporary/backup files use unique names
  with O_CREAT|O_EXCL; final commit locks the target descriptor, re-verifies
  realpath containment and pre-review content hash on the open handle, and only
  then writes/truncates.
"""

from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import shutil
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


def _lock_fd(fd: int) -> None:
    if os.name == "nt":
        import msvcrt
        os.lseek(fd, 0, os.SEEK_SET)
        try:
            msvcrt.locking(fd, msvcrt.LK_NBLCK, 1)
        except OSError as exc:
            raise PathBoundaryError(f"cannot acquire exclusive lock on target handle: {exc}") from None


def _unlock_fd(fd: int) -> None:
    if os.name == "nt":
        import msvcrt
        try:
            os.lseek(fd, 0, os.SEEK_SET)
            msvcrt.locking(fd, msvcrt.LK_UNLCK, 1)
        except OSError:
            pass


class PathBoundaryError(Exception):
    """Raised when a path cannot be PROVEN to stay inside the workspace."""


class FileToolExecutor:
    """Real file tools bounded to one workspace root."""

    STAGING_DIR = "_staging"
    TOOL_NAMES = ("read_file", "stage_write")

    def __init__(self, workspace_root: str | Path, *,
                 fault_injector: Optional[Callable[[str], None]] = None,
                 race_hook: Optional[Callable[[Path], None]] = None):
        raw_ws = Path(workspace_root)
        # R1: reject a junction/symlink workspace BEFORE resolve() can silently
        # dereference it into a different real directory.
        if _is_reparse_point(raw_ws):
            raise PathBoundaryError(f"workspace root is a link/junction: '{raw_ws}'")
        self.workspace_root = raw_ws.resolve()
        if _is_reparse_point(self.workspace_root):
            raise PathBoundaryError(f"workspace root resolves to a link/junction: '{self.workspace_root}'")
        self.fault_injector = fault_injector
        self.race_hook = race_hook
        self.executed_requests: List[Dict[str, Any]] = []
        self.events: List[Dict[str, Any]] = []

    def _record(self, **details) -> Dict[str, Any]:
        event = {"created_at": _utc_now_iso(), **details}
        self.events.append(event)
        return event

    def _assert_realpath_inside_workspace(self, candidate: Path, label: str) -> None:
        ws_real = os.path.normcase(os.path.realpath(str(self.workspace_root)))
        cand_real = os.path.normcase(os.path.realpath(str(candidate)))
        if not cand_real.startswith(ws_real) or cand_real[len(ws_real):][:1] not in ("", "\\", "/"):
            raise PathBoundaryError(
                f"post-open containment check failed ({label} escapes workspace): '{candidate}'")

    def _ensure_safe_dir(self, rel_parts: List[str]) -> Path:
        """Walk and create internal directories component-by-component, refusing any junction/symlink."""
        ws = Path(self.workspace_root)
        if not ws.exists() or _is_reparse_point(ws) or not ws.is_dir():
            raise PathBoundaryError(f"workspace root is invalid or a link/junction: '{ws}'")
        self._assert_realpath_inside_workspace(ws, "workspace_root")
        current = ws
        for part in rel_parts:
            if not part or part in (".", "..") or "/" in part or "\\" in part:
                raise PathBoundaryError(f"invalid internal directory component: '{part}'")
            step = current / part
            if os.path.lexists(str(step)):
                if _is_reparse_point(step):
                    raise PathBoundaryError(f"ancestor component is a link/junction: '{step}'")
                if not step.is_dir():
                    raise PathBoundaryError(f"ancestor component is not a directory: '{step}'")
            else:
                os.mkdir(str(step))
                if _is_reparse_point(step) or not step.is_dir():
                    raise PathBoundaryError(f"created directory became a link/junction: '{step}'")
            self._assert_realpath_inside_workspace(step, "internal_dir")
            current = step
        return current

    # ------------------------------------------------------------------
    # Path boundary (per-call, on actual parameters)
    # ------------------------------------------------------------------

    def resolve_workspace_path(self, raw_path: str) -> Path:
        """Resolve raw_path against the workspace and PROVE containment."""
        if not isinstance(raw_path, str) or not raw_path.strip():
            raise PathBoundaryError("path parameter is empty")
        raw = raw_path.strip()
        if "\x00" in raw:
            raise PathBoundaryError("path contains a null byte")
        if raw.endswith("/") or raw.endswith("\\"):
            raise PathBoundaryError("path denotes a directory (trailing separator): '" + raw + "'")

        ws = Path(self.workspace_root)
        if _is_reparse_point(ws):
            raise PathBoundaryError(f"workspace root is a link/junction: '{ws}'")
        ws_norm = os.path.normcase(str(ws))
        raw_path_obj = Path(raw)

        if raw_path_obj.is_absolute():
            abs_norm = os.path.normcase(os.path.abspath(raw))
            if not abs_norm.startswith(ws_norm):
                raise PathBoundaryError(f"path escapes workspace: '{raw}'")
            tail = abs_norm[len(ws_norm):]
            if tail and tail[0] not in ("\\", "/"):
                raise PathBoundaryError(f"path prefix collision with workspace: '{raw}'")
            components = list(Path(abs_norm).parts[len(Path(Path(abs_norm).anchor).parts):])
            current = Path(Path(abs_norm).anchor)
        else:
            components = []
            for segment in raw_path_obj.parts:
                components.extend(segment.split("\\") if segment == "\\" else [segment])
            current = ws

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
                if os.path.lexists(str(step)) and _is_reparse_point(step):
                    raise PathBoundaryError(f"final component is a link/junction: '{step}' (raw: '{raw}')")
                if step.exists() and step.is_dir():
                    raise PathBoundaryError(f"target is an existing directory: '{step}'")
                current = step
                break
            if not os.path.lexists(str(step)):
                raise PathBoundaryError(f"parent component does not exist: '{step}' (raw: '{raw}')")
            if _is_reparse_point(step):
                raise PathBoundaryError(f"ancestor component is a link/junction: '{step}' (raw: '{raw}')")
            if not step.is_dir():
                raise PathBoundaryError(f"ancestor component is not a directory: '{step}'")
            current = step

        resolved_path = current
        resolved_str = str(resolved_path)
        resolved_norm = os.path.normcase(resolved_str)
        if not resolved_norm.startswith(ws_norm):
            raise PathBoundaryError(f"path escapes workspace: '{raw}' resolves to '{resolved_str}'")
        tail = resolved_norm[len(ws_norm):]
        if tail and tail[0] not in ("\\", "/"):
            raise PathBoundaryError(f"path prefix collision with workspace: '{raw}'")
        self._assert_realpath_inside_workspace(resolved_path.parent, "resolved_parent")
        return resolved_path

    def _open_verified(self, path: Path, *, mode: str) -> int:
        """Open without pre-verification truncation (ARCH-01 & TOCTOU defence).

        mode:
          - 'read': open existing file O_RDONLY
          - 'write': open O_RDWR|O_CREAT (NO O_TRUNC before verification; truncates
            via os.ftruncate ONLY after post-open containment and reparse checks pass)
          - 'create_exclusive': open O_RDWR|O_CREAT|O_EXCL (never overwrites existing)
          - 'read_write_existing': open existing file O_RDWR without O_CREAT or O_TRUNC
        """
        resolved = self.resolve_workspace_path(str(path))
        if self.race_hook:
            self.race_hook(resolved)
        # Re-check all ancestors and final component AFTER any race window, BEFORE os.open!
        if _is_reparse_point(resolved) or _is_reparse_point(resolved.parent):
            raise PathBoundaryError(f"final or parent component is a link/junction at open time: '{resolved}'")
        self.resolve_workspace_path(str(resolved))

        binary_flag = getattr(os, "O_BINARY", 0)
        if mode == "read":
            flags = os.O_RDONLY | binary_flag
        elif mode == "create_exclusive":
            flags = os.O_RDWR | os.O_CREAT | os.O_EXCL | binary_flag
        elif mode == "read_write_existing":
            flags = os.O_RDWR | binary_flag
        elif mode == "write":
            flags = os.O_RDWR | os.O_CREAT | binary_flag
        else:
            raise ValueError(f"unknown open mode: {mode}")

        existed_before = os.path.lexists(str(resolved))
        try:
            fd = os.open(str(resolved), flags)
        except FileExistsError:
            raise PathBoundaryError(f"exclusive create refused (file already exists): '{resolved}'") from None
        except FileNotFoundError:
            raise PathBoundaryError(f"target parent missing at open time: '{resolved}'") from None
        except OSError as exc:
            raise PathBoundaryError(f"open refused: '{resolved}': {exc}") from None

        try:
            real = os.path.normcase(os.path.realpath(str(resolved)))
            ws_norm = os.path.normcase(os.path.realpath(str(self.workspace_root)))
            if not real.startswith(ws_norm) or real[len(ws_norm):][:1] not in ("", "\\", "/"):
                raise PathBoundaryError(
                    f"post-open containment check failed (path changed between check and open): '{resolved}'")
            if _is_reparse_point(resolved) or _is_reparse_point(resolved.parent):
                raise PathBoundaryError(f"final component is a link/junction at open time: '{resolved}'")
            if mode == "write":
                os.ftruncate(fd, 0)
            return fd
        except Exception:
            os.close(fd)
            if not existed_before:
                try:
                    if os.path.lexists(str(resolved)) and not _is_reparse_point(resolved):
                        os.unlink(str(resolved))
                except OSError:
                    pass
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
        """Write candidate content to the verified staging area (never to the target)."""
        target = self.resolve_workspace_path(target_path)
        pre_sha = _sha256_file(target) if target.is_file() else None
        staging_root = self._ensure_safe_dir([self.STAGING_DIR, task_id])
        # Unique staged name per call: two targets sharing a basename (or two
        # stage_write calls for one target) must never silently overwrite each
        # other's staged bytes. The recorded staged_path is authoritative.
        staged_path = staging_root / f"{uuid.uuid4().hex}_{Path(target_path).name}"
        fd = self._open_verified(staged_path, mode="create_exclusive")
        with os.fdopen(fd, "w", encoding="utf-8", newline="") as handle:
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
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
    # Post-review apply (handle-pinned, version-reconfirmed, collision-free)
    # ------------------------------------------------------------------

    def apply_staged(self, task_id: str, *, target_path: str, staged_path: str,
                     content_sha256: str, target_pre_sha256: Optional[str],
                     authorized_files: Optional[set] = None) -> Dict[str, Any]:
        """Apply a reviewed staged write to the final target."""
        # 1. Re-validate FINAL target path and staged path.
        target = self.resolve_workspace_path(target_path)
        if authorized_files is not None and len(authorized_files) > 0:
            norm = os.path.normcase(str(target)).replace("\\", "/")
            norm_ws = os.path.normcase(str(self.workspace_root)).replace("\\", "/") + "/"
            rel = norm[len(norm_ws):]
            authorized_norm = {os.path.normcase(f).replace("\\", "/") for f in authorized_files}
            if rel not in authorized_norm:
                raise PathBoundaryError(
                    f"apply refused: target '{target_path}' is outside the authorized file scope")
        staged = self.resolve_workspace_path(staged_path)
        if not staged.is_file():
            raise FileNotFoundError(f"staged file missing at apply time: '{staged_path}'")
        staged_sha = _sha256_file(staged)
        if staged_sha != content_sha256:
            raise PathBoundaryError(
                f"apply refused: staged content sha {staged_sha[:12]}... != reviewed {content_sha256[:12]}...")

        # 2. Initial target version re-confirmation (must match the pre-review state).
        target_exists = target.is_file()
        current_sha = _sha256_file(target) if target_exists else None
        if current_sha != target_pre_sha256:
            raise PathBoundaryError(
                f"apply refused: target changed since review (current={current_sha}, expected={target_pre_sha256})")

        candidate = self.read_file(str(staged))
        if _sha256_text(candidate) != content_sha256:
            raise PathBoundaryError("apply refused: staged content does not match reviewed hash")
        candidate_bytes = candidate.encode("utf-8")

        self._record(kind="apply_start", task_id=task_id, target_path=str(target),
                     content_sha256=content_sha256, target_pre_sha256=target_pre_sha256)
        if self.fault_injector:
            self.fault_injector("apply_start")

        staging_dir = self._ensure_safe_dir([self.STAGING_DIR, task_id])

        # 3. Backup the original (if any) inside verified staging with an exclusive name.
        backup_path = None
        if target_exists:
            backup_path = staging_dir / f"backup_{uuid.uuid4().hex[:12]}_{Path(target_path).name}"
            # Verified read: refuse (before any backup/commit write) if the
            # target path no longer resolves inside the workspace.
            bsrc = self._open_verified(target, mode="read")
            with os.fdopen(bsrc, "rb") as bhandle:
                orig_bytes = bhandle.read()
            if hashlib.sha256(orig_bytes).hexdigest() != current_sha:
                raise PathBoundaryError("apply refused: target changed while creating backup")
            bfd = self._open_verified(backup_path, mode="create_exclusive")
            with os.fdopen(bfd, "wb") as bhandle:
                bhandle.write(orig_bytes)
                bhandle.flush()
                os.fsync(bhandle.fileno())
            if _sha256_file(backup_path) != current_sha:
                raise OSError("backup verification failed; apply aborted before any target modification")
        self._record(kind="backup_created", backup_path=str(backup_path) if backup_path else None,
                     target_pre_sha256=target_pre_sha256)
        if self.fault_injector:
            self.fault_injector("backed_up")

        # 4. Isolated, uniquely named exclusive temp file inside staging (ARCH-02: never touches neighbor files).
        temp_path = staging_dir / f".tmp_{uuid.uuid4().hex}_{target.name}"
        tfd = self._open_verified(temp_path, mode="create_exclusive")
        try:
            with os.fdopen(tfd, "wb") as thandle:
                thandle.write(candidate_bytes)
                thandle.flush()
                os.fsync(thandle.fileno())
            self._record(kind="temp_written", temp_path=str(temp_path))
            if self.fault_injector:
                self.fault_injector("temp_written")

            # 5. Handle-pinned, version-reconfirmed commit (ARCH-03 & ARCH-04).
            # Re-resolve target path immediately before opening handle to catch any parent junction swap.
            target_now = self.resolve_workspace_path(target_path)
            if _sha256_file(temp_path) != content_sha256:
                raise PathBoundaryError("apply refused: temp file hash mismatch before commit")

            if target_pre_sha256 is None:
                # Target must not exist prior to commit: open with O_CREAT|O_EXCL.
                if os.path.lexists(str(target_now)):
                    raise PathBoundaryError(
                        f"apply refused: target changed since review (file appeared concurrently: '{target_now}')")
                cfd = self._open_verified(target_now, mode="create_exclusive")
                try:
                    _lock_fd(cfd)
                    try:
                        self._assert_realpath_inside_workspace(target_now, "commit_new_target")
                        os.lseek(cfd, 0, os.SEEK_SET)
                        os.write(cfd, candidate_bytes)
                        os.ftruncate(cfd, len(candidate_bytes))
                        os.fsync(cfd)
                    finally:
                        _unlock_fd(cfd)
                finally:
                    os.close(cfd)
            else:
                # Target must exist and still match target_pre_sha256 on the locked handle.
                if not target_now.is_file():
                    raise PathBoundaryError(
                        f"apply refused: target changed since review (target removed concurrently: '{target_now}')")
                cfd = self._open_verified(target_now, mode="read_write_existing")
                try:
                    _lock_fd(cfd)
                    try:
                        self._assert_realpath_inside_workspace(target_now, "commit_existing_target")
                        os.lseek(cfd, 0, os.SEEK_SET)
                        live_chunks = []
                        while True:
                            chunk = os.read(cfd, 65536)
                            if not chunk:
                                break
                            live_chunks.append(chunk)
                        live_sha = hashlib.sha256(b"".join(live_chunks)).hexdigest()
                        if live_sha != target_pre_sha256:
                            raise PathBoundaryError(
                                f"apply refused: target changed since review "
                                f"(current={live_sha}, expected={target_pre_sha256})")
                        os.lseek(cfd, 0, os.SEEK_SET)
                        os.write(cfd, candidate_bytes)
                        os.ftruncate(cfd, len(candidate_bytes))
                        os.fsync(cfd)
                    finally:
                        _unlock_fd(cfd)
                finally:
                    os.close(cfd)
        finally:
            try:
                if os.path.lexists(str(temp_path)) and not _is_reparse_point(temp_path):
                    os.unlink(str(temp_path))
            except OSError:
                pass

        self._record(kind="replaced", target_path=str(target))
        if self.fault_injector:
            self.fault_injector("replaced")

        # 6. Read-back confirmation; cannot prove the outcome -> uncertain.
        verified_target = self.resolve_workspace_path(target_path)
        applied_sha = _sha256_file(verified_target)
        if applied_sha != content_sha256:
            self._record(kind="apply_uncertain", target_path=str(verified_target),
                         observed_sha=applied_sha, expected_sha=content_sha256)
            raise SimulatedInterruptionError(
                "apply outcome cannot be confirmed (target hash mismatch after replace)")
        self._record(kind="apply_confirmed", target_path=str(verified_target), applied_sha=applied_sha)
        if self.fault_injector:
            self.fault_injector("confirmed")
        return {"applied": True, "target_path": str(verified_target), "applied_sha256": applied_sha,
                "backup_path": str(backup_path) if backup_path else None,
                "replaced_existing": target_exists}

    def recover_apply(self, task_id: str, *, target_path: str, staged_path: str,
                      content_sha256: str, target_pre_sha256: Optional[str]) -> Dict[str, Any]:
        """Classify an interrupted apply without blind replay."""
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
