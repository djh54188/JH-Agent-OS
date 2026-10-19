"""JH-020 run-history query surface: the application interface and its guards.

A synthetic registry is written in the exact recorded format the adapter emits,
plus one case that reuses the real accepted round-trip evidence when it is
present locally (skipped otherwise, so the suite stays self-contained).

Positive and negative controls are paired. Every guard that protects the source,
the evidence digest or the source files is paired with a case where it is
violated. No test launches an app-server, calls a model or sends a session
message; no test writes to the registry or to the evidence file.
"""
import hashlib
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from src.app_api import ApiError, AppInterface  # noqa: E402
from src.storage import TaskStorage  # noqa: E402

REAL_EVIDENCE_REGISTRY = ROOT / "data" / "jh020-r2-final-roundtrip-20261018" / "runs.jsonl"

RUN_ID = "codex-run-1-abc12345"
THREAD = "01a1aaaa-1111-7000-8000-000000000001"
TURN = "01a1bbbb-2222-7000-8000-000000000002"
FRAMES = ["{\"method\": \"item/completed\"}", "{\"method\": \"turn/completed\"}"]


def snapshot(**over):
    data = {"schema": "jh018/submit/v1", "app_kind": "codex",
            "instance_id": "codex-app-server/local", "session_id": "new",
            "model_id": "gpt-6.1-sol", "role": "worker", "task_id": "task_run_history",
            "attempt": 1, "workspace": "D:/ws", "allowed_scope": [],
            "budget_tokens": None, "instructions": "reply with the token",
            "artifacts": []}
    data.update(over)
    return json.dumps(data, ensure_ascii=False)


def record(event="reserved", **over):
    base = {"event": event, "run_id": RUN_ID, "app_kind": "codex",
            "instance_id": "codex-app-server/local", "session_id": "new",
            "task_id": "task_run_history", "attempt": 1,
            "request_fingerprint": "a" * 64, "request_snapshot": snapshot(),
            "idempotency_key": "key-1", "native_session_id": None,
            "native_turns": [], "native_model": None, "state": "queued",
            "native_state": "reserved", "at": "2026-10-18T00:00:00Z"}
    base.update(over)
    return base


def write_evidence(path: Path):
    payload = "".join(line + "\n" for line in FRAMES).encode("utf-8")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(payload)
    return {"path": str(path), "sha256": hashlib.sha256(payload).hexdigest(),
            "frames": len(FRAMES), "bytes": len(payload)}


def write_registry(path: Path, records):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("".join(json.dumps(r, ensure_ascii=False) + "\n" for r in records),
                    encoding="utf-8")
    return path


def healthy_records(evidence=None):
    return [
        record("reserved"),
        record("dispatched", native_session_id=THREAD, native_turns=[TURN],
               native_model="gpt-6.1-sol", state="running", native_state="turn-started",
               thread_id=THREAD, turn_id=TURN),
        record("collected", native_session_id=THREAD, native_turns=[TURN],
               native_model="gpt-6.1-sol", state="declared_complete", native_state="completed",
               declared_status="completed", artifacts=[], raw_refs=[f"{THREAD}/{TURN}"],
               unverified=["adapter observation only; review decides success"],
               usage={"turn": {"totalTokens": 120}, "thread_total": {"totalTokens": 900},
                      "model_context_window": 475000, "source": {"thread_id": THREAD, "turn_id": TURN}},
               raw=evidence),
    ]


class _ApiCase(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.tmp = Path(self._tmp.name)
        self.runs = self.tmp / "runs.jsonl"
        self.evidence = self.tmp / "raw" / "run.notifications.jsonl"
        self.addCleanup(self._tmp.cleanup)

    def api(self, runs_path=None, db_path=None):
        return AppInterface(db_path=db_path or (self.tmp / "absent.sqlite3"),
                            drafts_path=self.tmp / "drafts.json",
                            runs_path=runs_path or self.runs)


class TestRunHistoryStates(_ApiCase):
    def test_ok_state_lists_runs_with_the_display_fields(self):
        write_registry(self.runs, healthy_records(write_evidence(self.evidence)))
        payload = self.api().list_agent_runs()
        self.assertTrue(payload["ok"])
        self.assertTrue(payload["read_only"])
        self.assertEqual(payload["state"], "ok")
        self.assertEqual(payload["count"], 1)
        self.assertFalse(payload["live"])
        run = payload["runs"][0]
        self.assertEqual(run["run_id"], RUN_ID)
        self.assertEqual(run["app_kind"], "codex")
        self.assertEqual(run["instance_id"], "codex-app-server/local")
        self.assertEqual(run["role"], "worker")
        self.assertEqual(run["requested_model"], "gpt-6.1-sol")
        self.assertEqual(run["native_model"], "gpt-6.1-sol")
        self.assertEqual(run["native_session_id"], THREAD)
        self.assertEqual(run["native_turns"], [TURN])
        self.assertEqual(run["record_state"], "declared_complete")
        self.assertEqual(run["native_state"], "completed")
        self.assertEqual(run["last_recorded_at"], "2026-10-18T00:00:00Z")
        self.assertEqual(run["usage"]["turn"]["totalTokens"], 120)
        self.assertEqual(run["usage"]["thread_total"]["totalTokens"], 900)
        self.assertEqual(run["evidence"]["state"], "verified")
        self.assertTrue(run["unverified"])
        self.assertEqual(run["events"], ["reserved", "dispatched", "collected"])
        self.assertFalse(payload["source"]["browser_supplied"])

    def test_absent_registry_is_its_own_state(self):
        payload = self.api().list_agent_runs()
        self.assertEqual(payload["state"], "absent")
        self.assertEqual(payload["runs"], [])
        self.assertIn("不存在", payload["message"])

    def test_empty_registry_is_its_own_state(self):
        write_registry(self.runs, [])
        payload = self.api().list_agent_runs()
        self.assertEqual(payload["state"], "empty")
        self.assertEqual(payload["runs"], [])

    def test_corrupt_registry_is_reported_and_left_untouched(self):
        write_registry(self.runs, healthy_records())
        # A parseable record whose run_id is null must poison the whole history.
        damaged = [dict(r) for r in healthy_records()]
        damaged[0]["run_id"] = None
        write_registry(self.runs, damaged)
        before = self.runs.read_bytes()
        payload = self.api().list_agent_runs()
        self.assertEqual(payload["state"], "corrupt")
        self.assertEqual(payload["runs"], [])
        self.assertEqual(payload["error"]["code"], "runs_corrupt")
        self.assertEqual(self.runs.read_bytes(), before)          # not repaired

    def test_unreadable_registry_is_its_own_state(self):
        directory = self.tmp / "a-directory.jsonl"
        directory.mkdir()
        payload = self.api(runs_path=directory).list_agent_runs()
        self.assertEqual(payload["state"], "unreadable")
        self.assertEqual(payload["error"]["code"], "runs_unreadable")

    def test_run_detail_adds_the_task_link_and_raw_records(self):
        write_registry(self.runs, healthy_records(write_evidence(self.evidence)))
        detail = self.api().agent_run_detail(RUN_ID)
        self.assertEqual(detail["run"]["run_id"], RUN_ID)
        self.assertFalse(detail["run"]["link"]["linked"])
        self.assertIn("未创建任务", detail["run"]["link"]["note"])
        self.assertEqual([r["event"] for r in detail["records"]],
                         ["reserved", "dispatched", "collected"])

    def test_unknown_run_id_is_a_404_and_never_a_path(self):
        write_registry(self.runs, healthy_records(write_evidence(self.evidence)))
        api = self.api()
        for bad in ("../../etc/passwd", "..\\windows\\win.ini", str(self.runs), "no-such-run"):
            with self.assertRaises(ApiError) as ctx:
                api.agent_run_detail(bad)
            self.assertEqual(ctx.exception.code, "run_not_found")


class TestEvidenceClassification(_ApiCase):
    def _run_with(self, raw):
        write_registry(self.runs, healthy_records(raw))
        return self.api().list_agent_runs()["runs"][0]["evidence"]

    def test_verified_digest(self):
        state = self._run_with(write_evidence(self.evidence))
        self.assertEqual(state["state"], "verified")
        self.assertEqual(state["frames"], len(FRAMES))

    def test_missing_reference(self):
        self.assertEqual(self._run_with(None)["state"], "missing")

    def test_mismatch_on_altered_bytes(self):
        raw = write_evidence(self.evidence)
        self.evidence.write_bytes(self.evidence.read_bytes() + b"{}\n")
        self.assertEqual(self._run_with(raw)["state"], "mismatch")

    def test_mismatch_on_frame_count(self):
        raw = write_evidence(self.evidence)
        self.assertEqual(self._run_with({**raw, "frames": raw["frames"] + 1})["state"], "mismatch")

    def test_unverifiable_when_unreadable(self):
        raw = write_evidence(self.evidence)
        os.remove(self.evidence)
        self.assertEqual(self._run_with(raw)["state"], "unverifiable")


class TestNoSideEffects(_ApiCase):
    def test_query_never_spawns_a_process(self):
        write_registry(self.runs, healthy_records(write_evidence(self.evidence)))
        api = self.api()
        with mock.patch.object(subprocess, "Popen", side_effect=AssertionError("must not spawn")):
            payload = api.list_agent_runs()
            detail = api.agent_run_detail(RUN_ID)
        self.assertEqual(payload["state"], "ok")
        self.assertEqual(detail["run"]["run_id"], RUN_ID)

    def test_query_never_rewrites_the_registry_or_the_evidence(self):
        write_registry(self.runs, healthy_records(write_evidence(self.evidence)))
        before_runs = self.runs.read_bytes()
        before_evidence = self.evidence.read_bytes()
        api = self.api()
        api.list_agent_runs()
        api.agent_run_detail(RUN_ID)
        self.assertEqual(self.runs.read_bytes(), before_runs)
        self.assertEqual(self.evidence.read_bytes(), before_evidence)

    def test_declared_complete_is_not_reported_as_success(self):
        write_registry(self.runs, healthy_records(write_evidence(self.evidence)))
        payload = self.api().list_agent_runs()
        text = json.dumps(payload, ensure_ascii=False)
        self.assertNotIn("succeeded", text)
        self.assertFalse(payload["live"])
        self.assertIn("不代表任务验收成功", payload["note"])
        run = payload["runs"][0]
        self.assertEqual(run["record_state"], "declared_complete")
        self.assertNotIn("success", run)

    def test_unlinked_run_does_not_create_a_task_record(self):
        db = self.tmp / "tasks.sqlite3"
        storage = TaskStorage(db, read_only=False)
        storage.close()
        write_registry(self.runs, healthy_records(write_evidence(self.evidence)))
        api = self.api(db_path=db)
        detail = api.agent_run_detail(RUN_ID)
        self.assertTrue(detail["run"]["link"]["checked"])
        self.assertFalse(detail["run"]["link"]["linked"])
        probe = TaskStorage(db, read_only=True)
        try:
            self.assertEqual(probe.list_tasks(limit=100), [])
        finally:
            probe.close()


@unittest.skipUnless(REAL_EVIDENCE_REGISTRY.is_file(),
                     "accepted round-trip evidence not present locally")
class TestRealEvidenceDisplay(unittest.TestCase):
    """Display verification against the real accepted round-trip registry."""

    def test_real_registry_lists_and_verifies(self):
        api = AppInterface(runs_path=REAL_EVIDENCE_REGISTRY,
                           db_path=ROOT / "data" / "does-not-exist.sqlite3",
                           drafts_path=ROOT / "data" / "gui" / "goal-drafts.json")
        payload = api.list_agent_runs()
        self.assertEqual(payload["state"], "ok")
        run = payload["runs"][0]
        self.assertEqual(run["app_kind"], "codex")
        self.assertEqual(run["native_model"], "gpt-6.1-sol")
        self.assertTrue(run["native_session_id"])
        self.assertTrue(run["native_turns"])
        self.assertEqual(run["evidence"]["state"], "verified")
        detail = api.agent_run_detail(run["run_id"])
        self.assertEqual(detail["run"]["evidence"]["state"], "verified")
        self.assertFalse(detail["run"]["link"]["linked"])


if __name__ == "__main__":
    unittest.main()


class TestEvidenceBoundary(_ApiCase):
    """R1: evidence is readable only under server-owned roots, and is refused
    BEFORE any byte is read. Every refusal here is also a zero-read assertion."""

    def _registry_with_raw(self, raw):
        write_registry(self.runs, healthy_records(raw))
        return self.api()

    def _reads(self, api, run_id=None):
        seen = []
        real = Path.read_bytes
        def observed(path):
            seen.append(str(path))
            return real(path)
        with mock.patch.object(Path, "read_bytes", observed):
            if run_id:
                api.agent_run_detail(run_id)
            else:
                api.list_agent_runs()
        return seen

    def test_inside_root_verifies_and_reads_once(self):
        evidence = write_evidence(self.evidence)          # <registry>/raw/run...
        api = self._registry_with_raw(evidence)
        reads = self._reads(api)
        self.assertEqual(len(reads), 1)
        self.assertEqual(api.list_agent_runs()["runs"][0]["evidence"]["state"], "verified")

    def test_outside_absolute_is_refused_before_read(self):
        outside = self.tmp / "outside" / "x.jsonl"
        raw = write_evidence(outside)
        api = self._registry_with_raw(raw)
        self.assertEqual(self._reads(api), [])
        self.assertEqual(api.list_agent_runs()["runs"][0]["evidence"]["state"], "outside_root")

    def test_relative_reference_resolves_against_registry_dir_not_cwd(self):
        # A relative reference inside the registry's raw/ directory is legal...
        write_evidence(self.evidence)
        raw = {"path": "raw/run.notifications.jsonl",
               "sha256": hashlib.sha256(self.evidence.read_bytes()).hexdigest(),
               "frames": len(FRAMES), "bytes": self.evidence.stat().st_size}
        api = self._registry_with_raw(raw)
        self.assertEqual(api.list_agent_runs()["runs"][0]["evidence"]["state"], "verified")
        # ...and one that climbs out is not.
        escaped = {"path": "../outside/x.jsonl", **{k: raw[k] for k in ("sha256", "frames", "bytes")}}
        self.assertEqual(self._registry_with_raw(escaped).list_agent_runs()["runs"][0]["evidence"]["state"],
                         "outside_root")

    def test_same_prefix_sibling_is_refused(self):
        sibling = self.tmp / "run-history-other" / "x.jsonl"
        api = self._registry_with_raw(write_evidence(sibling))
        self.assertEqual(self._reads(api), [])
        self.assertEqual(api.list_agent_runs()["runs"][0]["evidence"]["state"], "outside_root")

    def test_special_path_forms_are_never_touched(self):
        # Raw strings: these are the exact Win32 forms, not escaped text.
        cases = {"unc": r"\\server\share\evidence.jsonl",
                 "device": r"\\?\C:\Windows\win.ini",
                 "ads": r"C:\evidence.jsonl:stream"}
        for name, path in cases.items():
            raw = {"path": path, "sha256": "a" * 64, "frames": 1, "bytes": 1}
            api = self._registry_with_raw(raw)
            with mock.patch.object(os.path, "realpath", side_effect=AssertionError("must not resolve")):
                state = api.list_agent_runs()["runs"][0]["evidence"]
            self.assertEqual(state["state"], "outside_root", name)
            self.assertIn("refused before access", state["detail"], name)

    def test_invalid_utf8_inside_root_is_structured(self):
        self.evidence.parent.mkdir(parents=True, exist_ok=True)
        payload = b"\xff\n"
        self.evidence.write_bytes(payload)
        raw = {"path": str(self.evidence), "sha256": hashlib.sha256(payload).hexdigest(),
               "frames": 1, "bytes": len(payload)}
        state = self._registry_with_raw(raw).list_agent_runs()["runs"][0]["evidence"]
        self.assertEqual(state["state"], "invalid_content")
        self.assertIn("UTF-8", state["detail"])

    def test_malformed_references_are_invalid_without_exception(self):
        for raw in ("not-an-object", 7, ["x"], {"path": 7}, {"path": "bad\x00path"},
                    {"path": str(self.evidence), "sha256": "zz", "frames": 1},
                    {"path": str(self.evidence), "sha256": "a" * 64, "frames": "1"}):
            state = self._registry_with_raw(raw).list_agent_runs()["runs"][0]["evidence"]
            self.assertEqual(state["state"], "invalid", raw)

    def test_explicitly_configured_extra_root_is_allowed(self):
        extra = self.tmp / "extra-root"
        raw = write_evidence(extra / "x.jsonl")
        write_registry(self.runs, healthy_records(raw))
        api = AppInterface(runs_path=self.runs, evidence_roots=[extra],
                           db_path=self.tmp / "absent.sqlite3",
                           drafts_path=self.tmp / "drafts.json")
        self.assertEqual(api.list_agent_runs()["runs"][0]["evidence"]["state"], "verified")

    def test_junction_under_the_root_is_refused_before_read(self):
        outside_dir = self.tmp / "outside-dir"
        outside_dir.mkdir(parents=True, exist_ok=True)
        payload = b'{"x":1}\n'
        (outside_dir / "x.jsonl").write_bytes(payload)
        raw_dir = self.runs.parent / "raw"
        raw_dir.mkdir(parents=True, exist_ok=True)
        junction = raw_dir / "linked"
        # cmd.exe writes in the console codepage; read bytes so a localized
        # message can never fail the test with a decode error.
        made = subprocess.run(["cmd", "/c", "mklink", "/J", str(junction), str(outside_dir)],
                              capture_output=True)
        if not junction.exists():
            self.skipTest(f"junction creation refused: {made.returncode}")
        raw = {"path": str(junction / "x.jsonl"),
               "sha256": hashlib.sha256(payload).hexdigest(), "frames": 1, "bytes": len(payload)}
        api = self._registry_with_raw(raw)
        self.assertEqual(self._reads(api), [])
        state = api.list_agent_runs()["runs"][0]["evidence"]
        self.assertEqual(state["state"], "outside_root")
        self.assertIn("reparse", state["detail"])

    def test_files_are_byte_identical_after_a_refused_and_an_allowed_query(self):
        outside = write_evidence(self.tmp / "outside" / "x.jsonl")
        write_registry(self.runs, healthy_records(outside))
        before_registry, before_evidence = self.runs.read_bytes(), Path(outside["path"]).read_bytes()
        api = self.api()
        api.list_agent_runs()
        api.agent_run_detail(RUN_ID)
        self.assertEqual(self.runs.read_bytes(), before_registry)
        self.assertEqual(Path(outside["path"]).read_bytes(), before_evidence)

    def test_legacy_wrapper_keeps_the_two_tuple_contract(self):
        from src.agent_session_codex import verify_raw_evidence
        evidence = write_evidence(self.evidence)
        ok, detail = verify_raw_evidence(evidence, allowed_roots=[self.evidence.parent])
        self.assertIs(ok, True)
        self.assertIsInstance(detail, str)
        ok, _ = verify_raw_evidence(evidence, allowed_roots=[self.tmp / "somewhere-else"])
        self.assertIs(ok, False)
