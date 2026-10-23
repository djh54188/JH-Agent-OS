"""Hark-派单-14: offline tests for scripts/win/hark14-handoff-sync.ps1.

Everything runs against temporary repositories: a bare "remote", a local source
repository holding the evidence commit, and a synthetic package with a manifest.
The script's -TestConfig/-RemoteUrl overrides point it at these fixtures; the
production command passes neither. Needs PowerShell (env HARK14_PWSH or `pwsh`
on PATH); skipped otherwise. No network, no real repository, no credentials.
"""
import hashlib
import json
import os
import re
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "win" / "hark14-handoff-sync.ps1"
PWSH = os.environ.get("HARK14_PWSH") or shutil.which("pwsh")

REF_A = "refs/heads/win-evidence-exec09-20261023"
REF_B = "refs/heads/handoff-local-gemini-20261023"
EXIT_PUBLISHED, EXIT_PREPARED, EXIT_FAIL, EXIT_PARTIAL = 0, 10, 1, 2

PAYLOAD = {
    "reports/gemini-windows-probe-independent-review-20261023.md": "# review\n复核 正文\n".encode(),
    "reports/review/crlf.txt": b"line1\r\nline2\r\n",
    "reports/review/binary.bin": bytes(range(256)) * 3,
    "reports/review/中文 目录/说明.md": "中文 内容\n".encode(),
    "reports/exec08/report with spaces.md": b"no trailing newline",
}


def git(cwd, *args, env=None, check=True):
    r = subprocess.run(["git", *args], cwd=cwd, capture_output=True, env=env)
    if check and r.returncode:
        raise RuntimeError(f"git {args}: {r.stderr.decode(errors='replace')}")
    return r.stdout.decode().strip()


class Fixture:
    def __init__(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="hark14-"))
        self.home = self.tmp / "home"
        self.home.mkdir()
        # hostile global config: must not change archived bytes, and must stay unmodified
        (self.home / ".gitconfig").write_text(
            "[user]\n\tname = t\n\temail = t@example.invalid\n[core]\n\tautocrlf = true\n")
        self.env = dict(os.environ, HOME=str(self.home), GIT_CONFIG_NOSYSTEM="1",
                        XDG_CONFIG_HOME=str(self.home / ".config"))
        self.remote = self.tmp / "remote.git"
        git(self.tmp, "init", "--bare", "-q", str(self.remote), env=self.env)
        self.source = self.tmp / "source"
        git(self.tmp, "init", "-q", str(self.source), env=self.env)
        (self.source / "docs").mkdir()
        (self.source / "docs" / "base.md").write_text("base\n")
        (self.source / "reports").mkdir()
        (self.source / "reports" / "existing.md").write_text("existing\n")
        git(self.source, "add", ".", env=self.env)
        git(self.source, "commit", "-q", "-m", "base", env=self.env)
        self.base = git(self.source, "rev-parse", "HEAD", env=self.env)
        git(self.source, "push", "-q", str(self.remote), "HEAD:refs/heads/main", env=self.env)
        (self.source / "reports" / "exec09.md").write_text("exec09 evidence\n")
        git(self.source, "add", ".", env=self.env)
        git(self.source, "commit", "-q", "-m", "exec09", env=self.env)
        self.evidence = git(self.source, "rev-parse", "HEAD", env=self.env)   # local only, not on remote
        self.package = self.tmp / "package"
        self.payload = dict(PAYLOAD)
        self.write_package()
        self.workroot = self.tmp / "work"
        self.config_path = self.tmp / "test-config.json"
        self.write_config()

    def write_package(self, manifest_override=None):
        if self.package.exists():
            shutil.rmtree(self.package)
        for rel, data in self.payload.items():
            p = self.package / rel
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_bytes(data)
        entries = [{"path": rel, "sha256": hashlib.sha256(d).hexdigest(), "bytes": len(d)}
                   for rel, d in sorted(self.payload.items())]
        manifest = manifest_override if manifest_override is not None else {"schema": "test/v1", "files": entries}
        self.manifest_bytes = json.dumps(manifest, ensure_ascii=False, indent=2).encode()
        self.package.mkdir(parents=True, exist_ok=True)
        (self.package / "MANIFEST.json").write_bytes(self.manifest_bytes)
        self.entries = entries

    def write_config(self, **over):
        cfg = {"base_sha": self.base, "evidence_sha": self.evidence,
               "manifest_sha256": hashlib.sha256(self.manifest_bytes).hexdigest(),
               "payload_count": len(self.entries), "payload_bytes": sum(e["bytes"] for e in self.entries)}
        cfg.update(over)
        self.config_path.write_text(json.dumps(cfg))

    def run(self, publish=False, script=SCRIPT, remote=None):
        args = [PWSH, "-NoProfile", "-NonInteractive", "-File", str(script),
                "-PackageDir", str(self.package), "-SourceRepo", str(self.source),
                "-WorkRoot", str(self.workroot), "-RemoteUrl", str(remote or self.remote),
                "-TestConfig", str(self.config_path)]
        if publish:
            args.append("-Publish")
        r = subprocess.run(args, capture_output=True, env=self.env, timeout=300)
        out = r.stdout.decode(errors="replace") + r.stderr.decode(errors="replace")
        runs = sorted(self.workroot.glob("run-*")) if self.workroot.exists() else []
        result = None
        if runs and (runs[-1] / "result.json").exists():
            result = json.loads((runs[-1] / "result.json").read_text(encoding="utf-8-sig"))
        return r.returncode, out, result

    def remote_refs(self):
        out = git(self.remote, "for-each-ref", "--format=%(refname) %(objectname)", env=self.env)
        return dict(line.split(" ") for line in out.splitlines() if line)

    def source_state(self):
        return (git(self.source, "for-each-ref", env=self.env), git(self.source, "status", "--porcelain", env=self.env),
                git(self.source, "rev-parse", "HEAD", env=self.env),
                hashlib.sha256((self.source / ".git" / "index").read_bytes()).hexdigest(),
                (self.home / ".gitconfig").read_bytes())

    def package_digest(self):
        h = hashlib.sha256()
        for p in sorted(self.package.rglob("*")):
            if p.is_file() and not p.is_symlink():
                h.update(str(p.relative_to(self.package)).encode() + b"\0" + p.read_bytes())
        return h.hexdigest()

    def close(self):
        shutil.rmtree(self.tmp, ignore_errors=True)


def failed_checks(result):
    return [c["name"] for c in (result or {}).get("checks", []) if not c["ok"]]


@unittest.skipUnless(PWSH, "PowerShell not available")
class _Base(unittest.TestCase):
    def fixture(self):
        f = Fixture()
        self.addCleanup(f.close)
        return f

    def assert_refused(self, f, publish=True, check=None, script=SCRIPT):
        before = f.remote_refs()
        code, out, result = f.run(publish=publish, script=script)
        self.assertEqual(code, EXIT_FAIL, out)
        self.assertEqual(f.remote_refs(), before, "nothing may be published")
        if check:
            self.assertIn(check, failed_checks(result), out)
        return out, result


class TestNormal(_Base):
    def test_prepare_only_does_not_publish(self):
        f = self.fixture()
        before = f.source_state(), f.package_digest()
        code, out, result = f.run()
        self.assertEqual(code, EXIT_PREPARED, out)
        self.assertEqual(result["verdict"], "PREPARED_NOT_PUBLISHED")
        self.assertNotIn(REF_A, f.remote_refs())
        self.assertNotIn(REF_B, f.remote_refs())
        self.assertIn("NOT PUBLISHED", out)
        self.assertEqual((f.source_state(), f.package_digest()), before)

    def test_publish_then_rerun_idempotent(self):
        f = self.fixture()
        before = f.source_state(), f.package_digest()
        code, out, result = f.run(publish=True)
        self.assertEqual(code, EXIT_PUBLISHED, out)
        refs = f.remote_refs()
        self.assertEqual(refs[REF_A], f.evidence)
        archive = refs[REF_B]
        self.assertEqual(git(f.remote, "rev-parse", archive + "^", env=f.env), f.base)
        changes = git(f.remote, "-c", "core.quotepath=false", "diff-tree", "-r", "--name-status", f.base, archive,
                      env=f.env).splitlines()
        self.assertTrue(all(c.startswith("A\t") for c in changes))
        expected = set(f.payload) | {"reports/local-handoff-sync-20261023/MANIFEST.json"}
        self.assertEqual({c[2:] for c in changes}, expected)
        for rel, data in f.payload.items():                               # exact bytes despite autocrlf=true
            blob = subprocess.run(["git", "cat-file", "blob", f"{archive}:{rel}"], cwd=f.remote,
                                  capture_output=True, env=f.env).stdout
            self.assertEqual(blob, data, rel)
        manifest_blob = subprocess.run(["git", "cat-file", "blob", f"{archive}:reports/local-handoff-sync-20261023/MANIFEST.json"],
                                       cwd=f.remote, capture_output=True, env=f.env).stdout
        self.assertEqual(manifest_blob, f.manifest_bytes)
        self.assertEqual((f.source_state(), f.package_digest()), before)   # source, package, global config untouched
        code2, out2, result2 = f.run(publish=True)
        self.assertEqual(code2, EXIT_PUBLISHED, out2)
        self.assertEqual(result2["push"]["refspecs"], [])
        self.assertEqual(f.remote_refs()[REF_B], archive)                  # deterministic archive commit

    def test_one_ref_already_present_pushes_only_the_other(self):
        f = self.fixture()
        git(f.source, "push", "-q", str(f.remote), f"{f.evidence}:{REF_A}", env=f.env)
        code, out, result = f.run(publish=True)
        self.assertEqual(code, EXIT_PUBLISHED, out)
        self.assertEqual(len(result["push"]["refspecs"]), 1)
        self.assertIn(REF_B, result["push"]["refspecs"][0])


class TestPackageRefusals(_Base):
    def test_tampered_payload_byte(self):
        f = self.fixture()
        p = f.package / "reports/review/binary.bin"
        b = bytearray(p.read_bytes()); b[10] ^= 1; p.write_bytes(bytes(b))
        self.assert_refused(f, check="package.bytes_match_manifest")

    def test_missing_payload_file(self):
        f = self.fixture()
        (f.package / "reports/review/crlf.txt").unlink()
        self.assert_refused(f, check="package.file_set_equals_manifest")

    def test_extra_file_in_package(self):
        f = self.fixture()
        (f.package / "reports/review/extra.txt").write_bytes(b"x")
        self.assert_refused(f, check="package.file_set_equals_manifest")

    def test_manifest_digest_mismatch(self):
        f = self.fixture()
        f.write_config(manifest_sha256="0" * 64)
        self.assert_refused(f, publish=False, check="manifest.sha256_fixed")

    def test_manifest_count_and_bytes_fixed(self):
        f = self.fixture()
        f.write_config(payload_count=99, payload_bytes=1)
        out, result = self.assert_refused(f, publish=False)
        self.assertIn("manifest.count", failed_checks(result))
        self.assertIn("manifest.total_bytes", failed_checks(result))

    def test_schema_mismatch_reports_keys(self):
        f = self.fixture()
        entries = [{"path": e["path"], "sha256": e["sha256"], "size": e["bytes"]} for e in f.entries]
        f.write_package(manifest_override={"files": entries}); f.write_config()
        out, result = self.assert_refused(f, check="manifest.schema")
        self.assertIn("size", out)

    def test_path_escapes(self):
        for bad in ("../outside.md", "reports/../docs/base.md", "/abs/x.md", "C:/x.md", "docs/x.md",
                    "reports\\win.md", "reports/a/./b.md", "reports/CON.md", "reports/trail.", "reports/.git/config",
                    "reports/local-handoff-sync-20261023/MANIFEST.json"):
            with self.subTest(path=bad):
                f = self.fixture()
                f.payload = {bad if not bad.startswith("/") else bad: b"x", **PAYLOAD}
                entries = [{"path": rel, "sha256": hashlib.sha256(d).hexdigest(), "bytes": len(d)} for rel, d in f.payload.items()]
                # write only the safe files; the manifest still lists the bad path
                if f.package.exists():
                    shutil.rmtree(f.package)
                for rel, d in PAYLOAD.items():
                    (f.package / rel).parent.mkdir(parents=True, exist_ok=True)
                    (f.package / rel).write_bytes(d)
                f.manifest_bytes = json.dumps({"files": entries}, ensure_ascii=False).encode()
                (f.package / "MANIFEST.json").write_bytes(f.manifest_bytes)
                f.entries = entries; f.write_config()
                self.assert_refused(f, check="manifest.paths_whitelisted")

    def test_case_insensitive_duplicate(self):
        f = self.fixture()
        entries = list(f.entries) + [dict(f.entries[0], path=f.entries[0]["path"].upper().replace("REPORTS/", "reports/"))]
        f.manifest_bytes = json.dumps({"files": entries}, ensure_ascii=False).encode()
        (f.package / "MANIFEST.json").write_bytes(f.manifest_bytes)
        f.entries = entries; f.write_config()
        self.assert_refused(f, check="manifest.no_duplicate_paths_case_insensitive")

    @unittest.skipIf(os.name == "nt", "symlink creation needs privileges on Windows")
    def test_links_refused(self):
        for kind in ("file", "dir"):
            with self.subTest(kind=kind):
                f = self.fixture()
                target = f.package / "reports/review/crlf.txt"
                if kind == "file":
                    outside = f.tmp / "outside.txt"; outside.write_bytes(target.read_bytes())
                    target.unlink(); os.symlink(outside, target)
                else:
                    d = f.package / "reports/review/中文 目录"
                    moved = f.tmp / "moved"; shutil.move(str(d), str(moved)); os.symlink(moved, d, target_is_directory=True)
                self.assert_refused(f, check="package.no_links_or_reparse_points")

    def test_target_exists_in_base(self):
        f = self.fixture()
        f.payload = {"reports/existing.md": b"other\n", **PAYLOAD}
        f.write_package(); f.write_config()
        self.assert_refused(f, check="target.paths_new_in_base")

    def test_production_remote_with_test_config_refused(self):
        f = self.fixture()
        before = f.remote_refs()
        code, out, _ = f.run(publish=True, remote="https://github.com/djh54188/JH-Agent-OS.git")
        self.assertEqual(code, EXIT_FAIL, out)
        self.assertIn("production remote", out)
        self.assertEqual(f.remote_refs(), before)


class TestSourceAndRefs(_Base):
    def test_evidence_commit_missing(self):
        f = self.fixture()
        f.write_config(evidence_sha="1" * 40)
        self.assert_refused(f, check="source.evidence_commit_present")

    def test_evidence_parent_not_base(self):
        f = self.fixture()
        (f.source / "reports" / "more.md").write_text("more\n")
        git(f.source, "add", ".", env=f.env); git(f.source, "commit", "-q", "-m", "more", env=f.env)
        f.write_config(evidence_sha=git(f.source, "rev-parse", "HEAD", env=f.env))
        self.assert_refused(f, check="source.evidence_parent_is_base")

    def test_ref_a_conflict_stops_both(self):
        f = self.fixture()
        git(f.source, "push", "-q", str(f.remote), f"{f.base}:{REF_A}", env=f.env)
        self.assert_refused(f, check="remote.ref_a_absent_or_same")
        self.assertNotIn(REF_B, f.remote_refs())

    def test_ref_b_conflict_stops_both(self):
        f = self.fixture()
        git(f.source, "push", "-q", str(f.remote), f"{f.base}:{REF_B}", env=f.env)   # ancestor: would fast-forward
        self.assert_refused(f, check="remote.ref_b_absent_or_same")
        self.assertNotIn(REF_A, f.remote_refs())

    def test_push_rejected_is_atomic(self):
        f = self.fixture()
        hook = f.remote / "hooks" / "pre-receive"
        hook.write_text("#!/bin/sh\nwhile read o n r; do case \"$r\" in *handoff-local*) echo reject >&2; exit 1;; esac; done\nexit 0\n")
        hook.chmod(0o755)
        code, out, result = f.run(publish=True)
        self.assertEqual(code, EXIT_FAIL, out)
        self.assertNotEqual(result["push"]["exit"], 0)
        self.assertEqual(f.remote_refs().get(REF_A), None)                  # atomic: A not left behind
        self.assertEqual(f.remote_refs().get(REF_B), None)

    def test_remote_differs_after_push_is_not_pass(self):
        f = self.fixture()
        hook = f.remote / "hooks" / "post-receive"
        hook.write_text(f"#!/bin/sh\ngit update-ref {REF_B} {f.base}\n")
        hook.chmod(0o755)
        code, out, result = f.run(publish=True)
        self.assertEqual(code, EXIT_PARTIAL, out)
        self.assertEqual(result["push"]["exit"], 0)                         # push "succeeded"
        self.assertIn("after.ref_b_exact", failed_checks(result))
        self.assertEqual(result["verdict"], "PARTIAL_OR_MISMATCH")


GUARDS = {
    "digest": (r"function Test-ManifestDigest\(\[string\]\$actual\) \{ return \(.*?\) \}",
               "function Test-ManifestDigest([string]$actual) { return $true }",
               "TestPackageRefusals.test_manifest_digest_mismatch"),
    "fileset": (r"function Test-FileSetEqual\(\$listed, \$found\) \{",
                "function Test-FileSetEqual($listed, $found) { return $true",
                "TestPackageRefusals.test_extra_file_in_package"),
    "path": (r"function Test-PayloadPath\(\[string\]\$p\) \{",
             "function Test-PayloadPath([string]$p) { return $null",
             "TestPackageRefusals.test_target_exists_in_base__path"),
    "reparse": (r"function Test-NotReparse\(\$item\) \{ return .*? \}",
                "function Test-NotReparse($item) { return $true }",
                "TestPackageRefusals.test_links_refused"),
    "refconflict": (r"function Test-RemoteRefAllowed\(\[string\]\$remoteSha, \[string\]\$wanted\) \{",
                    "function Test-RemoteRefAllowed([string]$remoteSha, [string]$wanted) { if ($remoteSha) { return 'same' }",
                    "TestSourceAndRefs.test_ref_b_conflict_stops_both"),
    "postverify": (r"function Test-RemoteBlob\(.*?\n.*?function Test-RemoteRefExact\(.*?\n",
                   "function Test-RemoteBlob([string]$a, [string]$b) { return $true }\nfunction Test-RemoteRefExact([string]$a, [string]$b) { return $true }\n",
                   "TestSourceAndRefs.test_remote_differs_after_push_is_not_pass"),
}


@unittest.skipUnless(PWSH, "PowerShell not available")
class TestGuardWeakening(unittest.TestCase):
    """Control first (unweakened script passes the targeted test), then the weakened copy must fail it."""

    def _run_case(self, target, script):
        if target.endswith("__path"):                       # path guard: one escape, run directly
            f = Fixture()
            try:
                bad = "docs/x.md"                           # outside the reports/ whitelist, file present
                entries = list(f.entries) + [{"path": bad, "sha256": hashlib.sha256(b"x").hexdigest(), "bytes": 1}]
                f.manifest_bytes = json.dumps({"files": entries}).encode()
                (f.package / "MANIFEST.json").write_bytes(f.manifest_bytes)
                (f.package / "docs").mkdir()
                (f.package / "docs" / "x.md").write_bytes(b"x")
                f.entries = entries; f.write_config()
                before = f.remote_refs()
                code, out, result = f.run(publish=True, script=script)
                return code == EXIT_FAIL and f.remote_refs() == before
            finally:
                f.close()
        cls, method = target.split(".")
        test = globals()[cls](method)
        test.run_script = script
        res = unittest.TestResult()
        original = Fixture.run

        def patched(self_, publish=False, script=SCRIPT, remote=None):
            return original(self_, publish=publish, script=test.run_script, remote=remote)
        Fixture.run = patched
        try:
            test.run(res)
        finally:
            Fixture.run = original
        return res.wasSuccessful()

    def test_each_weakened_guard_is_detected(self):
        text = SCRIPT.read_text(encoding="utf-8")
        tmp = Path(tempfile.mkdtemp(prefix="hark14-weak-"))
        self.addCleanup(shutil.rmtree, tmp, True)
        lines = []
        for name, (pattern, replacement, target) in GUARDS.items():
            with self.subTest(guard=name):
                self.assertTrue(self._run_case(target, SCRIPT), f"control for {name} must pass")
                weak, n = re.subn(pattern, lambda m: replacement, text, count=1, flags=re.S)
                self.assertEqual(n, 1, f"pattern for {name} not found")
                weak_path = tmp / f"weak-{name}.ps1"
                weak_path.write_text(weak, encoding="utf-8")
                detected = not self._run_case(target, weak_path)
                lines.append(f"{name}\tcontrol=pass\tweakened={'detected' if detected else 'MISSED'}\t{target}")
                self.assertTrue(detected, f"weakening {name} went undetected")
        out = os.environ.get("HARK14_WEAKENING_LOG")
        if out:
            Path(out).write_text("\n".join(lines) + "\n", encoding="utf-8")


if __name__ == "__main__":
    unittest.main()
