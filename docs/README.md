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
| [ADR-0001](adr/ADR-0001-codex-integration.md) | 原 Codex 接入设计；与新主要入口的职责关系已在 N1 能力归属分析中复核，待 JH-004 更新 ADR |
| [ADR-0002](adr/ADR-0002-gemini-local-proxy.md) | 用户已裁决的 Gemini 本地代理接入 |
| [N1 能力归属与旧成果处置](n1-capability-ownership.md) | JH-003 交付：宿主原生 / JH 新增 / 最小封装 / 暂缓四分法及旧 E1—E4 处置方案 |
| [文件工具契约与权限边界](file-tools-contract.md) | JH-002（原旧 E4 首批）交付：`read_file` / `stage_write` / 审核后受控 `apply` 与路径/junction 边界 |
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
