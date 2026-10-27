"""Run the entry in-process, recording every file it opens (audit hook) and, optionally,
SIMULATING a Windows reparse point (Hark-派单-26 tests only; a simulation, not a real junction).
argv: <out.json> <comma-separated paths to report as reparse points, or -> <entry.py> <args...>"""
import json
import os
import runpy
import sys

out_path, sim, entry = sys.argv[1], sys.argv[2], sys.argv[3]
simulated = {os.path.abspath(p) for p in sim.split(",")} if sim != "-" else set()
opened = []


def hook(event, args):
    if event == "open" and args and isinstance(args[0], (str, bytes)):
        opened.append(os.fsdecode(args[0]))


class _Reparse:
    def __init__(self, st):
        self._st = st
        self.st_file_attributes = 0x400          # FILE_ATTRIBUTE_REPARSE_POINT
        self.st_reparse_tag = 0xA0000003         # IO_REPARSE_TAG_MOUNT_POINT (junction)

    def __getattr__(self, name):
        return getattr(self._st, name)


_real_lstat = os.lstat


def fake_lstat(path, *a, **k):
    st = _real_lstat(path, *a, **k)
    if isinstance(path, str) and os.path.abspath(path) in simulated:
        return _Reparse(st)
    return st


os.lstat = fake_lstat
sys.addaudithook(hook)
sys.argv = [entry] + sys.argv[4:]
code = 0
try:
    runpy.run_path(entry, run_name="__main__")
except SystemExit as exc:
    code = exc.code if isinstance(exc.code, int) else 1
finally:
    with open(out_path, "w", encoding="utf-8") as fh:
        json.dump({"opened": opened, "exit": code}, fh)
sys.exit(code)
