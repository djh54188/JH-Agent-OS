# docs · JH-Agent OS · 文档索引

定位与入口见 [根 README](../README.md)，当前状态见 [STATUS](../STATUS.md)，路线见 [ROADMAP](../ROADMAP.md)，任务见 [TODO](../TODO.md)，历史见 [CHANGELOG](../CHANGELOG.md)。

## 当前需求与路线

| 文档 | 用途 |
|---|---|
| [产品需求 v1.0](product-requirements-v1.0.md) | 用户目标、固定约束、场景与能力需求 |
| [现行路线 v2.0](../ROADMAP.md) | P1—P4 与 N1—N8、范围和验收条件 |

产品需求与现行路线决定后续产品范围。现有安全、预算、审核和协议约束继续有效；范围重排本身不构成降低控制要求的授权。

## 技术设计与接入

| 文档 / 目录 | 用途与适用限制 |
|---|---|
| [ADR-0001](adr/ADR-0001-codex-integration.md) | 原接入设计保留为历史；主要入口方向由 ADR-0003 替代 |
| [ADR-0002](adr/ADR-0002-gemini-local-proxy.md) | 用户已裁决的 Gemini 本地代理接入 |
| [ADR-0003](adr/ADR-0003-codex-hosted-entry.md) | Codex 主控 → 项目 Skill + CLI → JH，架构方案批准，宿主验收待完成 |
| [N1 能力归属与旧成果处置](n1-capability-ownership.md) | JH-003 方向已批准；当前实现安全限制以独立复核为准 |
| [文件工具契约与权限边界](file-tools-contract.md) | 目标契约保留；80979f3 新增完整性阻塞，JH-015 继续整改 |
| [文件应用机制说明（JH016 整改依据）](file-tools-apply-mechanism.md) | 原子发布 + 守卫提交的保护范围、失败分支与残留窗口；实现完成，未验证 |
| [N1 架构初次复核](../reports/n1-architecture-review-20261012.md) | ec11584 的五项历史反例、范围裁决与 JH-004 准入 |
| [JH-016 第二批最新复核](../reports/jh016-batch2-review-20261013.md) / [继续整改指令](handoffs/jh015-batch2-followup-20261013.md) | 80979f3 上的未提交工作树：150 单测 3 错误，R2/R3 两轮实测不通过 |
| [文件应用机制草案](file-tools-apply-mechanism.md) | 执行方未验证设计；R1/R4 说明待校正，R2/R3 已被最新复核拒绝 |
| [JH-016 2026-10-12 独立复核](../reports/jh016-review-20261012.md) / [下一批执行指令](handoffs/jh015-after-jh016-20261012.md) | 80979f3 结论 revise；3 项 P1 完整性阻塞及 1 项 P2 变异证据问题 |
| [执行交接](handoffs/n1-20261012-execution.md) / [审查交接](handoffs/n1-20261012-review.md) | 双方对齐同一裁决与证据边界 |
| [新执行 Agent 续接交接](handoffs/jh015-agent-resume-20261013.md) | 2026-10-13 未提交半成品、必读材料、续接要求与源码保护快照 |
| [Muse 主执行与 Gemini 子智能体](handoffs/muse-lead-gemini-workers.md) | Muse 主执行、Gemini 执行子任务；当前架构师会话兼任 JH-016 最终审查，产品运行时配置沿用现有 ADR |
| [Gemini 接入说明](gemini-setup.md) | 当前 Gemini 配置与验证方式 |
| [Muse 接入说明](muse-setup.md) | Muse 配置与验证方式 |
| [适配器契约](adapter-contracts.md) | Provider 请求、响应与失败约定 |
| [危险动作策略](danger-action-policy.md) | 已有动作风险与权限约束；不因重规划自动放宽 |
| [Schema 索引](schemas/README.md) | 结构化协议定义 |
| [原 MVP 验收案例](mvp-acceptance-cases.md) | 旧工程范围下的案例；新产品 P1 使用时逐项映射，不能直接宣称全部覆盖 |
| [独立优化与验收补充](optimization-20261010-side/review.md) | 建议及适用范围；规格案例与已执行测试要分别读取 |

## 历史规划与状态

| 文档 | 定位 |
|---|---|
| [旧八阶段路线 v1.1](architecture-roadmap-v1.1.md) | 保留旧 E1—E8 背景，不再维护后续排期 |
| [旧阶段状态](phase-status.md) | 保留旧阶段结项与历史范围，实时状态迁至根 STATUS.md |

文件原位置保留，避免断开源码、报告和既有审查引用。

## 报告与证据

报告放在 [reports](../reports/)；实际项目已有该目录，沿用它，不另复制交易量化的 records 体系。
重点入口是 [阶段 3 主闭环](../reports/phase3-verification.json)、[修复链](../reports/phase3-repair-verification.json)、[阶段 3 实施报告](../reports/phase3-implementation-report.md) 以及 [JH-002 文件工具真实闭环](../reports/phase4-file-loop-verification.json)。

新增技术决策沿用 adr，协议沿用 schemas，任务实施 / 审查 / 取证沿用 reports。
旧报告保留当时的结果和限制；更正附上时间与证据，不把历史失败覆盖成成功。
