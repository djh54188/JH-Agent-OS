# docs · JH-Agent OS · 文档索引

更新：2026-10-17。现行方向为模块化统一 GUI 与可替换 Agent 协作中间站；本次只更新文档、不派发执行任务。
实时状态见 [STATUS](../STATUS.md)，任务见 [TODO](../TODO.md)，历史见 [CHANGELOG](../CHANGELOG.md)。

## 当前方向与设计

| 文档 | 定位 |
|---|---|
| [产品需求 v1.1](product-requirements-v1.1.md) | 当前用户目标与约束，替代 v1.0 中冲突部分 |
| [模块化架构](modular-architecture.md) | 六模块、GUI 三层、会话寻址、消息与适配契约设计 |
| [ADR-0005](adr/ADR-0005-modular-gui-agent-hub.md) | 用户确认的入口/GUI/可替换 Agent 决策，尚未实现 |
| [路线 v3.0](../ROADMAP.md) | 保持 N1—N8，N2 补充模块化 GUI 与外部 Agent 闭环 |

文档中的目标接口不等于生产已支持；当前实现、验收和门禁见 STATUS。GUI 不直接操作数据库或工具，流程不能增加权限；原 production complete/FileToolExecutor 关闭不变。

## 现有能力与证据

| 文档 | 已覆盖与限制 |
|---|---|
| [N1 结项](../reports/n1-acceptance-20261014.md) | 原 Skill/CLI 生成入口通过，不包括统一 GUI/通用外部 Agent |
| [JH-005 验收](../reports/jh005-acceptance-20261015.md) | 原规格/夹具封存通过，夹具字节保留 |
| [JH-006 host-entry](../reports/jh006-host-entry-20261017.md) | c6c0af57 接线批通过，生产桥接仍未准入 |
| [真实登记输入盘点](../reports/jh006-real-host-input-inventory-20261017.md) | d650bd3 只读盘点；真实库/等待交付/产物/审核缺失，未发真实调用 |
| [离线绑定审查](../reports/jh006-binding-review-20261016.md) | 既有 Codex 采集绑定，不自动泛化到其他 Agent |
| [桌面采集审查](../reports/jh006-host-capture-review-20261016.md) | 已测协议与归档可信边界，不证明任意应用或当前桌面会话可被唤醒 |
| [N2 原规格](n2-acceptance-spec.md) / [宿主交付契约](host-delivery-contract.md) | 原代表任务与证据要求保留，新 GUI/Agent 验收需补充 |
| [模型适配器契约](adapter-contracts.md) | ModelAdapter API 通道；外部 AgentSessionAdapter 另行设计 |
| [Gemini 接入](gemini-setup.md) / [Muse 接入](muse-setup.md) | 已批准模型通道配置，不固定未来应用/角色 |
| [危险动作策略](danger-action-policy.md) / [Schema 索引](schemas/README.md) | 现有控制与协议，本次不修改生产 Schema 或授权 |
| [文件工具契约](file-tools-contract.md) / [失败应用机制](file-tools-apply-mechanism.md) | 原契约保留，自动应用未准入 |
| [发布矩阵 STOP](../reports/jh016-parent-publish-review-20261013.md) | 旧失败设计不复活 |

## 历史方向、ADR 与交接

| 文档 | 历史定位 |
|---|---|
| [产品需求 v1.0](product-requirements-v1.0.md) | 原始需求，正文保留，入口/GUI 冲突由 v1.1 替代 |
| [N1 能力归属](n1-capability-ownership.md) | 原四分法与处置背景，现行边界见 ADR-0005/模块化架构 |
| [ADR-0001](adr/ADR-0001-codex-integration.md) | 原 Codex 辅助/接入设计，不作为新 GUI 唤起能力证明 |
| [ADR-0002](adr/ADR-0002-gemini-local-proxy.md) | 现有 API 接入配置继续有效，未来角色可配置 |
| [ADR-0003](adr/ADR-0003-codex-hosted-entry.md) | 已验收 Skill/CLI 兼容入口，主要 GUI 方向由 ADR-0005 替代 |
| [ADR-0004](adr/ADR-0004-n1-artifact-first.md) | 历史 N1 范围与文件 STOP 保留，不豁免 N2 |
| [旧 E 路线](architecture-roadmap-v1.1.md) / [旧阶段状态](phase-status.md) | 历史原文，不作为当前排期 |
| [原 MVP 案例](mvp-acceptance-cases.md) / [优化建议](optimization-20261010-side/review.md) | 历史控制案例与建议，逐项映射后复用 |
| [Muse/Gemini 研发交接](handoffs/muse-lead-gemini-workers.md) | 历史分工，不固定后续执行 Agent |
| [原执行交接](handoffs/n1-20261012-execution.md) / [原审查交接](handoffs/n1-20261012-review.md) | 原批次指令；当前暂停新派发，不能自动重启 |

handoffs/ 下带日期的文件保留各自批次要求与当时状态，以根 STATUS/TODO 和最新明确授权决定是否执行；不从旧正文的“当前/下一批”推定新任务已派发。
reports/ 是历史取证与审查资料，保留原字节；本次不改写报告、失败证据或正式封存夹具。新增方向 ADR 沿用 adr/，不新增平行权威源。
