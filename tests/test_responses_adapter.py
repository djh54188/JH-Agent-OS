"""Offline tests for protocol mapping, budget limits and fail-closed review."""
import copy
import io
import json
import os
import unittest
import urllib.error
from unittest.mock import patch

from src.adapters import AdapterError, SchemaValidationError
from src.model_config import load_model_adapter
from src.responses_adapter import OpenAIResponsesAdapter

SCHEMA = {"type": "object", "properties": {"verdict": {"enum": ["pass", "revise"]}},
          "required": ["verdict"], "additionalProperties": False}
RESPONSE = {"id": "resp_test", "model": "muse-spark-1.3-contributor-free",
            "status": "completed",
            "output": [{"type": "message", "role": "assistant",
                        "content": [{"type": "output_text", "text": '{"verdict":"pass"}'}]}],
            "usage": {"input_tokens": 10, "output_tokens": 5, "total_tokens": 15}}


class TestResponsesAdapter(unittest.TestCase):
    def setUp(self):
        self.environment = patch.dict(os.environ, {"MUSE_TEST_KEY": "test-secret"})
        self.environment.start()
        self.addCleanup(self.environment.stop)
        self.adapter = OpenAIResponsesAdapter({
            "role": "reviewer", "model_id": RESPONSE["model"],
            "endpoint_url": "http://127.0.0.1:8787/v1",
            "api_key_env": "MUSE_TEST_KEY", "max_output_tokens": 131072,
            "reasoning_effort": "max",
        })

    def fake_response(self, payload):
        return patch.object(self.adapter._opener, "open",
                            return_value=io.BytesIO(json.dumps(payload).encode()))

    def test_responses_request_and_usage(self):
        with self.fake_response(RESPONSE) as opened:
            response = self.adapter.invoke_structured(
                [{"role": "user", "content": "Review"}], SCHEMA, max_output_tokens=2048)
        request = opened.call_args.args[0]
        body = json.loads(request.data)
        self.assertEqual(request.full_url, "http://127.0.0.1:8787/v1/responses")
        self.assertEqual(body["reasoning"]["effort"], "max")
        self.assertEqual(body["max_output_tokens"], 2048)
        self.assertEqual(body["text"]["format"]["type"], "json_schema")
        self.assertFalse(body["store"])
        self.assertNotIn("messages", body)
        self.assertNotIn("temperature", body)
        self.assertEqual(response.parsed_json, {"verdict": "pass"})
        self.assertEqual(response.total_tokens, 15)
        self.assertFalse(response.is_mock)

    def test_incomplete_or_failed_response_is_rejected(self):
        for status in ("incomplete", "failed", "queued", None):
            with self.subTest(status=status):
                raw = copy.deepcopy(RESPONSE)
                raw["status"] = status
                with self.fake_response(raw), self.assertRaises(AdapterError):
                    self.adapter.invoke([])

    def test_invalid_review_is_rejected(self):
        for text in ('not-json', '{"verdict":"unknown"}', '{"verdict":"pass","extra":true}'):
            with self.subTest(text=text):
                raw = copy.deepcopy(RESPONSE)
                raw["output"][0]["content"][0]["text"] = text
                with self.fake_response(raw), self.assertRaises(SchemaValidationError):
                    self.adapter.invoke_structured([], SCHEMA)

    def test_refusal_or_empty_output_is_rejected(self):
        for output in ([], [{"type": "message", "content": [
                {"type": "refusal", "refusal": "No"},
                {"type": "output_text", "text": '{"verdict":"pass"}'}]}]):
            with self.subTest(output=output):
                raw = copy.deepcopy(RESPONSE)
                raw["output"] = output
                with self.fake_response(raw), self.assertRaises(AdapterError):
                    self.adapter.invoke([])

    def test_missing_usage_stays_unknown(self):
        raw = copy.deepcopy(RESPONSE)
        raw.pop("usage")
        with self.fake_response(raw):
            response = self.adapter.invoke([])
        self.assertIsNone(response.total_tokens)
        self.assertIsNone(response.tokens_prompt)
        self.assertIsNone(response.tokens_completion)

    def test_output_budget_limit(self):
        with self.assertRaises(AdapterError):
            self.adapter.invoke([], max_output_tokens=131073)

    def test_http_error_does_not_expose_key_or_body(self):
        error = urllib.error.HTTPError("http://example", 401, "test-secret", {},
                                       io.BytesIO(b"test-secret"))
        with patch.object(self.adapter._opener, "open", side_effect=error):
            with self.assertRaises(AdapterError) as caught:
                self.adapter.invoke([])
        self.assertNotIn("test-secret", str(caught.exception))
        self.assertIn("401", str(caught.exception))

    def test_muse_profile_uses_responses(self):
        adapter = load_model_adapter("reviewer")
        self.assertIsInstance(adapter, OpenAIResponsesAdapter)
        self.assertEqual(adapter.model_id, RESPONSE["model"])
        self.assertEqual(adapter.max_output_tokens, 131072)
        self.assertEqual(adapter.reasoning_effort, "max")


if __name__ == "__main__":
    unittest.main()
