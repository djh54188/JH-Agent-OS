"""Anthropic Messages-protocol adapter for the configured Gemini worker.

User-ruled access path (2026-10-11): local proxy http://localhost:8787/v1
speaking the Anthropic Messages protocol (POST /v1/messages) for model
gemini-3.8-flash-high. The official Google generateContent endpoint is retired
for the JH Runtime worker; only this adapter layer changes — task/review/artifact
schemas and control-layer contracts are unchanged.

Protocol notes:
- API key travels in the `x-api-key` header (env var GEMINI_API_KEY), plus the
  required `anthropic-version` header.
- The system prompt goes into the top-level `system` field, never as a
  role="system" message.
- `max_tokens` is required by the protocol; it maps from the per-call output
  budget computed by the control layer and is capped by the configured limit.
- No native JSON-schema enforcement exists in this protocol; the schema travels
  in the prompt and Draft-07 validation stays local and authoritative.
- stop_reason mapping: "max_tokens" -> truncation error, "refusal" -> refusal
  error, "end_turn"/"stop_sequence" -> success. Unknown tool_use cannot occur
  (no tools are sent) and is rejected as an unexpected stop.
- Token usage: Anthropic reports input_tokens/output_tokens; total is their sum
  when both are present, otherwise None (unknown) — never defaulted to zero.
"""

import json
import os
import time
import urllib.error
import urllib.request
from typing import Any, Dict, List
from urllib.parse import urlsplit

from jsonschema import Draft7Validator, FormatChecker, ValidationError

from src.adapters import (
    AdapterError,
    AdapterResponse,
    CredentialMissingError,
    ModelAdapter,
    SchemaValidationError,
    invocation_timeout,
)


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


class AnthropicMessagesAdapter(ModelAdapter):
    """Call /v1/messages without protocol fallback or automatic retries."""

    ANTHROPIC_VERSION = "2023-06-01"

    def __init__(self, config: Dict[str, Any]):
        super().__init__(config)
        self.endpoint_url = config["endpoint_url"].rstrip("/")
        self.api_key_env = config.get("api_key_env", "GEMINI_API_KEY")
        self.max_output_tokens = config.get("max_output_tokens", 64000)
        self.temperature = config.get("temperature")
        handlers = [_NoRedirect()]
        if urlsplit(self.endpoint_url).hostname in {"127.0.0.1", "localhost", "::1"}:
            handlers.append(urllib.request.ProxyHandler({}))
        self._opener = urllib.request.build_opener(*handlers)

    def _get_api_key(self):
        key = os.environ.get(self.api_key_env, "").strip()
        if not key:
            raise CredentialMissingError(f"Missing environment variable '{self.api_key_env}'")
        return key

    def _request(self, route, payload=None, timeout=None):
        headers = {
            "x-api-key": self._get_api_key(),
            "anthropic-version": self.ANTHROPIC_VERSION,
        }
        body = None
        if payload is not None:
            headers["Content-Type"] = "application/json"
            body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        request = urllib.request.Request(self.endpoint_url + route, data=body, headers=headers)
        try:
            with self._opener.open(request, timeout=invocation_timeout(self.timeout_seconds, timeout)) as response:
                raw = json.load(response)
        except urllib.error.HTTPError as error:
            # Error bodies/URLs can echo credentials or prompts; do not include them.
            error.close()
            raise AdapterError(f"Messages endpoint returned HTTP {error.code}") from None
        except (OSError, ValueError, urllib.error.URLError):
            raise AdapterError("Messages request failed or returned invalid JSON") from None
        if not isinstance(raw, dict):
            raise AdapterError("Messages endpoint returned a non-object")
        return raw

    def check_health(self):
        try:
            raw = self._request("/models", timeout=min(10, self.timeout_seconds))
            models = [item.get("id") for item in raw.get("data", []) if isinstance(item, dict)]
            present = self.model_id in models
            return {"healthy": present, "model_available": present, "models": models,
                    "error": None if present else "Configured model not listed"}
        except AdapterError as error:
            return {"healthy": False, "model_available": False, "models": [], "error": str(error)}

    def invoke(self, messages: List[Dict[str, str]], **kwargs) -> AdapterResponse:
        max_output = kwargs.get("max_output_tokens", self.max_output_tokens)
        if isinstance(max_output, bool) or not isinstance(max_output, int) or not 1 <= max_output <= self.max_output_tokens:
            raise AdapterError("Requested output budget exceeds the configured limit")
        timeout = invocation_timeout(self.timeout_seconds, kwargs.get("timeout"))

        system_parts = [m["content"] for m in messages if m.get("role") == "system"]
        convo = [{"role": "user" if m.get("role") != "assistant" else "assistant", "content": m["content"]}
                 for m in messages if m.get("role") != "system"]
        if not convo:
            raise AdapterError("Messages request needs at least one non-system message")
        payload: Dict[str, Any] = {
            "model": self.model_id,
            "max_tokens": max_output,
            "messages": convo,
            "stream": False,
        }
        if system_parts:
            payload["system"] = "\n\n".join(system_parts)
        if self.temperature is not None:
            payload["temperature"] = self.temperature

        started = time.perf_counter()
        raw = self._request("/messages", payload, timeout=kwargs.get("timeout"))
        latency_ms = int((time.perf_counter() - started) * 1000)

        if raw.get("type") not in (None, "message") or raw.get("role", "assistant") != "assistant":
            raise AdapterError("Messages endpoint returned an unexpected response type")
        stop_reason = raw.get("stop_reason")
        if stop_reason == "max_tokens":
            raise AdapterError("Provider truncated the output (stop_reason=max_tokens); result is incomplete")
        if stop_reason == "refusal":
            raise AdapterError("Provider refused the request (stop_reason=refusal)")
        if stop_reason not in ("end_turn", "stop_sequence", None):
            raise AdapterError(f"Provider stopped unexpectedly (stop_reason={stop_reason})")
        if raw.get("type") == "error":
            raise AdapterError("Messages endpoint returned an error object")

        texts = []
        for part in raw.get("content") or []:
            if not isinstance(part, dict):
                continue
            if part.get("type") == "text":
                texts.append(part.get("text", ""))
        content = "".join(texts)
        if not content.strip():
            raise AdapterError("Messages generation has no assistant text")

        usage = raw.get("usage") or {}
        input_tokens = usage.get("input_tokens")
        output_tokens = usage.get("output_tokens")
        if isinstance(input_tokens, int) and isinstance(output_tokens, int):
            total_tokens: Any = input_tokens + output_tokens
        else:
            total_tokens = usage.get("total_tokens")  # None stays unknown
        return AdapterResponse(
            content=content, parsed_json=None,
            model_id=raw.get("model") or self.model_id,
            tokens_prompt=input_tokens, tokens_completion=output_tokens,
            total_tokens=total_tokens, latency_ms=latency_ms, is_mock=False,
            raw_response={"response_id": raw.get("id"), "stop_reason": stop_reason,
                          "requested_model": self.model_id, "response_model": raw.get("model")},
        )

    def invoke_structured(self, messages, schema, **kwargs):
        Draft7Validator.check_schema(schema)
        schema_prompt = ("Return only a JSON object matching the supplied JSON Schema. "
                         "Do not include markdown fences.\n" + json.dumps(schema, ensure_ascii=False))
        augmented = [{"role": "system", "content": schema_prompt}] + list(messages)
        response = self.invoke(augmented, **kwargs)
        try:
            parsed = json.loads(response.content)
            Draft7Validator(schema, format_checker=FormatChecker()).validate(parsed)
        except (ValueError, ValidationError):
            raise SchemaValidationError("Messages output does not match the requested JSON Schema") from None
        response.parsed_json = parsed
        return response
