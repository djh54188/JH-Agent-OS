# JH-Agent OS 阶段 1：架构契约与接入验证 完成报告（Rev 2）

- **日期**：2026-10-09
- **执行环境**：Windows x64 / Python 3.14.7 / Node v24.19.0 / Codex CLI 0.160.1
- **工作分支**：`phase1-contracts-and-validation`
- **阶段状态**：**条件通过 / 修订版已就绪（Revise Complete）**。
  - 针对独立审查意见的 10 项缺陷已 100% 修复并复验通过；
  - 核心协议全面对齐路线图 v1.1（11 态状态机、四级 verdict、三维评估分离、逐调用动态鉴权、六类场景全覆盖）；
  - 真实调用方面，**Codex CLI**（通过 `--json` 解析出真实 15,521 Tokens）与 **DeepSeek API**（结构化生成三维评估 Plan，消耗 1,678 Tokens）双真实调用证据闭环；
  - **Gemini 与 Muse 真实调用因缺少宿主机凭据/接口信息仍处于阻断状态**，已转入严格 Schema Mock 验证，明确记录证据并提交架构师裁决。

---

## 1. 针对独立审查反馈的修订与对齐清单

| 审查意见项 | 严重级别 | 涉及文件 | 修订动作与闭环证据 |
| :--- | :--- | :--- | :--- |
| **任务 Schema 缺验收条件与三维依据** | Major | `docs/schemas/task.v1.json` | 增设必填 `acceptance_criteria` 数组、`risk_assessment` 对象（含 `risk_level`, `complexity`, `uncertainty`, `risk_basis`, `routing_basis`）、`artifact_refs` 数组。测试通过。 |
| **审核结论枚举偏离路线图** | Major | `docs/schemas/review_result.v1.json` | `verdict` 枚举严格对齐路线图第 93 行：`pass`, `revise`, `reject`, `needs_human`。单测对旧值 `needs_revision` 成功阻断。 |
| **任务状态词汇不一致** | Major | 全仓契约与代码 | 统一对齐路线图第 72 行 11 态：`created`, `classified`, `planning`, `awaiting_approval`, `executing`, `reviewing`, `repairing`, `succeeded`, `failed`, `cancelled`, `needs_attention`。 |
| **复杂度与不确定性无表达** | Major | `docs/schemas/plan.v1.json` | Plan 步骤增设 `complexity`, `uncertainty`, `rationale` 字段；`schemas/README.md` 明确三维指标的独立决策用途。 |
| **risk_level 与危险分级映射缺失** | Major | `docs/danger-action-policy.md` | 建立完整映射表：`read_only` -> Level 0, `workspace_write` -> Level 1, `system_sensitive` -> Level 2, `danger_external` -> Level 3。 |
| **Codex 适配器未接线 --output-schema 与 --json** | Major | `src/adapters.py` | 重构 `CodexCliAdapter`：`invoke` 启用 `--json` 解析 JSONL 事件流与 Token usage；`invoke_structured` 临时落盘 Schema 并传入 `--output-schema`。实测成功。 |
| **三项核心铁律未显式重述** | Major | `docs/danger-action-policy.md`, `docs/mvp-acceptance-cases.md`, `docs/adapter-contracts.md` | 显式写入“每次工具调用前检查实际参数和权限”、“Worker 完成不等于任务成功”、“Reviewer 不可用不得自动视为通过”，并明确 `revise` 进 `repairing`、`reject` 进 `failed` 的处置分野。 |
| **MVP 案例场景覆盖不全** | Major | `docs/mvp-acceptance-cases.md` | 增补 TC-MVP-06（供应商故障，转入 `needs_attention`）与 TC-MVP-07（写入中断与快照回滚，转入 `needs_attention` 或 `failed`），达成六类场景 100% 覆盖；纠正 TC-MVP-05 级别与状态。 |
| **Codex 证据 token 为 0 且日期陈旧** | Minor | `src/adapters.py`, `tests/verification_results.json` | 修复 token 解析逻辑，重跑实测记录真实 Token（Codex: 15,521 tokens; DeepSeek: 1,678 tokens），生成时间更新为当前实时 ISO 时间。 |
| **Gemini 模型 ID 漂移与 Muse 占位无警示** | Minor | `configs/adapters.example.json`, `docs/adapter-contracts.md` | 统一默认基准模型为 `gemini-1.5-pro`；Muse 示例增加显式 `_note` 占位警示语；Muse 验证分支明确异常提示。 |

---

## 2. 交付物索引清单

| 类别 | 文件路径 | 核心修订内容 |
| :--- | :--- | :--- |
| **架构决策** | `docs/adr/ADR-0001-codex-integration.md` | 选定 `codex exec` 子进程模式，统一 11 态状态机与双重权限闸门 |
| **JSON Schema** | `docs/schemas/task.v1.json` | 增补 `acceptance_criteria` 与三维评估字段（`risk_assessment`） |
|  | `docs/schemas/plan.v1.json` | 步骤支持 `complexity`, `uncertainty`, `rationale` |
|  | `docs/schemas/tool_request.v1.json` | 明确四级 `risk_level` 映射至 Level 0~3 安全分级 |
|  | `docs/schemas/tool_result.v1.json` | 捕获受控执行输出、退出码与拦截原因 |
|  | `docs/schemas/review_result.v1.json` | `verdict` 统一为 `pass`, `revise`, `reject`, `needs_human` |
|  | `docs/schemas/memory_record.v1.json` | 负结果入册与检验状态约束 |
|  | `docs/schemas/README.md` | 三维评估用途分离、11 态状态机与四级审查结论详细规范 |
| **适配器与配置** | `src/adapters.py` | 支持 Codex `--json` 事件流与 `--output-schema`，支持当前日期强约束 |
|  | `docs/adapter-contracts.md` | 统一基准模型为 `gemini-1.5-pro`，显式重述三项安全铁律 |
|  | `configs/adapters.example.json` | 增加 Muse 占位警告标识 `_note`，统一基线模型 |
|  | `configs/env.example` | 环境变量模板声明 |
| **测试与验证** | `tests/test_schemas.py` | 6 核心 Schema 语法与正反向边界断言测试（6/6 PASSED） |
|  | `tests/test_adapters.py` | 适配器异常处理、健康检查与契约测试（3/3 PASSED） |
|  | `tests/run_minimal_verification.py` | 最小验证驱动脚本（支持真实 Token 统计与 Schema 校验） |
|  | `tests/verification_results.json` | 包含真实 Token（15,521 & 1,678）与结构化样本的复核证据 |
| **策略与验收** | `docs/mvp-acceptance-cases.md` | TC-MVP-01 至 TC-MVP-07 六大核心场景全覆盖验收规格 |
|  | `docs/danger-action-policy.md` | Level 0~3 映射表、逐调用动态鉴权与 Level 3 零降级铁律 |

---

## 3. 验证结果汇总（真实调用 vs Mock 验证）

| 验证对象 | 目标角色 | 验证类型 | 验证状态 | 关键指标证据（见 `verification_results.json`） |
| :--- | :--- | :--- | :--- | :--- |
| **OpenAI Codex CLI** | 内核引擎 | **真实调用 (REAL)** | **PASSED** | 模型 `gpt-6.1-sol`，沙箱 `read-only`，输出 `"256 × 4 = 1024。环境正常。"`，耗时 125,300ms，**Tokens: 15,521**（通过 `--json` 事件精确提取） |
| **DeepSeek API** | 备选 Worker | **真实调用 (REAL)** | **PASSED** | 模型 `deepseek-flash`，结构化生成三维评估 Plan，通过 `plan.v1.json` 强校验，耗时 154ms，**Tokens: 1,678**，时间戳为实时生成 |
| **Google Gemini** | 首选 Worker | **Mock 契约 (MOCK)** | **BLOCKED (LIVE)** / **PASSED (MOCK)** | 本地未配置 `GEMINI_API_KEY`，真实网络调用阻塞；Mock 数据 100% 通过 `plan.v1.json` 校验 |
| **Muse** | 首选 Reviewer | **Mock 契约 (MOCK)** | **BLOCKED (LIVE)** / **PASSED (MOCK)** | 接口、端点与凭据全缺失，真实网络调用阻塞；Mock 数据 100% 通过 `review_result.v1.json` 校验 |

---

## 4. 提交架构师（Meta Planner / 用户）裁决事项

针对审查报告中的 `human_decisions`，正式提交架构师裁决：

1. **Muse Reviewer 接入决策**：
   - 请提供 Muse 的真实供应商名称、API 端点、模型 ID、认证协议与接口文档；
   - **或** 书面批准在阶段 2 MVP 期间，以本地连通验证通过的 **DeepSeek（OpenAI 兼容通道）** 作为 Reviewer 的临时替补通道。
2. **Gemini Worker 凭据供给**：
   - 请在宿主机安全环境变量中配置 `GEMINI_API_KEY`（或配置代理网关），以便在进入阶段 2 时激活真实端点调用。
3. **两家真实调用的验收口径裁决**：
   - 裁决是否接受 **Codex（内核引擎）+ DeepSeek（已实测通过结构化 Schema 输出）** 作为阶段 1 真实调用的充分证据，允许阶段 1 正式结项并推进阶段 2。
4. **Codex CLI 异步超时容忍度确认**：
   - 确认在阶段 2 控制层中将 Codex CLI 默认超时设为 180~300 秒并采用后台异步子进程与 JSONL 事件流轮询调度。
