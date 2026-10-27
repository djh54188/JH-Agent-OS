"""Hark-派单-24/25: offline tests of the task01 user entry (no Node, no CLI, no model, no network).

Runs happen in TEMPORARY CLONES of this repository whose HEAD is a temporary commit carrying the
current entry files; each test lays out <parent>/ctx-<stamp> with <parent>/repo-<stamp> pointing
at the clone, exactly the layout the .ps1 creates. "node" is tests/fixtures/hark24/fake_node.py,
the "CLI" is B's CONSTRUCTED tests/fixtures/hark21/fake_cli_t01.py; the adapter, its registry and
evidence, and B's checker are the real, unchanged code. No result here is a real model's work.

PowerShell tests run the REAL .ps1 with the official pwsh 7 when it is available (HARK24_PWSH or
/workspace/work/pwsh/pwsh); Windows PowerShell 5.1 is not available here and is not claimed.
Guard weakening edits a COPY of entry.py / the .ps1 inside a temporary clone; production code has
no weakening hook.
"""
import hashlib
import json
import os
import shutil
import stat
import subprocess
import sys
import tempfile
import threading
import time
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, "tests", "fixtures", "hark21"))
import build  # noqa: E402  (B's builders, read-only use)

ENTRY_REL = "scripts/gemini_task01_run/entry.py"
PS1_REL = "scripts/win/hark24-gemini-task01.ps1"
FIX = "tests/fixtures/hark24"
SENTINEL = "SENTINEL-hark24-not-a-key-7f3a9c"
SHORT_SENTINEL = "Zq9"
ENDPOINT = "https://constructed-endpoint.invalid/base"
MODEL = "constructed-explicit-model"
NEW_FILES = (ENTRY_REL, PS1_REL) + tuple(f"{FIX}/{n}" for n in (
    "fake_node.py", "git_spy.py", "audit_run.py", "inproc_run.py", "stub_entry.py", "probe_entry.py"))
PWSH = os.environ.get("HARK24_PWSH") or ("/workspace/work/pwsh/pwsh" if os.path.exists("/workspace/work/pwsh/pwsh")
                                         else shutil.which("pwsh"))
REAL_GIT = shutil.which("git")
_COUNTER = [0]


def _git(repo, *args):
    return subprocess.run(["git", "-C", repo, *args], check=True, capture_output=True, text=True).stdout.strip()


def _have_baseline():
    try:
        _git(ROOT, "merge-base", "--is-ancestor", "95d941ec1025470dad4fc1d00e9f4775361e3b91", "HEAD")
        _git(ROOT, "cat-file", "-e", "f6bf47c59688c43d927dede01b59f772520815d8^{commit}")
        return True
    except (subprocess.CalledProcessError, OSError):
        return False


HAVE = _have_baseline()


def stamp():
    _COUNTER[0] += 1
    n = _COUNTER[0]
    return f"20261027-{n // 3600 % 24:02d}{n // 60 % 60:02d}{n % 60:02d}"


def sha256(b):
    return hashlib.sha256(b).hexdigest()


class Clone:
    """A temporary clone at HEAD plus the current entry files, committed (clean)."""

    def __init__(self, entry_source=None):
        self.base = os.path.realpath(tempfile.mkdtemp(prefix="hark24-"))
        self.repo = os.path.join(self.base, "clone repo")
        subprocess.run(["git", "clone", "-q", "--no-hardlinks", ROOT, self.repo], check=True)
        _git(self.repo, "checkout", "-q", "--detach", _git(ROOT, "rev-parse", "HEAD"))
        for rel in NEW_FILES:
            dest = os.path.join(self.repo, *rel.split("/"))
            os.makedirs(os.path.dirname(dest), exist_ok=True)
            shutil.copyfile(os.path.join(ROOT, *rel.split("/")), dest)
        gone = os.path.join(self.repo, FIX, "guarded_prepare.py")
        if os.path.exists(gone):
            os.remove(gone)
        if entry_source is not None:
            with open(os.path.join(self.repo, *ENTRY_REL.split("/")), "w", encoding="utf-8") as fh:
                fh.write(entry_source)
        self.head = self.commit_all("test: current entry files")

    def commit_all(self, msg):
        _git(self.repo, "add", "-A")
        if _git(self.repo, "status", "--porcelain"):
            _git(self.repo, "-c", "user.name=t", "-c", "user.email=t@invalid", "commit", "-q", "-m", msg)
        self.head = _git(self.repo, "rev-parse", "HEAD")
        return self.head

    def edit(self, rel, edits):
        path = os.path.join(self.repo, *rel.split("/"))
        with open(path, encoding="utf-8") as fh:
            src = fh.read()
        for old, new in edits:
            assert src.count(old) == 1, (rel, old)
            src = src.replace(old, new)
        with open(path, "w", encoding="utf-8") as fh:
            fh.write(src)
        return self.commit_all("test: weakened copy")

    def tree(self, rel):
        return _git(self.repo, "rev-parse", f"HEAD:{rel}")

    def bare_remote(self):
        remote = os.path.join(self.base, "remote fixture.git")
        if not os.path.exists(remote):
            subprocess.run(["git", "clone", "-q", "--bare", self.repo, remote], check=True)
            _git(remote, "config", "uploadpack.allowAnySHA1InWant", "true")
            open(os.path.join(remote, "hark24-test-fixture"), "w").close()
        else:
            subprocess.run(["git", "-C", self.repo, "push", "-q", "--force", remote, "HEAD:refs/heads/t"], check=True)
        return remote

    def close(self):
        for d, _, _ in os.walk(self.base):
            try:
                os.chmod(d, 0o755)
            except OSError:
                pass
        shutil.rmtree(self.base, ignore_errors=True)


def read(path, mode="r"):
    with open(path, mode, **({} if "b" in mode else {"encoding": "utf-8"})) as fh:
        return fh.read()


class _Base(unittest.TestCase):
    clone = None

    @classmethod
    def setUpClass(cls):
        if HAVE:
            cls.clone = Clone()

    @classmethod
    def tearDownClass(cls):
        if cls.clone:
            cls.clone.close()

    def setUp(self):
        if not HAVE:
            self.skipTest("accepted baseline / fixed commit not in this clone")
        self.parent = os.path.join(self.clone.base, f"p {self._testMethodName[:30]} {time.time_ns()}")
        os.makedirs(self.parent)
        self.env = {k: v for k, v in os.environ.items() if k in ("PATH", "SYSTEMROOT", "LANG", "TMPDIR")}
        self.env.update(GEMINI_API_KEY=SENTINEL, HARK24_UNRELATED="must-not-pass",
                        GOOGLE_GEMINI_BASE_URL="https://user-env-endpoint.invalid/" + SENTINEL,
                        HTTPS_PROXY="http://u:" + SENTINEL + "@proxy.invalid:1")
        self.node = self.make_node()

    # ---------------------------------------------------------------- fixtures
    def make_node(self, **cfg):
        d = os.path.join(self.parent, f"node-{time.time_ns()}")
        os.makedirs(d)
        path = os.path.join(d, "node")
        with open(path, "w", encoding="utf-8") as fh:
            fh.write(f"#!{sys.executable}\n" + read(os.path.join(self.clone.repo, FIX, "fake_node.py")))
        os.chmod(path, 0o755)
        self.record = os.path.join(self.parent, "host-record.jsonl")
        self.node_cfg = os.path.join(d, "fake-node.json")
        self.write_node_cfg(**cfg)
        return path

    def write_node_cfg(self, **cfg):
        cfg.setdefault("version", "v22.23.4")
        cfg.setdefault("record", self.record)
        cfg.setdefault("scenario", os.path.join(self.parent, "scenario.json"))
        with open(self.node_cfg, "w", encoding="utf-8") as fh:
            json.dump(cfg, fh)

    def layout(self, parent=None, repo=None):
        """A new ctx-<stamp> path with <parent>/repo-<stamp> -> the clone."""
        parent = parent or self.parent
        st = stamp()
        link = os.path.join(parent, f"repo-{st}")
        os.symlink(repo or self.clone.repo, link)
        return os.path.join(parent, f"ctx-{st}")

    def entry_path(self, clone=None):
        return os.path.join((clone or self.clone).repo, *ENTRY_REL.split("/"))

    def entry(self, *args, env=None, wrapper=None):
        cmd = [sys.executable, "-I", "-B"] + (wrapper or []) + [self.entry_path(), *args]
        done = subprocess.run(cmd, env=env or self.env, capture_output=True, text=True, timeout=300)
        self.last = (done.returncode, done.stdout, done.stderr)
        return done.returncode, done.stdout, done.stderr

    def parse(self, out):
        return json.loads(out)

    def prepare(self, work_root=None, **over):
        work_root = work_root or self.layout()
        opts = {"--node": self.node, "--cli-js": os.path.join(self.clone.repo, "tests", "fixtures", "hark21",
                                                              "fake_cli_t01.py"),
                "--endpoint": ENDPOINT, "--model": MODEL, "--key-env-name": "GEMINI_API_KEY"}
        opts.update(over)
        args = ["prepare", "--work-root", work_root, "--entry-commit", self.clone.head]
        for k, v in opts.items():
            if v is not None:
                args += [k, v]
        code, out, err = self.entry(*args)
        res = self.parse(out)
        return code, res, work_root

    def ready(self, **sc):
        code, res, wr = self.prepare()
        self.assertEqual((code, res["outcome"]), (0, "PREPARED"), self.last)
        self.scenario(wr, **sc)
        return wr, res["context_sha256"]

    def scenario(self, work_root, delivery=build.ALIGNED_DELIVERY, **extra):
        sc = {"tools": build.good_tools(os.path.join(work_root, "target")), "delivery": delivery}
        sc.update(extra)
        with open(os.path.join(self.parent, "scenario.json"), "w", encoding="utf-8") as fh:
            json.dump(sc, fh, ensure_ascii=False)

    def run_args(self, wr, sha, accept=True, authorize=True, timeout="60"):
        args = ["run-real", "--context", os.path.join(wr, "context.json"), "--context-sha256", sha,
                "--collect-timeout", timeout]
        if authorize:
            args.append("--authorize-real-model-call")
        if accept:
            args += ["--accept-boundary", self.phrase()]
        return args

    def run_real(self, wr, sha, env=None, wrapper=None, **kw):
        code, out, err = self.entry(*self.run_args(wr, sha, **kw), env=env, wrapper=wrapper)
        return code, self.parse(out)

    def candidate(self, wr, sha, env=None):
        code, out, err = self.entry("candidate", "--context", os.path.join(wr, "context.json"),
                                    "--context-sha256", sha, env=env)
        return code, self.parse(out)

    def phrase(self):
        src = read(self.entry_path())
        start = src.index("ACCEPT_PHRASE = (")
        return eval(src[start + len("ACCEPT_PHRASE = "):src.index(")", start) + 1])  # noqa: S307

    def host_calls(self):
        if not os.path.exists(self.record):
            return []
        return [json.loads(l) for l in read(self.record).splitlines() if l.strip()]

    def dispatches(self, settle=0.0):
        end = time.time() + settle
        while True:
            found = [c for c in self.host_calls() if "--version" not in c["argv_tail"]]
            if found or time.time() >= end:
                return found
            time.sleep(0.1)

    def registry_runs(self, wr):
        path = os.path.join(wr, "evidence", "registry.jsonl")
        if not os.path.isfile(path):
            return []
        recs = [json.loads(l) for l in read(path).splitlines() if l.strip()]
        return sorted({r["run_id"] for r in recs if r["event"] == "reserved"})

    def all_bytes_under(self, *roots):
        for root in roots:
            for d, _, fs in os.walk(root):
                for f in fs:
                    yield os.path.join(d, f), read(os.path.join(d, f), "rb")

    def rewrite_context(self, wr, fn):
        p = os.path.join(wr, "context.json")
        ctx = json.loads(read(p))
        new = fn(ctx)
        data = (json.dumps(new, ensure_ascii=False, indent=1, sort_keys=True) + "\n").encode()
        os.chmod(p, 0o644)
        with open(p, "wb") as fh:
            fh.write(data)
        return sha256(data)

    def no_trace(self):
        self.assertNotIn("Traceback", self.last[2])


@unittest.skipUnless(HAVE, "baseline not available")
class TestPrepare(_Base):
    def test_prepared_context_is_non_sensitive_and_dispatches_nothing(self):
        code, res, wr = self.prepare()
        self.assertEqual((code, res["outcome"]), (0, "PREPARED"))
        self.assertEqual(res["context_sha256"], sha256(read(os.path.join(wr, "context.json"), "rb")))
        ctx = json.loads(read(os.path.join(wr, "context.json")))
        self.assertEqual(ctx["status"], "PREPARED_NOT_ADMITTED")
        self.assertFalse(ctx["key_value_checked"] or ctx["node"]["version_verified"] or ctx["cli"]["version_verified"])
        self.assertFalse(ctx["endpoint"]["protocol_verified"])
        self.assertEqual(ctx["export_files"], 26)
        self.assertFalse(os.path.exists(ctx["evidence_root"]))
        self.assertEqual(self.host_calls(), [])
        for path, data in self.all_bytes_under(wr):
            self.assertNotIn(SENTINEL.encode(), data, path)
        self.assertNotIn(SENTINEL, self.last[1] + self.last[2])

    def spy_env(self):
        d = os.path.join(self.parent, f"spy-{time.time_ns()}")
        os.makedirs(d)
        with open(os.path.join(d, "git"), "w", encoding="utf-8") as fh:
            fh.write(f"#!{sys.executable}\n" + read(os.path.join(self.clone.repo, FIX, "git_spy.py")))
        os.chmod(os.path.join(d, "git"), 0o755)
        rec = os.path.join(self.parent, "git-spy.jsonl")
        with open(os.path.join(d, "git-spy.json"), "w", encoding="utf-8") as fh:
            json.dump({"record": rec, "real_git": REAL_GIT, "sentinels": [SENTINEL, "must-not-pass"]}, fh)
        env = dict(self.env, PATH=d + os.pathsep + self.env["PATH"])
        return env, rec

    def test_prepare_children_get_a_minimal_environment(self):
        env, rec = self.spy_env()
        code, out, err = self.entry("prepare", "--work-root", self.layout(), "--entry-commit", self.clone.head,
                                    "--model", MODEL, env=env)
        self.assertIn(code, (0, 4), err)
        calls = [json.loads(l) for l in read(rec).splitlines()]
        self.assertGreaterEqual(len(calls), 4)                     # ours + B's exporter (ls-tree / cat-file)
        self.assertTrue(any("cat-file" in c["argv"] for c in calls))      # B's exporter's own git calls
        for c in calls:
            self.assertFalse(c["sentinel_in_env"], c)
            self.assertFalse({"GEMINI_API_KEY", "GOOGLE_GEMINI_BASE_URL", "HTTPS_PROXY", "HARK24_UNRELATED"}
                             & set(c["env_names"]), c)

    def test_missing_or_unverified_settings_are_needs_setup(self):
        cases = {
            "no-endpoint": {"--endpoint": None}, "no-model": {"--model": None},
            "auto-model": {"--model": "auto-gemini-2.5"}, "alias-model": {"--model": "pro"},
            "other-key-name": {"--key-env-name": "MY_KEY"}, "no-key-name": {"--key-env-name": None},
            "no-node": {"--node": None}, "no-cli": {"--cli-js": os.path.join(self.parent, "absent.js")},
            "proxy-name": {"--proxy-env-name": "ALL_PROXY"},
        }
        for name, over in cases.items():
            with self.subTest(case=name):
                code, res, wr = self.prepare(**over)
                self.assertEqual((code, res["outcome"]), (4, "NEEDS_SETUP"), self.last)
                self.assertEqual(json.loads(read(os.path.join(wr, "context.json")))["status"], "NEEDS_SETUP")

    def test_endpoint_with_credentials_or_query_is_refused_without_echo(self):
        for name, url in {"userinfo": f"https://user:{SENTINEL}@h.invalid/",
                          "query": f"https://h.invalid/?key={SENTINEL}",
                          "fragment": f"https://h.invalid/#{SENTINEL}", "scheme": f"ftp://h.invalid/{SENTINEL}"}.items():
            with self.subTest(case=name):
                code, res, wr = self.prepare(**{"--endpoint": url})
                self.assertEqual(res["outcome"], "NEEDS_SETUP")
                for path, data in self.all_bytes_under(wr):
                    self.assertNotIn(SENTINEL.encode(), data, path)
                self.assertNotIn(SENTINEL, self.last[1] + self.last[2])

    def test_layout_existing_root_and_wrong_commit_refused(self):
        code, res, wr = self.prepare()
        before = sorted(os.listdir(wr))
        code, res, _ = self.prepare(work_root=wr)
        self.assertEqual((code, res["outcome"]), (2, "REFUSED"))
        self.assertEqual(sorted(os.listdir(wr)), before)
        for bad in (os.path.join(self.parent, "ctx"), os.path.join(self.parent, "ctx-20261027-999999x")):
            code, out, _ = self.entry("prepare", "--work-root", bad, "--entry-commit", self.clone.head)
            self.assertEqual(json.loads(out)["outcome"], "REFUSED")
            self.assertFalse(os.path.exists(bad))
        other = os.path.join(self.parent, "ctx-20261027-235959")        # no repo-<stamp> beside it
        code, out, _ = self.entry("prepare", "--work-root", other, "--entry-commit", self.clone.head)
        self.assertEqual(json.loads(out)["outcome"], "REFUSED")
        code, out, _ = self.entry("prepare", "--work-root", self.layout(), "--entry-commit", "0" * 40)
        self.assertEqual(json.loads(out)["outcome"], "REFUSED")

    def test_export_keeps_task_and_source_bytes(self):
        code, res, wr = self.prepare()
        ctx = json.loads(read(os.path.join(wr, "context.json")))
        self.assertEqual(ctx["task_sha256"], "5ed4f438c57918d7924ea00bf02134c529b20bf9556e6b01c6dffa7830e80518")
        sys.path.insert(0, os.path.join(ROOT, "scripts", "gemini_task01"))
        import pins
        for rel, (_, size, digest) in pins.SOURCE_FILES.items():
            data = read(os.path.join(wr, "target", *rel.split("/")), "rb")
            self.assertEqual((len(data), sha256(data)), (size, digest))


@unittest.skipUnless(HAVE, "baseline not available")
class TestRunReal(_Base):
    def test_one_dispatch_through_the_real_adapter_and_checker(self):
        wr, sha = self.ready()
        code, res = self.run_real(wr, sha)
        self.assertEqual((code, res["outcome"], res["mechanical"]), (0, "COLLECTED", "all_pass"), self.last)
        self.assertEqual(res["semantic_review"], "required")
        self.assertEqual(res["task_outcome"], "not decided by this entry")
        self.assertEqual(res["host_versions"], {"node": "v22.23.4", "cli": "0.65.0"})
        self.assertEqual(len(self.dispatches()), 1)
        self.assertEqual(len(self.registry_runs(wr)), 1)
        call = self.dispatches()[0]
        self.assertTrue(call["key_present"])
        self.assertEqual(call["key_len"], len(SENTINEL))
        self.assertEqual(call["endpoint"], ENDPOINT)                  # the context's, not the user env's
        self.assertTrue(set(call["env_names"]) <= {"PATH", "SYSTEMROOT", "LANG", "GEMINI_API_KEY",
                                                   "GOOGLE_GEMINI_BASE_URL", "GEMINI_CLI_HOME", "HOME",
                                                   "USERPROFILE", "GEMINI_CLI_NO_RELAUNCH", "NO_COLOR",
                                                   "FAKE_T01_SCENARIO", "LC_CTYPE"}, call["env_names"])
        self.assertEqual(call["argv_tail"][call["argv_tail"].index("-m") + 1], MODEL)
        for path, data in self.all_bytes_under(wr):
            self.assertNotIn(SENTINEL.encode(), data, path)
        self.assertNotIn(SENTINEL, self.last[1] + self.last[2])

    def test_second_run_and_rebound_marker_are_frozen(self):
        wr, sha = self.ready()
        self.run_real(wr, sha)
        code, res = self.run_real(wr, sha)
        self.assertEqual((code, res["outcome"]), (5, "FROZEN"))
        wr2, sha2 = self.ready()
        code, res = self.run_real(wr2, sha2)
        self.assertEqual((code, res["outcome"]), (5, "FROZEN"))
        # re-point the JSON marker (and every other derived path) with a matching new digest
        for field, value in (("dispatch_marker", os.path.join(self.parent, "other.marker")),
                             ("evidence_root", os.path.join(wr2, "evidence2")),
                             ("repo", os.path.join(self.parent, "elsewhere")),
                             ("workspace", os.path.join(wr2, "target2")),
                             ("export_manifest", os.path.join(self.parent, "m.json"))):
            with self.subTest(field=field):
                sha3 = self.rewrite_context(wr2, lambda c: dict(c, **{field: value}))
                code, res = self.run_real(wr2, sha3)
                self.assertEqual((code, res["outcome"]), (2, "REFUSED"), res)
                self.assertIn(field, res["summary"])
        self.assertEqual(len(self.dispatches()), 1)
        self.assertTrue(os.path.exists(os.path.join(self.parent, "task01-real-dispatch.marker")))

    def test_concurrent_entries_dispatch_once(self):
        roots = [self.ready() for _ in range(3)]
        outs = []
        threads = [threading.Thread(target=lambda r=r: outs.append(self.run_real(*r))) for r in roots]
        [t.start() for t in threads]
        [t.join() for t in threads]
        self.assertEqual(sorted(o[1]["outcome"] for o in outs), ["COLLECTED", "FROZEN", "FROZEN"])
        self.assertEqual(len(self.dispatches()), 1)
        self.assertEqual(sum(len(self.registry_runs(r[0])) for r in roots), 1)

    def test_refusals_before_dispatch_leave_no_marker(self):
        wr, sha = self.ready()
        for name, kw in {"no-authorize": {"authorize": False}, "no-phrase": {"accept": False}}.items():
            with self.subTest(case=name):
                code, res = self.run_real(wr, sha, **kw)
                self.assertEqual((code, res["outcome"]), (2, "REFUSED"))
        code, res = self.run_real(wr, "0" * 64)
        self.assertEqual((code, res["outcome"]), (2, "REFUSED"))
        env = {k: v for k, v in self.env.items() if k != "GEMINI_API_KEY"}
        code, res = self.run_real(wr, sha, env=env)
        self.assertEqual((code, res["outcome"]), (4, "NEEDS_SETUP"))
        self.write_node_cfg(version="v24.0.0")
        code, res = self.run_real(wr, sha)
        self.assertEqual((code, res["outcome"]), (2, "REFUSED"))
        self.write_node_cfg()
        self.scenario(wr, version="0.66.0")
        code, res = self.run_real(wr, sha)
        self.assertEqual((code, res["outcome"]), (2, "REFUSED"))
        self.assertEqual(self.dispatches(), [])
        for p in (os.path.join(self.parent, "task01-real-dispatch.marker"), os.path.join(wr, "run-real.started"),
                  os.path.join(wr, "evidence")):
            self.assertFalse(os.path.exists(p), p)

    def test_wrong_shapes_and_types_are_stable_refusals(self):
        wr, sha = self.ready()
        p = os.path.join(wr, "context.json")
        original = read(p, "rb")
        for name, data in {"array": b"[1, 2]\n", "number": b"7\n", "string": b"\"x\"\n", "garbage": b"{x\n",
                           "model-int": original.replace(json.dumps(MODEL).encode(), b"42"),
                           "attempt-str": original.replace(b"\"attempt\": 1", b"\"attempt\": \"1\""),
                           "missing-field": original.replace(b"\"auth_type\"", b"\"auth_typo\"")}.items():
            with self.subTest(case=name):
                with open(p, "wb") as fh:
                    fh.write(data)
                code, res = self.run_real(wr, sha256(data))
                self.assertEqual((code, res["outcome"]), (2, "REFUSED"), res)
                self.no_trace()
        with open(p, "wb") as fh:
            fh.write(original)
        self.assertEqual(self.dispatches(), [])

    def test_manifest_and_export_tampering_refused_without_out_of_scope_open(self):
        wr, sha = self.ready()
        outside = os.path.join(self.parent, "outside-export-sentinel.txt")
        with open(outside, "w") as fh:
            fh.write("outside")
        mp = os.path.join(wr, "export-manifest.json")
        original = read(mp, "rb")
        man = json.loads(original)
        man["files"]["../outside-export-sentinel.txt"] = {"blob": "0" * 40, "size": 7, "sha256": "0" * 64}
        bad = (json.dumps(man, indent=1, sort_keys=True) + "\n").encode()
        variants = {"manifest-entry": (bad, None), "manifest-entry-with-ctx-digest": (bad, sha256(bad))}
        for name, (data, ctx_digest) in variants.items():
            with self.subTest(case=name):
                os.chmod(mp, 0o644)
                with open(mp, "wb") as fh:
                    fh.write(data)
                use_sha = sha
                if ctx_digest:
                    use_sha = self.rewrite_context(wr, lambda c: dict(c, export_manifest_sha256=ctx_digest))
                obs = os.path.join(self.parent, f"audit-{name}.json")
                code, out, err = self.entry(*self.run_args(wr, use_sha),
                                            wrapper=[os.path.join(self.clone.repo, FIX, "audit_run.py"), obs])
                self.assertEqual(json.loads(out)["outcome"], "REFUSED")
                opened = json.loads(read(obs))["opened"]
                self.assertFalse([o for o in opened if "outside-export-sentinel" in o], opened)
                if ctx_digest:
                    self.rewrite_context(wr, lambda c: dict(c, export_manifest_sha256=sha256(original)))
        with open(mp, "wb") as fh:
            fh.write(original)
        sha = sha256(read(os.path.join(wr, "context.json"), "rb"))
        victim = os.path.join(wr, "target", "src", "memory.py")
        os.chmod(victim, 0o644)
        with open(victim, "ab") as fh:
            fh.write(b"\n# changed\n")
        code, res = self.run_real(wr, sha)
        self.assertEqual(res["outcome"], "REFUSED")
        os.remove(victim)
        code, res = self.run_real(wr, sha)
        self.assertEqual(res["outcome"], "REFUSED")
        self.assertEqual(self.dispatches(), [])

    def test_needs_setup_context_refused(self):
        code, res, wr = self.prepare(**{"--model": None})
        code, res2 = self.run_real(wr, res["context_sha256"])
        self.assertEqual(res2["outcome"], "NEEDS_SETUP")
        self.assertEqual(self.dispatches(), [])

    def test_timeout_is_unknown_frozen_and_not_killed(self):
        wr, sha = self.ready()
        self.write_node_cfg(sleep=6)
        code, res = self.run_real(wr, sha, timeout="1")
        self.assertEqual((code, res["outcome"]), (6, "TIMEOUT_UNKNOWN"))
        self.assertIn("billing is not proven stopped", res["summary"])
        os.kill(self.dispatches()[0]["pid"], 0)
        code, res = self.run_real(wr, sha)
        self.assertEqual(res["outcome"], "FROZEN")
        self.assertEqual(len(self.registry_runs(wr)), 1)
        time.sleep(7)

    def test_launch_failure_recorded_and_frozen(self):
        wr, sha = self.ready()
        self.write_node_cfg(unexecutable_after_cli_version_calls=2)
        code, res = self.run_real(wr, sha)
        self.assertEqual((code, res["outcome"]), (7, "LAUNCH_FAILED_RECORDED"), res)
        self.assertIs(res["dispatched"], False)
        self.assertEqual(self.dispatches(), [])
        os.chmod(self.node, 0o755)
        code, res = self.run_real(wr, sha)
        self.assertEqual(res["outcome"], "FROZEN")

    def inproc(self, fault, wr, sha):
        env = dict(self.env, HARK25_TEST_SENTINEL=SENTINEL)
        code, out, err = self.entry(*self.run_args(wr, sha), env=env,
                                    wrapper=[os.path.join(self.clone.repo, FIX, "inproc_run.py"), fault])
        self.assertNotIn(SENTINEL, out + err)
        self.no_trace()
        return code, json.loads(out)

    def test_post_dispatch_faults_are_classified_and_frozen(self):
        expect = {"lose_registry": (6, "DISPATCH_UNKNOWN", None), "corrupt_registry": (6, "DISPATCH_UNKNOWN", None),
                  "collect_raises": (8, "DISPATCHED_INCOMPLETE", True), "status_raises": (8, "DISPATCHED_INCOMPLETE", True),
                  "check_raises": (8, "DISPATCHED_INCOMPLETE", True)}
        for fault, (want_code, outcome, dispatched) in expect.items():
            with self.subTest(fault=fault):
                self.setUp()
                wr, sha = self.ready()
                code, res = self.inproc(fault, wr, sha)
                self.assertEqual((code, res["outcome"], res["dispatched"]), (want_code, outcome, dispatched), res)
                self.assertEqual(len(self.dispatches(settle=5)), 1)
                stored = json.loads(read(os.path.join(wr, "run-real-result.json")))
                self.assertEqual(stored["outcome"], outcome)
                self.assertIn("stage", stored)
                self.assertNotIn(SENTINEL, json.dumps(stored))
                code, res = self.run_real(wr, sha)
                self.assertEqual(res["outcome"], "FROZEN")
                self.assertEqual(len(self.dispatches()), 1)

    def test_registry_lost_by_host_side_is_incomplete_not_refused(self):
        wr, sha = self.ready()
        self.write_node_cfg(sabotage_registry=True)
        code, res = self.run_real(wr, sha)
        self.assertNotEqual(code, 0)
        self.assertTrue(res["dispatched"] in (True, None), res)
        self.assertIn(res["outcome"], ("DISPATCHED_INCOMPLETE", "DISPATCH_UNKNOWN", "COLLECTED"))
        self.assertEqual(len(self.dispatches()), 1)

    def test_result_not_writable_is_terminal_non_pass(self):
        wr, sha = self.ready()
        code, res = self.inproc("readonly_root", wr, sha)
        self.assertEqual((code, res["outcome"]), (9, "RESULT_NOT_RECORDED"), res)
        os.chmod(wr, 0o755)
        self.assertFalse(os.path.exists(os.path.join(wr, "run-real-result.json")))
        code, res = self.run_real(wr, sha)
        self.assertEqual(res["outcome"], "FROZEN")

    def test_host_failure_kept_as_is(self):
        wr, sha = self.ready(exit_code=1, result_status="error")
        code, res = self.run_real(wr, sha)
        self.assertEqual(res["outcome"], "COLLECTED")
        self.assertNotEqual(res["mechanical"], "all_pass")
        self.assertNotEqual(code, 0)

    def test_reversed_explanation_never_becomes_a_task_pass(self):
        wr, sha = self.ready(delivery=build.REVERSED_DELIVERY)
        code, res = self.run_real(wr, sha)
        self.assertEqual((res["outcome"], res["mechanical"]), ("COLLECTED", "all_pass"))
        self.assertEqual(res["semantic_review"], "required")
        self.assertNotIn('"PASS"', read(os.path.join(wr, "run-real-result.json")))


@unittest.skipUnless(HAVE, "baseline not available")
class TestCandidate(_Base):
    def assert_public_clean(self, res, values):
        blob = subprocess.run(["git", "-C", self.clone.repo, "show", f"{res['commit']}:{res['files'][0]}"],
                              capture_output=True, check=True).stdout
        for v in values:
            self.assertNotIn(v.encode(), blob)
        return json.loads(blob)

    def test_stages_are_explicit_and_not_collected_when_not_run(self):
        code, res, wr = self.prepare()
        sha = res["context_sha256"]
        code, c = self.candidate(wr, sha)
        self.assertEqual((code, c["outcome"], c["stage"], c["verified"], c["pushed"]),
                         (10, "CANDIDATE_PREPARE_ARCHIVE", "prepare-archive", True, False), c)
        self.assertEqual(c["parents"], [self.clone.head])
        self.assertEqual(len(c["files"]), 1)
        self.assertIsNone(c["push_command"])                         # origin is not the verified remote
        self.assertEqual(_git(self.clone.repo, "rev-parse", "HEAD"), self.clone.head)
        pub = self.assert_public_clean(c, [SENTINEL])
        self.assertEqual(pub["stage"], "prepare-archive")
        self.assertNotIn("check", pub)
        code, c2 = self.candidate(wr, "1" * 64)
        self.assertEqual(c2["outcome"], "REFUSED")

    def test_failure_archive_and_collected(self):
        wr, sha = self.ready()
        self.write_node_cfg(sleep=4)
        self.run_real(wr, sha, timeout="1")
        code, c = self.candidate(wr, sha)
        self.assertEqual((code, c["outcome"], c["stage"]), (11, "CANDIDATE_FAILURE_ARCHIVE", "failure-archive"), c)
        time.sleep(5)
        self.setUp()
        wr, sha = self.ready()
        self.run_real(wr, sha)
        code, c = self.candidate(wr, sha)
        self.assertEqual((code, c["outcome"], c["stage"], c["verified"]), (0, "CANDIDATE_COLLECTED", "collected", True))
        pub = self.assert_public_clean(c, [SENTINEL])
        self.assertEqual(pub["check"]["overall"], "all_pass")
        self.assertEqual(pub["check"]["semantic_status"], "required")

    def test_reflected_secret_in_host_text_is_not_public(self):
        for value, keep_env in ((SENTINEL, True), (SENTINEL, False), (SHORT_SENTINEL, True), (SHORT_SENTINEL, False)):
            with self.subTest(value=value, env_kept=keep_env):
                self.setUp()
                env = dict(self.env, GEMINI_API_KEY=value)
                code, res, wr = self.prepare()
                sha = res["context_sha256"]
                self.scenario(wr, delivery=build.ALIGNED_DELIVERY + "\nhost said: " + value,
                              stderr="error: key " + value + " rejected\n")
                code, out, err = self.entry(*self.run_args(wr, sha), env=env)
                self.assertNotIn(value, out + err)
                report = read(os.path.join(wr, "check-report.json"))
                self.assertIn(value, report)                           # the full report keeps it, locally
                cenv = env if keep_env else {k: v for k, v in env.items() if k != "GEMINI_API_KEY"}
                code, out, err = self.entry("candidate", "--context", os.path.join(wr, "context.json"),
                                            "--context-sha256", sha, env=cenv)
                c = json.loads(out)
                self.assertEqual(c["outcome"], "CANDIDATE_COLLECTED")
                self.assertNotIn(value, out + err)
                self.assert_public_clean(c, [value])


# ------------------------------------------------------------------ guard weakening (Python entry)
def _w_ready(t):
    return t.ready()


def _w_frozen(t):
    wr, sha = t.ready()
    t.run_real(wr, sha)
    return t.ready()


def _w_node(t):
    r = t.ready()
    t.write_node_cfg(version="v24.0.0")
    return r


def _w_context_sha(t):
    wr, sha = t.ready()
    p = os.path.join(wr, "context.json")
    data = read(p, "rb").replace(MODEL.encode(), b"constructed-other-model")
    with open(p, "wb") as fh:
        fh.write(data)
    return wr, sha                                                     # the REVIEWED digest, file changed


def _w_endpoint(t):
    wr, sha = t.ready()
    return wr, t.rewrite_context(wr, lambda c: dict(c, endpoint=dict(c["endpoint"], value=ENDPOINT + "?key=x")))


def _w_derived(t):
    wr, sha = t.ready()
    t.run_real(wr, sha)
    wr2, _ = t.ready()
    return wr2, t.rewrite_context(wr2, lambda c: dict(c, dispatch_marker=os.path.join(t.parent, "other.marker")))


def _check_dispatch(t, wr, sha, kw):
    before = len(t.dispatches())
    code, res = t.run_real(wr, sha, **kw)
    return {"outcome": res["outcome"], "exit": code, "dispatches": len(t.dispatches()) - before,
            "mechanical": res.get("mechanical"), "bad": len(t.dispatches()) - before > 0}


def _probe_manifest(t, clone):
    wr, sha = t.ready()
    outside = os.path.join(t.parent, "outside-export-sentinel.txt")
    with open(outside, "w") as fh:
        fh.write("outside")
    mp = os.path.join(wr, "export-manifest.json")
    man = json.loads(read(mp))
    man["files"]["../outside-export-sentinel.txt"] = {"size": 7, "sha256": "0" * 64}
    with open(mp, "w") as fh:
        json.dump(man, fh)
    obs = os.path.join(t.parent, "audit.json")
    code, out, err = t.entry(*t.run_args(wr, sha), wrapper=[os.path.join(clone.repo, FIX, "audit_run.py"), obs])
    opened = [o for o in json.loads(read(obs))["opened"] if "outside-export-sentinel" in o]
    return {"outcome": json.loads(out)["outcome"], "exit": code, "outside_opens": len(opened), "bad": bool(opened)}


def _probe_unknown(t, clone):
    wr, sha = t.ready()
    env = dict(t.env, HARK25_TEST_SENTINEL=SENTINEL)
    code, out, err = t.entry(*t.run_args(wr, sha), env=env, wrapper=[os.path.join(clone.repo, FIX, "inproc_run.py"),
                                                                    "lose_registry"])
    res = json.loads(out)
    return {"outcome": res["outcome"], "exit": code, "dispatched": res.get("dispatched"),
            "bad": res.get("dispatched") is False}


def _probe_stage_failure(t, clone):
    wr, sha = t.ready()
    code, out, err = t.entry(*t.run_args(wr, sha), wrapper=[os.path.join(clone.repo, FIX, "inproc_run.py"),
                                                            "status_raises"])
    present = os.path.exists(os.path.join(wr, "run-real-result.json"))
    return {"outcome": json.loads(out)["outcome"], "exit": code, "result_file": present, "bad": not present}


def _probe_public(t, clone):
    wr, sha = t.ready(delivery=build.ALIGNED_DELIVERY + "\nhost said: " + SENTINEL)
    t.run_real(wr, sha)
    code, c = t.candidate(wr, sha)
    blob = subprocess.run(["git", "-C", clone.repo, "show", f"{c['commit']}:{c['files'][0]}"],
                          capture_output=True).stdout if c.get("commit") else b""
    return {"outcome": c["outcome"], "exit": code, "sentinel_public": SENTINEL.encode() in blob,
            "bad": SENTINEL.encode() in blob}


def _probe_prepare_env(t, clone):
    env, rec = TestPrepare.spy_env(t)
    t.entry("prepare", "--work-root", t.layout(), "--entry-commit", clone.head, "--model", MODEL, env=env)
    leaks = [json.loads(l) for l in read(rec).splitlines() if json.loads(l)["sentinel_in_env"]]
    return {"leaking_children": len(leaks), "bad": bool(leaks)}


def _probe_stage_label(t, clone):
    code, res, wr = t.prepare()
    code, c = t.candidate(wr, res["context_sha256"])
    return {"outcome": c["outcome"], "exit": code, "bad": code == 0 or c["outcome"] == "CANDIDATE_COLLECTED"}


WEAKENING = [
    ("authorize", [("    if not args.authorize_real_model_call or args.accept_boundary != ACCEPT_PHRASE:\n",
                    "    if False:\n")], lambda t, c: _check_dispatch(t, *_w_ready(t), {"authorize": False})),
    ("single_dispatch", [("    if any(os.path.lexists(p) for p in (d[\"dispatch_marker\"], d[\"local_marker\"], "
                          "d[\"evidence_root\"], d[\"result\"])):\n", "    if False:\n"),
                         ("        write_new(d[\"dispatch_marker\"], canon({", "        (lambda *a: None)(d[\"dispatch_marker\"], canon({")],
     lambda t, c: _check_dispatch(t, *_w_frozen(t), {})),
    ("node_version", [("    if node_v != PINNED_NODE_VERSION or not gem.version_is_pinned(cli_v):\n",
                       "    if not gem.version_is_pinned(cli_v):\n")], lambda t, c: _check_dispatch(t, *_w_node(t), {})),
    ("key_presence", [("    if setup:\n", "    if False:\n")],
     lambda t, c: _check_dispatch(t, *_w_ready(t), {"env": {k: v for k, v in t.env.items() if k != "GEMINI_API_KEY"}})),
    ("endpoint", [("    problems += endpoint_problems(ctx[\"endpoint\"].get(\"value\"))\n", "")],
     lambda t, c: _check_dispatch(t, *_w_endpoint(t), {})),
    ("reviewed_context_sha", [("        if sha256_bytes(data) != args.context_sha256:\n"
                               "            raise Refused(\"context bytes are not the reviewed context (sha256 differs)\")\n", "")],
     lambda t, c: _check_dispatch(t, *_w_context_sha(t), {})),
    ("derived_paths", [("        if ctx[k] != d[k]:\n            out.append(f\"context field {k} is not the derived path\")\n",
                        "        pass\n"),
                       ("    if any(os.path.lexists(p) for p in (d[\"dispatch_marker\"], ", "    if any(os.path.lexists(p) for p in (ctx[\"dispatch_marker\"], "),
                       ("        write_new(d[\"dispatch_marker\"], canon({", "        write_new(ctx[\"dispatch_marker\"], canon({")],
     lambda t, c: _check_dispatch(t, *_w_derived(t), {})),
    ("manifest_names", [("    if sha256_bytes(data) != want_sha:\n        return [\"export manifest changed\"]\n", ""),
                        ("    if not isinstance(files, dict) or set(files) != set(pins.SOURCE_FILES):\n"
                         "        return [\"export manifest file set is not the pinned src/ set\"]\n"
                         "    for rel, meta in files.items():\n",
                         "    for rel, meta in files.items():\n        sha256_file(os.path.join(d[\"workspace\"], *rel.split(\"/\")))\n"
                         "        continue\n")], _probe_manifest),
    ("unknown_after_submit", [("        if view == \"ok\" and events == [\"reserved\", \"dispatch_failed\"]:\n",
                               "        if view != \"ok\" or events == [\"reserved\", \"dispatch_failed\"]:\n")],
     _probe_unknown),
    ("post_dispatch_stage", [("    except Exception as exc:  # noqa: BLE001 - frozen, finite, non-passing; message not echoed\n",
                              "    except ZeroDivisionError as exc:\n")], _probe_stage_failure),
    ("public_whitelist", [("            out[\"evidence_digests\"] = {", "            out[\"full_report\"] = rep\n"
                           "            out[\"evidence_digests\"] = {")], _probe_public),
    ("prepare_env_scrub", [("    scrub_environ()                              # before any child process or lookup\n", "")],
     _probe_prepare_env),
    ("candidate_stage", [("            stage = \"prepare-archive\"\n", "            stage = \"collected\"\n")],
     _probe_stage_label),
]


@unittest.skipUnless(HAVE, "baseline not available")
class TestGuardWeakening(_Base):
    def test_each_guard_control_then_weakened(self):
        report = {}
        for label, edits, probe in WEAKENING:
            with self.subTest(guard=label):
                self.setUp()
                control = probe(self, self.clone)
                weak = Clone()
                saved = self.clone
                try:
                    weak.edit(ENTRY_REL, edits)
                    self.clone = weak
                    self.setUp()
                    weakened = probe(self, weak)
                finally:
                    self.clone = saved
                    weak.close()
                report[label] = {"control": control, "weakened": weakened}
                self.assertFalse(control["bad"], control)
                self.assertTrue(weakened["bad"], weakened)
                if "dispatches" in weakened:
                    self.assertEqual(control["dispatches"], 0)
                    self.assertNotEqual(control["exit"], 0)
                    self.assertEqual((weakened["outcome"], weakened["mechanical"], weakened["exit"]),
                                     ("COLLECTED", "all_pass", 0))
        print("WEAKENING25 " + json.dumps(report, sort_keys=True))


# ------------------------------------------------------------------ PowerShell (official pwsh 7, not PS 5.1)
@unittest.skipUnless(HAVE and PWSH, "pwsh not available")
class TestPowerShell(_Base):
    def ps(self, *args, ps1=None, env=None):
        cmd = [PWSH, "-NoProfile", "-NonInteractive", "-File", ps1 or os.path.join(self.clone.repo, *PS1_REL.split("/")),
               *args]
        done = subprocess.run(cmd, env=env or self.env, capture_output=True, text=True, timeout=300)
        self.last = (done.returncode, done.stdout, done.stderr)
        return done.returncode, done.stdout, done.stderr

    def make_testcfg(self, clone, trees=None):
        cfg = {"remote": clone.bare_remote()}
        if trees:
            cfg["trees"] = trees
        path = os.path.join(self.parent, f"testcfg-{time.time_ns()}.json")
        with open(path, "w", encoding="utf-8") as fh:
            json.dump(cfg, fh)
        return path

    def stub_clone(self):
        stub = Clone()
        shutil.copyfile(os.path.join(stub.repo, FIX, "stub_entry.py"), os.path.join(stub.repo, *ENTRY_REL.split("/")))
        stub.commit_all("test: stub entry")
        return stub

    def fake_context(self, clone, parent=None, body=None):
        wr = self.layout(parent=parent, repo=clone.repo)
        os.makedirs(wr)
        data = body if body is not None else (json.dumps({"schema": "x", "entry_commit": clone.head}) + "\n").encode()
        with open(os.path.join(wr, "context.json"), "wb") as fh:
            fh.write(data)
        return os.path.join(wr, "context.json"), sha256(data)

    def test_prepare_real_entry_exit_and_minimal_environment(self):
        parent = os.path.join(self.parent, "ps parent with space")
        os.makedirs(parent)
        env, rec = TestPrepare.spy_env(self)
        cfg = self.make_testcfg(self.clone)
        code, out, err = self.ps("-Commit", self.clone.head, "-Parent", parent, "-Python", sys.executable,
                                 "-Node", self.node, "-CliJs", self.node, "-Endpoint", ENDPOINT, "-Model", MODEL,
                                 "-KeyEnvName", "GEMINI_API_KEY", "-TestConfig", cfg, env=env)
        self.assertEqual(code, 0, (out, err))
        self.assertIn('"outcome": "PREPARED"', out)
        self.assertIn("HARK24-PS Prepare exit 0", out)
        calls = [json.loads(l) for l in read(rec).splitlines()]
        self.assertTrue(any("fetch" in c["argv"] for c in calls))          # the .ps1's own git fetch
        self.assertTrue(any("cat-file" in c["argv"] for c in calls))       # B's exporter under the driver
        for c in calls:
            self.assertFalse(c["sentinel_in_env"], c)
        self.assertNotIn(SENTINEL, out + err)
        code, out, err = self.ps("-Commit", self.clone.head, "-Parent", parent, "-Python", sys.executable,
                                 "-Node", self.node, "-TestConfig", cfg)
        self.assertEqual(code, 4, (out, err))
        self.assertIn('"outcome": "NEEDS_SETUP"', out)

    def test_driver_output_never_becomes_the_exit_code(self):
        stub = self.stub_clone()
        try:
            trees = {"scripts/gemini_task01_run": stub.tree("scripts/gemini_task01_run")}
            cfg = self.make_testcfg(stub, trees)
            parent = os.path.join(self.parent, "stub parent x")
            os.makedirs(parent)
            cases = [("none", 4, ""), ("single", 3, ""), ("multi", 4, ""), ("multi", 6, "warn: on stderr"),
                     ("single", 0, ""), ("none", 0, ""), ("multi", 7, "err"), ("single", 11, "")]
            for mode in ("RunReal", "Candidate"):
                for stdout, exit_code, stderr in cases:
                    with self.subTest(mode=mode, stdout=stdout, exit=exit_code):
                        with open(os.path.join(parent, "stub.json"), "w") as fh:
                            json.dump({"stdout": stdout, "exit": exit_code, "stderr": stderr, "label": "L" + str(exit_code)}, fh)
                        ctxp, sha = self.fake_context(stub, parent)
                        extra = ["-AuthorizeRealModelCall", "-AcceptBoundary", "x"] if mode == "RunReal" else []
                        code, out, err = self.ps("-Mode", mode, "-Context", ctxp, "-ContextSha256", sha,
                                                 "-Python", sys.executable, "-TestConfig", cfg, *extra)
                        self.assertEqual(code, exit_code, (out, err))
                        if stdout != "none":
                            self.assertIn('"outcome": "L' + str(exit_code) + '"', out.replace('":"', '": "'))
                        if stderr:
                            self.assertIn(stderr, out + err)
            with open(os.path.join(parent, "stub.json"), "w") as fh:
                json.dump({"stdout": "multi", "exit": 4, "label": "P4"}, fh)
            code, out, err = self.ps("-Commit", stub.head, "-Parent", parent, "-Python", sys.executable,
                                     "-TestConfig", cfg)
            self.assertEqual(code, 4, (out, err))
            self.assertIn('"outcome": "P4"', out)
        finally:
            stub.close()

    def probe_clone(self):
        probe = Clone()
        shutil.copyfile(os.path.join(probe.repo, FIX, "probe_entry.py"), os.path.join(probe.repo, *ENTRY_REL.split("/")))
        probe.commit_all("test: probe entry")
        return probe

    def write_probe_cfg(self, repo_dir):
        sentinel_file = os.path.join(self.parent, f"probe-ran-{time.time_ns()}.txt")
        with open(os.path.join(os.path.dirname(repo_dir), "probe.json"), "w") as fh:
            json.dump({"sentinel_file": sentinel_file}, fh)
        return sentinel_file

    def test_source_is_bound_before_any_repo_code_runs(self):
        probe = self.probe_clone()
        try:
            # (a) the 24 reproduction: {"repo": <non-git dir with a probe>} anywhere, no sidecar
            fake = os.path.join(self.parent, "fake repo")
            os.makedirs(os.path.join(fake, "scripts", "gemini_task01_run"))
            shutil.copyfile(os.path.join(probe.repo, *ENTRY_REL.split("/")),
                            os.path.join(fake, "scripts", "gemini_task01_run", "entry.py"))
            ran = self.write_probe_cfg(fake)
            loose = os.path.join(self.parent, "loose.json")
            data = json.dumps({"repo": fake}).encode()
            with open(loose, "wb") as fh:
                fh.write(data)
            cases = {"loose-json": (loose, sha256(data))}
            # (b) proper layout, repo-<stamp> is the non-git probe dir
            cases["non-git-repo"] = self.fake_context(type("C", (), {"repo": fake, "head": "a" * 40})())
            # (c) proper layout, repo-<stamp> is a git clone whose entry is the probe (trees differ)
            cases["probe-clone"] = self.fake_context(probe)
            self.write_probe_cfg(probe.repo)
            # (d) proper layout, the real clone, but the digest is not the reviewed one
            ctxp, _ = self.fake_context(self.clone)
            cases["wrong-digest"] = (ctxp, "2" * 64)
            # (e) array JSON with a matching digest
            cases["array-json"] = self.fake_context(probe, body=b"[1]\n")
            for name, (ctxp, sha) in cases.items():
                for mode in ("Candidate", "RunReal"):
                    with self.subTest(case=name, mode=mode):
                        extra = ["-AuthorizeRealModelCall", "-AcceptBoundary", "x"] if mode == "RunReal" else []
                        code, out, err = self.ps("-Mode", mode, "-Context", ctxp, "-ContextSha256", sha,
                                                 "-Python", sys.executable, *extra)
                        self.assertEqual(code, 2, (out, err))
                        self.assertIn("HARK24-PS REFUSED", out)
                        for f in os.listdir(self.parent):
                            self.assertFalse(f.startswith("probe-ran-"), f)
        finally:
            probe.close()

    def test_ps_weakening_exit_and_source_binding(self):
        report = {}
        src = read(os.path.join(self.clone.repo, *PS1_REL.split("/")))
        old_style = src.replace("        & $Python -I -B $entry @DriverArgs | Out-Host\n        $script:DriverExit = [int]$LASTEXITCODE\n",
                                "        & $Python -I -B $entry @DriverArgs\n        return $LASTEXITCODE\n")
        old_style = old_style.replace("    Invoke-Driver $RepoDir @('run-real',", "    $script:DriverExit = Invoke-Driver $RepoDir @('run-real',")
        no_bind = src.replace("    Assert-Repo $RepoDir $want\n", "")
        self.assertNotEqual(old_style, src)
        self.assertNotEqual(no_bind, src)
        stub = self.stub_clone()
        probe = self.probe_clone()
        try:
            trees = {"scripts/gemini_task01_run": stub.tree("scripts/gemini_task01_run")}
            cfg = self.make_testcfg(stub, trees)
            parent = os.path.join(self.parent, "w")
            os.makedirs(parent)
            with open(os.path.join(parent, "stub.json"), "w") as fh:
                json.dump({"stdout": "single", "exit": 4}, fh)
            results = {}
            for label, text in (("control", src), ("weakened", old_style)):
                ps1 = os.path.join(self.parent, f"{label}-exit.ps1")
                with open(ps1, "w", encoding="utf-8") as fh:
                    fh.write(text)
                ctxp, sha = self.fake_context(stub, parent)
                code, out, err = self.ps("-Mode", "RunReal", "-Context", ctxp, "-ContextSha256", sha, "-Python",
                                         sys.executable, "-TestConfig", cfg, "-AuthorizeRealModelCall",
                                         "-AcceptBoundary", "x", ps1=ps1)
                results[label] = code
            report["exit_separation"] = results
            self.assertEqual(results["control"], 4)
            self.assertNotEqual(results["weakened"], 4)
            results = {}
            for label, text in (("control", src), ("weakened", no_bind)):
                ps1 = os.path.join(self.parent, f"{label}-bind.ps1")
                with open(ps1, "w", encoding="utf-8") as fh:
                    fh.write(text)
                pparent = os.path.join(self.parent, f"pp-{label}")
                os.makedirs(pparent)
                ctxp, sha = self.fake_context(probe, pparent)
                ran = self.write_probe_cfg(probe.repo)
                code, out, err = self.ps("-Mode", "Candidate", "-Context", ctxp, "-ContextSha256", sha,
                                         "-Python", sys.executable, ps1=ps1)
                results[label] = {"exit": code, "probe_ran": os.path.exists(ran)}
            report["source_binding"] = results
            self.assertFalse(results["control"]["probe_ran"])
            self.assertTrue(results["weakened"]["probe_ran"])
        finally:
            stub.close()
            probe.close()
        print("PSWEAKENING25 " + json.dumps(report, sort_keys=True))


@unittest.skipUnless(HAVE, "baseline not available")
class TestStatic(unittest.TestCase):
    def test_ps1_boundaries(self):
        src = read(os.path.join(ROOT, *PS1_REL.split("/")), "rb")
        self.assertTrue(all(b < 128 for b in src), "ps1 must be ASCII for PS 5.1")
        text = src.decode("ascii")
        self.assertIn("[ValidateSet('Prepare', 'RunReal', 'Candidate')] [string]$Mode = 'Prepare'", text)
        self.assertIn("[string]$Parent = ''", text)
        self.assertNotIn("D:\\", text)
        self.assertNotRegex(text, r"\$env:(GEMINI|GOOGLE|HTTPS?_PROXY)")
        self.assertNotRegex(text, r"git\s+push|'push'")
        self.assertNotIn("@@TREE", text)

    def test_ps1_pins_the_trees_of_this_commit(self):
        text = read(os.path.join(ROOT, *PS1_REL.split("/")))
        head = _git(ROOT, "rev-parse", "HEAD")
        for rel in ("src", "scripts/gemini_task01"):
            self.assertIn(_git(ROOT, "rev-parse", f"{head}:{rel}"), text)
        blob = _git(ROOT, "hash-object", os.path.join(ROOT, *ENTRY_REL.split("/")))
        tree = subprocess.run(["git", "-C", ROOT, "mktree", "--missing"], input=f"100644 blob {blob}\tentry.py\n",
                              capture_output=True, text=True, check=True).stdout.strip()
        self.assertIn(tree, text)

    def test_entry_has_no_test_hooks_and_no_translation_layer(self):
        src = read(os.path.join(ROOT, *ENTRY_REL.split("/")))
        for word in ("WEAKEN", "HARK24_TEST", "HARK25_TEST", "launcher=", "anthropic", "openai", "/v1/messages"):
            self.assertNotIn(word, src)
        self.assertIn("gem.GeminiCliAgentSessionAdapter(config)", src)


if __name__ == "__main__":
    unittest.main()
