# JH-020 真实会话接入切片 · 2026-10-18（实现完成、真实往返通过，**待独立验收**）

> **本文件对应 14e42a0，其实现未通过独立审查。** 见 [jh020-session-rectification-20261018.md](jh020-session-rectification-20261018.md)：
> R1 事实绑定、R2 派发前登记/不重发/原始证据、R3 地址与模型/预算约束已整改；
> 本文件 §2 的“能力诚实”与 §4 的离线/真实结论仍成立，但 §2 的 `_assistant_texts/_token_usage` 行为与 §3 的登记时机已被整改取代。当前状态 **候选/待独立验收**。

**结论先行**：本切片实现契约的首个**真实 AgentSessionAdapter**（Codex app-server），并跑通**一次**隔离、只读、固定标识的真实派发/收集往返——
**实际启动、完成与结果关联均已取到原始证据**。指定审查会话**只做只读核查、未发送**（原因见 §5）。
**交付/送达 ≠ 任务验收成功，本切片不代表真实协作闭环完成**，交当前 Codex 架构师独立验收。
`production complete`、`FileToolExecutor` 保持关闭。

- 工作树：`D:\JH-Agent OS\data\worktrees\n1-artifact-entry`（分支 `n1-artifact-entry`，HEAD `a1efe74`）
- 未合入 Hark 候选；未动原树 WIP、三份归档、Hark 负责的验证器。
- 证据目录：`data/jh020-codex-session-20261018/`

## 1. 选型与依据（先核实，再裁剪）

| 候选 | 本机实测 | 结论 |
|---|---|---|
| **Codex** | `codex` CLI 0.160.1 在本机 PATH；`codex app-server --stdio` 握手成功；`auth.json` 存在；`thread/start`+`turn/start`+`turn/completed` 实跑成功 | **选用** |
| ZCode | `zcode.cjs` 存在（15MB），但非 PATH 命令、需 `node` 直接拉起；JH-018 探测记录默认模型已弃用、须显式 `setModel`，且需起动第二个 ZCode 实例 | 本轮不用（可用性更脆、且本机已在 ZCode 宿主内） |

选 Codex 的**技术理由**：app-server JSON-RPC 直接给出**显式原生 thread/turn 关联**与 `turn/completed` 通知，
与契约的 `NativeTurn`/`RunBinding` 天然对应；传输层复用 JH-018 已验证的握手与客户端反向请求处理逻辑。

## 2. 实现（最小派发/收集，不建 Runtime）

`src/agent_session_codex.py`：实现 `AgentSessionAdapter`。

- **传输**：`CodexStdioTransport`（`codex app-server --stdio` JSON-RPC 2.0；读线程解复用 响应/通知/服务端请求；
  未知服务端请求回错误并计数，审批类一律 decline）。
- **submit**：`thread/start`（隔离 cwd，`sandbox="read-only"`，`approvalPolicy="never"`）+ `turn/start`
  （`sandboxPolicy={"type":"readOnly","networkAccess":false}`）；登记 `RunBinding`、请求快照、**SHA-256 指纹**、
  原生 thread/turn；幂等键与 run 索引**分开**。
- **status / collect**：本地登记表；`collect` **有界等待**匹配本 thread+turn 的 `turn/completed`，**不自动重试**；
  收集助手文本、用量、原始引用；不确认项进 `unverified`。
- **capabilities**：`capabilities/submit/status/collect` 声明支持；**`continue`/`cancel` 声明不支持**——
  `turn/steer`/`turn/interrupt` 本传输**未实测**，有界整改属后续切片；不支持即抛 `UnsupportedCapability`，绝不模拟。
- **持久化**：`registry_path` 追加式 JSONL，保存请求指纹、目标会话、原生 turn/run 关联与原始结果。
- **无成功判决**：没有任何状态叫 `succeeded`；成功仍属控制层。

## 3. 真实往返（一次，隔离、只读、固定标识）

`scripts/verify_jh020_codex_session.py`（证据 `roundtrip.json`、`runs.jsonl`）：

- 新建**隔离工作区** `data/jh020-codex-session-20261018/ws`；下发随机 token `JH020-RT-B9F528D4`，要求逐字回显且不使用任何工具。
- 结果：`submit` 返回 run 与原生 turn → `collect` 得 `declared_status=completed` → 文本含该 token；
  `turn/completed` 的 threadId/turn 与本轮一致、status `completed`、error `null`；`unknown_server_requests=[]`。
- **工作区运行前后清单均为空**（无文件写入）。
- 关联三要素齐备：**请求指纹** `06faab3c…`、**目标会话** `codex/codex-app-server/local/jh020-isolated-exec-session`、
  **原生关联** thread `01a14d62-95e0-7281-8d06-619193a20ff6` / turn `01a14d62-9c47-76b0-a6de-a41c5ea949eb`。

## 4. 三类结果分别报告

| 类别 | 结果 |
|---|---|
| **Mock / 离线契约测试** | `tests/test_jh020_codex_agent_session.py` **19 项全过**（能力声明、幂等重放与冲突、地址不匹配、外来完成被拒、状态/错误拒收、超时无重试、未实现请求暴露、注册表持久化） |
| **守卫变异** | `scripts/verify_jh020_session_guards.py` **5/5 检出**（关联、状态、幂等、地址、能力诚实性），`errors=0` |
| **真实运行** | 一次真实往返**通过**（§3）；**仅投递成功不算成功**——脚本只在“关联被证明”时退出 0 |
| **指定审查能力** | **未验证、未发送**（§5）；不因 CLI 会话或另一 app-server 而冒充 |

## 5. 指定审查会话（只读核查；**未发送**）

只读核查（`review_session_readonly.json`，`sent_turn:false`）：

- 目标 `01a14a25-7b28-7a53-825e-a340bebb87fb`：`thread/list` 中**存在**（list 共 10 条）；`thread/read` 成功；
  `cwd=D:\JH-Agent OS`、`name=接任架构师并复核 JH-Agent OS`、`source=vscode`、`model=gpt-6.1-sol/provider=openai`。
- **归属**：一致（cwd 与线程名匹配本项目的架构师角色）。
- **活跃状态**：本 app-server 报告 `status.type=notLoaded`——按既有规则，**不能仅凭另一 app-server 的 notLoaded 推断桌面宿主空闲**；
  且其会话 rollout 在约 52 分钟前仍有活动，末条为 **`turn_aborted`** 后 `thread_settings_applied`。
- **可接续能力**：`thread/resume`+`turn/start` 的**向指定会话**接续本轮**未验证**（不发送即无该证据）。
- **裁决：不发送。** 未新建替代架构师会话、未把独立 CLI 会话当作指定会话已唤起、未改其模型/权限/全局配置。

## 6. 验证计数

- 全量 **540 项 539 通过 / 1 既有跳过**（独立计数一致）；本切片新增 19 项契约测试 + 2 项 JH-018 守卫调整（见下）。
- 本切片定向变异 **5/5 检出**；既有验证器运行结果**按“运行记录”口径**保留（有效性待 Hark 修复与独立复核）。
- **JH-018 守卫最小调整**：`test_production_modules_do_not_import_this_contract` 原先禁止**任何** `src/*.py` 引用契约；
  现改为“核心模块仍禁止，**真实适配器**可引用”，并新增 `test_contract_consumers_are_real_adapters` 强制白名单项
  **必须实际继承 `AgentSessionAdapter`**——守卫强度不降反增（不修改契约模块本身）。

## 7. 交付物与复现

- 新增：`src/agent_session_codex.py`、`tests/test_jh020_codex_agent_session.py`、
  `scripts/verify_jh020_codex_session.py`、`scripts/verify_jh020_session_guards.py`。
- 复现：`python scripts/verify_jh020_codex_session.py`（真实往返）、
  `python scripts/verify_jh020_session_guards.py`（变异）、
  `python -m unittest discover -s tests -p "test_*.py"`（全量）。

## 8. 边界与遗留

- GUI 执行入口、有界整改（continue）、取消（cancel）**留待该路径复核后**推进；本轮显式声明不支持。
- 不建调度器/Runtime；不降低独立审核、验收证据、权限、预算或副作用门禁。
- 本切片**不代表真实协作闭环完成**，也不代表指定审查会话可用；缺失事实如实保留。
