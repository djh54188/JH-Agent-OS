# JH-018 应用/会话能力探测与适配契约 · 2026-10-17

**结论：两个候选应用都实测到了真实、可编程的会话接口。ZCode 有本地 CLI 与 ZCode Protocol stdio app-server；Codex CLI 有 JSON-RPC app-server，可对指定 thread 派发、接续、中断与发起审查。因此"向指定会话派任务/收交付/发整改"与"交付触发指定 Codex 审查会话工作"都**不需要**人工复制，也**不需要**新建 Agent Runtime。**

本批只做探测、契约设计与验证夹具：不改生产流程、Schema、门禁与权限；`production complete` 与 `FileToolExecutor` 保持关闭；未触碰用户正在使用的会话（所有写入只发生在自建隔离会话）。

基线 `d9cf37de5ba4d1692c473446cf1320373541d179`（分支 `n1-artifact-entry`）。

## 1. 能力矩阵

标记口径：**实测**＝本机实际调用并观察到结果；**文档/模式**＝有权威来源（随包生成的协议 schema 或 `--help`）但本批未实际调用；**不支持**＝实测确认无该能力；**未知**＝未取得证据。

### 1.1 ZCode（实测版本 0.16.9，`resources/glm/zcode.cjs` sha256 `31965ce1d5d4a808…`）

| 能力 | 结论 | 证据 / 限制 |
|---|---|---|
| 应用实例与会话寻址 | **实测** | 会话 id 形如 `sess_<uuid>`；`workspace.{workspaceKey,workspacePath}` 标识工作区；`sessionKind`；本地库 `session` 表另有 `project_id/workspace_id/parent_id/directory` |
| CLI 接口 | **实测** | `zcode -p/--prompt`、`--resume <sess_…>`、`-c/--continue`、`--json`、`--cwd`、`--mode build\|edit\|plan\|yolo\|guarded`、`--target`、`--surface terminal\|desktop`、`--disallowed-tools`；另有 `doctor/plugins/skills/commands/version` |
| stdio 协议接口 | **实测** | `zcode app-server --stdio`：换行分隔 JSON，请求 `{"id","method","params"}` → `{"id","result"}`。**双向**：服务端会反向发请求并在收到客户端应答前阻塞 |
| MCP | **不支持（作为服务端）** | 包内只有 MCP *客户端* 方法（`tools/list`、`resources/read`、`sampling/createMessage`…）与 `/mcp` 管理；未发现把 ZCode 暴露为 MCP server 的接口 |
| 派发任务 | **实测** | `session/create {workspace:{workspaceKey,workspacePath}}` → `session/send {sessionId,content}` 返回 `{accepted,sessionId,stateRevision}`；无头 `-p` 亦可 |
| 查询状态 | **实测** | `session/read` 返回 `projection.{status,turnCount,pendingPermissions,contextUsed,backgroundJobs,target}`、`runtime.eventSeq/stateRevision`、`session.*` |
| 收集交付 | **实测** | `session/messages`（含 role/modelId/providerId/tokens）、`session/usage`（`totalTokens/inputTokens/outputTokens/reasoningTokens/modelRequestCount/modelErrorCount/inputBaselineBySource`）、`session/events` |
| 接续 | **实测** | `--resume <sess_…>` / `session/resume`，或直接对已存在会话再 `session/send` |
| 取消 | **实测** | `session/stop {sessionId}` 把运行中的 turn 由 `running` 变为 `idle`；另有 `session/cancelBackgroundTask {sessionId,taskId}`（模式） |
| 运行事件 / 用量 / 错误 | **实测** | `session/events` 逐条带 `seq/eventId/turnId/traceId/type`（`turn.started`、`session.updated`、`session.titleUpdated`、模型调用遥测…）；通知 `state.updated`、`turn-*`；本地库有 `model_usage/tool_usage/turn_usage/permission` 表 |
| 同应用不同会话可靠区分 | **实测** | `session/list` 一次返回 13–31 条互不相同的 `sessionId`（含本项目会话与本次隔离探测会话） |
| 指定会话自动启动 | **实测** | 对已存在会话 `session/send` 即触发新 turn（`idle→running→idle`） |
| 结束后唤醒某个在用桌面会话 | **未知** | 未对用户在用会话发起任何写入（按本批约束）；机制存在但不是通过"当前桌面聊天"验证的 |

**必须写进适配器的 ZCode 限制**
1. 默认模型是已弃用的 `new-provider/muse-spark-1.3-contributor-free`（实测 `-p` 直接返回 `HTTP 410 ModelDeprecated`）。派发前必须 `session/setModel`，且 `model` 是对象 `{providerId,modelId,options:{reasoningLevel}}`；部分模型（如 `exo-free`）缺 `options.reasoningLevel` 会被拒（`Reasoning level is required`）。
2. app-server 是双向协议：`session/create` 会先向客户端发 `session/requestRuntimePreferences`，客户端不回（或回错 schema，必须含 `nativeSearchEnhancementsEnabled:boolean`）就 15 s 超时并失败。适配器必须实现服务端→客户端请求应答，并记录 `interaction/requestPermission`、`interaction/requestUserInput` 这类审批/追问。
3. 无 CLI 级 `status/cancel`；取消与状态查询只能走 app-server 或本地库。
4. `session/requestRuntimePreferences`、`controller/tasks-index`、`controller/workspaces` **不是**客户端可调用方法（返回 `-32601 Method not found`）；探测方法表时按此排除。

### 1.2 Codex（实测 `codex-cli 0.160.1`）

| 能力 | 结论 | 证据 / 限制 |
|---|---|---|
| 会话寻址 | **实测** | 会话即 `thread_id`（UUID），可用 `thread/list`、`thread/read`、`codex agents`；`ThreadSourceKind` 区分 `cli/vscode/exec/appServer/subAgent/…` |
| CLI 接口 | **实测** | `codex exec`、`codex exec resume <id>`、`codex queue --thread <UUID\|name> --message`、`codex review`（`--uncommitted/--base/--commit`）、`codex resume/fork/archive/agents` |
| JSON-RPC 协议接口 | **实测** | `codex app-server --stdio`（亦支持 `unix://`、`ws://IP:PORT`）；`initialize` 握手后可用；随包可生成 187 个方法的权威 schema |
| 派发任务 | **实测** | `codex exec "<prompt>" --json` → `thread.started{thread_id}`、`turn.started`、`item.completed`、`turn.completed{usage}`；协议侧 `thread/resume`→`turn/start` 返回 turn 对象（`status: inProgress`）并最终 `turn/completed` |
| 查询状态 | **实测** | `thread/list`、`thread/read`；通知 `thread/status/changed`、`thread/tokenUsage/updated` |
| 收集交付 | **实测** | 通知 `item/started`、`item/completed`、`item/agentMessage/delta`、`item/commandExecution/outputDelta`、`item/fileChange/outputDelta`、`turn/diff/updated`；CLI `--json` 直出 usage |
| 接续（活跃任务收到外部结果后继续） | **实测** | `codex exec resume <threadId> "…"` 在同一 thread 上产生第二个 turn（实测两次 turn，`input_tokens` 18227→37516） |
| 取消 | **文档/模式** | 协议 `turn/interrupt {threadId,turnId}`（另有 `thread/archive`、`codex queue` 队列语义）；本批**未实际调用** |
| 触发指定会话审查 | **文档/模式** | 协议 `review/start {threadId,target?,delivery?}` 与 `codex review` CLI；本批**未实际发起审查**（只为隔离 thread 做过 `turn/start`） |
| 运行事件 / 用量 / 错误 | **实测** | JSON 事件流含 `turn.completed{usage:{input_tokens,cached_input_tokens,output_tokens,reasoning_output_tokens}}`；通知含 `error`、`account/rateLimits/updated`、`model/verification` |
| 同应用不同会话可靠区分 | **实测** | `thread/list` 同时列出用户的 `vscode` 会话与本次 `exec` 隔离会话，`source` 与 `id` 可区分 |
| 结束后唤醒指定在用会话 | **文档/模式** | 协议可对任意持久化 thread `thread/resume`+`turn/start`（`thread/list` 里在用会话显示为 `notLoaded`，可被加载）；本批按约束**未向用户在用会话写入** |
| `codex queue`（向已有会话投递消息） | **未知（本批未跑通）** | 实测报 `failed to connect to …\app-server-control.sock`（本机 app-server daemon 未运行）；`--thread` 支持 UUID 或"精确会话名" |

**Codex 侧要点**：`initialize` 未握手前一切请求返回 `-32600 Not initialized`；持久化 thread 需先 `thread/resume` 载入，否则 `turn/start` 报 `thread not found`；`TurnSteerParams` 的 `expectedTurnId` 天然支持"旧轮次拒绝"，`clientUserMessageId` 支持幂等去重。

### 1.3 未运行项（如实列出）

- `codex queue`：需要本机 app-server daemon 运行；本批 daemon 未运行，未启动（避免改变系统状态）。
- `review/start`、`codex review`：未实际发起审查调用。
- `turn/interrupt`、`turn/steer`：未实际调用（取消走的是 ZCode `session/stop`）。
- ZCode 对用户在用桌面会话的写入：按约束不做。
- ZCode 声明式 `session/goal`、`session/subagents`、`session/fork`、`session/compact`、`usage/stats`：只验证了参数 schema 存在，**未实际调用**。
- 真实端到端"执行→审查→整改"闭环：**未运行**（本批只到接口能力层）。

## 2. AgentSessionAdapter 最小契约与复用映射

契约实现见 `src/agent_session.py`（新文件，无任何生产模块导入它），一致性夹具见 `tests/test_jh018_agent_session_contract.py`。

**六个能力**：`capabilities / submit / status / collect / continue / cancel`。关键设计：

- 地址是 `SessionAddress{app_kind, instance_id, session_id, model_id?, role?}`——按应用实例+原生会话寻址，**不以模型名充当会话地址**；角色只是登记属性。
- `submit` 带 `idempotency_key`（建议 `task_id/attempt`），一次逻辑派发重试不产生第二个 run。
- **运行状态里没有 `succeeded`**：只有 `queued/accepted/running/declared_complete/failed/cancelled/unknown`。成功属于控制层，适配器无权宣布。
- `CollectedResult.unverified[]` 必须显式列出适配器**没能确认**的事实；自报内容不因进入 JH 而变成可信证据。
- 不支持的能力**抛 `UnsupportedCapability`**（含 app_kind + capability），不得静默降级为"通知/人工接续"以外的另一种行为。
- 契约里没有任何 ZCode/Codex 专有字段；原生差异（`session/setModel` 的 reasoning level、`thread/resume` 前置、双向应答）留在各自适配器。

**现有模块复用映射（不新增存储、不改 Schema）**

| 契约要素 | 复用的现有模块 |
|---|---|
| `SubmitRequest.task_id/attempt/allowed_scope/workspace` | 协作核心任务状态机 + 既有授权范围与 `context.workspace_root` |
| 幂等与去重（`idempotency_key`） | 既有 call/item 唯一约束思路；外部侧映射到 ZCode `inputId/queryId`、Codex `clientUserMessageId` |
| `RunStatus`/`CollectedResult` 证据 | 存储与证据模块：`model_attempts`、`artifact_versions`、`task_events` |
| 审查绑定（task/attempt/产物哈希） | 现有 review binding 与 `active_attempt_id` 门禁 |
| 用量与预算 | 既有 `budget/tokens_unknown_count` → 映射 ZCode `session/usage`、Codex `turn.completed.usage` |
| 审批/追问 | 既有 `pending_json`/Level 2 审批 → 映射 ZCode `interaction/requestPermission`、Codex `commandExecution/requestApproval` |
| 模型 API 通道 | `ModelAdapter` 保留不变；`AgentSessionAdapter` 是另一层，二者不互相冒充 |

## 3. 消息与证据边界

**七个状态必须分开记录，不得互相冒充**：已接收 → 已投递 → 目标已接受 → 运行中 → 执行方声明完成 → 审查通过 → 任务成功。ZCode `session/send` 的 `accepted:true` 只是"已接受"；`turn.completed`/`declared_complete` 只是"执行方声明完成"；只有 JH 审查 pass + 全部必需验收 + 无未决权限/预算/副作用才可能是"任务成功"。

**下一步对象来自授权流程**：路由由已授权的角色→会话映射决定；执行方回复正文里的 `next_agent`/指令性文字是**待核实数据**，不得改写路由或授权。

**异常与边界处理**：
- 重复/重试：客户端幂等键去重（ZCode `inputId/queryId`、Codex `clientUserMessageId`），同一逻辑派发不得产生第二个 run。
- 旧轮次：Codex `turn/steer` 用 `expectedTurnId`、ZCode 用 `stateRevision`/`expectedRevision` 拒绝过期写入；迟到结果不得污染当前裁决。
- 错会话：地址是实例+session_id，`session_id` 不匹配即拒绝（实测 ZCode 给未知 sessionId 返回 schema/`sessionUnavailable` 类错误）。
- 断线：重连后先 `thread/read` / `session/read` 重查事实，不用乐观前端状态补写。
- 取消：区分"请求已发出 / 原生已确认 / 副作用未知"三态；副作用未知时禁止盲目重放（沿用既有要求）。
- 未知结果：`usage` 缺失记为未知（不记 0）；归属不明即 `unverified`。

**保留的既有约束**：task/产物哈希/审核关联（产物变化即旧审查失效）、预算上限、所有权、未知用量与副作用门禁一律沿用。外部 Agent 自报记录**不自动**成为可信证据；ZCode/Codex 的原生事件是"观测依据"，可信等级由审查与控制模块判定。

## 4. "执行 → 审查 → 整改"最小方案与验收案例

**最小方案（三段，全部走已实测接口）**

1. **执行（ZCode 适配器）**：`session/create`（隔离工作区）→ `session/setModel`（钉住可用模型 + reasoningLevel）→ `session/send`（任务信封，幂等键=task/attempt）→ 轮询 `session/read` 至 `idle` → 用 `session/messages`+`session/usage`+`session/events` 收集。记录 `sessionId/turnId/eventSeq/usage`，产出物哈希入 JH 存储。
2. **审查触发（Codex 适配器）**：对**已登记**的审查 thread `thread/resume` → `turn/start`（或 `review/start`），指令绑定 `{task_id, attempt, 产物 sha256, 工作区, commit}`；观察 `item/completed`+`turn/completed`。**不唤醒用户在用会话**，除非用户显式指定该会话。审查结论按 read-only 沙箱获取。
3. **整改（ZCode continue）**：审查 `revise` → 对**同一**会话同一 run `session/send`（= `continue_run`），带具体缺陷行号；**不重置**预算、授权与 attempt。

**验收案例（可复跑判据）**

| # | 案例 | 期望 |
|---|---|---|
| A1 | 对新建隔离 ZCode 会话派发一条可判定任务 | `session/send` 返回 `accepted:true`；观察到 `idle→running→idle`；`session/usage.modelRequestCount≥1`；`session/messages` 含 assistant 消息且 `modelId/providerId` 与设定一致 |
| A2 | 对已存在 ZCode 会话再次 `session/send` | 在该会话内产生新 turn，`sessionId` 不变（接续而非新建） |
| A3 | 派发时故意不 `setModel` | 明确失败（`ModelDeprecated` 类），适配器报 `failed`+原因，**不得**记 running |
| A4 | 运行中 `session/stop` | `running→idle`，`turnCount` 不增；`cancel` 返回"请求已发出"，不声称副作用已清零 |
| A5 | 用两个不同 mock 适配器驱动同一调用点 | 调用代码零修改（本批 `test_jh018_agent_session_contract.py` 已过）；不支持的能力抛 `UnsupportedCapability` |
| A6 | 对隔离 Codex thread 先 `turn/start` A、再 `exec resume` B | 同一 `thread_id` 两次 turn；`--json` 事件流含两次 `turn.completed` 与 usage |
| A7 | 重复投递同一 `idempotency_key` | 只产生一个 run（适配器去重），不重复计费 |
| A8 | 旧轮次/错 session 写入 | 被拒（`expectedTurnId`/`stateRevision` 不匹配，或 session 不存在） |
| A9 | 产物在审查后被改动 | 既有绑定失效，审查作废（沿用现有门禁，本批未改） |
| A10 | 交付消息只证明"已投递" | 不得直接置任务 `succeeded`；必须另有一轮独立审查记录 |

**本批未完成的**：A1/A2/A4/A6 已在隔离会话实测到接口层行为；A3 由实测的 `HTTP 410` 观察到；A5 由本批测试覆盖；A7–A10 是设计判据，**未运行**（属后续实施批次）。

## 5. 证据与脚本

- `scripts/probe_zcode_capabilities.py`：ZCode app-server 探测（`--enumerate` 方法表 / `--roundtrip` 派发-收集-停止 / `--cancel-trial` / `--events-trial`），含服务端→客户端请求应答。
- `scripts/probe_codex_sessions.py`：Codex app-server 探测（`initialize`→`thread/list`→`thread/read`→`thread/resume`→`turn/start`→等 `turn/completed`）。
- 结果：`data/jh018-probes-20261017/`（`enumerate.json`、`roundtrip.json`、`codex-app-server.json`、随包生成的 `codex-app-server-schema/`）。`data/` 为忽略目录，证据不入 Git。
- 原始 headless 记录：`D:/tmp/jh018-probe/`（ZCode）与 `D:/tmp/jh018-codex/`（Codex `exec1.jsonl`、`resume1.jsonl`）。

**边界**：`production complete`、`FileToolExecutor` 继续关闭；未恢复失败方案；未改凭据、封存夹具、Schema 与门禁；未把 mock 适配器当作"第二个应用已接入"；未把 `accepted:true`、`turn.completed` 或"消息已投递"当作任务成功。
