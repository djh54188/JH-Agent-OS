"""Verify that isolated in-memory defects are detected by Responses guard tests."""
import importlib.util
import io
import json
from pathlib import Path
import sys
import types
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
spec = importlib.util.spec_from_file_location("muse_guard_tests", ROOT / "tests/test_responses_adapter.py")
tests = importlib.util.module_from_spec(spec)
spec.loader.exec_module(tests)
source = (ROOT / "src/responses_adapter.py").read_text(encoding="utf-8")

mutations = [
    ("route", 'raw = self._request("/responses", payload,',
     'raw = self._request("/chat/completions", payload,', "test_responses_request_and_usage"),
    ("status", 'if raw.get("error") or raw.get("status") != "completed":',
     "if False:", "test_incomplete_or_failed_response_is_rejected"),
    ("schema", "Draft7Validator(schema, format_checker=FormatChecker()).validate(parsed)",
     "pass", "test_invalid_review_is_rejected"),
    ("refusal", 'if part.get("type") == "refusal":',
     "if False:", "test_refusal_or_empty_output_is_rejected"),
    ("empty_output", "if not content.strip():",
     "if False:", "test_refusal_or_empty_output_is_rejected"),
    ("usage", 'total_tokens=usage.get("total_tokens"),',
     'total_tokens=usage.get("total_tokens", 0),', "test_missing_usage_stays_unknown"),
    ("budget", "if isinstance(max_output, bool) or not isinstance(max_output, int) or not 1 <= max_output <= self.max_output_tokens:",
     "if False:", "test_output_budget_limit"),
    ("redaction", 'f"Responses endpoint returned HTTP {error.code}"',
     'f"Responses endpoint returned {error}"', "test_http_error_does_not_expose_key_or_body"),
]
records = []
for name, original, replacement, test_name in mutations:
    if source.count(original) != 1:
        raise RuntimeError("Mutation target is ambiguous: " + name)
    module = types.ModuleType("src._muse_mutant")
    exec(compile(source.replace(original, replacement, 1), str(ROOT / "src/responses_adapter.py"), "exec"), module.__dict__)
    mutated_class = module.OpenAIResponsesAdapter
    if name == "budget":
        # The intentionally broken budget guard must never cause a real network call.
        mutated_class._request = lambda *args, **kwargs: tests.RESPONSE
    suite = unittest.TestSuite([tests.TestResponsesAdapter(test_name)])
    with patch.object(tests, "OpenAIResponsesAdapter", mutated_class):
        result = unittest.TextTestRunner(stream=io.StringIO()).run(suite)
    records.append({"guard": name, "detected": bool(result.failures), "unexpected_errors": len(result.errors)})
passed = all(record["detected"] and not record["unexpected_errors"] for record in records)
print(json.dumps({"mutations": len(records), "passed": passed, "results": records}))
raise SystemExit(0 if passed else 1)
