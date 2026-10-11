# JH-Agent OS 阶段 3：真实双模型闭环 实现交付报告

- **日期**：2026-10-11（Asia/Shanghai）
- **开发基线**：`28b1f4611a2c4f1eda57e92cf87a95ca626dcba2`（阶段 2 收口提交）
- **工作分支**：`phase3-real-dual-model-loop`
- **结论（负面优先）**：**阶段 3 实现已交付；真实 Muse 审核证据与 DeepSeek+Muse 补充闭环证据已取得；真实 Gemini+Muse 闭环验收仍未完成，不宣称通过。**
  Gemini 缺 `GEMINI_API_KEY`（blocked，未发起任何调用）。Muse 本地代理首轮验证时返回 HTTP 502
  （上游瞬时波动），复认时已恢复（HTTP 200），随后完成两项真实审核与一次真实补充闭环；
  协议/模型/审核通道全程未改动。依据任务书第八节，阶段 3 验收以 Gemini 通道恢复为前提，
  当前只交付实现、真实 Muse 证据与阻塞清单。

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
- **单元测试**：`python -m unittest discover tests` → **90 项全部通过**（1 项跳过为阶段 4 junction 预留）。其中新增 `tests/test_phase3_loop.py` 12 项：
  - **A 正常流程**（4 项）：生成→可信验收→独立审核→succeeded；产物 v1 + 控制层 SHA-256 + 审核绑定关联；控制验收不采信 Worker/Reviewer 意见（产物错误时即使 review pass 也 needs_attention）。
  - **B 错误发现与修复**（2 项）：夹具注入错误产物（注入内容与前后哈希在测试与断言中显式记录，绝不归属为模型自然生成）→ Muse-mock 出 revise 带证据 → 修复上下文含 Reviewer 问题+当前产物哈希 → v2 重新验收+重新审核（新旧 review_id/attempt_id 不同）→ succeeded；修复上限 2 轮耗尽→failed，负结果记忆保留证据。
  - **C 失败路径**（4 项）：生成阶段 timeout/invalid_json/truncated/refused 全部 fail-closed 且无产物落库；产物 task_id 关联不符被拒；产物 Schema 不符被拒；未知用量 fail-closed 到 needs_attention（生命周期类失败路径复用阶段 2 OPT 套件，未重复实现）。
    **失败语义分野（审查 minor #2 补释）**：生成调用链失败（超时/截断/拒绝/非法 JSON/产物关联或 Schema 不符）进 `failed` 终态；预算、时限与用量不确定性（无法安全继续或重放）进 `needs_attention`——与阶段 2 OPT 套件的生命周期分工一致，`tests/test_phase3_loop.py` 断言与之一致。
  - **D 持久化**（2 项）：文件库重启后查询终态/产物/审核绑定/记忆记录，模型调用次数为零；产物仅存 `mem://` 路径与 SQLite，绝无真实文件写入。
- **变异守卫**：`python scripts/verify_phase3_guards.py` → **4/4 全部检出**（产物关联校验跳过、验收证据引用篡改、修复上下文降级、泛化经验误标 verified）。
- **既有守卫复用**：`verify_phase2_hardening.py`（14 变异检出）、`verify_phase2_guards.py`（5/5）、`verify_responses_guards.py`（8/8）在本分支复跑全部通过。

### 2.2 真实调用（is_mock=false）——Muse 与补充闭环已取证，Gemini 仍阻塞（详见 §3）
- Muse 复认两项真实审核通过（正确产物 pass；注入缺陷被 revise 且证据引用缺陷表达式）；
  DeepSeek（worker）+ Muse（reviewer）补充闭环真实跑通至 succeeded。
- 真实 Gemini+Muse 闭环（场景 A/B 的验收路径）因缺少凭据未执行。
### 2.3 故障注入（is_mock=false 的故障模式，不破坏宿主网络）
- 截断/拒绝/超时/非法 JSON 均通过适配器故障模式与进程内变异注入，未对宿主网络或供应商制造故障。

---

## 3. 真实调用报告（证据文件：`reports/phase3-verification.json`）

| 通道 | 供应商/模型 | 端点/协议 | 调用次数 | 结果 |
| :--- | :--- | :--- | :--- | :--- |
| Gemini Worker | Google / `gemini-3.8-flash`（型号经官方文档核实修正） | `generativelanguage.googleapis.com/v1beta` | **0**（未发起） | **BLOCKED_MISSING_CREDENTIAL**：`GEMINI_API_KEY`/`GOOGLE_API_KEY` 不存在。执行 AI 自身会话模型接入与 JH Runtime 的 API 接入相互独立，不得以会话凭据替代 |
| Muse Reviewer | 用户本地 Muse 代理 / `muse-spark-1.3-contributor-free` | `http://127.0.0.1:8787/v1`（Responses 协议，沿用已批准配置，未切换协议/模型/通道） | **2** 次真实审核 | **已取证**：正确产物 `pass`（`rev_p3_muse_ok01`，2663 tokens，4.9s）；注入缺陷被 `revise` 且证据引用缺陷表达式（`rev_p3_muse_bad02`，3070 tokens，9.4s）。注入元数据（第 4 行 `a / b`→`a * b`、前后 SHA-256、非模型生成声明）见验证 JSON。首轮探测曾遇 HTTP 502，复认时恢复 200，属上游瞬时波动 |
| DeepSeek+Muse 补充联调 | DeepSeek `deepseek-flash`（worker）+ Muse（reviewer） | DeepSeek Chat Completions + Muse Responses | **完整闭环 1 次**（规划+生成+审核各 1，共 3 turns，6513 tokens） | **真实跑通至 succeeded**：产物 v1 `dbc8bf65…` 经控制层验收与 Muse `pass`（`rev_a9ce485b25fb`）。按裁决明确标注**不作为阶段 3 验收证据**，仅证明新调用链在真实模型上端到端可用 |

**用量与耗时说明**：以上均为真实供应商返回的用量（Muse 2663/3070 tokens；补充闭环 6513 tokens，其中含 DeepSeek 与 Muse 两侧）；无未知用量条目；耗时与哈希逐条记录在 `reports/phase3-verification.json`。

---

## 4. 阻塞项与架构师所需输入

1. **GEMINI_API_KEY 缺失**（阻塞 Gemini 真实 Worker 通道，唯一剩余阻塞项）：请在运行进程的用户环境变量配置密钥后，重跑 `python scripts/verify_phase3.py` 即可自动补测场景 A/B 的真实闭环，随后方可申请阶段 3 验收裁决。
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
