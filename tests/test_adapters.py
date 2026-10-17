"""
Unit tests for JH-Agent OS Model Adapters v1.1.0
Verifies adapter contracts, error handling, mock compliance, and schema alignment.
"""

import json
import os
import unittest
from src.adapters import (
    MockModelAdapter,
    OpenAICompatibleAdapter,
    GeminiAdapter,
    CodexCliAdapter,
    CredentialMissingError,
    SchemaValidationError
)

class TestModelAdapters(unittest.TestCase):

    def test_mock_adapter_conformance(self):
        canned_plan = {
            "schema_version": "1.0.0",
            "plan_id": "plan_test12345678",
            "task_id": "task_12345678abcdef",
            "goal_summary": "Mock plan verification",
            "revision": 1,
            "steps": [
                {
                    "step_id": "step_01",
                    "title": "测试步骤",
                    "description": "说明",
                    "role": "worker",
                    "tool_dependencies": [],
                    "risk_level": "read_only",
                    "complexity": "low",
                    "uncertainty": "low",
                    "rationale": "只读安全探测",
                    "expected_outcome": "完成",
                    "status": "pending"
                }
            ],
            "fallback_strategy": "无",
            "created_at": "2026-10-09T20:00:00Z"
        }
        adapter = MockModelAdapter(
            {"role": "worker", "model_id": "mock_worker"},
            canned_payload=canned_plan
        )
        self.assertTrue(adapter.check_health()["healthy"])
        
        schema_path = os.path.join(os.path.dirname(__file__), "..", "docs", "schemas", "plan.v1.json")
        with open(schema_path, "r", encoding="utf-8") as f:
            schema = json.load(f)

        resp = adapter.invoke_structured([{"role": "user", "content": "ping"}], schema=schema)
        self.assertTrue(resp.is_mock)
        self.assertEqual(resp.parsed_json["plan_id"], "plan_test12345678")

    def test_gemini_adapter_missing_credentials(self):
        old_key = os.environ.pop("GEMINI_API_KEY", None)
        old_gkey = os.environ.pop("GOOGLE_API_KEY", None)
        try:
            adapter = GeminiAdapter({
                "role": "worker",
                "model_id": "gemini-1.5-pro",
                "api_key_env": "GEMINI_API_KEY"
            })
            with self.assertRaises(CredentialMissingError):
                adapter.invoke([{"role": "user", "content": "hello"}])
        finally:
            if old_key:
                os.environ["GEMINI_API_KEY"] = old_key
            if old_gkey:
                os.environ["GOOGLE_API_KEY"] = old_gkey

    def test_codex_cli_health(self):
        adapter = CodexCliAdapter({
            "role": "worker",
            "model_id": "gpt-6.1-sol",
            "workspace_root": "D:/JH-Agent OS"
        })
        health = adapter.check_health()
        self.assertTrue(health["healthy"])
        self.assertIn("0.160.1", health["version"])

if __name__ == "__main__":
    unittest.main()
