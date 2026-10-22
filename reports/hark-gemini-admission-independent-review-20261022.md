# Hark-派单-12 独立复核：保留一项 R1 重开尾项

日期：2026-10-22，Asia/Shanghai。当前指定 Codex 架构师 / 独立审查。

## 裁决

**暂不整体验收，执行AI-派单-08继续不发行。** R2、R3及 R1 的已验证并发 / 现存登记校验按下列离线范围关闭；只留一项同类尾项：**新对象重开时，登记已删除、清空、纯空白或丢掉末尾完整运行，仍会重复派发原来未确定的请求**。这是“未知不重放”的直接反例，不是要求恢复全部历史或建设通用 Runtime。

- 用户正式转回 `Hark｜Hark-派单-12｜交付报告`，确认派单12已送达、已交付。
- 固定功能 `be61b924b2bf7e41d5ad2d6718b927c81440676e`；报告 `d9cbbf7df5033663470a9912ba3b13f15157a21a`；基线 `7a41ac1673adb5cf202b12a25c6c83ae8fdbb659`。
- 远端 Hark 分支 HEAD 核对为 d9cbbf7，基线比较 ahead 2 / behind 0；be61b92 至 d9cbbf7 只有 reports 改动。未改旧契约、Codex 适配器、已验收测试与 JH018 白名单。
- Hark 关于未推送的 1a45a57 与作者元数据改写的说明保留为执行方来源说明；本轮固定并复核的是可读 be61b92 / d9cbbf7 的实际字节，不以作者说明替代候选验证。
- 本地产品 HEAD 保持 `03d9a0224fd93917ed3a1b9d514e465dfc996825`。只在忽略目录构建审查副本，未移植产品、安装 / 启动真实 Gemini、读取凭据、调用模型或跑完整套件。

## 本轮关闭，不重做

| 范围 | 独立结论 | 局限 |
|---|---|---|
| R1 同源多对象 / 同对象线程 | 冻结反例现在只启动一次、返回同一 run；坏 JSON、缺字段 / 类型 / 绑定 / 顺序等冻结反例拒绝新派发 | 不是登记丢失后新对象重开已通过 |
| Windows 跨进程锁 | 真正两个 Windows Python 进程使用候选 msvcrt.locking：同键启动一次且同 run；不同键可各启动；已有锁时 RegistryBusy、零启动 | launcher 是内存替身；未运行 Gemini；非网络文件系统或 junction 证明 |
| R2 清单 / 形状 | 枚举失败启动前零派发；仅收集失败为 failed / workspace_unchanged=null；坏事件、未知工具状态得到诊断；非对象聊天首行不抛异常 | 次级聊天缺失仍明确列入未核实，不要求当成必需证据；未声称解决全部 I/O、竞态、断电 |
| R3 路径 / 夹具 | 原存在相对目录被拒；Windows 路径分类器正反控、Windows 字符串回放、旧捕获字节重建和原有效交付正控均通过 | 不是 CLI 宿主在 Windows 上的权限 / junction 实测 |
| 原宿主证据 | 新提交后的 summary 有13项 true，合成请求5条、声明工具仅四个读工具；来源仍为离线宿主方法 | 架构师只读取证据，未亲自运行真实 CLI / 模型 |

冻结脚本与上一轮原件 SHA-256 相同。两次各43格中42格直接作用于候选，另1格刻意运行旧文本替换算法，继续产生非法 JSON 是对照而不是候选失败；候选新夹具由原始定向测试另证。预期判断由 `audit_review.py` 明确断言。

## 唯一未关闭：登记丢失后新对象补发（P1）

定位：`src/agent_session_gemini.py:914` 新对象 prefix 从空开始；`:915` 只在登记存在时构造恢复；`:924–929` 将 FileNotFoundError 转为空字节；`:934` 的前缀检查仅绑定本对象已读字节。

最小反例（两个对象是先后创建，不是原来已关闭的并发问题）：

1. A 在独立、合法的 evidence_root 内 submit 请求 K，留下 reserved/dispatched 和 runs/<run_id>，结果仍未确定。
2. 只删除 registry.jsonl，或截断为空 / 写成纯空白；保留 run 目录和其他原始证据。
3. 再创建 B，向同一个 root submit 同一个 K。
4. B 再次调用 launcher，返回另一个 run_id。原未确定执行没有被保留为未知阻塞。

一次扫同类变体的结果：

| 新对象重开时的布局 | 新增启动 | 结果 |
|---|---:|---|
| 真正全新存储，无旧 run | 1 | 合法首次派发正控 |
| 原登记完整 | 0 | 同一 run / unknown，正确 |
| 坏 JSON | 0 | RegistryUnavailable，正确 |
| 登记零字节 | 1 | 错误新增 run |
| 登记已删除，旧 run 目录仍在 | 1 | 错误新增 run |
| 登记仅空白 | 1 | 错误新增 run |
| 登记保留原 run 的 reserved 前缀 | 0 | 同一 run / unknown，正确 |
| 已派发 K1、K2，只保留 K1 的完整合法历史，再提交 K2 | 1 | 非空有效文件仍丢了 K2，错误新增 run |
| 两个 run 都保留各自 reserved 前缀 | 0 | K2 保持同一 run / unknown，正确 |

删除 / 清空 / 空白三种在两个新证据目录里重复一致；非空尾部丢失在扩展轮确认。新启动数是内存 launcher 调用，不是真实模型次数。这个反例不依赖 msvcrt、Windows 路径或 TOCTOU，也不是“非属主 status/collect 不重读登记”这一已声明局限。

现有测试 `test_deleted_registry_refuses_owner` / `test_truncated_registry_refuses` 检查的是仍记得旧 prefix 的对象，不能代表新对象 / 重启后的持久保证。登记没内容不等于这个 root 从未派发过。

## 对请求裁决①–⑤的答复

| 项 | 裁决 | 解释 |
|---|---|---|
| ① verify --workspace 非绝对退出2 | 接受 | 与 R3 输入绝对路径要求一致；不恢复 abspath 隐式锚定 |
| ② workspace_unchanged=null；新 unconfirmed 字段；聊天 helper 返回 tuple | 接受 | null 表示未证明，不能当 true；显式区分未确认请求。helper 的新返回值在本批模块 / 直接测试内使用，不改变 AgentSession 契约 |
| ③ 同对象对登记问题粘滞拒绝 | 接受这一策略，但不足以关闭 R1 | 新对象也必须面对 root 的持久不确定性；不能把“新建对象”当作忘记旧派发的授权 |
| ④ 版本探测锁外、锁内准入和预检、超时 RegistryBusy | 接受这一组织方式 | Windows 锁探针验证了限定行为；不把这项接受解释成源丢失后的准入已正确 |
| ⑤ failed run 仍保存宿主 declared_status=success | 保留，不改为 unattributed | 该字段记录宿主声明；run.state=failed 和具体 unverified 必须保留。关联本身无效时继续 unattributed。不得从 declared_status 单字段推导任务成功，不借本批重定义旧字段 |

以上是限定设计裁决，不是整批、真实路径或产品集成验收。

## 证据、平台与复现

平台：Windows 11 build26200 / AMD64 / CPython3.14.7。完整本地取证在 `data/hark12-independent-review-20261022/`；拟供转交的派生材料在同目录报告旁的 `hark-gemini-admission-review-20261022/`，本轮仅本地保存，未发布云端。

| 项目 | 结果 | 原始文件 / 提取 |
|---|---|---|
| 候选下载 | 22文件 Git blob SHA-1及磁盘SHA-256一致 | download-manifest.jsonl；audit_review.py |
| 冻结反例 | 两轮各42候选格＋1旧算法对照，预期一致 | frozen-01/results.json、frozen-02/results.json；audit_review.py |
| 原始定向 Windows 测试 | 10项通过，0错误 / 跳过 | selected-windows-original-tests.txt/.json |
| 独立 Windows 进程锁 | 同键 / 不同键 / 锁占用三格符合预期 | probe_process_lock.py；process-lock-01/results.json |
| 新对象源丢失 | 首轮6格、扩展9格；4种源丢失布局导致新增派发 | probe_reopen_boundary.py、probe_reopen_variants.py；reopen-boundary-01/02/results.json |
| 完整日志（只读） | 736→768，各27 failures /78 errors /2 skipped；各106失败行、105唯一行，逐行集合相同 | 固定源 reports/hark-gemini-admission-20261022/logs/baseline-suite.txt、postcommit-suite.txt；audit_review.py |
| 新测试日志 / AST | 三份日志各32 OK；AST32；既有74项日志OK | 同源 postcommit-hark12-run1..3.txt、postcommit-hark11-jh018.txt；audit_review.py |
| 守卫削弱日志 | 7条控制先通过且削弱被检出；部分检出是异常而非断言 | 同源 postcommit-weakening-detail.txt；不把7条全写成断言检出 |

本轮未运行完整套件或候选全部测试。第一次选11项时，Windows multiprocessing.Queue 被沙箱命名管道权限拒绝、字节重建测试缺审查副本未下载的旧夹具：原始结果保留 selected-windows-original-tests-initial.*。补回固定旧夹具后，选10项通过；原 Queue 测试未伪装为通过，用独立文件同步进程探针补证实际 msvcrt 路径。审查 runner 仅用普通 mkdir 替代本机 Python3.14 mkdtemp ACL 问题；候选源码 / 测试字节未改。这些是取证环境接缝，不归为产品修复或完整 Windows 验收。

复现新尾项：在独立副本准备候选 src 与冻结 probe 依赖后，运行 `python -X utf8 -B probe_reopen_boundary.py` 和 `probe_reopen_variants.py`；每份脚本固定输出目录必须不存在，用脚本 / 目录副本复跑，不改冻结原件。脚本 exit0只表示取证完成。

## 下一批边界

仅发行 **Hark-派单-13**：续接 d9cbbf7，关闭“有既往运行痕迹、登记却丢失/不完整时，新对象误判全新存储”的准入尾项。允许保守拒绝；不要求恢复历史，不从目录名拼装可信登记，不自动重建幂等或补发。全新存储与合法未完成前缀必须保留，读取 / 检查失败不能当作无历史。

不重做本轮关闭的 R2/R3 / 并发锁；不改变既有适配器、契约、JH018、成功门或新增GUI / continue / cancel / 自动应用。整根存储及所有证据都被人为抹除、网络文件系统、TOCTOU、断电恢复和通用存储平台不纳入本批。

执行AI08继续未发行。下一新编号为执行AI-派单-08 / Hark-派单-14。13生成不等于发送；用户只转发本轮唯一完整提示词。旧 Hark09三尾项继续冻结，真实异构工具任务仍未接通，production complete 与 FileToolExecutor 自动应用继续关闭。
