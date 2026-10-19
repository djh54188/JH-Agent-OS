"""Hark-派单-03: cross-record consistency of the run-history query (read-only).

Positive controls come from two places: hand-written records in the writer's
exact format, and registries produced by the REAL writer
(`CodexAgentSessionAdapter`) driven through every submit/collect branch with the
accepted fake transport, so the rules are tied to what `_persist` actually
emits rather than to a guess. Negative controls cover each fault class, not only
the four seed counterexamples.

No test launches an app-server, calls a model or sends a session message, and
no test writes to a registry it then queries through AppInterface.
"""
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tests"))

import src.app_api as app_api  # noqa: E402
from src.app_api import AppInterface  # noqa: E402
from src.run_history_consistency import (  # noqa: E402
    BINDING_FIELDS,
    HistoryInconsistent,
    check_history,
)
import test_jh020_codex_agent_session as writer_fx  # noqa: E402

RUN_A = "codex-run-1-aaaaaaaa"
RUN_B = "codex-run-2-bbbbbbbb"
THREAD = "01a1aaaa-1111-7000-8000-000000000001"
TURN = "01a1bbbb-2222-7000-8000-000000000002"
MODEL = "gpt-6.1-sol"
AT = "2026-10-19T08:00:00Z"
# The mutation verifier turns this off so a mutant only counts as detected when
# the ok/corrupt OUTCOME changes, never because a message's wording changed.
CHECK_MESSAGES = True


def rec(event, run_id=RUN_A, key="key-a", **over):
    base = {"event": event, "run_id": run_id, "app_kind": "codex",
            "instance_id": "codex-app-server/local", "session_id": "new",
            "task_id": "task-" + run_id, "attempt": 1,
            "request_fingerprint": ("a" if run_id == RUN_A else "b") * 64,
            "request_snapshot": json.dumps({"task_id": "task-" + run_id, "role": "worker"}),
            "idempotency_key": key, "native_session_id": None, "native_turns": [],
            "native_model": None, "state": "queued", "native_state": "reserved", "at": AT}
    base.update(over)
    return base


def reserved(run_id=RUN_A, key="key-a", **over):
    return rec("reserved", run_id, key, **over)


def dispatched(run_id=RUN_A, key="key-a", **over):
    fields = dict(native_session_id=THREAD, native_turns=[TURN], native_model=MODEL,
                  state="running", native_state="turn-started", thread_id=THREAD, turn_id=TURN)
    fields.update(over)
    return rec("dispatched", run_id, key, **fields)


def collected(run_id=RUN_A, key="key-a", **over):
    fields = dict(native_session_id=THREAD, native_turns=[TURN], native_model=MODEL,
                  state="declared_complete", native_state="completed", declared_status="completed",
                  artifacts=[], raw_refs=[f"{THREAD}/{TURN}"], unverified=["synthetic"], usage={})
    fields.update(over)
    return rec("collected", run_id, key, **fields)


def collect_timeout(run_id=RUN_A, key="key-a", **over):
    fields = dict(native_session_id=THREAD, native_turns=[TURN], native_model=MODEL,
                  state="unknown", native_state="turn-started", reason="no turn/completed")
    fields.update(over)
    return rec("collect_timeout", run_id, key, **fields)


def failed(stage, native_state, run_id=RUN_A, key="key-a", **over):
    fields = dict(state="failed", native_state=native_state, stage=stage)
    fields.update(over)
    return rec("dispatch_failed", run_id, key, **fields)


def uncertain(stage, run_id=RUN_A, key="key-a", **over):
    fields = dict(state="unknown", native_state="dispatch-uncertain", stage=stage)
    fields.update(over)
    return rec("dispatch_uncertain", run_id, key, **fields)


def write(path: Path, records):
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = "".join(json.dumps(r, ensure_ascii=False) + "\n" for r in records).encode("utf-8")
    path.write_bytes(payload)
    return payload


class _Case(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.tmp = Path(self._tmp.name)
        self.runs = self.tmp / "runs.jsonl"

    def api(self, runs=None):
        return AppInterface(db_path=self.tmp / "absent.sqlite3",
                            drafts_path=self.tmp / "drafts.json", runs_path=runs or self.runs)

    def assert_ok(self, records):
        check_history(records)            # raises on any contradiction
        write(self.runs, records)
        payload = self.api().list_agent_runs()
        self.assertEqual(payload["state"], "ok", payload.get("message"))
        self.assertEqual(payload["count"], len({r["run_id"] for r in records}))

    def assert_corrupt(self, records, needle=None):
        with self.assertRaises(HistoryInconsistent) as ctx:
            check_history(records)
        if needle and CHECK_MESSAGES:
            self.assertIn(needle, str(ctx.exception))
        before = write(self.runs, records)
        payload = self.api().list_agent_runs()
        self.assertEqual(payload["state"], "corrupt")
        self.assertEqual(payload["runs"], [])
        self.assertEqual(payload["count"], 0)
        self.assertEqual(payload["error"]["code"], "runs_corrupt")
        self.assertEqual(self.runs.read_bytes(), before)


# --------------------------------------------------------------------------
# Positive controls produced by the real writer
# --------------------------------------------------------------------------
class _NoThreadId(writer_fx.FakeCodexTransport):
    def call(self, method, params, timeout=60.0):
        if method == "thread/start":
            self.calls.append({"method": method, "params": params})
            return {"id": 2, "result": {"thread": {}, "model": writer_fx.MODEL}}
        return super().call(method, params, timeout)


class _NoTurnId(writer_fx.FakeCodexTransport):
    def call(self, method, params, timeout=60.0):
        if method == "turn/start":
            self.calls.append({"method": method, "params": params})
            return {"id": 3, "result": {"turn": {}}}
        return super().call(method, params, timeout)


class TestRealWriterHistoriesStayOk(_Case):
    """Every branch `_persist` can emit must still read as one consistent run."""

    def drive(self, transport, *, collect=False, key=None, request=None):
        adapter, _ = writer_fx.make_adapter(transport=transport, registry_path=str(self.runs))
        request = request or writer_fx.make_request(idempotency_key=key)
        try:
            status = adapter.submit(request)
        except Exception:
            return adapter, None
        if collect:
            adapter.collect(request.session, status.run_id, timeout=1)
        return adapter, status

    def events(self):
        return [json.loads(line)["event"] for line in self.runs.read_text(encoding="utf-8").splitlines()]

    def assert_registry_ok(self, expected_events):
        self.assertEqual(self.events(), expected_events)
        payload = self.api().list_agent_runs()
        self.assertEqual(payload["state"], "ok", payload.get("message"))

    def test_thread_start_timeout(self):
        self.drive(writer_fx.FakeCodexTransport(timeout_method="thread/start"))
        self.assert_registry_ok(["reserved", "dispatch_uncertain"])

    def test_thread_start_refused(self):
        self.drive(writer_fx.FakeCodexTransport(error_method="thread/start"))
        self.assert_registry_ok(["reserved", "dispatch_failed"])

    def test_thread_start_without_id(self):
        self.drive(_NoThreadId())
        self.assert_registry_ok(["reserved", "dispatch_failed"])

    def test_model_not_honored(self):
        self.drive(writer_fx.FakeCodexTransport(reported_model="some-other-model"))
        self.assert_registry_ok(["reserved", "dispatch_failed"])

    def test_turn_start_timeout(self):
        self.drive(writer_fx.FakeCodexTransport(timeout_method="turn/start"))
        self.assert_registry_ok(["reserved", "dispatch_uncertain"])

    def test_turn_start_refused(self):
        self.drive(writer_fx.FakeCodexTransport(error_method="turn/start"))
        self.assert_registry_ok(["reserved", "dispatch_failed"])

    def test_turn_start_without_id(self):
        self.drive(_NoTurnId())
        self.assert_registry_ok(["reserved", "dispatch_failed"])

    def test_dispatched_unfinished_prefix(self):
        self.drive(writer_fx.FakeCodexTransport())
        self.assert_registry_ok(["reserved", "dispatched"])

    def test_collected_completed(self):
        self.drive(writer_fx.FakeCodexTransport(), collect=True)
        self.assert_registry_ok(["reserved", "dispatched", "collected"])

    def test_collected_failed_status(self):
        self.drive(writer_fx.FakeCodexTransport(
            completion_result=writer_fx.completion(status="failed")), collect=True)
        self.assert_registry_ok(["reserved", "dispatched", "collected"])

    def test_collected_unattributed_completion(self):
        self.drive(writer_fx.FakeCodexTransport(
            completion_result=writer_fx.completion(thread=writer_fx.OTHER_THREAD),
            force_completion=True), collect=True)
        self.assert_registry_ok(["reserved", "dispatched", "collected"])

    def test_collect_timeout(self):
        self.drive(writer_fx.FakeCodexTransport(
            completion_result=writer_fx.completion(turn=writer_fx.OTHER_TURN)), collect=True)
        self.assert_registry_ok(["reserved", "dispatched", "collect_timeout"])

    def test_server_reports_no_model_when_none_requested(self):
        request = writer_fx.make_request(session=writer_fx.address(model=None))
        self.drive(writer_fx.FakeCodexTransport(reported_model=None), collect=True, request=request)
        self.assert_registry_ok(["reserved", "dispatched", "collected"])

    def test_idempotent_replay_writes_nothing_and_stays_ok(self):
        adapter, _ = self.drive(writer_fx.FakeCodexTransport(), key="replay-key")
        adapter.submit(writer_fx.make_request(idempotency_key="replay-key"))
        self.assert_registry_ok(["reserved", "dispatched"])

    def test_reopened_writer_appends_a_second_run(self):
        self.drive(writer_fx.FakeCodexTransport(), collect=True, key="k1")
        self.drive(writer_fx.FakeCodexTransport(error_method="turn/start"),
                   request=writer_fx.make_request(idempotency_key="k2", attempt=2))
        self.drive(writer_fx.FakeCodexTransport(), request=writer_fx.make_request(attempt=3))
        self.drive(writer_fx.FakeCodexTransport(), request=writer_fx.make_request(attempt=4))
        self.assert_registry_ok(["reserved", "dispatched", "collected", "reserved", "dispatch_failed",
                                 "reserved", "dispatched", "reserved", "dispatched"])
        self.assertEqual(self.api().list_agent_runs()["count"], 4)


# --------------------------------------------------------------------------
# 1. binding / request / key never change inside one run
# --------------------------------------------------------------------------
class TestBindingFields(_Case):
    VALUES = {"app_kind": "zcode", "instance_id": "codex-app-server/other",
              "session_id": "new:other", "task_id": "another-task", "attempt": 99,
              "request_snapshot": "{}", "request_fingerprint": "f" * 64,
              "idempotency_key": "key-other"}

    def test_the_table_covers_every_binding_field(self):
        self.assertEqual(set(self.VALUES), set(BINDING_FIELDS))

    def test_every_binding_field_change_is_corrupt_at_every_later_record(self):
        for name, value in self.VALUES.items():
            for position in (1, 2):
                with self.subTest(field=name, position=position):
                    history = [reserved(), dispatched(), collected()]
                    history[position][name] = value
                    self.assert_corrupt(history, f"{name} changed")

    def test_key_change_from_null_to_value_is_corrupt(self):
        self.assert_corrupt([reserved(key=None), dispatched(key="late-key")], "idempotency_key changed")

    def test_identical_binding_is_ok(self):
        self.assert_ok([reserved(), dispatched(), collected()])


# --------------------------------------------------------------------------
# 2. native identifiers: unknown -> known only, never back or sideways
# --------------------------------------------------------------------------
class TestNativeEvolution(_Case):
    def test_unknown_to_known_at_dispatch_is_ok(self):
        self.assert_ok([reserved(), dispatched(), collected()])

    def test_model_unknown_throughout_is_ok(self):
        self.assert_ok([reserved(), dispatched(native_model=None), collected(native_model=None)])

    def test_thread_known_when_turn_start_was_uncertain_is_ok(self):
        self.assert_ok([reserved(), uncertain("turn/start", native_session_id=THREAD, native_model=MODEL)])

    def test_native_session_conflict(self):
        self.assert_corrupt([reserved(), dispatched(), collected(native_session_id="other-thread")],
                            "native_session_id conflicts")

    def test_native_session_regresses_to_null(self):
        self.assert_corrupt([reserved(), dispatched(), collect_timeout(native_session_id=None)],
                            "native_session_id regressed")

    def test_native_model_conflict(self):
        self.assert_corrupt([reserved(), dispatched(), collected(native_model="other-model")],
                            "native_model conflicts")

    def test_native_model_regresses_to_null(self):
        self.assert_corrupt([reserved(), dispatched(), collected(native_model=None)],
                            "native_model regressed")

    def test_native_turn_replaced(self):
        self.assert_corrupt([reserved(), dispatched(), collected(native_turns=["other-turn"])],
                            "native_turns regressed or changed")

    def test_native_turns_dropped(self):
        self.assert_corrupt([reserved(), dispatched(), collect_timeout(native_turns=[])],
                            "native_turns regressed or changed")


# --------------------------------------------------------------------------
# 4. event order per run, in append order
# --------------------------------------------------------------------------
class TestEventOrder(_Case):
    def test_every_legal_sequence_is_ok(self):
        legal = {
            "reserved_only": [reserved()],
            "dispatched_prefix": [reserved(), dispatched()],
            "collected": [reserved(), dispatched(), collected()],
            "collected_failed": [reserved(), dispatched(), collected(state="failed", native_state="failed")],
            "collect_timeout": [reserved(), dispatched(), collect_timeout()],
            "uncertain_thread": [reserved(), uncertain("thread/start")],
            "uncertain_turn": [reserved(), uncertain("turn/start", native_session_id=THREAD)],
            "failed_thread_refused": [reserved(), failed("thread/start", "thread/start-refused")],
            "failed_thread_no_id": [reserved(), failed("thread/start", "thread/start-no-id")],
            "failed_model": [reserved(), failed("thread/start", "model-not-honored",
                                                native_session_id=THREAD)],
            "failed_turn_refused": [reserved(), failed("turn/start", "turn/start-refused",
                                                       native_session_id=THREAD, native_model=MODEL)],
            "failed_turn_no_id": [reserved(), failed("turn/start", "turn/start-no-id",
                                                     native_session_id=THREAD)],
        }
        for name, history in legal.items():
            with self.subTest(sequence=name):
                self.assert_ok(history)

    def test_interleaved_runs_with_identical_timestamps_are_ok(self):
        self.assert_ok([reserved(), reserved(RUN_B, "key-b"), dispatched(RUN_B, "key-b"),
                        dispatched(), collect_timeout(RUN_B, "key-b"), collected()])

    def test_unknown_event(self):
        self.assert_corrupt([reserved(), rec("submitted", state="running")], "unknown event")

    def test_run_must_start_reserved(self):
        for first in (dispatched(), collected(), collect_timeout(),
                      failed("thread/start", "thread/start-refused"), uncertain("thread/start")):
            with self.subTest(first=first["event"]):
                self.assert_corrupt([first], "always reserves first")

    def test_reordered_events(self):
        self.assert_corrupt([collected(), reserved(), dispatched()], "always reserves first")
        self.assert_corrupt([reserved(), collected(), dispatched()], "cannot follow")
        # Shapes and identifiers are individually legal here; only the order is wrong.
        self.assert_corrupt([failed("thread/start", "thread/start-refused"), reserved()],
                            "always reserves first")
        self.assert_corrupt([uncertain("thread/start"), reserved()], "always reserves first")

    def test_duplicates(self):
        self.assert_corrupt([reserved(), reserved()], "cannot follow")
        self.assert_corrupt([reserved(), dispatched(), dispatched()], "cannot follow")

    def test_collection_without_dispatch(self):
        self.assert_corrupt([reserved(), collected()], "cannot follow")
        self.assert_corrupt([reserved(), collect_timeout()], "cannot follow")
        # No native identifiers at all, so only the missing dispatch is wrong.
        bare = dict(native_session_id=None, native_turns=[], native_model=None)
        self.assert_corrupt([reserved(), collect_timeout(native_state=None, **bare)], "cannot follow")
        self.assert_corrupt([reserved(), collected(**bare)], "cannot follow")

    def test_append_after_terminal(self):
        cases = {
            "after_failed": [reserved(), failed("thread/start", "thread/start-refused"), dispatched()],
            "after_uncertain": [reserved(), uncertain("thread/start"), dispatched()],
            "after_timeout": [reserved(), dispatched(), collect_timeout(), collected()],
            "after_collected": [reserved(), dispatched(), collected(), collected()],
        }
        for name, history in cases.items():
            with self.subTest(case=name):
                self.assert_corrupt(history, "after terminal event")


# --------------------------------------------------------------------------
# 4 (branches). state / native fields each call site writes
# --------------------------------------------------------------------------
class TestEventShape(_Case):
    def test_reserved_state(self):
        self.assert_corrupt([reserved(state="running")], "reserved must be queued/reserved")

    def test_reserved_with_native_ids(self):
        self.assert_corrupt([reserved(native_session_id=THREAD)], "must not carry native identifiers")

    def test_dispatch_failed_branch_not_emitted_by_writer(self):
        self.assert_corrupt([reserved(), failed("turn/start", "thread/start-refused",
                                                native_session_id=THREAD)], "is not one the writer emits")
        self.assert_corrupt([reserved(), failed(None, "thread/start-refused")], "is not one the writer emits")

    def test_dispatch_failed_session_presence(self):
        self.assert_corrupt([reserved(), failed("thread/start", "thread/start-refused",
                                                native_session_id=THREAD)], "presence contradicts")
        self.assert_corrupt([reserved(), failed("turn/start", "turn/start-refused")], "presence contradicts")

    def test_dispatch_failed_state(self):
        self.assert_corrupt([reserved(), failed("thread/start", "thread/start-refused", state="unknown")],
                            "dispatch_failed must be failed")

    def test_dispatch_failed_turns_or_early_model(self):
        self.assert_corrupt([reserved(), failed("turn/start", "turn/start-no-id", native_session_id=THREAD,
                                                native_turns=[TURN])], "must not carry native turns")
        self.assert_corrupt([reserved(), failed("thread/start", "model-not-honored",
                                                native_session_id=THREAD, native_model=MODEL)],
                            "before the model was accepted")

    def test_dispatch_uncertain_shape(self):
        self.assert_corrupt([reserved(), uncertain("thread/start", native_session_id=THREAD)],
                            "presence contradicts")
        self.assert_corrupt([reserved(), uncertain("turn/start")], "presence contradicts")
        self.assert_corrupt([reserved(), uncertain("collect")], "is not one the writer emits")
        self.assert_corrupt([reserved(), uncertain("thread/start", state="failed")],
                            "must be unknown/dispatch-uncertain")
        self.assert_corrupt([reserved(), uncertain("thread/start", native_model=MODEL)],
                            "before thread/start answered")
        self.assert_corrupt([reserved(), uncertain("turn/start", native_session_id=THREAD,
                                                   native_turns=[TURN])], "must not carry native turns")

    def test_dispatched_shape(self):
        self.assert_corrupt([reserved(), dispatched(state="declared_complete")], "running/turn-started")
        self.assert_corrupt([reserved(), dispatched(native_turns=[])], "exactly one turn")
        self.assert_corrupt([reserved(), dispatched(native_turns=[TURN, "t2"])], "exactly one turn")
        self.assert_corrupt([reserved(), dispatched(thread_id="other")], "thread_id contradicts")
        self.assert_corrupt([reserved(), dispatched(turn_id="other")], "turn_id contradicts")

    def test_dispatched_without_echo_fields_is_ok(self):
        record = dispatched()
        del record["thread_id"], record["turn_id"]
        self.assert_ok([reserved(), record])

    def test_collect_states(self):
        self.assert_corrupt([reserved(), dispatched(), collect_timeout(state="failed")],
                            "collect_timeout must be unknown")
        self.assert_corrupt([reserved(), dispatched(), collect_timeout(native_state="completed")],
                            "collect_timeout changed native_state")
        self.assert_corrupt([reserved(), dispatched(), collected(state="succeeded")],
                            "declared_complete or failed")

    def test_collection_never_introduces_identifiers(self):
        self.assert_corrupt([reserved(), dispatched(native_model=None), collected()],
                            "collected introduced native_model")
        self.assert_corrupt([reserved(), dispatched(), collected(native_turns=[TURN, "t2"])],
                            "collected changed the number of native turns")


# --------------------------------------------------------------------------
# 3. idempotency key scope: one registry source
# --------------------------------------------------------------------------
class TestIdempotencyScope(_Case):
    def test_second_run_same_key_same_fingerprint(self):
        self.assert_corrupt([reserved(), reserved(RUN_B, "key-a", request_fingerprint="a" * 64)],
                            "already bound to run")

    def test_second_run_same_key_other_fingerprint(self):
        self.assert_corrupt([reserved(), dispatched(), collected(), reserved(RUN_B, "key-a")],
                            "already bound to run")

    def test_second_run_same_key_interleaved(self):
        self.assert_corrupt([reserved(), reserved(RUN_B, "key-a"), dispatched()], "already bound to run")

    def test_null_key_may_repeat(self):
        self.assert_ok([reserved(key=None), reserved(RUN_B, None), dispatched(key=None),
                        dispatched(RUN_B, None)])

    def test_distinct_keys_are_ok(self):
        self.assert_ok([reserved(), reserved(RUN_B, "key-b")])

    def test_scope_is_one_source_not_global(self):
        other = self.tmp / "other" / "runs.jsonl"
        write(other, [reserved(RUN_B, "key-a")])
        self.assert_ok([reserved()])
        self.assertEqual(self.api(runs=other).list_agent_runs()["state"], "ok")


# --------------------------------------------------------------------------
# 5. wiring: whole history corrupt, before summary and evidence
# --------------------------------------------------------------------------
class TestApiWiring(_Case):
    def bad_history_with_good_run(self):
        evidence = self.tmp / "raw" / "a.notifications.jsonl"
        evidence.parent.mkdir(parents=True, exist_ok=True)
        evidence.write_bytes(b"{}\n")
        good = [reserved(), dispatched(), collected(raw={"path": str(evidence), "sha256": "0" * 64,
                                                         "frames": 1, "bytes": 3})]
        bad = [reserved(RUN_B, "key-b"), collected(RUN_B, "key-b")]
        return good + bad

    def test_list_returns_no_partial_summary(self):
        history = self.bad_history_with_good_run()
        before = write(self.runs, history)
        with mock.patch.object(app_api, "raw_evidence_state",
                               side_effect=AssertionError("evidence read")) as evidence:
            payload = self.api().list_agent_runs()
        self.assertEqual(payload["state"], "corrupt")
        self.assertEqual((payload["runs"], payload["count"]), ([], 0))
        self.assertIn("inconsistent", payload["message"])
        self.assertEqual(payload["error"]["code"], "runs_corrupt")
        evidence.assert_not_called()
        self.assertEqual(self.runs.read_bytes(), before)

    def test_detail_of_a_healthy_run_inside_a_corrupt_history(self):
        history = self.bad_history_with_good_run()
        before = write(self.runs, history)
        with mock.patch.object(app_api, "raw_evidence_state",
                               side_effect=AssertionError("evidence read")) as evidence, \
             mock.patch.object(AppInterface, "_task_link",
                               side_effect=AssertionError("task link read")) as link:
            detail = self.api().agent_run_detail(RUN_A)
        self.assertEqual(detail["state"], "corrupt")
        self.assertIsNone(detail["run"])
        self.assertNotIn("records", detail)
        self.assertEqual(detail["runs"], [])
        evidence.assert_not_called()
        link.assert_not_called()
        self.assertEqual(self.runs.read_bytes(), before)

    def test_no_process_model_or_transport_is_touched(self):
        write(self.runs, self.bad_history_with_good_run())
        boom = AssertionError("process started")
        with mock.patch.object(subprocess, "Popen", side_effect=boom), \
             mock.patch("src.agent_session_codex.CodexStdioTransport", side_effect=boom):
            self.assertEqual(self.api().list_agent_runs()["state"], "corrupt")
            self.assertEqual(self.api().agent_run_detail(RUN_A)["state"], "corrupt")
        write(self.runs, [reserved(), dispatched()])
        with mock.patch.object(subprocess, "Popen", side_effect=boom), \
             mock.patch("src.agent_session_codex.CodexStdioTransport", side_effect=boom):
            self.assertEqual(self.api().list_agent_runs()["state"], "ok")

    def test_ok_payload_fields_are_unchanged(self):
        write(self.runs, [reserved(), dispatched(), collected()])
        payload = self.api().list_agent_runs()
        self.assertEqual(set(payload), {"ok", "read_only", "source", "state", "runs", "count",
                                        "message", "live", "note"})
        self.assertEqual(set(payload["runs"][0]), {
            "run_id", "events", "app_kind", "instance_id", "session_id", "role", "requested_model",
            "native_model", "logical_task_id", "attempt", "request_fingerprint", "idempotency_key",
            "native_session_id", "native_turns", "record_state", "native_state", "last_recorded_at",
            "first_recorded_at", "usage", "evidence", "unverified", "declared_status",
            "dispatch_uncertain_reason", "snapshot"})
        detail = self.api().agent_run_detail(RUN_A)
        self.assertEqual(detail["state"], "ok")
        self.assertEqual([r["event"] for r in detail["records"]], ["reserved", "dispatched", "collected"])

    def test_field_validation_still_runs_first(self):
        history = [reserved(), dispatched()]
        history[1]["attempt"] = "1"          # a single-record fault, not a cross-record one
        write(self.runs, history)
        payload = self.api().list_agent_runs()
        self.assertEqual(payload["state"], "corrupt")
        self.assertIn("attempt must be a non-negative integer", payload["message"])

    def test_check_does_not_mutate_records(self):
        history = [reserved(), dispatched(), collected()]
        snapshot = json.dumps(history, sort_keys=True)
        check_history(history)
        self.assertEqual(json.dumps(history, sort_keys=True), snapshot)


# --------------------------------------------------------------------------
# R1 (Hark-派单-04): JSON type space of the `stage` extra field
# --------------------------------------------------------------------------
STAGE_BRANCHES = [
    ("failed_thread_refused", "dispatch_failed", "failed", "thread/start-refused", "thread/start", None),
    ("failed_thread_no_id", "dispatch_failed", "failed", "thread/start-no-id", "thread/start", None),
    ("failed_model", "dispatch_failed", "failed", "model-not-honored", "thread/start", THREAD),
    ("failed_turn_refused", "dispatch_failed", "failed", "turn/start-refused", "turn/start", THREAD),
    ("failed_turn_no_id", "dispatch_failed", "failed", "turn/start-no-id", "turn/start", THREAD),
    ("uncertain_thread", "dispatch_uncertain", "unknown", "dispatch-uncertain", "thread/start", None),
    ("uncertain_turn", "dispatch_uncertain", "unknown", "dispatch-uncertain", "turn/start", THREAD),
]
_MISSING = object()
STAGE_VALUES = [("correct", _MISSING), ("null", None), ("absent", _MISSING), ("empty", ""),
                ("unknown", "other"), ("bool", True), ("int", 7), ("float", 1.5),
                ("empty_array", []), ("array", ["thread/start"]), ("empty_object", {}),
                ("object", {"value": "turn/start"})]


def stage_history(branch, kind, value):
    _, event, state, native_state, stage, session = branch
    last = rec(event, state=state, native_state=native_state, native_session_id=session)
    if kind == "correct":
        last["stage"] = stage
    elif kind != "absent":
        last["stage"] = value
    return [reserved(), last]


def observe(call):
    """An API return or raise, as one explicit observed value."""
    try:
        return call()
    except Exception as exc:  # observed and asserted on, never swallowed
        return type(exc).__name__


class TestStageJsonType(_Case):
    def matrix(self):
        for branch in STAGE_BRANCHES:
            for kind, value in STAGE_VALUES:
                yield branch, kind, stage_history(branch, kind, value)

    def test_direct_check_turns_every_invalid_stage_into_history_inconsistent(self):
        counts = {"ok": 0, "inconsistent": 0}
        for branch, kind, history in self.matrix():
            with self.subTest(branch=branch[0], stage=kind):
                def run():
                    try:
                        check_history(history)
                        return "ok"
                    except HistoryInconsistent:
                        return "inconsistent"
                observed = observe(run)
                expected = "ok" if kind == "correct" else "inconsistent"
                self.assertEqual(observed, expected)
                counts[observed] = counts.get(observed, 0) + 1
        self.assertEqual(counts, {"ok": 7, "inconsistent": 77})

    def test_api_list_and_detail_are_corrupt_without_reads(self):
        for branch, kind, history in self.matrix():
            if kind == "correct":
                continue
            with self.subTest(branch=branch[0], stage=kind):
                runs = self.tmp / f"{branch[0]}-{kind}.jsonl"
                before = write(runs, history)
                api = self.api(runs=runs)
                with mock.patch.object(app_api, "raw_evidence_state",
                                       side_effect=AssertionError("evidence read")) as evidence, \
                     mock.patch.object(AppInterface, "_task_link",
                                       side_effect=AssertionError("task link read")) as link:
                    listed = observe(api.list_agent_runs)
                    detail = observe(lambda: api.agent_run_detail(RUN_A))
                self.assertIsInstance(listed, dict, listed)
                self.assertEqual((listed["state"], listed["runs"], listed["count"]), ("corrupt", [], 0))
                self.assertIsInstance(detail, dict, detail)
                self.assertEqual(detail["state"], "corrupt")
                self.assertIsNone(detail["run"])
                evidence.assert_not_called()
                link.assert_not_called()
                self.assertEqual(runs.read_bytes(), before)

    def test_api_correct_stage_stays_ok(self):
        for branch in STAGE_BRANCHES:
            with self.subTest(branch=branch[0]):
                runs = self.tmp / f"{branch[0]}-correct.jsonl"
                before = write(runs, stage_history(branch, "correct", None))
                api = self.api(runs=runs)
                listed = observe(api.list_agent_runs)
                detail = observe(lambda: api.agent_run_detail(RUN_A))
                self.assertIsInstance(listed, dict, listed)
                self.assertEqual((listed["state"], listed["count"]), ("ok", 1))
                self.assertIsInstance(detail, dict, detail)
                self.assertEqual(detail["run"]["run_id"], RUN_A)
                self.assertEqual(runs.read_bytes(), before)

    def test_inputs_pass_the_shared_field_parser(self):
        from src.agent_session_codex import parse_registry_text
        for branch, kind, history in self.matrix():
            with self.subTest(branch=branch[0], stage=kind):
                text = "".join(json.dumps(r) + "\n" for r in history)
                self.assertEqual(len(parse_registry_text(text)), 2)


if __name__ == "__main__":
    unittest.main()
