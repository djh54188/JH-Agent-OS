"""
JH-Agent OS Phase 1: Minimal Verification Runner
Performs live calls (Codex, DeepSeek) and contract/mock verifications (Gemini, Muse),
recording structured evidence to tests/verification_results.json.
"""

import json
import os
import sys
import time
from typing import Dict, Any

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from src.adapters import (
    CodexCliAdapter,
    OpenAICompatibleAdapter,
    GeminiAdapter,
    MockModelAdapter,
    CredentialMissingError,
    AdapterError
)

def load_schema(schema_name: str) -> dict:
    schema_path = os.path.join(os.path.dirname(__file__), "..", "docs", "schemas", schema_name)
    with open(schema_path, "r", encoding="utf-8") as f:
        return json.load(f)

def run_verifications() -> Dict[str, Any]:
    results = {
        "timestamp": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "environment": {
            "os": sys.platform,
            "python_version": sys.version.split()[0],
            "workspace": os.path.abspath(os.path.join(os.path.dirname(__file__), "..")),
        },
        "verifications": {}
    }

    plan_schema = load_schema("plan.v1.json")
    review_schema = load_schema("review_result.v1.json")

    # -------------------------------------------------------------
    # 1. Codex CLI Live Verification
    # -------------------------------------------------------------
    print("[1/4] Verifying OpenAI Codex CLI (Live Call)...")
    codex_adapter = CodexCliAdapter({
        "role": "worker",
        "model_id": "gpt-6.1-sol",
        "workspace_root": results["environment"]["workspace"],
        "sandbox_mode": "read-only",
        "timeout_seconds": 120
    })
    health = codex_adapter.check_health()
    if health["healthy"]:
        try:
            prompt = [{"role": "user", "content": "请输出计算结果: 256 * 4 = 1024，并简短确认环境正常。"}]
            resp = codex_adapter.invoke(prompt)
            results["verifications"]["codex_cli"] = {
                "type": "REAL_LIVE_CALL",
                "status": "PASSED",
                "model_id": resp.model_id,
                "latency_ms": resp.latency_ms,
                "tokens_used": resp.total_tokens,
                "output_excerpt": resp.content[:300],
                "is_mock": False,
                "notes": "成功通过 Codex CLI 子进程完成沙箱只读调用与 Token 统计"
            }
            print("  -> Codex CLI Live Call: PASSED")
        except Exception as e:
            results["verifications"]["codex_cli"] = {
                "type": "REAL_LIVE_CALL",
                "status": "FAILED",
                "error": str(e),
                "is_mock": False
            }
            print(f"  -> Codex CLI Live Call: FAILED ({e})")
    else:
        results["verifications"]["codex_cli"] = {
            "type": "REAL_LIVE_CALL",
            "status": "UNHEALTHY",
            "error": health.get("error"),
            "is_mock": False
        }
        print(f"  -> Codex CLI Unhealthy: {health.get('error')}")

    # -------------------------------------------------------------
    # 2. DeepSeek Live Verification (Structured Plan Schema)
    # -------------------------------------------------------------
    print("[2/4] Verifying DeepSeek Adapter (Live Structured Output Call)...")
    deepseek_key = os.environ.get("DEEPSEEK_API_KEY", "").strip()
    if deepseek_key:
        ds_adapter = OpenAICompatibleAdapter({
            "role": "worker",
            "model_id": "deepseek-flash",
            "endpoint_url": "https://api.deepseek.com/v1",
            "api_key_env": "DEEPSEEK_API_KEY",
            "temperature": 0.1,
            "max_tokens": 2048,
            "timeout_seconds": 30
        })
        try:
            req_messages = [
                {
                    "role": "system",
                    "content": "You are a software engineering planner for JH-Agent OS."
                },
                {
                    "role": "user",
                    "content": "Create a minimal 1-step Plan for task_12345678abcdef to verify file readability."
                }
            ]
            resp = ds_adapter.invoke_structured(req_messages, schema=plan_schema)
            results["verifications"]["deepseek_live"] = {
                "type": "REAL_LIVE_CALL",
                "status": "PASSED",
                "model_id": resp.model_id,
                "latency_ms": resp.latency_ms,
                "tokens_prompt": resp.tokens_prompt,
                "tokens_completion": resp.tokens_completion,
                "total_tokens": resp.total_tokens,
                "validated_plan_id": resp.parsed_json.get("plan_id"),
                "parsed_plan_sample": resp.parsed_json,
                "is_mock": False,
                "notes": "真实调用 DeepSeek API，成功返回且通过 plan.v1.json Schema 校验"
            }
            print("  -> DeepSeek Live Call: PASSED (Schema validated)")
        except Exception as e:
            results["verifications"]["deepseek_live"] = {
                "type": "REAL_LIVE_CALL",
                "status": "FAILED",
                "error": str(e),
                "is_mock": False
            }
            print(f"  -> DeepSeek Live Call: FAILED ({e})")
    else:
        results["verifications"]["deepseek_live"] = {
            "type": "REAL_LIVE_CALL",
            "status": "BLOCKED_MISSING_CREDENTIALS",
            "notes": "DEEPSEEK_API_KEY 环境变量未设置",
            "is_mock": False
        }
        print("  -> DeepSeek Live Call: BLOCKED (No key)")

    # -------------------------------------------------------------
    # 3. Gemini Worker Verification (Cred Check + Mock Contract)
    # -------------------------------------------------------------
    print("[3/4] Verifying Gemini Worker (Live Cred Check & Contract Test)...")
    gemini_key = os.environ.get("GEMINI_API_KEY") or os.environ.get("GOOGLE_API_KEY")
    if gemini_key:
        # If credentials present, execute live
        gemini_adapter = GeminiAdapter({
            "role": "worker",
            "model_id": "gemini-1.5-flash",
            "api_key_env": "GEMINI_API_KEY",
            "timeout_seconds": 30
        })
        try:
            resp = gemini_adapter.invoke([{"role": "user", "content": "ping"}])
            results["verifications"]["gemini_worker"] = {
                "type": "REAL_LIVE_CALL",
                "status": "PASSED",
                "model_id": resp.model_id,
                "latency_ms": resp.latency_ms,
                "is_mock": False
            }
        except Exception as e:
            results["verifications"]["gemini_worker"] = {
                "type": "REAL_LIVE_CALL",
                "status": "FAILED",
                "error": str(e),
                "is_mock": False
            }
    else:
        # Missing credentials: run strict Mock contract verification
        canned_worker_plan = {
            "schema_version": "1.0.0",
            "plan_id": "plan_gemini_mock01",
            "task_id": "task_12345678abcdef",
            "goal_summary": "Gemini Worker 契约离线验证步骤",
            "revision": 1,
            "steps": [
                {
                    "step_id": "step_01",
                    "title": "分析工程依赖",
                    "description": "读取项目配置并检查依赖包完备性",
                    "role": "worker",
                    "tool_dependencies": ["read_file"],
                    "risk_level": "read_only",
                    "expected_outcome": "产出依赖分析清单",
                    "status": "pending"
                }
            ],
            "fallback_strategy": "降级为人工复核",
            "created_at": "2026-10-09T20:30:00Z"
        }
        mock_gemini = MockModelAdapter(
            {"role": "worker", "model_id": "gemini-1.5-pro-mock"},
            canned_payload=canned_worker_plan
        )
        resp = mock_gemini.invoke_structured(
            [{"role": "user", "content": "generate plan"}],
            schema=plan_schema
        )
        results["verifications"]["gemini_worker"] = {
            "type": "MOCK_CONTRACT_TEST",
            "status": "BLOCKED_FOR_LIVE_CALL_BUT_MOCK_VERIFIED",
            "blocking_reason": "缺少 GEMINI_API_KEY / GOOGLE_API_KEY 凭据，无法发起真实网络请求",
            "contract_validation": "PASSED",
            "validated_plan_id": resp.parsed_json["plan_id"],
            "is_mock": True,
            "notes": "严格基于 plan.v1.json 完成 Mock 契约验证，真实调用待补齐凭据后激活"
        }
        print("  -> Gemini Worker: BLOCKED for Live (Missing key) | MOCK CONTRACT PASSED")

    # -------------------------------------------------------------
    # 4. Muse Reviewer Verification (Cred Check + Mock Contract)
    # -------------------------------------------------------------
    print("[4/4] Verifying Muse Reviewer (Live Cred Check & Contract Test)...")
    muse_key = os.environ.get("MUSE_API_KEY")
    muse_endpoint = os.environ.get("MUSE_API_ENDPOINT")
    if muse_key and muse_endpoint:
        # If credentials present, execute live
        pass
    else:
        canned_review_result = {
            "schema_version": "1.0.0",
            "review_id": "rev_muse_mock001",
            "task_id": "task_12345678abcdef",
            "target_artifact_type": "code",
            "target_artifact_path": "src/adapters.py",
            "verdict": "pass",
            "overall_score": 92.0,
            "issues": [
                {
                    "issue_id": "iss_muse_01",
                    "severity": "p3_minor",
                    "category": "maintainability",
                    "description": "建议为 subprocess 调用补充更细粒度的超时重试机制",
                    "evidence": "src/adapters.py: line 150-180",
                    "file_path": "src/adapters.py",
                    "line_start": 150,
                    "line_end": 180,
                    "recommendation": "在阶段 2 引入 RetryHandler"
                }
            ],
            "summary": "代码结构清晰，适配器契约抽象完备，符合四层架构规范。",
            "reviewer_role": "muse_reviewer_mock",
            "created_at": "2026-10-09T20:35:00Z"
        }
        mock_muse = MockModelAdapter(
            {"role": "reviewer", "model_id": "muse-v1-mock"},
            canned_payload=canned_review_result
        )
        resp = mock_muse.invoke_structured(
            [{"role": "user", "content": "review code"}],
            schema=review_schema
        )
        results["verifications"]["muse_reviewer"] = {
            "type": "MOCK_CONTRACT_TEST",
            "status": "BLOCKED_FOR_LIVE_CALL_BUT_MOCK_VERIFIED",
            "blocking_reason": "缺少 Muse 供应商信息、API 端点与 MUSE_API_KEY 凭据，无法发起真实网络请求",
            "contract_validation": "PASSED",
            "validated_review_id": resp.parsed_json["review_id"],
            "is_mock": True,
            "notes": "严格基于 review_result.v1.json 完成 Mock 契约验证，真实调用待架构师明确接口信息后激活"
        }
        print("  -> Muse Reviewer: BLOCKED for Live (Missing endpoint/key) | MOCK CONTRACT PASSED")

    # Write output to json file
    out_file = os.path.join(os.path.dirname(__file__), "verification_results.json")
    with open(out_file, "w", encoding="utf-8") as f:
        json.dump(results, f, ensure_ascii=False, indent=2)
    print(f"\nVerification results recorded in: {out_file}")
    return results

if __name__ == "__main__":
    run_verifications()
