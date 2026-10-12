# STATUS · JH-Agent OS · 会话续接卡

更新：2026-10-12。本文记录当前项目快照；恢复工作时同时核对 Git 工作树与最新交付。

方向见 [产品需求](docs/product-requirements-v1.0.md)，阶段与验收见 [ROADMAP.md](ROADMAP.md)，任务看板见 [TODO.md](TODO.md)，历史见 [CHANGELOG.md](CHANGELOG.md)。

## 一句话现状

旧工程 E3 已结项，旧 E4 首批文件工具（`db86bd6`）已达可审查节点并完成与新路线 N1 的交接与能力归属对齐（JH-002 / JH-003）；当前待独立审查与架构师对旧 E4 范围处置及 N1 接入形态裁决后推进 JH-004。

## 已确认与未确认

| 项目 | 状态与证据范围 |
|---|---|
| 产品方向 | 用户已提供产品需求 v1.0；已整理落盘，后续使用新路线 N1—N8，旧阶段结项保留为历史证据 |
| 旧 E1 / E2 基础能力 | 已有协议、模型适配、任务控制、预算、审核关联和 SQLite；历史验收范围见旧状态记录与报告 |
| 旧 E3 | 架构师于 2026-10-11 批准结项，证据提交 4c25037；归档提交 88cab8f 已存在 |
| Gemini 原缺凭据阻塞 | 已由用户裁决的本地代理通道解除，不再将旧缺密钥记录当成当前阻塞 |
| 旧 E4 文件工具首批（JH-002） | 执行方已完成提交（`50a39de` / `6956300` / `db86bd6`）：含 `FileToolExecutor`（`read_file` / `stage_write` / 审核后受控 `apply`、逐组件路径与 junction 边界、中断恢复演练）、17 项新增单测（全仓 127 项通过，1 跳过）、5 项新变异守卫与 1 次真实 Gemini+Muse+文件工具闭环证据（[phase4-file-loop-verification.json](reports/phase4-file-loop-verification.json)）；已达可审查节点，待独立审查确认 |
| 新 N1（JH-003 能力归属） | 已完成 [N1 能力归属与旧成果处置方案](docs/n1-capability-ownership.md)：列明宿主原生 / JH 新增 / 最小封装 / 暂缓四分法及文件工具保留为“受控暂存落盘守卫”、停止通用 Shell/Git 平台的处置建议；待架构师裁决与 JH-004 实际 Codex 内接入验证 |
| 产品 P1 / 新 N2 | 双模型生成、修复与暂存文件落盘底层能力已有真实调用证据；Codex 内主控发起与完整代表任务验收尚未完成 |

旧 E3 结项与 JH-002 首批证据：
[阶段 3 主闭环](reports/phase3-verification.json)、
[阶段 3 修复链](reports/phase3-repair-verification.json)、
[429 拒绝证据](reports/phase3-repair-fault-429.json)、
[阶段 3 实施报告](reports/phase3-implementation-report.md)、
[JH-002 文件工具真实闭环](reports/phase4-file-loop-verification.json)、
[文件工具契约](docs/file-tools-contract.md)。
报告中的早期缺凭据状态是历史记录，结合后续补遗读取。

## 当前工作与下一步

当前分支为 `phase4-file-tools-batch1`。JH-002（文件工具首批可审查节点）与 JH-003（N1 能力归属与旧成果处置分析）已完成交付与文档同步。

下一步见 [TODO.md](TODO.md)：由独立审查复核 JH-002 代码与证据、由架构师裁决文件工具保留边界（取消原 E4 通用 Shell/Git 平台）与 N1 最小接入形态后，执行 JH-004（最小 Codex 内接入与 ADR 更新）。

## 当前缺口

- 尚缺用户在 Codex 内发起、查看、接续并收到 JH 结果的完整验证（JH-004）。
- 已完成原接入 ADR-0001 与新“Codex 为主要入口”的差异分析（见 [docs/n1-capability-ownership.md](docs/n1-capability-ownership.md)），尚待在 JH-004 中落盘新 ADR 并实测。
- 已结项的数据转换演示与单文件暂存写入演示不能替代 P1 实际开发任务的全部验收；已新增 `file_write_acceptance_evaluator`（3 条件：暂存成功、JSON 语义相等、工作区与授权范围），更广的开发任务验收器仍待 JH-005 扩展。
- 新阶段重划不能自动降低现有审核、权限、预算和恢复要求。

本轮完成了 JH-002 收口复验（全仓 127 项单测与 6 套变异守卫脚本通过，真实文件闭环取证完成）及 JH-003 范围对齐文档；最终是否通过以独立审查与架构师裁决为准。

## 续接时注意

实际项目在 D:/JH-Agent OS；C 盘旧项目目录不作为工作副本。
先查工作树，保留其他执行方的未提交改动。
用户要求方向、需求或阶段变化时主动完成关联检查、文档同步及执行 / 审查双方交接，不等待逐项提醒。具体约定见 [README 的主动对齐说明](README.md#方向变化后的主动对齐)。

详细接入、协议与历史证据按 [docs 索引](docs/README.md) 按需读取，避免把全部历史报告塞进每次上下文。
