"""Independent capture-entry controls. Synthetic inputs are never runtime proof."""
import copy
import importlib.util
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from src.host_capture import (CaptureError, parse_jsonl, require_desktop_session_meta,
    select_desktop_execution, select_desktop_tool_call, verify_desktop_binding,
    build_capture_manifest, sha256_hex, verify_capture_archive)
from test_jh006_host_capture import (_desktop_meta, _desktop_exec, _desktop_call,
    _desktop_call_output, _desktop_segment, THREAD, TURN, ITEM, CALL, FIXED_ARGV,
    WORKDIR, CALL_SCRIPT)

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("capture_entry", ROOT / "scripts/probe_host_capture.py")
entry = importlib.util.module_from_spec(spec)
spec.loader.exec_module(entry)


def lines():
    return [_desktop_meta(), _desktop_call(), _desktop_exec(), _desktop_call_output()]


def binding(raw, call_id=CALL, **over):
    rs = parse_jsonl(raw, source_label="synthetic-boundary")
    require_desktop_session_meta(rs, THREAD)
    selected = select_desktop_execution(rs, thread_id=THREAD, item_id=ITEM)
    call = select_desktop_tool_call(rs, call_id=call_id, turn_id=TURN)
    opts = dict(expected_argv=FIXED_ARGV, expected_workdir=WORKDIR,
                expected_exit=0, expected_call_input=CALL_SCRIPT)
    opts.update(over)
    return verify_desktop_binding(selected, call, **opts)


class TestCaptureBoundaries(unittest.TestCase):
    def test_same_turn_unrelated_call_does_not_bind(self):
        records = lines() + [_desktop_call(call="other", script="text(42)", ordinal=5),
                             _desktop_call_output(call="other", ordinal=6)]
        with self.assertRaisesRegex(CaptureError, "call-window-ambiguous|call-execution-mismatch"):
            binding(_desktop_segment(*records), call_id="other", expected_call_input="text(42)")

    def test_nested_call_window_refused(self):
        records = lines() + [_desktop_call(call="outer", ordinal=0),
                             _desktop_call_output(call="outer", ordinal=8)]
        with self.assertRaisesRegex(CaptureError, "call-window-ambiguous"):
            binding(_desktop_segment(*records))

    def test_multiple_executions_in_window_refused(self):
        records = lines() + [_desktop_exec(item="other-exec", ordinal=3)]
        with self.assertRaisesRegex(CaptureError, "call-window-ambiguous"):
            binding(_desktop_segment(*records))

    def test_execution_before_call_refused(self):
        records = [_desktop_meta(), _desktop_exec(ordinal=1), _desktop_call(), _desktop_call_output()]
        with self.assertRaisesRegex(CaptureError, "call-window-ambiguous"):
            binding(_desktop_segment(*records))

    def test_foreign_uri_authority_is_not_local_cwd(self):
        records = [_desktop_meta(), _desktop_call(),
                   _desktop_exec(cwd_uri="file://remote-server/D:/synth-isolated-ws"),
                   _desktop_call_output()]
        with self.assertRaisesRegex(CaptureError, "cwd-unparseable"):
            binding(_desktop_segment(*records))

    def test_conflicting_session_meta_refused(self):
        with self.assertRaisesRegex(CaptureError, "meta-mismatch"):
            binding(_desktop_segment(*lines(), _desktop_meta(thread="foreign")))

    def test_missing_exact_script_pin_refused(self):
        with self.assertRaisesRegex(CaptureError, "expectation-malformed"):
            binding(_desktop_segment(*lines()), expected_call_input=None)

    def test_relative_workspace_pin_refused(self):
        with self.assertRaisesRegex(CaptureError, "expectation-malformed"):
            binding(_desktop_segment(*lines()), expected_workdir="relative")

    def test_malformed_payload_controlled_rejection(self):
        with self.assertRaisesRegex(CaptureError, "record-malformed"):
            parse_jsonl(b'{"type":"event_msg","payload":true}\n', source_label="synthetic")

    def test_duplicate_json_keys_refused(self):
        with self.assertRaisesRegex(CaptureError, "unparsed-line"):
            parse_jsonl(b'{"type":"x","type":"session_meta"}\n', source_label="synthetic")

    def test_workspace_file_is_not_a_session_source(self):
        with tempfile.TemporaryDirectory(dir=ROOT / "data") as tmp:
            source = Path(tmp) / "rollout-synthetic.jsonl"
            source.write_bytes(_desktop_segment(*lines()))
            with self.assertRaisesRegex(CaptureError, "source-untrusted"):
                entry._trusted_segment(source)

    def test_real_store_path_resolution_cannot_escape(self):
        # Resolving an existing workspace file must never qualify through a textual root prefix.
        with patch.object(entry, "SESSION_ROOT", ROOT / "tests-other"):
            with self.assertRaisesRegex(CaptureError, "source-untrusted"):
                entry._trusted_segment(Path(__file__))

    def _archive(self):
        raw = _desktop_segment(*lines())
        event = binding(raw)
        manifest = build_capture_manifest(task_id="synthetic-task", delivery_id="synthetic-delivery",
            artifact_claim={}, review_claim={}, apply_attempt_id="synthetic-attempt",
            protocol="desktop", source_files=[], bindings=event)
        manifest["archive_digest"] = sha256_hex(raw)
        return manifest, raw

    def test_entire_context_round_trip(self):
        manifest, raw = self._archive()
        verify_capture_archive(manifest, archive_raw=raw)

    def test_meta_context_tamper_refused(self):
        manifest, raw = self._archive()
        changed = raw.replace(b"SynthDesktop", b"ChangedDesktop")
        with self.assertRaisesRegex(CaptureError, "archive-tampered"):
            verify_capture_archive(manifest, archive_raw=changed)

    def test_manifest_workdir_must_match_raw_cwd(self):
        manifest, raw = self._archive()
        manifest["event"]["workdir"] = "D:/foreign-workspace"
        with self.assertRaisesRegex(CaptureError, "cwd-mismatch"):
            verify_capture_archive(manifest, archive_raw=raw)

    def test_read_stability_failure_preserves_no_archive(self):
        with tempfile.TemporaryDirectory(dir=ROOT / "data") as tmp:
            path = Path(tmp) / "sample.jsonl"
            path.write_bytes(b"old")
            with patch.object(entry.time, "sleep", side_effect=lambda _: path.write_bytes(b"new")):
                with self.assertRaisesRegex(CaptureError, "segment-unstable"):
                    entry._read_stable(path)

    def test_read_limit_enforced_before_parsing(self):
        with tempfile.TemporaryDirectory(dir=ROOT / "data") as tmp:
            path = Path(tmp) / "sample.jsonl"
            path.write_bytes(b"12345")
            with patch.object(entry, "MAX_INPUT_BYTES", 4):
                with self.assertRaisesRegex(CaptureError, "input-too-large"):
                    entry._read_stable(path, delay_seconds=0)

    def test_untrusted_entry_source_creates_no_archive(self):
        with tempfile.TemporaryDirectory(dir=ROOT / "data") as tmp:
            archive = Path(tmp) / "must-not-exist"
            pin = Path(tmp) / "pin.js"; pin.write_text(CALL_SCRIPT)
            args = ["--protocol", "desktop", "--thread-id", THREAD,
                "--segment", str(Path(__file__)), "--item-id", ITEM, "--call-id", CALL,
                "--expect-argv-json", json.dumps(FIXED_ARGV), "--expect-workdir", WORKDIR,
                "--expect-exit", "0", "--expect-call-input-file", str(pin),
                "--task-id", "synthetic-task", "--delivery-id", "synthetic-delivery",
                "--apply-attempt-id", "synthetic-attempt", "--artifact-claim-json", "{}",
                "--review-claim-json", "{}", "--archive-dir", str(archive)]
            self.assertEqual(entry.main(args), 2)
            self.assertFalse(archive.exists())


if __name__ == "__main__":
    unittest.main()
