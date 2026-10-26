"""Run `entry prepare` in-process with guards (Hark-派单-24 tests only).

* os.environ is replaced by a mapping that RECORDS every lookup of the key variable
  (and of the endpoint / proxy variables) - prepare must make none;
* subprocess.Popen records every argv; prepare may start only `git`.
Writes the observations to argv[1] as JSON, then exits with prepare's exit code."""
import collections.abc
import json
import os
import subprocess
import sys

WATCH = {"GEMINI_API_KEY", "GOOGLE_API_KEY", "GOOGLE_GEMINI_BASE_URL", "HTTPS_PROXY", "HTTP_PROXY"}
seen = []
started = []


class Guard(collections.abc.MutableMapping):
    def __init__(self, inner):
        self._d = dict(inner)

    def _note(self, k):
        if k in WATCH:
            seen.append(k)

    def __getitem__(self, k):
        self._note(k)
        return self._d[k]

    def __setitem__(self, k, v):
        self._d[k] = v

    def __delitem__(self, k):
        del self._d[k]

    def __iter__(self):
        return iter(self._d)

    def __len__(self):
        return len(self._d)

    def __contains__(self, k):
        self._note(k)
        return k in self._d

    def copy(self):
        return dict(self._d)


_orig = subprocess.Popen.__init__


def _popen(self, args, *a, **kw):
    started.append(list(args) if isinstance(args, (list, tuple)) else [args])
    return _orig(self, args, *a, **kw)


out_path = sys.argv[1]
sys.argv = [sys.argv[0]] + sys.argv[2:]
os.environ = Guard(os.environ)
subprocess.Popen.__init__ = _popen
sys.path.insert(0, os.getcwd())
from scripts.gemini_task01_run import entry  # noqa: E402

code = entry.main(sys.argv[1:])
with open(out_path, "w", encoding="utf-8") as fh:
    json.dump({"env_lookups": seen, "processes": started, "exit": code}, fh)
sys.exit(code)
