# Muse Reviewer 本地接入

配置日期：2026-10-10（Asia/Shanghai）。

## 接入参数

- Base URL：http://127.0.0.1:8787/v1
- 请求端点：POST /responses
- 模型：muse-spark-1.3-contributor-free
- 思考等级：max，对应 reasoning.effort。
- 配置上下文容量：1,000,000 tokens。
- 配置最大输出：131,072 tokens，对应 max_output_tokens。
- 认证：Bearer，密钥读取 MUSE_API_KEY 环境变量。
- 默认请求超时：300 秒。
- 默认不携带上一轮 response ID，审核上下文由调用方独立提供。
- 默认不自动重试、不自动更换协议或模型。

上下文容量和输出上限由用户提供；最小验证不代表已完成满容量压测。
基础地址是回环地址，调用进程需要与代理运行在同一台宿主机。
未来迁移到容器或另一台机器时，需要重新配置可达地址。

## 配置与调用

实际配置位于 configs/adapters.local.json；它不包含密钥，并由 Git 忽略。
可提交的 configs/adapters.example.json 保留相同的 Muse 接入字段。

用户级 MUSE_API_KEY 环境变量在配置时持久化。
已有终端或执行 AI 进程可能需要重新启动以继承新的环境变量。
运行中的 PowerShell 可通过以下方式刷新当前进程变量：

    $env:MUSE_API_KEY = [Environment]::GetEnvironmentVariable('MUSE_API_KEY', 'User')

Python 接入入口：

    from src.model_config import load_model_adapter
    reviewer = load_model_adapter("reviewer")
    result = reviewer.invoke_structured(messages, schema, max_output_tokens=8192)

运行最小真实验证：

    python scripts/verify_muse.py

结果保存在 reports/muse-verification.json。它区分模型列表检查、
正确产物审核、错误产物审核和协议/Schema 校验。
单次验证使用 8192 输出预算，保留 max 思考等级。
两例通过只证明最小接入和基本错误识别，不代表完整 Reviewer 质量验收。

## 协议依据

Responses 使用 input、reasoning.effort、max_output_tokens 和 text.format。
代理是否完整实现这些字段，以实际验证为准。
官方参考：https://developers.openai.com/api/docs/guides/migrate-to-responses
