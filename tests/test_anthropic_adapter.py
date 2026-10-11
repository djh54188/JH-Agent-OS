"""Offline tests for the Anthropic Messages adapter (protocol mapping, budget limits,
fail-closed truncation/refusal, sanitized errors). No real network is contacted."""

import copy
import io
import json
import os
import unittest
import urllib.error
from unittest.mock import patch

from src.adapters import AdapterError, SchemaValidationError
from src.anthropic_adapter import AnthropicMessagesAdapter
from src.model_config import load_model_adapter

SCHEMA = {"type": "object", "properties": {"verdict": {"enum": ["pass", "revise"]}},
          "required": ["verdict"], "additionalProperties": False}
RESPONSE = {"id": "msg_test", "type": "message", "role": "assistant",
            "model": "gemini-3.8-flash-high", "stop_reason": "end_turn",
            "content": [{"type": "text", "text": '{"verdict":"pass"}'}],
            "usage": {"input_tokens": 10, "output_tokens": 5}}


class TestAnthropicAdapter(unittest.TestCase):
    def setUp(self):
        self.environment = patch.dict(os.environ, {"GEM_TEST_KEY": "test-secret"})
        self.environment.start()
        self.addCleanup(self.environment.stop)
        self.adapter = AnthropicMessagesAdapter({
            "role": "worker", "model_id": RESPONSE["model"],
            "endpoint_url": "http://localhost:8787/v1",
            "api_key_env": "GEM_TEST_KEY", "max_output_tokens": 64000,
            "timeout_seconds": 60,
        })

    def fake_response(self, payload):
        return patch.object(self.adapter._opener, "open",
                            return_value=io.BytesIO(json.dumps(payload).encode()))

    def test_messages_request_shape_and_usage(self):
        with self.fake_response(RESPONSE) as opened:
            response = self.adapter.invoke_structured(
                [{"role": "system", "content": "Be terse."},
                 {"role": "user", "content": "Review"}], SCHEMA, max_output_tokens=2048, timeout=17)
        request = opened.call_args.args[0]
        body = json.loads(request.data)
        self.assertEqual(request.full_url, "http://localhost:8787/v1/messages")
        self.assertEqual(request.get_header("X-api-key"), "test-secret")
        self.assertEqual(request.get_header("Anthropic-version"), "2023-06-01")
        self.assertEqual(body["model"], RESPONSE["model"])
        self.assertEqual(body["max_tokens"], 2048)
        self.assertTrue((body.get("system") or "").endswith("Be terse."))
        self.assertIn("JSON Schema", body.get("system") or "")
        self.assertEqual([m["role"] for m in body["messages"]], ["user"])
        self.assertNotIn("temperature", body)
        self.assertFalse(body["stream"])
        self.assertEqual(opened.call_args.kwargs["timeout"], 17)
        self.assertEqual(response.parsed_json, {"verdict": "pass"})
        self.assertEqual(response.total_tokens, 15)  # input + output summed
        self.assertFalse(response.is_mock)

    def test_temperature_forwarded_when_configured(self):
        self.adapter.temperature = 0.2
        with self.fake_response(RESPONSE) as opened:
            self.adapter.invoke([{"role": "user", "content": "ping"}])
        body = json.loads(opened.call_args.args[0].data)
        self.assertEqual(body["temperature"], 0.2)

    def test_truncation_and_refusal_rejected(self):
        # Each specific guard has its own error class text; the unknown-stop
        # backstop must not be able to mask a removed specific guard.
        for stop, fragment in (("max_tokens", "truncated"), ("refusal", "refused"), ("tool_use", "unexpectedly")):
            with self.subTest(stop=stop):
                raw = copy.deepcopy(RESPONSE)
                raw["stop_reason"] = stop
                with self.fake_response(raw), self.assertRaisesRegex(AdapterError, fragment):
                    self.adapter.invoke([{"role": "user", "content": "ping"}])

    def test_invalid_review_is_rejected(self):
        for text in ('not-json', '{"verdict":"unknown"}', '{"verdict":"pass","extra":true}'):
            with self.subTest(text=text):
                raw = copy.deepcopy(RESPONSE)
                raw["content"] = [{"type": "text", "text": text}]
                with self.fake_response(raw), self.assertRaises(SchemaValidationError):
                    self.adapter.invoke_structured([{"role": "user", "content": "Review"}], SCHEMA)

    def test_empty_output_rejected(self):
        for content in ([], [{"type": "text", "text": "   "}]):
            with self.subTest(content=content):
                raw = copy.deepcopy(RESPONSE)
                raw["content"] = content
                with self.fake_response(raw), self.assertRaises(AdapterError):
                    self.adapter.invoke([{"role": "user", "content": "ping"}])

    def test_missing_usage_stays_unknown(self):
        raw = copy.deepcopy(RESPONSE)
        raw.pop("usage")
        with self.fake_response(raw):
            response = self.adapter.invoke([{"role": "user", "content": "ping"}])
        self.assertIsNone(response.total_tokens)
        self.assertIsNone(response.tokens_prompt)
        self.assertIsNone(response.tokens_completion)

    def test_usage_validation_matrix(self):
        """Rectification #2: illegal usage can never become a trusted total."""
        cases = [
            # (usage payload, expectation) — "unknown" | ("total", n) | "raise"
            (None, "unknown"),
            ({}, "unknown"),
            ({"input_tokens": 10, "output_tokens": 5}, ("total", 15)),
            ({"input_tokens": 0, "output_tokens": 0}, ("total", 0)),  # legal zero values
            ({"input_tokens": 10}, "unknown"),  # partially missing
            ({"output_tokens": 5}, "unknown"),
            ({"input_tokens": True, "output_tokens": True}, "raise"),  # bools must not sum to 2
            ({"input_tokens": -10, "output_tokens": 20}, "raise"),  # negatives must not net to 10
            ({"input_tokens": "10", "output_tokens": 5}, "raise"),  # strings
            ({"input_tokens": 1.5, "output_tokens": 2}, "raise"),
            ({"input_tokens": True, "output_tokens": None}, "raise"),
            ({"total_tokens": 99}, "unknown"),  # fallback total must not mask missing counters
        ]
        for usage, expectation in cases:
            with self.subTest(usage=usage):
                raw = copy.deepcopy(RESPONSE)
                if usage is None:
                    raw.pop("usage")
                else:
                    raw["usage"] = usage
                with self.fake_response(raw):
                    if expectation == "raise":
                        with self.assertRaisesRegex(AdapterError, "invalid token usage"):
                            self.adapter.invoke([{"role": "user", "content": "ping"}])
                    elif expectation == "unknown":
                        response = self.adapter.invoke([{"role": "user", "content": "ping"}])
                        self.assertIsNone(response.total_tokens)
                        self.assertIsNone(response.tokens_prompt)
                        self.assertIsNone(response.tokens_completion)
                    else:
                        response = self.adapter.invoke([{"role": "user", "content": "ping"}])
                        self.assertEqual(response.total_tokens, expectation[1])

    def test_output_budget_limit(self):
        with self.assertRaises(AdapterError):
            self.adapter.invoke([{"role": "user", "content": "ping"}], max_output_tokens=64001)

    def test_http_error_does_not_expose_key_or_body(self):
        error = urllib.error.HTTPError("http://example", 401, "test-secret", {},
                                       io.BytesIO(b"test-secret"))
        with patch.object(self.adapter._opener, "open", side_effect=error):
            with self.assertRaises(AdapterError) as caught:
                self.adapter.invoke([{"role": "user", "content": "ping"}])
        self.assertNotIn("test-secret", str(caught.exception))
        self.assertIn("401", str(caught.exception))

    def test_gemini_profile_uses_anthropic_messages(self):
        adapter = load_model_adapter("worker")
        self.assertIsInstance(adapter, AnthropicMessagesAdapter)
        self.assertEqual(adapter.model_id, "gemini-3.8-flash-high")
        self.assertEqual(adapter.max_output_tokens, 64000)
        self.assertEqual(adapter.endpoint_url, "http://localhost:8787/v1")

    def test_system_only_messages_rejected(self):
        with self.assertRaises(AdapterError):
            self.adapter.invoke([{"role": "system", "content": "only system"}])


if __name__ == "__main__":
    unittest.main()
