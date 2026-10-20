# Hark（执行角色）｜Hark-派单-08｜交付报告

日期：2026-10-20（Asia/Shanghai）。续接派单-07，原提交与证据保留不动。

## 0. 分级结论

**云端候选，Linux 可验证部分已实跑；Windows 特有项未验证。不是验收。** 未调用真实模型或真实 Codex app-server。

- R2 尾项（拒绝后收尾写入）：Linux 关闭。冻结 `probe_cleanup.py` 五种布局在提交后链接目标的文件集合不变，合成认领保持 `accepted`；本批 8 项定向测试另核字节不变。
- R3 尾项（明确 inactive 仍记为检出）：关闭（平台无关逻辑）。`mutation_active=False` 与 passed/assertion/error/skipped/zero_tests 任一组合都记 `mutation_inactive`、不计数；冻结 main 入口反例由 exit0/pass 变为 exit1/fail。`None` 与明确 `False` 分开报告。
- Windows junction 与 msvcrt 锁的收尾路径：**未测**。

## 1. 身份与提交

- 仓库 djh54188/JH-Agent-OS；基线 `5c466b6980fb97e7759fde79d0583e1e12974051`（核对一致）；规则依据 `6725d60796256a59577d6e50e7732b5145739d4a`。
- 分支 `hark-gui-session-probe-20261020`（续接，已推送）。
- 功能 R2：`0cff6ee7078eba6c03eed0b0c7822d4cda9f6c4a`（`src/session_probe.py`，新增 `tests/test_hark08_cleanup_isolation.py`）
- 功能 R3：`99abcd71db08931143612f36c9f6953718751c79`（`scripts/verify_hark06_session_probe_guards.py`，`tests/test_hark07_verifier_classification.py`）
- 报告：本提交（只含报告与证据）。
- 冻结反例：自 `0250dc651058d0d46b3e54d4d9b149d1eabafb9d` 只读取出副本复跑，哈希见 `frozen-input-sha256.txt`；未改冻结探针或旧结果。
- 未改 main、n1-artifact-entry、PR #1；未合并、未 force push。未改 GUI、已验收适配器、会话契约、历史校验器、旧守卫。

## 2. 实现

R2（`src/session_probe.py`）：
- `acquire_source_lock` 打开锁文件前自行复查来源目录（`source_dir_problem`：根到试运行目录逐级链接/联接、普通目录）；新增 `create=False`，锁文件已不存在时拒绝而不重建。
- 受理时记录试运行目录与本对象写入的认领文件的 `(st_dev, st_ino)`。
- `_finish_claim` 只在以下全成立时把认领标为 finished：来源目录可信、仍是受理时同一目录、认领仍是同一文件、认领/锁为普通单链接文件、认领内容仍是本次 `accepted`。锁内再查一次。
- 任一不成立：不创建、不改写任何文件，认领保持阻断；`status.last_error` 增加 `cleanup_refused{code,message}`，写明待人工处理。不自动清理、不重发。
- submit 前复查同时核对来源身份（目录或认领在受理后被普通目录/文件替换→`probe_source_replaced`/`probe_claim_replaced`，零外部调用）。
- raw/ws 链接不影响认领位置：仍在真实来源内正常标记 finished（outcome=`refused_before_dispatch`）。

R3（验证器）：
- `classify_python_row`：控制通过后，`mutation_active is False` 立刻返回 `mutation_inactive`（不计数，extra 标 `explicitly_inactive`），不再读取变异结果。
- `None`（未取活跃度证明）：普通行仍以断言失败为检出证据；声明冗余行缺证明时记 `mutation_activity_unproven`（原为 `mutation_inactive`，现分开）。
- 新增 3 行守卫变异：`cleanup_check_ignored`、`source_identity_recheck_ignored`、`lock_recreate_guard_ignored`；`worker_recheck_ignored` 补丁签名随参数调整。

## 3. 冻结反例复跑（Linux，符号链接代 junction；a/b 两次，去掉 python 版本字段后逐字段一致；exit0 只表示观测结束）

| 布局 | 基线 5c466b6 | 提交后 99abcd7 |
|---|---|---|
| root→空目标 | 0 调用；目标新增 probe.lock | 0 调用；目标仍为空 |
| root→含合成认领 | 0 调用；新增 probe.lock，认领 accepted→finished | 0 调用；文件集合不变，认领保持 accepted |
| 祖先→含 probe 子目录 | 0 调用；新增 probe/probe.lock | 0 调用；文件集合不变 |
| raw 链接（正控） | 0 调用；目标不变 | 同左 |
| ws 链接（正控） | 0 调用；目标不变 | 同左 |
| 分类矩阵 active=False×5 | assertion 记 detected/计数 | 5 格全 mutation_inactive/不计数 |
| 分类矩阵 active=True×5 | passed→redundant、assertion→detected、其余失败 | 不变 |
| main 入口（nullcontext＋assertion） | exit0，pass，detected，counted | exit1，fail，mutation_inactive |

基线结果与架构师复核表一致（Linux 侧）。派单-07 冻结探针（3d29343 版）提交后仍全部拒绝；其验证器五情形中 assertion 一格由 exit0 变为 exit1，与本批 R3 预期一致。

## 4. 计数（logs/ 原始日志；`extract_counts.sh`、`compare_probe08.py`、`failed_ids.sh` 可重算）

- test_hark06_session_probe 41 OK（不变）
- test_hark07_source_isolation 20 OK（不变）
- test_hark07_verifier_classification 17→21 OK（+4：矩阵、True 保留、None 区分、main 反例×5 种结果；1 项改为分别断言 None/False）
- 新 test_hark08_cleanup_isolation 8 OK（Linux；4 项符号链接类在 Windows 跳过）
- JH018 46 OK；hark06 harness 13/13；JH019 4/4；JH020 13/13；Hark05 11/11（不变）
- hark06 守卫验证器：基线 22 detected + 1 redundant → 提交后 25 detected + 1 redundant，pass，连跑 3 次一致
- 旧前端变异 2+2、Hark05 削弱 3、JH020 会话守卫 17（不变）；旧 jh020 历史守卫 exit1（6+1+2 control_failed，既有 Linux 情形，未改）
- 全量：无代理 786 F21/E63/S2 → 798 F21/E63/S2；有代理 786 F27/E78/S2 → 798 F27/E78/S2。798=786+4+8。去重失败 ID 84=84、105=105，diff 0；新测试不在失败列表。
- 提交后在全新 clone（99abcd7）复验。

## 5. 覆盖

已覆盖（Linux 实跑）：根/祖先/raw/ws 链接收尾；目标已有合成认领与锁；普通目录替换来源（含复制的认领与锁）；认领文件同字节替换；锁文件被删（不重建、认领保持阻断、下次 `probe_claim_unresolved`）；正常来源结束后可再次试运行；thread/start 超时未知结果仍 `probe_unresolved` 阻断。每项断言 thread/start 次数、目标文件集合与字节、原认领状态、status 报告。

平台无关（Windows 应可运行但未在 Windows 跑）：普通目录/文件替换 4 项、分类矩阵与 main 反例。

未覆盖：Windows 真实 junction 与 msvcrt 锁下的收尾；任意 syscall 间 TOCTOU（检查与 open/rename 之间的替换）；断电持久性；全部文件系统类型；真实模型/app-server/Windows GUI。

## 6. 偏离

① 锁文件被删的情形：派发照常进行（未重定向写入），但收尾拒绝重建锁、认领保持 accepted，需人工核对。属保守阻断，非新功能。
② 收尾拒绝信息作为 `last_error.cleanup_refused` 字段附加，不新增 API 或 GUI 字段。
③ 声明冗余行缺活跃度证明时的判定名由 `mutation_inactive` 改为 `mutation_activity_unproven`（仍失败），以区分未设置与明确 false。
④ 新增 3 行守卫变异与 `_always_create` 辅助；无新测试辅助模块。
未改已验收契约、预算、成功门；未删反例、未放宽计分；未触发停止条件。

## 7. 未做

真实模型/app-server 往返；Windows junction、msvcrt 锁与真实 GUI；任意 TOCTOU 防护；自动清理认领入口；GUI 改动；一般派发、指定审查、continue/cancel、自动整改、文件应用。
