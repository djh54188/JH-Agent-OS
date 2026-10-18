# JH-020 真实会话运行记录的统一查询与 GUI 只读展示 · 2026-10-18（本切片完成，待独立复核）

**结论先行**：本批完成**真实会话运行记录的统一查询与 GUI 只读展示**这一限定切片：统一应用接口新增运行记录列表/详情，
现有 Agent/会话 视图接入并按服务端配置的登记来源渲染。**只报告本切片完成**；**JH-020 整体、N2/P1、指定审查会话与 GUI 执行仍未完成**。

- 工作树：`D:\JH-Agent OS\data\worktrees\n1-artifact-entry`（分支 `n1-artifact-entry`）
- 基线：`4c94d8d53542a68988e5f525c0babb6f988074bd`
- 承接裁决：`1288d8a` 已按「最小 Codex 隔离只读会话派发／收集及登记证据」范围验收；`4c94d8d` 已按「验证器精准移植与 Windows 运行验证」范围验收。验证器仍为**诊断工具**，旧 12/12、19/19 **不恢复**。
- 证据：`data/jh020-run-history-20261018/`（GUI 截图、面板/详情文本、API 原始 JSON、服务端日志）

## 1. 复用的既有已验收代码（不复制状态机）

| 用途 | 复用来源 |
|---|---|
| 登记读取（区分“不存在”） | `src/agent_session_codex.read_registry_text` |
| 登记解析 + **逐字段校验**（不跳过坏行） | `src/agent_session_codex.parse_registry_text` |
| 证据摘要核对（读磁盘字节） | `src/agent_session_codex.raw_evidence_state` |
| 任务是否存在（只读） | `src.cli.open_readonly_storage` + `TaskStorage.get_task` |

`raw_evidence_state` 为本批新增的**分类器**：把证据判为 `verified / missing / mismatch / unverifiable` 四态；
原有 `verify_raw_evidence(raw) -> (ok, detail)` 保留为薄封装，行为与既有测试不变（未改判定规则）。

## 2. 统一应用接口（`src/app_api.py`）

新增能力声明 `agent_run_history`（supported）与两个只读方法：

- `list_agent_runs()` → `{state, runs[], count, source, live:false, message, note}`
- `agent_run_detail(run_id)` → 单个运行 + 原始记录 + 关联任务核对

**来源由服务端配置**：`AppInterface(runs_path=...)` / `python -m src.gui_http --runs <path>`。
接口**没有**任何接收磁盘路径的参数；`source.browser_supplied` 恒为 `false`。浏览器只能请求“列表”或“某个已存在的 run id”。

**状态四态分明**（互不冒充，且都不改写原文件）：

| state | 含义 |
|---|---|
| `absent` | 登记文件不存在（首次） |
| `empty` | 文件存在但没有记录 |
| `ok` | 有记录且**每条都通过字段校验** |
| `unreadable` | 存在但不可读（`OSError`） |
| `corrupt` | 任一行无法解析或字段非法 ⇒ **整历史不可用** |

沿用既有校验：`run_id` 非空字符串、`idempotency_key` 字段必须存在（合法 `null` 与缺失区分）等；**不跳过坏行、不修复、不迁移、不截断**。

**证据核验**：对配置范围内的原始证据**实际读取磁盘字节**核对 sha256 与帧数，区分
`verified`（通过）/ `missing`（无引用）/ `mismatch`（不匹配）/ `unverifiable`（无法读取）。**不开放通用文件读取**：
`run_id` 只在已解析的历史里查找，绝不当作路径。

## 3. 展示字段（列表与详情）

应用、实例、模型（请求模型与原生回报模型）、角色、run、原生 thread/turn、最后记录时间、记录状态与原生状态、
**本轮用量 / 线程累计用量**（`turn` 与 `thread_total` 分开，附 `model_context_window` 与来源）、证据（含 sha256/帧数/字节）、
未验证事项、事件序列、以及**关联任务**（`linked` + 明示说明）。

真实性口径：
- 面板显式标注 **`live=false`**——历史记录**不代表实时在线**；
- **`declared_complete` 明确写为“执行方声明，非任务验收成功”**，视图内不出现任何成功字面量；
- 未关联任务时明示**“未关联（不创建任务记录）”**，并**不创建任何任务记录**。

## 4. GUI（保持三层分离）

- `gui/data_access.js`：**唯一** `fetch` 处，新增 `listAgentRuns()` / `agentRunDetail(runId)`。
- `gui/components.js`：新增纯渲染组件 `runSourceNotice` / `evidenceNotice` / `bulletList`（无 `JH.api`、无成功判定）。
- `gui/views.js`：Agent/会话 视图中新增“运行记录（历史，只读）”面板与详情；选择运行用**按钮携带 run id**（`dataset.runId`），不解析卡片文本。
- 未新增 GUI 文件（既有分层测试要求的五文件集合不变）；页面仍只经 `JH.api`。

## 5. 验证

| 项 | 结果 |
|---|---|
| 新增 API 测试 `tests/test_jh020_run_history_api.py` | **17 项通过**（含正控与 `absent/empty/corrupt/unreadable`、证据四态、零进程、零改写、未关联不建任务） |
| 新增 HTTP 测试 `tests/test_jh020_run_history_http.py` | **7 项通过**（真实环回服务；含“查询串不能换来源”“run id 形如路径不被当路径”） |
| 新增前端行为测试台 `tests/js/jh020_run_history_harness.mjs` + `tests/test_jh020_run_history_frontend.py` | **7 场景全绿**（字段、非成功口径、模拟与历史区分、证据四态、按 id 选择、损坏/缺失来源） |
| 新增守卫变异 `scripts/verify_jh020_run_history_guards.py` | **4/4 检出**（证据核验被绕过、字段校验被跳过、缺失被折叠成空、未关联被伪造成已关联） |
| 既有 JH-019 前端测试台 | 扩展 API 桩后**四场景仍全绿**（未改断言） |
| 全量业务套件 | **618 项 617 通过 / 1 既有跳过**；**AST 独立计数 618** |
| 实机 GUI 验证 | 真实浏览器打开 `http://127.0.0.1:8791/`，Agent/会话 视图渲染出真实运行记录：`登记状态：ok`、`live=false`、`browser_supplied=false`、`codex-run-1-1779aa1b`、`gpt-6.1-sol`、原生 thread/turn、`本轮用量(turn)=18393`、`线程累计(thread_total)=18393`、`证据核验：verified`（sha256 `de7f3bb9…`、frames 26 / bytes 8868）；详情视图显示 `关联任务 linked=false` 与“未关联（不创建任务记录）”；模拟会话仍显示“未连接（模拟登记）”。截图与文本见证据目录。 |

> 实机说明（如实记录）：IAB 面板中**直接指针点击超时**，改用页面内 `element.click()` 事件派发完成视图切换与详情展开；
> 页面渲染与数据均来自真实服务端与真实 `views.js`。第二次截图因 surface 准备超时未取到，保留首次整页截图与面板/详情文本。

**来源与状态区分**：列表来源恒为服务端配置路径；模拟数据（静态会话登记）继续显式标注 `simulated=true / connected=false`；
历史运行记录与模拟登记在界面上**分区显示**，不互相冒充。

## 6. 边界（本批不做）

不接 GUI 派发、不执行 collect RPC、不实现 continue/cancel、不发送指定审查会话、不另建架构师、
不改任务成功门、不启用 `production complete` 或 `FileToolExecutor`、不扩建调度平台；
未改 Hark 验证器；未推送云端、未合并 PR。查询路径**不启动 app-server、不调用模型、不发送任何会话消息**（有测试锁定）。

## 7. 状态措辞同步

- `1288d8a` 按「最小 Codex 隔离只读会话派发／收集及登记证据」范围**已验收**；`4c94d8d` 按「验证器精准移植与 Windows 运行验证」范围**已验收**。
- 旧快照保留；旧 12/12、19/19 验收结论**不恢复**，多层防护**未宣称通过**。
- **JH-020 整体、N2/P1、指定审查会话唤起与 GUI 执行仍未完成。**
