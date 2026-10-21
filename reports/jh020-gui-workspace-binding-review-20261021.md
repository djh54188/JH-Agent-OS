# GUI 会话试运行 Windows 独立复核 · 2026-10-21

## 裁决

对应执行AI-派单-07。审查对象为 `2bf1cead49c6934a75e27d131894bf86e58fe7a3`（15 文件）、`8c5dfabb9e618c903c65b9440e689f609968500a`（单独授权的 JH018 守卫偏离）、`411af6f4e25dd34b3949c2f7c6c212ddbdf53542`（报告/状态），父基线 `b07213d89874a82bce9003cb3fb0b2348f0dcae2`。

**暂缓整体验收，仅新增“工作区绑定与观测口径”整改。** 16 文件精准移植、提交分组、既有隔离与计分整改、默认关闭，以及真实 GUI 固定标识消息往返均有证据支持，不重做。此次真实运行的原生 cwd 与登记工作区不一致，不能据登记目录为空就确认实际会话工作区为空。

本次没有发现工具调用、文件写入或目录逃逸。原生 cwd 仍在计划目录之下，但多重复了一段相对路径且该目录不存在；这是路径绑定及证据适用范围的问题。固定标识回显真实，不撤回为“模拟”；工作区绑定尚未通过，也不以回显代替该项。默认绝对路径配置和早期已核实的绝对路径适配器证据不因本反例一并撤回。

## 已关闭与证据

- 16 文件磁盘 SHA-256、HEAD blob、批准清单三者一致；15 文件功能、1 文件 JH018 偏离、报告/状态三个提交父链和范围正确。JH018 偏离承接先前限定消费者裁决，不重新宣称全局只收紧。
- 读取执行方提交前后原始完整日志，均 `Ran 798 / OK (skipped=11)`；定向 `90 / skipped=10`，JH018 `46 / OK`；独立 AST 计数 798。没有独立重跑完整套件。代码/守卫提交在 2026-10-20，真实往返在当晚；最后报告提交在 2026-10-21，仅文档，不改变运行代码。
- 新验证器保留 `22 detected + 1 redundant + 3 control_failed / exit 1`。三项为 ancestor_link_check_ignored、subdir_link_check_ignored、worker_recheck_ignored 的 POSIX 样例跳过；原生 junction 六布局审计另外成立，不折算成三个检出。冻结清理探针五布局及既有前端结果与交付一致。
- 默认关闭原始 JSON、服务日志和亲看截图相符；默认桌面快捷方式仍不含启用参数。临时启用服务已结束。本次未再次操作 GUI、模型或 app-server。
- 真实 GUI 服务日志只有一次 prepare 200、一次 start 202；指针记录和亲看确认前/结果截图相符。消息、thread/turn/model、用量和原始通知可关联；原生 rollout 进一步交叉确认，不用 Mock 或命令行回显替代 GUI 证据。
- 原 R1 同源互斥、R2 来源/收尾隔离、R3 计分尾项的关闭结论承接；不重复扩大为任意 TOCTOU 或全文件系统保证。

真实 run `codex-run-1-2060ac1b`，probe `536e8695bf01b0a7`，回显 `JH-PROBE-536E8695BF01B0A7`，原生 thread `01a159de-490a-75f0-8a47-b8fda46b1d2e`、turn `01a159de-5095-7df1-9ec8-735fc0b444fe`，模型 `gpt-6.1-sol`。原始通知回读 SHA-256 为 `da763e979b5f5be087810b6e9ff6b098fd63ef63c9b4c651dbbeffa1b0b38811`，11782 字节、35 帧；用量 20631，关联本轮；已完成 item 仅 userMessage/agentMessage，没有工具调用。旧真实历史登记 SHA-256 仍为 `0ca5c260fae77de816a55253513b3e6d9153c152939f8d96c728c64e53ba85c1`。

## R4：计划目录与原生 cwd 不一致

本次请求快照 workspace 为相对路径：

```text
data\hark-session-probe-integration-20261020\trial-01\ws\536e8695bf01b0a7
```

将其相对服务工作目录解释，计划目录为：

```text
D:\JH-Agent OS\data\worktrees\n1-artifact-entry\data\hark-session-probe-integration-20261020\trial-01\ws\536e8695bf01b0a7
```

但原始 `thread/started.params.thread.cwd`、原生 rollout 的 session_meta.cwd 和 turn_context.cwd 一致指向：

```text
D:\JH-Agent OS\data\worktrees\n1-artifact-entry\data\hark-session-probe-integration-20261020\trial-01\ws\536e8695bf01b0a7\data\hark-session-probe-integration-20261020\trial-01\ws\536e8695bf01b0a7
```

计划目录存在；原生目录不存在。当前 `workspace_files=[]` 只枚举计划目录。

源码依据（行号针对 411af6f）：`src/session_probe.py:522` 保留显式相对 probe_dir，`:188` 将 workspace 用作 app-server 进程 cwd，`:840` 又把同一相对字符串传给请求；既有适配器 `src/agent_session_codex.py:833` 和 `:872` 将其原样作为 thread/start、turn/start 的 cwd。原生侧因此相对已经改变的进程 cwd 再解释一次。`:637–694` 的试运行摘要确认回显/字节/工具情况，尚未核实原生工作区，只在 `:690` 列出计划目录。

附带同类证据缺口：即使配置为绝对路径，注入同线程错误 cwd、缺失 cwd、只有外来线程 cwd 或同线程矛盾 cwd，现有摘要仍为 round_trip_complete/workspace_files=[]。这些是离线故障注入，不声称真实服务发生了四种情况。消息往返完成可以单独保留；工作区确认及空目录观测必须明确绑定其实际来源。

## 故障类复核与复现

冻结 [probe_workspace_binding.py](jh020-gui-workspace-binding-review-20261021/probe_workspace_binding.py) 使用真实默认适配器工厂，仅把 stdio transport 替换为进程内解析模拟；没有启动进程、模型或真实 app-server。真实相对路径问题已由上述原生原件单独印证。Linux 复跑使用既有测试专用 POSIX 证据接缝，不能代替 Windows 原生路径验收。

| 输入类 | 本次观测 |
|---|---|
| 显式绝对路径、含空格/中文的绝对路径 | 两项计划/协议有效目录一致，正常回显对照成立 |
| 相对路径、前置点、带父级片段、含空格/中文的相对路径 | 四项有效 cwd 重复拼接，摘要仍完成/空列表 |
| 同线程原生目录不符 | 一项目录不符仍完成/空列表 |
| 原生目录缺失、只有外来线程目录 | 两项缺当前线程依据仍完成/空列表 |
| 线程响应与同线程通知目录矛盾 | 一项未识别矛盾仍完成/空列表 |

**共 10 项观测，不是 10 项通过。** 两次新目录复跑，去除输出目录和随机 probe id 后逐字段一致。结果文件的 workspace_matches 是模拟器已知值；在 missing/foreign 类中即使该值为 true，产品仍没有可采信的原生 cwd 证据，不能将模拟器真值当作产品验证结果。

未覆盖：盘符相对/UNC/设备路径的新增行为、任意 syscall 间 TOCTOU、断电持久性、全部文件系统类型。已经关闭的链接/联接拒绝不能被路径规范化顺带绕过；未测项目不自动登记为新增缺陷。

在同时可读材料目录 ANA、完整候选目录 BASE 的 PowerShell 中：

```powershell
$env:PYTHONDONTWRITEBYTECODE = '1'
python -X utf8 "$ANA\reports\jh020-gui-workspace-binding-review-20261021\probe_workspace_binding.py" --repo "$BASE" --out "$BASE\data\workspace-review-fresh"
```

ANA 为本报告所在复核分支，BASE 为完整 `dc6c5c2` 云端候选或本地 `411af6f`；输出目录必须全新且位于 BASE 下。Linux 使用对应明确绝对脚本路径，传同样参数。退出 0 仅代表观测执行结束，不能作验收断言。冻结原始脚本 SHA-256 为 `9ac6eb67b483150c5cd43ece5d88dcb9613e1e3d679353cba53034a1d31fabde`。

资产包括 [synthetic-results.json](jh020-gui-workspace-binding-review-20261021/synthetic-results.json)、[real-evidence-summary.json](jh020-gui-workspace-binding-review-20261021/real-evidence-summary.json)、[verification-summary.json](jh020-gui-workspace-binding-review-20261021/verification-summary.json)。它们分别是审查模拟观测、真实原件的筛选事实摘要、身份与计数核对，不冒充完整原生证据。真实原件仍只读保留于本机 `data/hark-session-probe-integration-20261020/` 及该 thread 的 Codex 原生归档，未上传原始私有会话。

## 后续边界与发布

本轮只新增架构师复核、探针、事实摘要和交接，不修改功能或执行方提交。当前本地三个提交不因真实回显成立就整体验收，不同步到云端 n1。为让 Hark 读取反例，本轮仅将复核材料从已接受的 b07213d 基线发布到独立 `architect-gui-workspace-review-20261021` 分支；不是把本地未验收产品推上云端。实际远端 SHA 与材料字节核对记录留在本地 data 目录。

下一批唯一目标为 R4 工作区一致性及真实观测口径；继续 Hark 原云端分支，具体实现自主。相对配置可一致支持或在任何外部调用前明确拒绝；默认/合法绝对配置保留。原生事实缺失、不符、来自外来线程或矛盾时，不宣称实际工作区已验证为空。优先限制在新试运行应用服务和必要展示/测试，不能擅改已验收通用适配器/契约。旧证据保留；不重调真实模型。修复后需另派 Windows 新 GUI 路径验证，旧错误 cwd 往返仅保留为消息证据。

执行AI-派单-07已送达并交付；不作废其已发生执行。新 Hark-派单-09仅为本轮生成，尚未确认送达；下一新编号为执行AI-派单-08、Hark-派单-10。N2/P1、一般派发、指定独立审查、continue/cancel、有界整改及真实协作闭环仍未完成，production complete 与自动文件应用仍关闭。
