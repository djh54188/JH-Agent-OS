# ADR-0001: Codex 接入方式与职责边界

- **状态**：已提议 / 阶段 1 实施基线（Proposed for Phase 1 Baseline）
- **日期**：2026-10-09
- **决策者**：JH-Agent OS 架构与执行团队
- **适用版本**：JH-Agent OS v1.0 - v1.1
- **参考依据**：
  - OpenAI Codex CLI v0.160.1 真实运行能力与命令行规范
  - [OpenAI Codex SDK 文档](https://learn.chatgpt.com/docs/codex-sdk)
  - [OpenAI Agents 运行规范](https://developers.openai.com/api/docs/guides/agents)
  - 项目架构路线图 `docs/architecture-roadmap-v1.1.md`

---

## 1. 背景与上下文

JH-Agent OS 采用四层架构：
- **Meta Planner**：架构审查、宏观目标分解、策略制定与重构裁决；
- **Orchestrator**：任务生命周期状态机、路由决策、风险策略守门与持久化；
- **Worker**：执行具体计划，生成代码、补丁与工具请求；
- **Reviewer**：独立质量审核，对 Worker 产物提供证据化判定与评分。

在项目定位中，“以 OpenAI Codex 为内核”是重要技术路线。然而，根据 `docs/architecture-roadmap-v1.1.md` 的核心审查结论：
1. **不能假定 Codex 原生提供跨供应商调度**（例如自动调用 Gemini 或 Muse）、SQLite 任务与经验持久化，或企业级业务风险守门规则。
2. **必须将“智能建议”与“真实执行权”严格分离**：Worker 仅负责输出结构化建议（计划、代码、ToolRequest），而权限检查、确认闸门与环境落地均由 JH 控制层主导。

因此，阶段 1 必须在真实环境核实 Codex 的可用形态，选定一种低复杂度、高稳定性且符合 MVP 单用户本地部署的接入方式，并清晰界定其职责边界。

---

## 2. 真实环境核查与备选方案评估

经在目标机器（Windows x64，Node v24.19.0，Python 3.14.7）实测，本地已安装官方工具链：
- **CLI 版本**：`codex-cli 0.160.1`（路径：`D:\npm-global\codex`）。
- **认证状态**：已配置有效 ChatGPT 授权（OAuth Bearer），模型路由支持 `gpt-6.1-sol` 等。
- **通信特征**：实测检测到本地网络对 responses WebSocket 存在超时降级，但自动优雅回退至 HTTPS 传输且成功完成命令执行与 Token 统计。

我们评估了四种接入方案：

| 维度 | 方案 A：`codex exec` 子进程驱动 | 方案 B：`codex app-server` RPC 服务 | 方案 C：直接调用 OpenAI API SDK | 方案 D：交互式终端 TUI 自动化 |
| :--- | :--- | :--- | :--- | :--- |
| **接入形式** | 通过 CLI 子进程以 non-interactive 方式调用 | 通过 stdio / WebSocket 对接本地 daemon | 绕过 Codex，直接用 Python/Node OpenAI SDK | 使用 PTY 伪终端模拟人机键盘交互 |
| **结构化保证** | 原生支持 `--output-schema <FILE>` 和 `--json` | 支持 JSON-RPC 与生成式 TS 客户端绑定 | 支持 JSON Mode / Structured Outputs | 无原生结构化约束，输出混杂控制符 |
| **沙箱隔离** | 原生提供 `--sandbox <read-only\|workspace-write>` | 依赖 server 配置与运行时注入 | 无本地沙箱，需项目自研全套隔离 | 依赖 CLI 自身交互配置 |
| **成熟度** | 稳定正式命令，已实测验证通过 | CLI 提示为 `[experimental]` 实验特性 | 稳定，但偏离“Codex 为内核”定位 | 极其脆弱，难以维护与自动化 |
| **工程复杂度** | 低（标准 stdin/stdout/JSONL 处理，易排障） | 中高（需维护长生命周期 socket、心跳与协议漂移） | 中（需自研代码编辑与环境反射工具） | 极高且不可靠 |

---

## 3. 架构决策

### 3.1 核心选型：采用方案 A（`codex exec` 子进程驱动模式）

JH-Agent OS 在阶段 1 至阶段 3（MVP 阶段）选定 **方案 A（`codex exec` CLI 驱动）** 作为 Codex 的内核接入实现方式：
1. **调用形态**：由 JH Orchestrator 派生独立子进程，调用 `codex exec`。
2. **结构化契约保障**：通过 `--output-schema` 强制指定目标 JSON Schema 约束其输出形态；通过 `--json` 采集结构化事件流（JSONL）。
3. **安全沙箱受控**：统一通过 `-s read-only` 或 `-s workspace-write` 施加沙箱限制，严禁在自动化流程中默认启用 `--dangerously-bypass-approvals-and-sandbox`。
4. **工作区约束**：显式指定 `-C <WORKSPACE_PATH>` 与 `--ephemeral`（或托管 Session ID），确保任务执行完全在受限项目目录内闭环。

### 3.2 抽象层预留（`CodexHarness` 接口）

为了防范未来架构锁定，JH 控制层设计 `CodexHarness` 抽象适配接口：
- 上层 Orchestrator 仅与 `CodexHarness.execute_turn(request)` 交互；
- 底层 MVP 默认由 `SubprocessCliHarness` 实现；
- 当系统演进至阶段 4+ 且 `codex app-server` 脱离 experimental 阶段后，可无缝平滑替换为 `AppServerRpcHarness`，无需重构业务核心协议。

---

## 4. 职责边界划分与安全规则

| 职责域 | Codex 内核职责 | JH-Agent OS 控制层（Orchestrator / Policies）职责 |
| :--- | :--- | :--- |
| **模型推理与生成** | 负责 GPT-6.x 代码推理、文件上下文定位、补丁草案生成 | 负责将任务按契约组装 Prompt，调度 Worker / Reviewer / Meta Planner |
| **工具与命令提议** | 在沙箱内生成 ToolRequest 或执行只读分析 | 集中校验工具请求参数、白名单过滤、风险等级评估 |
| **执行权限判定** | 遵从启动参数传入的 sandbox 策略 | 具有**最终执行裁决权**；危险命令（如越界删除、写系统盘、外部网络请求）直接拦截 |
| **任务状态流转** | 仅管理单次 turn 或单会话生命周期 | 维护 `PENDING -> PLANNING -> RUNNING -> REVIEWING -> COMPLETED/FAILED` 全局状态机 |
| **独立审核** | 不负责对自身代码产出进行独立无偏见的 Review | 调度独立 Reviewer 角色（如 Muse / 专用 Review 模型）执行代码审查与证据收集 |
| **记忆与经验** | 仅维持单会话 context window | 管理 SQLite 本地持久化，提炼并存储 `MemoryRecord`，管理经验检索 |

### 4.1 核心纪律原则

1. **执行权与建议权绝不混淆**：Codex 产出的任何“执行脚本”必须由 JH Orchestrator 的受控执行器（Tool Executor）接管并校验。
2. **双重风险闸门**：
   - 闸门 1（静态）：JH 控制层根据《危险动作策略》进行 AST / 正则 / 路径边界校验；
   - 闸门 2（动态）：超出只读或白名单范围的操作，必须暂停任务流并向人类架构师（Meta Planner）抛出批准请求。
3. **单用户本地串行**：MVP 架构明确单用户、本地宿主机运行、串行队列处理，不引入分布式并发协调复杂性。

---

## 5. 局限性与风险控制

- **局限 1：进程启动开销**：单次 `codex exec` 启动及认证握手需要约 1~3 秒。对于 MVP 离线批处理与串行代码任务，该延迟属于可接受范围。
- **局限 2：网络波动回退**：在当前环境下 WebSocket 可能偶发超时并自动降级为 HTTPS。控制层需设置合理的超时阈值（建议缺省 120 秒，长任务 300 秒），并捕获重试事件。
- **局限 3：实验特性隔离**：当前放弃接入 `app-server` 避免了协议频繁变动导致的开发阻塞。
