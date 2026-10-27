"""Run the entry in-process with ONE constructed fault injected around the REAL adapter
(Hark-派单-25 tests only; the production entry has no hook). argv: <fault> <entry.py> <args...>

  lose_registry      the real launcher starts the process, then the registry file is removed and
                     the launcher raises (dispatch happened; evidence lost)
  corrupt_registry   same, but the registry gets a garbage line instead
  collect_raises     adapter.collect raises OSError after a real dispatch
  status_raises      adapter.status raises OSError
  check_raises       B's run_checks raises ValueError
  readonly_root      after launch the work root becomes read-only (result files cannot be written)
Every injected message carries the sentinel given in argv to prove it is not echoed."""
import os
import runpy
import stat
import sys

fault, entry = sys.argv[1], sys.argv[2]
sentinel = os.environ.get("HARK25_TEST_SENTINEL", "sentinel")
root = os.path.dirname(os.path.dirname(os.path.dirname(os.path.realpath(entry))))
sys.path.insert(0, os.path.join(root, "scripts", "gemini_task01"))
sys.path.insert(0, root)
from src import agent_session_gemini as gem  # noqa: E402
import check_run  # noqa: E402

orig_launcher = gem.default_launcher


def _evidence(kw):
    return os.path.join(kw["cwd"], "..", "evidence")


def launcher(argv, **kw):
    proc = orig_launcher(argv, **kw)
    reg = os.path.join(_evidence(kw), "registry.jsonl")
    if fault == "lose_registry":
        proc.wait()
        os.remove(reg)
        raise RuntimeError("injected after launch " + sentinel)
    if fault == "corrupt_registry":
        proc.wait()
        with open(reg, "ab") as fh:
            fh.write(b"{not json\n")
        raise RuntimeError("injected after launch " + sentinel)
    if fault == "readonly_root":
        work_root = os.path.dirname(os.path.abspath(kw["cwd"]))
        os.chmod(work_root, stat.S_IRUSR | stat.S_IXUSR)
    return proc


def boom(*a, **k):
    raise OSError("injected " + sentinel)


def vboom(*a, **k):
    raise ValueError("injected " + sentinel)


gem.default_launcher = launcher
if fault == "collect_raises":
    gem.GeminiCliAgentSessionAdapter.collect = boom
if fault == "status_raises":
    gem.GeminiCliAgentSessionAdapter.status = boom
if fault == "check_raises":
    check_run.run_checks = vboom
sys.argv = [entry] + sys.argv[3:]
runpy.run_path(entry, run_name="__main__")
