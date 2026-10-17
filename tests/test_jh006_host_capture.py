"""Offline parse controls for independent host-record capture (synthetic only).

Every log below is SYNTHETIC test data (thread ids prefixed "test-synth-");
nothing here proves a real host executed anything. These controls prove the
capture parser refuses forged digests, echo-keyword commands, wrong workdirs,
cross-task/old-call reuse, and missing/duplicate raw events. Production
complete stays closed regardless; see test_jh006_host_boundary.py.
"""
import hashlib
import json
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from src.host_capture import (
    CaptureError,
    build_capture_manifest,
    parse_jsonl,
    require_desktop_session_meta,
    select_cli_execution,
    select_desktop_execution,
    select_desktop_tool_call,
    verify_archive_consistency,
    verify_cli_binding,
    verify_desktop_binding,
)

THREAD = "test-synth-thread-0001"
TURN = "test-synth-turn-0001"
ITEM = "test-synth-exec-0001"
CALL = "test-synth-call-0001"
WORKDIR = "D:/synth-isolated-ws"
WORKDIR_URI = "file:///D:/synth-isolated-ws"
CHECK_REL = "tests/fixtures/n2/code_sortfix/check_widget.py"
TARGET = "target/widget.py"
FIXED_ARGV = ["C:/synth/python.exe", CHECK_REL, "--target", f"{WORKDIR}/{TARGET}"]
CALL_SCRIPT = "tools.exec_command({cmd: FIXED_SYNTH_CHECK, workdir: D:/synth-isolated-ws})"
OUTPUT = "case 0: ok\ncase 1: ok\ncase 2: ok\ncase 3: ok\nPASS\n"


def _sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _desktop_meta(thread=THREAD):
    return {"timestamp": "2026-10-16T00:00:00Z", "ordinal": 1, "type": "session_meta",
            "payload": {"id": thread, "cwd": "C:/synth-chat", "originator": "SynthDesktop"}}


def _desktop_exec(item=ITEM, thread=THREAD, turn=TURN, argv=None, cwd_uri=WORKDIR_URI,
                  status="completed", exit_code=0, output=OUTPUT, ordinal=3):
    return {"timestamp": "2026-10-16T00:00:01Z", "ordinal": ordinal, "type": "event_msg",
            "payload": {"type": "item_completed", "thread_id": thread, "turn_id": turn,
                        "item": {"type": "CommandExecution", "id": item, "process_id": "4242",
                                 "command": FIXED_ARGV if argv is None else argv,
                                 "cwd": cwd_uri, "parsed_cmd": [], "source": "synth-test",
                                 "status": status, "aggregated_output": output,
                                 "exit_code": exit_code,
                                 "duration": {"secs": 0, "nanos": 1}},
                        "started_at_ms": 1, "completed_at_ms": 2}}


def _desktop_call(call=CALL, turn=TURN, script=CALL_SCRIPT, name="exec",
                  status="completed", ordinal=2):
    return {"timestamp": "2026-10-16T00:00:02Z", "ordinal": ordinal, "type": "response_item",
            "payload": {"type": "custom_tool_call", "id": "synth-ctc-1", "status": status,
                        "call_id": call, "name": name, "input": script,
                        "internal_chat_message_metadata_passthrough": {"turn_id": turn}}}


def _desktop_call_output(call=CALL, turn=TURN, ordinal=4):
    return {"timestamp": "2026-10-16T00:00:03Z", "ordinal": ordinal, "type": "response_item",
            "payload": {"type": "custom_tool_call_output", "id": "synth-ctco-1",
                        "call_id": call, "output": [{"type": "input_text", "text": "ok"}],
                        "internal_chat_message_metadata_passthrough": {"turn_id": turn}}}


def _desktop_segment(*lines):
    lines = sorted(lines, key=lambda line: line.get("ordinal", 0))
    return ("\n".join(json.dumps(line, ensure_ascii=False) for line in lines) + "\n").encode()


def _cli_stream(item=ITEM, thread=THREAD, command="SYNTH_FIXED_COMMAND",
                output=OUTPUT, exit_code=0, status="completed"):
    lines = [{"type": "thread.started", "thread_id": thread},
             {"type": "turn.started"},
             {"type": "item.completed",
              "item": {"id": item, "type": "command_execution", "command": command,
                       "aggregated_output": output, "exit_code": exit_code,
                       "status": status}}]
    return ("\n".join(json.dumps(line) for line in lines) + "\n").encode()


class TestDesktopCapturePositive(unittest.TestCase):
    def _records(self):
        raw = _desktop_segment(_desktop_meta(), _desktop_exec(), _desktop_call(),
                               _desktop_call_output())
        return parse_jsonl(raw, source_label="synth")

    def test_valid_capture_verifies_and_round_trips(self):
        records = self._records()
        require_desktop_session_meta(records, THREAD)
        selected = select_desktop_execution(records, thread_id=THREAD, item_id=ITEM)
        call = select_desktop_tool_call(records, call_id=CALL, turn_id=TURN)
        bindings = verify_desktop_binding(
            selected, call, expected_argv=FIXED_ARGV, expected_workdir=WORKDIR,
            expected_exit=0, expected_marker="PASS", expected_call_input=CALL_SCRIPT)
        self.assertEqual(bindings["thread_id"], THREAD)
        self.assertEqual(bindings["item_id"], ITEM)
        self.assertEqual(bindings["call_id"], CALL)
        self.assertEqual(bindings["argv"], FIXED_ARGV)
        self.assertEqual(bindings["output_digest"], _sha(OUTPUT.encode()))
        manifest = build_capture_manifest(
            task_id="task_synth0001", delivery_id="dlv_synth_v1",
            artifact_claim={"version": 1}, review_claim={"review_id": "rev_synth"},
            apply_attempt_id="synth-apply-1", protocol="desktop",
            source_files=[{"path": "synth.jsonl", "sha256": "00", "bytes": 8}],
            bindings=bindings)
        verify_archive_consistency(manifest, event_raw=selected["raw"], output=OUTPUT)

    def test_tampered_archive_bytes_fail_consistency(self):
        records = self._records()
        selected = select_desktop_execution(records, thread_id=THREAD, item_id=ITEM)
        call = select_desktop_tool_call(records, call_id=CALL, turn_id=TURN)
        bindings = verify_desktop_binding(
            selected, call, expected_argv=FIXED_ARGV, expected_workdir=WORKDIR,
            expected_exit=0, expected_call_input=CALL_SCRIPT)
        manifest = build_capture_manifest(
            task_id="task_synth0001", delivery_id="dlv_synth_v1",
            artifact_claim={}, review_claim={}, apply_attempt_id="synth-apply-1",
            protocol="desktop", source_files=[], bindings=bindings)
        with self.assertRaisesRegex(CaptureError, "archive-tampered"):
            verify_archive_consistency(manifest, event_raw=selected["raw"],
                                       output=OUTPUT + "forged line\n")
        with self.assertRaisesRegex(CaptureError, "archive-tampered"):
            verify_archive_consistency(manifest, event_raw=b'{"forged": true}',
                                       output=OUTPUT)


class TestDesktopCaptureNegative(unittest.TestCase):
    def _verify(self, *lines, **kwargs):
        records = parse_jsonl(_desktop_segment(*lines), source_label="synth")
        require_desktop_session_meta(records, THREAD)
        params = {"expected_argv": FIXED_ARGV, "expected_workdir": WORKDIR,
                  "expected_exit": 0, "expected_marker": "PASS",
                  "expected_call_input": CALL_SCRIPT}
        params.update(kwargs.pop("expect", {}))
        selected = select_desktop_execution(records, thread_id=THREAD, item_id=ITEM)
        call = select_desktop_tool_call(records, call_id=CALL, turn_id=TURN)
        return verify_desktop_binding(selected, call, **params)

    def test_echo_keyword_argv_refused_without_contains(self):
        echo_argv = ["C:/synth/pwsh.exe", "-Command",
                     f'Write-Output "PASS {CHECK_REL} {TARGET}"']
        with self.assertRaisesRegex(CaptureError, "argv-mismatch"):
            self._verify(_desktop_meta(), _desktop_exec(argv=echo_argv), _desktop_call(),
                         _desktop_call_output())

    def test_wrong_workdir_refused(self):
        with self.assertRaisesRegex(CaptureError, "cwd-mismatch"):
            self._verify(_desktop_meta(),
                         _desktop_exec(cwd_uri="file:///D:/other-ws"), _desktop_call(),
                         _desktop_call_output())

    def test_chat_cwd_is_not_tool_workdir(self):
        with self.assertRaisesRegex(CaptureError, "cwd-mismatch"):
            self._verify(_desktop_meta(),
                         _desktop_exec(cwd_uri="file:///C:/synth-chat"), _desktop_call(),
                         _desktop_call_output())

    def test_cross_thread_item_missing(self):
        with self.assertRaisesRegex(CaptureError, "missing-item"):
            records = parse_jsonl(_desktop_segment(
                _desktop_meta(), _desktop_exec(thread="test-synth-thread-9999"),
                _desktop_call(), _desktop_call_output()), source_label="synth")
            select_desktop_execution(records, thread_id=THREAD, item_id=ITEM)

    def test_duplicate_item_refused(self):
        raw = _desktop_segment(_desktop_meta(), _desktop_exec(), _desktop_exec(ordinal=5),
                               _desktop_call(), _desktop_call_output())
        records = parse_jsonl(raw, source_label="synth")
        with self.assertRaisesRegex(CaptureError, "duplicate-item"):
            select_desktop_execution(records, thread_id=THREAD, item_id=ITEM)

    def test_missing_item_refused(self):
        records = parse_jsonl(_desktop_segment(_desktop_meta(), _desktop_call(),
                                               _desktop_call_output()),
                              source_label="synth")
        with self.assertRaisesRegex(CaptureError, "missing-item"):
            select_desktop_execution(records, thread_id=THREAD, item_id=ITEM)

    def test_unparsed_line_fails_capture(self):
        raw = _desktop_segment(_desktop_meta(), _desktop_exec()) + b"{torn json\n"
        with self.assertRaisesRegex(CaptureError, "unparsed-line"):
            parse_jsonl(raw, source_label="synth")

    def test_nonzero_exit_refused(self):
        with self.assertRaisesRegex(CaptureError, "exit-mismatch"):
            self._verify(_desktop_meta(), _desktop_exec(exit_code=1), _desktop_call(),
                         _desktop_call_output())

    def test_failed_status_refused_at_selection(self):
        records = parse_jsonl(_desktop_segment(_desktop_meta(), _desktop_exec(status="failed")),
                              source_label="synth")
        with self.assertRaisesRegex(CaptureError, "status-not-completed"):
            select_desktop_execution(records, thread_id=THREAD, item_id=ITEM)

    def test_marker_absent_refused(self):
        with self.assertRaisesRegex(CaptureError, "marker-absent"):
            self._verify(_desktop_meta(), _desktop_exec(output="case 0: FAIL\nFAIL\n"),
                         _desktop_call(), _desktop_call_output())

    def test_call_turn_mismatch_refused(self):
        records = parse_jsonl(_desktop_segment(
            _desktop_meta(), _desktop_exec(),
            _desktop_call(turn="test-synth-turn-9999"), _desktop_call_output()),
            source_label="synth")
        with self.assertRaisesRegex(CaptureError, "call-turn-mismatch"):
            select_desktop_tool_call(records, call_id=CALL, turn_id=TURN)

    def test_call_input_mismatch_refused(self):
        with self.assertRaisesRegex(CaptureError, "call-input-mismatch"):
            self._verify(_desktop_meta(), _desktop_exec(),
                         _desktop_call(script="tools.exec_command({cmd: OTHER})"),
                         _desktop_call_output())

    def test_meta_thread_mismatch_refused(self):
        records = parse_jsonl(_desktop_segment(
            _desktop_meta(thread="test-synth-thread-9999"), _desktop_exec()),
            source_label="synth")
        with self.assertRaisesRegex(CaptureError, "meta-mismatch"):
            require_desktop_session_meta(records, THREAD)

    def test_oversized_output_refused(self):
        big = "PASS\n" + "x" * (1024 * 1024)
        with self.assertRaisesRegex(CaptureError, "output-too-large"):
            self._verify(_desktop_meta(), _desktop_exec(output=big), _desktop_call(),
                         _desktop_call_output())


class TestCliCaptureBinding(unittest.TestCase):
    def _select(self, raw):
        records = parse_jsonl(raw, source_label="synth")
        return select_cli_execution(records, thread_id=THREAD, item_id=ITEM)

    def test_cli_cannot_bind_workdir_and_fails_closed(self):
        selected = self._select(_cli_stream())
        with self.assertRaisesRegex(CaptureError, "workdir-unattested"):
            verify_cli_binding(selected, expected_command="SYNTH_FIXED_COMMAND",
                               expected_exit=0, expected_marker="PASS")

    def test_cli_command_uses_exact_equality(self):
        selected = self._select(_cli_stream())
        with self.assertRaisesRegex(CaptureError, "command-mismatch"):
            verify_cli_binding(selected, expected_command="SYNTH_FIXED_COMMAND plus echo",
                               expected_exit=0)

    def test_cli_missing_thread_envelope_refused(self):
        raw = ("\n".join(json.dumps(line) for line in [
            {"type": "turn.started"},
            {"type": "item.completed",
             "item": {"id": ITEM, "type": "command_execution", "command": "c",
                      "aggregated_output": "o", "exit_code": 0, "status": "completed"}}])
               + "\n").encode()
        records = parse_jsonl(raw, source_label="synth")
        with self.assertRaisesRegex(CaptureError, "thread-unverifiable"):
            select_cli_execution(records, thread_id=THREAD, item_id=ITEM)

    def test_cli_unconfirmed_exit_refused(self):
        raw = ("\n".join(json.dumps(line) for line in [
            {"type": "thread.started", "thread_id": THREAD},
            {"type": "item.completed",
             "item": {"id": ITEM, "type": "command_execution", "command": "c",
                      "aggregated_output": "", "exit_code": None, "status": "in_progress"}}])
               + "\n").encode()
        records = parse_jsonl(raw, source_label="synth")
        with self.assertRaisesRegex(CaptureError, "status-not-completed"):
            select_cli_execution(records, thread_id=THREAD, item_id=ITEM)

    def test_desktop_bytes_rejected_by_cli_selector(self):
        records = parse_jsonl(_desktop_segment(_desktop_meta(), _desktop_exec()),
                              source_label="synth")
        with self.assertRaisesRegex(CaptureError, "thread-unverifiable"):
            select_cli_execution(records, thread_id=THREAD, item_id=ITEM)

    def test_cli_bytes_rejected_by_desktop_selector(self):
        records = parse_jsonl(_cli_stream(), source_label="synth")
        with self.assertRaisesRegex(CaptureError, "meta-mismatch"):
            require_desktop_session_meta(records, THREAD)


if __name__ == "__main__":
    unittest.main()
