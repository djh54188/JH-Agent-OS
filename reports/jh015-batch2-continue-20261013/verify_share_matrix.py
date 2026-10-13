"""Share-mode publish compatibility matrix: the B2-01 gate proof (JH-015 batch 2).

Proves (or refutes) the premises of the parent-pinned publish design BEFORE the
redesign is accepted. Run on the Windows host that verifies this batch:

    python -X utf8 -B reports/jh015-batch2-continue-20261013/verify_share_matrix.py

Exit 0 means the script completed, NOT that the product passes. Verdicts live
in share-matrix.json: every row records allowed/denied plus WinError/return
code, and the GATE block says GO (premises hold, redesign may proceed) or STOP
(do not ship the redesign; escalate a scope ruling instead of weakening guards).

Premises under test:
  P-A: holding the PARENT dir with SHARE_READ still permits our own
       rename/replace of a child entry by path.
  P-B: the same parent handle denies child create / rename-inside-dir /
       dir-self-rename (entry operations need parent WRITE/DELETE).
  P-C: holding the TARGET file with SHARE_READ and NO delete-share denies
       child write-open and rename-away. (Own replace under ANY file guard is
       already proven incompatible by B2-01; the redesign releases the file
       guard adjacent to publish and pins entries with the parent guard.)
  P-D: a pre-opened ordinary O_RDWR handle blocks guard acquisition (R1 fact).

Calibration row B2-01-REPRO must show PermissionError/WinError 5, otherwise the
host differs from the review host and the whole matrix is void.

All fixtures live under .test-tmp/publish-matrix-<uuid>. No product sources are
modified; no model calls are made.
"""

import ctypes
import json
import os
import shutil
import subprocess
import sys
import uuid
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]

GENERIC_READ = 0x80000000
OPEN_EXISTING = 3
FILE_ATTRIBUTE_NORMAL = 0x80
FILE_FLAG_BACKUP_SEMANTICS = 0x02000000
SHARE_READ = 0x00000001
SHARE_WRITE = 0x00000002
SHARE_DELETE = 0x00000004
SHARE_ALL = 0x00000007
INVALID_HANDLE = ctypes.c_void_p(-1).value

k32 = ctypes.windll.kernel32
k32.CreateFileW.argtypes = [
    ctypes.c_wchar_p, ctypes.c_ulong, ctypes.c_ulong,
    ctypes.c_void_p, ctypes.c_ulong, ctypes.c_ulong, ctypes.c_void_p]
k32.CreateFileW.restype = ctypes.c_void_p
k32.CloseHandle.argtypes = [ctypes.c_void_p]
k32.CloseHandle.restype = ctypes.c_bool


def open_dir_handle(path, share):
    handle = k32.CreateFileW(
        str(path), GENERIC_READ, share, None, OPEN_EXISTING,
        FILE_FLAG_BACKUP_SEMANTICS, None)
    if handle == INVALID_HANDLE or handle is None:
        raise OSError("cannot open dir handle: %s" % path)
    return handle


def open_file_handle(path, share):
    handle = k32.CreateFileW(
        str(path), GENERIC_READ, share, None, OPEN_EXISTING,
        FILE_ATTRIBUTE_NORMAL, None)
    if handle == INVALID_HANDLE or handle is None:
        raise OSError("cannot open file handle: %s" % path)
    return handle


def close_handle(handle):
    try:
        k32.CloseHandle(handle)
    except OSError:
        pass


def child(code, *args):
    proc = subprocess.run(
        [sys.executable, "-X", "utf8", "-B", "-c", code, *[str(a) for a in args]],
        capture_output=True, text=True, encoding="utf-8", timeout=15)
    return {"returncode": proc.returncode,
            "stdout_tail": (proc.stdout or "").strip().splitlines()[-2:],
            "stderr_tail": (proc.stderr or "").strip().splitlines()[-2:]}


CHILD_CREATE = (
    "import os,sys; fd=os.open(sys.argv[1],os.O_CREAT|os.O_EXCL|os.O_WRONLY"
    "|getattr(os,'O_BINARY',0)); os.write(fd,b'x'); os.close(fd)")
CHILD_WRITE_OPEN = (
    "import os,sys; fd=os.open(sys.argv[1],os.O_RDWR"
    "|getattr(os,'O_BINARY',0)); os.close(fd)")
CHILD_RENAME = "import os,sys; os.rename(sys.argv[1],sys.argv[2])"


def volume_fs_name(path):
    k32.GetVolumePathNameW.argtypes = [ctypes.c_wchar_p, ctypes.c_wchar_p, ctypes.c_ulong]
    k32.GetVolumePathNameW.restype = ctypes.c_bool
    k32.GetVolumeInformationW.argtypes = [
        ctypes.c_wchar_p, ctypes.c_wchar_p, ctypes.c_ulong,
        ctypes.POINTER(ctypes.c_ulong), ctypes.POINTER(ctypes.c_ulong),
        ctypes.POINTER(ctypes.c_ulong), ctypes.c_wchar_p, ctypes.c_ulong]
    k32.GetVolumeInformationW.restype = ctypes.c_bool
    buf_path = ctypes.create_unicode_buffer(260)
    if not k32.GetVolumePathNameW(str(path), buf_path, 260):
        return "unknown(volume-path-failed)"
    fs_buf = ctypes.create_unicode_buffer(32)
    ok = k32.GetVolumeInformationW(
        buf_path.value, None, 0, None, None, None, fs_buf, 32)
    return fs_buf.value if ok else "unknown(volume-info-failed)"


def attempt(label, fn):
    try:
        fn()
        return {"row": label, "allowed": True}
    except OSError as exc:
        return {"row": label, "allowed": False,
                "error": type(exc).__name__, "winerror": getattr(exc, "winerror", None),
                "text": str(exc)[:160]}


def main():
    base = (ROOT / ".test-tmp").resolve()
    base.mkdir(exist_ok=True)
    case = base / ("publish-matrix-" + uuid.uuid4().hex)
    case.mkdir()
    rows = []
    holds = []
    try:
        work = case / "ws" / "out"
        work.mkdir(parents=True)
        target = work / "target.txt"
        target.write_bytes(b"ORIGINAL")
        temp = work / "candidate.tmp"
        temp.write_bytes(b"CANDIDATE")
        rows.append({"row": "volume_fs", "fs": volume_fs_name(target)})

        # Calibration: B2-01 must reproduce on this host.
        h = open_file_handle(target, SHARE_READ | SHARE_DELETE)
        holds.append(h)
        rows.append(attempt("B2-01-REPRO:file-guard-READ|DELETE-x-own-replace",
                            lambda: os.replace(temp, target)))
        temp.write_bytes(b"CANDIDATE")
        for h_ in holds:
            close_handle(h_)
        holds.clear()

        # P-C: file guard with SHARE_READ only.
        h = open_file_handle(target, SHARE_READ)
        holds.append(h)
        rows.append(attempt("P-C:file-guard-READ-x-own-replace",
                            lambda: os.replace(temp, target)))
        temp.write_bytes(b"CANDIDATE")
        r = child(CHILD_WRITE_OPEN, target)
        rows.append({"row": "P-C:file-guard-READ-x-child-write-open",
                     "allowed": r["returncode"] == 0, "child": r})
        r = child(CHILD_RENAME, target, work / "moved-away.txt")
        rows.append({"row": "P-C:file-guard-READ-x-child-rename-away",
                     "allowed": r["returncode"] == 0, "child": r})
        for h_ in holds:
            close_handle(h_)
        holds.clear()
        temp.write_bytes(b"CANDIDATE")

        # P-D: pre-opened ordinary writer blocks acquisition.
        fd = os.open(target, os.O_RDWR | getattr(os, "O_BINARY", 0))
        try:
            try:
                h = open_file_handle(target, SHARE_READ)
                holds.append(h)
                acquired = True
            except OSError:
                acquired = False
            rows.append({"row": "P-D:preopened-O_RDWR-x-guard-acquire",
                         "allowed": acquired})
        finally:
            os.close(fd)

        # P-A / P-B: parent dir guard with SHARE_READ.
        d = open_dir_handle(work, SHARE_READ)
        holds.append(d)
        rows.append(attempt("P-A:parent-guard-READ-x-own-rename",
                            lambda: os.rename(temp, work / "renamed.txt")))
        temp.write_bytes(b"CANDIDATE")
        rows.append(attempt("P-A:parent-guard-READ-x-own-replace",
                            lambda: os.replace(temp, target)))
        temp.write_bytes(b"CANDIDATE")
        r = child(CHILD_CREATE, work / "child-created.txt")
        rows.append({"row": "P-B:parent-guard-READ-x-child-create",
                     "allowed": r["returncode"] == 0, "child": r})
        # Write-open runs before any rename row so a moved target cannot
        # misattribute a missing-file failure as a sharing denial.
        r = child(CHILD_WRITE_OPEN, target)
        rows.append({"row": "FACT:parent-guard-READ-x-child-write-open(existing-file)",
                     "allowed": r["returncode"] == 0, "child": r})
        r = child(CHILD_RENAME, target, work / "child-moved.txt")
        rows.append({"row": "P-B:parent-guard-READ-x-child-rename-inside-dir",
                     "allowed": r["returncode"] == 0, "child": r})
        r = child(CHILD_RENAME, work, work.parent / "out-moved")
        rows.append({"row": "P-B:parent-guard-READ-x-child-dir-self-rename",
                     "allowed": r["returncode"] == 0, "child": r})
        for h_ in holds:
            close_handle(h_)
        holds.clear()

        gate = {}
        by_row = {r["row"]: r for r in rows if "allowed" in r}
        gate["calibration_B2-01_reproduced"] = not by_row[
            "B2-01-REPRO:file-guard-READ|DELETE-x-own-replace"]["allowed"]
        gate["P-A_own_publish_allowed_under_parent_READ"] = bool(
            by_row["P-A:parent-guard-READ-x-own-rename"]["allowed"]
            and by_row["P-A:parent-guard-READ-x-own-replace"]["allowed"])
        gate["P-B_entry_ops_denied_under_parent_READ"] = bool(
            not by_row["P-B:parent-guard-READ-x-child-create"]["allowed"]
            and not by_row["P-B:parent-guard-READ-x-child-rename-inside-dir"]["allowed"]
            and not by_row["P-B:parent-guard-READ-x-child-dir-self-rename"]["allowed"])
        gate["P-C_file_READ_denies_write_and_rename"] = bool(
            not by_row["P-C:file-guard-READ-x-child-write-open"]["allowed"]
            and not by_row["P-C:file-guard-READ-x-child-rename-away"]["allowed"])
        gate["P-D_preopened_writer_blocks_acquire"] = bool(
            not by_row["P-D:preopened-O_RDWR-x-guard-acquire"]["allowed"])
        gate["GO"] = bool(gate["calibration_B2-01_reproduced"]
                          and gate["P-A_own_publish_allowed_under_parent_READ"]
                          and gate["P-B_entry_ops_denied_under_parent_READ"]
                          and gate["P-C_file_READ_denies_write_and_rename"]
                          and gate["P-D_preopened_writer_blocks_acquire"])
    finally:
        for h_ in holds:
            close_handle(h_)
        resolved = case.resolve()
        if resolved.parent != base or not resolved.name.startswith("publish-matrix-"):
            raise RuntimeError("fixture cleanup boundary check failed")
        shutil.rmtree(resolved, ignore_errors=True)

    report = {"api_calls": 0, "fixture_cleaned": not case.exists(),
              "rows": rows, "gate": gate,
              "verdict": "GO" if gate.get("GO") else "STOP",
              "note": "STOP means: do not ship the parent-pinned redesign; "
                      "escalate a scope ruling with this file instead of "
                      "weakening any guard. GO means the redesign premises hold "
                      "on this host/filesystem; proceed to unit + mutation runs."}
    out = Path(__file__).with_name("share-matrix.json")
    out.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
