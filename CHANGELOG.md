# CHANGELOG · JH-Agent OS

按日期记录已经发生的变更。当前状态见 [STATUS.md](STATUS.md)，路线见 [ROADMAP.md](ROADMAP.md)，待办见 [TODO.md](TODO.md)。

## 2026-10-12 · 主动对齐约定（本地文档更新，尚未提交）

- 根据用户明确要求，将方向 / 需求 / 阶段变化后的关联检查、文档同步及执行与审查双方交接写入 README.md。
- 在 STATUS.md 增加续接提示，方便后续会话沿用。
- 本次仅更新协作说明与记录，不改变已有代码审查、阶段结项或权限规则。

## 2026-10-12 · JH-002 可审查节点交接与 JH-003 能力归属对齐

- **JH-002（旧 E4 文件工具首批到达可审查节点）**：
  - 完成 `src/file_tools.py`（`FileToolExecutor`：工作区受限 `read_file`、`stage_write` 暂存写入、审核 pass 后受控原子 `apply_staged` 及中断恢复 `recover_apply`；逐组件真实目录行走、盘符/大小写/同名前缀防护、全祖先与最终组件 junction/symlink 拦截、post-open 包含性复查）。
  - 完成 `docs/schemas/tool_plan.v1.json`、`src/acceptance.py::file_write_acceptance_evaluator`（3 条件：暂存成功、JSON 语义相等、工作区与授权范围）及 `Orchestrator` `file_tools` 模式接线（提交 `50a39de`）。
  - 完成离线测试 17 项（`tests/test_phase4_file_tools.py`，全仓 127 项通过、1 跳过）、变异守卫 5/5 检出（`scripts/verify_phase4_guards.py`）以及 1 次真实 Gemini + Muse + 文件工具闭环取证（`reports/phase4-file-loop-verification.json`，提交 `6956300` / `db86bd6`）。
  - 保护共享工作区：拆分撤回曾误含并行文档的临时提交，保持根文档工作树完好。
- **JH-003（N1 能力归属与旧成果处置分析）**：
  - 新增 [docs/n1-capability-ownership.md](docs/n1-capability-ownership.md)，按新路线 N1—N8 划分宿主原生、JH 必须新增、最小封装、暂缓四类能力矩阵。
  - 给出旧 E1—E4 成果处置方案：保留 E1—E3 全部协议/控制/双模型闭环能力；建议将 JH-002 的 `FileToolExecutor` 作为“跨模型产物受控暂存落盘守卫”保留，同时停止原旧 E4 计划中的通用 Shell/Git 工具平台建设。
  - 同步更新 `STATUS.md`、`TODO.md`、`docs/README.md`，单独列出待独立审查与架构师裁决项。

## 2026-10-12 · pm-dispatch 候选参考（本地交付，尚未提交）

- 根据用户确认，在 TODO.md 登记 pm-dispatch 为待评估设计参考，关联 N1 / N2 的现有任务。
- 记录自包含任务卡、派发前语义检查、任务隔离、实际产物验收、阻塞反馈及有界并行的候选借鉴点与验证条件。
- 来源仅为用户截图，完整 Skill / 源码和实际收益尚未验证；本轮仅登记候选，未修改代码、运行模型或改变阶段验收门槛。

## 2026-10-11 · 本轮文档整理（本地交付，尚未提交）

- 只读参考 D:/交易量化 的 README、STATUS、ROADMAP、TODO、CHANGELOG 与 docs 索引结构。
- 新增本项目的根目录文档入口、续接卡、路线、任务看板和变更记录。
- 将用户提供的产品需求 v1.0 整理到 docs/product-requirements-v1.0.md。
- 将后续路线调整为 P1—P4 产品分组与 N1—N8 开发阶段，保留旧 E1—E8 的历史与证据。
- 给两份旧路线 / 状态文档增加历史说明与现行入口，不删除原始验收内容。
- 新增 docs/README.md，区分现行需求、设计、历史规划与取证报告。
- 本轮未运行代码测试或真实模型调用；没有给正在开发的文件工具作验收判断。交易量化目录只读参考。

## 2026-10-11 · 已有阶段 3 成果（历史摘要）

- Gemini Worker 按用户裁决改经本地代理 Anthropic Messages 接入，记录 ADR-0002；实现提交 8aa1024。
- 主闭环真实证据提交 a279b8f。
- 严格成功门禁、非法用量拒绝与修复取证补强提交 f54ac16。
- 代理 429 限流时正确判失败的独立证据提交 a8bdef3。
- 真实 revise → 修复 → pass → succeeded 修复链证据提交 4c25037，并获架构师阶段结项。
- 旧阶段 3 结项记录与验证归档提交 88cab8f。

详情见 [阶段 3 实施报告](reports/phase3-implementation-report.md)、[真实主闭环](reports/phase3-verification.json) 和 [真实修复链](reports/phase3-repair-verification.json)。
上述是历史证据索引，不代表本轮重新执行或当前供应商实时健康。

## 更早的工程基础

原阶段 1 / 2 已建立协议、适配器、控制层和持久化。保留 [阶段 1 报告](reports/phase1-completion-report.md)、[阶段 2 验证](reports/phase2-verification.json) 与 [控制层补强审查](reports/phase2-hardening-review.md)；不在此重建未经逐条复核的完整提交史。
