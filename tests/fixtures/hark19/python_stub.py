"""Hark-派单-19 test stand-in for the replay interpreter (SYNTHETIC; tests only). It does NOT run
the Hark18 tests; it prints a unittest-shaped log so the Windows entry's evidence stage can be
exercised. HARK19_PY_FAULT: fail | moves_head | dirties. The original-replay line is ok only
when HARK18_ORIGINAL_STDOUT is set (as the entry does when the original digest matched)."""
import os, subprocess, sys

args = sys.argv[1:]
if args[:1] == ["-c"]:
    print("3.12.0 python-stub"); sys.exit(0)
fault = os.environ.get("HARK19_PY_FAULT", "")
log = os.environ.get("HARK18_WEAKENING_LOG")
if log:
    with open(log, "w", encoding="utf-8") as fh:
        for n in range(8):
            fh.write(f"guard{n}\tcontrol=pass\tweakened=detected\tStub.test\n")
orig = "ok" if os.environ.get("HARK18_ORIGINAL_STDOUT") else "skipped 'original not provided'"
lines = ["test_known_line_kept_as_diagnostic (tests.test_hark18_gemini_stdout.TestParser.test_known_line_kept_as_diagnostic) ... "
         + ("FAIL" if fault == "fail" else "ok"),
         f"test_original_bytes (tests.test_hark18_gemini_stdout.TestOriginalRun1Replay.test_original_bytes) ... {orig}",
         "", "Ran 20 tests in 1.0s", "", "FAILED (failures=1)" if fault == "fail" else "OK"]
if fault == "moves_head":
    subprocess.run([os.environ["HARK19_REAL_GIT"], "-c", "user.name=stub", "-c", "user.email=stub@invalid",
                    "commit", "-q", "--allow-empty", "-m", "moved"], check=True)
if fault == "dirties":
    open("stray-by-replay.txt", "w").write("x\n")
sys.stderr.write("\n".join(lines) + "\n")
sys.exit(1 if fault == "fail" else 0)
