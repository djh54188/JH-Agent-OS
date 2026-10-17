# JH-019 最小模块化 GUI 与统一应用接口 · 2026-10-17

**结论：本机可打开的最小 GUI 已实现并实测打开；四个视图只读呈现既有记录，目标输入保存为显式标记的草案，本批不触发任何 Agent／模型／工具执行。** 未接入真实派发与自动审查（属 JH-020）；`production complete` 与 `FileToolExecutor` 保持关闭；未宣布 N2/P1 完成。

基线 `dc162bed`（JH-018 已由架构师按"接口探测与契约设计"范围验收）。

## 1. 交付物

| 层 | 文件 | 职责 |
|---|---|---|
| 应用接口 | `src/app_api.py` | 统一业务入口：能力声明、任务索引、任务详情、会话登记、审查与交付、草案。复用 `src.cli.build_task_summary` 与 `TaskStorage` 只读查询，**不含**状态机、审核规则或 SQL |
| 传输 | `src/gui_http.py` | stdlib `http.server`，仅把应用接口暴露为 `/api/*` 并服务 `gui/` 静态资源；无业务规则 |
| 页面模块 | `gui/views.js` | 工作台 / 任务详情 / Agent·会话 / 审查与交付 |
| 公共组件 | `gui/components.js` | 任务卡片、状态标记、连接提示、证据列表、空/错/加载态；用 `textContent` 构建 DOM，不用 `innerHTML` |
| 数据访问层 | `gui/data_access.js` | **唯一**调用 `fetch` 的模块；错误也作为数据返回 |
| 外壳 | `gui/index.html`、`gui/styles.css` | 视图切换与样式 |
| 存储（加法） | `src/storage.py` | 新增只读 `list_tasks()`（仅 SELECT，无写路径） |
| 测试 | `tests/test_jh019_app_api.py`、`test_jh019_gui_layers.py`、`test_jh019_gui_http.py` | 核心脱 GUI、分层约束、真机 HTTP |

启动（本机）：`python -m src.gui_http --port 8765 [--db <sqlite>] [--drafts <json>]`

## 2. 逐条对照本批要求

1. **本机可打开的最小 GUI**：已实现四个视图，并在真实浏览器中实际打开验证（见 §4）。
2. **三层分离、业务全部走统一接口**：页面只调 `JH.api`；`data_access.js` 是唯一 `fetch` 处；`gui_http` 每个路由都委托 `self.api.*`。静态检查锁定（`test_jh019_gui_layers.py`）。
3. **复用既有能力，不复制状态机/审核规则**：应用接口 `from src.cli import build_task_summary, open_readonly_storage`；测试断言 `app_api.py` 不含 `orchestrator`/`create_task(`/`transition_task_state`/`subprocess`，且不直接持有数据库连接（无驱动 import、无 `_conn`、无手写 SQL）。
4. **目标输入为显式标记草案**：`save_goal_draft` 写入 `{draft: true, execution: "not_triggered", kind: "jh019-goal-draft/v1"}`；草案不建任务（测试对比任务数不变）；带 `dispatch/execute/run/start_task` 的请求被拒绝（409 `draft_cannot_execute`）。
5. **模拟会话显式标记；未接入/空/错如实显示**：会话登记全部 `connected:false`、`simulated:true`，并附 JH-018 实测证据（哪项实测、哪项未实测）；数据库缺失 → 显式空态（`database_present:false`）；未知任务 → 404 `task_not_found`；草案文件损坏 → `drafts_unreadable` 且不覆盖原文件。
6. **核心可脱 GUI 使用；前端不碰数据库/不启动工具/不判成功**：核心测试完全不经 HTTP；`gui_http` 缺失时 `src.app_api` 仍可用（子进程实测不导入传输层）；前端无 `sqlite`/`require(`/`child_process`，且无任何 `succeeded` 比较或赋值（正则锁定）。

## 3. 状态语义（不伪造）

- 任务状态徽标只显示后端记录的字符串；页面注明"本界面不判定成功"。
- 审查与交付视图直接呈现记录的 `task_status` / `review_verdict` / `delivery_scope`（`generated_artifact`、`target_applied` 等）；界面不推断交付完成。
- 能力页脚显式列出**不支持**项：`dispatch`、`collect`、`review_trigger`（原因指向 JH-020）——未接入的能力显示为未接入，而不是绿色已连接。
- 会话视图展示 JH-018 的接口证据（如 ZCode `dispatch` 实测、Codex `review` 未实测），把"接口存在"与"已接入"分开。

## 4. 真机验证

用 ZCode 内置浏览器实际打开 `http://127.0.0.1:8791/`（本地演示库 `data/jh019-gui/gui-demo.sqlite3`，`data/` 未跟踪）：

- **工作台**：显示真实任务 `task_gui_demo_01`（`status=created`）、草案表单与草案列表；页脚列出三个不支持能力。
- **草案保存**：界面填入标题/描述 → 保存成功提示 `已保存草案 draft-…（未触发任何执行）`，证据显示 `draft=true`、`execution=not_triggered`；**保存后列表即时刷新**显示该草案（首版曾出现陈旧空态，已修并复验）。
- **Agent / 会话**：两个会话均显示 `未连接（模拟登记）`、`simulated=true · connected=false`，并列出逐能力证据。
- **审查与交付**：显示 `created`、`review_verdict=—`、交付范围 `generated_artifact=false`、`target_applied=false` 与后端 note，验收为空态。
- **任务详情**：显示状态、用量（`turns_used=0`、`tokens_used=—`）、产物为空态、`events_count=1`。

截图：`C:\Users\a\.zcode\cli\artifacts\sess_47b77812-…\…png`（工作台）。

HTTP 层由 `tests/test_jh019_gui_http.py` 在临时端口上自建服务并实测：`/`、`/static/*`、`/api/capabilities|tasks|sessions|drafts`、`POST /api/drafts`、未知路径 404、`/static/../src/cli.py` 越界被拦。

## 5. 验证与计数

全量 **472 项 471 通过 / 1 既有跳过**（`test_phase2_hardening.py:152`），其中 JH-019 新增 46 项（应用接口 21、分层约束 12、真机 HTTP 13）。本轮**不调用真实模型、不触发工具**；GUI 只读既有记录 + 写草案文件。

## 6. 边界与遗留

- **未做（属 JH-020）**：真实派发、交付收集、自动触发独立审查。能力表中以 `supported:false` 显式列出，界面显示为不支持。
- 未引入微服务、前端框架、插件平台或完整编辑器；传输层只用标准库，技术选型可替换（换 GUI 不改核心：核心测试不经 HTTP）。
- `production complete`、`FileToolExecutor` 继续关闭；未改 Schema、门禁、权限与封存夹具；未启动真实 Agent/模型/工具执行。
- 演示库与草案文件位于未跟踪的 `data/jh019-gui/`，不入 Git。
