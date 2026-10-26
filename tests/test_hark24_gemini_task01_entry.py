"""Hark-派单-24: offline tests of the task01 user entry (no Node, no CLI, no model, no network).

Every run here happens in a TEMPORARY CLONE of this repository whose HEAD is a temporary
commit carrying the current entry files (so the entry sees a clean checkout descending from
the accepted baseline, exactly as the .ps1 prepares it). "node" is
tests/fixtures/hark24/fake_node.py and the "CLI" is B's CONSTRUCTED
tests/fixtures/hark21/fake_cli_t01.py: the adapter, its registry and evidence, and B's checker
are the real, unchanged code, but no result here is a real model's work.

Guard weakening is done by editing a COPY of entry.py inside the temporary clone (and committing
it there); the production entry has no weakening hook.
"""
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
SENTINEL = "SENTINEL-hark24-not-a-key-7f3a9c"
ENDPOINT = "https://constructed-endpoint.invalid/base"
MODEL = "constructed-explicit-model"
NEW_FILES = ("scripts/gemini_task01_run/entry.py", "scripts/win/hark24-gemini-task01.ps1",
             "tests/fixtures/hark24/fake_node.py", "tests/fixtures/hark24/guarded_prepare.py")


def _git(repo, *args):
    return subprocess.run(["git", "-C", repo, *args], check=True, capture_output=True,
                          text=True).stdout.strip()


def _have_baseline():
    try:
        _git(ROOT, "merge-base", "--is-ancestor", "95d941ec1025470dad4fc1d00e9f4775361e3b91", "HEAD")
        _git(ROOT, "cat-file", "-e", "f6bf47c59688c43d927dede01b59f772520815d8^{commit}")
        return True
    except (subprocess.CalledProcessError, OSError):
        return False


HAVE = _have_baseline()


class Clone:
    """A temporary clone at HEAD plus the current entry files, committed (clean)."""

    def __init__(self):
        self.base = os.path.realpath(tempfile.mkdtemp(prefix="hark24-"))
        self.repo = os.path.join(self.base, "repo")
        subprocess.run(["git", "clone", "-q", "--no-hardlinks", ROOT, self.repo], check=True)
        _git(self.repo, "checkout", "-q", "--detach", _git(ROOT, "rev-parse", "HEAD"))
        for rel in NEW_FILES:
            dest = os.path.join(self.repo, *rel.split("/"))
            os.makedirs(os.path.dirname(dest), exist_ok=True)
            shutil.copyfile(os.path.join(ROOT, *rel.split("/")), dest)
        self.commit_all("test: current entry files")
        self.head = _git(self.repo, "rev-parse", "HEAD")

    def commit_all(self, msg):
        _git(self.repo, "add", "-A")
        if _git(self.repo, "status", "--porcelain"):
            _git(self.repo, "-c", "user.name=t", "-c", "user.email=t@invalid", "commit", "-q", "-m", msg)
        return _git(self.repo, "rev-parse", "HEAD")

    def weaken(self, old, new):
        path = os.path.join(self.repo, *ENTRY_REL.split("/"))
        src = open(path, encoding="utf-8").read()
        assert src.count(old) == 1, old
        open(path, "w", encoding="utf-8").write(src.replace(old, new))
        self.head = self.commit_all("test: weakened copy")

    def close(self):
        shutil.rmtree(self.base, ignore_errors=True)


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
        self.parent = os.path.join(self.clone.base, f"p-{self._testMethodName}-{time.time_ns()}")
        os.makedirs(self.parent)
        self.env = {k: v for k, v in os.environ.items() if k in ("PATH", "SYSTEMROOT", "LANG", "TMPDIR")}
        self.env.update(PYTHONDONTWRITEBYTECODE="1", GEMINI_API_KEY=SENTINEL, HARK24_UNRELATED="must-not-pass")
        self.node = self.make_node()

    # ---------------------------------------------------------------- fixtures
    def make_node(self, version="v22.23.4", sleep=0):
        d = os.path.join(self.parent, f"node-{time.time_ns()}")
        os.makedirs(d)
        path = os.path.join(d, "node")
        src = open(os.path.join(self.clone.repo, "tests", "fixtures", "hark24", "fake_node.py"),
                   encoding="utf-8").read()
        with open(path, "w", encoding="utf-8") as fh:
            fh.write(f"#!{sys.executable}\n" + src)
        os.chmod(path, os.stat(path).st_mode | stat.S_IXUSR)
        self.record = os.path.join(self.parent, "host-record.jsonl")
        self.node_cfg = os.path.join(d, "fake-node.json")
        self.write_node_cfg(version=version, sleep=sleep, scenario=os.path.join(self.parent, "scenario.json"))
        return path

    def write_node_cfg(self, **cfg):
        cfg.setdefault("record", self.record)
        with open(self.node_cfg, "w", encoding="utf-8") as fh:
            json.dump(cfg, fh)

    def entry(self, *args, env=None, repo=None):
        done = subprocess.run([sys.executable, "-m", "scripts.gemini_task01_run.entry", *args],
                              cwd=repo or self.clone.repo, env=env or self.env, capture_output=True, text=True,
                              timeout=300)
        return done.returncode, done.stdout, done.stderr

    def prepare(self, name="ctx", **over):
        opts = {"--node": self.node, "--cli-js": os.path.join(self.clone.repo, "tests", "fixtures", "hark21",
                                                              "fake_cli_t01.py"),
                "--endpoint": ENDPOINT, "--model": MODEL, "--key-env-name": "GEMINI_API_KEY"}
        opts.update(over)
        args = ["prepare", "--work-root", os.path.join(self.parent, name), "--entry-commit", self.clone.head]
        for k, v in opts.items():
            if v is not None:
                args += [k, v]
        code, out, err = self.entry(*args)
        self.last = (code, out, err)
        return code, json.loads(out)

    def scenario(self, work_root, delivery=build.ALIGNED_DELIVERY, **extra):
        export = os.path.join(work_root, "target")
        sc = {"tools": build.good_tools(export), "delivery": delivery}
        sc.update(extra)
        with open(os.path.join(self.parent, "scenario.json"), "w", encoding="utf-8") as fh:
            json.dump(sc, fh, ensure_ascii=False)

    def run_real(self, work_root, *extra, accept=True, authorize=True, timeout="60", env=None, repo=None):
        args = ["run-real", "--context", os.path.join(work_root, "context.json"), "--collect-timeout", timeout]
        if authorize:
            args.append("--authorize-real-model-call")
        if accept:
            args += ["--accept-boundary", self.phrase()]
        code, out, err = self.entry(*args, *extra, env=env, repo=repo)
        self.last = (code, out, err)
        return code, json.loads(out)

    def phrase(self):
        sys.path.insert(0, self.clone.repo)
        src = open(os.path.join(self.clone.repo, *ENTRY_REL.split("/")), encoding="utf-8").read()
        start = src.index("ACCEPT_PHRASE = (")
        return eval(src[start + len("ACCEPT_PHRASE = "):src.index(")", start) + 1])  # noqa: S307

    def ready(self, name="ctx", **sc):
        code, res = self.prepare(name)
        self.assertEqual((code, res["outcome"]), (0, "PREPARED"), self.last)
        work_root = os.path.join(self.parent, name)
        self.scenario(work_root, **sc)
        return work_root

    def host_calls(self):
        if not os.path.exists(self.record):
            return []
        return [json.loads(l) for l in open(self.record, encoding="utf-8") if l.strip()]

    def dispatches(self):
        return [c for c in self.host_calls() if "--version" not in c["argv_tail"]]

    def registry_runs(self, work_root):
        path = os.path.join(work_root, "evidence", "registry.jsonl")
        if not os.path.exists(path):
            return []
        recs = [json.loads(l) for l in open(path, encoding="utf-8") if l.strip()]
        return sorted({r["run_id"] for r in recs if r["event"] == "reserved"})

    def all_bytes_under(self, *roots):
        for root in roots:
            for d, _, fs in os.walk(root):
                for f in fs:
                    with open(os.path.join(d, f), "rb") as fh:
                        yield os.path.join(d, f), fh.read()


@unittest.skipUnless(HAVE, "baseline not available")
class TestPrepare(_Base):
    def test_prepared_context_is_non_sensitive_and_dispatches_nothing(self):
        code, res = self.prepare()
        self.assertEqual((code, res["outcome"]), (0, "PREPARED"))
        self.assertFalse(res["dispatched"])
        self.assertFalse(res["version_or_auth_verified"])
        wr = os.path.join(self.parent, "ctx")
        ctx = json.load(open(os.path.join(wr, "context.json"), encoding="utf-8"))
        self.assertEqual(ctx["status"], "PREPARED_NOT_ADMITTED")
        self.assertEqual(ctx["key_env_name"], "GEMINI_API_KEY")
        self.assertFalse(ctx["key_value_checked"])
        self.assertFalse(ctx["node"]["version_verified"])
        self.assertFalse(ctx["cli"]["version_verified"])
        self.assertFalse(ctx["cli"]["sha256_equals_known_0650_bundle"])      # the stand-in is not the bundle
        self.assertFalse(ctx["endpoint"]["protocol_verified"])
        self.assertEqual(ctx["export_files"], 26)
        self.assertFalse(os.path.exists(ctx["evidence_root"]))
        self.assertEqual(self.host_calls(), [])                               # no node / CLI started
        for path, data in self.all_bytes_under(wr):
            self.assertNotIn(SENTINEL.encode(), data, path)
        self.assertNotIn(SENTINEL, self.last[1] + self.last[2])

    def test_prepare_never_looks_up_secret_variables_and_starts_only_git(self):
        obs = os.path.join(self.parent, "guard.json")
        args = [sys.executable, os.path.join(self.clone.repo, "tests", "fixtures", "hark24", "guarded_prepare.py"),
                obs, "prepare", "--work-root", os.path.join(self.parent, "g"), "--entry-commit", self.clone.head,
                "--node", self.node, "--cli-js", self.node, "--endpoint", ENDPOINT, "--model", MODEL,
                "--key-env-name", "GEMINI_API_KEY", "--proxy-env-name", "HTTPS_PROXY"]
        env = dict(self.env, HTTPS_PROXY="http://u:" + SENTINEL + "@proxy.invalid:1")
        done = subprocess.run(args, cwd=self.clone.repo, env=env, capture_output=True, text=True, timeout=300)
        self.assertEqual(done.returncode, 0, done.stderr)
        seen = json.load(open(obs, encoding="utf-8"))
        self.assertEqual(seen["env_lookups"], [])
        self.assertTrue(seen["processes"])
        self.assertTrue(all(os.path.basename(p[0]) == "git" for p in seen["processes"]), seen["processes"])
        self.assertNotIn(SENTINEL, done.stdout + done.stderr)

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
                code, res = self.prepare(name, **over)
                self.assertEqual((code, res["outcome"]), (4, "NEEDS_SETUP"), self.last)
                ctx = json.load(open(os.path.join(self.parent, name, "context.json"), encoding="utf-8"))
                self.assertEqual(ctx["status"], "NEEDS_SETUP")

    def test_endpoint_with_credentials_or_query_is_refused_without_echo(self):
        for name, url in {"userinfo": f"https://user:{SENTINEL}@h.invalid/",
                          "query": f"https://h.invalid/?key={SENTINEL}",
                          "fragment": f"https://h.invalid/#{SENTINEL}", "scheme": f"ftp://h.invalid/{SENTINEL}"}.items():
            with self.subTest(case=name):
                code, res = self.prepare(name, **{"--endpoint": url})
                self.assertEqual(res["outcome"], "NEEDS_SETUP")
                for path, data in self.all_bytes_under(os.path.join(self.parent, name)):
                    self.assertNotIn(SENTINEL.encode(), data, path)
                self.assertNotIn(SENTINEL, self.last[1] + self.last[2])

    def test_existing_work_root_and_wrong_entry_commit_refused(self):
        self.prepare("dup")
        before = sorted(os.listdir(os.path.join(self.parent, "dup")))
        code, res = self.prepare("dup")
        self.assertEqual((code, res["outcome"]), (2, "REFUSED"))
        self.assertEqual(sorted(os.listdir(os.path.join(self.parent, "dup"))), before)
        code, out, _ = self.entry("prepare", "--work-root", os.path.join(self.parent, "x"),
                                  "--entry-commit", "0" * 40)
        self.assertEqual(json.loads(out)["outcome"], "REFUSED")
        self.assertFalse(os.path.exists(os.path.join(self.parent, "x")))

    def test_export_keeps_task_and_source_bytes(self):
        self.prepare()
        wr = os.path.join(self.parent, "ctx")
        ctx = json.load(open(os.path.join(wr, "context.json"), encoding="utf-8"))
        self.assertEqual(ctx["task_sha256"], "5ed4f438c57918d7924ea00bf02134c529b20bf9556e6b01c6dffa7830e80518")
        sys.path.insert(0, os.path.join(ROOT, "scripts", "gemini_task01"))
        import pins
        for rel, (_, size, digest) in pins.SOURCE_FILES.items():
            data = open(os.path.join(wr, "target", *rel.split("/")), "rb").read()
            self.assertEqual((len(data), __import__("hashlib").sha256(data).hexdigest()), (size, digest))


@unittest.skipUnless(HAVE, "baseline not available")
class TestRunReal(_Base):
    def test_one_dispatch_through_the_real_adapter_and_checker(self):
        wr = self.ready()
        code, res = self.run_real(wr)
        self.assertEqual(res["outcome"], "COLLECTED", self.last)
        self.assertEqual(res["mechanical"], "all_pass")
        self.assertEqual(code, 0)
        self.assertEqual(res["semantic_review"], "required")
        self.assertEqual(res["task_outcome"], "not decided by this entry")
        self.assertEqual(res["host_versions"], {"node": "v22.23.4", "cli": "0.65.0"})
        self.assertEqual(len(self.dispatches()), 1)
        self.assertEqual(len(self.registry_runs(wr)), 1)
        report = json.load(open(os.path.join(wr, "check-report.json"), encoding="utf-8"))
        self.assertEqual(report["semantic_review"]["status"], "required")
        self.assertEqual(report["task_outcome"], "not decided by this tool")
        call = self.dispatches()[0]
        self.assertTrue(call["key_present"])
        self.assertEqual(call["key_len"], len(SENTINEL))
        self.assertEqual(call["endpoint"], ENDPOINT)
        self.assertNotIn("HARK24_UNRELATED", call["env_names"])
        self.assertTrue(set(call["env_names"]) <= {"PATH", "SYSTEMROOT", "LANG", "GEMINI_API_KEY",
                                                   "GOOGLE_GEMINI_BASE_URL", "GEMINI_CLI_HOME", "HOME",
                                                   "USERPROFILE", "GEMINI_CLI_NO_RELAUNCH", "NO_COLOR",
                                                   "FAKE_T01_SCENARIO", "LC_CTYPE"}, call["env_names"])
        self.assertIn("-m", call["argv_tail"])
        self.assertEqual(call["argv_tail"][call["argv_tail"].index("-m") + 1], MODEL)
        self.assertNotIn(SENTINEL, " ".join(call["argv_tail"]))
        for path, data in self.all_bytes_under(wr):
            self.assertNotIn(SENTINEL.encode(), data, path)
        self.assertNotIn(SENTINEL, self.last[1] + self.last[2])

    def test_second_run_same_context_and_new_context_same_parent_frozen(self):
        wr = self.ready()
        self.run_real(wr)
        code, res = self.run_real(wr)
        self.assertEqual((code, res["outcome"]), (5, "FROZEN"))
        wr2 = self.ready("ctx2")
        code, res = self.run_real(wr2)
        self.assertEqual((code, res["outcome"]), (5, "FROZEN"))
        self.assertEqual(len(self.dispatches()), 1)
        self.assertEqual(self.registry_runs(wr2), [])

    def test_concurrent_entries_dispatch_once(self):
        roots = [self.ready(f"c{i}") for i in range(3)]
        outs = []

        def go(r):
            outs.append(self.run_real(r))
        threads = [threading.Thread(target=go, args=(r,)) for r in roots]
        [t.start() for t in threads]
        [t.join() for t in threads]
        self.assertEqual(sorted(o[1]["outcome"] for o in outs), ["COLLECTED", "FROZEN", "FROZEN"])
        self.assertEqual(len(self.dispatches()), 1)
        self.assertEqual(sum(len(self.registry_runs(r)) for r in roots), 1)

    def test_refusals_before_dispatch_leave_no_marker(self):
        wr = self.ready()
        for name, kwargs in {"no-authorize": {"authorize": False}, "no-phrase": {"accept": False}}.items():
            with self.subTest(case=name):
                code, res = self.run_real(wr, **kwargs)
                self.assertEqual((code, res["outcome"]), (2, "REFUSED"))
        env = {k: v for k, v in self.env.items() if k != "GEMINI_API_KEY"}
        code, res = self.run_real(wr, env=env)
        self.assertEqual((code, res["outcome"]), (4, "NEEDS_SETUP"))
        self.write_node_cfg(version="v24.0.0", scenario=os.path.join(self.parent, "scenario.json"))
        code, res = self.run_real(wr)
        self.assertEqual((code, res["outcome"]), (2, "REFUSED"))
        self.write_node_cfg(version="v22.23.4", scenario=os.path.join(self.parent, "scenario.json"))
        self.scenario(wr, version="0.66.0")
        code, res = self.run_real(wr)
        self.assertEqual((code, res["outcome"]), (2, "REFUSED"))
        self.assertEqual(self.dispatches(), [])
        self.assertFalse(os.path.exists(os.path.join(self.parent, "task01-real-dispatch.marker")))
        self.assertFalse(os.path.exists(os.path.join(wr, "run-real.started")))
        self.assertFalse(os.path.exists(os.path.join(wr, "evidence")))

    def test_tampered_context_export_or_needs_setup_context_refused(self):
        wr = self.ready()
        ctxp = os.path.join(wr, "context.json")
        original = open(ctxp, "rb").read()
        open(ctxp, "wb").write(original.replace(MODEL.encode(), b"constructed-other-model"))
        code, res = self.run_real(wr)
        self.assertEqual(res["outcome"], "REFUSED")
        open(ctxp, "wb").write(original)
        victim = os.path.join(wr, "target", "src", "memory.py")
        os.chmod(victim, 0o644)
        with open(victim, "ab") as fh:
            fh.write(b"\n# changed\n")
        code, res = self.run_real(wr)
        self.assertEqual(res["outcome"], "REFUSED")
        self.assertIn("bytes differ", res["summary"])
        code, res2 = self.prepare("ns", **{"--model": None})
        code, res = self.run_real(os.path.join(self.parent, "ns"))
        self.assertEqual(res["outcome"], "NEEDS_SETUP")
        self.assertEqual(self.dispatches(), [])

    def test_timeout_is_unknown_frozen_and_not_killed(self):
        wr = self.ready()
        self.write_node_cfg(version="v22.23.4", sleep=6, scenario=os.path.join(self.parent, "scenario.json"))
        code, res = self.run_real(wr, timeout="1")
        self.assertEqual((code, res["outcome"]), (6, "TIMEOUT_UNKNOWN"))
        self.assertIn("billing is not proven stopped", res["summary"])
        pid = self.dispatches()[0]["pid"]
        os.kill(pid, 0)                                  # still alive: the entry did not kill it
        code, res = self.run_real(wr)
        self.assertEqual(res["outcome"], "FROZEN")
        self.assertEqual(len(self.registry_runs(wr)), 1)
        time.sleep(7)

    def test_launch_failure_recorded_and_frozen(self):
        wr = self.ready()
        self.write_node_cfg(version="v22.23.4", scenario=os.path.join(self.parent, "scenario.json"),
                            unexecutable_after_cli_version_calls=2)
        code, res = self.run_real(wr)
        self.assertEqual((code, res["outcome"]), (7, "DISPATCH_REFUSED"), res)
        self.assertIs(res["dispatched"], False)
        self.assertEqual(self.dispatches(), [])
        events = [json.loads(l)["event"] for l in open(os.path.join(wr, "evidence", "registry.jsonl"))]
        self.assertEqual(events, ["reserved", "dispatch_failed"])
        os.chmod(self.node, 0o755)
        code, res = self.run_real(wr)
        self.assertEqual(res["outcome"], "FROZEN")

    def test_host_failure_kept_as_is(self):
        wr = self.ready(exit_code=1, result_status="error")
        code, res = self.run_real(wr)
        self.assertEqual(res["outcome"], "COLLECTED")
        self.assertNotEqual(res["mechanical"], "all_pass")
        self.assertNotEqual(code, 0)
        code, res = self.run_real(wr)
        self.assertEqual(res["outcome"], "FROZEN")

    def test_reversed_explanation_never_becomes_a_task_pass(self):
        wr = self.ready(delivery=build.REVERSED_DELIVERY)
        code, res = self.run_real(wr)
        self.assertEqual(res["outcome"], "COLLECTED")
        self.assertEqual(res["mechanical"], "all_pass")          # mechanics only
        self.assertEqual(res["semantic_review"], "required")
        self.assertEqual(res["task_outcome"], "not decided by this entry")
        text = open(os.path.join(wr, "run-real-result.json"), encoding="utf-8").read()
        self.assertNotIn('"PASS"', text)

    def test_candidate_commit_verified_whitelisted_and_not_pushed(self):
        wr = self.ready()
        self.run_real(wr)
        try:
            code, out, err = self.entry("candidate", "--context", os.path.join(wr, "context.json"))
            res = json.loads(out)
            self.assertTrue(res["verified"], res)
            self.assertFalse(res["pushed"])
            self.assertEqual(res["parents"], [self.clone.head])
            names = sorted(os.path.basename(f) for f in res["files"])
            self.assertEqual(names, sorted(["context.json", "prepare-result.json", "run-real-result.json",
                                            "check-report.json", "evidence-digests.json"]))
            for f in res["files"]:
                blob = subprocess.run(["git", "-C", self.clone.repo, "show", f"{res['commit']}:{f}"],
                                      capture_output=True, check=True).stdout
                self.assertNotIn(SENTINEL.encode(), blob)
            self.assertNotIn("stdout.jsonl\"", json.dumps(res["files"]))
        finally:
            _git(self.clone.repo, "checkout", "-q", "--detach", self.clone.head)


# ------------------------------------------------------------------ guard weakening
# (label, [(old, new) edits of a COPY of entry.py], setup -> (work_root, run kwargs, expect_all_pass))

def _w_authorize(t):
    return t.ready(), {"authorize": False}, True


def _w_frozen(t):
    wr = t.ready()
    t.run_real(wr)
    return t.ready("second"), {}, True


def _w_node(t):
    wr = t.ready()
    t.write_node_cfg(version="v24.0.0", scenario=os.path.join(t.parent, "scenario.json"))
    return wr, {}, True


def _w_key(t):
    return t.ready(), {"env": {k: v for k, v in t.env.items() if k != "GEMINI_API_KEY"}}, True


def _w_endpoint(t):
    wr = t.ready()
    p = os.path.join(wr, "context.json")
    data = open(p, "rb").read().replace(ENDPOINT.encode(), (ENDPOINT + "?key=x").encode())
    open(p, "wb").write(data)
    open(os.path.join(wr, "context.sha256"), "w").write(__import__("hashlib").sha256(data).hexdigest() + "\n")
    return wr, {}, True


def _w_sidecar(t):
    wr = t.ready()
    p = os.path.join(wr, "context.json")
    with open(p, "rb") as fh:
        data = fh.read()
    with open(p, "wb") as fh:
        fh.write(data.replace(MODEL.encode(), b"constructed-other-model"))
    return wr, {}, True


def _w_export(t):
    wr = t.ready()
    victim = os.path.join(wr, "target", "src", "memory.py")
    os.chmod(victim, 0o644)
    with open(victim, "ab") as fh:
        fh.write(b"\n# changed\n")
    return wr, {}, False          # the checker's own anchor still fails it after dispatch


WEAKENING = [
    ("authorize", [("    if not args.authorize_real_model_call or args.accept_boundary != ACCEPT_PHRASE:\n",
                    "    if False:\n")], _w_authorize),
    ("single_dispatch", [("    if any(os.path.lexists(p) for p in (marker, local_marker, ctx[\"evidence_root\"], "
                          "result_path)):\n", "    if False:\n"),
                         ("        write_new(marker, canon({", "        (lambda *a: None)(marker, canon({")],
     _w_frozen),
    ("node_version", [("    if node_v != PINNED_NODE_VERSION or not gem.version_is_pinned(cli_v):\n",
                       "    if not gem.version_is_pinned(cli_v):\n")], _w_node),
    ("key_presence", [("    if setup:\n", "    if False:\n")], _w_key),
    ("endpoint", [("    problems += endpoint_problems(ctx[\"endpoint\"][\"value\"])\n", "")], _w_endpoint),
    ("context_sidecar", [("    if sha256_bytes(data) != want:\n", "    if False:\n")], _w_sidecar),
    ("export_verify", [("        problems += export_problems(ctx[\"workspace\"], ctx[\"export_manifest\"])\n",
                        "")], _w_export),
]


@unittest.skipUnless(HAVE, "baseline not available")
class TestGuardWeakening(_Base):
    def test_each_guard_control_then_weakened(self):
        report = {}
        for label, edits, setup in WEAKENING:
            with self.subTest(guard=label):
                # control: the real entry
                self.setUp()
                wr, kw, expect_all_pass = setup(self)
                before = len(self.dispatches())
                c_code, c_res = self.run_real(wr, **kw)
                c_dispatched = len(self.dispatches()) - before
                # weakened: a committed COPY of entry.py with only this guard removed
                weak = Clone()
                try:
                    for old, new in edits:
                        weak.weaken(old, new)
                    self.clone, saved = weak, self.clone
                    self.setUp()
                    wr, kw, _ = setup(self)
                    before = len(self.dispatches())
                    w_code, w_res = self.run_real(wr, **kw)
                    w_dispatched = len(self.dispatches()) - before
                    self.clone = saved
                finally:
                    weak.close()
                report[label] = {"control": c_res["outcome"], "control_exit": c_code,
                                 "control_dispatches": c_dispatched, "weakened": w_res["outcome"],
                                 "weakened_mechanical": w_res.get("mechanical"), "weakened_exit": w_code,
                                 "weakened_dispatches": w_dispatched}
                self.assertNotEqual(c_code, 0)
                self.assertEqual(c_dispatched, 0)
                self.assertEqual(w_res["outcome"], "COLLECTED", w_res)
                self.assertEqual(w_dispatched, 1)
                if expect_all_pass:
                    self.assertEqual((w_res["mechanical"], w_code), ("all_pass", 0))
        print("WEAKENING24 " + json.dumps(report, sort_keys=True))


@unittest.skipUnless(HAVE, "baseline not available")
class TestStatic(unittest.TestCase):
    def test_ps1_boundaries(self):
        src = open(os.path.join(ROOT, "scripts", "win", "hark24-gemini-task01.ps1"), "rb").read()
        self.assertTrue(all(b < 128 for b in src), "ps1 must be ASCII for PS 5.1")
        text = src.decode("ascii")
        self.assertIn("[ValidateSet('Prepare', 'RunReal', 'Candidate')] [string]$Mode = 'Prepare'", text)
        self.assertNotRegex(text, r"\$env:(GEMINI|GOOGLE|HTTPS?_PROXY)")
        self.assertNotIn("Invoke-WebRequest", text)
        self.assertNotRegex(text, r"'push'|git\s+push|@\('push")
        self.assertIn("if (-not $AuthorizeRealModelCall)", text)
        self.assertNotRegex(text, r"&\s*\$Node|&\s*node\b|gemini\.js")

    def test_entry_has_no_test_hooks_and_no_translation_layer(self):
        src = open(os.path.join(ROOT, *ENTRY_REL.split("/")), encoding="utf-8").read()
        for word in ("WEAKEN", "HARK24_TEST", "launcher=", "anthropic", "openai", "/v1/messages"):
            self.assertNotIn(word, src)
        self.assertIn("gem.GeminiCliAgentSessionAdapter(config)", src)


if __name__ == "__main__":
    unittest.main()
