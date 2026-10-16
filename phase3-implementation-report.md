# JH-Agent OS 阶段 3：真实双模型闭环 实现交付报告

- **日期**：2026-10-11（Asia/Shanghai）
- **开发基线**：`28b1f4611a2c4f1eda57e92cf87a95ca626dcba2`（阶段 2 收口提交）
- **工作分支**：`phase3-real-dual-model-loop`
- **结论（负面优先；本节为 2026-10-11 早间状态，已被 §7/§8 补遗取代）**：~~真实 Gemini+Muse 闭环验收仍未完成~~
  → **最新状态见 §7/§8**：Gemini 通道经 ADR-0002 本地代理接通后，真实 Gemini+Muse 主闭环与修复闭环证据均已取得。
  以下历史段落保留原始取证过程，其中"缺密钥/阻塞"的描述均为当时状态，不再反映现状。

---

## 1. 交付内容与改动文件

### 1.1 Worker 执行契约（补齐任务书第三节两个缺口）
| 文件 | 改动 |
| :--- | :--- |
| `docs/schemas/artifact.v1.json` | 新增 `ArtifactSchemaV1`：产物结构化协议 v1.0.0（`artifact_id`/`task_id`/`artifact_type`/`content`/`summary`）。模型只声明身份与内容；**版本号与 SHA-256 由控制层计算**，模型哈希永不采信 |
| `src/orchestrator.py` | ① 规划消息改为**可信上下文**（task_id、目标、验收条件、约束、input_data——修复缺口 1）；② 执行循环新增**无工具产物生成路径**：经统一 `_invoke_model`（尝试登记、预算计算、用量记录）调用 `invoke_structured(ArtifactSchemaV1)` 真实生成产物——修复缺口 2；③ 修复调用注入**原始目标 + 当前产物（版本/SHA-256/内容）+ Reviewer 问题 + 验收失败证据**；④ 产物经任务关联校验后按 `repair_count+1` 计版本、控制层算哈希、存 SQLite 并登记证据引用 |
| `src/mock_components.py` | `ScriptedWorkerAdapter` 支持产物生成契约（`artifact_content_by_turn`、修复上下文捕获 `generation_contexts`、`truncated`/`refused` 故障模式）；`mock_acceptance` 兼容三参 |
| `src/adapters.py` | Gemini/OpenAI 兼容适配器增加**截断与拒绝检测**（finishReason MAX_TOKENS/SAFETY、finish_reason length/content_filter → AdapterError）；Gemini 结构化调用增加官方 `responseMimeType=application/json`（同端点同协议的兼容增强，不使用 responseSchema，Schema 校验保持本地权威） |

### 1.2 可信验收与独立审核
| 文件 | 改动 |
| :--- | :--- |
| `src/acceptance.py` | 新增控制层自有验收器 `transform_acceptance_evaluator`：从 input_data **独立计算期望输出**，对实际产物内容做确定性判定；证据引用登记进 `task.tool_execution_evidence`，可追溯；不采信 Worker 自评分 |
| `src/artifacts.py` | 新增 `ArtifactStore`：`mem://<task_id>/<artifact_id>` 虚拟路径的内存/SQLite 产物谱系；`content_sha256` 控制层权威哈希；`snapshot_artifacts` 与工具产物走同一 `artifact_snapshot` 校验（审核前后哈希一致性与 OPT-05 同源） |
| `src/orchestrator.py`（审核段） | Muse 审核上下文包含原始要求、验收条件、**当前完整产物快照（内容+SHA-256）** 与工具/验收证据；`validate_review` 校验元数据关联、前后哈希一致、证据行号合法；成功门槛（`_validate_success`）要求：当前有效独立审核 pass + 全部验收条件通过且证据可追溯 + 无未解决权限/预算/用量不确定性。修复后产物重新计版本并**强制重新审核**（旧审核绑定旧 attempt，不可沿用） |

### 1.3 最小持久化闭环
| 文件 | 改动 |
| :--- | :--- |
| `src/storage.py` | 新增 `artifact_versions`（task_id+version 主键，内容、控制层 SHA-256、生成 attempt_id）与 `memory_records` 表；`save_artifact_version`/`get_latest_artifact`/`list_artifact_versions`/`attach_artifact`/`record_memory`/`list_review_bindings`；全部原子事务 |
| `src/memory.py` | 新增最小 MemoryRecord 写入：成功任务→1 条 **verified** 任务级事实（来源=任务+产物哈希+审核结论）+ 1 条 **unverified** 泛化候选（明确标注单次结果不得引用为统计）；失败任务→1 条 **unverified** negative_result（保留失败证据）。无检索平台/自动学习/自优化 |
| `src/cli.py` | 新增 `artifact` 子命令：只读查询终态、产物谱系、审核绑定、验收证据与记忆记录；**不触发任何模型调用** |

### 1.4 配置与接入说明
| 文件 | 改动 |
| :--- | :--- |
| `configs/adapters.example.json`、`configs/adapters.local.json` | `gemini_worker.model_id` 由已退役的 `gemini-1.5-pro` 修正为 **`gemini-3.8-flash`**（依据 [Gemini API 官方模型文档](https://ai.google.dev/gemini-api/docs/models) 当前稳定 Flash 档，核实日期 2026-10-11，核实来源记录在配置字段中） |
| `docs/adapter-contracts.md` | 同步模型 ID 修正与来源 |
| `docs/phase-status.md` | 新增阶段状态说明：阶段 2 = 离线控制层验收；阶段 3 = 真实双模型闭环（本阶段，实现交付、验收未完成）；阶段 4 = 完整 v0.1 MVP |
| `docs/schemas/README.md` 未改动 | artifact Schema 说明见 `artifact.v1.json` 内部描述（兼容性说明：新增协议版本 1.0.0，不改既有六类 Schema；Plan/Review/ToolRequest/ToolResult 协议原样复用） |

---

## 2. 验证证据（离线 / 真实 / 故障注入分别标注）

### 2.1 离线验证（is_mock=true）——全部通过
- **单元测试**：`python -m unittest discover tests` → **运行 91 项全部通过**（90 通过 + 1 项跳过为阶段 4 junction 预留；90 系审查 minor 修复前基线，本轮新增 1 项锁定测试后为 91）。其中新增 `tests/test_phase3_loop.py` 12 项：
  - **A 正常流程**（4 项）：生成→可信验收→独立审核→succeeded；产物 v1 + 控制层 SHA-256 + 审核绑定关联；控制验收不采信 Worker/Reviewer 意见（产物错误时即使 review pass 也 needs_attention）。
  - **B 错误发现与修复**（2 项）：夹具注入错误产物（注入内容与前后哈希在测试与断言中显式记录，绝不归属为模型自然生成）→ Muse-mock 出 revise 带证据 → 修复上下文含 Reviewer 问题+当前产物哈希 → v2 重新验收+重新审核（新旧 review_id/attempt_id 不同）→ succeeded；修复上限 2 轮耗尽→failed，负结果记忆保留证据。
  - **C 失败路径**（4 项）：生成阶段 timeout/invalid_json/truncated/refused 全部 fail-closed 且无产物落库；产物 task_id 关联不符被拒；产物 Schema 不符被拒；未知用量 fail-closed 到 needs_attention（生命周期类失败路径复用阶段 2 OPT 套件，未重复实现）。
    **失败语义分野（审查 minor #2 补释）**：生成调用链失败（超时/截断/拒绝/非法 JSON/产物关联或 Schema 不符）进 `failed` 终态；预算、时限与用量不确定性（无法安全继续或重放）进 `needs_attention`——与阶段 2 OPT 套件的生命周期分工一致，`tests/test_phase3_loop.py` 断言与之一致。
  - **D 持久化**（2 项）：文件库重启后查询终态/产物/审核绑定/记忆记录，模型调用次数为零；产物仅存 `mem://` 路径与 SQLite，绝无真实文件写入。
- **变异守卫**：`python scripts/verify_phase3_guards.py` → **4/4 全部检出**（产物关联校验跳过、验收证据引用篡改、修复上下文降级、泛化经验误标 verified）。
- **既有守卫复用**：`verify_phase2_hardening.py`（14 变异检出）、`verify_phase2_guards.py`（5/5）、`verify_responses_guards.py`（8/8）在本分支复跑全部通过。

### 2.2 真实调用（is_mock=false）——Muse 与补充闭环已取证；Gemini 当时阻塞（历史状态，已由 §7 补遗解除）
- Muse 复认两项真实审核通过（正确产物 pass；注入缺陷被 revise 且证据引用缺陷表达式）；
  DeepSeek（worker）+ Muse（reviewer）补充闭环真实跑通至 succeeded。
- 真实 Gemini+Muse 闭环（场景 A/B 的验收路径）当时因缺少凭据未执行（已由 §7/§8 补遗补齐）。
### 2.3 故障注入（is_mock=false 的故障模式，不破坏宿主网络）
- 截断/拒绝/超时/非法 JSON 均通过适配器故障模式与进程内变异注入，未对宿主网络或供应商制造故障。

---

## 3. 真实调用报告（证据文件：`reports/phase3-verification.json`）

| 通道 | 供应商/模型 | 端点/协议 | 调用次数 | 结果 |
| :--- | :--- | :--- | :--- | :--- |
| Gemini Worker（历史状态） | Google / `gemini-3.8-flash` | `generativelanguage.googleapis.com/v1beta` | **0**（未发起） | 当时 **BLOCKED_MISSING_CREDENTIAL**。→ 2026-10-11 已由 ADR-0002 本地代理接通（`gemini-3.8-flash-high`），最新证据见 §7/§8 |
| Muse Reviewer | 用户本地 Muse 代理 / `muse-spark-1.3-contributor-free` | `http://127.0.0.1:8787/v1`（Responses 协议，沿用已批准配置，未切换协议/模型/通道） | **2** 次真实审核 | **已取证**：正确产物 `pass`（`rev_p3_muse_ok01`，2663 tokens，4.9s）；注入缺陷被 `revise` 且证据引用缺陷表达式（`rev_p3_muse_bad02`，3070 tokens，9.4s）。注入元数据（第 4 行 `a / b`→`a * b`、前后 SHA-256、非模型生成声明）见验证 JSON。首轮探测曾遇 HTTP 502，复认时恢复 200，属上游瞬时波动 |
| DeepSeek+Muse 补充联调 | DeepSeek `deepseek-flash`（worker）+ Muse（reviewer） | DeepSeek Chat Completions + Muse Responses | **完整闭环 1 次**（规划+生成+审核各 1，共 3 turns，6513 tokens） | **真实跑通至 succeeded**：产物 v1 `dbc8bf65…` 经控制层验收与 Muse `pass`（`rev_a9ce485b25fb`）。按裁决明确标注**不作为阶段 3 验收证据**，仅证明新调用链在真实模型上端到端可用 |

**用量与耗时说明**：以上均为真实供应商返回的用量（Muse 2663/3070 tokens；补充闭环 6513 tokens，其中含 DeepSeek 与 Muse 两侧）；无未知用量条目；耗时与哈希逐条记录在 `reports/phase3-verification.json`。

---

## 4. 阻塞项与架构师所需输入

1. ~~GEMINI_API_KEY 缺失~~ **已解除（2026-10-11）**：按用户裁决改经本地代理接入（ADR-0002），令牌已持久化为 `GEMINI_API_KEY` 用户环境变量；真实闭环证据见 §7/§8。
2. ~~Muse 本地代理上游 502~~ **已解除**：复认时代理恢复（HTTP 200），真实审核与补充闭环证据已取得；期间未对代理/协议/模型做任何改动。

## 5. 新发现的架构问题（登记，不擅自扩大范围）

1. **`ScriptedWorkerAdapter.propose_tool_requests` 的默认 read_file 行为**对"无工具生成任务"不透明：调用方必须显式传 `tool_requests_by_turn=[[]]` 才进入生成路径。建议阶段 4 引入任务级 `execution_mode`（generation/tool）字段显式声明，而非依赖 Worker 提议结果推断。
2. **审核绑定排序**曾依赖 `created_at`（秒级精度相同则顺序不定），已改为 `rowid` 单调序；若未来引入多线程写审核，需要显式序列号。
3. **MemoryRecord 的失败记忆**仅在 `run_task` 驱动到终态时写入；`reject_task_request`（人工拒绝）已显式补写，但直接通过存储层转移终态的路径不写记忆——如需全覆盖应在阶段 4 统一收口。

## 6. 提交与复现

- 分支：`phase3-real-dual-model-loop`（自 `28b1f46` 切出）；提交信息见 Git 日志。
- 复现入口：
  - 全量离线：`python -m unittest discover tests`
  - 变异守卫：`python scripts/verify_phase3_guards.py`（及三个既有守卫脚本）
  - 真实调用与阻塞登记：`python scripts/verify_phase3.py` → `reports/phase3-verification.json`
  - 产物/证据查询：`python -m src.cli --db <db> artifact <task_id>`

---

## 7. 补遗（2026-10-11 晚）：Gemini 通道接通与真实闭环取证

### 7.1 通道变更（用户裁决，ADR-0002）
用户直接下达 Gemini Worker 本地代理接入参数：`http://localhost:8787/v1`、
Anthropic Messages 协议（POST /v1/messages）、模型 `gemini-3.8-flash-high`、
上下文 1,000,000 / 最大输出 64,000（用户提供，未压测）、认证 `x-api-key` 读
`GEMINI_API_KEY`（本机代理令牌，已持久化到用户环境变量）。
官方 Google generateContent 端点配置退役（`GeminiAdapter` 实现保留未被引用）。

### 7.2 新增实现
| 文件 | 内容 |
| :--- | :--- |
| `src/anthropic_adapter.py` | `AnthropicMessagesAdapter`：system→顶层字段映射、max_tokens 必填映射控制层预算、stop_reason 截断/拒绝映射、usage 缺失记 None、HTTP 错误脱敏、回环绕过代理、无自动重试 |
| `src/model_config.py` | 新增 `anthropic_messages` 协议分支 |
| `configs/adapters.local.json` + `example` | `gemini_worker` 换用新协议与模型（同步更新） |
| `tests/test_anthropic_adapter.py` | 10 项离线测试（请求形状、头与版本、system 映射、截断/拒绝/空输出/非法 JSON fail-closed、未知用量 None、预算上限、错误脱敏、profile 接线） |
| `scripts/verify_anthropic_guards.py` | 9 项变异守卫全部检出 |
| `tests/test_phase2_control_layer.py::test_04` | 改为 patch 凭据探测实现宿主状态无关隔离（防止注册表凭据穿透离线路由到真实网络） |
| `scripts/verify_phase3.py` | 新增第 3 节：真实 Gemini+Muse 主闭环（`counts_as_acceptance_evidence: true`） |

### 7.3 真实闭环证据（`reports/phase3-verification.json` 第 3 节，验收证据）
- **主闭环**：真实 Gemini worker（`gemini-3.8-flash-high`，Anthropic Messages）
  + 真实 Muse reviewer（`muse-spark-1.3-contributor-free`，Responses），
  固定输入排序任务 → 规划/生成/审核各 1 次真实调用，共 3 turns、5768 tokens（已知），
  产物 v1 SHA-256 `96441364e9d6db604166fb9506e085aebd4fc5d5832736b5c277c7b0d0feb8b0`，
  控制层验收通过（产物内容与独立计算的期望输出一致），Muse 审核 `rev_acfdfa7e66b1` pass，
  任务终态 **succeeded**，零修复。
- **Muse 复认**（同轮）：正确产物 pass、注入缺陷 revise 且证据引用缺陷表达式（同前轮口径）。
- **DeepSeek+Muse 补充联调**：再次 succeeded（非验收证据）。
- 审查 minor 三项修复（`080a865`）与本轮接入变更（`8aa1024`）之后，
  全仓 **101 项测试**与 5 个守卫脚本（9/4/14/5/8 项变异）复跑全绿。

### 7.4 结论更新
阶段 3 的判定性证据（真实 Gemini 产物 + 真实 Muse 审核 + 控制层验收 + 持久化）
已取得。**验收结论不自我宣称**：提交独立审查，由架构师作出阶段结项裁决。

---

## 8. 补遗（2026-10-11 深夜）：结项整改三项完成（裁决基线 a279b8f）

按架构师整改指令（阶段 3 结项暂缓），以 `a279b8f` 为基线完成以下整改与补证：

### 8.1 整改一：取证成功判定修复（提交 `f54ac16`）
- 新增 `src/control_guards.py::verify_closed_loop_success`：仅当 **succeeded + 当前 pass 审核 + 审核覆盖当前产物哈希 + 验收证据完整 + 无未知用量/未确认副作用** 才判定正常闭环通过；`failed`/`needs_attention`/`revise`/缺失绑定一律拒绝。
- `scripts/verify_phase3.py` 主闭环与补充闭环均接入门禁；汇总结论新增 `overall_required_checks`（离线测试 + Muse + 主闭环 + 修复链），任一未过 **退出码非零**。
- 离线反例测试 9 项（`TestClosedLoopSuccessGate`，纯内存不访问网络）；变异守卫扩展至 **8/8 全检出**（`verify_phase3_guards.py`）。
- **实测故障场景被正确拒绝**：代理限流（HTTP 429）期间两次运行，脚本均如实报 `passed: false` 并返回退出码 1（`reports/phase3-repair-fault-429.json`，独立保留）。

### 8.2 整改二：Anthropic 适配器用量校验（提交 `f54ac16`）
- `input_tokens`/`output_tokens` 必须各自为**非布尔、非负整数**才允许求和；布尔 `true+true=2`、负数 `-10+20=10` 不再产生可信总数。
- 缺失/部分缺失 → 全部记 `None`（未知），**不通过备用 `total_tokens` 字段掩盖**；存在但非法 → `AdapterError`（按既有非法用量规则受控停止）。
- 用量矩阵测试 12 例（布尔/负数/字符串/浮点/缺失/部分缺失/合法零值/仅 total_tokens）+ 变异守卫扩展至 **10/10 全检出**（`verify_anthropic_guards.py`）。

### 8.3 整改三：真实修复闭环取证（`reports/phase3-repair-verification.json`，验收证据）
`scripts/verify_phase3_repair.py`，任务 `task_p3_repair_loop1`，全链真实调用：
1. 真实 Gemini（`gemini-3.8-flash-high`）生成排序产物（SHA-256 `96441364…`，与主闭环产物一致）；
2. **测试夹具注入缺陷**（交换前两条记录破坏升序；`injected_by` 明确标注非模型生成；原始/注入内容与哈希全量保留）→ v1 SHA `c9bb1471…`；
3. 真实 Muse 审核出具 **revise**，p1_critical 证据直接引用注入后的错误产物内容；
4. 真实 Gemini 按修复上下文（Reviewer 问题+当前产物+验收失败证据）重新生成 → v2 SHA `e9870454…`（内容已正确排序，`gemini_repair_changed_the_artifact: true`）；
5. 控制层重新验收通过 → 真实 Muse 复审 **pass（100 分）** → 任务终态 **succeeded**，repair_count=1（原预算与 2 轮上限内，未另建任务）。

关联完整性：2 个产物版本、2 条审核绑定、5 次 attempt（worker 1315/778/1116、reviewer 4164/6023 tokens，全部已知）逐条关联；**v1/v2 完整内容**、审核 issues 原文、逐 attempt 用量均在报告中，可离线复查（SQLite：`data/phase3-repair-loop.sqlite3`，CLI 查询入口 `python -m src.cli --db data/phase3-repair-loop.sqlite3 artifact task_p3_repair_loop1`）。

### 8.4 最终验证状态（提交 `f54ac16` + 证据提交后复验）
- 离线：**110/110 测试**通过（新增门禁反例 9 项 + 用量矩阵 12 例）；守卫 8/8、10/10、14、5、8 全检出。
- 真实：主闭环 succeeded（严格门禁）、修复链 succeeded（revise→修复→pass→succeeded）、Muse 复认通过；全量 `verify_phase3.py` **退出码 0**，`overall_required_checks` 四项全 true。
- 期间代理限流（HTTP 429）引发的两次失败运行被脚本如实拒绝并单独留证，未混入成功证据。
