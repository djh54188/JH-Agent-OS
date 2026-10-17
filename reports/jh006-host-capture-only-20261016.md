# JH-006 独立宿主记录采集证明 · 实施记录（未运行、未提交）

基线 dce2e77 已核对（`.git/refs/heads/n1-artifact-entry`）。本批只交付采集证明，
未开放生产完成，未重接 82d3d09 候选路由（当前树经搜索确认无残留），未改既有
Schema/权限/供应商，未启用 FileToolExecutor。Shell 在本会话不可用：探针代码、
解析器与回归测试已写完但均未执行，见文末未运行项。

## 本会话实测探针与原始结果

- P-shell：`muse.powershell` 执行 `git rev-parse HEAD`（工作目录为本工作树），
  原始错误：`sandbox enforcement unavailable: windows_elevated setup_required:
  sandbox users are not ready`。此后未再重试 Shell。
- P-read-ext：`muse.read_file` 直接读取架构师给出的会话段路径
  `C:\Users\a\.codex\sessions\2026\10\15\rollout-...-01a120b3-..._01a13fc4-....jsonl`，
  结果为**成功**（返回 1.6MB 文件前 40 行）。上一批“工作区外不可读”结论被本
  会话实测推翻，特此更正。
- P-search-ext：`muse.search` 可检索上述外部文件（命中 60 条上限）与外部目录
  （按 thread id 定位到同日另外两个含该 id 的段文件）。
- P-empty：交接引证的两处 `reports/jh004-real-host-20261014/host-events.jsonl`
  （工作树与原树根目录）在本会话读取均为 0 字节，架构师测量成立。
  有内容的 33 行文件位于 `host-attempt-1/` 子目录；上一批实施记录引证缺了
  该子目录，是引证错误，形状描述本身与深层文件一致。
- P-index：`host-command-index.json` 含 12 条 `item_N` 命令记录（1,2,4,5,6,7,9,
  10,12,13,14,16），与架构师计数一致，仅说明历史形状。
- P-doc：亲自抓取 `learn.chatgpt.com/docs/non-interactive-mode.md`（200 OK），
  确认 `codex exec --json` 把 stdout 变为 JSONL 事件流（含 thread.started、
  turn.started、turn.completed、item.*、error），宿主启动器可直接捕获。
- P-tree：`src/` 内搜索生产完成候选标识（`_complete_production_host_record`、
  `HOST_BRIDGE_RECORDER`、`codex-transcript`、`validate_host_record`、
  `host_record_accepted`）零命中；`tests/` 内无候选测试文件；候选已封存。

## 两种协议的实测区分（均来自原始字节）

桌面 rollout 日志：信封 `{timestamp,ordinal,type,payload}`；工具执行记录为
`event_msg/item_completed` 内 `CommandExecution{id:exec-UUID,process_id,
command:[argv 数组],cwd:file:// URI,parsed_cmd,source,status,
aggregated_output,exit_code,duration}`，另有 `custom_tool_call/call_output`
经 `call_id` 关联（`{type,id,call_id,output:list,...}`），`session_meta` 给出
聊天 cwd 与 originator。实测 `status` 含 `completed` 与 `failed` 两种取值；
`parsed_cmd` 恒为 `unknown`，不作为验证依据。

CLI 事件流：扁平 `{type,item}` 事件；`command_execution{id,item_N,
command:字符串,aggregated_output,exit_code,status}`，线程 id 仅见于
`thread.started` 信封。**无 cwd、无 argv 数组、无时间戳**，工作目录无法从该
协议记录本身绑定（解析器对此明确失败关闭，见下）。

工作目录归属实测：同一桌面段内，聊天 cwd 为
`file:///C:/Users/a/Documents/ChatGPT/JH-Agent%20OS`，而带 `workdir` 参数的
工具执行 cwd 为 `file:///D:/JH-Agent%20OS/data/worktrees/n1-artifact-entry`。
聊天 cwd 不能当任务工作区证明；每次执行以其自带 cwd 为准。

## 本批交付

- `src/host_capture.py`（新建，stdlib only）：严格 jsonl 解析（坏行即整体失败）、
  桌面段归属（session_meta 线程匹配）、桌面执行单选（缺失/重复失败）、桌面
  工具调用关联（call/output 配对、turn 一致、exec 工具 pin、调用脚本精确匹配）、
  CLI 执行单选（单线程信封检查）、桌面绑定核对（argv 数组精确相等、file URI
  工作目录精确相等、退出码、输出标记、输出上限）、CLI 绑定核对（命令全文精确
  相等先行，工作目录位恒定失败关闭并给出独立原因）、采集 manifest 组装、
  归档一致性重算。全程无 substring/contains 命令验证。
- `scripts/probe_host_capture.py`（新建，可执行）：受信宿主入口运行；参数只提供
  选择子、期望值与认领标签，执行事实只读会话段字节；段文件双读稳定性门；
  任一核对失败则退出 2 且不写任何文件；成功写 `events.jsonl`（逐字选取行）
  与 `manifest.json` 并打印核对报告。不调用模型/网络/子进程。
- `tests/test_jh006_host_capture.py`（新建，22 项，全合成日志，前缀 `test-synth-`）：
  桌面合法采集往返、归档篡改（输出与事件各一）、echo 关键词 argv、错工作区、
  聊天 cwd 冒充、跨线程、重复/缺失事件、坏行、非零退出、failed 状态、缺标记、
  调用 turn 错位、调用脚本不匹配、段归属错位、超大输出、CLI 工作目录缺位关闭、
  CLI 命令精确相等、CLI 缺线程信封、CLI 未确认退出、双向协议混淆拒绝。
- 生产完成入口保持关闭：本批未触 `complete_code_delivery` 路由；8 项边界回归
  继续覆盖拒绝语义。

## 原 34 项候选测试的两处错误（单独说明，不计通过）

以下逐字节核对封存件，均属上一批执行方（即本会话同一执行身份）测试代码错误，
不计入任何通过项，不删除证据：

1. 参数冲突（`TypeError: got multiple values for argument 'review'`）：封存测试
   第 168 行 helper 签名为 `_host_record(self, tid, delivery, review, **over)`，
   第 419–420 行调用又传 `review={"review_id": "rev_other0001"}` 覆盖字段，
   位置参数与关键字参数重名。正确做法应改名其一；封存件不改。
2. 摘要键错误（`KeyError`）：封存测试第 245–246 行断言
   `accepted[0]["details"]["record"]["output_digest"]`，而摘要实际位于
   `record["tests"]["output_digest"]`，顶层无此键。该断言从未验证任何内容。

## 发给架构师的内容（需裁决）

1. CLI 流工作目录缺位：`command_execution` 记录无 cwd 字段，解析器已按失败关闭
   实现并用回归锁定。请裁决：生产采集是否只接受桌面协议，或补充宿主启动器侧
   cwd 证明后再议 CLI 路径。
2. 归档写入信任边界：manifest/摘要只证明封存后一致；封存动作本身依赖受信宿主
   入口（算子运行 pin 定脚本、直读实时日志）。本地日志面对具 Shell 能力的积极
   篡改者无硬保证；当前威胁模型为“模型误报/夸大”，请确认该边界或加码。
3. 调用脚本精确匹配为可选项（`--expect-call-input-file`）：桌面调用 input 为整段
   JS 文本时适用；若生产固定检查改走直调 argv 而非脚本包装，该 pin 可收紧为
   必填，请定夺。
4. 真实取证仍未启动；本批未做“解析成功即完成”的任何连接，是否开放生产完成
   由审查后裁决。

## 未运行项（交架构师承接）

1. `tests/test_jh006_host_capture.py` 22 项（需 Shell：`python -m unittest`）。
2. `scripts/probe_host_capture.py` 对真实桌面段的试运行（含一个合法采集与至少
   一个负例复核）；本会话未执行任何采集。
3. 既有全量 259 项基线 + JH-005/006 守卫复核；按明确范围提交并提交后验证。
