"""
Offline Unit Tests for ManagedCodexAdapter (Stage 2)
Covers:
1. Default parameters verification (300 / 600 / 2 / 180 / 1 / 0)
2. Normal JSONL event stream parsing and token extraction
3. Missing token usage preserved as None (never defaulted to 0)
4. Remaining task budget smaller than single-call timeout truncates wait and times out (and <= 0 raises AdapterError)
5. Exceeding no_event_warning_seconds (180s) emits no_event_warning with auto_restarts_attempted == 0
6. Mutual exclusion protection when max_concurrency == 1
7. Cancellation callback (cancel_check) terminates process and returns "cancelled"
8. Write-mode (workspace-write) timeout/kill marks side_effect_unconfirmed=True
"""

import json
import threading
import time
import unittest
from typing import Any, Callable, Dict, List, Optional, Tuple

from src.adapters import AdapterError, SchemaValidationError
from src.codex_managed import ManagedCodexAdapter, ManagedCodexConfig


class FakeClock:
    """Deterministic monotonic clock for testing 180s/300s thresholds offline."""

    def __init__(self, initial: float = 1000.0) -> None:
        self.current = initial
        self._lock = threading.Lock()

    def __call__(self) -> float:
        with self._lock:
            return self.current

    def sleep(self, seconds: float) -> None:
        with self._lock:
            self.current += max(0.0, float(seconds))


class ScriptedCodexProcess:
    """
    Fake process driven by a FakeClock timeline.
    Each scheduled item is (emit_at_elapsed_seconds, json_dict_or_raw_line).
    """

    def __init__(
        self,
        clock: FakeClock,
        scheduled_events: List[Tuple[float, Any]],
        exit_at_elapsed: Optional[float] = 10.0,
        exit_code: int = 0,
    ) -> None:
        self._clock = clock
        self._start_time = clock()
        self._events = list(scheduled_events)
        self._emitted_indices = set()
        self._exit_at_elapsed = exit_at_elapsed
        self._exit_code = exit_code
        self.killed = False
        self.terminated = False
        self.stdout = None
        self.stderr = []

    def read_available_lines(self) -> List[str]:
        if self.killed or self.terminated:
            return []
        elapsed = self._clock() - self._start_time
        out: List[str] = []
        for idx, (at_sec, payload) in enumerate(self._events):
            if idx not in self._emitted_indices and elapsed >= at_sec:
                self._emitted_indices.add(idx)
                if isinstance(payload, dict):
                    out.append(json.dumps(payload, ensure_ascii=False) + "\n")
                else:
                    out.append(str(payload) + "\n")
        return out

    def poll(self) -> Optional[int]:
        if self.killed or self.terminated:
            return -9
        if self._exit_at_elapsed is None:
            return None
        elapsed = self._clock() - self._start_time
        if elapsed >= self._exit_at_elapsed:
            return self._exit_code
        return None

    def kill(self) -> None:
        self.killed = True

    def terminate(self) -> None:
        self.terminated = True


class TestManagedCodexAdapter(unittest.TestCase):
    def test_1_default_approved_parameters(self) -> None:
        """1. Verify approved default parameters (300 / 600 / 2 / 180 / 1 / 0)."""
        cfg = ManagedCodexConfig()
        self.assertEqual(cfg.call_timeout_seconds, 300)
        self.assertEqual(cfg.task_deadline_seconds, 600)
        self.assertEqual(cfg.poll_interval_seconds, 2.0)
        self.assertEqual(cfg.no_event_warning_seconds, 180.0)
        self.assertEqual(cfg.max_concurrency, 1)
        self.assertEqual(cfg.auto_restart_retries, 0)

        # Verify non-zero auto_restart_retries is rejected
        with self.assertRaises(ValueError):
            ManagedCodexAdapter(ManagedCodexConfig(auto_restart_retries=1))

    def test_2_normal_jsonl_stream_and_token_extraction(self) -> None:
        """2. Verify normal JSONL event stream parsing, structured output, and token extraction."""
        clock = FakeClock()
        events = [
            (0.0, {"type": "thread.started", "thread_id": "th_001"}),
            (1.0, {"type": "turn.started"}),
            (
                2.0,
                {
                    "type": "item.completed",
                    "item": {
                        "id": "msg_1",
                        "type": "agent_message",
                        "text": '{"answer": "ok", "confidence": 0.95}',
                    },
                },
            ),
            (
                4.0,
                {
                    "type": "turn.completed",
                    "usage": {
                        "input_tokens": 140,
                        "output_tokens": 60,
                        "total_tokens": 200,
                    },
                },
            ),
        ]

        proc = ScriptedCodexProcess(clock, events, exit_at_elapsed=4.0, exit_code=0)
        received_events: List[Dict[str, Any]] = []

        adapter = ManagedCodexAdapter(
            ManagedCodexConfig(sandbox_mode="read-only"),
            process_runner=lambda cmd: proc,
            clock=clock,
            sleep_fn=clock.sleep,
        )

        schema = {
            "type": "object",
            "required": ["answer", "confidence"],
            "properties": {
                "answer": {"type": "string"},
                "confidence": {"type": "number"},
            },
        }
        result = adapter.run_async_turn(
            "Verify output",
            output_schema=schema,
            on_event=received_events.append,
        )

        self.assertEqual(result["status"], "completed")
        self.assertEqual(result["content"], '{"answer": "ok", "confidence": 0.95}')
        self.assertEqual(result["parsed_json"], {"answer": "ok", "confidence": 0.95})
        self.assertEqual(result["tokens_prompt"], 140)
        self.assertEqual(result["tokens_completion"], 60)
        self.assertEqual(result["total_tokens"], 200)
        self.assertEqual(result["events_count"], 4)
        self.assertEqual(len(received_events), 4)
        self.assertEqual(result["warnings"], [])
        self.assertEqual(result["auto_restarts_attempted"], 0)
        self.assertFalse(result["side_effect_unconfirmed"])

    def test_3_missing_tokens_preserved_as_none(self) -> None:
        """3. Verify missing token counts in turn.completed are recorded as None (never 0)."""
        clock = FakeClock()
        events = [
            (0.0, {"type": "turn.started"}),
            (
                2.0,
                {
                    "type": "item.completed",
                    "item": {"type": "agent_message", "text": "done without token usage"},
                },
            ),
            (2.0, {"type": "turn.completed"}),  # No usage field provided
        ]
        proc = ScriptedCodexProcess(clock, events, exit_at_elapsed=2.0, exit_code=0)
        adapter = ManagedCodexAdapter(
            process_runner=lambda cmd: proc,
            clock=clock,
            sleep_fn=clock.sleep,
        )

        result = adapter.run_async_turn("Test missing usage")
        self.assertEqual(result["status"], "completed")
        self.assertEqual(result["content"], "done without token usage")
        self.assertIsNone(result["tokens_prompt"])
        self.assertIsNone(result["tokens_completion"])
        self.assertIsNone(result["total_tokens"])

    def test_4_remaining_budget_truncation_and_exhaustion(self) -> None:
        """4. Verify remaining_budget_seconds truncates call timeout and <= 0 raises immediately."""
        clock = FakeClock()
        spawn_count = 0

        def make_hanging_proc(cmd: List[str]) -> ScriptedCodexProcess:
            nonlocal spawn_count
            spawn_count += 1
            return ScriptedCodexProcess(
                clock,
                scheduled_events=[(0.0, {"type": "turn.started"})],
                exit_at_elapsed=None,  # hangs indefinitely
            )

        adapter = ManagedCodexAdapter(
            ManagedCodexConfig(call_timeout_seconds=300, poll_interval_seconds=2.0),
            process_runner=make_hanging_proc,
            clock=clock,
            sleep_fn=clock.sleep,
        )

        # Case A: remaining_budget_seconds <= 0 must raise immediately without starting process
        with self.assertRaises(AdapterError):
            adapter.run_async_turn("Should not start", remaining_budget_seconds=0)
        with self.assertRaises(AdapterError):
            adapter.run_async_turn("Should not start negative", remaining_budget_seconds=-5.0)
        self.assertEqual(spawn_count, 0)

        # Case B: remaining_budget_seconds (12s) < call_timeout_seconds (300s) truncates wait to 12s
        start_ts = clock()
        result = adapter.run_async_turn("Truncated budget call", remaining_budget_seconds=12.0)
        elapsed = clock() - start_ts

        self.assertEqual(spawn_count, 1)
        self.assertEqual(result["status"], "timeout")
        self.assertAlmostEqual(elapsed, 12.0, places=2)
        self.assertEqual(result["auto_restarts_attempted"], 0)

    def test_5_no_event_warning_without_auto_restart(self) -> None:
        """5. Verify >180s silence emits no_event_warning and does not restart process."""
        clock = FakeClock()
        spawn_count = 0
        callback_events: List[Dict[str, Any]] = []

        # Event at 0s, then 200s of silence (>180s threshold), then completion at 200s
        events = [
            (0.0, {"type": "turn.started"}),
            (
                200.0,
                {
                    "type": "item.completed",
                    "item": {"type": "agent_message", "text": "completed after slow thinking"},
                },
            ),
            (
                200.0,
                {
                    "type": "turn.completed",
                    "usage": {"input_tokens": 50, "output_tokens": 25},
                },
            ),
        ]

        def factory(cmd: List[str]) -> ScriptedCodexProcess:
            nonlocal spawn_count
            spawn_count += 1
            return ScriptedCodexProcess(clock, events, exit_at_elapsed=200.0, exit_code=0)

        adapter = ManagedCodexAdapter(
            ManagedCodexConfig(
                call_timeout_seconds=300,
                no_event_warning_seconds=180.0,
                poll_interval_seconds=2.0,
            ),
            process_runner=factory,
            clock=clock,
            sleep_fn=clock.sleep,
        )

        result = adapter.run_async_turn("Slow reasoning task", on_event=callback_events.append)

        self.assertEqual(result["status"], "completed")
        self.assertEqual(spawn_count, 1)
        self.assertEqual(result["auto_restarts_attempted"], 0)
        self.assertEqual(len(result["warnings"]), 1)
        self.assertIn("no_event_warning", str(result["warnings"][0]))
        warning_callbacks = [e for e in callback_events if e.get("type") == "no_event_warning"]
        self.assertEqual(len(warning_callbacks), 1)
        self.assertGreaterEqual(warning_callbacks[0]["idle_seconds"], 180.0)

    def test_6_concurrency_limit_mutex_protection(self) -> None:
        """6. Verify max_concurrency=1 enforces mutual exclusion and respects budget while waiting."""
        active_now = 0
        max_concurrent = 0
        lock = threading.Lock()

        class RealTimeShortProcess:
            def __init__(self, duration_sec: float, label: str) -> None:
                self._start = time.monotonic()
                self._duration = duration_sec
                self._label = label
                self._emitted = False
                self.stdout = None
                self.stderr = []

            def read_available_lines(self) -> List[str]:
                if not self._emitted and (time.monotonic() - self._start) >= self._duration:
                    self._emitted = True
                    return [
                        json.dumps(
                            {
                                "type": "item.completed",
                                "item": {"type": "agent_message", "text": self._label},
                            }
                        ),
                        json.dumps(
                            {
                                "type": "turn.completed",
                                "usage": {"input_tokens": 10, "output_tokens": 5},
                            }
                        ),
                    ]
                return []

            def poll(self) -> Optional[int]:
                if (time.monotonic() - self._start) >= self._duration:
                    return 0
                return None

            def kill(self) -> None:
                pass

        def runner(cmd: List[str]) -> RealTimeShortProcess:
            nonlocal active_now, max_concurrent
            with lock:
                active_now += 1
                if active_now > max_concurrent:
                    max_concurrent = active_now

            proc = RealTimeShortProcess(0.08, cmd[-1])
            orig_poll = proc.poll

            def wrapped_poll() -> Optional[int]:
                nonlocal active_now
                res = orig_poll()
                if res is not None:
                    with lock:
                        active_now = max(0, active_now - 1)
                return res

            proc.poll = wrapped_poll  # type: ignore[method-assign]
            return proc

        adapter = ManagedCodexAdapter(
            ManagedCodexConfig(
                max_concurrency=1,
                poll_interval_seconds=0.01,
                call_timeout_seconds=5.0,
            ),
            process_runner=runner,
        )

        results: List[Dict[str, Any]] = []

        def worker(prompt_text: str) -> None:
            res = adapter.run_async_turn(prompt_text, remaining_budget_seconds=2.0)
            results.append(res)

        t1 = threading.Thread(target=worker, args=("call-1",))
        t2 = threading.Thread(target=worker, args=("call-2",))
        t1.start()
        t2.start()
        t1.join()
        t2.join()

        self.assertEqual(len(results), 2)
        self.assertTrue(all(r["status"] == "completed" for r in results))
        self.assertEqual(max_concurrent, 1)

    def test_7_cancel_check_terminates_process(self) -> None:
        """7. Verify cancel_check callback terminates process and returns status='cancelled'."""
        clock = FakeClock()
        proc = ScriptedCodexProcess(
            clock,
            scheduled_events=[(0.0, {"type": "turn.started"})],
            exit_at_elapsed=None,
        )

        adapter = ManagedCodexAdapter(
            ManagedCodexConfig(poll_interval_seconds=2.0, sandbox_mode="read-only"),
            process_runner=lambda cmd: proc,
            clock=clock,
            sleep_fn=clock.sleep,
        )

        start_time = clock()
        result = adapter.run_async_turn(
            "Cancellable task",
            cancel_check=lambda: (clock() - start_time) >= 6.0,
        )

        self.assertEqual(result["status"], "cancelled")
        self.assertTrue(proc.killed or proc.terminated)
        self.assertEqual(result["auto_restarts_attempted"], 0)
        self.assertFalse(result["side_effect_unconfirmed"])

    def test_8_write_mode_timeout_marks_side_effect_unconfirmed(self) -> None:
        """8. Verify workspace-write timeout/kill marks side_effect_unconfirmed=True."""
        clock = FakeClock()
        proc = ScriptedCodexProcess(
            clock,
            scheduled_events=[(0.0, {"type": "turn.started"})],
            exit_at_elapsed=None,
        )

        adapter = ManagedCodexAdapter(
            ManagedCodexConfig(
                sandbox_mode="workspace-write",
                call_timeout_seconds=300,
                poll_interval_seconds=2.0,
            ),
            process_runner=lambda cmd: proc,
            clock=clock,
            sleep_fn=clock.sleep,
        )

        result = adapter.run_async_turn("Mutating workspace task", remaining_budget_seconds=10.0)
        self.assertEqual(result["status"], "timeout")
        self.assertTrue(proc.killed or proc.terminated)
        self.assertTrue(result["side_effect_unconfirmed"])
        self.assertEqual(result["auto_restarts_attempted"], 0)


if __name__ == "__main__":
    unittest.main()
