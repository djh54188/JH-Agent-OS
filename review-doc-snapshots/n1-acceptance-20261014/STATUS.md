# STATUS · JH-Agent OS · 会话续接卡

更新：2026-10-14。本文记录当前项目快照；恢复工作时同时核对 Git 工作树与最新交付。

方向见 [产品需求](docs/product-requirements-v1.0.md)，阶段与验收见 [ROADMAP.md](ROADMAP.md)，任务看板见 [TODO.md](TODO.md)，历史见 [CHANGELOG.md](CHANGELOG.md)。

## 2026-10-14 · JH-004 两项遗留关闭，进入真实取证

全量实际 **185 项：184 通过、0 失败、0 错误、1 跳过**，没有排除测试。历史来源缺失反例在装配前阻断，没有新调用；8 项定向变异均断言检出、零异常，包含独立错 attempt_id 场景。历史来源 P1 与测试措辞 P2 均关闭，见[离线关闭报告](data/worktrees/n1-artifact-entry/reports/jh004-offline-closure-20261014.md)。

当前无代码整改项；按[有界取证说明](data/worktrees/n1-artifact-entry/docs/handoffs/jh004-real-evidence-20261014.md)提交后验证真实模型、CLI 接续与实际 Codex Skill 发现/调用。代码与离线通过不构成 N1 结项，真实文件应用门禁仍关闭。旧失败证据与原树源码保留，不再重做两项或 A–E。

## 历史快照（2026-10-14，已由上文更新）· JH-004 整改复核：仍 revise，仅余两项

实际全量 180 项：**178 通过、1 失败、0 错误、1 跳过**。R01、R04 原反例关闭；R03 工作树模块指向正确但宿主发现/调用未验证；R02 当前调用 Mock/缺失标记拒绝生效，但历史 completed attempt 缺来源事件仍可 production succeeded（独立两轮复现：4 attempt / 3 source）。

剩余 **1 P1 历史来源覆盖、1 P2 containing/contains 文本测试不一致**，见[最新复核](data/worktrees/n1-artifact-entry/reports/jh004-rereview-20261014.md)及[两项继续整改](data/worktrees/n1-artifact-entry/docs/handoffs/jh004-r02-followup-20261014.md)。已按用户要求保存 WIP 检查点 58d6058；提交后复跑仍为 178 通过/1 失败/1 跳，历史来源缺口仍复现。检查点不是阶段准入。产品实现未由审查会话改写，真实调用 0，门禁关闭，N1 未结项。

执行只处理两项，不重做完整 A–E 或四项。文件编辑可用即可继续修复；Shell 验证由当前架构师会话承接。旧证据与原工作树失败 WIP 保留。

## 历史快照（2026-10-13，已由上文更新）· JH-004 独立复核：revise，转四项整改

当前会话已实际运行全量 165 项：164 通过、0 失败、0 错误、1 跳过；独立行为探针仍确认 **3 项 P1、1 项 P2**：任意两条件误通过、明确 Mock 响应得到 production 成功、Skill 指向原树旧 CLI，以及 events 缺失任务成功空列表。见[最新审查](data/worktrees/n1-artifact-entry/reports/jh004-review-20261013.md)。

当前不是只等待 Shell。按[四项整改交接](data/worktrees/n1-artifact-entry/docs/handoffs/jh004-after-review-20261013.md)修复，离线缺陷关闭前不消耗真实模型取证资源；尚未验证实际 Codex Skill 发现/调用，真实文件应用门禁关闭，N1 未结项。受审代码未被本会话改写或提交，HEAD 仍 9a7c5dd（隔离分支）。有文件编辑能力的执行会话可继续修复，Shell 检查由本架构师会话承接，不重复生成就绪包。

## 历史快照（2026-10-13，当前状态见上）· JH-004 去向：隔离工作树实施中（原树不动）

JH-004 A–E 与 20 项新测试已在 `data/worktrees/n1-artifact-entry`（分支同名）实现，未验证、未提交；原工作树失败 WIP 与证据继续原样保留，不在此处改产品源码。进展与验证命令见新工作树内 STATUS 与实施记录。

## 历史快照（2026-10-13，当前状态见上）· JH-004 隔离已准备，待代码实施

原执行会话报告 Shell 不可用，交付仅为就绪包。当前架构师会话已实际运行 Shell，从 80979f3 创建分支 n1-artifact-entry 与隔离工作树 D:/JH-Agent OS/data/worktrees/n1-artifact-entry。原工作树及失败源码保留；文档检查点已在隔离分支提交为 9a7c5dd；既有源码基线两轮各 145 项（144 通过、1 跳过），数字独立核对一致。A–E 新实现及其测试/真实接入均尚未完成。执行按[启动卡](docs/handoffs/jh004-start-here-20261013.md)进入已建工作树，不重复创建。真实写入门禁关闭，N1 未结项。

## 2026-10-13 · 最新裁决：发布矩阵 STOP，转 JH-004

父目录守卫先行矩阵已由当前会话实际运行：P-A=false（自身 rename/replace 被拒），P-B=false（另一个进程仍可创建子文件），总门禁 **STOP**。脚本退出码 0 是运行完成，不是准入通过。按手册未继续运行本版预期 156 单测、15 变异或后续产品对照，源文件未改。

[最新独立复核](reports/jh016-parent-publish-review-20261013.md)与[原始矩阵](reports/jh015-batch2-continue-20261013/share-matrix.json)为当前依据。W1 不接受为防覆盖保证；“所有邻接写入必报 uncertain”和“纯 Win32 不可能消除”未获证明。

按 [ADR-0004](docs/adr/ADR-0004-n1-artifact-first.md)，停止该自动发布设计，保留失败 WIP/测试/证据与原契约，真实文件写入门禁关闭。当前执行任务改为 **JH-004：真实 Codex Skill + CLI 的双模型生成产物、只读查询、跨进程接续及结果返回**，直接按[新交接](docs/handoffs/jh004-artifact-entry-20261013.md)实施。八阶段不重排，N1 未结项；N2 代码修改和宿主应用验收未获豁免。

## 历史记录（2026-10-13，当前排期已更新）· JH-015 继续整改实现快照（未验证）

针对第二批复核 B2-01/02/03 已实现父目录钉选重设计（`_DirGuard` 跨发布持有、文件守卫发布前相邻释放、旧对象漂移检测报 uncertain），R1/R4 已校正落实，变异扩至 15 项并同步异常类装配，回归 +6。详见 [机制说明第二版](docs/file-tools-apply-mechanism.md)与 [运行手册](reports/jh015-batch2-continue-20261013/runbook.md)。本会话无 Shell：须先跑共享矩阵先行门（GO 才继续），再跑单测/变异/对照。门禁保持关闭，验证通过后才提交并交复核。

## 历史记录（2026-10-13，当前排期已更新）· JH-015 第二批实现快照（未验证）

第二批四项整改已按 [机制说明](docs/file-tools-apply-mechanism.md)实现完毕但**未验证**：`src/file_tools.py` 原子发布接线完成，`src/orchestrator.py` 错误分类分离，`scripts/verify_phase4_guards.py` 装配与口径重写，新增 5 项回归测试。详见 [实施记录](reports/jh015-batch2-20261013.md)。本会话 Shell 不可用，未运行测试、未提交；旧基线结论不移用。门禁保持关闭，待有 Shell 的会话验证后交 JH-016 复核。

## 历史记录（2026-10-13，当前排期已更新）· 当前未提交批次与复核

HEAD 仍为 80979f3；第二批未提交实现已接线，当前会话完成离线复核：150 项单测为 146 通过、3 错误、1 跳过；13 项变异为 5 检出、7 冗余、1 harness_error。新增独立 4 场景两轮观察一致。最新结论见 [第二批复核](reports/jh016-batch2-review-20261013.md)，执行按 [继续整改指令](docs/handoffs/jh015-batch2-followup-20261013.md)。早间续接快照是历史半成品记录，不再据其恢复旧补丁。

## 一句话现状

旧 E3 已结项。JH-015 v2 先行矩阵 STOP，自动文件应用暂缓准入且未通过；当前转 JH-004 真实生成产物接入，不修改用户目标文件。N1 未结项，真实写入门禁保持关闭。

## 已确认与未确认

| 项目 | 状态与证据范围 |
|---|---|
| 产品方向 | 用户已提供产品需求 v1.0；已整理落盘，后续使用新路线 N1—N8，旧阶段结项保留为历史证据 |
| 旧 E1 / E2 基础能力 | 已有协议、模型适配、任务控制、预算、审核关联和 SQLite；历史验收范围见旧状态记录与报告 |
| 旧 E3 | 架构师于 2026-10-11 批准结项，证据提交 4c25037；归档提交 88cab8f 已存在 |
| Gemini 原缺凭据阻塞 | 已由用户裁决的本地代理通道解除，不再将旧缺密钥记录当成当前阻塞 |
| 旧 E4 文件工具首批（JH-002 / JH-015） | **未通过、暂缓准入**：最新 v2 父守卫矩阵 STOP；失败实现/测试/证据保留，真实写入关闭，见[最新复核](reports/jh016-parent-publish-review-20261013.md) |
| 新 N1（JH-003 能力归属） | **方向裁决通过，阶段未结项**：取消通用 Shell/Git 平台；ADR-0004 批准 JH-004 先验证生成产物接入，FileToolExecutor 不在本批默认生产路径 |
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

当前原分支为 phase4-file-tools-batch1，HEAD 80979f3；失败发布实现与并行文档均未提交且保留。父目录发布方案已作 STOP 范围裁决，不再等待 Shell，也不再按旧交接继续打补丁。

下一步：Muse 按 [JH-004 执行交接](docs/handoffs/jh004-artifact-entry-20261013.md)建立明确检查点并隔离新批，从已提交基线实现生产无工具生成入口、固定可信模板、来源/接续绑定及真实 Skill 验证。新批交付直接发回当前架构师兼审查会话。

## 当前缺口

- 尚缺用户在 Codex 内发起、查看、接续并收到 JH 结果的完整验证（JH-004）。
- [ADR-0003](docs/adr/ADR-0003-codex-hosted-entry.md)入口方向已批准；80979f3 含只读查询/摘要与可信装配准备及 7 项离线测试。真实宿主发起、接续、结果返回及生产装配完整验收仍待 JH-004，不能以准备实现代替实际接入。
- JH-015 最新 v2 的前提矩阵 STOP，P-A/P-B 不成立。发布设计停止推进，W1 未获接受，详见 ADR-0004；失败文件应用仍是待准入工作，不阻塞本批无工具生成入口的实施。
- 已结项的数据转换演示与单文件暂存写入演示不能替代 P1 实际开发任务的全部验收；已新增 `file_write_acceptance_evaluator`（3 条件：暂存成功、JSON 语义相等、工作区与授权范围），更广的开发任务验收器仍待 JH-005 扩展。
- 新阶段重划不能自动降低现有审核、权限、预算和恢复要求。

历史测试对应各自源码快照：80979f3 的 145 项、上一版第二批 150 项均不能移用于 v2。本轮 v2 只运行矩阵，未继续其预期 156 项/15 变异；模型调用 0、夹具清理、共享产品源码未改，N1 未结项。

## 续接时注意

实际项目在 D:/JH-Agent OS；C 盘旧项目目录不作为工作副本。
先查工作树，保留其他执行方的未提交改动。
用户要求方向、需求或阶段变化时主动完成关联检查、文档同步及执行 / 审查双方交接，不等待逐项提醒。具体约定见 [README 的主动对齐说明](README.md#方向变化后的主动对齐)。

详细接入、协议与历史证据按 [docs 索引](docs/README.md) 按需读取，避免把全部历史报告塞进每次上下文。
