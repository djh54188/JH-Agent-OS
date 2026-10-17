"""Hark pilot 2026-10-16: independent offline probes (SYNTHETIC ONLY).

Scope and limits
- Every task, workspace, delivery, review, archive and session line built here is
  synthetic. Workspaces live in a fresh temp dir prefixed "hark-pilot-synthetic-";
  the real ~/.codex store, real user files and model APIs are never touched.
- Production source, tests, sealed fixtures, schemas and permissions are NOT
  modified or monkeypatched. Shared test helpers are imported read-only only to
  assemble the same synthetic reviewing task the regression suite uses.
- This is not a production bridge or N2/P1 acceptance. production complete and
  FileToolExecutor stay closed; nothing here feeds complete_code_delivery success.
- Probes that need the Windows desktop path model are reported as platform
  blocked, never as passed.

Run from the repo root:  python -X utf8 -B scripts/hark_pilot_probe.py
Exit 0 = every probe matched its fail-closed expectation (or is reported blocked).
"""
import json
import os
import platform
import shutil
import sqlite3
import sys
import tempfile
import uuid
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tests"))

import test_jh006_code_delivery as tcd  # noqa: E402  (read-only helper reuse)
from test_jh006_capture_binding import _BindCase, TARGET  # noqa: E402
import src.capture_binding as cb  # noqa: E402
from src.capture_binding import BindingRefused, verify_binding_current  # noqa: E402
from src.code_delivery import CodeDeliveryRefused  # noqa: E402
from src.host_capture import CaptureError, verify_desktop_binding  # noqa: E402
from src.router_policy import RiskPolicyEngine  # noqa: E402

RESULTS = []


def record(name, expectation, observed, ok, kind="probe"):
    RESULTS.append({"probe": name, "kind": kind, "expectation": expectation,
                    "observed": observed, "verdict": ("pass" if ok else "FAIL")
                    if kind == "probe" else kind})


def refusal(fn):
    try:
        fn()
    except (BindingRefused, CaptureError, CodeDeliveryRefused, ValueError) as exc:
        return type(exc).__name__ + ": " + str(exc)[:160]
    return None


class _Probe(_BindCase):
    """Synthetic case; overrides setUp so nothing is written inside the repo."""

    def runTest(self):  # pragma: no cover - unittest plumbing only
        pass

    def setUp(self):
        self.case = Path(tempfile.mkdtemp(prefix="hark-pilot-synthetic-"))
        self.ws = self.case / "ws"
        (self.ws / "target").mkdir(parents=True)
        (self.ws / TARGET).write_bytes(tcd._baseline_bytes())
        self.addCleanup(shutil.rmtree, self.case, ignore_errors=True)
        self.db = self.case / "probe.sqlite3"
        self._seq = 0
        self._receipt_stores = {}


def new_case():
    case = _Probe()
    case.setUp()
    return case


def main():
    env = {"python": sys.version.split()[0], "platform": platform.platform(),
           "os_name": os.name, "shell": os.environ.get("SHELL"),
           "home_codex_exists": (Path.home() / ".codex").exists()}

    # P1 platform gate: POSIX workdir is refused (fail-closed), not accepted.
    c = new_case()
    try:
        orch, tid, delivery = c._prepared_production()
        c._fixture_apply()
        plan = c._plan_for(tid, delivery)
        posix_workdir = "/tmp/hark-pilot-posix/ws"
        fake_exec = {"thread_id": "t", "item_id": "i", "turn_id": "u", "source": "s",
                     "argv": plan["argv"], "cwd_uri": "file://" + posix_workdir,
                     "exit_code": 0, "output": "PASS"}
        fake_call = {"execution_key": ("t", "i"), "turn_id": "u", "source": "s",
                     "input": plan["call_input"]}
        why = refusal(lambda: verify_desktop_binding(
            fake_exec, fake_call, expected_argv=plan["argv"], expected_workdir=posix_workdir,
            expected_exit=0, expected_marker="PASS", expected_call_input=plan["call_input"]))
        record("P1-posix-workdir-refused", "non-Windows workdir refused, not bound",
               why, why is not None and "expectation-malformed" in why)

        # P2 currentness: post-apply snapshot ok; target drift refused.
        snap = cb._control_snapshot(orch.storage, tid, delivery["delivery_id"])
        record("P2a-snapshot-after-apply", "snapshot succeeds once target == expected_after",
               {"observed_target_sha256": snap["observed_target_sha256"][:16]},
               snap["observed_target_sha256"] == tcd._sha(tcd._reference_bytes()))
        (c.ws / TARGET).write_bytes(tcd._baseline_bytes())
        why = refusal(lambda: cb._control_snapshot(orch.storage, tid, delivery["delivery_id"]))
        record("P2b-target-revert-refused", "target reverted to baseline -> refused",
               why, why is not None and "target-bytes-mismatch" in why)
        c._fixture_apply(b"tampered\n")
        why = refusal(lambda: cb._control_snapshot(orch.storage, tid, delivery["delivery_id"]))
        record("P2c-target-foreign-bytes-refused", "foreign target bytes -> refused",
               why, why is not None and "target-bytes-mismatch" in why)
        c._fixture_apply()
        plan_pre = cb.capture_invocation_for_delivery(orch.storage, tid, delivery["delivery_id"])
        record("P2d-plan-is-deterministic", "read-only plan equals fixed invocation",
               plan_pre == plan, plan_pre == plan)

        # P3 echo reassertion is not the fixed check.
        echo_argv = [plan["argv"][0], "-Command", "echo PASS"]
        record("P3-echo-differs-from-fixed", "caller echo argv != control-owned argv",
               {"equal": echo_argv == plan["argv"]}, echo_argv != plan["argv"])

        # P4 pending approval / unknown usage block currentness.
        orch.storage.hold_request(tid, {"request_id": "synthetic-hold"})
        why = refusal(lambda: cb._control_snapshot(orch.storage, tid, delivery["delivery_id"]))
        record("P4a-pending-approval-blocks", "pending approval -> control-blocked",
               why, why is not None and "control-blocked" in why)
        orch.storage._conn.execute("UPDATE task_control SET pending_json = NULL WHERE task_id = ?", (tid,))
        orch.storage._conn.commit()
        orch.storage.record_usage(tid, actor_role="reviewer", tokens_used=None)
        why = refusal(lambda: cb._control_snapshot(orch.storage, tid, delivery["delivery_id"]))
        record("P4b-unknown-usage-blocks", "unknown token usage -> control-blocked",
               why, why is not None and "control-blocked" in why)

        # P5 production complete stays closed.
        before = orch.storage.get_task(tid)["status"]
        why = refusal(lambda: orch.complete_code_delivery(tid, c._receipt(tid, delivery)))
        after = orch.storage.get_task(tid)["status"]
        record("P5-production-complete-closed", "production complete refused, state unchanged",
               {"refusal": why, "before": before, "after": after},
               why is not None and "out of scope" in why and before == after == "reviewing")

        # P6 verify_binding_current with no binding: current false, no writes.
        res = verify_binding_current(orch.storage, tid, delivery["delivery_id"])
        record("P6-no-binding-not-current", "no binding -> current false",
               res["reasons"], res["current"] is False and res["reasons"] == ["no-binding"])
    finally:
        c.doCleanups()

    # P7 new review attempt / new artifact invalidate the snapshot (fresh task each).
    for label, mutate, needle in (
            ("P7a-new-review-attempt", "review", "review-stale"),
            ("P7b-new-artifact-version", "artifact", "artifact-changed")):
        c = new_case()
        try:
            orch, tid, delivery = c._prepared_production()
            c._fixture_apply()
            if mutate == "review":
                latest = orch.storage.get_latest_artifact(tid)
                last = [a for a in orch.storage.list_model_attempts(tid)
                        if a["actor_role"] == "reviewer"][-1]
                aid = orch.storage.begin_attempt(tid, "reviewer", {"artifact_hashes": {
                    f"mem://{tid}/{latest['artifact_id']}": latest["sha256"]}})
                orch.storage.complete_attempt(tid, aid, last["response"], 100)
            else:
                orch.artifact_store.save_version(tid, envelope={
                    "schema_version": "1.0.0", "artifact_id": "art_probe" + uuid.uuid4().hex[:6],
                    "task_id": tid, "artifact_type": "patch_text",
                    "content": tcd._patch_envelope(tcd._reference_bytes()),
                    "summary": "synthetic second version"}, version=2,
                    generation_attempt_id=None)
            why = refusal(lambda: cb._control_snapshot(orch.storage, tid, delivery["delivery_id"]))
            record(label, f"{mutate} drift -> refused", why, why is not None and needle in why)
        finally:
            c.doCleanups()

    # P8 cross-task: fixed invocation / apply-attempt id are delivery-specific.
    c = new_case()
    try:
        orch, tid1, d1 = c._prepared_production()
        _, tid2, d2 = c._prepared_production(orch=orch)
        p1, p2 = c._plan_for(tid1, d1), c._plan_for(tid2, d2)
        ok = p1["apply_attempt_id"] != p2["apply_attempt_id"] and p1["call_input"] != p2["call_input"]
        record("P8-cross-task-plans-differ", "two deliveries never share a fixed check identity",
               {"same_apply_attempt": p1["apply_attempt_id"] == p2["apply_attempt_id"]}, ok)

        # P9 database uniqueness + rollback (direct storage, synthetic rows only).
        st = orch.storage

        def row(task_id, delivery_id, thread, item, call):
            return {"binding_id": "cb_" + uuid.uuid4().hex, "task_id": task_id,
                    "delivery_id": delivery_id, "thread_id": thread, "item_id": item,
                    "call_id": call, "protocol": "desktop", "snapshot_json": "{}",
                    "capture_json": "{}", "manifest_json": "{}", "archive_dir": "synthetic",
                    "event_digest": "0" * 64, "manifest_digest": "0" * 64,
                    "apply_attempt_id": "synthetic", "review_items_json": "[]",
                    "recorded_at": "2026-10-17T00:00:00Z"}

        with st.transaction():
            st.save_capture_binding(row(tid1, d1["delivery_id"], "th-syn", "it-1", "ca-1"))
        dup_call = refusal(lambda: st.save_capture_binding(row(tid2, d2["delivery_id"], "th-syn", "it-2", "ca-1")))
        dup_item = refusal(lambda: st.save_capture_binding(row(tid2, d2["delivery_id"], "th-syn", "it-1", "ca-2")))
        dup_deliv = refusal(lambda: st.save_capture_binding(row(tid1, d1["delivery_id"], "th-syn2", "it-9", "ca-9")))
        record("P9a-unique-call", "same thread+call with other item rejected", dup_call, dup_call is not None)
        record("P9b-unique-item", "same thread+item with other call rejected", dup_item, dup_item is not None)
        record("P9c-unique-delivery", "same task+delivery twice rejected", dup_deliv, dup_deliv is not None)
        try:
            with st.transaction():
                st.save_capture_binding(row(tid2, d2["delivery_id"], "th-roll", "it-r", "ca-r"))
                raise RuntimeError("synthetic event failure")
        except RuntimeError:
            pass
        rolled = st.get_capture_binding(tid2, d2["delivery_id"]) is None
        record("P9d-transaction-rollback", "row absent after failure inside transaction", rolled, rolled)
        st._conn.execute("DELETE FROM capture_bindings WHERE thread_id LIKE 'th-%'")
        st._conn.commit()
    finally:
        c.doCleanups()

    # P10 informational: Windows-absolute paths under the POSIX path model.
    tmp = Path(tempfile.mkdtemp(prefix="hark-pilot-synthetic-policy-"))
    try:
        eng = RiskPolicyEngine(str(tmp))
        req = {"schema_version": "1.0.0", "request_id": "treq_probe0001", "task_id": "task_probe00001",
               "tool_name": "write_file", "parameters": {"path": "D:/another-project/notes.md"},
               "requested_by": "worker", "created_at": "2026-10-17T00:00:00Z"}
        try:
            action = eng.evaluate_tool_request(req).action
        except Exception as exc:  # report, do not hide
            action = f"error: {type(exc).__name__}: {exc}"
        record("P10-windows-abs-path-on-posix", "informational: Windows-absolute path classification on this OS",
               {"os_name": os.name, "action": action}, None, kind="observation")
    finally:
        shutil.rmtree(tmp, ignore_errors=True)

    failed = [r for r in RESULTS if r["verdict"] == "FAIL"]
    out = {"kind": "hark-pilot-synthetic-probes", "api_calls": 0, "env": env,
           "probes": len([r for r in RESULTS if r["kind"] == "probe"]),
           "failed": len(failed), "results": RESULTS,
           "platform_blocked": ["desktop register/current/archive verification need a Windows drive "
                                "workdir and the measured pwsh desktop wrapper; not exercised here"]
                               if os.name != "nt" else [],
           "not_exercised": ["full desktop register/current/archive verification is outside this probe script"],
           "verdict": "pass" if not failed else "fail"}
    print(json.dumps(out, ensure_ascii=False, indent=2, default=str))
    return 0 if not failed else 1


if __name__ == "__main__":
    sys.exit(main())
