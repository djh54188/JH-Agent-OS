# STATUS · JH-Agent OS · 会话续接卡

更新：2026-10-12。本文记录当前项目快照；恢复工作时同时核对 Git 工作树与最新交付。

方向见 [产品需求](docs/product-requirements-v1.0.md)，阶段与验收见 [ROADMAP.md](ROADMAP.md)，任务看板见 [TODO.md](TODO.md)，历史见 [CHANGELOG.md](CHANGELOG.md)。

## 一句话现状

旧工程 E3 已结项。N1 架构方向已裁决：取消自研通用 Shell/Git 平台，采用 Codex 主控 → 项目 Skill → 结构化 CLI → JH。独立离线复核在 JH-002 复现五项文件边界/并发/审核绑定缺陷，当前需先完成 JH-015 整改；JH-004 可做 ADR/只读准备，真实文件写入接入待修复独立通过。N1 未结项。

## 已确认与未确认

| 项目 | 状态与证据范围 |
|---|---|
| 产品方向 | 用户已提供产品需求 v1.0；已整理落盘，后续使用新路线 N1—N8，旧阶段结项保留为历史证据 |
| 旧 E1 / E2 基础能力 | 已有协议、模型适配、任务控制、预算、审核关联和 SQLite；历史验收范围见旧状态记录与报告 |
| 旧 E3 | 架构师于 2026-10-11 批准结项，证据提交 4c25037；归档提交 88cab8f 已存在 |
| Gemini 原缺凭据阻塞 | 已由用户裁决的本地代理通道解除，不再将旧缺密钥记录当成当前阻塞 |
| 旧 E4 文件工具首批（JH-002） | **需整改**：执行提交及正常真实闭环保留；本轮在 `ec11584` 独立复现五项阻塞缺陷，见[复核报告](reports/n1-architecture-review-20261012.md)和[探针结果](reports/n1-architecture-probes-20261012.json)。执行方报告 127 项（126 通过、1 跳过）及既有守卫；本轮未重跑该全量检查 |
| 新 N1（JH-003 能力归属） | **方向裁决通过**：四分法与最小组件定位保留，取消通用 Shell/Git 平台；[ADR-0003](docs/adr/ADR-0003-codex-hosted-entry.md)批准 Skill + CLI 的入口方向。组件安全性与实际宿主接入尚未通过 |
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

当前分支为 `phase4-file-tools-batch1`，本轮审查基线 `ec11584`。JH-003 的边界裁决已落盘，JH-002 未通过；新增 JH-015 跟踪五项整改。

下一步见 [TODO.md](TODO.md)：先修复并独立复核 JH-015，再完成 JH-004 的可信 CLI 装配与实际 Codex 内接入。等待期间可做 ADR/只读准备。双方使用[执行交接](docs/handoffs/n1-20261012-execution.md)和[审查交接](docs/handoffs/n1-20261012-review.md)，未经复核的组件不接入真实工作文件。

## 当前缺口

- 尚缺用户在 Codex 内发起、查看、接续并收到 JH 结果的完整验证（JH-004）。
- [ADR-0003](docs/adr/ADR-0003-codex-hosted-entry.md)已批准入口反转；现有 CLI 默认工具/验收装配、真实缺配置退回 Mock、只读查询建库、接续与授权边界仍需补齐，不能仅包一层 Skill 宣称接入完成。
- JH-015：暂存 junction 事后拒绝却已截断、固定临时名碰撞、版本检查后并发更新覆盖、替换前父目录 junction 越界、审核 B 却应用 A 并 succeeded，均已离线复现。
- 已结项的数据转换演示与单文件暂存写入演示不能替代 P1 实际开发任务的全部验收；已新增 `file_write_acceptance_evaluator`（3 条件：暂存成功、JSON 语义相等、工作区与授权范围），更广的开发任务验收器仍待 JH-005 扩展。
- 新阶段重划不能自动降低现有审核、权限、预算和恢复要求。

执行方的正常闭环与全量验证为前次交付证据；本轮独立运行五项反例（全部复现），未调用 API、未修改共享实现、未重跑全量单测/既有守卫。架构方向批准与代码验收分开，N1 尚未结项。

## 续接时注意

实际项目在 D:/JH-Agent OS；C 盘旧项目目录不作为工作副本。
先查工作树，保留其他执行方的未提交改动。
用户要求方向、需求或阶段变化时主动完成关联检查、文档同步及执行 / 审查双方交接，不等待逐项提醒。具体约定见 [README 的主动对齐说明](README.md#方向变化后的主动对齐)。

详细接入、协议与历史证据按 [docs 索引](docs/README.md) 按需读取，避免把全部历史报告塞进每次上下文。
