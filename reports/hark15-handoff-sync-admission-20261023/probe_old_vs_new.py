"""Hark-派单-15: same-input comparison of the Hark14 candidate (1102105) and the Hark15 script.

Usage (repository root, PowerShell on PATH or HARK14_PWSH set):
    python reports/hark15-handoff-sync-admission-20261023/probe_old_vs_new.py <out.json>
R1: ref injected on the local fixture AFTER the check, right before push (deterministic race).
    The old script gets the identical injection snippet inserted before its push (test harness only).
R2: test mode remote that is not a local fixture (127.0.0.1 ports that refuse; no real host is contacted).
R3: the old/new weakening helper fed a skipped case.
"""
import importlib.util, json, subprocess, sys, tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from tests import test_hark14_handoff_sync as T  # noqa: E402

INJECT = '''    if ($IsTest -and $Cfg.ContainsKey('inject_before_push')) {
        foreach ($inj in @($Cfg.inject_before_push)) {
            & git --git-dir $RemoteUrl update-ref $inj.ref $inj.sha
            Write-Log ("TEST injected {0} -> {1}" -f $inj.ref, $inj.sha)
        }
    }
'''
tmp = Path(tempfile.mkdtemp(prefix="hark15-probe-"))
old_text = subprocess.run(["git", "show", "11021059f84c46941d17bd482e33f11ff7176b5d:scripts/win/hark14-handoff-sync.ps1"],
                          cwd=ROOT, capture_output=True, check=True).stdout.decode()
anchor = "    $script:PushAttempted = $true\n"
assert old_text.count(anchor) == 1
OLD = tmp / "old-1102105-with-injection.ps1"
OLD.write_text(old_text.replace(anchor, INJECT + anchor), encoding="utf-8")
NEW = T.SCRIPT
out = {"old_script": "1102105 + injection snippet before push", "new_script": str(NEW.relative_to(ROOT)), "r1": [], "r2": [], "r3": {}}

for label in ("A", "B", "both"):
    for name, script in (("old", OLD), ("new", NEW)):
        f = T.Fixture()
        try:
            refs = {"A": [T.REF_A], "B": [T.REF_B], "both": [T.REF_A, T.REF_B]}[label]
            inj = {r: f.base for r in refs}
            f.write_config(inject_before_push=[{"ref": r, "sha": s} for r, s in inj.items()])
            code, text, result = f.run(publish=True, script=script)
            after = f.remote_refs()
            moved = [r for r, s in inj.items() if after.get(r) != s]
            other = [r for r in (T.REF_A, T.REF_B) if r not in inj and r in after]
            out["r1"].append({"case": f"ancestor-{label}", "script": name, "exit": code,
                              "push_exit": (result or {}).get("push", {}).get("exit"),
                              "final_targets_match": after.get(T.REF_A) == f.evidence and T.REF_B in after and after[T.REF_B] != f.base,
                              "existing_different_refs_moved": len(moved), "other_new_target_left": other})
        finally:
            f.close()

for url in ("https://127.0.0.1:9/x.git", "ssh://git@127.0.0.1:1/x.git", "git@127.0.0.1:x.git"):
    for name, script in (("old", OLD), ("new", NEW)):
        f = T.Fixture()
        try:
            code, text, result = f.run(publish=True, script=script, remote=url)
            out["r2"].append({"url": url, "script": name, "exit": code,
                              "reached_git_transport": "remote.base_fetchable" in text,
                              "refused_before_transport": "refused before any git transport" in text})
        finally:
            f.close()

def load(path, modname):
    spec = importlib.util.spec_from_file_location(modname, path)
    m = importlib.util.module_from_spec(spec); spec.loader.exec_module(m); return m

old_tests = tmp / "tests"; old_tests.mkdir()
(old_tests / "old_h14.py").write_bytes(subprocess.run(["git", "show", "11021059f84c46941d17bd482e33f11ff7176b5d:tests/test_hark14_handoff_sync.py"],
                                                      cwd=ROOT, capture_output=True, check=True).stdout)
import unittest
for name, mod in (("old", load(old_tests / "old_h14.py", "old_h14")), ("new", T)):
    class Stand(unittest.TestCase):
        @unittest.skip("platform condition missing")
        def test_x(self):
            pass
    mod.__dict__["_Stand"] = Stand
    r = mod.TestGuardWeakening("test_each_weakened_guard_is_detected")._run_case("_Stand.test_x", NEW)
    out["r3"][name] = {"helper_result_for_skipped_case": r,
                       "counted_as_control_pass": r is True or r == "pass",
                       "would_report_detected": not (r is True or r == "pass") and r != "skip"}
Path(sys.argv[1]).write_text(json.dumps(out, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
print(json.dumps(out, indent=1))
