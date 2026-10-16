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
| [ADR-0004](adr/ADR-0004-n1-artifact-first.md) | 当前范围：N1 先验证生成产物接入，自动文件应用暂缓准入，N2 完整验收保留 |
| [JH-004 当前执行交接](handoffs/jh004-artifact-entry-20261013.md) | 真实 Skill/CLI、无工具生产装配、固定模板、查询与跨进程接续；需实际实施验证 |
| [父目录发布最新复核](../reports/jh016-parent-publish-review-20261013.md) / [矩阵回执](../reports/jh016-parent-publish-20261013/gate-verification.json) | P-A/P-B false，STOP；没有运行 v2 后续 156 单测/15 变异 |
| [N1 能力归属与旧成果处置](n1-capability-ownership.md) | JH-003 方向已批准；当前实现安全限制以独立复核为准 |
| [文件工具契约与权限边界](file-tools-contract.md) | 原契约保留；组件未通过、自动应用暂缓，真实写入门禁关闭 |
| [文件应用机制说明 v2（未通过设计）](file-tools-apply-mechanism.md) | 父目录发布方案先行矩阵 STOP；保留失败设计，不作为当前实施指令 |
| [N1 架构初次复核](../reports/n1-architecture-review-20261012.md) | ec11584 的五项历史反例、范围裁决与 JH-004 准入 |
| [JH-016 第二批前版复核](../reports/jh016-batch2-review-20261013.md) / [历史整改指令](handoffs/jh015-batch2-followup-20261013.md) | 历史未提交实现 150 单测 3 错误；后续 v2 矩阵 STOP，当前整改排期已暂停 |
| [JH-016 2026-10-12 独立复核](../reports/jh016-review-20261012.md) / [历史整改指令（已暂停）](handoffs/jh015-after-jh016-20261012.md) | 80979f3 历史结论 revise；当前范围以 ADR-0004/JH-004 为准，旧完整性问题未被豁免 |
| [执行交接](handoffs/n1-20261012-execution.md) / [审查交接](handoffs/n1-20261012-review.md) | 双方对齐同一裁决与证据边界 |
| [历史执行 Agent 续接交接](handoffs/jh015-agent-resume-20261013.md) | 早间源码保护记录，已暂停；不据此恢复旧半成品或继续整改 |
| [Muse 主执行与 Gemini 子智能体](handoffs/muse-lead-gemini-workers.md) | Muse 主执行、Gemini 执行子任务；当前架构师会话独立审查 JH-004；JH-016 历史复核为 STOP，产品运行时配置沿用现有 ADR |
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
