"""Hark-派单-19: evidence gate of scripts/win/hark18-gemini-stdout.ps1 (offline, local only).

Runs the WHOLE entry with PowerShell (HARK19_PWSH, else skipped and counted as skipped) in test
mode: the remote is a marked local bare-repo fixture holding the fixed replay commit, git goes
through tests/fixtures/hark19/git_stub.py (real git unless a fault is injected) and, except in
the real-interpreter cases, the replay interpreter is tests/fixtures/hark19/python_stub.py, which
does NOT run the Hark18 tests (CONSTRUCTED replay outcome). No network, no real CLI, no model.
For every case the terminal VERDICT line, the final out/result.json and the exit code must agree.
"""
import hashlib, json, os, re, shutil, stat, subprocess, sys, tempfile, unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "win" / "hark18-gemini-stdout.ps1"
FIX = ROOT / "tests" / "fixtures" / "hark19"
PWSH = os.environ.get("HARK19_PWSH", "")
REAL_PY = os.environ.get("HARK19_REAL_PYTHON", sys.executable)
CANDIDATE = "f6bf47c59688c43d927dede01b59f772520815d8"
EVDIR = "reports/hark18-gemini-stdout-20261024/windows"


def _launcher(path, target):
    path.write_text(f'#!/bin/sh\nexec "{sys.executable}" "{target}" "$@"\n', encoding="utf-8")
    path.chmod(path.stat().st_mode | stat.S_IEXEC)
    return path


@unittest.skipUnless(PWSH and os.name != "nt", "HARK19_PWSH not set (POSIX pwsh harness)")
class _Base(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = Path(tempfile.mkdtemp(prefix="hark19-"))
        cls.real_git = shutil.which("git")
        remote = cls.tmp / "remote.git"
        subprocess.run([cls.real_git, "init", "-q", "--bare", str(remote)], check=True)
        subprocess.run([cls.real_git, "-C", str(ROOT), "push", "-q", str(remote), f"{CANDIDATE}:refs/heads/fixture"], check=True)
        (remote / "hark19-test-fixture").write_text("hark19 local test fixture\n")
        cls.remote = remote
        cls.git = _launcher(cls.tmp / "git-stub", FIX / "git_stub.py")
        cls.py = _launcher(cls.tmp / "python-stub", FIX / "python_stub.py")
        cls.original = cls.tmp / "constructed-original.jsonl"
        cls.original.write_bytes(b"CONSTRUCTED stand-in for node22-run1 stdout\n")
        cls.n = 0

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.tmp, ignore_errors=True)

    def run_entry(self, git_fault="", py_fault="", inject="", original=True, python=None, script=SCRIPT):
        type(self).n += 1
        case = self.tmp / f"case{self.n}"
        state = case / "state"; state.mkdir(parents=True)
        data = self.original.read_bytes()
        cfg = {"remote": str(self.remote), "git": str(self.git), "inject": inject,
               "original_bytes": len(data), "original_sha256": hashlib.sha256(data).hexdigest()}
        (case / "cfg.json").write_text(json.dumps(cfg), encoding="utf-8")
        env = dict(os.environ, HARK19_REAL_GIT=self.real_git, HARK19_GIT_FAULT=git_fault,
                   HARK19_PY_FAULT=py_fault, HARK19_STUB_STATE=str(state))
        args = [PWSH, "-NoProfile", "-File", str(script),
                "-OriginalStdout", str(self.original if original else case / "missing.jsonl"),
                "-WorkRoot", str(case / "work"), "-Python", str(python or self.py), "-TestConfig", str(case / "cfg.json")]
        done = subprocess.run(args, capture_output=True, text=True, env=env, timeout=600)
        runs = list((case / "work").glob("run-*"))
        result = json.loads((runs[0] / "out" / "result.json").read_text(encoding="utf-8-sig")) if runs else {}
        verdicts = re.findall(r"^VERDICT (\S+) \(exit (\d+)\)", done.stdout, re.M)
        return {"exit": done.returncode, "stdout": done.stdout, "result": result, "verdicts": verdicts,
                "repo": runs[0] / "repo" if runs else None}

    def check(self, r, verdict, code, evidence, stage=None, replay=None):
        res = r["result"]
        self.assertEqual(r["exit"], code, r["stdout"][-3000:])
        self.assertEqual(r["verdicts"], [(verdict, str(code))])
        self.assertEqual((res.get("final"), res.get("verdict"), res.get("exit_code")), (True, verdict, code))
        if evidence is not None:
            self.assertEqual(res["evidence"]["status"], evidence)
            self.assertEqual(res["evidence"]["failed_stage"], stage)
        if replay:
            self.assertEqual(res["replay"]["status"], replay)
        has_push = " push " in r["stdout"]
        self.assertEqual(has_push, evidence == "verified", "push command printed iff evidence verified")
        self.assertEqual(bool(res.get("push_command")), evidence == "verified")


class TestEvidenceGate(_Base):
    def test_normal_control_pass_and_commit_matches(self):
        r = self.run_entry()
        self.check(r, "PASS", 0, "verified", None, "PASS")
        ev = r["result"]["evidence"]
        git = lambda *a: subprocess.run([self.real_git, "-C", str(r["repo"])] + list(a), capture_output=True, text=True, check=True).stdout.strip()
        self.assertEqual(git("rev-parse", "HEAD"), ev["commit"])
        self.assertEqual(git("rev-parse", ev["commit"] + "^"), CANDIDATE)
        self.assertEqual(sorted(git("diff", "--name-only", CANDIDATE, ev["commit"]).splitlines()),
                         sorted(f"{EVDIR}/{n}" for n in ("replay-result.json", "run-log.txt", "hark18-tests.txt", "weakening.txt")))
        rec = json.loads(git("show", f"{ev['commit']}:{EVDIR}/replay-result.json"))
        self.assertEqual((rec["final"], rec["replay"]["status"]), (False, "PASS"))
        self.assertNotIn('"verdict"', git("show", f"{ev['commit']}:{EVDIR}/replay-result.json"))

    def test_evidence_failures_never_pass(self):
        cases = {
            "prepare_dir": dict(inject="prepare_dir", stage="prepare_dir"),
            "copy": dict(inject="copy", stage="snapshot"),
            "branch_fail": dict(git_fault="fail:checkout-b", stage="branch"),
            "add_fail": dict(git_fault="fail:add", stage="add"),
            "commit_fail": dict(git_fault="fail:commit", stage="commit"),
            "commit_noop": dict(git_fault="noop:commit", stage="verify_identity"),
            "identity_read_fail": dict(git_fault="fail:post-commit-rev-parse", stage="verify_identity"),
            "branch_rebound": dict(git_fault="rebind:commit", stage="verify_identity"),
            "wrong_parent": dict(git_fault="preparent:commit", stage="verify_parent"),
            "extra_path": dict(git_fault="extra:commit", stage="verify_paths"),
            "content_changed": dict(git_fault="mutate:commit", stage="verify_content"),
            "left_dirty": dict(git_fault="dirty:commit", stage="verify_clean"),
            "head_moved_during_replay": dict(py_fault="moves_head", stage="identity_before"),
            "dirtied_during_replay": dict(py_fault="dirties", stage="identity_before"),
        }
        for name, c in cases.items():
            with self.subTest(name):
                stage = c.pop("stage")
                r = self.run_entry(**c)
                self.check(r, "EVIDENCE_NOT_DELIVERED", 4, "failed", stage, "PASS")

    def test_result_write_failure(self):
        r = self.run_entry(inject="result_write")
        self.assertEqual(r["exit"], 5)
        self.assertEqual(r["verdicts"], [("RESULT_NOT_RECORDED", "5")])
        self.assertEqual(r["result"]["verdict"], "RESULT_NOT_RECORDED")
        self.assertNotIn(" push ", r["stdout"])

    def test_replay_outcomes_keep_their_verdict(self):
        self.check(self.run_entry(py_fault="fail"), "FAIL", 1, "verified", None, "FAIL")
        self.check(self.run_entry(py_fault="fail", git_fault="fail:commit"), "FAIL", 1, "failed", "commit", "FAIL")
        self.check(self.run_entry(original=False), "INCOMPLETE", 3, "verified", None, "INCOMPLETE")
        self.check(self.run_entry(original=False, git_fault="fail:add"), "INCOMPLETE", 3, "failed", "add", "INCOMPLETE")

    def test_placeholder_is_never_pass(self):
        text = SCRIPT.read_text(encoding="utf-8")
        first_write = text.index("[IO.File]::WriteAllText($ResultPath")
        self.assertIn("verdict = 'RUNNING'", text[:first_write])
        self.assertEqual(len(re.findall(r"\$verdict = 'PASS'", text)), 1)


class TestRealInterpreter(_Base):
    """The real Hark18 tests through the entry (no original here, so INCOMPLETE is the right answer)."""
    def test_real_replay_incomplete_with_verified_evidence(self):
        r = self.run_entry(original=False, python=REAL_PY)
        self.check(r, "INCOMPLETE", 3, "verified", None, "INCOMPLETE")
        self.assertEqual(r["result"]["tests"]["ran"], 20)
        self.assertEqual(r["result"]["tests"]["detected"], 8)


# gate -> ([(exact text, weakened text)], fault scenario that only this gate stops)
GATES = {
    "final": ([("elseif ($Evidence.verified -eq $true -and $Evidence.status -eq 'verified') {", "elseif ($true) {")],
              dict(git_fault="fail:commit")),
    "push": ([("if ($Evidence.verified -eq $true) {   # GATE:push", "if ($true) {   # GATE:push")],
             dict(git_fault="fail:commit")),
    "branch_binding": ([("if ($branchSha -ne $ev) {", "if ($false) {"),
                        ("if ($sym -ne ('refs/heads/' + $Cfg.evidence_branch)) {", "if ($false) {"),
                        ("$sym = (Invoke-Git @('symbolic-ref', '-q', 'HEAD')).Text", "$sym = (Invoke-Git @('symbolic-ref', '-q', 'HEAD') -AllowFail).Text")],
                       dict(git_fault="rebind:commit")),
    "parent": ([("if (($parents.Count -ne 2) -or ($parents[1] -ne $Cfg.candidate)) {", "if ($false) {")],
               dict(git_fault="preparent:commit")),
    "paths": ([("if (($want -join '|') -ne ($got -join '|')) {", "if ($false) {")], dict(git_fault="extra:commit")),
    "content": ([("if ($blob -ne $expected[$p].blob) {", "if ($false) {")], dict(git_fault="mutate:commit")),
    "clean": ([("if ($dirty -ne '') { throw ('worktree not clean after", "if ($false) { throw ('worktree not clean after")],
              dict(git_fault="dirty:commit")),
}


class TestGateWeakening(_Base):
    def test_each_gate_effective_then_detected(self):
        original = SCRIPT.read_text(encoding="utf-8")
        rows = []
        for name, (subs, scenario) in GATES.items():
            with self.subTest(gate=name):
                weak = original
                for exact, repl in subs:
                    self.assertEqual(original.count(exact), 1, f"{name}: {exact!r} not unique")
                    weak = weak.replace(exact, repl, 1)
                path = self.tmp / f"weak-{name}.ps1"
                path.write_text(weak, encoding="utf-8")
                control = self.run_entry(**scenario)
                weakened = self.run_entry(script=path, **scenario)
                ok_control = control["exit"] != 0 and " push " not in control["stdout"]
                missed = weakened["exit"] != 0 and " push " not in weakened["stdout"]
                rows.append(f"{name}\tcontrol={'pass' if ok_control else 'FAIL'}\tweakened={'MISSED' if missed else 'detected'}"
                            f"\tcontrol_exit={control['exit']}\tweak_exit={weakened['exit']}")
                self.assertTrue(ok_control)
                self.assertFalse(missed)
        log = os.environ.get("HARK19_WEAKENING_LOG")
        if log:
            Path(log).write_text("\n".join(rows) + "\n", encoding="utf-8")


if __name__ == "__main__":
    unittest.main()
