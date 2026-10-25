"""Hark-派单-19: same git fault through the OLD entry (507315d) and the NEW entry.

OLD: a copy of 507315d:scripts/win/hark18-gemini-stdout.ps1 in which ONLY the two pinned original
size/digest constants are replaced by those of a CONSTRUCTED stand-in file (so that the old
replay stage can pass without the real original); git is the stub, put first on PATH as `git`;
it fetches the fixed commit from GitHub as the old script does. NEW: the current entry in test
mode (local marked fixture remote, same stub). The replay interpreter is the python stub for both.
Usage: HARK19_PWSH=<pwsh> python reports/hark19-win-evidence-gate-20261025/probe_old_vs_new.py <out.json>
"""
import hashlib, json, os, re, shutil, stat, subprocess, sys, tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
PWSH = os.environ["HARK19_PWSH"]
CANDIDATE = "f6bf47c59688c43d927dede01b59f772520815d8"
REAL_GIT = shutil.which("git")
tmp = Path(tempfile.mkdtemp(prefix="hark19-probe-"))
def launcher(p, target):
    p.write_text(f'#!/bin/sh\nexec "{sys.executable}" "{target}" "$@"\n'); p.chmod(p.stat().st_mode | stat.S_IEXEC); return p
bindir = tmp / "bin"; bindir.mkdir()
git = launcher(bindir / "git", ROOT / "tests/fixtures/hark19/git_stub.py")
py = launcher(tmp / "python-stub", ROOT / "tests/fixtures/hark19/python_stub.py")
orig = tmp / "constructed-original.jsonl"; orig.write_bytes(b"CONSTRUCTED stand-in for node22-run1 stdout\n")
size, digest = len(orig.read_bytes()), hashlib.sha256(orig.read_bytes()).hexdigest()
old = subprocess.run([REAL_GIT, "-C", str(ROOT), "show", "507315d63dd650731d50e78ccb301b91a780f91c:scripts/win/hark18-gemini-stdout.ps1"],
                     capture_output=True, check=True).stdout.decode("utf-8")
assert old.count("original_bytes    = 3212") == 1 and old.count("4f8effca4110d539f64bf06a90cb830b593f706ec82a4e3a65a28c8a67ffa4aa") == 1
old = old.replace("original_bytes    = 3212", f"original_bytes    = {size}").replace(
    "4f8effca4110d539f64bf06a90cb830b593f706ec82a4e3a65a28c8a67ffa4aa", digest)
old_path = tmp / "old-507315d-constants-only.ps1"; old_path.write_text(old, encoding="utf-8")
remote = tmp / "remote.git"
subprocess.run([REAL_GIT, "init", "-q", "--bare", str(remote)], check=True)
subprocess.run([REAL_GIT, "-C", str(ROOT), "push", "-q", str(remote), f"{CANDIDATE}:refs/heads/fixture"], check=True)
(remote / "hark19-test-fixture").write_text("fixture\n")
rows = []
for fault in ["", "fail:checkout-b", "fail:add", "fail:commit", "noop:commit", "fail:post-commit-rev-parse", "extra:commit", "mutate:commit"]:
    for label in ("old", "new"):
        case = tmp / f"{label}-{fault.replace(':', '_') or 'none'}"; state = case / "state"; state.mkdir(parents=True)
        env = dict(os.environ, HARK19_REAL_GIT=REAL_GIT, HARK19_GIT_FAULT=fault, HARK19_STUB_STATE=str(state),
                   PATH=str(bindir) + os.pathsep + os.environ["PATH"])
        args = [PWSH, "-NoProfile", "-File", str(old_path if label == "old" else ROOT / "scripts/win/hark18-gemini-stdout.ps1"),
                "-OriginalStdout", str(orig), "-WorkRoot", str(case / "work"), "-Python", str(py)]
        if label == "new":
            (case / "cfg.json").write_text(json.dumps({"remote": str(remote), "git": str(git), "original_bytes": size, "original_sha256": digest}))
            args += ["-TestConfig", str(case / "cfg.json")]
        done = subprocess.run(args, capture_output=True, text=True, env=env, timeout=600)
        v = re.findall(r"^VERDICT (\S+) \(exit (\d+)\)", done.stdout, re.M)
        res = json.loads(next((case / "work").glob("run-*/out/result.json")).read_text(encoding="utf-8-sig"))
        ev = res.get("evidence") if isinstance(res.get("evidence"), dict) else None
        row = {"fault": fault or "none", "entry": label, "exit": done.returncode, "terminal_verdict": v,
               "result_json_verdict": res.get("verdict"), "evidence": (ev or {}).get("status"),
               "failed_stage": (ev or {}).get("failed_stage"), "push_line_printed": " push " in done.stdout}
        rows.append(row); print(json.dumps(row))
Path(sys.argv[1]).write_text(json.dumps(rows, indent=2) + "\n", encoding="utf-8")
shutil.rmtree(tmp, ignore_errors=True)
