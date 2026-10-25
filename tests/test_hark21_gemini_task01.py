"""Hark-派单-21/22: first Gemini source task package (task01), offline only.

Every run here is CONSTRUCTED: the real adapter code dispatches to the stand-in
tests/fixtures/hark21/fake_cli_t01.py, whose tool outputs and delivery texts are
written by tests/fixtures/hark21/build.py. No real CLI, Node, model or synthetic
endpoint is started. A constructed "good" run is a control for the checker, not a
real-path positive control of the task.
"""
import copy
import hashlib
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tests" / "fixtures" / "hark21"))
sys.path.insert(0, str(ROOT / "scripts" / "gemini_task01"))

import build  # noqa: E402
import check_run  # noqa: E402
import export_source  # noqa: E402

PKG_DIR = ROOT / "scripts" / "gemini_task01"
HOST_PROBE = ROOT / "reports" / "hark-gemini-bridge-20261021" / "host-probe" / "bridge"
HOST_PROBE_RUN = "gemini-run-e4d091ccdd29"
FORBIDDEN_VERDICT_WORDS = ("approved", "succeeded", "accepted", "acceptance_pass", "task_pass")


def _have_commit() -> bool:
    try:
        subprocess.run(["git", "-C", str(ROOT), "cat-file", "-e",
                        export_source.load_package(str(PKG_DIR / "package.json"))["source_commit"] + "^{commit}"],
                       check=True, capture_output=True)
        return True
    except (OSError, subprocess.CalledProcessError):
        return False


HAVE_COMMIT = _have_commit()


def tree_digest(root):
    h = hashlib.sha256()
    for dirpath, dirs, files in os.walk(root):
        dirs.sort()
        for name in sorted(files):
            p = os.path.join(dirpath, name)
            h.update(os.path.relpath(p, root).encode() + b"\0")
            with open(p, "rb") as fh:
                h.update(fh.read())
    return h.hexdigest()


def statuses(result, prefix):
    return [i["status"] for i in result["mechanical"]["items"] if i["check"].startswith(prefix)]


def no_verdict_words(obj):
    text = json.dumps(obj, ensure_ascii=False).lower()
    keys = json.dumps(list(_keys(obj))).lower()
    return not any(w in keys for w in FORBIDDEN_VERDICT_WORDS) and '"pass": true' not in text


def _keys(obj):
    if isinstance(obj, dict):
        for k, v in obj.items():
            yield k
            yield from _keys(v)
    elif isinstance(obj, list):
        for v in obj:
            yield from _keys(v)


@unittest.skipUnless(HAVE_COMMIT, "fixed source commit not available in this clone")
class _Base(unittest.TestCase):
    def setUp(self):
        check_run.WEAKEN.clear()
        self.base = os.path.realpath(tempfile.mkdtemp(prefix="hark21-"))
        self.export, self.manifest = build.make_export(self.base)

    def tearDown(self):
        check_run.WEAKEN.clear()
        shutil.rmtree(self.base, ignore_errors=True)

    def run_case(self, tools=None, delivery=build.GOOD_DELIVERY, **scenario):
        sc = {"tools": build.good_tools(self.export) if tools is None else tools, "delivery": delivery}
        sc.update(scenario.pop("scenario", {}))
        self.evidence, self.run_id = build.run_adapter(self.base, self.export, sc, **scenario)
        return self.evidence, self.run_id

    def check(self, package=None, manifest=None, run_id=None):
        return check_run.run_checks(package or build.PACKAGE, manifest or self.manifest, self.export,
                                    self.evidence, run_id or self.run_id)

    def run_dir(self):
        return os.path.join(self.evidence, "runs", self.run_id)


class TestExport(_Base):
    def test_export_is_src_only_and_byte_exact(self):
        man = json.loads(Path(self.manifest).read_text("utf-8"))
        self.assertTrue(man["files"])
        self.assertTrue(all(p.startswith("src/") for p in man["files"]))
        self.assertEqual(man["files"]["src/agent_session_gemini.py"]["blob"], check_run.PINNED_BLOB)
        for name in ("tests", "docs", "reports", ".git", "task.txt", "review-baseline.md", "package.json"):
            self.assertFalse(os.path.lexists(os.path.join(self.export, name)), name)
        for path, meta in man["files"].items():
            data = Path(self.export, *path.split("/")).read_bytes()
            self.assertEqual(export_source.git_blob_id(data), meta["blob"])

    def test_export_refuses_non_empty_out_and_manifest_inside(self):
        with self.assertRaises(SystemExit):
            export_source.export(str(ROOT), self.export, os.path.join(self.base, "m2.json"), build.package())
        fresh = os.path.join(self.base, "fresh")
        with self.assertRaises(SystemExit):
            export_source.export(str(ROOT), fresh, os.path.join(fresh, "m.json"), build.package())

    def test_task_text_and_baseline_kept_apart(self):
        task = (PKG_DIR / "task.txt").read_text("utf-8")
        baseline = (PKG_DIR / "review-baseline.md").read_text("utf-8")
        for answer_marker in ("1069", "1134", "outcome-unknown-after-reopen", "RegistryUnavailable",
                              "_reload_locked", "_dispatch_locked"):
            self.assertNotIn(answer_marker, task)
            self.assertIn(answer_marker, baseline)
        self.assertIn("dispatched", task)          # B7 is asked explicitly
        self.assertIn("B7", baseline)


class TestControlAndSemanticBoundary(_Base):
    def test_constructed_good_run_all_mechanical_pass_but_no_verdict(self):
        self.run_case()
        ev_before = tree_digest(self.evidence)
        ex_before = tree_digest(self.export)
        result = self.check()
        self.assertEqual(result["mechanical"]["overall"], "all_pass", result["mechanical"]["items"])
        self.assertEqual(result["semantic_review"]["status"], "required")
        self.assertEqual(result["task_outcome"], "not decided by this tool")
        self.assertTrue(no_verdict_words(result))
        self.assertEqual(tree_digest(self.evidence), ev_before)
        self.assertEqual(tree_digest(self.export), ex_before)

    def test_reversed_explanation_with_correct_refs_is_not_a_verdict(self):
        self.run_case(delivery=build.REVERSED_DELIVERY)
        result = self.check()
        # Locations are the same as the good run: the tool cannot and does not judge meaning.
        self.assertEqual(result["mechanical"]["overall"], "all_pass")
        self.assertEqual(result["semantic_review"]["status"], "required")
        self.assertTrue(all(p["meaning"] == "not judged by this tool" for p in result["semantic_review"]["points"]))
        self.assertTrue(no_verdict_words(result))
        self.assertEqual(result["semantic_review"]["delivery_text"], build.REVERSED_DELIVERY)

    def test_b7_meaning_omitted_is_not_a_verdict(self):
        self.run_case(delivery=build.B7_OMITTED_DELIVERY)
        result = self.check()
        self.assertEqual(result["mechanical"]["overall"], "all_pass")
        self.assertEqual(result["semantic_review"]["status"], "required")
        self.assertTrue(no_verdict_words(result))

    def test_b7_not_cited_fails_location(self):
        self.run_case(delivery=build.GOOD_DELIVERY.split("5. ")[0])
        result = self.check()
        self.assertEqual(statuses(result, "refs.B7"), ["fail"])

    def test_checker_starts_no_process_and_no_network(self):
        src = (PKG_DIR / "check_run.py").read_text("utf-8")
        found = [m for m in ("import subprocess", "import socket", "urllib", "http.client", "import requests",
                             "os.system", "Popen") if m in src]
        self.assertEqual(found, [])


class TestBinding(_Base):
    def test_other_task_text(self):
        self.run_case(instructions="Find where class RunState is defined.")
        r = self.check()
        self.assertIn("fail", statuses(r, "binding.request"))
        self.assertEqual(r["mechanical"]["overall"], "fail")

    def test_other_task_id(self):
        self.run_case(task_id="some-other-task")
        self.assertEqual(statuses(self.check(), "binding.request"), ["fail"])

    def test_unknown_run_id(self):
        self.run_case()
        r = self.check(run_id="gemini-run-000000000000")
        self.assertEqual(statuses(r, "binding.run"), ["fail"])

    def test_no_explicit_model(self):
        self.run_case(model=None)
        self.assertEqual(statuses(self.check(), "binding.request"), ["fail"])

    def test_prompt_received_differs(self):
        self.run_case(scenario={"user_content_override": "something else"})
        self.assertEqual(statuses(self.check(), "binding.stdin"), ["fail"])

    def test_uncollected_run_is_unknown_not_resent(self):
        evidence, run_id = self.run_case()
        # Cut the collected record: the run is reserved+dispatched only.
        reg = os.path.join(evidence, "registry.jsonl")
        lines = Path(reg).read_bytes().split(b"\n")
        Path(reg).write_bytes(b"\n".join(lines[:2]) + b"\n")
        before = Path(reg).read_bytes()
        r = self.check()
        self.assertEqual(statuses(r, "binding.run"), ["unknown"])
        self.assertEqual(r["mechanical"]["overall"], "undetermined")
        self.assertEqual(Path(reg).read_bytes(), before)
        self.assertIn("no re-dispatch", r["on_unknown_or_fail"])

    def test_old_host_probe_run_is_another_task(self):
        if not HOST_PROBE.is_dir():
            self.skipTest("host-probe evidence absent")
        self.evidence, self.run_id = str(HOST_PROBE), HOST_PROBE_RUN
        r = self.check()
        self.assertEqual(statuses(r, "binding.request"), ["fail"])
        self.assertEqual(r["mechanical"]["overall"], "fail")


class TestSourceAndEvidence(_Base):
    def test_export_byte_changed(self):
        self.run_case()
        p = Path(self.export, "src", "agent_session.py")
        p.write_bytes(p.read_bytes() + b"#")
        self.assertEqual(statuses(self.check(), "export.tree"), ["fail"])

    def test_export_extra_file_at_run_time(self):
        Path(self.export, "src", "ANSWER.md").write_text("x")
        self.run_case()
        r = self.check()
        self.assertEqual(statuses(r, "export.tree"), ["fail"])
        self.assertIn("fail", statuses(r, "workspace."))

    def test_manifest_for_other_commit(self):
        self.run_case()
        man = json.loads(Path(self.manifest).read_text("utf-8"))
        man["source_commit"] = "0" * 40
        other = os.path.join(self.base, "other-manifest.json")
        Path(other).write_text(json.dumps(man))
        self.assertEqual(statuses(self.check(manifest=other), "export.manifest_pins"), ["fail"])

    def test_evidence_missing_is_unknown(self):
        self.run_case()
        os.remove(os.path.join(self.run_dir(), "host-chat-record.jsonl"))
        r = self.check()
        self.assertEqual(statuses(r, "evidence.host-chat-record.jsonl"), ["unknown"])
        self.assertEqual(r["mechanical"]["overall"], "undetermined")

    def test_chat_record_absent_at_collect_is_unknown(self):
        self.run_case(scenario={"chat_record": False})
        r = self.check()
        self.assertEqual(statuses(r, "evidence.host-chat-record.jsonl"), ["unknown"])
        self.assertNotEqual(r["mechanical"]["overall"], "all_pass")

    def test_evidence_byte_changed(self):
        self.run_case()
        p = Path(self.run_dir(), "stdout.jsonl")
        data = bytearray(p.read_bytes())
        data[-3] ^= 1
        p.write_bytes(bytes(data))
        self.assertEqual(statuses(self.check(), "evidence.stdout.jsonl"), ["fail"])

    def test_registry_field_shape_wrong(self):
        self.run_case()
        reg = Path(self.evidence, "registry.jsonl")
        recs = [json.loads(l) for l in reg.read_text("utf-8").splitlines()]
        recs[0]["attempt"] = "1"
        reg.write_text("".join(json.dumps(r) + "\n" for r in recs), "utf-8")
        self.assertEqual(statuses(self.check(), "registry.legal"), ["fail"])

    def test_tool_result_field_shape_wrong(self):
        tools = build.good_tools(self.export)
        tools[0]["stream_output"] = 7          # output must be a string in 0.65.0
        self.run_case(tools=tools)
        self.assertEqual(statuses(self.check(), "adapter.gates"), ["fail"])

    def test_workspace_mutated_by_run(self):
        self.run_case(scenario={"mutate": ["src/NEW.txt"]})
        r = self.check()
        self.assertEqual(statuses(r, "adapter.gates"), ["fail"])

    def test_nonzero_exit(self):
        self.run_case(scenario={"exit_code": 1})
        self.assertEqual(statuses(self.check(), "adapter.gates"), ["fail"])

    def test_crlf_task_copy_is_reported(self):
        self.run_case()
        pkg_copy = os.path.join(self.base, "pkg")
        shutil.copytree(PKG_DIR, pkg_copy)
        t = Path(pkg_copy, "task.txt")
        t.write_bytes(t.read_bytes().replace(b"\n", b"\r\n"))
        r = self.check(package=os.path.join(pkg_copy, "package.json"))
        item = [i for i in r["mechanical"]["items"] if i["check"] == "package.task_file"][0]
        self.assertEqual(item["status"], "fail")
        self.assertIn("CRLF", item["detail"])


class TestToolContent(_Base):
    def test_grep_line_differs_from_source(self):
        tools = build.good_tools(self.export)
        tools[0]["chat_output"] = tools[0]["chat_output"].replace("_registry_problem", "_registry_problen", 3)
        self.run_case(tools=tools)
        self.assertIn("fail", statuses(self.check(), "tools.grep_search__1"))

    def test_read_content_shifted(self):
        tools = build.good_tools(self.export)
        shifted, _ = build.read_output(self.export, build.TARGET, 1061, 1146)
        tools[1]["chat_output"] = shifted.replace("1061-1146", "1060-1145")
        self.run_case(tools=tools)
        self.assertEqual(statuses(self.check(), "tools.read_file__1"), ["fail"])

    def test_stream_summary_disagrees(self):
        tools = build.good_tools(self.export)
        tools[1]["stream_output"] = "Read lines 1-20 of 1582 from src/agent_session_gemini.py"
        self.run_case(tools=tools)
        self.assertEqual(statuses(self.check(), "tools.read_file__1"), ["fail"])

    def test_unknown_grep_format(self):
        tools = build.good_tools(self.export)
        for t in tools:
            if t["name"] == "grep_search":
                t["chat_output"] = "Matches:\n" + t["chat_output"]
        self.run_case(tools=tools)
        r = self.check()
        self.assertEqual(statuses(r, "tools.grep_search__1"), ["unknown"])
        self.assertEqual(statuses(r, "required.tools"), ["unknown"])
        self.assertEqual(r["mechanical"]["overall"], "undetermined")

    def test_untruncated_read_format_is_unknown(self):
        tools = build.good_tools(self.export)
        tools[1]["chat_output"] = Path(self.export, *build.TARGET.split("/")).read_text("utf-8")
        self.run_case(tools=tools)
        self.assertEqual(statuses(self.check(), "tools.read_file__1"), ["unknown"])

    def test_read_outside_workspace_is_not_read(self):
        tools = build.good_tools(self.export)
        outside = os.path.join(self.base, "outside-not-created", "secret.txt")
        tools[2]["params"] = {"file_path": outside, "start_line": 1, "end_line": 2}
        tools[2]["stream_output"] = "Read lines 1-2 of 2 from secret.txt"
        self.run_case(tools=tools)
        r = self.check()
        self.assertEqual(statuses(r, "tools.read_file__2"), ["fail"])
        self.assertFalse(os.path.exists(outside))

    def test_chat_call_missing_is_unknown(self):
        tools = build.good_tools(self.export)
        tools[1]["chat_id"] = "some-other-id"
        self.run_case(tools=tools)
        self.assertEqual(statuses(self.check(), "tools.read_file__1"), ["unknown"])

    def test_chat_args_disagree(self):
        tools = build.good_tools(self.export)
        tools[1]["chat_args"] = dict(tools[1]["params"], start_line=1)
        self.run_case(tools=tools)
        self.assertEqual(statuses(self.check(), "tools.read_file__1"), ["fail"])

    def test_no_grep_at_all(self):
        tools = [t for t in build.good_tools(self.export) if t["name"] != "grep_search"]
        self.run_case(tools=tools)
        self.assertEqual(statuses(self.check(), "required.tools"), ["fail"])

    def test_ref_to_missing_line(self):
        self.run_case(delivery=build.GOOD_DELIVERY + "另见 src/agent_session_gemini.py:99999。\n")
        self.assertEqual(statuses(self.check(), "refs.exist"), ["fail"])

    def test_core_point_not_cited(self):
        delivery = build.GOOD_DELIVERY.replace("src/agent_session_gemini.py:1218-1233", "_replay_locked") \
            .replace("submit 两次判定 src/agent_session_gemini.py:1237-1256", "submit 两次判定")
        self.run_case(delivery=delivery)
        self.assertEqual(statuses(self.check(), "refs.B4"), ["fail"])


class TestHostFormatSample(unittest.TestCase):
    """The old host-probe run is used ONLY as a sample of the real CLI 0.65.0 tool
    output format (real tools, synthetic model), re-checked against the source
    bytes it read, which are recovered from Git. It is a different task and is
    never a positive control for task01."""

    def test_parsers_match_real_cli_output_sample(self):
        run = HOST_PROBE / "runs" / HOST_PROBE_RUN
        if not run.is_dir():
            self.skipTest("host-probe evidence absent")
        before = json.loads((run / "workspace-before.json").read_text("utf-8"))
        want = before["src/agent_session.py"]["sha256"]
        revs = subprocess.run(["git", "-C", str(ROOT), "rev-list", "--all", "--", "src/agent_session.py"],
                              capture_output=True, text=True).stdout.split()
        src = None
        for rev in revs:
            data = subprocess.run(["git", "-C", str(ROOT), "show", f"{rev}:src/agent_session.py"],
                                  capture_output=True).stdout
            if hashlib.sha256(data).hexdigest() == want:
                src = data.decode("utf-8").split("\n")
                break
        if src is None:
            self.skipTest("sampled source revision not in this clone")
        calls, notes = check_run.chat_tool_calls((run / "host-chat-record.jsonl").read_bytes())
        seen = set()
        for call in calls.values():
            out = check_run.chat_output(call)
            if call["name"] == "read_file":
                m = check_run.READ_TRUNC.match(out)
                self.assertIsNotNone(m)
                a, b, total = int(m.group(1)), int(m.group(2)), int(m.group(3))
                self.assertEqual(total, len(src))
                self.assertEqual(m.group(4), "\n".join(src[a - 1:b]))
                seen.add("read")
            elif call["name"] == "grep_search":
                lines = out.split("\n")
                self.assertIsNotNone(check_run.GREP_HEAD.match(lines[0]))
                for line in lines[1:]:
                    if line in ("---", "") or line.startswith("File: "):
                        continue
                    m = check_run.GREP_LINE.match(line)
                    self.assertIsNotNone(m, line)
                    self.assertEqual(src[int(m.group(1)) - 1], m.group(3))
                seen.add("grep")
        self.assertEqual(seen, {"read", "grep"})
        summaries = [json.loads(l).get("output") for l in (run / "stdout.jsonl").read_text("utf-8").splitlines()
                     if json.loads(l).get("type") == "tool_result"]
        self.assertTrue(any(isinstance(s, str) and check_run.READ_SUMMARY.match(s) for s in summaries))


class TestHostFormatBindingSample(unittest.TestCase):
    """Hark-派单-22 R3: the request/response binding rules accept the REAL CLI 0.65.0
    sample (grep with the host's default context 50; read with an explicit range)
    against the source revision it read, recovered from Git. Format sample only:
    a different task, never a positive control for task01."""

    def test_real_sample_binds(self):
        run = HOST_PROBE / "runs" / HOST_PROBE_RUN
        if not run.is_dir():
            self.skipTest("host-probe evidence absent")
        before = json.loads((run / "workspace-before.json").read_text("utf-8"))
        want = before["src/agent_session.py"]["sha256"]
        revs = subprocess.run(["git", "-C", str(ROOT), "rev-list", "--all", "--", "src/agent_session.py"],
                              capture_output=True, text=True).stdout.split()
        data = None
        for rev in revs:
            blob = subprocess.run(["git", "-C", str(ROOT), "show", f"{rev}:src/agent_session.py"],
                                  capture_output=True).stdout
            if hashlib.sha256(blob).hexdigest() == want:
                data = blob
                break
        if data is None:
            self.skipTest("sampled source revision not in this clone")
        tmp = os.path.realpath(tempfile.mkdtemp(prefix="hark22-sample-"))
        self.addCleanup(shutil.rmtree, tmp, True)
        os.makedirs(os.path.join(tmp, "src"))
        Path(tmp, "src", "agent_session.py").write_bytes(data)
        reg = json.loads((HOST_PROBE / "registry.jsonl").read_text("utf-8").splitlines()[0])
        ctx = {"run": {"reserved": {"workspace": reg["workspace"]}}, "export_dir": tmp,
               "manifest": {"files": {"src/agent_session.py": {}}},
               "package": {"target_file": "src/agent_session.py"}}
        stream = [json.loads(l) for l in (run / "stdout.jsonl").read_text("utf-8").splitlines()]
        uses = {e["tool_id"]: e for e in stream if e["type"] == "tool_use"}
        results = {e["tool_id"]: e for e in stream if e["type"] == "tool_result"}
        calls, _ = check_run.chat_tool_calls((run / "host-chat-record.jsonl").read_bytes())
        seen = {}
        for tid, call in calls.items():
            if call["name"] not in ("grep_search", "read_file"):
                continue
            relation, _ = check_run.args_relation(call["name"], uses[tid]["parameters"], call["args"])
            out = check_run.chat_output(call)
            if call["name"] == "grep_search":
                self.assertEqual(relation, "default")
                status, detail, hits = check_run.check_grep(ctx, call["args"], out)
                self.assertEqual((status, hits), ("pass", 1), detail)
            else:
                self.assertEqual(relation, "same")
                status, detail, on_target = check_run.check_read(ctx, call["args"], out,
                                                                 results[tid].get("output"))
                self.assertEqual((status, on_target), ("pass", True), detail)
            seen[call["name"]] = status
        self.assertEqual(seen, {"grep_search": "pass", "read_file": "pass"})


class TestCli(_Base):
    def _main(self, *extra):
        return check_run.main(["--manifest", self.manifest, "--export-dir", self.export,
                               "--evidence-root", self.evidence, "--run-id", self.run_id, *extra])

    def test_exit_codes(self):
        self.run_case()
        out = os.path.join(self.base, "report.json")
        self.assertEqual(self._main("--out", out), 0)
        self.assertEqual(json.loads(Path(out).read_text("utf-8"))["semantic_review"]["status"], "required")
        self.assertEqual(self._main("--out", os.path.join(self.evidence, "r.json")), 2)
        os.remove(os.path.join(self.run_dir(), "host-chat-record.jsonl"))
        self.assertEqual(self._main("--out", os.path.join(self.base, "r2.json")), 3)
        Path(self.run_dir(), "stderr.txt").write_bytes(b"tampered")
        self.assertEqual(self._main("--out", os.path.join(self.base, "r3.json")), 1)


# Guard weakening: (weaken flag, case builder, check whose fail must disappear).
# Each case is detected ONLY by the named guard, so control fails and the
# weakened run must no longer fail overall.
def _w_package(t):
    t.run_case()
    pkg_copy = os.path.join(t.base, "pkg")
    shutil.copytree(PKG_DIR, pkg_copy)
    Path(pkg_copy, "review-baseline.md").write_text("tampered", "utf-8")
    return dict(package=os.path.join(pkg_copy, "package.json"))


def _w_export(t):
    t.run_case()
    p = Path(t.export, "src", "agent_session.py")
    p.write_bytes(p.read_bytes() + b"#")
    return {}


def _w_binding(t):
    t.run_case(task_id="some-other-task")
    return {}


def _w_stdin(t):
    t.run_case(scenario={"user_content_override": "something else"})
    return {}


def _w_workspace(t):
    t.run_case()
    p = Path(t.run_dir(), "workspace-after.json")
    obj = json.loads(p.read_text("utf-8"))
    obj["src/EXTRA"] = {"kind": "file", "size": 1, "sha256": "0" * 64}
    p.write_text(json.dumps(obj), "utf-8")
    return {}


def _w_evidence(t):
    t.run_case()
    Path(t.run_dir(), "stderr.txt").write_bytes(b"tampered")
    return {}


def _w_adapter(t):
    t.run_case(scenario={"exit_code": 1})
    return {}


def _w_tool(t):
    tools = build.good_tools(t.export)
    shifted, _ = build.read_output(t.export, build.TARGET, 1061, 1146)
    tools[1]["chat_output"] = shifted.replace("1061-1146", "1060-1145")
    t.run_case(tools=tools)
    return {}


def _w_required(t):
    t.run_case(tools=[x for x in build.good_tools(t.export) if x["name"] != "grep_search"])
    return {}


def _w_refs_exist(t):
    t.run_case(delivery=build.GOOD_DELIVERY + "另见 src/agent_session_gemini.py:99999。\n")
    return {}


def _w_refs_core(t):
    t.run_case(delivery=build.GOOD_DELIVERY.split("5. ")[0])
    return {}


# ===================================================================== Hark-派单-22 =====
# Rectification of the four confirmed counter-example classes R1-R4. Every case is
# CONSTRUCTED; where a counter-example rewrites an original file, the registry /
# manifest / package digests that describe it are updated with it, so that only the
# new cross-checks can catch it.

import uuid  # noqa: E402

import pins  # noqa: E402


# ---- read/write observer (Python audit hook; installed once, active only inside Observe)
_AUDIT = {"on": False, "events": []}
_AUDITED = ("open", "os.scandir", "os.listdir", "os.mkdir", "os.rename", "os.replace", "os.remove",
            "os.symlink", "shutil.copyfile")


def _audit_hook(event, args):
    if _AUDIT["on"] and event in _AUDITED:
        path = args[0] if args else None
        if isinstance(path, bytes):
            path = os.fsdecode(path)
        if isinstance(path, str):
            _AUDIT["events"].append((event, path))


sys.addaudithook(_audit_hook)


class Observe:
    """Records every open / scandir / listdir / mkdir / ... path while active."""

    def __enter__(self):
        _AUDIT["events"] = []
        _AUDIT["on"] = True
        return self

    def __exit__(self, *exc):
        _AUDIT["on"] = False
        self.events = list(_AUDIT["events"])
        return False

    def touched(self, root):
        """Events whose path, resolved through any link, lies under root."""
        root = os.path.realpath(root)
        out = []
        for event, path in self.events:
            try:
                real = os.path.realpath(path)
            except (OSError, ValueError):
                continue
            if real == root or real.startswith(root.rstrip(os.sep) + os.sep):
                out.append((event, path))
        return out


def write_manifest_from_disk(manifest, export):
    """Rewrite the export manifest so it describes the export exactly as it now is."""
    man = json.loads(Path(manifest).read_text("utf-8"))
    files = {}
    for dirpath, dirs, names in os.walk(export):
        for name in names:
            full = os.path.join(dirpath, name)
            rel = os.path.relpath(full, export).replace(os.sep, "/")
            data = Path(full).read_bytes()
            files[rel] = {"blob": export_source.git_blob_id(data), "size": len(data),
                          "sha256": hashlib.sha256(data).hexdigest()}
    man["files"] = files
    os.remove(manifest)
    Path(manifest).write_text(json.dumps(man, indent=1, sort_keys=True) + "\n", "utf-8")


def package_copy(base, edit=None, task=None):
    """A copy of the package directory; `edit(pkg)` changes package.json, `task` replaces task.txt
    (and its digest in the package, so the copy is self-consistent)."""
    d = os.path.join(base, "pkg-copy")
    shutil.copytree(PKG_DIR, d, ignore=shutil.ignore_patterns("__pycache__"))
    pkg = json.loads(Path(d, "package.json").read_text("utf-8"))
    if task is not None:
        Path(d, "task.txt").write_bytes(task.encode("utf-8"))
        pkg["task_sha256"] = hashlib.sha256(task.encode("utf-8")).hexdigest()
    if edit:
        edit(pkg)
    Path(d, "package.json").write_text(json.dumps(pkg, ensure_ascii=False, indent=1, sort_keys=True) + "\n",
                                       "utf-8")
    return os.path.join(d, "package.json")


def item(result, check):
    return [i for i in result["mechanical"]["items"] if i["check"] == check]


OTHER_TASK = "Find where class RunState is defined and list its states as path:line.\n"


# ---------------------------------------------------------------- R1 builders ------------

def _r1_non_target_changed(t):
    p = Path(t.export, "src", "agent_session.py")
    p.write_bytes(p.read_bytes() + b"# changed\n")
    write_manifest_from_disk(t.manifest, t.export)
    t.run_case()
    return {}


def _r1_file_added(t):
    Path(t.export, "src", "EXTRA_NOTES.py").write_text("NOTE = 1\n", "utf-8")
    write_manifest_from_disk(t.manifest, t.export)
    t.run_case()
    return {}


def _r1_file_deleted(t):
    os.remove(os.path.join(t.export, "src", "agent_session.py"))
    write_manifest_from_disk(t.manifest, t.export)
    t.run_case()
    return {}


def _r1_task_swapped(t):
    pkg = package_copy(t.base, edit=lambda p: p.update(task_id="other-task"), task=OTHER_TASK)
    t.run_case(instructions=OTHER_TASK, task_id="other-task")
    return {"package": pkg}


def _r1_focus_moved(t):
    src = build.lines_of(t.export, build.TARGET)

    def edit(p):
        f = p["core_points"]["B7"]["focus"]
        f["start"], f["end"] = 1235, 1259            # consistent digest of other lines
        f["sha256"] = hashlib.sha256("\n".join(src[1234:1259]).encode("utf-8")).hexdigest()
    pkg = package_copy(t.base, edit=edit)
    t.run_case(delivery=build.GOOD_DELIVERY.replace("src/agent_session_gemini.py:1324-1330",
                                                    "src/agent_session_gemini.py:1240-1250"))
    return {"package": pkg}


# ---------------------------------------------------------------- R2 builders ------------

def _r2_result_error(t):
    t.run_case(scenario={"result_status": "error"})
    build.edit_collected(t.evidence, t.run_id, build.forge_clean)
    return {}


def _r2_init_model(t):
    t.run_case(scenario={"host_model": "other-model"})

    def fix(r):
        build.forge_clean(r)
        r["artifacts"][0]["host_reported_model"] = build.MODEL
    build.edit_collected(t.evidence, t.run_id, fix)
    return {}


def _r2_usage_missing(t):
    t.run_case(scenario={"no_stats": True})
    return {}


def _r2_usage_field_missing(t):
    t.run_case(scenario={"drop_stats_fields": ["duration_ms"]})
    return {}


PREAMBLE = ["Rename failed with EPERM, retrying in 50ms (attempt 1/5)...",
            "Rename failed with EPERM, retrying in 100ms (attempt 2/5)..."]


def _r2_preamble_stderr_failure(t):
    t.run_case(scenario={"stdout_preamble": PREAMBLE, "stderr": "Failed to save project registry\n"})
    build.edit_collected(t.evidence, t.run_id, build.forge_clean)
    return {}


def _r2_errors_mismatch(t):
    t.run_case(scenario={"extra_events": [{"type": "error", "timestamp": "t", "severity": "warning",
                                           "message": "host warning"}]})
    build.edit_collected(t.evidence, t.run_id, lambda r: r["artifacts"][0].update(host_errors=[]))
    return {}


def _r2_chat_session(t):
    t.run_case()
    path = Path(t.run_dir(), "host-chat-record.jsonl")
    lines = path.read_text("utf-8").split("\n")
    head = json.loads(lines[0])
    head["sessionId"] = str(uuid.uuid4())
    lines[0] = json.dumps(head, separators=(",", ":"))
    build.replace_evidence(t.evidence, t.run_id, "host-chat-record.jsonl", "\n".join(lines).encode("utf-8"))
    return {}


def _r2_chat_model(t):
    t.run_case(scenario={"chat_model": "other-model"})
    return {}


def _r2_fr_name(t):
    tools = build.good_tools(t.export)
    tools[1]["fr_name"] = "grep_search"
    t.run_case(tools=tools)
    return {}


def _r2_illegal_tool_call(t):
    t.run_case(scenario={"chat_extra_lines": [
        {"id": "g-x", "timestamp": "t", "type": "gemini", "content": "", "model": build.MODEL,
         "toolCalls": [{"id": "bogus-without-name"}]}]})
    return {}


def _r2_tool_calls_int(t):
    t.run_case(scenario={"chat_extra_lines": [
        {"id": "g-y", "timestamp": "t", "type": "gemini", "content": "", "model": build.MODEL, "toolCalls": 7}]})
    return {}


def _r2_ghost_tool_call(t):
    fr = {"functionResponse": {"id": "ghost__1", "name": "read_file", "response": {"output": "x"}}}
    t.run_case(scenario={"chat_extra_lines": [
        {"id": "g-z", "timestamp": "t", "type": "gemini", "content": "", "model": build.MODEL,
         "toolCalls": [{"id": "ghost__1", "name": "read_file", "args": {"file_path": "x"}, "result": [fr],
                        "status": "success"}]},
        {"id": "u-z", "timestamp": "t", "type": "user", "content": [fr]}]})
    return {}


def _r2_auto_model(t):
    t.run_case(model="auto")
    return {}


# ---------------------------------------------------------------- R3 builders ------------

def _r3_read_range(t):
    tools = build.good_tools(t.export)
    tools[1]["params"] = dict(tools[1]["params"], start_line=1, end_line=5)
    t.run_case(tools=tools)
    return {}


def _r3_grep_header(t):
    tools = build.good_tools(t.export)
    tools[0]["chat_output"] = build.grep_output(t.export, "_registry_problem", header_pattern="registry_text_problem")
    t.run_case(tools=tools)
    return {}


def _r3_grep_window(t):
    tools = build.good_tools(t.export)
    tools[3]["chat_output"] = build.grep_output(t.export, build.DEFAULT_GREP, context=10)
    t.run_case(tools=tools)
    return {}


# ---------------------------------------------------------------- R4 builders ------------

def _r4_task_outside(t):
    outside = os.path.join(t.base, "outside-task.txt")
    shutil.copyfile(PKG_DIR / "task.txt", outside)
    pkg = package_copy(t.base, edit=lambda p: p.update(task_file="../outside-task.txt"))
    os.remove(os.path.join(os.path.dirname(pkg), "task.txt"))
    t.run_case()
    t.sentinel = outside
    return {"package": pkg, "_base": {"package_anchor"}}


def _r4_run_dir_link(t):
    t.run_case()
    elsewhere = os.path.join(t.base, "elsewhere")
    os.makedirs(elsewhere)
    moved = os.path.join(elsewhere, t.run_id)
    shutil.move(t.run_dir(), moved)
    os.symlink(moved, t.run_dir(), target_is_directory=True)
    t.sentinel = elsewhere
    return {}


def _r4_evidence_root_link(t):
    t.run_case()
    real = os.path.join(t.base, "evidence-real")
    shutil.move(t.evidence, real)
    os.symlink(real, t.evidence, target_is_directory=True)
    t.sentinel = real
    return {}


def _r4_evidence_file_link(t):
    t.run_case()
    outside = os.path.join(t.base, "outside-ev")
    os.makedirs(outside)
    src = os.path.join(t.run_dir(), "stderr.txt")
    dst = os.path.join(outside, "stderr.txt")
    shutil.move(src, dst)
    os.symlink(dst, src)
    t.sentinel = outside
    return {}


def _r4_export_root_link(t):
    link = os.path.join(t.base, "target-link")
    os.symlink(t.export, link, target_is_directory=True)
    t.sentinel = t.export
    t.export = link
    t.run_case(scenario={"workspace_alias": link})
    return {}


# (label, weakened guard, builder). Each case is caught ONLY by the named guard:
# the control must not be all_pass, and with that guard weakened it must become
# all_pass / exit 0. A case's "_base" set is weakened in BOTH runs (a guard that
# would stop the case earlier, e.g. the package anchor for a rewritten package).
WEAKENING = [
    ("package_digest", "package_digest", _w_package), ("export_tree", "export_tree", _w_export),
    ("binding_request", "binding_request", _w_binding), ("stdin", "stdin", _w_stdin),
    ("workspace_manifest", "workspace_manifest", _w_workspace), ("evidence_digest", "evidence_digest", _w_evidence),
    ("adapter_gates", "adapter_gates", _w_adapter), ("tool_content", "tool_content", _w_tool),
    ("required_tools", "required_tools", _w_required), ("refs_exist", "refs_exist", _w_refs_exist),
    ("refs_core", "refs_core", _w_refs_core),
    # R1
    ("R1.non_target_changed", "src_anchor", _r1_non_target_changed),
    ("R1.file_added", "src_anchor", _r1_file_added),
    ("R1.file_deleted", "src_anchor", _r1_file_deleted),
    ("R1.task_swapped", "package_anchor", _r1_task_swapped),
    ("R1.focus_moved", "package_anchor", _r1_focus_moved),
    # R2
    ("R2.result_error", "native_status", _r2_result_error),
    ("R2.init_model", "native_model", _r2_init_model),
    ("R2.usage_missing", "usage", _r2_usage_missing),
    ("R2.usage_field_missing", "usage", _r2_usage_field_missing),
    ("R2.preamble_stderr_failure", "host_preamble", _r2_preamble_stderr_failure),
    ("R2.errors_mismatch", "host_errors", _r2_errors_mismatch),
    ("R2.chat_session", "chat_record", _r2_chat_session),
    ("R2.chat_model", "chat_record", _r2_chat_model),
    ("R2.fr_name", "chat_record", _r2_fr_name),
    ("R2.illegal_tool_call", "chat_record", _r2_illegal_tool_call),
    ("R2.tool_calls_int", "chat_record", _r2_tool_calls_int),
    ("R2.ghost_tool_call", "chat_record", _r2_ghost_tool_call),
    ("R2.auto_model", "explicit_model", _r2_auto_model),
    # R3
    ("R3.read_range", "read_binding", _r3_read_range),
    ("R3.grep_header", "grep_binding", _r3_grep_header),
    ("R3.grep_window", "grep_binding", _r3_grep_window),
    # R4 (I/O guards: the observer also shows no access to the sentinel in the control)
    ("R4.task_outside", "input_paths", _r4_task_outside),
    ("R4.run_dir_link", "evidence_paths", _r4_run_dir_link),
    ("R4.evidence_root_link", "evidence_paths", _r4_evidence_root_link),
    ("R4.evidence_file_link", "evidence_paths", _r4_evidence_file_link),
    ("R4.export_root_link", "export_root", _r4_export_root_link),
]


class TestGuardWeakening(_Base):
    def _exit_code(self, kwargs):
        out = os.path.join(self.base, f"report-{uuid.uuid4().hex}.json")
        os.environ["HARK21_TEST_WEAKEN"] = "1"
        try:
            return check_run.main(["--package", kwargs.get("package", build.PACKAGE), "--manifest", self.manifest,
                                   "--export-dir", self.export, "--evidence-root", self.evidence,
                                   "--run-id", self.run_id, "--out", out])
        finally:
            os.environ.pop("HARK21_TEST_WEAKEN", None)

    def test_each_guard_control_then_weakened(self):
        report = {}
        for label, flag, make in WEAKENING:
            with self.subTest(case=label):
                shutil.rmtree(self.base, ignore_errors=True)
                os.makedirs(self.base)
                self.export, self.manifest = build.make_export(self.base)
                self.sentinel = None
                check_run.WEAKEN.clear()
                kwargs = make(self)
                base = set(kwargs.pop("_base", set()))
                check_run.WEAKEN.update(base)
                with Observe() as obs_c:
                    control = self.check(**kwargs)
                control_exit = self._exit_code(kwargs)
                check_run.WEAKEN.add(flag)
                with Observe() as obs_w:
                    weakened = self.check(**kwargs)
                weakened_exit = self._exit_code(kwargs)
                check_run.WEAKEN.clear()
                c, w = control["mechanical"]["overall"], weakened["mechanical"]["overall"]
                entry = {"guard": flag, "control": c, "control_exit": control_exit, "weakened": w,
                         "weakened_exit": weakened_exit}
                if self.sentinel:
                    entry["sentinel_access_control"] = len(obs_c.touched(self.sentinel))
                    entry["sentinel_access_weakened"] = len(obs_w.touched(self.sentinel))
                report[label] = entry
                self.assertNotEqual(c, "all_pass", (label, control["mechanical"]["items"]))
                self.assertNotEqual(control_exit, 0, label)
                self.assertEqual(w, "all_pass", (label, [i for i in weakened["mechanical"]["items"]
                                                         if i["status"] != "pass"]))
                self.assertEqual(weakened_exit, 0, label)
                if self.sentinel:
                    self.assertEqual(entry["sentinel_access_control"], 0, (label, obs_c.touched(self.sentinel)))
                    self.assertGreater(entry["sentinel_access_weakened"], 0, label)
        print("WEAKENING " + json.dumps(report, sort_keys=True))


# ------------------------------------------------------------------ direct R1-R4 tests ----

class TestR1Anchors(_Base):
    def test_pins_are_the_git_objects_of_the_fixed_commit(self):
        raw = subprocess.run(["git", "-C", str(ROOT), "ls-tree", "-r", "--full-tree", pins.SOURCE_COMMIT,
                              "--", "src/"], capture_output=True, text=True, check=True).stdout.split("\n")
        listed = {l.split("\t")[1]: l.split()[2] for l in raw if l}
        self.assertEqual(listed, {p: v[0] for p, v in pins.SOURCE_FILES.items()})
        self.assertEqual(len(listed), 26)
        self.assertEqual(pins.SOURCE_FILES[pins.TARGET_FILE][0], pins.TARGET_BLOB)
        for p, (blob, size, digest) in pins.SOURCE_FILES.items():
            data = subprocess.run(["git", "-C", str(ROOT), "cat-file", "blob", blob], capture_output=True,
                                  check=True).stdout
            self.assertEqual((len(data), hashlib.sha256(data).hexdigest()), (size, digest), p)

    def test_package_files_match_pins(self):
        for name, digest in (("package.json", pins.PACKAGE_SHA256), ("task.txt", pins.TASK_SHA256),
                             ("review-baseline.md", pins.REVIEW_BASELINE_SHA256)):
            self.assertEqual(hashlib.sha256((PKG_DIR / name).read_bytes()).hexdigest(), digest, name)
        pkg = build.package()
        self.assertEqual((pkg["task_id"], pkg["source_commit"], pkg["target_blob"]),
                         (pins.TASK_ID, pins.SOURCE_COMMIT, pins.TARGET_BLOB))

    def _expect(self, make, check):
        kwargs = make(self)
        r = self.check(**kwargs)
        self.assertEqual([i["status"] for i in item(r, check)], ["fail"], r["mechanical"]["items"])
        self.assertEqual(r["mechanical"]["overall"], "fail")

    def test_non_target_changed(self):
        self._expect(_r1_non_target_changed, "export.src_anchor")

    def test_file_added(self):
        self._expect(_r1_file_added, "export.src_anchor")

    def test_file_deleted(self):
        self._expect(_r1_file_deleted, "export.src_anchor")

    def test_task_swapped(self):
        self._expect(_r1_task_swapped, "package.anchor")

    def test_focus_moved(self):
        self._expect(_r1_focus_moved, "package.anchor")

    def test_exporter_refuses_unpinned_package(self):
        pkg = package_copy(self.base, task=OTHER_TASK)
        with self.assertRaises(SystemExit):
            export_source.load_package(pkg)


class TestR2Native(_Base):
    def _expect(self, make, check, status="fail"):
        make(self)
        r = self.check()
        self.assertEqual([i["status"] for i in item(r, check)], [status], r["mechanical"]["items"])
        self.assertNotEqual(r["mechanical"]["overall"], "all_pass")
        return r

    def test_result_error_but_collected_success(self):
        self._expect(_r2_result_error, "native.status")

    def test_init_model_differs(self):
        self._expect(_r2_init_model, "native.model")

    def test_usage_missing_is_unknown(self):
        self._expect(_r2_usage_missing, "native.usage", "unknown")

    def test_usage_field_missing_is_unknown(self):
        self._expect(_r2_usage_field_missing, "native.usage", "unknown")

    def test_usage_differs_from_stats(self):
        self.run_case()
        build.edit_collected(self.evidence, self.run_id, lambda r: r["usage"].update(total_tokens=999))
        self.assertEqual([i["status"] for i in item(self.check(), "native.usage")], ["fail"])

    def test_preamble_with_stderr_final_failure(self):
        self._expect(_r2_preamble_stderr_failure, "native.host_preamble")

    def test_known_preamble_alone_is_accepted(self):
        self.run_case(scenario={"stdout_preamble": PREAMBLE})
        r = self.check()
        self.assertEqual(r["mechanical"]["overall"], "all_pass", r["mechanical"]["items"])

    def test_errors_mismatch(self):
        self._expect(_r2_errors_mismatch, "native.errors")

    def test_chat_session_differs(self):
        self._expect(_r2_chat_session, "chat.record")

    def test_chat_model_differs(self):
        self._expect(_r2_chat_model, "chat.record")

    def test_function_response_name_differs(self):
        self._expect(_r2_fr_name, "chat.record")

    def test_illegal_tool_call(self):
        self._expect(_r2_illegal_tool_call, "chat.record")

    def test_tool_calls_not_a_list(self):
        self._expect(_r2_tool_calls_int, "chat.record")

    def test_tool_call_not_in_stream(self):
        self._expect(_r2_ghost_tool_call, "chat.record")

    def test_rewrite_changes_tool_call(self):
        tools = build.good_tools(self.export)
        self.run_case(tools=tools, scenario={"chat_extra_lines": [
            {"id": "g-1", "timestamp": "t", "type": "gemini", "content": "", "model": build.MODEL,
             "toolCalls": []}]})
        r = self.check()
        self.assertEqual([i["status"] for i in item(r, "chat.record")], ["fail"])

    def test_auto_model(self):
        self._expect(_r2_auto_model, "binding.model")

    def test_real_chat_record_sample_is_supported_shape(self):
        run = HOST_PROBE / "runs" / HOST_PROBE_RUN
        if not run.is_dir():
            self.skipTest("host-probe evidence absent")
        rec = json.loads((HOST_PROBE / "registry.jsonl").read_text("utf-8").splitlines()[0])
        parsed = check_run.parse_chat((run / "host-chat-record.jsonl").read_bytes(), rec["native_session_id"],
                                      rec["requested_model"])
        self.assertEqual((parsed["fail"], parsed["unknown"]), ([], []))
        self.assertEqual(len(parsed["calls"]), 4)


class TestR3Binding(_Base):
    def test_read_range_differs_from_request(self):
        _r3_read_range(self)
        self.assertEqual(statuses(self.check(), "tools.read_file__1"), ["fail"])

    def test_grep_header_pattern_differs(self):
        _r3_grep_header(self)
        self.assertEqual(statuses(self.check(), "tools.grep_search__1"), ["fail"])

    def test_grep_context_window_differs(self):
        _r3_grep_window(self)
        self.assertEqual(statuses(self.check(), "tools.grep_search__2"), ["fail"])

    def test_unexplained_grep_parameter_is_unknown(self):
        tools = build.good_tools(self.export)
        tools[0]["chat_args"] = dict(tools[0]["params"], include="*.py")
        self.run_case(tools=tools)
        self.assertEqual(statuses(self.check(), "tools.grep_search__1"), ["unknown"])

    def test_unexplained_default_value_is_unknown(self):
        tools = build.good_tools(self.export)
        tools[3]["chat_args"] = {"pattern": build.DEFAULT_GREP, "context": 30}
        self.run_case(tools=tools)
        self.assertEqual(statuses(self.check(), "tools.grep_search__2"), ["unknown"])

    def test_read_without_explicit_range_is_unknown(self):
        tools = build.good_tools(self.export)
        tools[1]["params"] = {"file_path": tools[1]["params"]["file_path"]}
        self.run_case(tools=tools)
        self.assertEqual(statuses(self.check(), "tools.read_file__1"), ["unknown"])

    def test_read_end_beyond_file_is_unknown(self):
        tools = build.good_tools(self.export)
        total = len(build.lines_of(self.export, build.TARGET))
        out, summary = build.read_output(self.export, build.TARGET, 1500, total)
        tools[1].update(params=dict(tools[1]["params"], start_line=1500, end_line=total + 50),
                        chat_output=out, stream_output=summary)
        self.run_case(tools=tools)
        self.assertEqual(statuses(self.check(), "tools.read_file__1"), ["unknown"])


class TestR4Boundaries(_Base):
    def _no_access(self, make):
        kwargs = make(self)
        kwargs.pop("_base", None)
        with Observe() as obs:
            r = self.check(**kwargs)
        self.assertEqual(obs.touched(self.sentinel), [])
        self.assertEqual(r["mechanical"]["overall"], "fail")
        return r

    def test_run_dir_link_not_followed(self):
        r = self._no_access(_r4_run_dir_link)
        self.assertIn("fail", statuses(r, "evidence."))

    def test_evidence_root_link_not_followed(self):
        r = self._no_access(_r4_evidence_root_link)
        self.assertEqual(statuses(r, "registry.read"), ["fail"])

    def test_evidence_file_link_not_followed(self):
        r = self._no_access(_r4_evidence_file_link)
        self.assertEqual(statuses(r, "evidence.stderr.txt"), ["fail"])

    def test_export_root_link_not_followed(self):
        r = self._no_access(_r4_export_root_link)
        self.assertEqual(statuses(r, "export.root"), ["fail"])

    def test_task_file_outside_not_read(self):
        kwargs = _r4_task_outside(self)
        check_run.WEAKEN.update(kwargs.pop("_base"))
        with Observe() as obs:
            r = self.check(**kwargs)
        self.assertEqual(obs.touched(self.sentinel), [])
        self.assertEqual(statuses(r, "package.task_file"), ["fail"])

    def test_run_id_traversal_not_opened(self):
        self.run_case()
        with Observe() as obs:
            r = self.check(run_id="../../" + os.path.basename(self.base))
        self.assertEqual(statuses(r, "registry.run_id"), ["fail"])
        self.assertEqual([e for e in obs.events if self.evidence in os.path.realpath(e[1])], [])

    def test_exporter_refuses_link_root_and_writes_nothing(self):
        real = os.path.join(self.base, "link-target")
        os.makedirs(real)
        link = os.path.join(self.base, "out-link")
        os.symlink(real, link, target_is_directory=True)
        with Observe() as obs:
            with self.assertRaises(SystemExit):
                export_source.export(str(ROOT), link, os.path.join(self.base, "m-link.json"), build.package())
        self.assertEqual(os.listdir(real), [])
        self.assertEqual(obs.touched(real), [])
        self.assertFalse(os.path.exists(os.path.join(self.base, "m-link.json")))

    def test_exporter_refuses_link_parent(self):
        real = os.path.join(self.base, "parent-target")
        os.makedirs(real)
        link = os.path.join(self.base, "parent-link")
        os.symlink(real, link, target_is_directory=True)
        with self.assertRaises(SystemExit):
            export_source.export(str(ROOT), os.path.join(link, "out"), os.path.join(self.base, "m2.json"),
                                 build.package())
        self.assertEqual(os.listdir(real), [])

    def test_export_entry_link_recorded_not_read(self):
        outside = os.path.join(self.base, "outside-src")
        os.makedirs(outside)
        target = os.path.join(outside, "agent_session.py")
        p = os.path.join(self.export, "src", "agent_session.py")
        shutil.move(p, target)
        os.symlink(target, p)
        self.sentinel = outside
        self.run_case()
        with Observe() as obs:
            r = self.check()
        self.assertEqual(statuses(r, "export.tree"), ["fail"])
        self.assertEqual(obs.touched(outside), [])

    def test_windows_path_kinds(self):
        ws = "C:\\jh\\target"
        cases = {
            "C:\\jh\\target\\src\\agent_session_gemini.py": "src/agent_session_gemini.py",
            "c:/JH/target/src/agent_session_gemini.py": "src/agent_session_gemini.py",
            "src\\agent_session_gemini.py": "src/agent_session_gemini.py",
            "src/agent_session_gemini.py": "src/agent_session_gemini.py",
            "\\src\\agent_session_gemini.py": None,          # root-relative
            "/src/agent_session_gemini.py": None,            # root-relative
            "C:src\\agent_session_gemini.py": None,          # drive-relative
            "D:\\jh\\target\\src\\agent_session_gemini.py": None,
            "\\\\server\\share\\src\\agent_session_gemini.py": None,
            "\\\\?\\C:\\jh\\target\\src\\agent_session_gemini.py": None,
            "C:\\jh\\target\\..\\target\\src\\x.py": None,
            "src\\..\\src\\agent_session_gemini.py": None,
            "src\\agent_session_gemini.py:stream": None,
            "src\\agent_session_gemini.py.": None,
        }
        got = {p: check_run.map_to_export(p, ws) for p in cases}
        self.assertEqual(got, cases)
        # weakened guard reproduces the reported misreading (root/drive-relative mapped legal)
        check_run.WEAKEN.add("ws_path_kinds")
        weak = {p: check_run.map_to_export(p, ws) for p in ("\\src\\agent_session_gemini.py",
                                                             "/src/agent_session_gemini.py",
                                                             "C:src\\agent_session_gemini.py")}
        check_run.WEAKEN.clear()
        self.assertEqual(set(weak.values()), {"src/agent_session_gemini.py"})
        posix = {"/w/t/src/a.py": "src/a.py", "src/a.py": "src/a.py", "/src/a.py": None,
                 "src\\a.py": None, "../t/src/a.py": None}
        self.assertEqual({p: check_run.map_to_export(p, "/w/t") for p in posix}, posix)

    def test_root_relative_reference_is_judged_not_skipped(self):
        refs = [m.group(1) for m in check_run.SOURCE_REF.finditer(
            "见 /src/agent_session_gemini.py:1134 与 \\src\\agent_session_gemini.py:1135 和 C:src\\a.py:3")]
        self.assertEqual(refs, ["/src/agent_session_gemini.py", "\\src\\agent_session_gemini.py", "C:src\\a.py"])
        self.run_case(delivery=build.GOOD_DELIVERY + "另见 /src/agent_session_gemini.py:1134。\n")
        self.assertEqual(statuses(self.check(), "refs.exist"), ["fail"])

    def test_input_shapes_give_stable_diagnostics(self):
        self.run_case()
        ok_reg = Path(self.evidence, "registry.jsonl").read_bytes()
        cases = []
        # package not an object (anchor weakened so the shape check itself is reached)
        cases.append(("package.shape", {"package": self._raw_package(b"[]\n")}, {"package_anchor"}))
        cases.append(("package.anchor", {"package": self._raw_package(b"[]\n")}, set()))
        # manifest not an object / entry escaping
        m1 = os.path.join(self.base, "m-list.json")
        Path(m1).write_text("[1, 2]", "utf-8")
        cases.append(("export.manifest", {"manifest": m1}, set()))
        m2 = os.path.join(self.base, "m-escape.json")
        man = json.loads(Path(self.manifest).read_text("utf-8"))
        man["files"]["src/../../outside.py"] = man["files"]["src/agent_session.py"]
        Path(m2).write_text(json.dumps(man), "utf-8")
        cases.append(("export.manifest", {"manifest": m2}, set()))
        for check, kwargs, weaken in cases:
            with self.subTest(check=check, kwargs=list(kwargs)):
                check_run.WEAKEN.clear()
                check_run.WEAKEN.update(weaken)
                r = self.check(**kwargs)
                check_run.WEAKEN.clear()
                self.assertEqual([i["status"] for i in item(r, check)], ["fail"], r["mechanical"]["items"])
        # collected record shapes (registry still legal: only nested values change)
        for label, fn, check in (
                ("artifact not an object", lambda r: r.update(artifacts=[5]), "adapter.gates"),
                ("evidence entry not an object", lambda r: r["evidence"].update({"stderr.txt": "x"}),
                 "evidence.stderr.txt")):
            with self.subTest(label=label):
                Path(self.evidence, "registry.jsonl").write_bytes(ok_reg)
                build.edit_collected(self.evidence, self.run_id, fn)
                r = self.check()
                self.assertEqual([i["status"] for i in item(r, check)], ["fail"], r["mechanical"]["items"])
        # registry line not an object
        Path(self.evidence, "registry.jsonl").write_bytes(ok_reg + b"7\n")
        self.assertEqual(statuses(self.check(), "registry.legal"), ["fail"])

    def _raw_package(self, data):
        d = os.path.join(self.base, f"pkg-raw-{uuid.uuid4().hex[:6]}")
        shutil.copytree(PKG_DIR, d, ignore=shutil.ignore_patterns("__pycache__"))
        Path(d, "package.json").write_bytes(data)
        return os.path.join(d, "package.json")

    def test_main_refuses_weakened_hook_outside_tests(self):
        self.run_case()
        check_run.WEAKEN.add("stdin")
        try:
            code = check_run.main(["--manifest", self.manifest, "--export-dir", self.export,
                                   "--evidence-root", self.evidence, "--run-id", self.run_id,
                                   "--out", os.path.join(self.base, "r-w.json")])
        finally:
            check_run.WEAKEN.clear()
        self.assertEqual(code, 2)


if __name__ == "__main__":
    unittest.main()
