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

## 4b. 整改 R1—R4（基线 8bfc9405）

**R1 草案存储可靠性**
- 单进程写入边界：按解析后的草案路径取进程级锁（`_drafts_lock`），**跨越整个「读取 → 追加 → 发布」周期**，不只是最后一次替换；多进程协调明确不在本批范围（单本地进程）。
- 临时文件唯一化：改用 `tempfile.mkstemp`（同一目录、唯一名），写完 `flush`+`fsync` 后原子发布；任何失败路径都删除临时文件并保持旧文件字节不变。
- 最终改名加**有界重试**（`_atomic_replace`，仅针对 `PermissionError`）；改名本身仍原子，持续失败仍保持旧文件不变。**起因说明（如实）**：全量套件曾出现 1 次并发 500，**未复现，根因未确认**——单独 30×8=240 次并发保存、随后 6 次全套件均未复现，因此**不能断言**它属于某一类（如 Windows 占用竞态）。有界重试是**预防性加固**，不是已确认根因的修复；补「重试后成功」「持续失败保留字节」两个测试只为固定该行为。
- 结构错误不再当空列表：顶层非数组 → `drafts_malformed`；条目非对象或缺字段 → `drafts_malformed`；不可解析 → `drafts_unreadable`。三者都保留原字节、不覆盖。
- 测试：8 线程并发保存后**每个返回的 draft_id 都存在**（计数一致）；并发保存时**临时文件名互不相同**（拦截 `os.replace` 记录）；注入发布失败 → 旧文件字节不变、无临时残留、错误以结构化 500 返回；真实 HTTP 并发（8 并发 POST）同样全部落盘。

**R2 前端请求归属**
- `renderGeneration` 在每次视图切换/任务选择时自增；视图响应只在代次仍为当前时绘制，**迟到响应不会覆盖当前页面**。
- 任务详情/审查与交付双条件守卫：代次一致**且** `currentTaskId` 未变。
- 草案列表独立 `draftsGeneration`：旧列表响应既不回写空列表也不重复追加（绘制前再次清空）。
- 行为验证（非字符串检查）：`tests/js/jh019_generation_harness.mjs` 在 DOM 桩中真实执行 `components.js`/`views.js`，用可控制完成顺序的 Promise 复现三个场景——工作台→会话页（旧工作台响应迟到）、任务 A→B（A 响应迟到）、保存后列表（旧空列表迟到）；断言迟到响应不改变 DOM。

**R3 本机服务边界**
- 只允许环回：`LOOPBACK_HOSTS = {127.0.0.1, ::1, localhost}`，`validate_host` 在**创建 socket 之前**拒绝其它主机（`0.0.0.0`、`::`、私网 IP、域名）。
- 测试分两类：允许（三个字面量通过校验；本机可绑定的 `127.0.0.1`/`localhost` 实际绑定成功）与拒绝（非环回参数抛 `ValueError`，且断言 `ThreadingHTTPServer` **未被构造**）。未引入远程访问或认证平台。

**R4 最终 GUI 证据（整改后复验）**
- 四个视图与草案保存刷新已在真实浏览器中复验通过；草案保存后列表**即时**出现该草案并显示 `draft=true`、`execution=not_triggered`。
- 归档到项目忽略目录（`data/` 未跟踪，不入 Git）：
  - `data/jh019-gui/evidence/jh019-workbench.png`（工作台整页截图）
  - `data/jh019-gui/evidence/jh019-views-snapshots.json`（任务详情 / Agent·会话 / 审查与交付 三视图 DOM 记录）
  - `data/jh019-gui/evidence/jh019-draft-after-save.txt` 与 `.png`（保存后草案区记录与截图）
- 演示库 `data/jh019-gui/gui-demo.sqlite3` 在 `data/jh019-gui/README.md` 中明确标注为**演示数据**：执行方本地构造，仅用于展示只读渲染，**不是真实模型任务**，不含真实 Agent/模型执行记录。

## 4c. 二次整改（基线 356d63f）

**跨面板干扰（已修）**：草案列表的代次原先挂在模块级，任何面板的回调都会推进它。复现路径：保存请求挂起 → 切到会话页 → 返回工作台 → 新面板的列表请求挂起 → 旧保存完成（推进模块级代次）→ 新面板列表返回却被判为过期而丢弃，页面显示 0 条。
- 修复：代次**归属具体面板**（`draftPanel(panelGeneration)` 内的 `panelDraftsGeneration`），并在刷新与绘制前校验该面板是否仍是当前渲染代次。失效面板的回调既不发出新请求，也不能使当前面板的在途请求失效。
- 回归：新增行为场景 `save_pending_across_view_switch`，逐步复现上述序列，断言**当前列表显示该草案（1 条、不重复）**、当前面板无空态、且失效面板未再发请求；原 3 个场景保留。
- **反空跑验证**：把 `gui/views.js` 换回基线版本再跑同一测试台，该场景**失败**（`stale panel issued another draft-list request`），换回修复版后通过——说明该场景确实捕获所报缺陷。

**证据重采**：新增 `data/jh019-gui/evidence/jh019-draft-after-save-v2.png` 与 `.txt`。替换原因：旧图 `jh019-draft-after-save.png` 为整页拼接，且当时保存反馈额外重复了一遍 `draft`/`execution`（与下方草案卡片内容重复），画面出现重复块。本次改为：成功反馈只保留一行提示（不再重复卡片字段）、草案面板滚动到视野内后取**单帧视口**截图，画面清晰显示「已保存草案 draft-…（未触发任何执行）」与**唯一一条**新增草案卡片。旧证据保留未删。

## 5. 验证与计数

全量 **492 项 491 通过 / 1 既有跳过**（`test_phase2_hardening.py:152`），其中 JH-019 共 66 项（应用接口 29、分层约束 12、真机 HTTP 20、前端请求归属行为 5 + 前端行为测试台 4 场景）。计数以实际运行结果为准。R2 为**行为测试**（DOM 桩 + 可控 Promise 顺序），不是源码字符串检查。本轮**不调用真实模型、不触发工具**；GUI 只读既有记录 + 写草案文件。

## 6. 边界与遗留

- **未做（属 JH-020）**：真实派发、交付收集、自动触发独立审查。能力表中以 `supported:false` 显式列出，界面显示为不支持。
- 未引入微服务、前端框架、插件平台或完整编辑器；传输层只用标准库，技术选型可替换（换 GUI 不改核心：核心测试不经 HTTP）。
- `production complete`、`FileToolExecutor` 继续关闭；未改 Schema、门禁、权限与封存夹具；未启动真实 Agent/模型/工具执行。
- 演示库与草案文件位于未跟踪的 `data/jh019-gui/`，不入 Git。
