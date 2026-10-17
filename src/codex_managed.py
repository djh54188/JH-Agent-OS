"""
JH-Agent OS Managed Async Codex Adapter v1.0.0
Enforces approved Phase 2 operational parameters:
- Single call timeout: 300s
- Task total deadline: 600s
- Status poll interval: 2.0s
- No-event warning threshold: 180.0s
- Max Codex concurrency: 1
- Auto-restart retries: 0 (never automatically restarts failed/timed-out Codex calls)
- All waits bounded by remaining task budget
- Missing token counts recorded as None (unknown), never defaulted to 0
"""

from dataclasses import dataclass
import json
import os
import queue
import shutil
import subprocess
import tempfile
import threading
import time
from typing import Any, Callable, Dict, List, Optional

from jsonschema import Draft7Validator, FormatChecker, ValidationError
from src.adapters import AdapterError, SchemaValidationError


@dataclass
class ManagedCodexConfig:
    workspace_root: str = "D:/JH-Agent OS"
    model_id: str = "gpt-6.1-sol"
    sandbox_mode: str = "read-only"
    call_timeout_seconds: float = 300.0
    task_deadline_seconds: float = 600.0
    poll_interval_seconds: float = 2.0
    no_event_warning_seconds: float = 180.0
    max_concurrency: int = 1
    auto_restart_retries: int = 0


class ManagedCodexAdapter:
    """
    Managed asynchronous wrapper for OpenAI Codex CLI (`codex exec --json`).
    Provides concurrency gating, periodic polling, no-event threshold warnings,
    remaining-budget timeout capping, and unconfirmed side-effect tracking.
    """

    def __init__(
        self,
        config: Optional[ManagedCodexConfig] = None,
        *,
        process_runner: Optional[Callable[..., Any]] = None,
        process_factory: Optional[Callable[..., Any]] = None,
        clock: Optional[Callable[[], float]] = None,
        sleep_fn: Optional[Callable[[float], None]] = None,
    ):
        self.config = config or ManagedCodexConfig()
        if self.config.max_concurrency < 1:
            raise ValueError("max_concurrency must be >= 1")
        if self.config.auto_restart_retries != 0:
            raise ValueError("auto_restart_retries must be 0 per Phase 2 architectural ruling")
        self._semaphore = threading.BoundedSemaphore(self.config.max_concurrency)
        self._process_factory = process_runner or process_factory
        self._clock = clock or time.monotonic
        self._sleep = sleep_fn or time.sleep
        self.codex_bin = shutil.which("codex") or "codex"

    def compute_effective_timeout(self, remaining_budget_seconds: Optional[float]) -> float:
        base_limit = min(self.config.call_timeout_seconds, self.config.task_deadline_seconds)
        if remaining_budget_seconds is None:
            return float(base_limit)
        return float(min(base_limit, remaining_budget_seconds))

    def run_async_turn(
        self,
        prompt: str,
        *,
        remaining_budget_seconds: Optional[float] = None,
        output_schema: Optional[Dict[str, Any]] = None,
        on_event: Optional[Callable[[Dict[str, Any]], None]] = None,
        cancel_check: Optional[Callable[[], bool]] = None,
    ) -> Dict[str, Any]:
        effective_timeout = self.compute_effective_timeout(remaining_budget_seconds)
        if effective_timeout <= 0:
            raise AdapterError("Remaining task budget is exhausted (<= 0s); refusing to start Codex call")

        start_time = self._clock()
        acquired = self._semaphore.acquire(timeout=max(0.01, effective_timeout))
        if not acquired:
            raise AdapterError("Codex concurrency limit (1) reached and wait exceeded remaining budget")

        tf_path: Optional[str] = None
        try:
            elapsed_waiting = max(0.0, self._clock() - start_time)
            run_budget = effective_timeout - elapsed_waiting
            if run_budget <= 0:
                raise AdapterError("Remaining task budget exhausted while waiting for Codex concurrency slot")

            if output_schema is not None:
                tf = tempfile.NamedTemporaryFile("w", suffix=".json", delete=False, encoding="utf-8")
                json.dump(output_schema, tf, ensure_ascii=False)
                tf_path = tf.name
                tf.close()

            cmd = [
                self.codex_bin,
                "exec",
                "--ephemeral",
                "--skip-git-repo-check",
                "--sandbox",
                self.config.sandbox_mode,
                "-C",
                self.config.workspace_root,
                "--json",
            ]
            if tf_path:
                cmd.extend(["--output-schema", tf_path])
            cmd.append(prompt)

            return self._poll_process(
                cmd=cmd,
                timeout_budget=run_budget,
                output_schema=output_schema,
                on_event=on_event,
                cancel_check=cancel_check,
            )
        finally:
            self._semaphore.release()
            if tf_path and os.path.exists(tf_path):
                try:
                    os.remove(tf_path)
                except OSError:
                    pass

    def _poll_process(
        self,
        *,
        cmd: List[str],
        timeout_budget: float,
        output_schema: Optional[Dict[str, Any]],
        on_event: Optional[Callable[[Dict[str, Any]], None]],
        cancel_check: Optional[Callable[[], bool]],
    ) -> Dict[str, Any]:
        start_clock = self._clock()
        last_event_clock = start_clock
        warning_emitted = False
        warnings: List[str] = []
        events_count = 0
        collected_texts: List[str] = []
        last_agent_text = ""
        tokens_prompt: Optional[int] = None
        tokens_completion: Optional[int] = None
        total_tokens: Optional[int] = None
        is_write_mode = self.config.sandbox_mode != "read-only"

        if self._process_factory is not None:
            proc = self._process_factory(cmd)
        else:
            proc = subprocess.Popen(
                cmd,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                shell=(os.name == "nt"),
            )

        line_queue: "queue.Queue[Optional[str]]" = queue.Queue()
        reader_thread: Optional[threading.Thread] = None

        if isinstance(getattr(proc, "stdout", None), (list, tuple)):
            for raw_line in proc.stdout:
                line_queue.put(raw_line)
            line_queue.put(None)
        elif not callable(getattr(proc, "read_available_lines", None)):
            started_evt = threading.Event()

            def _reader() -> None:
                started_evt.set()
                try:
                    if proc.stdout is not None:
                        for raw_line in proc.stdout:
                            line_queue.put(raw_line)
                finally:
                    line_queue.put(None)

            reader_thread = threading.Thread(target=_reader, daemon=True)
            reader_thread.start()
            started_evt.wait(timeout=0.5)

        eof_reached = False
        status = "completed"

        def _consume_available_lines() -> None:
            nonlocal last_event_clock, events_count, last_agent_text, tokens_prompt, tokens_completion, total_tokens, eof_reached, warning_emitted
            if callable(getattr(proc, "read_available_lines", None)):
                for extra_line in proc.read_available_lines():
                    line_queue.put(extra_line)
            while True:
                try:
                    item_line = line_queue.get_nowait()
                except queue.Empty:
                    break
                if item_line is None:
                    eof_reached = True
                    break
                stripped = item_line.strip()
                if not stripped:
                    continue
                try:
                    ev = json.loads(stripped)
                except Exception:
                    continue
                last_event_clock = self._clock()
                warning_emitted = False
                events_count += 1
                if on_event is not None:
                    on_event(ev)
                ev_type = ev.get("type")
                if ev_type == "turn.completed":
                    usage = ev.get("usage") or {}
                    in_tok = usage.get("input_tokens")
                    out_tok = usage.get("output_tokens")
                    tokens_prompt = in_tok if isinstance(in_tok, int) else None
                    tokens_completion = out_tok if isinstance(out_tok, int) else None
                    if tokens_prompt is not None and tokens_completion is not None:
                        total_tokens = tokens_prompt + tokens_completion
                    else:
                        tot = usage.get("total_tokens")
                        total_tokens = tot if isinstance(tot, int) else None
                elif ev_type == "item.completed":
                    item = ev.get("item") or {}
                    if item.get("type") == "agent_message":
                        txt = item.get("text", "")
                        collected_texts.append(txt)
                        last_agent_text = txt

        while True:
            _consume_available_lines()

            if cancel_check is not None and cancel_check():
                try:
                    proc.kill()
                except Exception:
                    pass
                status = "cancelled"
                break

            now = self._clock()
            elapsed = now - start_clock
            idle_seconds = now - last_event_clock

            if not warning_emitted and idle_seconds >= self.config.no_event_warning_seconds:
                warning_emitted = True
                warn_msg = f"no_event_warning: No Codex JSONL event received for {int(idle_seconds)}s (threshold={int(self.config.no_event_warning_seconds)}s)"
                warnings.append(warn_msg)
                if on_event is not None:
                    on_event({"type": "no_event_warning", "idle_seconds": idle_seconds, "message": warn_msg})

            if elapsed >= timeout_budget:
                try:
                    proc.kill()
                except Exception:
                    pass
                status = "timeout"
                break

            ret = proc.poll()
            if ret is not None:
                if reader_thread is not None and reader_thread.is_alive():
                    reader_thread.join(timeout=0.2)
                _consume_available_lines()
                if eof_reached or callable(getattr(proc, "read_available_lines", None)):
                    if ret != 0:
                        status = "failed"
                    break

            remaining = max(0.0, timeout_budget - elapsed)
            step_sleep = min(self.config.poll_interval_seconds, remaining)
            if step_sleep > 0:
                self._sleep(step_sleep)

        duration_ms = int(max(0.0, self._clock() - start_clock) * 1000)
        final_content = last_agent_text if last_agent_text else "\n".join(collected_texts)
        parsed_json = None

        if status == "completed" and output_schema is not None:
            cleaned = final_content.strip()
            if cleaned.startswith("```json"):
                cleaned = cleaned[7:]
            elif cleaned.startswith("```"):
                cleaned = cleaned[3:]
            if cleaned.endswith("```"):
                cleaned = cleaned[:-3]
            cleaned = cleaned.strip()
            try:
                parsed_json = json.loads(cleaned)
                Draft7Validator(output_schema, format_checker=FormatChecker()).validate(parsed_json)
            except (ValueError, ValidationError) as exc:
                raise SchemaValidationError(f"Codex output failed JSON Schema validation: {exc}") from None

        side_effect_unconfirmed = bool(is_write_mode and status in {"timeout", "cancelled", "failed"})

        return {
            "status": status,
            "content": final_content,
            "parsed_json": parsed_json,
            "tokens_prompt": tokens_prompt,
            "tokens_completion": tokens_completion,
            "total_tokens": total_tokens,
            "latency_ms": duration_ms,
            "warnings": warnings,
            "events_count": events_count,
            "auto_restarts_attempted": 0,
            "side_effect_unconfirmed": side_effect_unconfirmed,
        }
