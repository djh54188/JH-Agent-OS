# Hark-派单-11：Gemini CLI 桥接独立复核

日期：2026-10-22，Asia/Shanghai。角色：当前指定 Codex 架构师 / 独立审查。

## 裁决与边界

**暂不验收 Gemini 桥接候选，不准入 Windows 产品集成或真实模型验证。** 不是撤回稳定基线：本批新增桥接存在重复派发、观测失败误证、异常证据解析及路径输入问题，详见 R1–R3。首个非 Codex 工具任务仍是下一产品里程碑；本轮只补其直接准入条件，不恢复旧 GUI 派单10，不扩大到通用调度或 Runtime。

- 固定基线：`b07213d89874a82bce9003cb3fb0b2348f0dcae2`。
- 候选：`7a41ac1673adb5cf202b12a25c6c83ae8fdbb659`；功能 `f740849983b6aed46a07a5492349d0edb5cce556`；独立偏离 `070004fd011bebae02d88b67c2a261ac689775b5`。
- 远端比较为 ahead 3 / behind 0。产品改动集中于新增适配器、夹具、验证入口、文档和 JH018 消费者增量；未合入本机或云端 n1。
- 本地真正工作树 HEAD 保持 `03d9a0224fd93917ed3a1b9d514e465dfc996825`。原树 WIP、未跟踪 review-backlog 不动。
- 本轮 Windows 11 build 26200，Python 3.14.7 AMD64；未安装 / 启动真实 Gemini CLI，未调用真实模型或用户代理，未读取凭据，未跑完整套件。

## 本轮关闭与有限偏离裁决

1. **接受 JH018 单独偏离 070004f 的限定增量**：仅加入 `agent_session_gemini.py`，仍保留原扫描，新增实际继承和扫描命中检查。独立运行这两个原测试均通过；新类确实继承 `AgentSessionAdapter`。不授予其它消费者白名单，不放宽契约。
2. **接受合成端点作为离线宿主验证方法**：所读脚本在沙箱回环地址提供固定响应，真实模型调用为零，使用占位值；不等于调用用户本机代理。可作为原授权“无需模型探测 / 假进程”范围的有限离线方法，须继续显式标注。这里接受的是方法与可复查证据分类，未授予真实账户、费用或 Windows 实测通过。
3. **不否定已交付的 Linux 离线事实**：原生日志模板按会话 id / 工作区替换后与夹具逐字节相同；所读 D/E 原始请求分别显示工作区外读取拒绝和仅四个读工具声明，写 / Shell 未注册。完整日志中的旧失败集合相同。不能由这些事实替代下面反例的关闭。
4. `allowed_scope` 非空时拒绝，属于新适配器能力限制的明确表达，可保留。continue/cancel、GUI 接线、自动审查和自动应用仍不支持。

## R1（P1）：登记与幂等准入没有绑定当前持久状态

定位：`src/agent_session_gemini.py:569`（恢复）、`:644`（提交）。

- `two_instances_same_source_same_key`：同一登记源，先构造 A/B 两个实例，再依次提交同一个请求；观察到两个不同 run，启动替身两次。
- `same_instance_two_threads_same_key`：单实例两个线程在无模型的版本探测处同步；同键同请求仍生成两个 run / 两次启动。不是未知请求的安全重试。
- `registry_bad_json_after_initialization`：实例构造后将登记变成坏 JSON，再提交；仍启动一次。对照 `registry_bad_json_control` 在构造前已坏时拒绝、零启动。
- 缺地址 / 任务 / attempt / 原生会话 / 指纹与快照，错误类型、未知 event、非法 state、快照或指纹失配、跨记录原生绑定矛盾、事件逆序等，均可在重开后继续启动新请求。恢复仅检查对象与 run_id 字符串，不能支撑“登记损坏先关闭外部调用”。合法派发前缀 / 未收集重开对照仍为 unknown 且不重发。

完成条件：同源多对象、同对象并发及多进程对同键同请求最多一次派发；其余可安全拒绝。读取当前登记并建立互斥 / 认领须先于外部派发；损坏、不可读、形状错及直接相关绑定 / 顺序矛盾应拒绝，原始字节不修复。保留合法前缀、重开 unknown 和同键不同请求冲突。无需引入通用调度器。

## R2（P1/P2）：不可核实的观测被当成肯定证据，坏形状 JSON 泄漏异常

定位：`tree_manifest:195`、`parse_stream:257`、`associate:280`、`find_chat_record:383`、`_finish:771`。

- `enumerate_root_both` / `enumerate_child_both`：非空目录，启动前与收集时同一根 / 子目录枚举均抛 PermissionError；`os.walk` 默认忽略错误，结果仍是 declared_complete、`workspace_unchanged=true`。仅收集时失败的对照会因清单不同而 failed，不代表两次失败已受控。
- `tool_result_status_{missing,null,unknown,array}`：删除或改变工具结果状态；仍 declared_complete，写 / Shell 仍进入 `denied_tool_requests`。缺失或未知不是可确认的拒绝。应保留真实宿主的 success/error/拒绝依据，不能把“不等于 success”一概写成已拒绝。
- `stream_tool_id_array` / `stream_type_array`：可解析 JSON 中的错误字段形状导致 TypeError；宿主聊天记录首行是数组、null、字符串、数字、布尔值时导致 AttributeError。次级记录不能让 collect 崩溃，也不能冒认为当前会话已核实。

完成条件：启动前清单无法完整建立时零派发；收集时不可核实则 failed / unverified，不报未修改。输入按直接相关形状与状态校验，保留原始证据，返回明确失败 / 未核实而非未处理异常；次级记录缺失可继续作为缺失项列明，不强制升级为必需证据。无需扩展到全文件系统竞态、断电恢复或通用 schema 平台。

## R3（P2）：路径准入与 Windows 回放夹具

定位：`src/agent_session_gemini.py:661`；`tests/fixtures/gemini_cli/fake_cli.py` 的模板替换；`tests/test_hark11_gemini_session.py` 的相对路径反例。

- `existing_relative_workspace`：相对 `ws` 确实存在时被接受并启动。submit 在检查前先 abspath；原测试只用了不存在的相对目录，实际证明的是不存在而不是绝对路径要求。
- `windows_fixture_substitution`：原 fake_cli 将 Windows 反斜杠路径直接塞进 JSON 文本，形成非法转义。独立执行原始有效交付测试为 failed（应为 declared_complete）；宿主配置拒绝及两项 JH018 原测试通过。未修改产品 / 夹具以凑绿。

完成条件：存在 / 不存在的普通相对路径及 Windows 根相对 / 驱动器相对路径，均在派发前按绝对路径要求拒绝；合法绝对路径正控保留。回放按 JSON 数据进行替换 / 编码，不依赖 POSIX 路径字符串；无需重写已验收的旧业务测试。

## 证据、复现与数字

本地完整取证：`data/hark11-independent-review-20261022/`。拟供云端复现的派生反例在本报告旁的 `hark-gemini-bridge-review-20261022/`；当前仅本地保存，发布因 GitHub 连接器 403 未完成。

| 项目 | 独立结果 | 原始文件 / 提取方式 |
|---|---|---|
| 固定候选下载与回读 | 24 个文件的 Git blob SHA-1 与磁盘 SHA-256 一致 | `download-manifest.jsonl`；`audit_evidence.py` |
| 两轮故障类探针 | 每轮 43 格；按状态、异常、调用数等归一后结果一致；不是 43 项“通过” | `windows-round-04.json`、`windows-round-05.json`；`audit-summary.json.probe_outcomes_repeat` |
| 候选原始定向测试 | 4 项，3 通过 / 1 断言失败 / 0 error / 0 skip | `windows-selected-original-tests.txt`；`audit-summary.json.selected_original_tests` |
| 基线完整日志（只读） | 704；27 failures / 78 errors / 2 skipped | 源候选 `reports/hark-gemini-bridge-20261021/logs/baseline-suite.txt` |
| 前后完整日志（只读） | 各 736；27 failures / 78 errors / 2 skipped；各 106 条失败行，105 个唯一行；集合相同 | 同源 `precommit-suite.txt`、`postcommit-suite.txt`；`audit_evidence.py` 正则解析运行器行与失败集合 |
| 新测试日志 / AST | 日志 Ran 32 OK；AST 方法 32 | 同源 `postcommit-hark11.txt`；`audit_evidence.py` 用 AST 复核 |
| 宿主两份 summary | 各有 13 项 true；这是执行方结果，架构师未重跑 CLI | 同源 `host-probe/summary.json`、`host-probe-postcommit/summary.json` |

探针复现：按同目录 README 准备隔离 candidate 后，`python -X utf8 -B probe_boundaries.py --round fresh-name`。脚本 exit 0 只表示取证完成，不表示候选通过。独立探针使用正确 JSON 编码后的原生事件及内存进程替身，零进程 / 网络 / 模型；所选原测试使用 Python 假 CLI 子进程，不启动 Gemini。

Windows 环境接缝：第一次所选原测试被 Python 3.14 `mkdtemp(mode=0700)` 的本机 ACL 拦住（两项 setUp error）。原件保留本地 `targeted-original-tests-acl.txt`；后续只在审查 runner 中把临时目录创建替换为普通 mkdir，候选文件字节不变，得到上述 4 项结果。此接缝不算产品能力或产品缺陷。

## 未覆盖与下一批

未执行真实 Gemini、账户 / 登录 / 额度、Windows 宿主策略与 junction 拒绝、多进程真实 CLI。跨进程和不可读登记的变体留执行方自建反例；本轮重复启动证据是多对象与线程替身。未闭合系统设置覆盖、`@路径` 预处理、TOCTOU、断电、超大目录耗时，不据推测列成新增缺陷。固定版本官方设置源码还显示系统配置优先于用户配置、文件 admin 设置不被采用，因此用户 settings 中的开关不单独构成宿主权限证明；真正 Windows 阶段仍须核实实际有效配置：[官方 v0.65.0 settings.ts](https://github.com/google-gemini/gemini-cli/blob/v0.65.0/packages/cli/src/config/settings.ts)。

下一批仅发行 **Hark-派单-12**，续接 7a41ac1，关闭 R1–R3，沿用已核对的 CLI 格式 / Linux 宿主证据，只补变更影响的验证。实际实现方式自主。执行AI-派单-08仍未发行。新编号之后为执行AI-派单-08 / Hark-派单-13。本报告不等于派单已发送。

旧 Hark09 未修三尾项继续冻结；JH019 与其既有验收不重做。production complete、FileToolExecutor 自动应用关闭；N2/P1、指定审查与真实协作闭环未完成。
