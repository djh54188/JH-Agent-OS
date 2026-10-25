"""Hark-派单-21: first Gemini source task package (task01), offline only.

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
        tools[0]["chat_output"] = "Matches:\n" + tools[0]["chat_output"]
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
        tools = build.good_tools(self.export)[1:]
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
    t.run_case(tools=build.good_tools(t.export)[1:])
    return {}


def _w_refs_exist(t):
    t.run_case(delivery=build.GOOD_DELIVERY + "另见 src/agent_session_gemini.py:99999。\n")
    return {}


def _w_refs_core(t):
    t.run_case(delivery=build.GOOD_DELIVERY.split("5. ")[0])
    return {}


WEAKENING = [
    ("package_digest", _w_package), ("export_tree", _w_export), ("binding_request", _w_binding),
    ("stdin", _w_stdin), ("workspace_manifest", _w_workspace), ("evidence_digest", _w_evidence),
    ("adapter_gates", _w_adapter), ("tool_content", _w_tool), ("required_tools", _w_required),
    ("refs_exist", _w_refs_exist), ("refs_core", _w_refs_core),
]


class TestGuardWeakening(_Base):
    def test_each_guard_control_then_weakened(self):
        report = {}
        for flag, make in WEAKENING:
            with self.subTest(guard=flag):
                shutil.rmtree(self.base, ignore_errors=True)
                os.makedirs(self.base)
                self.export, self.manifest = build.make_export(self.base)
                check_run.WEAKEN.clear()
                kwargs = make(self)
                control = self.check(**kwargs)["mechanical"]["overall"]
                check_run.WEAKEN.add(flag)
                weakened = self.check(**kwargs)["mechanical"]["overall"]
                check_run.WEAKEN.clear()
                report[flag] = (control, weakened)
                self.assertEqual(control, "fail", flag)
                self.assertNotEqual(weakened, "fail", flag)
        print("WEAKENING " + json.dumps(report, sort_keys=True))


if __name__ == "__main__":
    unittest.main()
