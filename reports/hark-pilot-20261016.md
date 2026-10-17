# Hark 首轮离线复核 · JH-006 已审查快照 · 2026-10-16 批次

执行时间：2026-10-17（Asia/Shanghai）。执行者：Hark 云端沙箱。本报告是独立复核记录，不是架构验收；裁决由当前架构师会话做出。

## 结论：平台阻塞（平台无关部分未发现可复现绕过）

- 全量 unittest：**359 项，292 通过，11 失败，55 错误，1 跳过，退出码 1**。66 项失败/错误逐一归因为 Windows 专属能力缺失（盘符路径模型、`_winapi`/junction、`cmd mklink`、实测 `pwsh.exe` 桌面包装、本机 Codex CLI），本轮没有一项被确认为平台无关的实现缺陷。依任务要求，未修改测试、未扩大 skip，按**平台阻塞**原样报告，不计通过。
- 四个离线检查：JH-005 夹具 **通过**；JH-005 变异 **通过（14/14 断言检出）**；JH-006 宿主边界 **通过（5/5 检出，0 异常）**；JH-006 采集守卫 **退出码 1：30 变异，12 断言检出，18 异常（19 个测试错误），0 冗余**。18 项异常全部是同一平台门（`expectation-malformed`），**异常不算检出**。
- 独立探针 `scripts/hark_pilot_probe.py`：17 项全部符合拒绝预期，另有 1 项平台观察。目标漂移、审核/产物漂移、待审批、预算未知、跨任务调用身份、call/item/delivery 唯一约束、事务回滚和 production complete 关闭都保持住了。
- 桌面采集登记（register）、`verify_binding_current` 正例和归档复核在本平台**无法执行**，所以标为**未验证**，不是通过。上游 359/358/1、30 变异 29 检出 + 1 冗余是历史成绩，不是本轮成绩。

## 环境与版本

| 项 | 值 |
|---|---|
| 远端 | https://github.com/djh54188/JH-Agent-OS（公开，匿名 clone 成功） |
| 检出分支 / HEAD | `main` / `bc3e0f286d4a8a2863ab15daf223a2900f0dd44a`（"chore: replace scattered upload with reviewed JH-Agent OS pilot"） |
| 副本源提交 | `1f605c30fbca8573b6865f9cde2a1244f8f9ae4c`（本地已审查提交，副本不带其 Git 历史，云端无法 `git show`；与 GitHub HEAD 不是同一提交号） |
| Python | 主跑 3.12.15（venv，jsonschema 4.26.0）；复跑 3.14.8（uv venv），失败/错误集合**逐项相同** |
| OS | Debian GNU/Linux 13 (trixie)，Linux 6.12 aarch64 |
| Shell | bash 5.2.37；**没有 PowerShell / pwsh / cmd** |
| 其它 | 没有 D 盘，没有本地 8787 代理，没有 `~/.codex`，没有真实模型密钥；TMPDIR 独立设为 `/tmp/hark-pilot-tmp` |

已读：CLOUD_START_HERE.md、HARK_FIRST_TASK.md、STATUS.md 最新段（2026-10-16 离线持久化绑定审查通过，production complete 关闭）、docs/host-delivery-contract.md、docs/n2-acceptance-spec.md、reports/jh006-binding-review-20261016.md。

## 实际命令与原始结果摘要

在仓库根目录执行，`TMPDIR=TEMP=TMP=/tmp/hark-pilot-tmp`：

| 命令 | 退出码 | 结果 |
|---|---|---|
| `python -X utf8 -B -m unittest discover -s tests` | 1 | Ran 359，failures=11，errors=55，skipped=1（17.4 s） |
| `python -X utf8 -B scripts/verify_jh005_fixtures.py` | 0 | status ok；fixtures code_sortfix、data_rollup |
| `python -X utf8 -B scripts/verify_jh005_mutations.py` | 0 | sealed_control pass；14 变体全部 detected_by_assertion；fixtures_cleaned true |
| `python -X utf8 -B scripts/verify_jh006_capture_guards.py` | 1 | 30 变异：12 断言检出，18 异常，0 冗余 |
| `python -X utf8 -B scripts/verify_jh006_host_boundary.py` | 0 | containment_only；route_regressions 1，controls 5，replays 5，detected 5，api_calls 0 |
| `python -X utf8 -B scripts/hark_pilot_probe.py` | 0 | 17 probes，0 failed，1 observation，api_calls 0 |

unittest 末尾另有一行脚本输出：`{"captured": false, "reason": "source-untrusted: ... No such file or directory: '/home/sandbox/.codex'"}`，说明采集入口在找不到真实会话库时拒绝，没有伪造采集。

### 66 项失败/错误的归因

| 组 | 数量 | 根因（原始异常） | 平台依赖位置 |
|---|---|---|---|
| test_jh006_capture_binding + test_jh006_binding_audit | 47 错误 + 1 失败 | `CaptureError: expectation-malformed: absolute workdir and exact call script are mandatory`；临时工作区是 POSIX 路径，不符合盘符正则 | src/host_capture.py:318-323（`^[A-Za-z]:[\\/]`），:287-288（cwd 必须是 Windows 盘符） |
| test_jh006_binding_audit（control_plan_*） | 2 错误 | `FileNotFoundError: .../powershell/pwsh.exe` | src/capture_binding.py:327 固定桌面包装 |
| test_phase4_file_tools | 5 错误 | `ModuleNotFoundError: _winapi`（junction） | 测试建 junction |
| test_phase4_file_tools | 1 错误 | `FileNotFoundError: 'cmd'`（`mklink /J`） | tests/test_phase4_file_tools.py:207 |
| test_phase4_file_tools | 2 失败 | 大小写折叠、`..\evil.txt` 在 POSIX 上反斜杠不是分隔符 → success | Windows 路径语义 |
| test_phase2_hardening | 7 失败 | `D:/...`、`C:/Windows/System32` 在 POSIX 上被当作相对路径，路由为 allow/hold，而不是拒绝 | src/router_policy.py:96-102、:122、:215 |
| test_adapters.test_codex_cli_health | 1 失败 | 本机没有 Codex CLI，healthy=false | 环境 |

采集守卫中 18 个未检出变异：binding-owner-held、delivery-request、claim-identity/artifact/review、artifact-current、review-current、target-bytes、liveness、exit/marker-passthrough、argv、output-digest、fixed-invocation、current-reload（2 测试）、atomic-event、unique-call、unique-item。它们全都在 `_archive_for` 调 `verify_desktop_binding` 时先撞到同一平台门，被测断言根本没有执行。

## 独立复核（src/capture_binding.py、src/host_capture.py、src/storage.py）

本轮能覆盖的部分由探针实际执行（合成工作区 `hark-pilot-synthetic-*`，不读真实会话，不改生产代码，不打补丁）：

| 探针 | 结论 | 原始观察 |
|---|---|---|
| P1 POSIX workdir | 拒绝 | `expectation-malformed`，非 Windows 来源没有被绑定 |
| P2a/b/c 目标字节 | 应用后快照成立；改回基线或外来字节都拒绝 | `target-bytes-mismatch` |
| P2d 规划只读 | `capture_invocation_for_delivery` 等于控制层固定调用 | true |
| P3 echo 重申 | echo argv 不等于固定 argv；完整拒绝路径在第 440 行，本平台到不了，见未验证 | equal=false |
| P4a/b 待审批、预算未知 | 拒绝 | `control-blocked` |
| P5 production complete | 拒绝，状态仍为 reviewing | `production host assembly is out of scope ...` |
| P6 无绑定 | current=false | `["no-binding"]` |
| P7a/b 新审核 attempt、新产物版本 | 拒绝 | `review-stale` / `artifact-changed` |
| P8 跨任务 | 两个交付的 apply_attempt_id 与调用脚本不同 | same_apply_attempt=false |
| P9a/b/c 唯一约束 | 同 thread+call 换 item、同 thread+item 换 call、同 task+delivery 都被数据库拒绝 | `UNIQUE constraint failed: ...` |
| P9d 事务回滚 | 事务内写入后故障，行不存在 | true |
| P10（观察） | POSIX 上 `write_file D:/another-project/notes.md` 被判为 allow | os_name=posix，action=allow |

### 发现项（按严重度）

1. **[已复现·仅限非 Windows] 路径策略不在平台层失败关闭**。src/router_policy.py:96-102 用宿主 `Path` 解析，POSIX 上 `D:/x` 是相对路径，:122/:215 的 Windows 系统路径规则也就失效。最小反例：P10 和 test_phase2_hardening 的 7 项。生产只支持 Windows，FileToolExecutor 关闭，所以**当前不构成生产漏洞**。但只要以后在非 Windows 上开写门，这就是越界写入。建议：RiskPolicyEngine / FileToolExecutor 在 `os.name != "nt"` 时显式拒绝；测试改为带原因的平台门（由架构师决定，本轮不改测试）。
2. **[推测·失败关闭] 归档路径未规范化**。src/capture_binding.py:470 保存的是 `str(archive_dir)`，没有 resolve。传入相对路径时，后续 `verify_binding_current` 依赖调用时的 CWD，换目录就变成 archive-missing（current=false）。只会误拒，不会误收，因为摘要还要对上。建议：保存 `resolve(strict=True)` 后的绝对路径。
3. **[推测·运行脆弱性] 固定调用依赖控制进程环境**。capture_binding.py:300、:323、:327 把 `sys.executable`、仓库检查器绝对路径和 `Path.home()` 写进 argv/脚本。规划和登记只要换了解释器、用户或检出目录，就会 invocation-unverified。这是失败关闭，但宿主接线时应固定同一解释器，或把这些量纳入快照。
4. **[代码阅读·信息] 死分支**。:475-477 的 `review_items_json` 恒为 `[]`，所以 :537-538 的 invocation-unverified 分支永远不会触发；删掉它不会被任何变异检出。建议：删掉，或注明保留原因。
5. **[代码阅读·信息] 唯一索引按线程划分**。storage.py:210-213 的 `(thread_id, call_id)` 和 `(thread_id, item_id)` 允许不同线程复用相同 call/item id。如果桌面 id 本身就是线程内作用域，这样设计是合理的；需要架构师确认 id 的作用域。
6. **[代码阅读·确认] 幂等返回在终检之前**。:446-451 对相同捕获直接返回已有行，没有再走 :455 的 final_snapshot。:406 的快照已经过完整核验，而且不写任何东西，风险低。

`verify_binding_current`（:515-545）在 POSIX 上必然因为 `verify_capture_archive → verify_desktop_binding` 的平台门报 `expectation-malformed`，即失败关闭。

## 未验证 / 未运行

- 桌面采集正例登记、重复/冲突登记、事件故障回滚（真实 `_bind_verified` 路径）、`verify_binding_current` 正例及登记后漂移、echo 重申在 :440 的拒绝、归档篡改、实时活性比对：**都被平台阻塞，未运行到被测断言**。
- 两次真实本地检查进程（pwsh 包装）：未运行。
- 旧库兼容（需要旧提交 `git show`）：不在副本范围，未运行。
- 真实 Codex desktop 来源、真实模型闭环、用户文件应用：不在范围，未运行。

## 建议下一步

1. 由架构师在 Windows Codex desktop 主机（Python 3.14、`.test-tmp` 作 TEMP）复跑全量和采集守卫，取得本快照在支持平台上的成绩。
2. 决定发现项 1 的处理：给路径策略加显式平台门，或把相关测试改为带原因的平台门；不能让非 Windows 上的结果静默变成 allow。
3. 处理发现项 2 和 4（小改、只加不减），在宿主入口接线批次里一起审查。
4. 发现项 3 写进宿主入口交接：规划和登记使用同一解释器与检出目录。

## 范围声明

本分支只新增本报告和 `scripts/hark_pilot_probe.py`。没有修改 src、tests、封存夹具、Schema、Skill、供应商或权限；没有开启 production complete 或 FileToolExecutor；没有调用任何模型 API；没有包含凭据、宿主日志或账户资料。
