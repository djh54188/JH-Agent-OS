# JH-018 应用/会话能力探测与适配契约 · 2026-10-17（R1–R4 整改后）

**结论（接口能力层，整改待审）：两个候选应用都实测到真实、可编程的会话接口。** ZCode 0.16.9 有本地 CLI 与 ZCode Protocol stdio app-server；Codex CLI 0.160.1 有 JSON-RPC app-server，可对指定 thread 发起并接续一轮工作。故"向指定会话派任务 / 收交付 / 发整改"与"交付触发指定 Codex 会话工作"在接口上成立，**不需要**人工复制，也**不需要**新建 Agent Runtime。

本批只是探测、契约设计与验证夹具。**未实现 GUI、统一应用接口或产品闭环**；不改生产流程、Schema、门禁与权限；`production complete` 与 `FileToolExecutor` 保持关闭；所有写入只发生在自建隔离会话，**未向用户正在使用的会话投递任何内容**。基线 `9595b347`（R1–R4 整改前），分支 `n1-artifact-entry`。

## 0. 四种能力必须分开（本批实际做到哪一步）

| 场景 | 含义 | 本批状态 |
|---|---|---|
| 已结束会话恢复 | 对已经跑完的会话再开一轮，历史保留 | **实测**（ZCode `session/resume`/`--resume sess_…`；Codex `thread/resume`+`turn/start`、`codex exec resume <threadId>`） |
| 活跃轮次接续 | 目标会话**正在跑**时注入下一步/整改 | **未实测**（接口存在：Codex `turn/steer {threadId, expectedTurnId}`；ZCode 对运行中会话再 `session/send`。本批只做过"等待终态后再发"） |
| 实际审查 | 真的发起一次 review 并取得审查结论 | **未实测**（只验证 `review/start {threadId,target?,delivery?}` 与 `codex review` 存在；未调用） |
| 完整协作闭环 | 执行→收集→独立审查→一次整改→成功判定 | **未运行**（属后续实施批次） |

## 1. 能力矩阵

标记：**实测**＝本机实际调用并观察到结果；**文档/模式**＝有权威来源（随包生成的协议 schema 或 `--help`）但本批未实际调用；**未发现**＝在包/接口中未找到该能力，且未做穷尽验证；**未知**＝未取得证据。

### 1.1 ZCode（实测 0.16.9，`resources/glm/zcode.cjs` sha256 `31965ce1d5d4a808…`）

| 能力 | 结论 | 证据 / 限制 |
|---|---|---|
| 应用实例与会话寻址 | 实测 | 会话 id `sess_<uuid>`；`workspace.{workspaceKey,workspacePath}`；`sessionKind`；本地库 `session` 表另有 `project_id/workspace_id/parent_id/directory` |
| CLI 接口 | 实测 | `-p/--prompt`、`--resume <sess_…>`、`-c`、`--json`、`--cwd`、`--mode`、`--target`、`--surface`、`--disallowed-tools`；`doctor/plugins/skills/commands/version` |
| stdio 协议接口 | 实测 | `zcode app-server --stdio`：换行分隔 JSON，请求 `{"id","method","params"}` → `{"id","result"}`。**双向**：服务端会反向发请求并阻塞等待客户端应答 |
| MCP | **未发现**（作为服务端） | 包内只见 MCP *客户端* 方法（`tools/list`、`resources/read`、`sampling/createMessage`…）与 `/mcp` 管理命令；未见把 ZCode 暴露为 MCP server 的接口。未做穷尽验证，故不写"不支持" |
| 派发任务 | 实测 | `session/create` → `session/send {sessionId,content}` 返回 `{accepted,sessionId,stateRevision}`；无头 `-p` 亦可（但受下条限制） |
| 查询状态 | 实测 | `session/read` → `projection.{status,turnCount,pendingPermissions,contextUsed,backgroundJobs,target}`、`runtime.eventSeq/stateRevision`、`session.*` |
| 收集交付 | 实测 | `session/messages`（role/modelId/providerId/tokens）、`session/usage`（`totalTokens/inputTokens/outputTokens/reasoningTokens/modelRequestCount/modelErrorCount/inputBaselineBySource`）、`session/events` |
| 已结束会话恢复 | 实测 | `--resume <sess_…>` / `session/resume`；或对已存在会话再 `session/send` |
| 活跃轮次接续 | 未实测 | 机制上对运行中会话可再 `session/send`；本批未在 running 中注入 |
| 取消 | 实测（请求语义） | `session/stop {sessionId}`：把 running 的 turn 变回 `idle`；`session/cancelBackgroundTask {sessionId,taskId}` 仅验参数 schema |
| 运行事件 / 用量 / 错误 | 实测 | `session/events` 逐条带 `seq/eventId/turnId/traceId/type`（`turn.started`、`session.updated`、`session.titleUpdated`、模型调用遥测…）；通知 `state.updated`、`turn-*`；本地库有 `model_usage/tool_usage/turn_usage/permission` |
| 同应用不同会话可靠区分 | 实测 | `session/list` 一次返回 13–31 条互不相同的 `sessionId`（含本项目会话与本次隔离探测会话） |
| 结束后唤醒某个在用桌面会话 | 未知 | 未对用户在用会话发起任何写入（本批约束）；机制存在，但未通过"当前桌面聊天"验证 |

**必须写进适配器的 ZCode 限制**
1. 默认模型是已弃用的 `new-provider/muse-spark-1.3-contributor-free`（无头 `-p` 实测 `HTTP 410 ModelDeprecated`，且 `-p` **不接受** `--model`）。派发前必须 `session/setModel`，`model` 为对象 `{providerId,modelId,options:{reasoningLevel}}`；部分模型（如 `exo-free`）缺 `reasoningLevel` 会被拒。
2. app-server 双向：`session/create` 会先反向请求 `session/requestRuntimePreferences`（schema 需 `nativeSearchEnhancementsEnabled:boolean`），客户端不应答即 15 s 超时失败。
3. 无 CLI 级 `status/cancel`；只能走 app-server。
4. `session/requestRuntimePreferences`、`controller/tasks-index`、`controller/workspaces` 客户端调用实测返回 `-32601 Method not found`（不是可调用方法）。

### 1.2 Codex（实测 `codex-cli 0.160.1`）

| 能力 | 结论 | 证据 / 限制 |
|---|---|---|
| 会话寻址 | 实测 | 会话即 `thread_id`（UUID）；`thread/list`/`thread/read`/`codex agents`；`ThreadSourceKind` 区分 `cli/vscode/exec/appServer/subAgent/…` |
| CLI 接口 | 实测 | `exec`、`exec resume <id>`、`queue --thread <UUID\|name> --message`、`review`（`--uncommitted/--base/--commit`）、`resume/fork/archive/agents` |
| JSON-RPC 协议接口 | 实测 | `codex app-server --stdio`（亦 `unix://`、`ws://IP:PORT`）；`initialize` 握手后可用；随包可生成 187 方法权威 schema |
| 派发任务 | 实测 | `codex exec "…" --json` → `thread.started{thread_id}`、`turn.started`、`item.completed`、`turn.completed{usage}`；协议侧 `thread/resume`→`turn/start` 返回 turn 对象并最终 `turn/completed` |
| 查询状态 | 实测 | `thread/list`、`thread/read`；通知 `thread/status/changed`、`thread/tokenUsage/updated` |
| 收集交付 | 实测 | 通知 `item/started`、`item/completed`、`item/agentMessage/delta`、`item/commandExecution/outputDelta`、`item/fileChange/outputDelta`、`turn/diff/updated`；CLI `--json` 直出 usage |
| 已结束会话恢复 | 实测 | `codex exec resume <threadId> "…"` 在同一 thread 产生第二轮（实测两次 `turn.completed`，`input_tokens` 18227→37516） |
| 活跃轮次接续 | 未实测 | 协议 `turn/steer {threadId, expectedTurnId, input}`（`expectedTurnId` 天然拒绝旧轮次）；未调用 |
| 取消 | 未实测 | 协议 `turn/interrupt {threadId,turnId}`；未调用 |
| 触发指定会话审查 | 未实测 | 协议 `review/start {threadId,target?,delivery?}` 与 `codex review` CLI；本批只为隔离 thread 做过 `turn/start` |
| 运行事件 / 用量 / 错误 | 实测 | 事件流含 `turn.completed{usage:{input_tokens,cached_input_tokens,output_tokens,reasoning_output_tokens}}`；通知含 `error`、`account/rateLimits/updated` |
| 同应用不同会话可靠区分 | 实测 | `thread/list` 同时列出用户的 `vscode` 会话与本次 `exec` 隔离会话，`source` 与 `id` 可区分 |
| 结束后唤醒指定在用会话 | 未实测 | 协议可对任意持久化 thread `thread/resume`+`turn/start`（在用会话显示 `notLoaded`，可加载）；本批按约束未向用户在用会话写入 |
| `codex queue` | 未知 | 实测报 `failed to connect to …\app-server-control.sock`（本机 app-server daemon 未运行）；未启动 daemon，未跑通 |

**Codex 侧要点**：`initialize` 未握手前一切请求 `-32600 Not initialized`；持久 thread 需先 `thread/resume` 载入，否则 `turn/start` 报 `thread not found`；`TurnCompletedNotification` 带 `threadId`+`turn{id,status,error}`，可用作完成判定与错关联检查。

### 1.3 未运行项（如实列出）

`codex queue`（daemon 未起）、`review/start`、`codex review`、`turn/interrupt`、`turn/steer`；ZCode `session/goal|subagents|fork|compact|cancelBackgroundTask|subscribe`（仅验 schema）；ZCode 活跃轮次注入；对用户在用会话的写入；完整"执行→审查→整改"闭环。

## 2. AgentSessionAdapter 最小契约与复用映射

实现：`src/agent_session.py`（新文件，无生产模块导入）。一致性夹具：`tests/test_jh018_agent_session_contract.py`（23 项，行为测试）。

六个能力：`capabilities / submit / status / collect / continue / cancel`。设计要点：

- 地址 `SessionAddress{app_kind, instance_id, session_id, model_id?, role?}`；角色是登记属性，不作地址。
- **运行绑定** `RunBinding{app_kind, instance_id, session_id, task_id, attempt}`：run 绑定应用实例+会话+逻辑任务+轮次。
- **逻辑任务 ≠ 原生轮次**：`NativeTurn{native_turn_id, native_session_id}`；一个 attempt 可跨多个原生轮次（初始 + 整改）。
- **两个索引分开**：`run_id` 索引与幂等键索引独立。返回的 `run_id` 必解析到自身；**跨地址查询抛 `AddressMismatch`，不得静默 `unknown`**；同一幂等键配不同请求抛 `IdempotencyConflict`，不得命中缓存。
- **取消是请求**：`CANCEL_REQUESTED` 只表示"请求已发出/待确认"；`CANCELLED` 仅在观察到原生确认后出现（mock 用 `confirm_cancel` 模拟该观察点）。
- **整改保留上下文**：`continue_run` 追加一个原生轮次，任务、历史、累计预算与授权范围不变，不加重授权、不重启 attempt。
- **无 `succeeded` 运行态**；`CollectedResult.unverified[]` 显式列出未确认事实。
- 不支持的能力**抛 `UnsupportedCapability`**，不静默降级。

**复用映射（不新增存储、不改 Schema）**：任务/授权/工作区 ← 协作核心与 `context.workspace_root`；幂等去重 ← 既有 call/item 唯一约束思路，外部映射 ZCode `inputId/queryId`、Codex `clientUserMessageId`；证据 ← `model_attempts/artifact_versions/task_events`；审查绑定 ← 现有 review binding 与 `active_attempt_id`；用量/预算 ← `budget/tokens_unknown_count` ← ZCode `session/usage`、Codex `turn.completed.usage`；审批 ← `pending_json`/Level 2 ← ZCode `interaction/requestPermission`、Codex `commandExecution/requestApproval`；`ModelAdapter` 保留，二者不互相冒充。

## 3. 消息与证据边界

七个状态分开：已接收 → 已投递 → 目标已接受 → 运行中 → 执行方声明完成 → 审查通过 → 任务成功。ZCode `accepted:true` 只是"已接受"；`turn.completed`/`declared_complete` 只是"执行方声明完成"；仅当 JH 审查 pass + 全部必需验收 + 无未决权限/预算/副作用才可能是成功。

下一步对象来自授权流程；执行方正文里的 `next_agent`/指令性文字是待核实数据，不得改写路由或授权。

异常处理：重复/重试走客户端幂等键；旧轮次用 Codex `expectedTurnId`、ZCode `stateRevision`/`expectedRevision` 拒绝；错会话按实例+session_id 拒绝；断线重连先 `thread/read`/`session/read` 重查；取消区分"请求已发出/原生已确认/副作用未知"；`usage` 缺失记未知而非 0；归属不明入 `unverified`。既有 task/产物哈希/审核关联、预算、所有权、未知用量与副作用门禁全部沿用；外部自报不自动成为可信证据。

## 4. "执行 → 审查 → 整改"最小方案与验收案例

1. **执行（ZCode 适配器）**：`session/create`（隔离工作区）→ `session/setModel` → `session/send`（幂等键=task/attempt）→ 轮询 `session/read` 至终态 → `session/messages`+`session/usage`+`session/events` 收集。
2. **审查触发（Codex 适配器）**：先做**归属检查**（`thread.cwd` == 隔离目录 且 preview 含实验标记），通过后 `thread/resume` → `turn/start`（或 `review/start`），指令绑定 `{task_id, attempt, 产物 sha256, 工作区, commit}`；完成通知必须匹配本 thread 与本 turn，并检查最终 status/error。**不唤醒用户在用会话**。
3. **整改**：审查 `revise` → 对同一 run `continue_run`（新增原生轮次），不重置预算/授权/attempt。

| # | 案例 | 期望 | 本批状态 |
|---|---|---|---|
| A1 | 新建隔离 ZCode 会话派发可判定任务 | `accepted:true`；`idle→running→idle`；`usage.modelRequestCount≥1`；assistant 消息的 modelId/providerId 与设定一致 | 实测通过（探测脚本判据） |
| A2 | 对已存在 ZCode 会话再 `session/send` | 同一 `sessionId` 产生新 turn | 实测通过 |
| A3 | 派发时故意不 `setModel` | 明确失败（`ModelDeprecated`），适配器报 failed，不记 running | 实测（`HTTP 410`） |
| A4 | 运行中 `session/stop` | `running→idle`；取消只报"请求已发出" | 实测通过（请求语义） |
| A5 | 两个**不同类**适配器驱动同一调用点 | 代码零修改；不支持抛 `UnsupportedCapability` | 本批测试覆盖（23 项） |
| A6 | 隔离 Codex thread 先 `turn/start` 再 `exec resume` | 同 `thread_id` 两轮，`turn.completed` 带 usage | 实测通过 |
| A7 | 重复投递同一 `idempotency_key` | 只产生一个 run | 测试覆盖 |
| A8 | 旧轮次/错 session 写入 | 被拒 | 部分：Codex 完成通知错关联/错 turn 已有离线反例；`expectedTurnId` 实拒未实测 |
| A9 | 产物在审查后被改动 | 旧审查失效 | 沿用现有门禁，本批未改未测 |
| A10 | 只证明"已投递" | 不得直接置 `succeeded` | 契约已禁（无 succeeded 态），端到端未运行 |

## 5. 证据与脚本

**脚本**（均可离线自测）：

- `scripts/probe_zcode_capabilities.py`：实时模式 `--enumerate`（schema，独立判据）/`--roundtrip`/`--cancel-trial`/`--events-trial`；`--selftest` 用离线假传输跑 **19 例**（2 正 + 17 反），且**每个反例都断言具体失败原因**（`expected_reason` 必须出现在失败列表里，避免因别的错误侥幸通过）。覆盖：协议错、超时、未接受、错 session、从未 active、**持续运行直到超时**、失败终态、用量为 0/错会话、无 assistant 消息、模型绑定不符、未知服务端请求、未 ready；取消侧含**正常取消正例**、**停止请求被拒负例**、**停止后仍持续运行负例**、**取消前从未 active 负例**。判据失败即退出非零。
- `scripts/probe_codex_sessions.py`：`--thread` + `--expect-cwd` + `--expect-preview`；**归属不明则拒发**（退出码 3，不发送）；完成通知必须匹配本 thread 与本 turn 且 status/error 合规；未知服务端请求回错误并判失败；`--selftest` 用离线假传输跑 13 例（含归属拒发且未调用 resume/turn-start）。

**生命周期判据（本轮收紧）**：ZCode 的完成判定不再以"不属于错误状态"代替成功终态。运行必须落到 `TERMINAL_OK`（`idle`/`completed`）；**超时仍处于 `running/queued/paused/waiting` 一律失败**（独立检查项 `*.not_active_on_timeout`），`error/failed/stopped` 亦失败（`*.terminal_ok`）；调度路径要求先观察到 active（`*.observed_active`）。取消试验**沿用停止前已观察到的 active 事实**（`cancel.observed_active_before_stop`），停止后**立即 idle 即可通过**（`require_active_first=False`），不要求再次进入 running。

**实测证据目录** `data/jh018-probes-20261017/`（`data/` 未跟踪，不入 Git）：

- `enumerate.json`、`roundtrip.json`、`codex-app-server.json`、`codex-app-server-schema/`（随包生成 275 个 v2 schema 文件）。
- `precommit/`、`postcommit/`：上一提交 `9595b347` 的提交前后全量测试原文与退出码（各 398 项 OK/skipped=1，退出码 0）。
- `raw/`：**原始探测文件只读副本**，见 `raw/README.md`。

**`exec1.exit=1` 与成功回复并存（已修正口径，未覆盖旧文件）**

- **可直接核实的事实**：`raw/codex-exec/exec1.exit` 内容为 `EXIT=1`；同目录 `exec1.jsonl` 是 4 行成功事件（`thread.started`/`turn.started`/`item.completed(PROBE_OK)`/`turn.completed(usage)`）；`exec1.err` 仅剩 `Reading additional input from stdin...`。`resume1.*` 为单次尝试，`.exit`=0 与 `.jsonl` 一致。
- **执行方说明（未独立证实）**：这两个文件被两次尝试共用文件名——第一次缺 `--skip-git-repo-check` 失败（退出 1，stderr 为 `Not inside a trusted directory and --skip-git-repo-check was not specified.`）并写了 `exec1.exit`；第二次成功，命令把 stdout/stderr 重定向覆盖、退出码只打印未回写，故 `.exit` 停留在第一次的值。
- **为何只能算"说明"**：已检索本机 ZCode 执行日志（`~/.zcode/cli/exec/<session>/`）与仓库，**未找到**该次失败的原始终端/命令记录（该目录只有 stdout 日志，无 stderr），第一次的 stderr 文本已被第二次重定向覆盖。因此失败原因不可独立复核，只能作为执行方陈述；成功回复与 `.exit` 的矛盾本身是可直接观察的事实。结论只引第二次的成功回复。未重跑模型补造历史。

**本轮收口（R1 生命周期 / R2 指纹 / R3 整改）**

- **R1**：见上"生命周期判据"；新增"持续运行直到超时""停止后仍持续运行"两个负例与"正常取消"正例，负例断言具体原因。
- **R2**：幂等指纹改为 **SHA-256 over 规范化 JSON**（`canonical_request`/`fingerprint`），覆盖地址、任务、attempt、工作区、授权范围、预算、指令与**产物引用及其版本/内容哈希**；不再使用分隔符拼接，输入里含 `|` 等字符也不会碰撞（已用 `t|1`+`x` 与 `t`+`1|x` 反例锁定）。`submit` 时保存**请求快照**（规范化 JSON + 指纹）；调用方事后修改自己的可变 dict 不改变已登记事实，且该修改会被判为"不同请求"→ `IdempotencyConflict`。
- **R3**：同一 run + 同一幂等键 + 同一整改请求**只追加一个原生轮次**（重放返回同一 turn）；同键不同请求 `IdempotencyConflict`；`continue_run` 返回值与随后 `status` 查询一致（都置 `RUNNING`）；**已确认取消的运行**再要求整改抛 `RemediationRefused` 且不追加轮次；两个不同类 mock 都跑这些行为测试，历史/预算/授权范围保持。

**本轮收口 B（异步启动 / 整改重放关联 / 取消终态）**

- **异步启动**：ZCode 等待改为**两段有界**——先等本次执行开始（`start_timeout`），再等终态（`settle_timeout`）。`accepted` 之后先出现一段 `idle` **不再判失败**；仍保留三条拒绝判据：从未启动（`*.started`）、超时仍活跃（`*.not_active_on_timeout`）、失败终态（`*.terminal_ok`）。取消路径不做开始等待（`require_active_first=False`），停止后立即 idle 照旧通过，未回退。新增正例 `async_start_idle_then_running`（实测 `polls_before_active=2`，即容忍了一段 idle 后仍 settled）。
- **整改重放关联**：幂等记录改为保存"请求指纹 + 该请求对应的**原生轮次**"。交错序列 A(key=A) → B(key=B) → 重放 A 时，返回的是 **A 自己的操作轮次**，不会把 B 当作 A。`RunStatus` 明确区分 `native_turn`（本操作对应轮次）与 `latest_turn`（运行当前最新轮次），`state` 始终是运行**当前**状态，为返回历史轮次不会倒退当前状态。两个适配器都补了交错重放与"重放不倒退状态"测试。
- **取消终态**：确认取消后重复取消保持 `CANCELLED`，不追加轮次、不回退为 `CANCEL_REQUESTED`（note 标 `already cancelled`）；已取消运行拒绝整改的行为保留。两个适配器均补回归。
- **组合序列自检**：新增 `TestCombinedSequenceConsistency`，对两个适配器逐一跑 `派发 → 整改 A → 整改 B → 重放 A → 状态查询 → 取消请求 → 重复取消 → 确认取消 → 再取消 → 拒绝整改 → 收集`，断言每一步返回值与记录一致（重放返回 A 的轮次、`state` 跟记录、轮次总数 3、收集不报 `succeeded`）。

**本轮验证**：全量 **426 项 425 通过 / 1 既有跳过**（`test_phase2_hardening.py:152`）；ZCode selftest **20 例**（3 正 + 17 反，反例断言具体原因）、Codex selftest 13 例；契约夹具 37 项行为测试。**不调用真实模型**：只用离线假传输与既有真实记录（`raw/` 副本、`codex-app-server-schema/`）验证；真实往返判据未重跑（旧实测记录已归档保留）。

**边界**：`production complete`、`FileToolExecutor` 继续关闭；未恢复失败方案；未改凭据、封存夹具、Schema、门禁与权限；未把 mock 适配器当作"第二个应用已接入"；未把 `accepted:true`、`turn.completed` 或"消息已投递"当作任务成功；**本批状态为"整改待审"**。
