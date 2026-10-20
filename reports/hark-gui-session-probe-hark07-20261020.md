# Hark（执行角色）｜Hark-派单-07｜交付报告

日期：2026-10-20（Asia/Shanghai）。规则：AGENTS.md v2 与 docs/collaboration-rules.md，规则依据 6725d60796256a59577d6e50e7732b5145739d4a。

## 0. 分级结论

**云端候选，Linux 可验证部分已实跑；Windows 特有项（目录联接、msvcrt 文件锁、Windows 路径语义、真实 GUI）未验证。不是验收。** 未调用真实模型或真实 Codex app-server。

- R1 同源多实例互斥：Linux 关闭（多对象、多进程、受理未落盘、超时、进程退出、重开均实跑）。
- R2 文件身份与写入路径隔离：Linux 关闭（硬链接、根/祖先/raw/ws 符号链接作为静态链接对应物）；Windows junction 仅以属性分支单测，未真实创建。
- R3 验证器分类：关闭；原“16 检出 + 1 冗余，通过”更正为 **16 检出 + 1 行应判失败（mutated=error），整体本应 fail**。

## 1. 身份与提交

| 项 | 值 |
|---|---|
| 仓库 | djh54188/JH-Agent-OS |
| 基线 | 287317e7810609e7c0e80c45c364c6d3c6373f5d（续接分支头，核对一致） |
| AGENTS.md SHA-256（6725d60） | edc9846b33c908ee6f16597cd2dd920f1c0564489271713a027bbe7e24ea74b3（一致） |
| 分支 | hark-gui-session-probe-20261020（续接，已推送） |
| 功能 R1/R2 | e02faf4108d1d3ebc5082bc0dc4dc493a3d321b2 |
| 功能 R3 | bddaf3f6617b976fc0295653c03724ce4dfa69d8 |
| 报告 | 本提交（分支头） |
| 冻结反例 | 3d29343bee7d5a5b9f4e184f8477fd16fe92e70c，只读副本复跑，哈希见 `frozen-input-sha256.txt` |

未改 main、n1-artifact-entry、PR #1；未合并、未 force push。未改：agent_session.py、agent_session_codex.py、run_history_consistency.py、旧守卫脚本、gui/。

## 2. 实现

### R1：互斥从“对象”改为“来源”（src/session_probe.py）
- `acquire_source_lock(probe_dir)`：按规范路径的进程内线程锁 + `<probe_dir>/probe.lock` 操作系统文件锁（POSIX `flock`，Windows `msvcrt.locking`）。超时（默认 5 s）即拒绝 `probe_source_busy`，不无限等待、不绕过。
- 持久认领 `probe-claim.json`（schema `jh020-gui-session-probe-claim/v1`）：在锁内读登记与认领、算阻断项，**受理前**以临时文件 + fsync + rename 写入 `state=accepted`；工作线程结束时在锁内改 `finished`（附 outcome）。
- 阻断：认领为 accepted（另一对象/进程进行中，或受理后进程退出）→ `probe_claim_unresolved`；认领损坏 → `probe_claim_corrupt`。两者都“待人工处理”，不自动清除、不重发。登记里的未解决 run 仍独立阻断（`probe_unresolved`），认领 finished 不代替登记判断。
- status/prepare 仍零写入（只读认领）。

### R2：外部调用前的身份与静态链接检查
- `history_conflict`：同路径、同规范路径、`samefile`（硬链接/别名）、历史文件位于试运行目录内 → `probe_registry_is_history_source`。
- `link_component`：从文件系统根到试运行目录，逐个已存在组件 lstat，符号链接或 reparse point（含 junction）即拒；最深已存在组件的 realpath 与书写路径不同也拒 → `probe_path_is_link`。
- `dir_problem`：raw、ws、根为链接/非目录即拒。
- `file_identity_problem`：登记、认领、锁文件若存在须为普通文件、非链接、`st_nlink==1`；否则 `probe_path_hardlinked`/`probe_path_is_link`/`probe_path_invalid`。
- 检查时点：prepare/start 读阶段、建目录后、锁内，以及工作线程 **submit 前再查一次**（`_precheck_before_submit`），失败记 `refused_before_dispatch`，零外部调用。

### R3：验证器（scripts/verify_hark06_session_probe_guards.py）
- `classify_python_row`：detected 仅当对照 passed 且变异为 assertion；redundant 仅限声明行，且须对照 passed、**变异后正式目标干净 passed**、验证器自行确认削弱生效（`MUTATION_ACTIVE_CHECKS`）、诊断完整有效（`diagnostic_valid`：整数 thread_starts==1、首次受理已观测、二次结果属已知集合、无异常）。
- error / skipped / zero_tests / 对照失败 / 削弱未生效 / 诊断无效 / 打补丁失败 → 整体 fail。声明冗余行若出现 assertion → 记 detected，并标 `redundancy_contradicted`。
- `run_case`：加载失败、unexpectedSuccess 记 error；expectedFailure 记 skipped。
- replay_check_removed 新正式样例 `TestAtMostOnce.test_repeat_start_of_one_id_never_dispatches_twice`：只断言外部事实（同一 id 重复 start 至多一次 thread/start，接受 replay 或 `probe_not_prepared`）。
- 新增 6 行 R1/R2 守卫削弱：source_claim_ignored、source_lock_disabled、hardlink_identity_ignored、ancestor_link_check_ignored、subdir_link_check_ignored、worker_recheck_ignored。history_source_clash_ignored 改为削弱 `history_conflict`。

## 3. 冻结反例复跑（Linux，只读副本，a/b 两次）

提取：`compare_probe.py`（去随机编号后比较 a/b）。exit 0 只表示取证完成。

| 观测 | 基线 287317e | 本批 |
|---|---|---|
| two_service_objects_one_source | 两次受理，thread/start=2 | 第二个 `probe_claim_unresolved`，thread/start=1 |
| history_hardlink_alias（空） | 派发 1，历史被改 | 拒绝，0，历史字节不变 |
| healthy_history_hardlink_alias | 派发 1，历史被改 | 拒绝，0，历史字节不变 |
| junction_ancestor（Linux 为符号链接） | 派发 1，目标写入登记/raw/ws | `probe_path_is_link`，0，目标为空 |
| junction_root/raw/ws（Linux 符号链接） | 已拒（Linux 下 islink 可见） | 拒绝，0，目标为空 |
| 单源正控、同对象互斥、重开未解决、同路径 | 正常 | 不变 |
| redundant_passed（冻结脚本用 nullcontext，削弱并未生效） | redundant/exit0 | `mutation_inactive`/exit1 |
| redundant_assertion | redundant/exit0 | detected/exit0（记检出，非冗余） |
| redundant_error / skipped / zero_tests | redundant/exit0 | `mutated_*`/exit1 |

a/b 两次去随机后逐字段一致（基线、本批均 True）。说明：冻结脚本里的 passed 情形没有真正施加削弱，故新口径判失败；真实削弱生效的干净 passed 由验证器正式运行（replay 行 redundant、mutation_active=True）和元测试 `test_clean_pass_active_valid_is_the_only_redundant_pass` 覆盖。

## 4. 测试与计数（logs/ 原始日志，`extract_counts.sh` 可重算）

| 项 | 基线 | 提交后（全新 clone） |
|---|---|---|
| tests/test_hark06_session_probe.py | 40 OK | 41 OK（+1 正式样例） |
| tests/test_hark07_source_isolation.py | — | 20 OK |
| tests/test_hark07_verifier_classification.py | — | 17 OK |
| JH018 契约 | 46 OK | 46 OK |
| hark06 GUI harness / JH019 / JH020 / Hark05 | 13 / 4 / 13 / 11 | 13 / 4 / 13 / 11 |
| hark06 守卫验证器 | 16+1 “pass”（口径错误） | **22 detected + 1 redundant，pass**，连跑 3 次一致 |
| 旧前端变异 / Hark05 削弱 / JH020 会话守卫 | 2+2 / 3 / 17 | 不变 |
| 旧 jh020_run_history_guards | exit1（6+1+2 control_failed，既有 Linux 情形） | 同，未改 |
| 全量 无代理 | 748：F21/E63/S2 | 786：F21/E63/S2 |
| 全量 有代理 | 748：F27/E78/S2 | 786：F27/E78/S2 |

786 = 748 + 1 + 20 + 17。失败 ID（`failed_ids.sh`，去重）无代理 84=84、有代理 105=105，diff 0；原始 FAIL/ERROR 行为 85/106，差 1 是同一 ID 在子进程中重复出现。新增测试无一出现在失败列表。

## 5. 故障类覆盖

| 故障类 | 覆盖（Linux 实跑） | 未覆盖 |
|---|---|---|
| 同进程两对象同源 | 受理后未登记即拒；锁内竞态（测试钩子，0.4 s 窗口） | — |
| 多进程 | 4 进程同时 start：合计 thread/start=1、受理 1；持锁进程期间另一进程 `probe_source_busy` | Windows msvcrt 锁 |
| 受理未落盘 / 进程退出 / 重开 | 子进程受理后 `os._exit`：重开后 `probe_claim_unresolved`，0 派发 | 断电级 fsync 语义 |
| 超时 | thread/start 超时 → 登记未解决，其他对象拒绝 | — |
| 硬链接 | 历史别名（空/健康）、登记链到无关文件、认领/锁文件硬链接：拒绝且字节不变 | — |
| 静态链接布局 | 根、祖先、raw、ws 符号链接；受理后 raw 变链接 → submit 前拒绝 | **Windows junction 未真实创建**（仅 reparse 属性单测）；任意 TOCTOU 替换 |
| 合法独立目录 | 正控完成 round_trip_complete，历史字节不变；结束后同源可再试运行 | — |
| 验证器分类 | 五种冻结情形 + 削弱未生效、诊断无效、补丁失败、JS 分支、run_case 各结果 | — |

## 6. 偏离（新增，单列）

1. 新增两个非测试辅助模块 `tests/hark07_probe_child.py`（多进程子进程）、`tests/hark07_verifier_samples.py`（预置 pass/assert/error/skip/空样例）；无 `test_` 前缀，全量发现不会执行。
2. 测试接缝 `_claim_hook`（默认 None，产品不设置）用于确定性复现锁内竞态。
3. 安全拒绝面变宽：试运行目录的**任一祖先**为链接/联接或规范路径不同即拒，可能拒绝放在链接路径下的合法仓库；需改用非链接目录配置 `--session-probe-dir`。
4. 进程退出遗留的 accepted 认领不会自动清除，需人工核对后处理；本批不提供清除入口。
5. 06 验证器的 replay_check_removed 正式目标换成新样例；history_source_clash_ignored 改削弱新函数。这是本批自有新验证器，不涉及已验收旧守卫。

未改已验收契约、预算、成功门；未删反例、未放宽计分；未触发停止条件。

## 7. 未做

真实模型/app-server 往返；Windows junction、msvcrt 锁、Windows 路径与真实 GUI；任意并发文件替换 TOCTOU；断电级持久性；浏览器截图（GUI 未改，仅跑 harness）。一般派发、指定审查、continue/cancel、自动整改、文件应用均未接。

## 8. 下一步路由

- 架构师：独立复核 e02faf4、bddaf3f 与本报告；裁决偏离 3、4。
- 执行AI：复核准入后在 Windows 复跑冻结探针（真实 junction）与新测试，再经 GUI 取得一次真实固定标识往返。
- Hark：待命，不再修改仓库。
- 用户：转发本报告，无需判断。
