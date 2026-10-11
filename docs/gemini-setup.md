# Gemini Worker 本地代理接入（Anthropic Messages 协议）

配置日期：2026-10-11（Asia/Shanghai）。接入参数由用户（Meta Planner 侧）直接下达，
属已裁决的通道变更（见 `docs/adr/ADR-0002-gemini-local-proxy.md`）。

## 接入参数

- Base URL：http://localhost:8787
- 请求端点：POST /v1/messages（Anthropic Messages 协议）
- 模型：gemini-3.8-flash-high
- 配置上下文容量：1,000,000 tokens。
- 配置最大输出：64,000 tokens，对应协议必填的 max_tokens。
- 认证：`x-api-key` 头，密钥读取 GEMINI_API_KEY 环境变量（本机代理令牌，
  已持久化到用户级环境变量）；同时携带 `anthropic-version: 2023-06-01`。
- 默认请求超时：300 秒。
- 默认不自动重试、不自动更换协议或模型。
- 结构化输出：本协议无原生 JSON Schema 约束；Schema 随提示词下发，
  Draft-07 校验在控制层本地完成且为权威判定。
- stop_reason 映射：max_tokens → 截断错误；refusal → 拒绝错误；
  end_turn / stop_sequence → 成功；其余（如 tool_use，本方不发工具）拒绝。

上下文与输出上限由用户提供；最小验证不代表已完成满容量压测。
基础地址是回环地址，调用进程需与代理运行在同一台宿主机。

## 配置与调用

实际配置位于 configs/adapters.local.json（不含密钥，Git 忽略）；
可提交的 configs/adapters.example.json 保留相同字段。
用户级 GEMINI_API_KEY 已持久化；新进程自动继承，已有进程通过
`src.model_config.refresh_user_env_var` 从注册表刷新。

Python 接入入口：

    from src.model_config import load_model_adapter
    worker = load_model_adapter("worker")
    result = worker.invoke_structured(messages, schema, max_output_tokens=8192)

运行阶段 3 验证（含真实 Gemini+Muse 主闭环）：

    python scripts/verify_phase3.py

结果保存在 reports/phase3-verification.json，区分离线测试、Muse 复认、
Gemini 接入预检、真实 Gemini+Muse 主闭环（验收证据）与 DeepSeek 补充联调（非验收证据）。

## 协议依据

Anthropic Messages 协议要点：system 提示置于顶层 `system` 字段；`max_tokens` 必填；
`x-api-key` + `anthropic-version` 头；usage 返回 input_tokens/output_tokens
（total 为两者之和，缺失记为未知，不默认记零）。
