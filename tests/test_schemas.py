"""
Tests for JH-Agent OS Core JSON Schemas v1.0.0
Validates schema syntax and negative/positive boundary test cases.
"""

import json
import os
import unittest
from jsonschema import Draft7Validator, validate, ValidationError

SCHEMAS_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "docs", "schemas"))

class TestJsonSchemas(unittest.TestCase):

    def load_schema(self, filename: str) -> dict:
        filepath = os.path.join(SCHEMAS_DIR, filename)
        self.assertTrue(os.path.exists(filepath), f"Schema file not found: {filepath}")
        with open(filepath, "r", encoding="utf-8") as f:
            return json.load(f)

    def test_all_schemas_are_valid_draft7(self):
        schemas = [
            "task.v1.json",
            "plan.v1.json",
            "tool_request.v1.json",
            "tool_result.v1.json",
            "review_result.v1.json",
            "memory_record.v1.json",
        ]
        for name in schemas:
            schema = self.load_schema(name)
            Draft7Validator.check_schema(schema)

    def test_task_schema_validation(self):
        schema = self.load_schema("task.v1.json")
        valid_task = {
            "schema_version": "1.0.0",
            "task_id": "task_12345678abcdef",
            "title": "实现基础配置解析器",
            "description": "解析 YAML/JSON 配置文件并进行字段边界检查",
            "priority": "high",
            "status": "pending",
            "budget": {
                "max_turns": 10,
                "max_tokens": 50000,
                "max_execution_time_seconds": 300,
                "max_cost_usd": 1.5
            },
            "context": {
                "workspace_root": "D:/JH-Agent OS",
                "target_files": ["configs/adapters.example.json"],
                "env_vars_whitelist": ["PYTHONPATH"],
                "reference_docs": ["docs/architecture-roadmap-v1.1.md"]
            },
            "assigned_worker": "gemini_worker",
            "assigned_reviewer": "muse_reviewer",
            "metadata": {"tag": "phase1"},
            "created_at": "2026-10-09T20:00:00Z",
            "updated_at": "2026-10-09T20:00:00Z"
        }
        # Positive case
        validate(instance=valid_task, schema=schema)

        # Negative case: illegal status
        bad_task = dict(valid_task, status="unknown_status")
        with self.assertRaises(ValidationError):
            validate(instance=bad_task, schema=schema)

        # Negative case: additional property
        bad_task2 = dict(valid_task, unknown_field="unexpected")
        with self.assertRaises(ValidationError):
            validate(instance=bad_task2, schema=schema)

    def test_plan_schema_validation(self):
        schema = self.load_schema("plan.v1.json")
        valid_plan = {
            "schema_version": "1.0.0",
            "plan_id": "plan_abcdef123456",
            "task_id": "task_12345678abcdef",
            "goal_summary": "三步完成最小适配器验证",
            "revision": 1,
            "steps": [
                {
                    "step_id": "step_01",
                    "title": "读取配置",
                    "description": "读取本地适配器配置文件",
                    "role": "worker",
                    "tool_dependencies": ["read_file"],
                    "risk_level": "read_only",
                    "expected_outcome": "获得适配器字典",
                    "status": "completed"
                },
                {
                    "step_id": "step_02",
                    "title": "执行测试",
                    "description": "调用最小请求",
                    "role": "worker",
                    "tool_dependencies": ["execute_command"],
                    "risk_level": "workspace_write",
                    "expected_outcome": "产出验证证据",
                    "status": "in_progress"
                }
            ],
            "fallback_strategy": "若模型调用超时，记录阻断原因并切换至 Mock 隔离测试",
            "created_at": "2026-10-09T20:05:00Z"
        }
        validate(instance=valid_plan, schema=schema)

        # Negative case: empty steps
        bad_plan = dict(valid_plan, steps=[])
        with self.assertRaises(ValidationError):
            validate(instance=bad_plan, schema=schema)

    def test_tool_request_and_result_schemas(self):
        req_schema = self.load_schema("tool_request.v1.json")
        res_schema = self.load_schema("tool_result.v1.json")

        valid_req = {
            "schema_version": "1.0.0",
            "request_id": "treq_998877665544",
            "task_id": "task_12345678abcdef",
            "step_id": "step_01",
            "caller_role": "worker",
            "tool_name": "read_file",
            "parameters": {"path": "configs/adapters.example.json"},
            "timeout_ms": 5000,
            "risk_level": "read_only",
            "requires_human_approval": False,
            "created_at": "2026-10-09T20:10:00Z"
        }
        validate(instance=valid_req, schema=req_schema)

        # Negative case: invalid risk level
        bad_req = dict(valid_req, risk_level="super_admin")
        with self.assertRaises(ValidationError):
            validate(instance=bad_req, schema=req_schema)

        valid_res = {
            "schema_version": "1.0.0",
            "result_id": "tres_112233445566",
            "request_id": "treq_998877665544",
            "task_id": "task_12345678abcdef",
            "tool_name": "read_file",
            "status": "success",
            "stdout": "file content here",
            "stderr": None,
            "structured_data": {"size_bytes": 1024},
            "execution_time_ms": 12,
            "error_message": None,
            "created_at": "2026-10-09T20:10:01Z"
        }
        validate(instance=valid_res, schema=res_schema)

        # Negative case: negative execution time
        bad_res = dict(valid_res, execution_time_ms=-5)
        with self.assertRaises(ValidationError):
            validate(instance=bad_res, schema=res_schema)

    def test_review_result_schema(self):
        schema = self.load_schema("review_result.v1.json")
        valid_review = {
            "schema_version": "1.0.0",
            "review_id": "rev_778899aabbcc",
            "task_id": "task_12345678abcdef",
            "target_artifact_type": "code",
            "target_artifact_path": "tests/test_schemas.py",
            "verdict": "needs_revision",
            "overall_score": 78.5,
            "issues": [
                {
                    "issue_id": "iss_01",
                    "severity": "p2_major",
                    "category": "boundary_violation",
                    "description": "缺少非法字符输入的边界异常测试",
                    "evidence": "def test_tool_request lacks negative id pattern check",
                    "file_path": "tests/test_schemas.py",
                    "line_start": 40,
                    "line_end": 55,
                    "recommendation": "增加带有特殊字符的 ID 测试样例"
                }
            ],
            "summary": "主体逻辑完整，需补充负向测试用例。",
            "reviewer_role": "muse_reviewer",
            "created_at": "2026-10-09T20:15:00Z"
        }
        validate(instance=valid_review, schema=schema)

        # Negative case: score > 100
        bad_review = dict(valid_review, overall_score=105.0)
        with self.assertRaises(ValidationError):
            validate(instance=bad_review, schema=schema)

    def test_memory_record_schema(self):
        schema = self.load_schema("memory_record.v1.json")
        valid_memory = {
            "schema_version": "1.0.0",
            "memory_id": "mem_001122334455",
            "task_id": "task_12345678abcdef",
            "category": "negative_result",
            "title": "Codex WebSocket 超时与降级",
            "key": "tools.codex.websocket_fallback",
            "content": "Windows 环境下 Codex CLI 连接 WebSocket 易超时，应配置 120s 容忍度并依赖 HTTPS 回退。",
            "tags": ["codex", "windows", "network"],
            "confidence": 0.95,
            "verification_status": "verified",
            "source_event": "ADR-0001-实测日志",
            "created_at": "2026-10-09T20:20:00Z",
            "updated_at": "2026-10-09T20:20:00Z"
        }
        validate(instance=valid_memory, schema=schema)

        # Negative case: confidence out of bounds (1.5)
        bad_memory = dict(valid_memory, confidence=1.5)
        with self.assertRaises(ValidationError):
            validate(instance=bad_memory, schema=schema)


if __name__ == "__main__":
    unittest.main()
