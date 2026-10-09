# JH-Agent OS 阶段 1：架构契约与接入验证 完成报告

- **日期**：2026-10-09
- **执行环境**：Windows x64 / Python 3.14.7 / Node v24.19.0 / Codex CLI 0.160.1
- **工作分支**：`phase1-contracts-and-validation`
- **阶段状态**：**条件通过（部分阻塞已隔离标注）**。核心协议、接入选型、危险策略与验证框架已 100% 交付；真实调用方面，Codex 与 DeepSeek 完成真实调用验证，Gemini 与 Muse 因缺少凭据/接口信息处于阻断状态，已转为契约 Mock 验证并记录阻断证据。

---

## 1. 核心结论与局限（负面与局限优先）

### 1.1 阻塞项与局限（Blockers & Limitations）
1. **[阻塞] Gemini 真实网络调用阻塞**：
   - **事实**：宿主机环境未配置 `GEMINI_API_KEY` 或 `GOOGLE_API_KEY`，无法发起 Google AI Studio 真实调用。
   - **应对**：本阶段通过 `MockModelAdapter` 基于 `plan.v1.json` 完成严格契约测试。
2. **[阻塞] Muse 接口与凭据完全缺失**：
   - **事实**：路线图与本地环境均未提供 Muse 的供应商名称、模型 ID、API 端点及鉴权密钥，无法发起真实网络调用。
   - **应对**：本阶段通过 `MockModelAdapter` 基于 `review_result.v1.json` 完成严格契约测试。
3. **[局限] Codex CLI 存在网络回退延迟**：
   - **事实**：本地网络在连接 Codex WebSocket 时易超时（耗时约 118 秒），依赖内置机制优雅降级为 HTTPS 传输方成功完成调用。
   - **影响**：MVP 控制层必须将单次调用超时阈值保守设置为 120 秒~300 秒，避免早退。

### 1.2 已验证成果（Positive Facts）
1. **Codex 接入方式定型**：实测验证 `codex exec` 子进程驱动模式具备可行性，完成 ADR-0001 决策记录并明确职责边界。
2. **核心契约全面版本化**：6 个核心 JSON Schema 全部建立，Draft-07 语法与边界测试 100% 绿灯通过。
3. **真实调用证据闭环**：
   - **Codex 内核 CLI**：真实完成一次非交互沙箱调用，产出数学计算与环境验证结果，耗时 118,251ms，Token 统计正常；
   - **DeepSeek 结构化调用**：真实调用 `deepseek-flash`（利用本地已具备的 `DEEPSEEK_API_KEY`），通过 `invoke_structured` 成功产生严格契合 `plan.v1.json` 的实体，耗时 145ms，消耗 1,130 Tokens。
4. **安全与验收基线确立**：完成 5 项端到端 MVP 验收用例与 4 级危险动作防御策略规范。

---

## 2. 交付物索引清单

| 类别 | 文件绝对/相对路径 | 核心内容说明 |
| :--- | :--- | :--- |
| **架构决策** | `docs/adr/ADR-0001-codex-integration.md` | 选定 `codex exec` 子进程驱动模式，定义四层职责边界与安全隔离原则 |
| **JSON Schema** | `docs/schemas/task.v1.json` | 任务实体契约（状态机、预算、上下文约束、强类型 ID） |
|  | `docs/schemas/plan.v1.json` | 执行计划契约（步骤、角色分工、工具依赖、风险等级、回退策略） |
|  | `docs/schemas/tool_request.v1.json` | 工具调用请求契约（调用方角色、超时、风险等级、人工审批标志） |
|  | `docs/schemas/tool_result.v1.json` | 工具执行结果契约（真实输出、退出码、执行耗时、拦截原因） |
|  | `docs/schemas/review_result.v1.json` | 独立审查结果契约（判定结论、评分、包含 Evidence 的 Issue 列表） |
|  | `docs/schemas/memory_record.v1.json` | 经验与记忆契约（支持 negative_result 与 verification_status） |
|  | `docs/schemas/README.md` | 契约设计规范与 Python 校验使用指引 |
| **适配器契约** | `docs/adapter-contracts.md` | 统一模型适配器接口定义、角色配置规范及凭据脱敏安全指引 |
|  | `configs/adapters.example.json` | Worker / Reviewer / Fallback 适配器配置示例（无明文密钥） |
|  | `configs/env.example` | 环境变量模板声明 |
|  | `src/adapters.py` | 统一适配器实现（Codex CLI、OpenAI-compatible、Gemini、Mock） |
| **测试与验证** | `tests/test_schemas.py` | 6 核心 Schema 语法与正反向边界断言测试（6/6 PASSED） |
|  | `tests/test_adapters.py` | 适配器异常处理、健康检查与契约测试（3/3 PASSED） |
|  | `tests/run_minimal_verification.py` | 阶段 1 最小验证自动化脚本（驱动真实调用与 Mock 校验） |
|  | `tests/verification_results.json` | 真实调用与 Mock 校验的可复核结构化结果证据文件 |
| **策略与验收** | `docs/mvp-acceptance-cases.md` | TC-MVP-01 至 TC-MVP-05 验收测试用例规格 |
|  | `docs/danger-action-policy.md` | Level 0~3 风险分级、绝对禁止黑名单与人工确认闸门 |

---

## 3. 验证结果汇总（真实调用 vs Mock 验证）

| 验证对象 | 目标角色 | 验证类型 | 验证状态 | 关键输出 / 证据摘要 |
| :--- | :--- | :--- | :--- | :--- |
| **OpenAI Codex CLI** | 内核引擎 | **真实调用 (REAL)** | **PASSED** | 运行 `codex exec`，输出 `"256 × 4 = 1024。环境正常。"`，模型 `gpt-6.1-sol` |
| **DeepSeek API** | 备选 Worker | **真实调用 (REAL)** | **PASSED** | 真实请求 `deepseek-flash` 产生 Plan，通过 `plan.v1.json` 强校验，耗时 145ms，1130 Tokens |
| **Google Gemini** | 首选 Worker | **Mock 契约 (MOCK)** | **BLOCKED (LIVE)** / **PASSED (MOCK)** | 本地缺少 `GEMINI_API_KEY`，阻塞真实网络调用；Mock 数据通过 `plan.v1.json` 校验 |
| **Muse** | 首选 Reviewer | **Mock 契约 (MOCK)** | **BLOCKED (LIVE)** / **PASSED (MOCK)** | 接口、端点与凭据全缺失，阻塞真实网络调用；Mock 数据通过 `review_result.v1.json` 校验 |

---

## 4. 待架构师（Meta Planner / 用户）裁决的事项

请架构师针对以下 3 点进行裁决，以解开后续阶段的工程阻塞：

1. **Muse Reviewer 的正式供应商与接入参数**：
   - 请提供 Muse 的服务商、API Endpoint、模型 ID、鉴权方式及调用文档；
   - 若 Muse 当前不可用，是否正式批准将本地已验证通畅的 **DeepSeek（或 OpenAI 兼容端点）** 作为 MVP 阶段 Reviewer 的临时替补？
2. **Gemini Worker 凭据供给**：
   - 请在宿主机环境变量中提供 `GEMINI_API_KEY`（或提供代理网关配置），以便进入阶段 2 时激活真实端点连通。
3. **Codex CLI 超时与降级容忍度确认**：
   - 当前网络下 WebSocket 易降级为 HTTPS（单次冷启动+生成耗时约 118 秒）。是否批准控制层将 Codex 默认超时设为 180 秒并在后台以异步方式调度？
