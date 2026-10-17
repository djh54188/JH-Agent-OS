"""Responses-protocol adapter for the configured Muse reviewer."""
import json
import os
import time
import urllib.error
import urllib.request
from typing import Any, Dict, List
from urllib.parse import urlsplit

from jsonschema import Draft7Validator, FormatChecker, ValidationError
from src.adapters import AdapterError, AdapterResponse, CredentialMissingError, ModelAdapter, SchemaValidationError, invocation_timeout


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


class OpenAIResponsesAdapter(ModelAdapter):
    """Call /responses without Chat Completions fallback or automatic retries."""

    def __init__(self, config: Dict[str, Any]):
        super().__init__(config)
        self.endpoint_url = config["endpoint_url"].rstrip("/")
        self.api_key_env = config.get("api_key_env", "MUSE_API_KEY")
        self.max_output_tokens = config.get("max_output_tokens", 131072)
        self.reasoning_effort = config.get("reasoning_effort", "max")
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
        headers = {"Authorization": "Bearer " + self._get_api_key()}
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
            raise AdapterError(f"Responses endpoint returned HTTP {error.code}") from None
        except (OSError, ValueError, urllib.error.URLError):
            raise AdapterError("Responses request failed or returned invalid JSON") from None
        if not isinstance(raw, dict):
            raise AdapterError("Responses endpoint returned a non-object")
        return raw

    def check_health(self):
        try:
            raw = self._request("/models", timeout=min(10, self.timeout_seconds))
            models = [item.get("id") for item in raw.get("data", []) if isinstance(item, dict)]
            present = self.model_id in models
            return {"healthy": present, "model_available": present, "models": models, "error": None if present else "Configured model not listed"}
        except AdapterError as error:
            return {"healthy": False, "model_available": False, "models": [], "error": str(error)}

    def invoke(self, messages: List[Dict[str, str]], **kwargs):
        max_output = kwargs.get("max_output_tokens", self.max_output_tokens)
        if isinstance(max_output, bool) or not isinstance(max_output, int) or not 1 <= max_output <= self.max_output_tokens:
            raise AdapterError("Requested output budget exceeds the configured limit")
        payload = {
            "model": self.model_id,
            "input": messages,
            "reasoning": {"effort": self.reasoning_effort},
            "max_output_tokens": max_output,
            "stream": False,
            "store": False,
        }
        schema = kwargs.get("_output_schema")
        if schema is not None:
            payload["text"] = {"format": {
                "type": "json_schema", "name": "jh_review_result",
                "schema": schema, "strict": False,
            }}
        started = time.perf_counter()
        raw = self._request("/responses", payload, timeout=kwargs.get("timeout"))
        latency_ms = int((time.perf_counter() - started) * 1000)
        if raw.get("error") or raw.get("status") != "completed":
            raise AdapterError("Responses generation did not complete successfully")
        texts = []
        for item in raw.get("output", []):
            if item.get("type") != "message" or item.get("role", "assistant") != "assistant":
                continue
            if item.get("status", "completed") != "completed":
                raise AdapterError("Responses message is incomplete")
            for part in item.get("content", []):
                if part.get("type") == "refusal":
                    raise AdapterError("Responses model refused the request")
                if part.get("type") == "output_text":
                    texts.append(part.get("text", ""))
        content = "".join(texts)
        if not content.strip():
            raise AdapterError("Responses generation has no assistant text")
        usage = raw.get("usage") or {}
        return AdapterResponse(
            content=content, parsed_json=None, model_id=raw.get("model") or self.model_id,
            tokens_prompt=usage.get("input_tokens"),
            tokens_completion=usage.get("output_tokens"),
            total_tokens=usage.get("total_tokens"),
            latency_ms=latency_ms, is_mock=False,
            raw_response={"response_id": raw.get("id"), "status": raw.get("status"),
                          "requested_model": self.model_id, "response_model": raw.get("model")},
        )

    def invoke_structured(self, messages, schema, **kwargs):
        Draft7Validator.check_schema(schema)
        prompt = ("Return only a JSON object matching the supplied JSON Schema. "
                  "Do not include markdown fences.\n" + json.dumps(schema, ensure_ascii=False))
        augmented = [{"role": "system", "content": prompt}] + list(messages)
        response = self.invoke(augmented, _output_schema=schema, **kwargs)
        try:
            parsed = json.loads(response.content)
            Draft7Validator(schema, format_checker=FormatChecker()).validate(parsed)
        except (ValueError, ValidationError):
            raise SchemaValidationError("Responses output does not match the requested JSON Schema") from None
        response.parsed_json = parsed
        return response
