# ADR-0002: Gemini Worker 改经本地代理 Anthropic Messages 协议接入

- **状态**：已裁决并实施（用户于 2026-10-11 直接下达接入参数）
- **日期**：2026-10-11
- **决策者**：用户（Meta Planner 侧）
- **参考**：`docs/adr/ADR-0001-codex-integration.md`、`docs/muse-setup.md`、`docs/gemini-setup.md`

## 1. 背景与裁决

阶段 3 要求真实 Gemini 产物 + 真实 Muse 审核闭环，但官方 Google 通道
（`generativelanguage.googleapis.com/v1beta`，模型 `gemini-3.8-flash`）因缺少
`GEMINI_API_KEY` 持续阻塞（阶段 1 起登记的遗留验收项）。2026-10-11 用户直接下达
本机代理接入参数，属既定裁决，无需另行升级：

- Base URL：`http://localhost:8787`（与 Muse Reviewer 同一本地代理）
- 协议：Anthropic Messages（POST `/v1/messages`），非 Gemini generateContent
- 模型：`gemini-3.8-flash-high`（代理暴露的分层变体）
- 认证：`x-api-key` 头，环境变量 `GEMINI_API_KEY`（本机代理令牌，已持久化）
- 容量：上下文 1,000,000 / 最大输出 64,000（用户提供，未压测）

## 2. 决策内容

1. **新增 `AnthropicMessagesAdapter`**（`src/anthropic_adapter.py`）：
   - system 提示映射到顶层 `system` 字段；`max_tokens` 由控制层逐调用预算
     （`call_limits`）计算并受 64,000 上限约束；
   - `stop_reason` 映射：`max_tokens` → 截断错误、`refusal` → 拒绝错误、
     `end_turn`/`stop_sequence` → 成功、其余（如 tool_use）→ 意外停止错误；
   - usage：`input_tokens`/`output_tokens`，total 为两者之和，缺失记 None（未知）；
   - 无原生 JSON Schema 约束 → Schema 随提示词下发，Draft-07 本地校验为权威；
   - HTTP 错误脱敏（不回显密钥/响应体）；回环地址绕过系统代理；不自动重试。
2. **配置更新**（`configs/adapters.local.json` 与可提交的 example 同步）：
   `gemini_worker.provider = "google_via_local_proxy"`、
   `api_protocol = "anthropic_messages"`、`endpoint_url = http://localhost:8787/v1`、
   `model_id = gemini-3.8-flash-high`、`max_output_tokens = 64000`、
   `context_window_tokens = 1000000`、`timeout_seconds = 300`。
   原 `gemini-1.5-pro`→`gemini-3.8-flash`（官方端点）配置退役，仅作历史记录保留于本 ADR。
3. **验证基线**：离线 10 项适配器测试 + `scripts/verify_anthropic_guards.py`
   9 项变异守卫全检出；`tests/test_phase2_control_layer.py::test_04` 改为
   patch 凭据探测实现宿主状态无关的离线隔离（防止注册表凭据泄漏进离线路由）。

## 3. 兼容性说明

- 仅适配器层变化：Task/Plan/Artifact/ToolRequest/ToolResult/ReviewResult/
  MemoryRecord 协议与控制层契约**全部不变**；`load_model_adapter("worker")`
  入口不变；`call_limits`/`input_reserve`/尝试登记/用量记录链路不变。
- 未知用量语义保持：usage 缺失记 NULL，不默认记零，触发 fail-closed。
- 官方 Google 端点配置退役但 `GeminiAdapter` 实现保留（未被任何 profile 引用），
  未来切回官方通道仅需改回 profile 配置。

## 4. 局限

- 回环代理需与调用进程同宿主机；迁移需重新配置可达地址。
- 容量为用户提供值，未做满容量压测。
- 本协议无原生结构化输出，模型可能输出非法 JSON；控制层本地校验失败即
  fail-closed（worker_generation_failed / SchemaValidationError），不重试。
