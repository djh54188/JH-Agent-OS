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
        (self.remote / "hark14-test-fixture").write_text("local temporary test fixture\n")
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

    def run(self, publish=False, script=SCRIPT, remote=None, test_config=True):
        args = [PWSH, "-NoProfile", "-NonInteractive", "-File", str(script),
                "-PackageDir", str(self.package), "-SourceRepo", str(self.source),
                "-WorkRoot", str(self.workroot), "-RemoteUrl", str(remote or self.remote)]
        if test_config:
            args += ["-TestConfig", str(self.config_path)]
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



class TestR2TestModeRemote(_Base):
    """R2: in test mode only a marked local temporary bare fixture is accepted, before any git transport."""

    def _refused_before_transport(self, f, remote, test_config=True, expect="refused before any git transport"):
        before = f.remote_refs()
        code, out, result = f.run(publish=True, remote=remote, test_config=test_config)
        self.assertEqual(code, EXIT_FAIL, out)
        self.assertIn(expect, out)
        self.assertNotIn("remote.base_fetchable", out)          # no fetch / ls-remote / push was reached
        self.assertIsNone(result)                                # refused before the run directory exists
        self.assertEqual(f.remote_refs(), before)

    def test_network_and_alias_urls_refused(self):
        for url in ("https://github.com/djh54188/JH-Agent-OS.git", "https://github.com/djh54188/JH-Agent-OS",
                    "https://GitHub.com/djh54188/jh-agent-os.git/", "http://127.0.0.1:9/x.git",
                    "https://example.invalid/x.git", "ssh://git@127.0.0.1:1/x.git", "git://127.0.0.1/x.git",
                    "git@github.com:djh54188/JH-Agent-OS.git", "github.com:djh54188/JH-Agent-OS.git",
                    "ext::sh -c touch% /tmp/hark14-ext", "//server/share/x.git", "\\\\server\\share\\x.git"):
            with self.subTest(url=url):
                f = self.fixture()
                self._refused_before_transport(f, url)

    def test_file_url_and_relative_path_refused(self):
        f = self.fixture()
        self._refused_before_transport(f, "file://" + str(f.remote))
        self._refused_before_transport(f, "remote.git")

    def test_unmarked_local_repo_refused(self):
        f = self.fixture()
        other = f.tmp / "real-receiver.git"
        git(f.tmp, "init", "--bare", "-q", str(other), env=f.env)
        before = git(other, "for-each-ref", env=f.env)
        self._refused_before_transport(f, str(other))
        self.assertEqual(git(other, "for-each-ref", env=f.env), before)

    def test_marked_repo_outside_temp_refused(self):
        f = self.fixture()
        outside = Path(os.environ.get("HARK14_OUTSIDE_DIR", str(ROOT.parent))) / f"hark14-outside-{os.getpid()}.git"
        self.addCleanup(shutil.rmtree, outside, True)
        git(f.tmp, "init", "--bare", "-q", str(outside), env=f.env)
        (outside / "hark14-test-fixture").write_text("x")
        self._refused_before_transport(f, str(outside))

    def test_non_production_remote_without_test_config_refused(self):
        f = self.fixture()
        self._refused_before_transport(f, str(f.remote), test_config=False, expect="requires -TestConfig")

    def test_protocol_layer_blocks_network_even_if_guard_weakened(self):
        """Second layer: git itself only allows the file transport in test mode."""
        f = self.fixture()
        weak = f.tmp / "weak-testremote.ps1"
        weak.write_text(re.sub(GUARDS["testremote"][0], lambda m: GUARDS["testremote"][1], SCRIPT.read_text(encoding="utf-8"),
                               count=1, flags=re.S), encoding="utf-8")
        code, out, result = f.run(publish=True, script=weak, remote="https://127.0.0.1:9/x.git")
        self.assertEqual(code, EXIT_FAIL, out)
        self.assertIn("remote.base_fetchable", failed_checks(result))
        self.assertIn("not allowed", out)                       # transport 'https' not allowed


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
        self.assertEqual(result["verdict"], "REJECTED_VERIFIED")
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


class TestR1CreateOnly(_Base):
    """R1: a ref that appears after the check (ancestor, diverged or exact) is never moved by this run."""

    def _side_commit(self, f):
        git(f.source, "checkout", "-q", "-b", "side", f.base, env=f.env)
        (f.source / "reports" / "side.md").write_text("side\n")
        git(f.source, "add", ".", env=f.env); git(f.source, "commit", "-q", "-m", "side", env=f.env)
        side = git(f.source, "rev-parse", "HEAD", env=f.env)
        git(f.source, "checkout", "-q", "-", env=f.env)
        git(f.source, "push", "-q", str(f.remote), f"{side}:refs/heads/other-owner", env=f.env)
        return side

    def _race(self, f, injected, script=SCRIPT):
        f.write_config(inject_before_push=[{"ref": r, "sha": s} for r, s in injected.items()])
        code, out, result = f.run(publish=True, script=script)
        refs = f.remote_refs()
        return code, out, result, refs

    def _assert_nothing_moved(self, f, injected, code, out, result, refs):
        self.assertEqual(code, EXIT_FAIL, out)
        for r, s in injected.items():
            self.assertEqual(refs.get(r), s, f"{r} must keep the value someone else gave it")
        for r in (REF_A, REF_B):
            if r not in injected:
                self.assertNotIn(r, refs, f"{r} must not be left behind")
        self.assertNotEqual(result["push"]["exit"], 0)
        self.assertEqual(result["verdict"], "REJECTED_VERIFIED")

    def test_ancestor_race_a(self):
        f = self.fixture(); inj = {REF_A: f.base}
        self._assert_nothing_moved(f, inj, *self._race(f, inj))

    def test_ancestor_race_b(self):
        f = self.fixture(); inj = {REF_B: f.base}
        self._assert_nothing_moved(f, inj, *self._race(f, inj))

    def test_ancestor_race_both(self):
        f = self.fixture(); inj = {REF_A: f.base, REF_B: f.base}
        self._assert_nothing_moved(f, inj, *self._race(f, inj))

    def test_diverged_race(self):
        f = self.fixture(); side = self._side_commit(f)
        for inj in ({REF_A: side}, {REF_B: side}, {REF_A: side, REF_B: side}):
            with self.subTest(refs=list(inj)):
                g = self.fixture(); side_g = self._side_commit(g)
                inj_g = {r: side_g for r in inj}
                self._assert_nothing_moved(g, inj_g, *self._race(g, inj_g))

    def test_exact_target_race_is_up_to_date_not_moved(self):
        f = self.fixture()
        git(f.source, "push", "-q", str(f.remote), f"{f.evidence}:refs/heads/carrier", env=f.env)
        inj = {REF_A: f.evidence}
        code, out, result, refs = self._race(f, inj)
        # someone created A at exactly the target: git reports it up to date ("="), nothing is moved, B is created
        self.assertEqual(code, EXIT_PUBLISHED, out)
        self.assertEqual(refs[REF_A], f.evidence)
        lines = result["push"]["output"].splitlines()
        self.assertTrue(any(l.startswith("=\t") and REF_A in l for l in lines), lines)
        self.assertTrue(any(l.startswith("*\t") and REF_B in l for l in lines), lines)
        f.write_config()
        code2, out2, result2 = f.run(publish=True)                # re-run: idempotent, nothing pushed
        self.assertEqual(code2, EXIT_PUBLISHED, out2)
        self.assertEqual(result2["push"]["refspecs"], [])

    def test_no_race_still_publishes(self):
        f = self.fixture()
        f.write_config(inject_before_push=[])
        code, out, result = f.run(publish=True)
        self.assertEqual(code, EXIT_PUBLISHED, out)
        self.assertEqual(f.remote_refs()[REF_A], f.evidence)


HUNG_UP = "fatal: the remote end hung up unexpectedly"


class TestR4Outcome(_Base):
    """R4: after a push attempt, a missing/garbled receipt never proves that nothing was written."""

    def _run(self, f, **cfg):
        f.write_config(**cfg)
        code, out, result = f.run(publish=True)
        return code, out, result, f.remote_refs()

    def _uncertain(self, code, out, result):
        self.assertEqual(code, EXIT_PARTIAL, out)
        self.assertEqual(result["verdict"], "PUBLISH_OUTCOME_UNCERTAIN")
        self.assertNotIn("nothing was written", out)
        self.assertNotIn("updated no remote ref", out)

    def test_committed_receipt_lost_reread_unavailable(self):
        f = self.fixture()
        code, out, result, refs = self._run(f, fake_push={"mode": "commit_hide", "exit": 128, "output": HUNG_UP}, verify_unavailable=True)
        self._uncertain(code, out, result)
        self.assertEqual(refs.get(REF_A), f.evidence)                 # the fixture really was written
        self.assertIn(REF_B, refs)
        self.assertEqual(result["push"]["exit"], 128)
        self.assertEqual(result["push"]["output"], HUNG_UP)

    def test_not_committed_receipt_lost_reread_unavailable(self):
        f = self.fixture()
        code, out, result, refs = self._run(f, fake_push={"mode": "nocommit_hide", "exit": 128, "output": HUNG_UP}, verify_unavailable=True)
        self._uncertain(code, out, result)
        self.assertNotIn(REF_A, refs); self.assertNotIn(REF_B, refs)   # really not written - same verdict as above

    def test_same_observation_same_verdict(self):
        f, g = self.fixture(), self.fixture()
        a = self._run(f, fake_push={"mode": "commit_hide", "exit": 128, "output": HUNG_UP}, verify_unavailable=True)
        b = self._run(g, fake_push={"mode": "nocommit_hide", "exit": 128, "output": HUNG_UP}, verify_unavailable=True)
        self.assertEqual((a[0], a[2]["verdict"]), (b[0], b[2]["verdict"]))

    def test_committed_receipt_lost_reread_complete_targets_verified(self):
        f = self.fixture()
        code, out, result, refs = self._run(f, fake_push={"mode": "commit_hide", "exit": 128, "output": HUNG_UP})
        self.assertEqual(code, EXIT_PARTIAL, out)
        self.assertEqual(result["verdict"], "TARGETS_VERIFIED_PUSH_UNCONFIRMED")
        self.assertIn("NOT confirmed to have created them", out)
        self.assertEqual(result["push"]["exit"], 128)                 # original exit kept

    def test_not_committed_receipt_lost_reread_complete_is_uncertain(self):
        f = self.fixture()
        code, out, result, refs = self._run(f, fake_push={"mode": "nocommit_hide", "exit": 128, "output": HUNG_UP})
        self._uncertain(code, out, result)
        self.assertTrue(result["remote_after"]["readable"])

    def test_partial_success_receipt(self):
        for unavailable, verdict in ((True, "PUBLISH_OUTCOME_UNCERTAIN"), (False, "TARGETS_VERIFIED_PUSH_UNCONFIRMED")):
            with self.subTest(reread_unavailable=unavailable):
                f = self.fixture()
                code, out, result, refs = self._run(f, fake_push={"mode": "truncate", "keep": 2, "exit": 128}, verify_unavailable=unavailable)
                self.assertEqual(code, EXIT_PARTIAL, out)
                self.assertEqual(result["verdict"], verdict)

    def test_garbled_rejection_receipt_is_not_proof(self):
        f = self.fixture()
        spec_a = f"{f.evidence}:{REF_A}"
        garbled = f"To {f.remote}\n!\t{spec_a}\t[rejected] (stale info)"   # only one refspec, no 'Done'
        code, out, result, refs = self._run(f, fake_push={"mode": "nocommit_hide", "exit": 1, "output": garbled})
        self._uncertain(code, out, result)

    def test_one_reread_fails(self):
        f = self.fixture()
        code, out, result, refs = self._run(f, verify_fetch_fail=[REF_B])
        self._uncertain(code, out, result)
        self.assertEqual(result["push"]["exit"], 0)
        self.assertIn(REF_B, refs)

    def test_one_reread_mismatch_after_successful_push(self):
        f = self.fixture()
        code, out, result, refs = self._run(f, inject_after_push=[{"ref": REF_B, "sha": ""}])
        self.assertEqual(code, EXIT_PARTIAL, out)
        self.assertEqual(result["verdict"], "PARTIAL_OR_MISMATCH")

    def test_one_reread_mismatch_after_failed_push(self):
        f = self.fixture()
        code, out, result, refs = self._run(f, fake_push={"mode": "commit_hide", "exit": 128, "output": HUNG_UP},
                                            inject_after_push=[{"ref": REF_B, "sha": f.base}])
        self._uncertain(code, out, result)

    def test_complete_rejection_but_reread_unavailable_is_uncertain(self):
        f = self.fixture()
        code, out, result, refs = self._run(f, inject_before_push=[{"ref": REF_A, "sha": f.base}], verify_unavailable=True)
        self._uncertain(code, out, result)
        self.assertEqual(refs.get(REF_A), f.base)                     # still never moved
        self.assertNotIn(REF_B, refs)

    def test_complete_rejection_verified(self):
        f = self.fixture()
        code, out, result, refs = self._run(f, inject_before_push=[{"ref": REF_A, "sha": f.base}])
        self.assertEqual(code, EXIT_FAIL, out)
        self.assertEqual(result["verdict"], "REJECTED_VERIFIED")

    def test_prepare_never_attempts(self):
        f = self.fixture()
        f.write_config(fake_push={"mode": "commit_hide", "exit": 128, "output": HUNG_UP}, verify_unavailable=True)
        code, out, result = f.run(publish=False)
        self.assertEqual(code, EXIT_PREPARED, out)
        self.assertIsNone(result["push"])
        self.assertEqual(f.remote_refs().get(REF_A), None)


SUCCESS_VERDICTS = {"PUBLISHED_VERIFIED", "TARGETS_VERIFIED_PUSH_UNCONFIRMED"}


class TestR5FetchedIdentity(_Base):
    """R5: identity/parent/diff/byte evidence is bound to the ref actually fetched, not to the ls-remote answer."""

    def _race(self, injections, **extra):
        f = self.fixture()
        f.write_config(inject_between_query_and_fetch=injections, **extra)
        code, out, result = f.run(publish=True)
        return f, code, out, result, f.remote_refs()

    def _not_success(self, code, out, result, verdict="PUBLISH_OUTCOME_UNCERTAIN"):
        self.assertNotEqual(code, EXIT_PUBLISHED, out)
        self.assertNotIn(result["verdict"], SUCCESS_VERDICTS)
        self.assertEqual(result["verdict"], verdict, out)
        self.assertIn("after.reread_complete", failed_checks(result))

    def _check_identity_kept(self, result, refs, ref, key):
        self.assertEqual(result["remote_after"][key], refs.get(ref))       # the ACTUAL fetched identity is reported

    def test_a_changed_after_query(self):
        for op in ("ancestor", "fork", "descendant"):
            with self.subTest(op=op):
                f, code, out, result, refs = self._race([{"ref": REF_A, "op": op}])
                self._not_success(code, out, result)
                self.assertEqual(result["remote_after"]["query_ref_a"], f.evidence)
                self.assertNotEqual(refs[REF_A], f.evidence)
                self._check_identity_kept(result, refs, REF_A, "ref_a")
                self.assertTrue(any("changed during re-read" in r for r in result["remote_after"]["reasons"]))

    def test_b_descendant_old_archive_still_reachable(self):
        f, code, out, result, refs = self._race([{"ref": REF_B, "op": "descendant"}])
        self._not_success(code, out, result)
        old_b = result["remote_after"]["query_ref_b"]
        self.assertEqual(git(f.remote, "rev-parse", refs[REF_B] + "^", env=f.env), old_b)   # old archive reachable
        self._check_identity_kept(result, refs, REF_B, "ref_b")
        self.assertNotIn("after.ref_b_parent_is_base", [c["name"] for c in result["checks"] if c["ok"]])

    def test_both_changed(self):
        f, code, out, result, refs = self._race([{"ref": REF_A, "op": "descendant"}, {"ref": REF_B, "op": "descendant"}])
        self._not_success(code, out, result)
        self._check_identity_kept(result, refs, REF_A, "ref_a")
        self._check_identity_kept(result, refs, REF_B, "ref_b")

    def test_target_disappears_after_query(self):
        f, code, out, result, refs = self._race([{"ref": REF_B, "op": "delete"}])
        self._not_success(code, out, result)
        self.assertIsNone(result["remote_after"]["ref_b"])

    def test_race_with_nonzero_push_keeps_code(self):
        f, code, out, result, refs = self._race([{"ref": REF_A, "op": "descendant"}],
                                                fake_push={"mode": "commit_hide", "exit": 128, "output": HUNG_UP})
        self._not_success(code, out, result)
        self.assertEqual(result["push"]["exit"], 128)

    def test_idempotent_rerun_with_race_is_not_verified_and_not_attributed(self):
        f = self.fixture()
        code, out, _ = f.run(publish=True)
        self.assertEqual(code, EXIT_PUBLISHED, out)
        f.write_config(inject_between_query_and_fetch=[{"ref": REF_B, "op": "descendant"}])
        code, out, result = f.run(publish=True)
        self.assertEqual(result["push"]["refspecs"], [])
        self._not_success(code, out, result, verdict="NOT_VERIFIED_NOTHING_PUSHED")
        self.assertIn("not attributed to this run", out)

    def test_no_change_still_verifies(self):
        f, code, out, result, refs = self._race([])
        self.assertEqual(code, EXIT_PUBLISHED, out)
        self.assertEqual(result["remote_after"]["ref_a"], result["remote_after"]["query_ref_a"])


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
    "createonly": (r"function Get-CreateOnlyLeaseArgs\(\[string\[\]\]\$refs\) \{.*?\n\}",
                   "function Get-CreateOnlyLeaseArgs([string[]]$refs) {\n    return @()\n}",
                   "TestR1CreateOnly.test_ancestor_race_b"),
    "testremote": (r"function Test-LocalFixtureRemote\(\[string\]\$u\) \{",
                   "function Test-LocalFixtureRemote([string]$u) { return $null",
                   "TestR2TestModeRemote.test_unmarked_local_repo_refused"),
    "rejectreceipt": (r"function Test-CompleteAtomicRejection\(\[string\]\$receipt, \[string\[\]\]\$specs\) \{",
                      "function Test-CompleteAtomicRejection([string]$receipt, [string[]]$specs) { return $true",
                      "TestR4Outcome.test_garbled_rejection_receipt_is_not_proof"),
    "uncertain": (r"if \(-not \$script:VerifyComplete\) \{\n    Write-Log 'publish was attempted",
                  "if (@($Result.push.output.Split(\"`n\") | Where-Object { $_ -match '^[\\* +]\\t' }).Count -eq 0) { Finish 'FAIL' 1 }\n"
                  "if (-not $script:VerifyComplete) {\n    Write-Log 'publish was attempted",
                  "TestR4Outcome.test_committed_receipt_lost_reread_unavailable"),
    "fetchedidentity": (r"\$fetched\[\$pair\[1\]\] = \$got +# GUARD:fetchedidentity\n +if \(\$got -ne \$pair\[0\]\) \{",
                        "$fetched[$pair[1]] = $pair[0]\n                if ($false) {",
                        "TestR5FetchedIdentity.test_b_descendant_old_archive_still_reachable"),
    "rereadstable": (r"if \(\$got -ne \$pair\[0\]\) \{",
                     "if ($false) {",
                     "TestR5FetchedIdentity.test_both_changed"),
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
                return "pass" if (code == EXIT_FAIL and f.remote_refs() == before) else "fail"
            finally:
                f.close()
        cls, method = target.split(".")
        test = globals()[cls](method)
        test.run_script = script
        res = unittest.TestResult()
        original = Fixture.run

        def patched(self_, publish=False, script=SCRIPT, remote=None, test_config=True):
            return original(self_, publish=publish, script=test.run_script, remote=remote, test_config=test_config)
        Fixture.run = patched
        try:
            test.run(res)
        finally:
            Fixture.run = original
        # R3: a skipped (or not run) case is neither a passing control nor a detection.
        if res.skipped or res.testsRun == 0:
            return "skip"
        return "pass" if res.wasSuccessful() else "fail"

    def test_each_weakened_guard_is_detected(self):
        text = SCRIPT.read_text(encoding="utf-8")
        tmp = Path(tempfile.mkdtemp(prefix="hark14-weak-"))
        self.addCleanup(shutil.rmtree, tmp, True)
        lines = []
        for name, (pattern, replacement, target) in GUARDS.items():
            with self.subTest(guard=name):
                weak, n = re.subn(pattern, lambda m: replacement, text, count=1, flags=re.S)
                self.assertEqual(n, 1, f"pattern for {name} not found")
                control = self._run_case(target, SCRIPT)
                if control == "skip":                     # platform condition missing: not a valid control
                    lines.append(f"{name}\tcontrol=skipped\tweakened=not-run\t{target}")
                    continue
                self.assertEqual(control, "pass", f"control for {name} must pass")
                weak_path = tmp / f"weak-{name}.ps1"
                weak_path.write_text(weak, encoding="utf-8")
                weakened = self._run_case(target, weak_path)
                state = {"fail": "detected", "pass": "MISSED", "skip": "skipped"}[weakened]
                lines.append(f"{name}\tcontrol=pass\tweakened={state}\t{target}")
                self.assertEqual(weakened, "fail", f"weakening {name}: {state}")
        out = os.environ.get("HARK14_WEAKENING_LOG")
        if out:
            Path(out).write_text("\n".join(lines) + "\n", encoding="utf-8")


class TestR3SkipAccounting(unittest.TestCase):
    """R3: the weakening helper reports skip / pass / fail separately (no PowerShell or Git needed)."""

    def _helper_result(self, body):
        class Stand(unittest.TestCase):
            pass
        Stand.test_x = body
        globals()["_R3Stand"] = Stand                             # visible to the helper, not to discovery
        try:
            return TestGuardWeakening("test_each_weakened_guard_is_detected")._run_case("_R3Stand.test_x", SCRIPT)
        finally:
            del globals()["_R3Stand"]

    def test_skip_is_not_pass(self):
        self.assertEqual(self._helper_result(unittest.skip("platform condition missing")(lambda self: None)), "skip")

    def test_fail_is_fail(self):
        self.assertEqual(self._helper_result(lambda self: self.fail("control failure")), "fail")

    def test_pass_is_pass(self):
        self.assertEqual(self._helper_result(lambda self: None), "pass")


if __name__ == "__main__":
    unittest.main()
