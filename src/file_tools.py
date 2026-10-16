"""JH-Agent OS Phase 4 batch 1 (JH-015 hardened): real workspace-limited file tools.

Implements the controlled file tool executor contract:
- read_file / stage_write executed ONLY inside a declared workspace root;
- staged writes never touch the final target until a post-review APPLY that
  re-confirms content hash, target version, path ownership and authorization;
- parent-pinned publish (B2-01/02/03, see docs/file-tools-apply-mechanism.md):
  the candidate is fully written to an isolated exclusive temp file with
  short-write looping, hash-verified and fsynced BEFORE any target mutation.
  The final target is never modified in place. A share-READ handle on the
  target PARENT directory (_DirGuard) pins entries from temp verification
  through publish: entry create/rename/delete races fail loudly instead of
  slipping through, and the parent itself cannot be renamed away mid-publish.
  The target FILE guard (_TargetGuard, share READ without delete-share)
  proves version/identity and blocks new writer opens, then is released
  adjacent to the publish syscall (a held file guard is proven incompatible
  with our own replace on this host); a retained old-object reader detects
  final-adjacency drift, which reports uncertain instead of succeeding.
  New files publish with os.rename (atomic, fails when the destination
  appeared concurrently); existing targets publish with os.replace (a new
  directory entry, so hardlink aliases keep the old bytes). Any failure
  before publish leaves the old target byte-identical; rename/replace
  failure leaves it untouched. Multi-link targets are refused before
  publish; non-Windows and non-NTFS volumes are refused outright.
  Residuals are documented in the mechanism note, not claimed away.
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


def _write_all(fd: int, data: bytes) -> None:
    """Write every byte, looping over short writes (JH016-01).

    A short count is retried from the current offset; zero progress with
    bytes remaining is an I/O error, never silently accepted. Callers must
    fsync afterwards and hash-verify the file object.
    """
    done = 0
    total = len(data)
    while done < total:
        written = os.write(fd, data[done:])
        if written == 0:
            raise OSError("short write made no progress while staging candidate")
        done += written


def _volume_fs_name(path: Path) -> str:
    """Filesystem name (e.g. 'NTFS') of the volume holding path, Windows only."""
    import ctypes
    k32 = ctypes.windll.kernel32
    k32.GetVolumePathNameW.argtypes = [ctypes.c_wchar_p, ctypes.c_wchar_p, ctypes.c_ulong]
    k32.GetVolumePathNameW.restype = ctypes.c_bool
    k32.GetVolumeInformationW.argtypes = [
        ctypes.c_wchar_p, ctypes.c_wchar_p, ctypes.c_ulong,
        ctypes.POINTER(ctypes.c_ulong), ctypes.POINTER(ctypes.c_ulong),
        ctypes.POINTER(ctypes.c_ulong), ctypes.c_wchar_p, ctypes.c_ulong]
    k32.GetVolumeInformationW.restype = ctypes.c_bool
    anchor = ctypes.create_unicode_buffer(260)
    if not k32.GetVolumePathNameW(str(path), anchor, 260):
        raise PathBoundaryError(f"apply refused: cannot locate publish volume for '{path}'")
    fs_name = ctypes.create_unicode_buffer(32)
    if not k32.GetVolumeInformationW(anchor.value, None, 0, None, None, None, fs_name, 32):
        raise PathBoundaryError(f"apply refused: cannot identify publish filesystem for '{path}'")
    return fs_name.value


def _require_publish_volume(path: Path) -> None:
    """Gate the publish mechanism to its proven support scope (R4).

    Only Windows NTFS is accepted: the share-mode guards and the atomic
    same-volume rename/replace premises were proven there (see the share
    matrix report). Anything else is refused BEFORE any side effect.
    """
    if os.name != "nt":
        raise PathBoundaryError(
            f"apply refused: atomic guarded publish requires Windows (host is '{os.name}')")
    fs_name = _volume_fs_name(path)
    if fs_name.upper() != "NTFS":
        raise PathBoundaryError(
            f"apply refused: guarded publish is proven only on NTFS (volume holds '{fs_name}')")


def _lock_fd(fd: int) -> None:
    # Legacy single-byte helper retained for import compatibility only; the
    # JH016 commit path uses _TargetGuard (share-mode guard), never this lock.
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


class _TargetGuard:
    """Share-mode guard over the on-disk target (Windows) for JH016-03/B2-02.

    Holds a GENERIC_READ handle with dwShareMode=FILE_SHARE_READ only (no
    delete-share), from the final version/identity verification until just
    before the publish syscall. While held, any NEW writer open
    (GENERIC_WRITE / O_RDWR / O_WRONLY) and any rename-away/delete open of
    the same file object fails with a sharing violation: concurrent edits
    fail loudly instead of being silently overwritten. Plain reads through
    other handles still succeed.

    The guard is released adjacent to the publish syscall because a held
    file guard is proven incompatible with our own os.replace on this host
    (B2-01, WinError 5); entry races during and after release stay blocked
    by the parent _DirGuard, and a detached old-object reader (see
    detached_reader) detects final-adjacency drift afterwards.

    R1 correction: a pre-opened ordinary O_RDWR handle makes guard
    ACQUISITION fail (sharing compatibility is bidirectional). That failure
    is fail-closed and protective: we refuse instead of proceeding
    unguarded. Other handle/mapping kinds (memory-mapped sections, backup
    handles, filter drivers) were not exhaustively tested and stay marked
    unverified in the mechanism note.

    Identity is read through the GUARD HANDLE (never by path string) via
    GetFileInformationByHandleEx: FileIdInfo (class 18) gives the stable
    (volume, 128-bit file id); FileStandardInfo (class 1) gives link count
    and end-of-file size. Content sha is read through a duplicated handle.
    """

    def __init__(self, target: Path, expected_pre_sha256: Optional[str]):
        if os.name != "nt":
            raise PathBoundaryError("target guard requires Windows share-mode semantics")
        import ctypes
        k32 = ctypes.windll.kernel32
        k32.GetFileInformationByHandleEx.argtypes = [
            ctypes.c_void_p, ctypes.c_int, ctypes.c_void_p, ctypes.c_ulong]
        k32.GetFileInformationByHandleEx.restype = ctypes.c_bool
        k32.CreateFileW.argtypes = [
            ctypes.c_wchar_p, ctypes.c_ulong, ctypes.c_ulong,
            ctypes.c_void_p, ctypes.c_ulong, ctypes.c_ulong, ctypes.c_void_p]
        k32.CreateFileW.restype = ctypes.c_void_p
        k32.CloseHandle.argtypes = [ctypes.c_void_p]
        k32.CloseHandle.restype = ctypes.c_bool
        k32.DuplicateHandle.argtypes = [
            ctypes.c_void_p, ctypes.c_void_p, ctypes.c_void_p,
            ctypes.POINTER(ctypes.c_void_p), ctypes.c_ulong, ctypes.c_bool, ctypes.c_ulong]
        k32.DuplicateHandle.restype = ctypes.c_bool
        GENERIC_READ = 0x80000000
        OPEN_EXISTING = 3
        FILE_SHARE_READ_ONLY = 0x00000001
        FILE_ATTRIBUTE_NORMAL = 0x80
        INVALID_HANDLE = ctypes.c_void_p(-1).value
        handle = k32.CreateFileW(str(target), GENERIC_READ, FILE_SHARE_READ_ONLY,
                                 None, OPEN_EXISTING, FILE_ATTRIBUTE_NORMAL, None)
        if handle == INVALID_HANDLE or handle is None:
            raise PathBoundaryError(
                f"apply refused: cannot guard target (open/share denied, likely changed concurrently): '{target}'")

        self._k32 = k32
        self._handle = handle
        self.target = target
        try:
            file_id, nlink, size = self._handle_identity()
        except OSError as exc:
            k32.CloseHandle(handle)
            self._handle = None
            raise PathBoundaryError(
                f"apply refused: cannot identify guarded target object: '{target}': {exc}") from None
        self.file_id = file_id
        self.nlink = nlink
        self.size = size
        # Read content through the guard handle (duplicated CRT fd; the
        # guard handle itself stays open).
        import msvcrt
        try:
            content = self._read_through_handle(msvcrt)
        except OSError as exc:
            self.close()
            raise PathBoundaryError(
                f"apply refused: cannot read guarded target: '{target}': {exc}") from None
        self.content_sha = hashlib.sha256(content).hexdigest()
        if expected_pre_sha256 is not None and self.content_sha != expected_pre_sha256:
            self.close()
            raise PathBoundaryError(
                f"apply refused: target changed since review "
                f"(current={self.content_sha}, expected={expected_pre_sha256})")
        # The guard was opened BY PATH: prove the live path still names the
        # same object before trusting any path-based publish step.
        try:
            self.assert_path_still_names_guarded(target)
        except PathBoundaryError:
            self.close()
            raise

    def _handle_identity(self):
        """(file_id, nlink, size) read through the guard handle via Ex API."""
        import ctypes
        import struct
        id_buf = ctypes.create_string_buffer(24)  # FileIdInfo: vol u64 + id 16 bytes
        if not self._k32.GetFileInformationByHandleEx(self._handle, 18, id_buf, 24):
            raise OSError("FileIdInfo query failed")
        std_buf = ctypes.create_string_buffer(24)  # FileStandardInfo: alloc 8 + eof 8 + links u32 + flags
        if not self._k32.GetFileInformationByHandleEx(self._handle, 1, std_buf, 24):
            raise OSError("FileStandardInfo query failed")
        volume = struct.unpack_from("<Q", bytes(id_buf), 0)[0]
        file_id = (volume, bytes(id_buf)[8:24])
        size = struct.unpack_from("<q", bytes(std_buf), 8)[0]
        nlink = struct.unpack_from("<I", bytes(std_buf), 16)[0]
        return file_id, nlink, size

    def _read_through_handle(self, msvcrt) -> bytes:
        # Duplicate the guard handle first: closing a CRT fd releases its
        # underlying handle, which must NOT release the guard itself.
        import ctypes
        dup = ctypes.c_void_p()
        current = self._k32.GetCurrentProcess()
        if not self._k32.DuplicateHandle(current, self._handle, current,
                                         ctypes.byref(dup), 0, False, 0x00000002):  # DUPLICATE_SAME_ACCESS
            raise PathBoundaryError(f"apply refused: cannot duplicate guard handle: '{self.target}'")
        fd = msvcrt.open_osfhandle(dup.value, 0)
        try:
            os.lseek(fd, 0, os.SEEK_SET)
            chunks = []
            while True:
                chunk = os.read(fd, 65536)
                if not chunk:
                    break
                chunks.append(chunk)
            return b"".join(chunks)
        finally:
            # Closes the duplicate only; the guard handle stays open.
            try:
                os.close(fd)
            except OSError:
                pass

    def reverify(self, expected_pre_sha256: Optional[str]) -> None:
        """Re-prove identity + version through the GUARD HANDLE (not by path).

        Raises PathBoundaryError on any drift: different object (volume/file
        id changed), size/link-count drift, or content sha drift.
        """
        try:
            file_id, nlink, size = self._handle_identity()
        except OSError:
            raise PathBoundaryError(f"apply refused: target guard handle lost: '{self.target}'")
        if file_id != self.file_id:
            raise PathBoundaryError(f"apply refused: guarded target object changed: '{self.target}'")
        if nlink != self.nlink or size != self.size:
            raise PathBoundaryError(
                f"apply refused: target changed under guard (link count or size drift): '{self.target}'")
        # Content re-read through the guard handle via a duplicated CRT fd.
        # Any read failure fails closed.
        import hashlib as _hl
        import msvcrt
        try:
            live_sha = _hl.sha256(self._read_through_handle(msvcrt)).hexdigest()
        except OSError as exc:
            raise PathBoundaryError(f"apply refused: cannot re-read guarded target: '{self.target}'") from None
        if expected_pre_sha256 is not None and live_sha != expected_pre_sha256:
            raise PathBoundaryError(
                f"apply refused: target changed since review "
                f"(current={live_sha}, expected={expected_pre_sha256})")

    def assert_single_link(self) -> None:
        # Fresh identity on every call: a hard link created after the guard
        # opened must also refuse (JH016-02).
        try:
            file_id, nlink, size = self._handle_identity()
        except OSError:
            raise PathBoundaryError(f"apply refused: target guard handle lost: '{self.target}'")
        if file_id != self.file_id:
            raise PathBoundaryError(f"apply refused: guarded target object changed: '{self.target}'")
        self.nlink = nlink
        self.size = size
        if nlink != 1:
            raise PathBoundaryError(
                f"apply refused: target has {nlink} hard links; publishing could orphan or share bytes "
                f"with unapproved aliases: '{self.target}'")

    def assert_path_still_names_guarded(self, path: Path) -> None:
        """Prove the live path still names the guarded object (parent-swap tripwire).

        Opens a second read-only handle BY PATH and compares its FileIdInfo
        against the guard's handle identity. Any failure or mismatch fails
        closed. The second open requests only GENERIC_READ, so the guard's
        own share mode never blocks it.
        """
        import ctypes
        import struct
        GENERIC_READ = 0x80000000
        OPEN_EXISTING = 3
        FILE_SHARE_ALL = 0x00000007
        FILE_ATTRIBUTE_NORMAL = 0x80
        INVALID_HANDLE = ctypes.c_void_p(-1).value
        handle = self._k32.CreateFileW(str(path), GENERIC_READ, FILE_SHARE_ALL,
                                       None, OPEN_EXISTING, FILE_ATTRIBUTE_NORMAL, None)
        if handle == INVALID_HANDLE or handle is None:
            raise PathBoundaryError(
                f"apply refused: cannot re-open target path before publish: '{path}'")
        try:
            id_buf = ctypes.create_string_buffer(24)
            if not self._k32.GetFileInformationByHandleEx(handle, 18, id_buf, 24):
                raise PathBoundaryError(
                    f"apply refused: cannot identify pre-publish target object: '{path}'")
            volume = struct.unpack_from("<Q", bytes(id_buf), 0)[0]
            file_id = (volume, bytes(id_buf)[8:24])
        finally:
            try:
                self._k32.CloseHandle(handle)
            except OSError:
                pass
        if file_id != self.file_id:
            raise PathBoundaryError(
                f"apply refused: target path no longer names the guarded object: '{path}'")

    def detached_reader(self) -> int:
        """Duplicate raw handle to the guarded object for post-publish drift reads.

        The duplicate refers to the SAME object even after the path entry is
        replaced, and survives close() of the guard. Ownership transfers to
        the caller: read it with read_detached (which closes it on every path)
        or close it with close_raw on failure paths. Never closed twice.
        """
        import ctypes
        dup = ctypes.c_void_p()
        current = self._k32.GetCurrentProcess()
        if not self._k32.DuplicateHandle(current, self._handle, current,
                                         ctypes.byref(dup), 0, False, 0x00000002):
            raise PathBoundaryError(f"apply refused: cannot duplicate guard handle: '{self.target}'")
        return dup.value

    def close_raw(self, handle: int) -> None:
        """Close a raw handle duplicated from this guard (prototyped API)."""
        try:
            self._k32.CloseHandle(handle)
        except OSError:
            pass

    def read_detached(self, raw_handle: int) -> bytes:
        """Read all bytes through a detached duplicate, closing it exactly once.

        The duplicate refers to the pre-publish object even after the path
        entry is replaced. Closes the duplicate on every path (success or
        failure) with the prototyped handle API. Any I/O failure fails closed
        as PublishWindowDriftError: without the old bytes we cannot prove
        that no concurrent write was destroyed.
        """
        import msvcrt
        try:
            fd = msvcrt.open_osfhandle(raw_handle, 0)
        except OSError as exc:
            self.close_raw(raw_handle)
            raise PublishWindowDriftError(
                f"apply uncertain: old target object unreadable after publish: '{self.target}'") from None
        try:
            os.lseek(fd, 0, os.SEEK_SET)
            chunks = []
            while True:
                chunk = os.read(fd, 65536)
                if not chunk:
                    break
                chunks.append(chunk)
            return b"".join(chunks)
        except OSError as exc:
            raise PublishWindowDriftError(
                f"apply uncertain: old target object unreadable after publish: '{self.target}'") from None
        finally:
            try:
                os.close(fd)
            except OSError:
                pass

    def close(self) -> None:
        if getattr(self, "_handle", None):
            try:
                self._k32.CloseHandle(self._handle)
            except OSError:
                pass
            self._handle = None

    def __enter__(self) -> "_TargetGuard":
        return self

    def __exit__(self, *exc_info) -> None:
        self.close()
        return None


class _DirGuard:
    """Share-READ handle on the target PARENT directory, held across publish.

    While held, entry create/rename/delete inside the directory and renames
    of the directory itself fail with sharing violations, so the exact
    B2-02 (rename-away + recreate) and B2-03 (parent swap + junction) races
    fail loudly at the attacker's syscall instead of slipping through. Plain
    reads still succeed. Our own path-based rename/replace compatibility with
    this guard was proven per-host by the share matrix report (premise P-A);
    any incompatibility fails LOUD here or at publish time. Guards are never
    silently skipped: acquisition failure refuses BEFORE any side effect.
    """

    def __init__(self, directory: Path):
        if os.name != "nt":
            raise PathBoundaryError("directory guard requires Windows share-mode semantics")
        import ctypes
        k32 = ctypes.windll.kernel32
        k32.CreateFileW.argtypes = [
            ctypes.c_wchar_p, ctypes.c_ulong, ctypes.c_ulong,
            ctypes.c_void_p, ctypes.c_ulong, ctypes.c_ulong, ctypes.c_void_p]
        k32.CreateFileW.restype = ctypes.c_void_p
        k32.CloseHandle.argtypes = [ctypes.c_void_p]
        k32.CloseHandle.restype = ctypes.c_bool
        k32.GetFileInformationByHandleEx.argtypes = [
            ctypes.c_void_p, ctypes.c_int, ctypes.c_void_p, ctypes.c_ulong]
        k32.GetFileInformationByHandleEx.restype = ctypes.c_bool
        GENERIC_READ = 0x80000000
        OPEN_EXISTING = 3
        FILE_SHARE_READ = 0x00000001
        FILE_FLAG_BACKUP_SEMANTICS = 0x02000000
        INVALID_HANDLE = ctypes.c_void_p(-1).value
        handle = k32.CreateFileW(str(directory), GENERIC_READ, FILE_SHARE_READ,
                                 None, OPEN_EXISTING, FILE_FLAG_BACKUP_SEMANTICS, None)
        if handle == INVALID_HANDLE or handle is None:
            raise PathBoundaryError(
                f"apply refused: cannot guard parent directory (likely changed concurrently): '{directory}'")
        self._k32 = k32
        self._handle = handle
        self.directory = directory
        try:
            self.file_id = self._handle_file_id()
        except OSError as exc:
            k32.CloseHandle(handle)
            self._handle = None
            raise PathBoundaryError(
                f"apply refused: cannot identify guarded directory: '{directory}': {exc}") from None

    def _handle_file_id(self):
        """(volume, 128-bit file id) of the guarded directory via its handle."""
        import ctypes
        import struct
        id_buf = ctypes.create_string_buffer(24)
        if not self._k32.GetFileInformationByHandleEx(self._handle, 18, id_buf, 24):
            raise OSError("directory FileIdInfo query failed")
        volume = struct.unpack_from("<Q", bytes(id_buf), 0)[0]
        return (volume, bytes(id_buf)[8:24])

    def reverify_identity(self) -> None:
        """The guarded handle must still name the same directory object."""
        try:
            file_id = self._handle_file_id()
        except OSError:
            raise PathBoundaryError(f"apply refused: parent guard handle lost: '{self.directory}'")
        if file_id != self.file_id:
            raise PathBoundaryError(f"apply refused: guarded parent directory changed: '{self.directory}'")

    def assert_path_still_names_guarded(self, path: Path) -> None:
        """Prove the live parent path still names the guarded directory object.

        Opens a second read-only handle BY PATH (with backup semantics, full
        share so our own guard never blocks it) and compares FileIdInfo.
        Any failure or mismatch fails closed. Callers must ALSO check path
        containment (junction to a same-filesystem location still resolves
        inside only if realpath says so).
        """
        import ctypes
        import struct
        GENERIC_READ = 0x80000000
        OPEN_EXISTING = 3
        FILE_SHARE_ALL = 0x00000007
        FILE_FLAG_BACKUP_SEMANTICS = 0x02000000
        INVALID_HANDLE = ctypes.c_void_p(-1).value
        handle = self._k32.CreateFileW(str(path), GENERIC_READ, FILE_SHARE_ALL,
                                       None, OPEN_EXISTING, FILE_FLAG_BACKUP_SEMANTICS, None)
        if handle == INVALID_HANDLE or handle is None:
            raise PathBoundaryError(
                f"apply refused: cannot re-open parent path before publish: '{path}'")
        try:
            id_buf = ctypes.create_string_buffer(24)
            if not self._k32.GetFileInformationByHandleEx(handle, 18, id_buf, 24):
                raise PathBoundaryError(
                    f"apply refused: cannot identify pre-publish parent: '{path}'")
            volume = struct.unpack_from("<Q", bytes(id_buf), 0)[0]
            file_id = (volume, bytes(id_buf)[8:24])
        finally:
            try:
                self._k32.CloseHandle(handle)
            except OSError:
                pass
        if file_id != self.file_id:
            raise PathBoundaryError(
                f"apply refused: parent path no longer names the guarded directory: '{path}'")

    def close(self) -> None:
        if getattr(self, "_handle", None):
            try:
                self._k32.CloseHandle(self._handle)
            except OSError:
                pass
            self._handle = None

    def __enter__(self) -> "_DirGuard":
        return self

    def __exit__(self, *exc_info) -> None:
        self.close()
        return None


class PathBoundaryError(Exception):
    """Raised when a path cannot be PROVEN to stay inside the workspace."""


class PublishWindowDriftError(Exception):
    """Post-publish drift evidence: the old object changed across the final
    publish adjacency, so a concurrent write may have been destroyed. The new
    bytes are the reviewed ones, but the outcome must NOT be reported as a
    clean success: needs_attention, no blind replay, no recovery-as-applied."""


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
    # Post-review apply (atomic publish, version-guarded, collision-free)
    # ------------------------------------------------------------------

    def apply_staged(self, task_id: str, *, target_path: str, staged_path: str,
                     content_sha256: str, target_pre_sha256: Optional[str],
                     authorized_files: Optional[set] = None) -> Dict[str, Any]:
        """Apply a reviewed staged write to the final target."""
        # 1. Re-validate Final target path and staged path.
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

        # 4. Isolated, uniquely named exclusive temp file inside staging (ARCH-02:
        # never touches neighbor files). The candidate is FULLY written here with
        # short-write looping, fsync and hash verification BEFORE any target
        # mutation (JH016-01); the target is never written in place.
        temp_path = staging_dir / f".tmp_{uuid.uuid4().hex}_{target.name}"
        tfd = self._open_verified(temp_path, mode="create_exclusive")
        try:
            try:
                _write_all(tfd, candidate_bytes)
                os.fsync(tfd)
            finally:
                os.close(tfd)
            if _sha256_file(temp_path) != content_sha256:
                raise PathBoundaryError("apply refused: temp file hash mismatch after write")
            self._record(kind="temp_written", temp_path=str(temp_path))
            if self.fault_injector:
                self.fault_injector("temp_written")

            # 5. Parent-pinned controlled publish (B2-01/02/03). The support
            # scope (Windows NTFS) is gated first; then the target parent is
            # pinned with a share-READ directory guard held through publish,
            # so entry create/rename/delete races fail loudly at the
            # attacker's syscall. Re-resolve immediately before commit to
            # catch any parent junction swap (ARCH-04).
            target_now = self.resolve_workspace_path(target_path)
            self._assert_realpath_inside_workspace(target_now.parent, "commit_parent")
            _require_publish_volume(target_now)
            if _sha256_file(temp_path) != content_sha256:
                raise PathBoundaryError("apply refused: temp file changed before commit")

            if target_pre_sha256 is None:
                # New file: the target must not exist. Publish with os.rename,
                # which atomically fails when the destination appeared
                # concurrently (controlled refusal, never a clobber or an
                # empty shell). Same-volume rename is atomic.
                if os.path.lexists(str(target_now)):
                    raise PathBoundaryError(
                        f"apply refused: target changed since review (file appeared concurrently: '{target_now}')")
                self._assert_realpath_inside_workspace(target_now.parent, "commit_new_target_parent")
                with _DirGuard(target_now.parent) as dir_guard:
                    dir_guard.assert_path_still_names_guarded(target_now.parent)
                    self._record(kind="commit_parent_guarded", target_path=str(target_now))
                    if self.fault_injector:
                        self.fault_injector("guarded")
                    fresh_target = self.resolve_workspace_path(target_path)
                    self._assert_realpath_inside_workspace(fresh_target.parent, "commit_guarded_parent")
                    if self.fault_injector:
                        self.fault_injector("pre_publish")
                    dir_guard.reverify_identity()
                    dir_guard.assert_path_still_names_guarded(fresh_target.parent)
                    try:
                        os.rename(str(temp_path), str(fresh_target))
                    except FileExistsError:
                        raise PathBoundaryError(
                            f"apply refused: target changed since review (file appeared concurrently: '{fresh_target}')") from None
                    except OSError:
                        # Rename is atomic: on failure the target is untouched
                        # (still nonexistent or still a concurrent file we did not modify).
                        raise
            else:
                # Existing file: version-prove under the file guard, pin
                # entries with the parent guard, then release the file guard
                # ADJACENT to the publish syscall (a held file guard is proven
                # incompatible with our own replace: B2-01, WinError 5).
                # os.replace only swaps the directory entry, so hardlink
                # aliases keep the old bytes (JH016-02); multi-link targets
                # are additionally refused outright. A retained old-object
                # reader detects final-adjacency drift afterwards.
                if not target_now.is_file():
                    raise PathBoundaryError(
                        f"apply refused: target changed since review (target removed concurrently: '{target_now}')")
                with _TargetGuard(target_now, target_pre_sha256) as guard:
                    guard.assert_single_link()
                    self._record(kind="commit_guarded", target_path=str(target_now),
                                 link_count=guard.nlink, guarded_size=guard.size)
                    if self.fault_injector:
                        self.fault_injector("guarded")
                    with _DirGuard(target_now.parent) as dir_guard:
                        dir_guard.assert_path_still_names_guarded(target_now.parent)
                        self._record(kind="commit_parent_guarded", target_path=str(target_now))
                        if self.fault_injector:
                            self.fault_injector("pre_publish")
                        # Final re-proof immediately before publish: same guarded
                        # objects (handle identities), same version bytes
                        # (handle re-read), and both paths still name those
                        # objects (fresh resolve + containment + file-id
                        # cross-checks).
                        fresh_target = self.resolve_workspace_path(target_path)
                        self._assert_realpath_inside_workspace(fresh_target.parent, "commit_guarded_parent")
                        guard.reverify(target_pre_sha256)
                        guard.assert_single_link()
                        guard.assert_path_still_names_guarded(fresh_target)
                        dir_guard.reverify_identity()
                        dir_guard.assert_path_still_names_guarded(fresh_target.parent)
                        old_reader = guard.detached_reader()
                        guard.close()
                        try:
                            os.replace(str(temp_path), str(fresh_target))
                        except OSError:
                            # Replace is atomic at the directory-entry level: on
                            # failure the old target keeps its old bytes. The
                            # retained duplicate is closed before propagating.
                            guard.close_raw(old_reader)
                            raise
                        old_bytes = guard.read_detached(old_reader)
                        if hashlib.sha256(old_bytes).hexdigest() != target_pre_sha256:
                            self._record(kind="publish_window_drift", target_path=str(fresh_target))
                            raise PublishWindowDriftError(
                                "apply uncertain: target object changed across final publish "
                                "(concurrent bytes may have been replaced); needs_attention")
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
