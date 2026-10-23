# Hark-派单-13 交付报告：登记丢失后的新对象准入（R1 尾项）

来源：Hark（执行角色）｜Hark-派单-13｜类型：交付报告
基线：d9cbbf7df5033663470a9912ba3b13f15157a21a｜分支：hark-gemini-subagent-20261021
功能提交：a086195f8568f81d20ad95c2584cad5e27854e4d｜报告提交：本提交
AGENTS.md SHA-256：edc9846b33c908ee6f16597cd2dd920f1c0564489271713a027bbe7e24ea74b3（开工时已核对）
独立提取：`python reports/hark-gemini-registry-loss-20261023/check_h13.py`

## 0. 范围理解

唯一目标：已有运行痕迹（`runs/<run_id>/`），但登记被删除、截为零字节、改成纯空白或丢失非空尾部时，新建的适配器（重启或新进程）不能把它当成全新存储，再次派发未知请求。只做准入拒绝和直接关联验证；不恢复历史、不自动修复、不重建幂等索引、不补发。开工时远端 HEAD = 基线 d9cbbf7，工作区干净，没有后续或并行工作。派单-12 已关闭的能力保持不变，不重做；没有改 AgentSession 公共契约、Codex 适配器、JH018、核心门、旧业务测试/守卫或规则。

## 1. 分级结论

| 层级 | 结论 |
|---|---|
| 实现 | 完成（候选，待独立复核） |
| 离线 | 四种源丢失布局（前三种各在两个全新根下）在基线上都会新增派发，候选全部拒绝且派发 0；四个正控保持；新测 18 个，连续 3 次 OK；派单-12/11 加 JH018 共 107 测 OK；新守卫定向削弱 1/1 检出，对照先有效；完整套件 768→787，Linux 既有失败集合相同 |
| 宿主探测（离线方法） | 提交后在真实 CLI 0.65.0＋沙箱合成端点上 13 PASS / 0 FAIL；合成端点请求 5 次；真实模型调用 0 |
| 真实路径 | 未做（Windows 本机、真实 Gemini、产品集成） |
| 独立验收 | 未验收 |

## 2. 实现

`src/agent_session_gemini.py`：

- 新增 `unaccounted_run_traces(evidence_root, registered_run_ids)`。`runs/<run_id>/` 先于 `reserved` 记录创建，所以有 run 目录、却没有登记记录，就说明登记丢了历史（或者预留只做了一半）。以下情况都返回问题：
  - run 目录没有被登记解释；
  - `runs/` 下有不是适配器 run 目录的条目（文件、名称不合规、符号链接）；
  - `runs` 不是普通目录，或者 lstat / 列目录失败。
  只比较目录名，不读其中内容，也不从中构造任何登记事实。
- 新增 `registry_text_problem(data)`：要求 UTF-8，以换行结尾，不能有空行或纯空白行。零字节合法。
- `_reload_locked`：先做原有前缀、JSON、`validate_registry_records` 校验，再检查 `unaccounted_run_traces`（已登记的 run_id ∪ 本对象拥有的 run）。发现问题就设粘滞的 `_registry_problem`，以 `RegistryUnavailable` 拒绝。整个判断在 `registry.lock` 内、启动任务进程前完成；检查本身失败也按拒绝处理。
- 构造函数和 submit 的锁内首查，触发条件由“登记存在”扩展为“登记存在，或 `runs` 路径存在”。不会创建任何东西；最终是否准入仍以版本探测后锁内的第二次判断为准（与派单-12 相同）。
- 内部持久证据格式没有变化：没有新增文件或字段，只是新增对 `runs/` 目录名的读取。

其他文件：`tests/test_hark13_registry_loss.py`（18 测，新）；`tests/fixtures/gemini_cli/h13_restart.py`（spawn 新进程重启辅助，新）；`scripts/repro_hark13_registry_loss.py`（基线/候选对比，新，只用基线已有的 API）；`docs/gemini-cli-adapter.md`（新增“登记丢失时的准入”一节）；`tests/test_hark12_gemini_admission.py`（见 §4 偏离①）。

## 3. 计数证据（原始日志在 `reports/hark-gemini-registry-loss-20261023/logs/`，平台 Linux aarch64 / CPython 3.14.8）

| 项 | 结果 | 日志 |
|---|---|---|
| 基线完整套件（d9cbbf7 独立 worktree） | Ran 768，FAILED (failures=27, errors=78, skipped=2) | baseline-suite.txt |
| 提交后完整套件（a086195 独立 worktree） | Ran 787，FAILED (failures=27, errors=78, skipped=2) | postcommit-suite.txt |
| 既有失败集合 | 105 项（日志 106 行，有一行重复），基线与提交后相同；也与派单-12 提交后日志相同 | baseline-failset.txt、postcommit-failset.txt |
| 新测 ×3 | 18 OK ×3 | hark13-run1..3.txt |
| 派单-12＋派单-11＋JH018 | 107 OK（含派单-12 的 7 项削弱） | h12-h11-jh018.txt |
| 本批反例对比 | 11 案：基线 7 个丢失布局全部新增派发 1 次；候选全部 RegistryUnavailable、派发 0；4 个正控两边一致 | repro-baseline.txt、repro-postcommit.txt |
| 派单-12 反例复现（候选） | 32 案，与 d9cbbf7 日志逐案一致（忽略 run_id） | repro12-candidate.txt |
| 宿主探测 | 13 PASS / 0 FAIL；合成请求 5；真实模型 0 | host-probe-postcommit.txt、host-probe-postcommit/ |

说明：套件日志开头的 “Ran 1 test … errors=1” 来自多进程子解释器的输出，基线和提交后都有，不计入。

## 4. 偏离（请裁决）

1. 修改了派单-12 新增的测试文件：新增 `test_tail_cut_registry_refuses_reader`，并把 `registry_prefix_intact` 削弱目标从 `test_truncated_registry_refuses` 改指新测试。原因：原场景是“截成空”，现在新守卫也会拒绝，前缀守卫的削弱因此被遮蔽。新测试把登记截回合法的、只有 reserved 的前缀（run 目录仍然被登记解释），这时只有前缀守卫会拒绝。原测试本身没有改动，仍然通过。
2. 拒绝面变宽：只要 `runs/` 无法列出、或里面有无法判断的条目，即使登记完整也会拒绝。
3. 预留失败的后果：如果已经建了 run 目录、但 `reserved` 没写成，这个目录会让同一 evidence_root 之后的所有派发都保守拒绝，直到人工处置。没有自动删除这个目录。
4. 登记里有空行或纯空白行时拒绝，即使没有 `runs/` 也一样。零字节登记且没有 `runs/` 仍视为全新存储（与真正全新的存储无法区分；整根抹除不在授权范围内）。
5. 一旦登记无法完整解释 run 证据，同一存储上的所有键都会被拒绝（包括 K1 这种登记仍完整的键），不做按键放行。

## 5. 故障类覆盖

| 故障类 | 输入 | 预期 | 证据 |
|---|---|---|---|
| 登记删除 | A.submit(K) 后删 registry.jsonl，保留 run 目录；新建 B 提交 K 和另一个键 | 拒绝，派发 0，证据字节不变 | test_registry_deleted（2 个根）、repro |
| 零字节 | 同上，截成 0 字节 | 同上 | test_registry_zero_bytes、repro |
| 纯空白 | 同上，写成 `" \n\t\n\n"` | 同上 | test_registry_whitespace_only、repro |
| 非空尾部丢失 | 派发 K1、K2，登记截回 K1 的两条记录；B 提交 K2 和 K1 | 都拒绝 | test_non_empty_tail_lost、repro |
| 新进程重启 | 删除登记后在 spawn 子进程里新建适配器 | 拒绝，子进程内派发 0 | test_restart_in_a_new_process |
| 先构造后丢失 | B 构造时看到完整登记，之后截成 0 | 拒绝 | test_constructed_before_loss_also_refuses |
| 检查不可用 | runs 目录 chmod 0（登记丢失 / 完整两种）；注入 scandir PermissionError | 拒绝 | TestUnjudgeableRunEvidence |
| 无法判断的条目 | runs/ 下有文件、非 run 名目录、指向别处的符号链接；runs 本身是文件 | 拒绝 | test_stray_entries_refused、test_runs_path_not_a_directory |
| 空白登记且无 runs | 登记只有一个 `\n` | 拒绝 | test_whitespace_registry_without_runs_refused |
| 正控 | 真正全新；零字节且无 runs；完整重开（replay unknown、异请求冲突、新键可派发）；只剩 reserved 前缀；两个 run 各只剩 reserved | 按原语义 | TestControls、repro |
| 削弱 | `unaccounted_run_traces` → 恒返回空 | 对照通过，削弱后被检出 | TestGuardWeakening |

未覆盖：Windows 上的 scandir 和 msvcrt 实测、junction、网络文件系统、TOCTOU（列目录与启动之间）、断电恢复、整个 evidence_root 被同时抹除。`registry_text_problem` 没有单独做削弱：空白行即使不经过它，也会被 JSON 解析拒绝，它只提供更清楚的诊断，属于冗余层。

## 6. 未做与阻塞

未做：Windows 本机验收、真实模型调用（0 次）、产品集成、continue/cancel、历史恢复和自动修复。没有读取、申请或上传任何 key，没有调用 localhost 代理，没有改全局配置。原生事件格式和 Linux 宿主限制沿用 d9cbbf7 / 7a41ac1 的证据，没有改动协议或宿主限制；因为准入路径变了，提交后重跑了宿主探测。阻塞：无。
