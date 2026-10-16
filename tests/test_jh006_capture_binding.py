"""Offline binding between control deliveries and captured host records.

Every session log below is SYNTHETIC test data (thread ids prefixed
"test-bind-"); nothing here proves a real host executed anything. Tests
assert persisted database rows, event counts, restart re-reads, and
unchanged state after refusals. Production complete stays closed.
"""
import hashlib
import importlib.util
import inspect
import json
import sys
import unittest
import uuid
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from test_jh006_code_delivery import (
    _Case, _baseline_bytes, _check_bytes, _reference_bytes, _sha, TPL)
from test_jh006_host_capture import (
    _desktop_call, _desktop_call_output, _desktop_exec, _desktop_meta,
    _desktop_segment, CALL_SCRIPT, FIXED_ARGV, OUTPUT)
import src.capture_binding as cb
from src.capture_binding import (
    BindingRefused,
    get_capture_binding,
    get_capture_binding_by_call,
    register_capture_binding,
    sealed_check_expectation,
    verify_binding_current,
)
from src.host_capture import (
    build_capture_manifest,
    parse_jsonl,
    select_desktop_execution,
    select_desktop_tool_call,
    verify_desktop_binding,
)
from src.storage import TaskStorage

TARGET = TPL["authorized_target"]
THREAD = "test-bind-thread-0001"
TURN = "test-bind-turn-0001"
ITEM = "test-bind-exec-0001"
CALL = "test-bind-call-0001"


def _cwd_uri(ws):
    return "file:///" + Path(ws).as_posix().lstrip("/")


class _BindCase(_Case):
    def _orch(self, **kwargs):
        # The shared fixture returns (orchestrator, worker, reviewer).
        return super()._orch(**kwargs)[0]

    def _prepared_production(self, orch=None, use_file_db=False):
        orch = orch or self._orch(use_file_db=use_file_db)
        payload = self._payload()
        tid = payload["task_id"]
        self._assemble_reviewing(orch, payload, stamp_source="production",
                                 with_source_event=False)
        task = orch.prepare_code_delivery(tid, 1)
        return orch, tid, task["metadata"]["_delivery"]

    def _plan_for(self, tid, delivery):
        # Synthetic construction, not a trusted source. Real registration
        # independently reconstructs and validates the control snapshot.
        snapshot = {"task_id": tid, "workspace_root": str(self.ws),
            "authorized_target": TARGET, "delivery": delivery,
            "artifact": {**delivery["artifact"],
                         "generation_attempt_id": delivery["generation_attempt_id"]},
            "review": {"attempt_id": delivery["review_attempt_id"],
                       "review_id": delivery["review_id"], "sha256": delivery["review_sha256"]},
            "check": cb.sealed_check_expectation()}
        return cb._invocation_for_snapshot(snapshot)

    def _live_store(self):
        store = self.case / ("live-store-" + uuid.uuid4().hex[:8])
        store.mkdir(parents=True)
        return store

    def _capture_lines(self, *, thread=THREAD, turn=TURN, item=ITEM, call=CALL,
                       argv=None, workdir=None, exit_code=0, output=OUTPUT,
                       call_script=CALL_SCRIPT):
        ws = str(self.ws) if workdir is None else workdir
        return [_desktop_meta(thread=thread),
                _desktop_call(call=call, turn=turn, script=call_script),
                _desktop_exec(item=item, thread=thread, turn=turn,
                              argv=list(FIXED_ARGV) if argv is None else argv,
                              cwd_uri=_cwd_uri(ws), exit_code=exit_code,
                              output=output),
                _desktop_call_output(call=call, turn=turn)]

    def _archive_for(self, tid, delivery, *, live_store=None, claim_over=None,
                     event_over=None, workdir=None, argv=None,
                     exit_code=0, output=OUTPUT, call_script=None,
                     thread=THREAD, item=ITEM, call=CALL, turn=TURN):
        plan = self._plan_for(tid, delivery)
        argv = plan["argv"] if argv is None else argv
        call_script = plan["call_input"] if call_script is None else call_script
        lines = self._capture_lines(thread=thread, turn=turn, item=item, call=call,
                                    argv=argv, workdir=workdir, exit_code=exit_code,
                                    output=output, call_script=call_script)
        events_raw = _desktop_segment(*lines)
        records = parse_jsonl(events_raw, source_label="synth-build")
        selected = select_desktop_execution(records, thread_id=thread, item_id=item)
        call_rec = select_desktop_tool_call(records, call_id=call, turn_id=turn)
        ws = str(self.ws) if workdir is None else workdir
        bindings = verify_desktop_binding(
            selected, call_rec, expected_argv=list(FIXED_ARGV) if argv is None else argv,
            expected_workdir=ws, expected_exit=exit_code,
            expected_marker="PASS" if "PASS" in output else None,
            expected_call_input=call_script)
        art = delivery["artifact"]
        manifest = build_capture_manifest(
            task_id=tid, delivery_id=delivery["delivery_id"],
            artifact_claim={"version": art["version"], "artifact_id": art["artifact_id"],
                            "sha256": art["sha256"]},
            review_claim={"review_id": delivery["review_id"]},
            apply_attempt_id=plan["apply_attempt_id"], protocol="desktop",
            source_files=[{"path": "synth.jsonl", "sha256": _sha(events_raw),
                           "bytes": len(events_raw)}],
            bindings=bindings)
        if claim_over:
            for key, val in claim_over.items():
                if isinstance(val, dict) and isinstance(manifest.get(key), dict):
                    manifest[key] = {**manifest[key], **val}
                else:
                    manifest[key] = val
        if event_over:
            for key, val in event_over.items():
                manifest["event"][key] = val
        manifest["archive_digest"] = _sha(events_raw)
        archive = self.case / ("archive-" + uuid.uuid4().hex[:8])
        archive.mkdir(parents=True)
        (archive / "events.jsonl").write_bytes(events_raw)
        manifest_raw = json.dumps(manifest, ensure_ascii=False, indent=2).encode()
        (archive / "manifest.json").write_bytes(manifest_raw)
        if live_store is not None:
            live_name = f"rollout-synth-{thread}-{item}-{uuid.uuid4().hex[:8]}-live.jsonl"
            (live_store / live_name).write_bytes(events_raw)
        return archive, manifest, events_raw, bindings

    def _register(self, orch, tid, delivery, archive, **over):
        plan = self._plan_for(tid, delivery)
        params = {"delivery_id": delivery["delivery_id"], "archive_dir": archive,
                  "thread_id": THREAD, "item_id": ITEM, "call_id": CALL,
                  "expected_argv": plan["argv"], "expected_call_input": plan["call_input"]}
        params.update(over)
        return register_capture_binding(orch.storage, tid, **params)

    def _assert_still_waiting(self, orch, tid, delivery):
        task = orch.storage.get_task(tid)
        self.assertEqual(task["status"], "reviewing")
        self.assertEqual(task["metadata"]["_delivery"], delivery)
        self.assertIsNone(get_capture_binding(orch.storage, tid, delivery["delivery_id"]))

    def _snapshot_state(self, orch, tid):
        task = orch.storage.get_task(tid)
        return {
            "task": task,
            "runtime": dict(task["_runtime"]),
            "events": orch.storage.list_events(tid),
            "attempts": orch.storage.list_model_attempts(tid),
            "deadline": orch.storage.get_control(tid)["deadline"],
            "bindings": orch.storage.list_review_bindings(tid),
            "target": (self.ws / TARGET).read_bytes(),
        }

    def _assert_state_kept(self, orch, tid, before):
        task = orch.storage.get_task(tid)
        self.assertEqual(task, before["task"])
        self.assertEqual(task["_runtime"], before["runtime"])
        self.assertEqual(orch.storage.list_events(tid), before["events"])
        self.assertEqual(orch.storage.list_model_attempts(tid), before["attempts"])
        self.assertEqual(orch.storage.get_control(tid)["deadline"], before["deadline"])
        self.assertEqual(orch.storage.list_review_bindings(tid), before["bindings"])
        self.assertEqual((self.ws / TARGET).read_bytes(), before["target"])


class TestCaptureBindingRegister(_BindCase):
    def test_register_persists_binding_and_event(self):
        orch, tid, delivery = self._prepared_production()
        self._fixture_apply()
        store = self._live_store()
        archive, manifest, events_raw, _ = self._archive_for(
            tid, delivery, live_store=store)
        before_events = len(orch.storage.list_events(tid))
        with patch.object(cb, "DESKTOP_SESSION_ROOT", store):
            row = self._register(orch, tid, delivery, archive)
        self.assertEqual(row["task_id"], tid)
        self.assertEqual(row["delivery_id"], delivery["delivery_id"])
        self.assertEqual((row["thread_id"], row["item_id"], row["call_id"]),
                         (THREAD, ITEM, CALL))
        self.assertEqual(row["protocol"], "desktop")
        self.assertEqual(row["event_digest"], _sha(events_raw))
        self.assertEqual(row["manifest_digest"],
                         _sha((archive / "manifest.json").read_bytes()))
        self.assertEqual(row["apply_attempt_id"], self._plan_for(tid, delivery)["apply_attempt_id"])
        snapshot = json.loads(row["snapshot_json"])
        self.assertEqual(snapshot["artifact"]["sha256"], delivery["artifact"]["sha256"])
        self.assertEqual(snapshot["review"]["review_id"], delivery["review_id"])
        self.assertEqual(snapshot["observed_target_sha256"], _sha(_reference_bytes()))
        capture = json.loads(row["capture_json"])
        self.assertEqual(capture["argv"], self._plan_for(tid, delivery)["argv"])
        self.assertTrue(row["binding_id"].startswith("cb_"))
        self.assertEqual(json.loads(row["review_items_json"]), [])
        stored = get_capture_binding(orch.storage, tid, delivery["delivery_id"])
        self.assertEqual(stored, row)
        by_call = get_capture_binding_by_call(orch.storage, THREAD, ITEM, CALL)
        self.assertEqual(by_call, row)
        events = orch.storage.list_events(tid)
        self.assertEqual(len(events), before_events + 1)
        self.assertEqual(events[-1]["event_type"], "capture_binding_registered")
        self.assertEqual(events[-1]["details"]["binding_id"], row["binding_id"])
        task = orch.storage.get_task(tid)
        self.assertEqual(task["status"], "reviewing")
        self.assertEqual(task["metadata"]["_delivery"], delivery)
        self.assertEqual(task["_runtime"]["turns_used"], 1)

    def test_repeat_registration_is_idempotent(self):
        orch, tid, delivery = self._prepared_production()
        self._fixture_apply()
        store = self._live_store()
        archive, _, _, _ = self._archive_for(tid, delivery, live_store=store)
        with patch.object(cb, "DESKTOP_SESSION_ROOT", store):
            first = self._register(orch, tid, delivery, archive)
            events_before = len(orch.storage.list_events(tid))
            second = self._register(orch, tid, delivery, archive)
        self.assertEqual(second, first)
        self.assertEqual(len(orch.storage.list_events(tid)), events_before)

    def test_unknown_task_raises_key_error(self):
        orch = self._orch()
        with self.assertRaises(KeyError):
            register_capture_binding(
                orch.storage, "task_missing0001", delivery_id="dlv_x",
                archive_dir=self.case, thread_id=THREAD, item_id=ITEM,
                call_id=CALL, expected_argv=list(FIXED_ARGV),
                expected_call_input=CALL_SCRIPT)

    def test_non_waiting_task_refused(self):
        orch = self._orch()
        payload = self._payload()
        orch.submit_task(payload)
        tid = payload["task_id"]
        with self.assertRaisesRegex(BindingRefused, "status-mismatch"):
            register_capture_binding(
                orch.storage, tid, delivery_id="dlv_x", archive_dir=self.case,
                thread_id=THREAD, item_id=ITEM, call_id=CALL,
                expected_argv=list(FIXED_ARGV), expected_call_input=CALL_SCRIPT)

    def test_reviewing_without_delivery_refused(self):
        orch = self._orch()
        payload = self._payload()
        self._assemble_reviewing(orch, payload, stamp_source="production",
                                 with_source_event=False)
        with self.assertRaisesRegex(BindingRefused, "no-waiting-delivery"):
            register_capture_binding(
                orch.storage, payload["task_id"], delivery_id="dlv_x",
                archive_dir=self.case, thread_id=THREAD, item_id=ITEM,
                call_id=CALL, expected_argv=list(FIXED_ARGV),
                expected_call_input=CALL_SCRIPT)

    def test_wrong_delivery_id_refused(self):
        orch, tid, delivery = self._prepared_production()
        self._fixture_apply()
        store = self._live_store()
        archive, _, _, _ = self._archive_for(tid, delivery, live_store=store,
                                             claim_over={"delivery_claim": "dlv_other"})
        before = self._snapshot_state(orch, tid)
        with patch.object(cb, "DESKTOP_SESSION_ROOT", store):
            with self.assertRaisesRegex(BindingRefused, "delivery-mismatch"):
                self._register(orch, tid, delivery, archive,
                               delivery_id="dlv_other")
        self._assert_state_kept(orch, tid, before)
        self._assert_still_waiting(orch, tid, delivery)

    def test_fixture_task_refused(self):
        orch = self._orch()
        payload = self._payload()
        tid = payload["task_id"]
        self._assemble_reviewing(orch, payload, stamp_source="fixture",
                                 with_source_event=False)
        orch.prepare_code_delivery(tid, 1)
        self._fixture_apply()
        with self.assertRaisesRegex(BindingRefused, "source-not-production"):
            register_capture_binding(
                orch.storage, tid,
                delivery_id=orch.storage.get_task(tid)["metadata"]["_delivery"]["delivery_id"],
                archive_dir=self.case, thread_id=THREAD, item_id=ITEM,
                call_id=CALL, expected_argv=list(FIXED_ARGV),
                expected_call_input=CALL_SCRIPT)


class TestCaptureBindingClaims(_BindCase):
    def _valid(self):
        orch, tid, delivery = self._prepared_production()
        self._fixture_apply()
        store = self._live_store()
        return orch, tid, delivery, store

    def test_manifest_task_claim_mismatch_refused(self):
        orch, tid, delivery, store = self._valid()
        archive, _, _, _ = self._archive_for(
            tid, delivery, live_store=store,
            claim_over={"task_claim": "task_other0001"})
        with patch.object(cb, "DESKTOP_SESSION_ROOT", store):
            with self.assertRaisesRegex(BindingRefused, "claim-identity-mismatch"):
                self._register(orch, tid, delivery, archive)
        self._assert_still_waiting(orch, tid, delivery)

    def test_manifest_delivery_claim_mismatch_refused(self):
        orch, tid, delivery, store = self._valid()
        archive, _, _, _ = self._archive_for(
            tid, delivery, live_store=store,
            claim_over={"delivery_claim": "dlv_other"})
        with patch.object(cb, "DESKTOP_SESSION_ROOT", store):
            with self.assertRaisesRegex(BindingRefused, "claim-identity-mismatch"):
                self._register(orch, tid, delivery, archive)
        self._assert_still_waiting(orch, tid, delivery)

    def test_manifest_artifact_claim_mismatch_refused(self):
        orch, tid, delivery, store = self._valid()
        artifact = dict(delivery["artifact"])
        artifact["version"] = 9
        archive, _, _, _ = self._archive_for(
            tid, delivery, live_store=store,
            claim_over={"artifact_claim": artifact})
        with patch.object(cb, "DESKTOP_SESSION_ROOT", store):
            with self.assertRaisesRegex(BindingRefused, "claim-artifact-mismatch"):
                self._register(orch, tid, delivery, archive)
        self._assert_still_waiting(orch, tid, delivery)

    def test_manifest_review_claim_mismatch_refused(self):
        orch, tid, delivery, store = self._valid()
        archive, _, _, _ = self._archive_for(
            tid, delivery, live_store=store,
            claim_over={"review_claim": {"review_id": "rev_other0001"}})
        with patch.object(cb, "DESKTOP_SESSION_ROOT", store):
            with self.assertRaisesRegex(BindingRefused, "claim-review-mismatch"):
                self._register(orch, tid, delivery, archive)
        self._assert_still_waiting(orch, tid, delivery)

    def test_changed_artifact_refused(self):
        orch, tid, delivery, store = self._valid()
        orch.artifact_store.save_version(
            tid,
            envelope={"schema_version": "1.0.0",
                      "artifact_id": "art_changed0001",
                      "task_id": tid, "artifact_type": "patch_text",
                      "content": "changed", "summary": "v2 probe"},
            version=2, generation_attempt_id=None)
        archive, _, _, _ = self._archive_for(tid, delivery, live_store=store)
        with patch.object(cb, "DESKTOP_SESSION_ROOT", store):
            with self.assertRaisesRegex(BindingRefused, "artifact-changed"):
                self._register(orch, tid, delivery, archive)
        self._assert_still_waiting(orch, tid, delivery)

    def test_new_review_attempt_refused(self):
        orch, tid, delivery, store = self._valid()
        aid = orch.storage.begin_attempt(tid, "reviewer", {})
        orch.storage.complete_attempt(tid, aid, {"review_id": "rev_newer0001"}, 10)
        archive, _, _, _ = self._archive_for(tid, delivery, live_store=store)
        with patch.object(cb, "DESKTOP_SESSION_ROOT", store):
            with self.assertRaisesRegex(BindingRefused, "review-stale"):
                self._register(orch, tid, delivery, archive)
        self._assert_still_waiting(orch, tid, delivery)

    def test_malformed_delivery_refused(self):
        orch, tid, delivery, store = self._valid()
        broken = dict(delivery)
        del broken["artifact"]
        orch.storage.save_code_delivery(tid, broken)
        archive, _, _, _ = self._archive_for(tid, delivery, live_store=store)
        with patch.object(cb, "DESKTOP_SESSION_ROOT", store):
            with self.assertRaisesRegex(BindingRefused, "delivery-malformed"):
                self._register(orch, tid, delivery, archive)


class TestCaptureBindingTruth(_BindCase):
    def _valid(self):
        orch, tid, delivery = self._prepared_production()
        self._fixture_apply()
        store = self._live_store()
        return orch, tid, delivery, store

    def test_echo_argv_refused(self):
        orch, tid, delivery, store = self._valid()
        echo = ["C:/synth/pwsh.exe", "-Command",
                'Write-Output "PASS check_widget.py target/widget.py"']
        archive, _, _, _ = self._archive_for(tid, delivery, live_store=store,
                                             argv=echo)
        with patch.object(cb, "DESKTOP_SESSION_ROOT", store):
            with self.assertRaisesRegex(BindingRefused, "argv-mismatch"):
                self._register(orch, tid, delivery, archive)
        self._assert_still_waiting(orch, tid, delivery)

    def test_wrong_workdir_refused(self):
        orch, tid, delivery, store = self._valid()
        archive, _, _, _ = self._archive_for(tid, delivery, live_store=store,
                                             workdir="D:/other-ws")
        with patch.object(cb, "DESKTOP_SESSION_ROOT", store):
            with self.assertRaisesRegex(BindingRefused, "cwd-mismatch"):
                self._register(orch, tid, delivery, archive)
        self._assert_still_waiting(orch, tid, delivery)

    def test_nonzero_exit_refused(self):
        orch, tid, delivery, store = self._valid()
        archive, _, _, _ = self._archive_for(tid, delivery, live_store=store,
                                             exit_code=1)
        with patch.object(cb, "DESKTOP_SESSION_ROOT", store):
            with self.assertRaisesRegex(BindingRefused, "exit-mismatch"):
                self._register(orch, tid, delivery, archive)
        self._assert_still_waiting(orch, tid, delivery)

    def test_markerless_output_refused(self):
        orch, tid, delivery, store = self._valid()
        archive, _, _, _ = self._archive_for(tid, delivery, live_store=store,
                                             output="case 0: FAIL\nFAIL\n")
        with patch.object(cb, "DESKTOP_SESSION_ROOT", store):
            with self.assertRaisesRegex(BindingRefused, "marker-absent"):
                self._register(orch, tid, delivery, archive)
        self._assert_still_waiting(orch, tid, delivery)

    def test_wrong_call_script_refused(self):
        orch, tid, delivery, store = self._valid()
        archive, _, _, _ = self._archive_for(
            tid, delivery, live_store=store, call_script="tools.exec_command({cmd: OTHER})")
        with patch.object(cb, "DESKTOP_SESSION_ROOT", store):
            with self.assertRaisesRegex(BindingRefused, "call-input-mismatch"):
                self._register(orch, tid, delivery, archive)
        self._assert_still_waiting(orch, tid, delivery)

    def test_live_bytes_diverged_refused(self):
        orch, tid, delivery, store = self._valid()
        archive, _, _, _ = self._archive_for(tid, delivery, live_store=store)
        (live_file,) = sorted(store.glob("*.jsonl"))
        tampered = live_file.read_bytes().replace(b"case 0: ok", b"case 0: OK", 1)
        self.assertNotEqual(tampered, live_file.read_bytes())
        live_file.write_bytes(tampered)
        with patch.object(cb, "DESKTOP_SESSION_ROOT", store):
            with self.assertRaisesRegex(BindingRefused, "archive-live-mismatch"):
                self._register(orch, tid, delivery, archive)
        self._assert_still_waiting(orch, tid, delivery)

    def test_missing_live_thread_refused(self):
        orch, tid, delivery, _ = self._valid()
        store = self._live_store()
        archive, _, _, _ = self._archive_for(tid, delivery, live_store=store)
        for path in store.glob("*.jsonl"):
            path.unlink()
        with patch.object(cb, "DESKTOP_SESSION_ROOT", store):
            with self.assertRaisesRegex(BindingRefused, "source-unresolvable"):
                self._register(orch, tid, delivery, archive)
        self._assert_still_waiting(orch, tid, delivery)

    def test_duplicate_live_item_refused(self):
        orch, tid, delivery, store = self._valid()
        archive, _, _, _ = self._archive_for(tid, delivery, live_store=store)
        (only,) = sorted(store.glob("*.jsonl"))
        (store / f"rollout-synth-{THREAD}-second.jsonl").write_bytes(only.read_bytes())
        with patch.object(cb, "DESKTOP_SESSION_ROOT", store):
            with self.assertRaisesRegex(BindingRefused, "source-ambiguous"):
                self._register(orch, tid, delivery, archive)
        self._assert_still_waiting(orch, tid, delivery)


class TestCaptureBindingArchive(_BindCase):
    def _valid(self):
        orch, tid, delivery = self._prepared_production()
        self._fixture_apply()
        store = self._live_store()
        return orch, tid, delivery, store

    def test_tampered_manifest_digest_refused(self):
        orch, tid, delivery, store = self._valid()
        archive, manifest, _, _ = self._archive_for(tid, delivery, live_store=store)
        manifest["event"]["output_digest"] = "0" * 64
        (archive / "manifest.json").write_text(
            json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
        with patch.object(cb, "DESKTOP_SESSION_ROOT", store):
            with self.assertRaisesRegex(BindingRefused, "archive-tampered"):
                self._register(orch, tid, delivery, archive)
        self._assert_still_waiting(orch, tid, delivery)

    def test_tampered_event_bytes_refused(self):
        orch, tid, delivery, store = self._valid()
        archive, _, _, _ = self._archive_for(tid, delivery, live_store=store)
        events_path = archive / "events.jsonl"
        tampered = events_path.read_bytes().replace(b"case 0: ok", b"case 0: OK", 1)
        self.assertNotEqual(tampered, events_path.read_bytes())
        events_path.write_bytes(tampered)
        with patch.object(cb, "DESKTOP_SESSION_ROOT", store):
            with self.assertRaisesRegex(BindingRefused, "archive-tampered"):
                self._register(orch, tid, delivery, archive)
        self._assert_still_waiting(orch, tid, delivery)

    def test_missing_archive_files_refused(self):
        orch, tid, delivery, store = self._valid()
        archive, _, _, _ = self._archive_for(tid, delivery, live_store=store)
        (archive / "events.jsonl").unlink()
        with patch.object(cb, "DESKTOP_SESSION_ROOT", store):
            with self.assertRaisesRegex(BindingRefused, "archive-missing"):
                self._register(orch, tid, delivery, archive)
        self._assert_still_waiting(orch, tid, delivery)

    def test_malformed_manifest_refused(self):
        orch, tid, delivery, store = self._valid()
        archive, _, _, _ = self._archive_for(tid, delivery, live_store=store)
        (archive / "manifest.json").write_text("{broken", encoding="utf-8")
        with patch.object(cb, "DESKTOP_SESSION_ROOT", store):
            with self.assertRaisesRegex(BindingRefused, "manifest-malformed"):
                self._register(orch, tid, delivery, archive)
        self._assert_still_waiting(orch, tid, delivery)

    def test_cli_protocol_archive_refused(self):
        orch, tid, delivery, store = self._valid()
        archive, manifest, _, _ = self._archive_for(tid, delivery, live_store=store)
        manifest["protocol"] = "cli"
        (archive / "manifest.json").write_text(
            json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
        with patch.object(cb, "DESKTOP_SESSION_ROOT", store):
            with self.assertRaisesRegex(BindingRefused, "protocol-not-admitted"):
                self._register(orch, tid, delivery, archive)
        self._assert_still_waiting(orch, tid, delivery)

    def test_request_selection_mismatch_refused(self):
        orch, tid, delivery, store = self._valid()
        archive, _, _, _ = self._archive_for(tid, delivery, live_store=store)
        with patch.object(cb, "DESKTOP_SESSION_ROOT", store):
            with self.assertRaisesRegex(BindingRefused, "claim-identity-mismatch"):
                self._register(orch, tid, delivery, archive, item_id="test-bind-exec-9999")
        self._assert_still_waiting(orch, tid, delivery)

    def test_duplicate_archive_item_refused(self):
        orch, tid, delivery, store = self._valid()
        archive, manifest, events_raw, _ = self._archive_for(tid, delivery, live_store=store)
        lines = events_raw.split(b"\n")
        exec_line = next(line for line in lines if b"test-bind-exec-0001" in line)
        doubled = events_raw + exec_line + b"\n"
        (archive / "events.jsonl").write_bytes(doubled)
        manifest["archive_digest"] = _sha(doubled)
        (archive / "manifest.json").write_text(
            json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
        with patch.object(cb, "DESKTOP_SESSION_ROOT", store):
            with self.assertRaisesRegex(BindingRefused, "duplicate-item"):
                self._register(orch, tid, delivery, archive)
        self._assert_still_waiting(orch, tid, delivery)


class TestCaptureBindingLifecycle(_BindCase):
    def _valid(self):
        orch, tid, delivery = self._prepared_production()
        self._fixture_apply()
        store = self._live_store()
        return orch, tid, delivery, store

    def test_baseline_target_refused_and_state_kept(self):
        orch = self._orch()
        payload = self._payload()
        tid = payload["task_id"]
        self._assemble_reviewing(orch, payload, stamp_source="production",
                                 with_source_event=False)
        task = orch.prepare_code_delivery(tid, 1)
        delivery = task["metadata"]["_delivery"]
        store = self._live_store()
        archive, _, _, _ = self._archive_for(tid, delivery, live_store=store)
        before = self._snapshot_state(orch, tid)
        with patch.object(cb, "DESKTOP_SESSION_ROOT", store):
            with self.assertRaisesRegex(BindingRefused, "target-bytes-mismatch"):
                self._register(orch, tid, delivery, archive)
        self._assert_state_kept(orch, tid, before)
        self._assert_still_waiting(orch, tid, delivery)

    def test_owner_held_refused(self):
        orch, tid, delivery, store = self._valid()
        archive, _, _, _ = self._archive_for(tid, delivery, live_store=store)
        self.assertTrue(orch.storage.claim_task(tid, "other-owner"))
        try:
            with patch.object(cb, "DESKTOP_SESSION_ROOT", store):
                with self.assertRaisesRegex(BindingRefused, "owner-held"):
                    self._register(orch, tid, delivery, archive)
        finally:
            orch.storage.release_task(tid, "other-owner")
        self._assert_still_waiting(orch, tid, delivery)

    def test_same_delivery_conflicting_call_refused(self):
        orch, tid, delivery, store = self._valid()
        first, _, _, _ = self._archive_for(tid, delivery, live_store=store)
        with patch.object(cb, "DESKTOP_SESSION_ROOT", store):
            row = self._register(orch, tid, delivery, first)
        other_item, other_call = "test-bind-exec-0002", "test-bind-call-0002"
        second, _, _, _ = self._archive_for(
            tid, delivery, live_store=store, item=other_item, call=other_call)
        before = self._snapshot_state(orch, tid)
        with patch.object(cb, "DESKTOP_SESSION_ROOT", store):
            with self.assertRaisesRegex(BindingRefused, "binding-conflict"):
                self._register(orch, tid, delivery, second, item_id=other_item,
                               call_id=other_call)
        self._assert_state_kept(orch, tid, before)
        kept = get_capture_binding(orch.storage, tid, delivery["delivery_id"])
        self.assertEqual(kept["binding_id"], row["binding_id"])

    def test_same_call_conflicting_delivery_refused(self):
        orch = self._orch()
        payload_a = self._payload()
        _, delivery_a = self._prepared_on(orch, payload_a)
        payload_b = self._payload()
        _, delivery_b = self._prepared_on(orch, payload_b)
        self._fixture_apply()
        store = self._live_store()
        archive_a, _, _, _ = self._archive_for(
            payload_a["task_id"], delivery_a, live_store=store)
        with patch.object(cb, "DESKTOP_SESSION_ROOT", store):
            self._register(orch, payload_a["task_id"], delivery_a, archive_a)
        archive_b, _, _, _ = self._archive_for(
            payload_b["task_id"], delivery_b, live_store=None)
        with patch.object(cb, "DESKTOP_SESSION_ROOT", store):
            with self.assertRaisesRegex(BindingRefused, "archive-live-mismatch|binding-conflict"):
                self._register(orch, payload_b["task_id"], delivery_b, archive_b)
        self.assertIsNone(get_capture_binding(
            orch.storage, payload_b["task_id"], delivery_b["delivery_id"]))

    def _prepared_on(self, orch, payload):
        tid = payload["task_id"]
        self._assemble_reviewing(orch, payload, stamp_source="production",
                                 with_source_event=False)
        task = orch.prepare_code_delivery(tid, 1)
        return orch, task["metadata"]["_delivery"]


class TestCaptureBindingCurrent(_BindCase):
    def test_binding_survives_restart_and_reads_back(self):
        orch, tid, delivery = self._prepared_production(use_file_db=True)
        self._fixture_apply()
        store = self._live_store()
        archive, _, _, _ = self._archive_for(tid, delivery, live_store=store)
        with patch.object(cb, "DESKTOP_SESSION_ROOT", store):
            row = self._register(orch, tid, delivery, archive)
        orch.storage.close()
        probe = TaskStorage(str(self.db), read_only=True)
        try:
            again = get_capture_binding(probe, tid, delivery["delivery_id"])
            self.assertEqual(again, row)
            by_call = get_capture_binding_by_call(probe, THREAD, ITEM, CALL)
            self.assertEqual(by_call, row)
            verdict = verify_binding_current(probe, tid, delivery["delivery_id"])
            self.assertTrue(verdict["current"])
            self.assertEqual(verdict["reasons"], [])
        finally:
            probe.close()

    def test_cancelled_task_binding_not_current_but_kept(self):
        orch, tid, delivery = self._prepared_production()
        self._fixture_apply()
        store = self._live_store()
        archive, _, _, _ = self._archive_for(tid, delivery, live_store=store)
        with patch.object(cb, "DESKTOP_SESSION_ROOT", store):
            row = self._register(orch, tid, delivery, archive)
        orch.cancel_task(tid, reason="binding staleness probe")
        verdict = verify_binding_current(orch.storage, tid, delivery["delivery_id"])
        self.assertFalse(verdict["current"])
        self.assertIn("task-not-waiting", verdict["reasons"])
        self.assertEqual(
            get_capture_binding(orch.storage, tid, delivery["delivery_id"])["binding_id"],
            row["binding_id"])

    def test_changed_artifact_binding_not_current(self):
        orch, tid, delivery = self._prepared_production()
        self._fixture_apply()
        store = self._live_store()
        archive, _, _, _ = self._archive_for(tid, delivery, live_store=store)
        with patch.object(cb, "DESKTOP_SESSION_ROOT", store):
            self._register(orch, tid, delivery, archive)
        orch.artifact_store.save_version(
            tid,
            envelope={"schema_version": "1.0.0", "artifact_id": "art_changed0002",
                      "task_id": tid, "artifact_type": "patch_text",
                      "content": "changed", "summary": "v2 probe"},
            version=2, generation_attempt_id=None)
        verdict = verify_binding_current(orch.storage, tid, delivery["delivery_id"])
        self.assertFalse(verdict["current"])
        self.assertIn("artifact-changed", verdict["reasons"])

    def test_deleted_archive_binding_not_current(self):
        orch, tid, delivery = self._prepared_production()
        self._fixture_apply()
        store = self._live_store()
        archive, _, _, _ = self._archive_for(tid, delivery, live_store=store)
        with patch.object(cb, "DESKTOP_SESSION_ROOT", store):
            self._register(orch, tid, delivery, archive)
        (archive / "events.jsonl").unlink()
        verdict = verify_binding_current(orch.storage, tid, delivery["delivery_id"])
        self.assertFalse(verdict["current"])
        self.assertIn("archive-missing", verdict["reasons"])

    def test_missing_binding_reports_not_current(self):
        orch = self._orch()
        verdict = verify_binding_current(orch.storage, "task_missing0001", "dlv_missing")
        self.assertFalse(verdict["current"])
        self.assertIn("no-binding", verdict["reasons"])

    def test_pre_feature_database_reads_empty(self):
        orch = self._orch()
        orch.storage._conn.execute("DROP TABLE capture_bindings")
        self.assertIsNone(get_capture_binding(orch.storage, "task_x", "dlv_x"))
        self.assertIsNone(get_capture_binding_by_call(orch.storage, "t", "i", "c"))


class TestCaptureBindingSurface(_BindCase):
    def test_register_signature_has_no_trust_root_params(self):
        params = list(inspect.signature(register_capture_binding).parameters)
        self.assertEqual(params, ["storage", "task_id", "delivery_id", "archive_dir",
                                  "thread_id", "item_id", "call_id", "expected_argv",
                                  "expected_call_input"])

    def test_trust_root_matches_probe_entry(self):
        import src.capture_binding as boundmod
        spec = importlib.util.spec_from_file_location(
            "capture_entry_check", ROOT / "scripts/probe_host_capture.py")
        entry = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(entry)
        self.assertEqual(boundmod.DESKTOP_SESSION_ROOT, entry.SESSION_ROOT)

    def test_sealed_check_oracle(self):
        expectation = sealed_check_expectation()
        self.assertEqual(expectation["expect_exit"], 0)
        self.assertEqual(expectation["marker"], "PASS")
        self.assertEqual(expectation["check_sha256"], _sha(_check_bytes()))


if __name__ == "__main__":
    unittest.main()
