# Hark-派单-09 交付报告：GUI 试运行工作区绑定

来源：Hark（执行角色）｜Hark-派单-09｜类型：交付报告

## 0. 分级结论

**云端候选，待独立复核与 Windows 集成。不是验收。** Linux 可验证部分已实跑；Windows 盘符、junction、`msvcrt` 与真实 GUI 未验证。未调用真实模型或真实 Codex app-server。旧的错误 cwd 真实往返只沿用为消息证据，不补成工作区通过。

| 完成判据 | 状态 |
|---|---|
| 1 默认/绝对可用；相对一致支持或调用前拒绝；不重复拼接 | Linux 关闭：相对与 `./` 在构造时一次锚定到服务 cwd；`..` 在任何外部调用前拒绝 |
| 2 原生缺失/不符/外来/矛盾时不宣称已核实为空；回显与核实分开 | 关闭（平台无关逻辑）：`latest.workspace.state`，仅 `verified` 给列表 |
| 3 覆盖类别并补自建反例 | 关闭（Linux）；未覆盖见 §5 |
| 4 链接/联接拒绝、认领互斥、未知不重发保留 | Linux 关闭；规范化不折叠 `..`，链接检查照旧 |
| 5 冻结探针副本＋新目录复跑；新增断言；定向削弱造成断言失败 | 关闭：`check_probe09.py` 基线 exit 1、提交后 exit 0；6 行新削弱均 detected |
| 6 回归、完整验证、提交后复验 | 关闭：完整套件 798→819，失败集合不变 |

## 1. 身份与提交

- 规则：AGENTS.md v2，SHA-256 `edc9846b33c908ee6f16597cd2dd920f1c0564489271713a027bbe7e24ea74b3`（核对一致）
- 代码基线：`dc6c5c2c17a7f0b5b07c7d41e59f43ddee75ca4f`（核对一致）；分支 `hark-gui-session-probe-20261020`（续接）
- 反例材料：`49a7d1e8754bed987d397d627d489baa938d3c72`，只用 `git show` 取出四个文件和报告到分支外目录（ANA）；未合并该分支。BASE 为本分支完整候选。冻结探针 SHA-256 `9ac6eb67…1fabde` 与报告所述一致，见 `frozen-input-sha256.txt`。
- 功能：`dcbd832d8f71b49db1ca9e95aa1189228d48d20c`（`src/session_probe.py`、`gui/views.js`、`tests/test_hark09_workspace_binding.py` 新增、`tests/test_hark06_session_probe.py` 夹具、`tests/js/hark06_session_probe_harness.mjs` +2 场景）
- 验证器：`0efdc04deb5a76923c582a4df5c823946b34748c`（`scripts/verify_hark06_session_probe_guards.py` +6 行）
- 报告：本提交
- 未改 main、`n1-artifact-entry`、PR #1；未合并、未 force push。未改 `src/agent_session_codex.py`、`src/agent_session.py`、契约、核心成功门、旧证据。

## 2. 实现

**绑定（判据 1、4）**
- 新 `resolve_probe_dir`：服务构造时只解析一次。绝对路径照用；相对路径 `os.path.join(服务 cwd, 配置)`；`.` 片段由 `Path` 词法去除；含 `..` 的路径（相对或绝对）记为 `probe_dir_parent_segment`，**不折叠**；Windows 上还要求带盘符和根，否则 `probe_dir_not_absolute`（未在 Windows 实测）。
- 路径问题作为第一项布局阻断，prepare/start 在任何目录创建、认领、锁、外部调用前拒绝。
- 之后的布局检查、锁、认领、计划工作区、适配器进程 cwd、thread/start 与 turn/start 的 cwd 都用同一个绝对字符串；之后服务 cwd 再变也不重锚。
- `plan.workspace_binding` 公开 configured / resolution / resolved_probe_dir。

**观测（判据 2）**
- `latest.workspace = {state, verified, reason, planned, dispatched, native_sources, capture, files, verified_empty, scope}`。
- 原生依据两类：已核实原始证据中的 `thread/started` 通知（`params.thread.id/cwd`），以及默认工厂对 stdio transport 的包装所记录的 thread/start **响应** cwd（`raw/probe-native-<id>.json`，原子写，写前查来源布局）。适配器代码未改，只替换传入的 `transport_factory`。
- 判定顺序：派发非绝对 → `dispatch_not_absolute`；派发≠计划 → `dispatch_mismatch`；捕获损坏 → `native_evidence_invalid`；原始证据未核实 → `native_evidence_unverified`（仅有捕获也不能核实）；无 thread → `native_thread_unknown`；同线程无 cwd：有外来线程 cwd → `native_foreign_only`，否则 `native_missing`；同线程不一致或类型无效 → `native_conflict`；一致但≠计划 → `native_mismatch`；全部一致 → `verified`。比较只折叠大小写与尾分隔符，不做 `..` 折叠或 realpath。
- 只有 `verified` 时 `workspace_files` 才是列表、`verified_empty` 才是布尔；其余为 `None`。`outcome`（固定标识回显）计算不变、独立表达。
- GUI：新增“工作区核实”“计划工作区”两栏；未核实时“隔离工作区文件”显示“未核实：不对实际工作区内容作断言”，不再显示“无”。缺少 `workspace` 的旧载荷也显示未核实。

**验证器（判据 5）**
新削弱行：`relative_probe_dir_unanchored`、`parent_segment_allowed`、`workspace_verification_skipped`、`same_thread_filter_ignored`、`native_conflict_check_ignored`（Python）、`workspace_unverified_shown_as_listing`（GUI 副本）。

## 3. 冻结探针复跑（Linux；ANA 副本，输出为 BASE 下全新 `data/workspace-review-fresh-{a,b}`）

| 输入类 | 基线 dc6c5c2 | 提交后 |
|---|---|---|
| 绝对、绝对空格中文 | 协议 cwd 一致；完成/[] | 一致；`verified`，[]，verified_empty=true |
| 相对、`./`、相对空格中文 | 传相对串，有效 cwd 重复拼接；完成/[] | 传绝对串＝计划＝有效 cwd；`verified` |
| 父级片段 `middle/../probe` | 重复拼接；完成/[] | `probe_dir_parent_segment` 拒绝，0 次派发 |
| 同线程原生不符 | 完成/[] | 回显完成；`native_mismatch`，列表 None |
| 原生缺失 | 完成/[] | `native_missing`，None |
| 只有外来线程 | 完成/[] | `native_foreign_only`，None |
| 响应与通知矛盾 | 完成/[] | `native_conflict`，None |

- 共 10 项观测；冻结探针在两边均 exit 0，只表示观测结束。a/b 两次去除输出目录名、随机 probe id、Python 版本后逐字段一致（`compare_probe09.py`）。
- 新断言层 `check_probe09.py` 以只读 status() 重读每个用例登记，断言真实 state、列表、verified_empty、回显、计划路径及协议 cwd：基线 a/b **exit 1**（10/10 失败，基线无 `workspace` 观测且父级未拒绝），提交后 a/b **exit 0**。
- 冻结探针自身的 `workspace_matches` 是模拟器真值，未作为产品验证结果使用。
- 派单-07 `probe_boundaries.py`、派单-08 `probe_cleanup.py` 冻结探针：提交后与基线结果一致（07 去除随机 id 后一致，08 原样一致）。

## 4. 计数（`logs/` 原始日志；`extract_counts.sh`、`failed_ids.sh`、`compare_probe09.py` 可重算）

- test_hark06_session_probe 41 OK；test_hark07_source_isolation 20 OK；test_hark07_verifier_classification 21 OK；test_hark08_cleanup_isolation 8 OK；JH018 46 OK（均不变）
- 新 test_hark09_workspace_binding **21 OK**（Linux；其中 2 项符号链接类在 Windows 跳过）
- hark06 harness 13→**15**/15；JH019 4/4、JH020 13/13、Hark05 11/11 不变
- hark06 守卫验证器：25+1 → **31 detected + 1 redundant，pass**，连跑 3 次一致
- 旧前端变异 2+2、Hark05 削弱 3、JH020 会话守卫 17 不变；旧 jh020 历史守卫 exit 1（既有 Linux 6+1+2 control_failed，未改）
- 完整套件：无代理 798 F21/E63/S2 → **819** F21/E63/S2；有代理 798 F27/E78/S2 → **819** F27/E78/S2；819=798+21。去重失败 ID 84=84、105=105，diff 0，新测试不在失败列表
- 提交后在全新 clone（0efdc04）上复验

## 5. 覆盖

已覆盖（Linux 实跑）：绝对；相对；前置 `./`；中间 `.`；相对/绝对 `..`；空格＋中文（相对与绝对）；构造后服务 cwd 改变；同线程不符、缺失、只外来线程、响应与通知矛盾、类型无效、捕获损坏、原始证据未核实、仅响应或仅通知；派发非绝对（真实 Windows 形态的单元复现）、派发≠计划；`..` 不折叠；已核实目录有文件时如实列出；相对配置下认领正常结束、thread/start 超时后不重发；相对路径经过链接仍拒绝；链接后接 `..` 被父级规则拒绝且不在任何一侧建目录。

未覆盖：Windows 盘符相对（`D:foo`）、根相对、UNC/设备路径的新行为与真实 junction；`msvcrt` 锁；任意 syscall 间 TOCTOU；断电持久性；全部文件系统类型；真实 app-server 的 thread/start 响应字段形态（只按 `result.cwd` 与 `result.thread.cwd` 读取，未见实物）；真实模型/app-server/Windows GUI。

## 6. 偏离

1. `..` 一律拒绝（含绝对路径中的 `..`），不做支持——判据 1 允许的“调用前明确拒绝”。
2. 修改了 Hark-06 自己的测试夹具 `EchoTransport`：新增可选 `native_cwd`（Rig 默认开），发出同线程 `thread/started`；不改冻结探针所用子类的默认行为。06 测试多一行断言 `verified`。
3. hark06 GUI harness 增加 2 个场景（13→15），未另建 harness。
4. 新增服务端捕获文件 `raw/probe-native-<id>.json`；仅凭它不能核实（要求原始证据已核实）。
5. 新状态字段只增不删；`workspace_files` 在未核实时由 `[]` 改为 `None`——这是本批目的，GUI 已适配。
6. 功能提交说明写“22 测”，实际 21 测；以本报告与日志为准。

未改已验收适配器/契约、核心成功门、旧证据；未触发停止条件。

## 7. 未做

真实模型/app-server、Windows 验收、视觉重设计、一般派发、指定审查、continue/cancel、自动整改、文件应用；自动清理遗留认领。
