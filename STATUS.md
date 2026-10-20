# 当前方向与续接 · 2026-10-18

**用户已确认：模块化统一 GUI + 可替换 Agent 协作中间站。** 方向文档已更新；JH-018、JH-019 已按各自范围验收；`1288d8a` 已按「最小 Codex 隔离只读会话派发／收集及登记证据」范围验收（R1/R2/R3 关闭）。Hark 验证器补丁已完成 Windows 本地移植与验证（独立批，待复核）。
权威方向：[产品需求 v1.1](docs/product-requirements-v1.1.md)、[模块化架构](docs/modular-architecture.md)、[ADR-0005](docs/adr/ADR-0005-modular-gui-agent-hub.md)、[路线 v3.0](ROADMAP.md)。

- GUI 为目标用户入口，Codex 保留默认主控/架构审查；Agent 应用、会话、模型、角色分离，支持跨应用和同应用不同会话的设计方向。ZCode 为首个候选；**真实执行应用本轮选用 Codex**（`codex app-server` 实测可用，见下）。
- GUI 与统一应用接口已按「最小模块化 GUI＋只读查询＋草案」范围验收（JH-019）；**产品级 AgentSessionAdapter 已落地首个真实实现**（`src/agent_session_codex.py`），但自动审查流转与 GUI 执行入口仍未接入。
- 已验收 N1、JH-005 和 JH-006 原成果保留。架构师已批准 c6c0af57 host-entry 接线批；d650bd3 仅补真实输入盘点；JH-018 于 d9cf37de 基线完成接口探测与契约设计，JH-019 于 6052cf31 基线验收。
- 原运行证据：389 项 388 过/1 既有跳过，33 变异 32 检出/1 冗余/0 异常，来自既有提交前后日志。Hark 验证器移植后全量 **590 项 589 过/1 既有跳过**（AST 独立计数 590）。旧 JH-006/Phase4/采集验证器成绩仍为**运行记录**；Hark 补丁已本地移植，其**诊断结论**见[移植报告](reports/hark-verifier-port-20261018.md)。
- 真实来源登记缺口仍在；N2/P1 未完成。production complete、FileToolExecutor 关闭，原树 80979f3 失败 WIP/STOP 和旧否决候选保留。
- 实现工作树仍是 D:/JH-Agent OS/data/worktrees/n1-artifact-entry，分支 n1-artifact-entry。原树仅保留 WIP 和方向跳转，不用于新验收。
- JH-020 真实会话切片：真实派发/收集已实测通过；**指定审查会话未发送**（只读核查见下）；**真实协作闭环仍未通过**。

---

## 2026-10-19 · Hark 历史一致性限定整改的 Windows 集成（候选，待独立验收）

按独立复核 [Hark-派单-04 限定整改复核](reports/hark-history-rectification-independent-review-20261019.md)，从固定候选 `e5f786e` 精准移植六文件：新增 `src/run_history_consistency.py`、`tests/test_jh020_history_consistency.py`、`scripts/verify_hark_history_consistency_guards.py`、`tests/test_jh020_run_history_guard_classification.py`，更新 `src/app_api.py`、`scripts/verify_jh020_run_history_guards.py`（后者与分类回归为已授权偏离，单独提交）。

- 探针：stage 77 项 0 异常；seed 两项正常保留、四项反例 corrupt 且源字节不变；分类矩阵 1458 项全部符合预期。
- 定向测试 72 项（新历史 63 + 分类回归 9）通过；新守卫 24/24 断言检出；旧守卫 8 检出 + 1 冗余（双层关闭只作隔离诊断，不计第九次检出）。
- 全量 **704 项 703 过/1 既有跳过**（AST 独立计数 704 = 632 + 63 + 9）；JH019 前端 4/4、JH020 前端 13/13、前端变异 2 检出 + 2 冗余。
- 真实旧历史只读：`data/jh020-r2-final-roundtrip-20261018/runs.jsonl`（源 `5b1dc26`）仍 `ok`/`count=1`/`evidence=verified`/`live=false`，登记与原始证据字节未变；未调用模型、未发送会话。
- 矛盾历史整份 `corrupt`（`runs=[]`、`count=0`、详情 `run=None`），拒绝发生在证据读取与任务关联之前；本批只加固查询路径，不声称恢复和派发已加固。**Windows 集成候选，待架构师独立验收**；production complete 与自动文件应用继续关闭。

---

## 2026-10-17 · JH-018 应用/会话能力探测与适配契约（接口层完成，产品未实现）

按明确指令派发并完成：实测 ZCode 0.16.9 与 Codex CLI 0.160.1 的会话接口。**ZCode**：`zcode app-server --stdio`（ZCode Protocol，换行 JSON、双向）+ 本地 CLI，实测 `session/create|send|read|messages|usage|events|setModel|stop`，派发-收集-取消跑通；限制是默认模型已弃用须显式 `setModel`（含 reasoningLevel），且客户端必须应答服务端反向请求（如 `session/requestRuntimePreferences`）。**Codex**：JSON-RPC app-server（187 方法，随包可生成 schema）+ `codex exec`/`exec resume`，实测对指定 thread `thread/resume`→`turn/start` 完成一轮并收到 `turn/completed` 与用量。

产出：契约 `src/agent_session.py`（六能力、运行态无 `succeeded`、不支持显式抛错、无原生差异、无生产模块导入）；一致性夹具 `tests/test_jh018_agent_session_contract.py`；探测脚本 `scripts/probe_zcode_capabilities.py`、`scripts/probe_codex_sessions.py`；报告 [JH-018](reports/jh018-agent-session-probe-20261017.md)。

**整改 R1—R4（2026-10-17，待审）**：R1 两个探测脚本收紧判据——必需步骤出现协议错误/超时/未接受/错会话/错轮次/失败终态即失败并退出非零，Codex 完成通知须匹配本 thread 与本 turn 且检查最终 status/error，ZCode 不以"有 result 或 idle"判完成，枚举模式与往返判据分开，并各加离线假传输正反控（ZCode 15 例、Codex 13 例，含"归属不明即拒发"）；未知服务端请求改回错误并计失败，不再默认成功应答；Codex 探测发送前做实验会话归属检查（`cwd` + preview 标记，仅传 thread ID 或覆盖 cwd 不算）。R2 契约幂等索引与 run_id 索引分开、跨地址查询抛 `AddressMismatch`、同幂等键配不同请求抛 `IdempotencyConflict`，绑定应用实例/会话/任务/轮次。R3 未确认取消只表 `CANCEL_REQUESTED`，`CANCELLED` 需原生确认；区分逻辑任务与原生轮次，整改保留历史/累计预算/授权范围；用两个**不同类** mock 适配器验证同一调用点。R4 文档区分"已结束会话恢复／活跃轮次接续／实际审查／完整闭环"，无证据项改写为"未发现/未知"，保留并解释 `exec1.exit=1` 与成功回复并存的原始事实。

**收口 R1–R3（2026-10-17，待审）**：① ZCode 生命周期——完成判定不再以"不属于错误状态"代替成功终态；必须落到 `idle/completed`，**超时仍 running/queued/paused/waiting 一律失败**，失败终态亦失败，调度路径须先观察到 active；取消试验沿用停止前已观察到的 active 事实，**停止后立即 idle 即通过**；新增"持续运行直到超时"负例、正常取消正例、停止请求被拒负例，且每个反例**断言具体失败原因**（ZCode selftest 19 例）。② 幂等指纹改为 SHA-256 over 规范化 JSON，覆盖地址/任务/attempt/工作区/授权范围/预算/指令/**产物引用与版本哈希**，消除分隔符碰撞；`submit` 保存请求快照，调用方事后改可变内容不影响已登记事实（新增反例锁定）。③ 整改接续：同 run+同幂等键+同请求只追加一个原生轮次，同键不同请求冲突，返回值与随后 `status` 一致，**已确认取消的运行拒绝整改**（`RemediationRefused`）；两个不同类 mock 都跑这些行为测试。④ 证据口径：`exec1.exit=1` 与成功回复并存降级为"执行方说明，**未独立证实**"（本机未找到该失败的原始终端/命令记录，stderr 已被覆盖），可直接核实的事实与陈述分开列。

**收口 B（2026-10-17，待审）**：① 异步启动——等待改为两段有界（先等执行开始，再等终态）；`accepted` 后先出现 `idle` **不再判失败**，保留"从未启动/超时仍活跃/失败终态"三条拒绝判据，取消路径仍允许停止后立即 idle。② 整改重放——幂等记录改存"指纹 + 该请求对应的原生轮次"；交错 A→B→重放 A 返回 **A 自己的操作轮次**，`RunStatus` 区分 `native_turn`（本操作）与 `latest_turn`（运行当前），`state` 始终是当前状态、不因返回历史轮次而倒退。③ 取消终态——确认取消后重复取消保持 `CANCELLED`，不追加轮次、不回退为请求中；已取消拒绝整改保留。另修取消前的异步启动等待：`_await_active` 首次看到 `idle` 不再立即结束，在 `active_wait` 内继续观察到真正 active、明确失败或期限耗尽；补完整取消路径正例（send accepted → 首查 idle → running → stop → 立即 idle），实测确等到了 running。两个适配器均补交错重放、重放不倒退、重复取消回归，并新增组合序列自检（派发→A→B→重放 A→查询→取消→重复取消→确认→再取消→拒绝整改→收集）。

**验收（2026-10-17）**：架构师已按「接口探测与契约设计」范围验收 JH-018（基线 `dc162bed`）。以下为历史待审表述，保留以便对照。

**仅接口能力层**：未实现 GUI、统一应用接口或产品闭环；`codex queue`、`review/start`、`turn/interrupt`、`turn/steer` 未实跑（daemon 未起或未调用）；未向用户在用会话写入；mock 适配器不算第二个应用接入。全量 **422 项 421 通过 / 1 既有跳过**。本轮不调用真实模型。`production complete`、`FileToolExecutor` 继续关闭；N2/P1 未完成。

## 2026-10-17 · JH-019 最小模块化 GUI 与统一应用接口（已验收；基线 6052cf31）

> 下文中 R1—R4 / 二次整改段落标注的「待审」为**当时**状态，验收后不再适用；保留以便对照。

按明确指令派发并实现：本机可打开的最小 GUI（工作台 / 任务详情 / Agent·会话 / 审查与交付），并在真实浏览器中实际打开验证。分层为**页面模块 / 公共组件 / 数据访问层**，业务操作全部经统一应用接口 `src/app_api.py`；传输层 `src/gui_http.py` 仅用标准库暴露 JSON 并服务静态资源。**复用**既有 `build_task_summary` 与 `TaskStorage` 只读查询，不复制状态机与审核规则；存储只新增只读 `list_tasks()`。

目标输入保存为**显式标记草案**（`draft=true`、`execution=not_triggered`），草案不建任务、不触发任何执行；带执行意图的草案请求被拒（409）。会话登记全部 `connected=false`/`simulated=true` 并附 JH-018 实测证据；数据库缺失、未知任务、空数据、损坏草案文件各有显式状态，不展示虚假连接或完成。**不支持**的能力（`dispatch`/`collect`/`review_trigger`）显式列为未接入（属 JH-020）。

产出：`src/app_api.py`、`src/gui_http.py`、`gui/`（5 文件）、`src/storage.py` 加法、5 个测试文件与 1 个前端行为测试台、[报告](reports/jh019-minimal-gui-20261017.md)。

**整改 R1—R4（2026-10-17，待审）**：① 草案存储——进程级锁跨越整个读取→追加→发布周期，`mkstemp` 唯一临时文件 + 原子替换，发布失败保留旧字节；顶层/条目结构错误报 `drafts_malformed` 并保留原字节，不再当空列表；单进程写入边界写明（不建复杂存储平台）。② 前端请求归属——视图/任务切换自增代次，迟到响应不覆盖当前页面；草案列表独立代次防止旧空列表回写或重复追加；用 DOM 桩 + 可控 Promise 顺序的**行为测试**验证三场景。③ 服务边界——只允许环回绑定，非环回参数在创建 socket 前拒绝，补允许/拒绝两类测试。④ 证据——整改后真机复验四视图与草案保存刷新；截图与 DOM 记录归档到忽略目录 `data/jh019-gui/evidence/`；演示库明确标注为**演示数据、非真实模型任务**。
**二次整改（2026-10-17，待审）**：修跨面板干扰——草案代次改为**归属具体面板**（原为模块级，失效面板的回调会推进代次、使当前面板在途请求被判过期而丢列表）；新增行为场景 `save_pending_across_view_switch` 复现该序列并断言当前列表显示草案且不重复，原 3 场景保留，并用基线版本反跑确认该场景能捕获缺陷。证据重采为 `jh019-draft-after-save-v2.png/.txt`（单帧视口、反馈不再重复卡片字段），旧证据保留并说明替换原因。累计**未复现**的并发 500 口径改为"根因未确认、有界重试属预防性加固"。全量 **492 项 491 通过 / 1 既有跳过**（JH-019 共 66 项）。本轮不调用真实模型、不触发工具；`production complete` 与 `FileToolExecutor` 继续关闭；未自宣 N2/P1 完成。

---

## 2026-10-17 · JH-020 首批（C-2/C-3）控制层加固（实现完成、离线验证通过，**待独立验收**）

按明确指令派发本批的一个真实工具任务，并在并行分工下承担其中两项控制层加固（`V-1/V-2 验证器修复`交 Hark，本批**未改验证器**）：

- **C-3**：`complete` 现从**同一份封存 `checks.json`** 取出 `expect_stdout_contains`，校验**已登记原始输出**是否满足该封存断言；不满足即 `needs_attention`。修复前只比对回执自述摘要，`case 0: FAIL` 的输出也能 `succeeded`（Hark R2 探针实测）。控制层**不重跑宿主命令**。
- **C-2**：任务带 `waiting_host` 交付而因取消/超时/不确定离开 `reviewing` 时，在同一事务内**持久**写入 `has_unconfirmed_side_effect` 与交付字段，并记 `host_effect_unconfirmed` 事件。~~新增 `reconcile-delivery` 对账入口……并清标记~~ → **2026-10-18 整改（见下）**：入口收缩为**仅记录观察**，**不清除**标记。

改动仅**加法**（复用既有列、无 Schema 迁移）；新增 19 项正反控、1 个定向变异验证器（5/5 检出）；全量 **511 项 510 过/1 既有跳过**。见[报告](reports/jh020-first-batch-20261017.md)。

**真实路径未通过**：指定 Codex 架构师会话 `01a14a25-…` 经**只读**核对（存在、`cwd=D:\JH-Agent OS`、末条 `task_complete`），**未发送任何内容**；真实派发与指定会话审查不在本切片内 ⇒ **本批 JH-020 闭环未通过**，交当前 Codex 架构师独立验收。不以 Mock/人工复制/另建会话补成成功。`production complete`、`FileToolExecutor` 保持关闭。

## 2026-10-18 · JH-020 首批 5f604fba 限定整改 R1–R3（离线验证通过，**待独立验收**）

按独立反例 `data/jh020-review-20261018/` 对 `5f604fba` 做**限定整改**，**未重做已通过的 C-3**，**未改 Hark 负责的脚本**：

- **R1**：`reconcile-delivery` 收缩为**仅记录观察**（`host_effect_observed`、`_delivery.reconciled` + 追加式 `observations`），**不再清除**未确认标记；`unreadable/diverged` 保持未解决且**允许再次观察**；**不抢 owner、不覆盖其他 in_flight 副作用**。真正解除标记留待明确、绑定来源的处置条件，不扩建宿主 Runtime。已删除上一批的 `set_unconfirmed_side_effect`（避免死代码），并更正“`diverged` 也清标记”等错误预期与对应变异。
- **R2**：新增**只读** `list_unresolved_host_effects()` 与 CLI `pending-reconciliation`；`recover` 追加终态未解决任务的**只读**行（`reason=host_side_effect_unresolved`、`auto_replay_allowed=false`、保持原终态）；补关闭重开数据库后的**可发现性**测试；枚举**不清标记、不抢 owner、不改状态**。
- **R3**：采集守卫按原始日志更正为 **33 项 / 32 检出 / 1 冗余 / 0 异常**；旧 JH-006/Phase4/采集验证器成绩**一律标注为“运行记录”**，有效性待 Hark 修复与独立复核。

全量 **520 项 519 过/1 既有跳过**；本批定向变异 **7/7 检出**；独立反例副本复跑五例均保持未解决且可发现、owner 与在途副作用未被改动。见[整改报告](reports/jh020-rectification-20261018.md)、证据 `data/jh020-rectify-20261018/`。
**真实协作闭环仍未完成**（本切片不含真实派发与指定会话审查）。`production complete`、`FileToolExecutor` 保持关闭。

---

## 2026-10-18 · JH-020 真实会话接入切片（首个真实 AgentSessionAdapter + 一次真实往返；**候选，待独立验收**）

> **状态更新（同日整改）**：`14e42a0` 经独立审查未通过，已按 R1–R3 整改（见下一节）；本节保留为历史记录，其 §`_assistant_texts/_token_usage` 行为与登记时机已被取代。当前**候选/待独立验收**。

**本切片**：实现契约的首个**真实适配器** `src/agent_session_codex.py`（Codex app-server JSON-RPC over stdio），跑通**一次**隔离、只读、固定标识的真实派发/收集往返。选型依据：`codex` CLI 0.160.1 在本机 PATH、`auth.json` 存在、`app-server --stdio` 握手与 `thread/start`+`turn/start`+`turn/completed` 实跑成功；ZCode 非 PATH 命令且需第二个实例、默认模型已弃用，故本轮不用（可用性更脆）。

- **契约接入**：submit=`thread/start`（隔离 cwd、`sandbox="read-only"`、approvals never）+`turn/start`（`readOnly`、无网络）；登记 `RunBinding`/请求快照/**SHA-256 指纹**/原生 thread+turn；幂等索引与 run 索引分开；`collect` **有界等待**匹配本 thread+turn 的完成通知、**不自动重试**；**无 succeeded 状态**，不确认项进 `unverified`。
- **能力诚实**：`continue`/`cancel` 声明**不支持**（`turn/steer`/`turn/interrupt` 未实测），不支持即抛 `UnsupportedCapability`，绝不模拟。
- **真实往返**（`data/jh020-codex-session-20261018/`）：随机 token 回显；原生 thread `01a14d62-95e0-…` / turn `01a14d62-9c47-…`；`turn/completed` status `completed`、error `null`；工作区运行前后均为空（**无文件写入**）；未实现服务端请求 `[]`。
- **指定审查会话**：只做**只读**核查（`thread/read` 成功、`cwd=D:\JH-Agent OS`、名称匹配本架构师角色）；本 app-server 报 `notLoaded` **不能**证明桌面宿主空闲，且其 rollout 于核查前约 52 分钟仍活动、末条为 `turn_aborted` ⇒ **裁决不发送**，未新建替代会话、未把 CLI 会话当作指定会话。
- **验证**：新增契约测试 **19 项**、守卫变异 **5/5 检出**；全量 **540 项 539 过/1 既有跳过**。JH-018 守卫最小调整：核心仍禁止引用契约，**真实适配器**可引用，并新增“白名单项必须实际继承 `AgentSessionAdapter`”强制校验（不修改契约模块）。
- **边界**：GUI 执行入口与有界整改（continue/cancel）留待该路径复核后推进；`production complete`、`FileToolExecutor` 关闭；**交付/送达 ≠ 验收成功，本切片不代表真实协作闭环完成**。见[报告](reports/jh020-real-session-20261018.md)。

## 2026-10-18 · JH-020 真实会话切片限定整改 R1–R3（离线+真实只读往返通过；**候选，待独立验收**）

按独立审查 `data/jh020-real-session-review-20261018/` 对 `14e42a0` 做**限定整改**，只改三项（未接 GUI、未实现 continue/cancel、未发送指定审查会话、未新建替代架构师）：

- **R1 事实绑定 native thread＋turn**：`item/completed` 与 `thread/tokenUsage/updated` 必须 `threadId` **且** `turnId` 精确匹配本轮，否则**拒收并计数**；用量拆 `{turn, thread_total, model_context_window, source}`，无绑定即记 **“usage not proven”**。夹具改为携带真实关联字段。
- **R2 派发前登记与不重复派发**：外部调用前落 `reserved`（含**规范化快照**），写盘失败即 **拒绝派发**；超时保留 `state=UNKNOWN` 与已知原生 ID、落 `dispatch_uncertain` 并返回该 run；同键重放**绝不重发**，**重开登记文件**后仍阻止不确定键重发；原始通知流写入 `raw/<run_id>.notifications.jsonl` 并登记**路径 + sha256 + 帧数**。
- **R3 地址与显式约束**：派发前校验 `app_kind`/实例/会话——**只支持新建隔离会话**，既有/指定地址拒绝且不偷偷新建 thread；**显式模型下发并与服务器回报核验**；**有限预算拒绝**。
- **反例对照**（`data/jh020-session-rectify-20261018/`）：R1 旧轮/无 thread 由“被采用”转“拒收”；R2 `timeout_replay` 的 `turn_start_calls` 2→1；R3 两例由“接受并调用”转 `UnsupportedAddress`、0 次调用。
- **验证**：全量 **559 项 558 过/1 既有跳过**；定向变异 **11/11 检出**；整改后真实只读往返通过（显式模型核验、thread/turn 关联、无文件写入），提交后以**新隔离证据目录**再复验一次并保留旧记录。见[整改报告](reports/jh020-session-rectification-20261018.md)。
- **仍为候选**：未验收；未宣布真实协作闭环通过；`production complete`、`FileToolExecutor`、现有成功门关闭。

## 2026-10-18 · JH-020 真实会话切片 R2 限定整改（R2-A/R2-B；**候选，待独立验收**）

按独立审查 `data/jh020-session-r2-review-20261018/` 修 **R2 两项**（R1/R3 已关闭，未重做）：

- **R2-A**：区分“文件确实不存在（首次建立）”与“存在但不可读/损坏”。后者**在任何外部调用与登记写入前拒发**（`RegistryUnavailable`），保留原字节与原因，**不跳过/清空/修复/截断**；`submit` 前置检查在外部调用之前。反例由“再派发 3 次并改写字节”变为 **0 次调用、字节不变**。
- **R2-B**：证据摘要改为**绑定实际写入字节**（二进制写入 + 对同一负载计算），新增 `verify_raw_evidence` 并让往返脚本**实际核对引用文件的字节哈希与帧数**。旧证据摘要语义为换行规范化文本（declared `48b186db…` ≠ disk `2281a545…`），**原样保留、不改写**；新证据写入新隔离目录。
- **验证**：全量 **569 项 568 过/1 既有跳过**；定向变异 **15/15 检出**；整改后真实只读往返（新目录）字节哈希与帧数自检通过。见[整改报告](reports/jh020-r2-rectification-20261018.md)。**仍为候选/待独立验收**，未宣布协作闭环通过。

## 2026-10-18 · JH-020 真实会话运行记录的统一查询与 GUI 只读展示（本切片完成，**待独立复核**）

- **复用**已验收代码（登记读取/字段校验/证据字节核对/只读任务查询），未复制状态机。
- **接口**：`list_agent_runs()` / `agent_run_detail(run_id)`；**登记来源由服务端配置**（`--runs`），浏览器不能传路径（`browser_supplied=false`）。
- **状态**：`absent`/`empty`/`ok`/`unreadable`/`corrupt` 四态分明，**不跳过坏行、不修复、不改写原文件**；**证据** `verified`/`missing`/`mismatch`/`unverifiable`（实读磁盘字节）；`run_id` 绝不当路径。
- **展示**：应用/实例/模型/角色/run/原生 thread+turns/最后记录时间/记录状态/**本轮与线程累计用量**/证据/未验证事项/事件/关联任务；**`live=false`**、**`declared_complete` 非任务验收成功**、未关联时“不创建任务记录”。
- **验证**：API 17、HTTP 7、前端行为 7 场景、守卫变异 4/4；JH-019 前端四场景仍全绿；全量 **618 项 617 过/1 既有跳过**（AST 独立计数 618）；**实机浏览器**渲染真实运行记录通过。见[报告](reports/jh020-run-history-gui-20261018.md)。
- **边界**：不接 GUI 派发/collect/continue/cancel、不发送指定审查会话、不改成功门、不启用 `production complete`/`FileToolExecutor`、不改 Hark 验证器、不推送/不合并 PR；**JH-020 整体、N2/P1 仍未完成**。

## 2026-10-18 · Hark 验证器补丁的 Windows 本地移植与验证（独立批，**待独立复核**）

来源 `fe0476d5807d6d562a1c290fe1b14623e53d1a46`。**仅精准移植** `scripts/verify_phase4_guards.py`、`scripts/verify_jh006_guards.py`、`tests/test_verifier_mutation_harness.py`（sha256 与候选逐字节一致）；**未**整分支合并、**未**覆盖旧 main 快照。

- **MUTATIONS AST 核对**：移植前本地 / 候选 / 移植后本地**三者一致**（phase4 `3cc35fe6…`、jh006 `f6a6ab4c…`），**未删改任何变异**；锚点对本地源码全部唯一（12 / 19 条）。
- **Windows 结果**：`verify_phase4_guards` → 12 条：**5 detected / 3 survived / 3 message_only / 1 undetermined（exit 1）**；`verify_jh006_guards` → 19 条：**18 detected / 1 undetermined（`prepared_version_field`）（exit 1）**；两者**基线/恒等全绿、无 harness 错误、无平台阻塞**。`exit 1` 是诊断缺口，**非运行失败**，**未改规则凑全绿**。
- **元变异**：恢复旧无条件 self 规则 / 关闭 `text_derived` 两个定向反事实（仅内存注入）→ **6/6 由未检出变 detected**（规则确实承担行为权重）。
- **套件**：全量 **590 项 589 过/1 既有跳过**；**AST 独立计数 590**（grep 618 含字符串常量里的伪计数）。见[报告](reports/hark-verifier-port-20261018.md)。
- **不恢复**旧 19/19、12/12 验收结论，**不宣称多层防护通过**；未改业务源码/夹具/权限/生产门；未调真实模型、未发送指定审查会话、未推送云端、未合并 PR #1。

## 2026-10-18 · JH-020 R2-A 登记记录字段校验（**候选，待独立验收**）

按独立审查 `data/jh020-r2-final-review-20261018/` 仅补字段校验（R1/R3/R2-B 已关闭，未重做）：

- **缺陷**：`parse_registry_text` 只要求 `run_id`/`event` 键名存在 ⇒ 把 unknown 请求的 `run_id` 置 `null` 或删除 `idempotency_key`（**JSON 仍可解析**）后，重开同键提交会**新建 run 并再次派发**并追加字节。
- **整改**：新增 `validate_registry_record`，在建索引前逐条校验：`run_id` **非空字符串**；**`idempotency_key` 字段必须存在**（合法 `null` 与字段缺失必须区分）；绑定/幂等/恢复字段按实际写入格式校验类型；`collected` 记录另校验载荷类型。非法 ⇒ **整体不可用、派发前拒绝、零调用、零改写**；不跳过/不默认/不修复/不迁移/不截断。
- **反例对照**：两例由“又一次派发、追加字节”变为 **`RegistryUnavailable`、0 调用、字节不变**；`healthy` 正控不变。计数口径：`initialize`/`thread/start`/`turn/start` 是三次协议调用＝**一次重新派发**。
- **验证**：全量 **577 项 576 过/1 既有跳过**（独立计数 577）；定向变异 **17/17 检出**；本批未再调真实模型，真实往返沿用 `5b1dc26` 证据。见[整改报告](reports/jh020-r2-record-shape-20261018.md)。**候选/待独立验收**。

---

以下是按时间保留的历史快照。其中“当前/下一批/待审查”以各记录日期为准，不覆盖上面的现行状态。

# STATUS · JH-Agent OS · 会话续接卡

更新：2026-10-17。本文记录当前项目快照；恢复工作时同时核对 Git 工作树与最新交付。

方向见 [产品需求](docs/product-requirements-v1.0.md)，阶段与验收见 [ROADMAP.md](ROADMAP.md)，任务看板见 [TODO.md](TODO.md)，历史见 [CHANGELOG.md](CHANGELOG.md)。




## 2026-10-17 · JH-006 受控宿主入口接线运行验证通过，生产桥接未准入

执行方完成 host-entry 接线批的整改与复跑。接手检查点 `a94c848`（未验证），起点已验收提交 `1f605c30`。全量 **389 项：388 通过、0 失败/错误、1 既有跳过**；变异 **33：32 检出、1 冗余（binding-output-digest）、0 异常**；`verify_all` `passed:true`（AST 389 = 报告 389，runtime 未变、storage/cli 加法），JH-005 封存/14 变体、既有 JH-006 守卫、1 路由 × 5 反例、旧库 9 表兼容均未退化。此前 386 项版本的 `cli_main` 别名重绑异常与汇总失败已关闭，并加三项 harness 回归反空跑。见[实施记录](reports/jh006-host-entry-20261017.md)。

本批仅接线运行验证通过；**无真实模型调用、无实际用户项目补丁应用、无真实宿主来源登记**。真实数据库、等待宿主的 task/delivery、真实 Codex 固定调用执行、原始归档四项仍缺，须由架构师宿主会话另行承接。production complete 与 FileToolExecutor 保持关闭，N2/P1 未完成，N1 结项不变。交架构师审查，未自宣结项。

## 2026-10-16 · 离线持久化绑定审查通过，生产完成保持关闭

原交付afb3ac1实际首测341项有39错误，三项独立误收已复现并修复。改为控制层生成固定检查调用、完整当前性重核、登记前后观察、事件事务与call/item独立唯一约束，见[审查报告](reports/jh006-binding-review-20261016.md)。最终359项358过/1既有跳过；30变异29检出+1冗余/0异常，旧库兼容与既有守卫通过。原历史运行器已恢复，证据未覆盖。

本批仅离线绑定通过，没有生产宿主登记/双模型新取证。下一批按[受控宿主入口接线](docs/handoffs/jh006-host-entry-20261016.md)，真实Codex来源步骤由架构师宿主会话承接。production complete、自动文件应用继续关闭，N2/P1未完成，N1结项不变。

## 历史交付快照 · 2026-10-16 · 离线绑定未验证

按离线绑定交接实现登记/读取/复核：控制快照取数、归档重算、实时活性比对、双向唯一落库一笔事务，见[实施记录](reports/jh006-capture-binding-20261016.md)。新增 42 项回归与 15 项定向变异用例；仅加法改动存储新表，其余运行时不动；上一批运行器断言已同步更新。本会话 Shell 不可用：测试、变异、提交与复核均未做。生产完成继续关闭，交架构师补跑验证、审查与裁决。

## 2026-10-16 · 独立桌面采集批通过，生产桥接未准入

原交付77150eb有三项误收，架构师已补强并实测：299项298过/1既有跳过，新采集10变异全检出；真实桌面正例/独立读回、错cwd/旧pin/覆盖负例及既有守卫通过。见[审查与裁决](reports/jh006-host-capture-review-20261016.md)。限定桌面协议、精确调用脚本必填、摘要只证明封存后一致；原始元数据留在本地忽略目录，仓库只收必要派生视图。

当前task/delivery/artifact/review/目标字节尚未可信接续；下一批按[离线绑定交接](docs/handoffs/jh006-capture-binding-20261016.md)。不重接旧候选、不启动真实模型/应用取证，production complete及FileToolExecutor关闭，N2/P1未完成，N1结项不变。

## 历史交付快照 · 2026-10-16 · 独立采集未验证

实测确认本会话可读 Codex 会话日志（上一批不可读结论作废），桌面/CLI 双协议形状已厘清，见[实施记录](reports/jh006-host-capture-only-20261016.md)。新增采集解析器、可执行采集脚本与 22 项合成正反控；生产完成保持关闭，候选路由未重接。本会话 Shell 不可用：测试、试运行与提交均未做，交架构师补跑、审查与裁决。

## 2026-10-16 · 宿主记录候选revise，误放行已封闭

82d3d09候选首轮285项有2错误，四个零宿主执行反例均误进succeeded。架构师否决自报输出/摘要作为来源证明，完整封存候选源码及34项未准入测试，恢复e390867生产拒绝路径。见[审查与裁决](reports/jh006-host-records-review-20261016.md)。安全收口不是桥接完成，原候选测试不计通过。当前259项258过/1既有跳过，1路由/5反例回灌全检出，既有守卫及封存复核通过。

下一批按[独立采集证明](docs/handoffs/jh006-host-capture-only-20261016.md)推进；先证明受信宿主采集与当前任务关联，再审查是否接通生产完成。N1及首批离线结论不变，N2/P1未完成，真实取证暂不启动，FileToolExecutor关闭。旧WIP与证据保留。

## 历史候选交付 · 2026-10-15（已否决并封存，不是当前运行能力）

只读探针确认 transcript 记录形状但无实时可查接口，桥接采用 in-band 输出加摘要重算，见[实施记录](reports/jh006-host-records-implementation-20261015.md)。新增生产完成路由与约31项离线正反控，fixture 路径零改动。本会话 Shell 不可用：测试、核对与提交均未做。门禁保持关闭，原树不动，交架构师补跑验证、审查与裁决。

## 2026-10-15 · JH-006 首批离线通过，生产宿主记录待接入

执行方未验证实现已保存为e4f5017；架构师实际发现并修复版本取值、载荷伪造交付、回执来源/脚本/布尔、范围核对及事务门禁缺陷，见[审查与补强](reports/jh006-offline-review-20261015.md)。本轮251项250过/1既有跳过，新19变异全检出，JH-005封存及14变体和既有控制守卫通过，历史证据原字节保留。

N1结项不变；JH-006仅首批离线通过，N2/P1未完成。生产complete仍拒绝，合成登记仅测试可用，真实调用零，文件应用关闭。下一批按[可信宿主记录交接](docs/handoffs/jh006-host-records-20261015.md)先核实并接入可验证宿主记录，审查后再安排真实取证。不重做JH-004/005，不复活原树STOP方案。

## 2026-10-15 · JH-006 离线实现已写完，未运行、未提交

在已有未提交实现上补齐：修复自动准备缺失方法与完成键名 bug，补 CLI 代码模板装配与准备/查询/完成命令、Skill 最小封装、约 41 项离线回归，见[实施记录](reports/jh006-offline-implementation-20261015.md)。本会话 Shell 不可用：全量/新增测试、JH-005 核对与提交均未做，旧成绩不证明新代码通过。门禁保持关闭，原树不动，交架构师补跑验证与提交。

## 2026-10-15 · JH-005 规格与夹具通过，下一批 JH-006 离线实现

四项旧问题关闭；全量185项（184通过/1跳，0失败错误），14个夹具不一致变体全部拒绝，两项守卫退化检出。架构师补齐辅助核对器六项 post-apply/授权绑定检查，并正式封存两套夹具，见[验收报告](reports/jh005-acceptance-20261015.md)。最终提交查隔离分支最新日志，原执行方整改保存在 c4ac80e 历史检查点。

下一批按[JH-006 离线交接](docs/handoffs/jh006-offline-code-delivery-20261015.md)实施固定代码模板及准备/完成的受控核对。N1结项不变，N2/P1未完成；等待宿主的运行机制尚未实现/验证，真实调用零，JH文件应用门禁关闭，原树失败WIP保留。不要再做JH-005整改或重复JH-004取证。


## 2026-10-15 · JH-005 四项整改实现，未运行

R1—R4 源码与回归已改完：契约两步交付与字节绑定、任务材料化与装配检查、核对器八变体全拒、参考字节契约、独立变异脚本，见[整改记录](reports/jh005-followup-fixes-20261015.md)。本会话 Shell 不可用：未运行、未封存、未提交；改动仅限本批文档/夹具/脚本与状态文档。交架构师补跑验证、封存与提交。门禁保持关闭。

## 历史复核（2026-10-15）· JH-005 revise

架构师已补跑：185 项 184 通过/1 跳过；基线行为检查按预期失败，参考版通过。新增核对器却误收八个不一致变体，宿主契约缺实际应用字节与审核结果绑定，另有任务/参考字节不一致。见[独立审查](reports/jh005-review-20261015.md)。

当前不是只等待 Shell；按[四项整改](docs/handoffs/jh005-followup-20261015.md)继续本批，不重做 JH-004。正式夹具未封存，检查点只是未验收 WIP（最新提交查隔离分支日志），JH-005 不通过、JH-006 不启动；N1 结项不变，N2/P1 未完成，文件应用关闭。原树失败 WIP 保留，真实调用/用户目标应用均零。


## 历史交付快照（2026-10-14）· JH-005 未运行

N2 验收规格、代码/数据双夹具、宿主交付契约及独立核对脚本已写完，见[规格](docs/n2-acceptance-spec.md)与[契约](docs/host-delivery-contract.md)。本会话 Shell 不可用：夹具未运行、哈希未封存、未提交；改动仅限新文档/夹具/脚本与状态文档。交架构师补跑核对、封存、提交与审查。门禁保持关闭。

## 2026-10-14 · N1 已结项，下一批 JH-005

架构师批准 JH-004/N1 按 ADR-0004 生成入口结项，见[验收裁决](reports/n1-acceptance-20261014.md)。实现 c7d7421，位于 D:/JH-Agent OS/data/worktrees/n1-artifact-entry；结项归档 HEAD 查隔离分支最新日志。原树保留 80979f3 与失败 JH-015 WIP，不作为已验收 CLI。

提交前后均 185 项（184 通过、1 跳过、0 失败/错误），8 变异断言检出。真实 Codex CLI 宿主读取正确 Skill，五进程提交/查询/接续/返回与真实 Gemini+Muse succeeded；3 turns、6088 tokens，25 项独立复算通过，查询字节与原预算/期限保持。主控用量单列，旧 C 盘桌面聊天未迁移。

下一批按[JH-005](docs/handoffs/jh005-n2-acceptance-20261014.md)准备 N2 规格、夹具和宿主交付契约。N2/P1 未完成，文件应用关闭、JH-015/016 仍 STOP。无 JH-004 代码整改，不重做 A–E、两项遗留或取证。

## 历史快照（2026-10-14，已由上文更新）· JH-004 离线关闭

全量实际 **185 项：184 通过、0 失败、0 错误、1 跳过**，没有排除测试。历史来源缺失反例在装配前阻断，没有新调用；8 项定向变异均断言检出、零异常，包含独立错 attempt_id 场景。历史来源 P1 与测试措辞 P2 均关闭，见[离线关闭报告](reports/jh004-offline-closure-20261014.md)。

当前无代码整改项；按[有界取证说明](docs/handoffs/jh004-real-evidence-20261014.md)提交后验证真实模型、CLI 接续与实际 Codex Skill 发现/调用。代码与离线通过不构成 N1 结项，真实文件应用门禁仍关闭。旧失败证据与原树源码保留，不再重做两项或 A–E。

## 历史快照（2026-10-14，已由上文更新）· JH-004 两项遗留已整改，未运行

P1 历史来源覆盖与 P2 措辞对齐已改完，新增 5 项回归，见[整改记录](reports/jh004-r02-followup-fixes-20261014.md)。本会话 Shell 不可用：未运行、未提交；改动文件为 src/cli.py、tests/test_jh004_artifact_entry.py 及状态文档。交架构师补跑验证。门禁保持关闭。

## 历史快照（2026-10-14，已由上文更新）· JH-004 整改复核：仍 revise，仅余两项

实际全量 180 项：**178 通过、1 失败、0 错误、1 跳过**。R01、R04 原反例关闭；R03 工作树模块指向正确但宿主发现/调用未验证；R02 当前调用 Mock/缺失标记拒绝生效，但历史 completed attempt 缺来源事件仍可 production succeeded（独立两轮复现：4 attempt / 3 source）。

剩余 **1 P1 历史来源覆盖、1 P2 containing/contains 文本测试不一致**，见[最新复核](reports/jh004-rereview-20261014.md)及[两项继续整改](docs/handoffs/jh004-r02-followup-20261014.md)。按用户要求保存明确未验收的 WIP 检查点，具体提交查隔离分支日志；不是阶段准入。产品实现未由审查会话改写，真实调用 0，门禁关闭，N1 未结项。

执行只处理两项，不重做完整 A–E 或四项。文件编辑可用即可继续修复；Shell 验证由当前架构师会话承接。旧证据与原工作树失败 WIP 保留。

## 历史快照（2026-10-13，已由上文更新）· JH-004 R01—R04 已整改，未运行

R01—R04 源码与 15 项新增测试已在隔离工作树完成，见[整改记录](reports/jh004-r01-r04-fixes-20261013.md)。本会话 Shell 不可用：未运行测试、未建立检查点、未提交；改动文件为 src/cli.py、src/orchestrator.py、tests/test_jh004_artifact_entry.py、.agents/skills/jh-task/SKILL.md 及状态文档。交架构师会话补跑验证与检查点。门禁保持关闭。

## 历史快照（2026-10-13，已由上文更新）· JH-004 独立复核：revise，转四项整改

当前会话已实际运行全量 165 项：164 通过、0 失败、0 错误、1 跳过；独立行为探针仍确认 **3 项 P1、1 项 P2**：任意两条件误通过、明确 Mock 响应得到 production 成功、Skill 指向原树旧 CLI，以及 events 缺失任务成功空列表。见[最新审查](reports/jh004-review-20261013.md)。

当前不是只等待 Shell。按[四项整改交接](docs/handoffs/jh004-after-review-20261013.md)修复，离线缺陷关闭前不消耗真实模型取证资源；尚未验证实际 Codex Skill 发现/调用，真实文件应用门禁关闭，N1 未结项。受审代码未被本会话改写或提交，HEAD 仍 9a7c5dd（隔离分支）。有文件编辑能力的执行会话可继续修复，Shell 检查由本架构师会话承接，不重复生成就绪包。

## 历史快照（2026-10-13，当前状态见上）· JH-004 A–E 已实现，未验证

A–E 代码与 20 项新回归测试已在隔离工作树完成，见[实施记录](reports/jh004-implementation-20261013.md)。本会话无 Shell，未运行、未提交；待有 Shell 会话按序验证（全量回归→真实取证→Skill 宿主）后按明确范围提交并送审。门禁保持关闭。

## 历史快照（2026-10-13，当前状态见上）· JH-004 隔离已准备，待代码实施

原执行会话报告 Shell 不可用，交付仅为就绪包。当前架构师会话已实际运行 Shell，从 80979f3 创建分支 n1-artifact-entry 与隔离工作树 D:/JH-Agent OS/data/worktrees/n1-artifact-entry。原工作树及失败源码保留；文档基线单独归档，A–E 代码/测试/真实接入均尚未完成。执行按[启动卡](docs/handoffs/jh004-start-here-20261013.md)进入已建工作树，不重复创建。真实写入门禁关闭，N1 未结项。

## 2026-10-13 · 最新裁决：发布矩阵 STOP，转 JH-004

父目录守卫先行矩阵已由当前会话实际运行：P-A=false（自身 rename/replace 被拒），P-B=false（另一个进程仍可创建子文件），总门禁 **STOP**。脚本退出码 0 是运行完成，不是准入通过。按手册未继续运行本版预期 156 单测、15 变异或后续产品对照，源文件未改。

[最新独立复核](reports/jh016-parent-publish-review-20261013.md)与[原始矩阵](reports/jh015-batch2-continue-20261013/share-matrix.json)为当前依据。W1 不接受为防覆盖保证；“所有邻接写入必报 uncertain”和“纯 Win32 不可能消除”未获证明。

按 [ADR-0004](docs/adr/ADR-0004-n1-artifact-first.md)，停止该自动发布设计，保留失败 WIP/测试/证据与原契约，真实文件写入门禁关闭。当前执行任务改为 **JH-004：真实 Codex Skill + CLI 的双模型生成产物、只读查询、跨进程接续及结果返回**，直接按[新交接](docs/handoffs/jh004-artifact-entry-20261013.md)实施。八阶段不重排，N1 未结项；N2 代码修改和宿主应用验收未获豁免。

## 历史记录（2026-10-13，当前排期已更新）· JH-015 继续整改实现快照（未验证）

针对第二批复核 B2-01/02/03 已实现父目录钉选重设计（`_DirGuard` 跨发布持有、文件守卫发布前相邻释放、旧对象漂移检测报 uncertain），R1/R4 已校正落实，变异扩至 15 项并同步异常类装配，回归 +6。详见 [机制说明第二版](docs/file-tools-apply-mechanism.md)与 [运行手册](reports/jh015-batch2-continue-20261013/runbook.md)。本会话无 Shell：须先跑共享矩阵先行门（GO 才继续），再跑单测/变异/对照。门禁保持关闭，验证通过后才提交并交复核。

## 历史记录（2026-10-13，当前排期已更新）· JH-015 第二批实现快照（未验证）

第二批四项整改已按 [机制说明](docs/file-tools-apply-mechanism.md)实现完毕但**未验证**：`src/file_tools.py` 原子发布接线完成，`src/orchestrator.py` 错误分类分离，`scripts/verify_phase4_guards.py` 装配与口径重写，新增 5 项回归测试。详见 [实施记录](reports/jh015-batch2-20261013.md)。本会话 Shell 不可用，未运行测试、未提交；旧基线结论不移用。门禁保持关闭，待有 Shell 的会话验证后交 JH-016 复核。

## 历史记录（2026-10-13，当前排期已更新）· 当前未提交批次与复核

HEAD 仍为 80979f3；第二批未提交实现已接线，当前会话完成离线复核：150 项单测为 146 通过、3 错误、1 跳过；13 项变异为 5 检出、7 冗余、1 harness_error。新增独立 4 场景两轮观察一致。最新结论见 [第二批复核](reports/jh016-batch2-review-20261013.md)，执行按 [继续整改指令](docs/handoffs/jh015-batch2-followup-20261013.md)。早间续接快照是历史半成品记录，不再据其恢复旧补丁。

## 一句话现状

N1 已按生成入口范围结项，N2/P1 未完成；下一批 JH-005 规格/夹具与宿主交付契约。文件应用仍未通过，门禁关闭，原树失败 WIP 保留。

## 已确认与未确认

| 项目 | 状态与证据范围 |
|---|---|
| 产品方向 | 用户已提供产品需求 v1.0；已整理落盘，后续使用新路线 N1—N8，旧阶段结项保留为历史证据 |
| 旧 E1 / E2 基础能力 | 已有协议、模型适配、任务控制、预算、审核关联和 SQLite；历史验收范围见旧状态记录与报告 |
| 旧 E3 | 架构师于 2026-10-11 批准结项，证据提交 4c25037；归档提交 88cab8f 已存在 |
| Gemini 原缺凭据阻塞 | 已由用户裁决的本地代理通道解除，不再将旧缺密钥记录当成当前阻塞 |
| 旧 E4 文件工具首批（JH-002 / JH-015） | **未通过、暂缓准入**：最新 v2 父守卫矩阵 STOP；失败实现/测试/证据保留，真实写入关闭，见[最新复核](reports/jh016-parent-publish-review-20261013.md) |
| 新 N1（JH-003 / JH-004） | **已结项（2026-10-14）**：真实 Skill + CLI 固定模板生成入口通过，不应用目标文件 |
| 产品 P1 / 新 N2 | **未完成**：N1 生成入口已通过，代码/数据、宿主应用及完整修复/失败验收待 N2，先 JH-005 |

旧 E3 结项与 JH-002 首批证据：
[阶段 3 主闭环](reports/phase3-verification.json)、
[阶段 3 修复链](reports/phase3-repair-verification.json)、
[429 拒绝证据](reports/phase3-repair-fault-429.json)、
[阶段 3 实施报告](reports/phase3-implementation-report.md)、
[JH-002 文件工具真实闭环](reports/phase4-file-loop-verification.json)、
[文件工具契约](docs/file-tools-contract.md)。
报告中的早期缺凭据状态是历史记录，结合后续补遗读取。

## 当前工作与下一步

已验收 N1 代码仍为 c7d7421；JH-005 规格、辅助脚本与夹具已通过，最新归档提交查 n1-artifact-entry 分支日志。当前下一批 JH-006 只做固定代码模板两步交付的离线实现与回归，不真实调用模型、不启用 JH 文件应用。运行与提交可由架构师承接，原树不动。

## 当前缺口

- JH-004 固定模板宿主入口已验收；旧 C 盘桌面聊天未迁移，通用任务/P1 不在该结论内。
- ADR-0003/0004 的 N1 生成入口已实际验证；JH-005 定义 N2 代码/数据、宿主应用验收，旧 80979f3 不作为当前实现。
- JH-015 最新 v2 的前提矩阵 STOP，P-A/P-B 不成立。发布设计停止推进，W1 未获接受，详见 ADR-0004；失败文件应用仍是待准入工作，不阻塞本批无工具生成入口的实施。
- 已结项的数据转换演示与单文件暂存写入演示不能替代 P1 实际开发任务的全部验收；已新增 `file_write_acceptance_evaluator`（3 条件：暂存成功、JSON 语义相等、工作区与授权范围），更广的开发任务验收器仍待 JH-005 扩展。
- 新阶段重划不能自动降低现有审核、权限、预算和恢复要求。

历史测试对应各自源码快照：80979f3 的 145 项、上一版第二批 150 项均不能移用于 v2。本轮 v2 只运行矩阵，未继续其预期 156 项/15 变异；模型调用 0、夹具清理、共享产品源码未改，N1 未结项。

## 续接时注意

实际项目在 D:/JH-Agent OS；C 盘旧项目目录不作为工作副本。
先查工作树，保留其他执行方的未提交改动。
用户要求方向、需求或阶段变化时主动完成关联检查、文档同步及执行 / 审查双方交接，不等待逐项提醒。具体约定见 [README 的主动对齐说明](README.md#方向变化后的主动对齐)。

详细接入、协议与历史证据按 [docs 索引](docs/README.md) 按需读取，避免把全部历史报告塞进每次上下文。
