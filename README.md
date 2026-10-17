# JH-Agent OS

模块化的个人 AI Agent 协作操作层：统一 GUI 作为用户入口，中间站连接可替换的 Agent 应用与会话，Codex 保留默认主控、架构与独立审查职责。

**2026-10-17 方向已确认、实现未完成。** 当前只同步文档，暂不向执行 AI 派发新任务；统一 GUI、通用外部 Agent 接入与自动审查不能从既有 CLI/API 能力推定已支持。

## 从哪里读起

| 文档 | 回答的问题 |
|---|---|
| [产品需求 v1.1](docs/product-requirements-v1.1.md) | 当前定位、入口、功能和约束 |
| [模块化架构](docs/modular-architecture.md) | 模块、依赖、GUI 分层、会话与消息边界 |
| [ADR-0005](docs/adr/ADR-0005-modular-gui-agent-hub.md) | 本次决策替代什么、保留什么 |
| [STATUS](STATUS.md) | 已验收、未实现、阻塞和当前暂停边界 |
| [ROADMAP v3.0](ROADMAP.md) | P1—P4 / N1—N8 范围与验收 |
| [TODO](TODO.md) | 具体待办，规划不等于已派发 |
| [CHANGELOG](CHANGELOG.md) / [docs 索引](docs/README.md) | 历史与技术文档入口 |

换会话先读 STATUS；方向和模块以需求/ADR/架构为准；具体任务以 TODO 与明确交接为准。历史需求 [v1.0](docs/product-requirements-v1.0.md) 保留，冲突内容由 v1.1 替代。

## 产品边界

- GUI、应用接口、协作核心、Agent 适配器、审查与控制、存储与证据分离；GUI 内再分页面、公共组件和数据访问层。
- 用户在 GUI 提目标、看进度、处理授权和接收交付；模型仍在各自 Agent 环境运行，中间站登记会话并按预设下一步流转。
- 支持方向包含跨 Agent 应用及同应用不同会话；应用、会话、模型、角色独立配置。ZCode 只是首个候选，接口待实测。
- 复用宿主文件/终端/Git/上下文/子智能体，不重建完整 Agent Runtime 或通用工具平台。ModelAdapter API 通道继续保留，不能冒充外部 Agent 的完整执行能力。
- 交付触发独立审查，审核退回有界整改；消息已投递、审查已运行、任务已成功分别记录。流程配置不能增加权限。
- 不默认能唤醒当前 Codex 桌面会话，不默认另建架构师会话；自动派发/接续须由实际接口证明。
- 换 GUI 不改核心，换 Agent 不改流程，调整流程不改权限规则；首版同一工程内模块化，技术栈和桌面封装尚未锁定。

## 当前实现与已验收范围

已验收实现工作树：`D:/JH-Agent OS/data/worktrees/n1-artifact-entry`，分支 `n1-artifact-entry`。`D:/JH-Agent OS` 原树保留 80979f3 与失败 JH-015 WIP，不用于新实现或验收。
N1 按原 Skill/CLI 生成范围已结项，见 [裁决](reports/n1-acceptance-20261014.md)。JH-006 host-entry 接线批 c6c0af57 已由架构师批准，见 [运行报告](reports/jh006-host-entry-20261017.md)；d650bd3 仅补 [输入盘点](reports/jh006-real-host-input-inventory-20261017.md)。
N2/P1 未完成。生产 complete 与 FileToolExecutor 继续关闭；新 GUI 与外部 Agent 自动流转尚未实现/验收。历史来源登记四缺口不因本次文档更新消失，也不自动启动补跑。

既有开发入口（在实现工作树运行）：

    python -m src.cli --help
    python -m unittest discover -s tests

现有 [Gemini API](docs/gemini-setup.md)、[Muse API](docs/muse-setup.md) 和 [适配器契约](docs/adapter-contracts.md) 保留原实测范围；凭据不写入文档。模型取证按明确批次需要运行，不无意义重跑。

## 角色与协作

默认主控/架构/最终研发审查为当前 Codex 会话；执行方使用哪个 Agent/模型由当前授权与能力决定，不将 Muse/Gemini 或 ZCode 写成不可替换角色。
原 Gemini Worker / Muse Reviewer 作为已验证的 API 运行配置保留；研发主执行、子任务与产品运行配置分别记录。执行参与者自检不代替独立审查。
用户当前要求先讨论架构并更新方向文档，**不向执行 AI 派发新任务**。旧 [Muse/Gemini 交接](docs/handoffs/muse-lead-gemini-workers.md) 与其他带日期交接是历史批次资料，不能自动启动旧任务。
普通实现选择由执行方负责；当前架构师独立核对代码、证据及阶段范围，不直接采信完成声明。只在确有整改、外部输入或裁决时生成对应内容，无新增事项不制造转发。

## 方向变化后的主动对齐

依据用户 2026-10-12 要求，方向变化主动检查需求、模块、路线、任务、ADR、进行中批次、审查与历史验收，并同步文档引用；区分规划、实现、验证和结项，不用新方向覆盖失败证据。
仅对需要行动且已有授权的接收方生成交接；用户暂停派发时先同步文档，不因主动对齐自行启动实施。
任务状态、项目状态、流程和权限各有归属；已有危险动作、预算、审核绑定和不确定性要求继续有效。旧 E 阶段、新 N 阶段与产品 P 分组不混用。
