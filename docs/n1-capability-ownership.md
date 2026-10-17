> **2026-10-17 方向基线更新**：本文件的 N1 四分法是历史方向；当前六模块、GUI 入口和可替换 Agent 见新架构。原验收、文件 STOP 与失败证据不变。 现行方向见 [最新基线](modular-architecture.md)。本次仅文档对齐，不派发新任务。

# N1 能力归属分析与旧成果处置方案（JH-003）

> **2026-10-14 补记**：N1 已按 ADR-0004 生成入口结项，见[裁决](../reports/n1-acceptance-20261014.md)。下文旧矩阵不扩展成实际文件应用保证；STOP 不变。

> **2026-10-13 当前处置**：父目录发布 v2 矩阵 STOP，见[最新复核](../reports/jh016-parent-publish-review-20261013.md)。按 [ADR-0004](adr/ADR-0004-n1-artifact-first.md)，FileToolExecutor 的候选最小职责保留，当前自动应用未通过、暂缓准入且不在 N1 默认生产路径；JH-004 改为真实生成产物、查询与接续。下文 ec11584 能力矩阵是方向分析和目标能力，不是本批已支持保证。

> **历史裁决补充（2026-10-12，当前排期以 ADR-0004 为准）**：下文保留为执行方的能力归属分析。四分法、取消通用 Shell/Git 平台、最小暂存应用职责及 Skill + CLI 方向已批准，见 [ADR-0003](adr/ADR-0003-codex-hosted-entry.md)。但“已有模块”不等于安全保证已成立：独立复核在 ec11584 复现五项阻塞缺陷，JH-002 需 JH-015 整改，详见[复核报告](../reports/n1-architecture-review-20261012.md)。正文有关路径、并发、审核绑定的保证是目标，不是当前通过结论。JH-004 可做 ADR/只读准备，真实文件写入接入待修复独立通过；N1 未结项。MCP 可用 stdio，不必常驻 daemon；保留此组件也不是因为只能在它与人工复制之间二选一。下文“待裁决”条目已由本补充与 ADR-0003 作答，不重复申请。

- **日期**：2026-10-12（Asia/Shanghai）
- **关联任务**：JH-002（旧 E4 文件工具批次审查交接）、JH-003（N1 能力归属与旧成果处置）
- **依据文档**：[产品需求 v1.0](product-requirements-v1.0.md)、[现行路线 v2.0](../ROADMAP.md)、[文件工具契约](file-tools-contract.md)、[ADR-0001](adr/ADR-0001-codex-integration.md)

---

## 1. 背景：从“独立 Runtime 调 Codex”到“Codex 内上层增强”的边界对齐

旧工程路线（E1—E4）默认以 Python `Orchestrator` 作为主入口，将 `codex exec` 视为被控制层调度的子进程工具（见 ADR-0001）。
根据《产品需求规格 v1.0》与《ROADMAP v2.0》：
- **Codex 是主要交互入口与核心工作环境**；
- 基础 Coding、文件操作、Git 工作流、基础 Agent 执行、项目上下文与基础工具调用**优先复用宿主**，不重复建设独立 AI 平台；
- **JH-Agent OS 的核心价值在于 Codex 上层增强**：多模型协作（Gemini Worker + Muse Reviewer）、独立审核绑定、分级质量验收、预算与风险控制、受控暂存整合，以及跨会话长期项目状态管理。

因此，必须在 N1 阶段明确区分四类能力归属，并对旧 E1—E4 成果（尤其是刚刚到达可审查节点的旧 E4 首批文件工具 `db86bd6`）给出明确的**保留、调整、暂缓**处置方案。

---

## 2. 四类能力归属矩阵（含实际场景依据）

| 能力分类 | 具体能力项 | 责任方 | 实际使用场景依据 |
|---|---|---|---|
| **1. 宿主原生承担**<br>（Codex Native，JH 不重复建设） | 交互式终端 UI、会话历史与上下文收集、日常单模型代码编辑、常规 Shell / 测试命令执行、常规 Git 操作（status / diff / branch / commit）、宿主级沙箱与基础权限提示 | Codex 宿主环境 | **场景 1（软件开发）**：用户在 Codex 对话中直接探索代码库、运行本地单测、查看 git diff；宿主已原生具备成熟工具链，JH 重建独立 Shell/Git 执行器会产生双套权限与维护冗余。 |
| **2. JH 必须新增**<br>（JH Must Add，核心差异化价值） | ① 跨模型协议适配（Gemini Worker `AnthropicMessagesAdapter` + Muse Reviewer `OpenAIResponsesAdapter`）<br>② 独立审核绑定与防篡改校验（`validate_review`：task_id / attempt_id / 产物 SHA-256 强绑定，产物变则旧审失效）<br>③ 控制层可信验收器（`acceptance.py`：不采信模型自评分，按确定性规则校验）<br>④ 逐调用预算/用量门禁（`input_reserve` / `call_limits` / 非法或未知用量受控停止）<br>⑤ 任务状态机、SQLite 持久化与跨会话状态续接（`tasks` / `model_attempts` / `review_bindings`） | JH-Agent OS 核心层 | **场景 1 & 2（多模型协作与复杂决策）**：Codex 原生不具备自动将产物交由异构模型（Gemini 生成 + Muse 独立严审）并强制要求“审核覆盖当前产物 SHA-256 + 客观验收通过才准许落盘/结项”的闭环能力。 |
| **3. 需要最小封装**<br>（Minimal Wrapper，连接宿主与 JH） | ① **Codex 内调用入口（Skill + `python -m src.cli`）**：供 Codex 主控发起 JH 协作任务、查询状态、读取产物与审核证据<br>② **待准入的暂存与受控应用守卫（`FileToolExecutor` 候选子集，本批不装配）**：当外部模型（Gemini）生成针对工作区文件的修改时，先写入 `_staging/<task_id>/`，经 Muse 审核 pass 且校验目标前置版本未变（`target_pre_sha256`）及路径边界（含 junction / `..` / 同名前缀防逃逸）后，再原子替换到目标文件 | JH CLI / 轻量守卫层（由 Codex 主控触发） | **场景 1（跨模型代码修改落地）**：Gemini Worker 作为远端/代理 API 模型无法直接安全写本地磁盘；若让其直接覆盖工作区文件会绕过 Muse 审核并破坏用户正在编辑的文件。保留 `stage_write` + 审核后 `apply_staged` 恰好充当“外部模型产物进入宿主工作区的安全隔离舱”。 |
| **4. 明确暂缓**<br>（Deferred，不进入 P1 / N1—N2） | ① 独立自研通用 Shell 工具执行器与 Git 工具平台（原旧 E4 后半批计划）<br>② 独立 Web / 桌面 UI 与常驻后台守护进程<br>③ 自动经验学习闭环、自学习路由与系统级自我修改（归 N6 / N8）<br>④ 无界多 Agent 并发调度平台（未验证单任务收益前不建） | 暂缓（后续按 N5—N8 评测驱动） | **全场景约束**：产品需求第 3 节与第 9 节明确规定不建设独立 AI 平台、不重复造 Shell/Git 轮子；优先跑通“用户在 Codex 提目标 → Gemini 执行 → Muse 审核 → Codex 修正/应用 → 交付”的 P1 最小闭环。 |

---

## 3. 旧 E1—E4 已有成果与当前文件工具批次（JH-002）处置方案

### 3.1 旧 E1—E3 成果处置（全部保留复用，仅调整入口方向）

| 旧阶段成果 | 涉及核心模块 | 处置决定 | 说明 |
|---|---|---|---|
| **E1 核心协议与模型适配** | `docs/schemas/*.json`、`src/adapters.py`、`src/responses_adapter.py`、`src/anthropic_adapter.py`、`src/model_config.py` | **完整保留** | Gemini（本地代理 Anthropic Messages，`gemini-3.8-flash-high`）与 Muse（本地代理 Responses，`muse-spark-1.3-contributor-free`）已实测打通，直接作为 N1/N2 的 Worker 与 Reviewer 引擎。 |
| **E2 控制层、状态机与 SQLite** | `src/state_machine.py`、`src/router_policy.py`、`src/storage.py`、`src/control_guards.py` | **完整保留** | 11 态转换、单拥有者租约（OPT-26/27）、事务原子性（OPT-24）、未知/非法用量阻断（OPT-21 及阶段 3 整改 2）、严格成功门禁（`verify_closed_loop_success`）是 P1 质量底线，不因路线重排降低任何门槛。 |
| **E3 真实双模型生成与修复闭环** | `src/artifacts.py`、`src/acceptance.py`、`src/memory.py`、`reports/phase3-*.json` | **完整保留** | 已在 `4c25037` 证明“Gemini 生成 → Muse revise → Gemini 修复 → Muse pass → succeeded”全链成立；作为 N2“资料整理 / 数据转换”代表任务的底层实现与历史证据基线。 |
| **E1/E2 `CodexCliAdapter` / `ManagedCodexAdapter`** | `src/codex_managed.py` | **降级为可选辅助组件（重构 ADR-0001）** | 原设计假设“JH 在外、把 Codex 当子进程调”；新路线下**用户在 Codex 内工作，Codex 调 JH**。该模块保留既有代码与单测（不删除），但在 N1 的 ADR 更新中明确其不再作为主入口。 |

### 3.2 旧 E4 首批文件工具成果（提交 `50a39de` / `6956300` / `db86bd6`）专项处置

当前分支 `phase4-file-tools-batch1` 已完成代码、17 项离线测试、5 项变异守卫与 1 次真实 Gemini+Muse+文件工具闭环取证（`reports/phase4-file-loop-verification.json`）。按 N1 范围对齐原则，具体处置如下：

1. **保留并纳入 N1/N2 核心链路的部分（Retain as Staged Apply Gate）**：
   - **`FileToolExecutor` 的路径边界检查（`resolve_workspace_path` + `_open_verified`）**：逐组件真实目录行走、盘符与大小写规范化、同名前缀目录防护（`ws` vs `ws-evil`）、全祖先与最终组件 reparse point（junction / symlink）拦截。这是防止外部模型提议的路径逃逸出当前项目的硬性边界。
   - **暂存写入与审核后受控应用（`stage_write` + `apply_staged` + `recover_apply`）**：直接对应 `TODO.md` 中 `pm-dispatch` 候选借鉴点“每任务隔离与受控整合（复用现有暂存、路径授权及版本检查）”，也是 N2“本地代码修改代表任务”让 Gemini 产出安全落地到工作区的现成机制。
   - **文件任务可信验收器（`file_write_acceptance_evaluator`）与 `tool_plan.v1.json` 协议**：保留用于验证结构化文件修改提议。
2. **明确调整的部分（Adjust）**：
   - **定位调整**：`FileToolExecutor` 不再向通用本地文件管理器扩展（不增加 move / copy / glob / chmod 等冗余工具），日常文件浏览与上下文收集交由宿主 Codex 原生工具完成；`FileToolExecutor` 专职负责**“需经 Muse 独立审核与版本对账的跨模型受控文件落盘”**。
3. **明确暂缓/停止的部分（Defer / Stop）**：
   - **停止旧 E4 第二批原定的“自研受限 Shell 执行器与 Git 工具封装”**：在 Codex 内使用时，测试运行（如 `python -m unittest`）与 Git 操作（`git status` / `git diff`）由宿主 Codex 按既有安全规则执行，或仅在 N2 验收夹具中以固定验证脚本形式由控制层调用，不建设通用 Shell/Git 工具平台。

---

## 4. ADR-0001 复核结论与 N1 最小 Codex 内接入方案（为 JH-004 铺路）

### 4.1 原 ADR-0001 与新路线的差异
- **原 ADR-0001**：`用户 → Codex 入口 → JH 控制层 → codex exec 子进程`。实际上只实现了 `JH 控制层 → codex exec`，缺少从 Codex 会话内部反向调用 JH 的轻量协议与工作流约定。
- **新 N1 目标**：`用户在当前 Codex 会话提出目标 → Codex 通过 JH Skill / CLI 提交并推进任务 → JH 协调 Gemini Worker 与 Muse Reviewer 完成执行、验收、修复与暂存应用 → Codex 读取结构化结果与证据向用户交付`。

### 4.2 推荐的 N1 最小接入形态（Skill + 增强 `src.cli`）
依据 `ROADMAP.md` 3.1 第 2 条（“优先验证 Skill + 现有 CLI；确实需要独立工具接口时再评估 MCP”）：
1. **CLI 入口增强（`python -m src.cli`）**：
   - 现有 `src/cli.py` 已支持 `submit`、`status`、`events`、`artifact`、`approve`、`cancel`、`recover`。
   - 在 JH-004 中补充一页式任务摘要与结果导出（包含当前状态、产物路径/内容、审核结论与问题列表、验收结果、阻塞原因），使 Codex 主控只需一次 CLI 调用即可获得结构化 JSON 反馈，无需拼装多条命令。
2. **项目内 Skill 封装**：
   - 提供标准的 JH 协作 Skill 说明，规定 Codex 在何种场景下调用 `python -m src.cli` 派发给 Gemini + Muse、如何传入自包含任务卡、如何处理 `awaiting_approval` / `needs_attention` 阻塞反馈。
3. **实际权限执行位置说明**：
   - **Skill / Prompt 仅提供流程引导**，不承担硬安全边界；
   - **硬权限边界位于两处**：① JH 控制层内部的 `RiskPolicyEngine` + `FileToolExecutor`（拦截越界路径、junction、未授权目标、未过审应用与超预算调用）；② 宿主 Codex 自身的命令执行沙箱与用户审批机制。

---

## 5. 待架构师裁决事项（衔接 JH-002 审查与 JH-004 启动）

1. **JH-002（旧 E4 首批文件工具，`db86bd6`）独立审查与处置确认**：
   - 裁决是否认可上述 §3.2 处置方案：**将已完成的 `FileToolExecutor`（暂存写入 + 路径/junction 边界 + 审核后原子应用与恢复）作为“跨模型产物受控落盘守卫”保留，同时正式取消旧 E4 原定的通用 Shell/Git 工具平台开发**。
2. **JH-004 接入形式确认**：
   - 裁决是否批准在 N1 阶段采用 **“项目级 Skill + `python -m src.cli` 结构化子命令”** 作为最小 Codex 内接入方案（暂不引入独立常驻 MCP Server daemon），并据此编写 ADR-0003 更新原 ADR-0001 的入口方向。
