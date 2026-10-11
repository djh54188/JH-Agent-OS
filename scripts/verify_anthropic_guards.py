"""Mutation guard verification for the Anthropic Messages adapter (memory-isolated mutants).

Mirrors scripts/verify_responses_guards.py: each mutation breaks one protocol
guarantee; the matching test in tests/test_anthropic_adapter.py must turn red.
"""

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

spec = importlib.util.spec_from_file_location("anthropic_tests", ROOT / "tests/test_anthropic_adapter.py")
anthropic_tests = importlib.util.module_from_spec(spec)
spec.loader.exec_module(anthropic_tests)

source = (ROOT / "src/anthropic_adapter.py").read_text(encoding="utf-8")

MUTATIONS = [
    ("route", 'raw = self._request("/messages", payload, timeout=kwargs.get("timeout"))',
     'raw = self._request("/chat/completions", payload, timeout=kwargs.get("timeout"))',
     "test_messages_request_shape_and_usage"),
    ("system_mapping", '        if system_parts:\n            payload["system"] = "\\n\\n".join(system_parts)',
     '        if False:\n            payload["system"] = "\\n\\n".join(system_parts)',
     "test_messages_request_shape_and_usage"),
    ("truncation", 'if stop_reason == "max_tokens":',
     "if False:", "test_truncation_and_refusal_rejected"),
    ("refusal", 'if stop_reason == "refusal":',
     "if False:", "test_truncation_and_refusal_rejected"),
    ("schema", "Draft7Validator(schema, format_checker=FormatChecker()).validate(parsed)",
     "pass", "test_invalid_review_is_rejected"),
    ("empty_output", "if not content.strip():",
     "if False:", "test_empty_output_rejected"),
    ("usage", "            total_tokens = usage.get(\"total_tokens\")  # None stays unknown",
     "            total_tokens = usage.get(\"total_tokens\", 0)", "test_missing_usage_stays_unknown"),
    ("budget", "not 1 <= max_output <= self.max_output_tokens",
     "False", "test_output_budget_limit"),
    ("redaction", 'f"Messages endpoint returned HTTP {error.code}"',
     'f"Messages endpoint returned {error}"', "test_http_error_does_not_expose_key_or_body"),
]

records = []
for name, original, replacement, test_name in MUTATIONS:
    if source.count(original) != 1:
        raise RuntimeError("Mutation target is ambiguous or missing: " + name)
    mutated_source = source.replace(original, replacement)
    module = types.ModuleType("src._anthropic_mutant")
    module.__file__ = str(ROOT / "src/anthropic_adapter.py")
    exec(compile(mutated_source, str(ROOT / "src/anthropic_adapter.py"), "exec"), module.__dict__)
    mutated_class = module.AnthropicMessagesAdapter
    if name == "budget":
        # The intentionally broken budget guard must never cause a real network call.
        mutated_class._request = lambda *args, **kwargs: anthropic_tests.RESPONSE

    suite = unittest.TestSuite([anthropic_tests.TestAnthropicAdapter(test_name)])
    with patch.object(anthropic_tests, "AnthropicMessagesAdapter", mutated_class):
        result = unittest.TextTestRunner(stream=io.StringIO()).run(suite)
    records.append({"guard": name, "detected": bool(result.failures), "unexpected_errors": len(result.errors)})

passed = all(record["detected"] and not record["unexpected_errors"] for record in records)
print(json.dumps({"mutations": len(records), "passed": passed, "results": records}))
raise SystemExit(0 if passed else 1)
