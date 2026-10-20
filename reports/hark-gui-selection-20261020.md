# Hark-派单-05：历史运行列表的当前选择提示与单列详情定位

来源：Hark（执行角色）｜Hark-派单-05｜类型：交付报告
日期：2026-10-20（Asia/Shanghai）

> **结论：云端候选，待独立复核与 Windows 集成。不是验收。**
> 所有浏览器结果均为 **Linux 合成预览，未作 Windows 验收**；记录为合成数据（`SYNTHETIC-RUN-*`），字体为云端安装的 Noto Sans CJK SC，不代表本地真实运行。

## 0. 身份与基线

| 项 | 值 |
|---|---|
| 仓库 | djh54188/JH-Agent-OS |
| 基线 | `3d6611a6d5d63c29b260bc00db7fee728a67a45b`（核对一致） |
| AGENTS.md SHA-256 | `edc9846b33c908ee6f16597cd2dd920f1c0564489271713a027bbe7e24ea74b3`（核对一致） |
| 分支 | `hark-gui-selection-20261020`（自基线新建） |
| 功能提交 | `068afb504bfcffdcbb9ba45ce056fd61b6a916ef` |
| 报告提交 | 本文件所在提交（分支头） |
| 未触碰 | main、n1-artifact-entry、PR #1；未合并、未 force push |

## 1. 改动范围（功能提交 068afb5，4 文件，+582/−0）

| 文件 | 改动 | SHA-256（提交后） |
|---|---|---|
| `gui/views.js` | +50：`markSelected`、`revealDetail`、`runButtons`（面板内）、模块级 `SINGLE_COLUMN_QUERY`/`isSingleColumn`/`setPressed`；点击处理器加两行调用 | `f6eba51f351e27873c7cd7f20b9431bf3ef38e01695023128fc387b6e5556215` |
| `gui/styles.css` | +16：选中卡片描边/阴影、“● 当前查看”文本标记、按钮 `aria-pressed="true"` 样式、`focus-visible`、`scroll-margin-top`，以及 1100px 断点内 `.panel-history .run-detail:not(:empty)` 最小高度；全部以 `.panel-history` 开头 | `cac2ee9c9e3878d9b14e700e47c8f187ec996a169c3ebbdaa1984afe2d5fb7e8` |
| `tests/js/hark05_run_selection_harness.mjs` | 新增离线行为测试，11 场景 | `d650552ed9e304b7a0d11ac51d920a78bffd1e103ca55fec7957aaa540f36aca` |
| `scripts/verify_hark05_selection_guards.py` | 新增三项定向削弱验证器 | `2e0fa55f14b2b4bcf44897909582377ae65b25ac857040bc81ce3ad28686abd1` |

`gui/components.js`、`gui/data_access.js`、`gui/index.html`、`src/`、既有测试/守卫/夹具：`git diff 3d6611a 068afb5` 为空。

### 行为设计

- 选择只在 `list` 点击处理器里改变：`markSelected(runId)` → 既有 `loadDetail(runId)` → `revealDetail()`。`loadDetail` 的 `.then` 回调一行未改，所以任何响应（迟到成功/错误/空/错误 run_id/旧面板）都没有改变选择或滚动的代码路径。
- 选中态：按钮 `aria-pressed="true|false"`（渲染时即为 `false`，作为切换按钮暴露给辅助技术）；卡片 `data-selected`；视觉上有“● 当前查看”文字标记＋描边，不只依赖颜色。按钮文字“查看记录详情”及历史/模拟/执行方声明/证据状态文案未改。
- 定位：`window.matchMedia("(max-width: 1100px)")` 与 styles.css 既有单列断点同一字符串；单列时 `detail.scrollIntoView({block: "start"})` 一次；双列（≥1101px）直接返回，不滚动。无 `matchMedia` 时按双列处理。
- 单列详情区非空时最小高度 `calc(100vh - 24px)`：否则加载态较短、位于页面末尾，滚动只能停在底部，标题落在视口下方（首轮实测标题 top≈700/900，可见但偏下）；加此规则后标题 top≈30。
- 点击只调用既有 `JH.api.agentRunDetail`；无新请求、无轮询。

### 四个请求关联守卫

`GENERATION_GUARD`、`REQUESTED_RUN_GUARD`、`PANEL_GUARD`、`RUN_ID_BLOCK` 四个锚点在提交后的 views.js 中各出现 1 次，判断语句未改；旧变异脚本照旧 2 检出 + 2 冗余。

## 2. 完成判据对照

| # | 判据 | 结果 | 证据 |
|---|---|---|---|
| 1 | 新行为测试覆盖并全部通过 | 11/11 ok，exit 0；基线 GUI 上同一测试 1 ok/10 FAIL（仅 `click_only_queries_detail` 在基线也成立） | `logs/postcommit-hark05_run_selection_harness.txt`、`logs/baseline-hark05_run_selection_harness.txt` |
| 2 | JH019 4/4、JH020 13/13、既有变异 2+2 | 基线与提交后均 4/4、13/13；变异 detected=2、redundant=2、verdict pass | `logs/{baseline,postcommit}-jh019_*.txt`、`-jh020_*.txt`、`-verify_jh020_run_history_frontend_guards.txt` |
| 3 | 三项定向削弱 | control_ok=true；3/3 以断言失败检出，异常 0（见第 4 节） | `logs/postcommit-verify_hark05_selection_guards.txt` |
| 4 | 真实指针 1440/760/480 | 选择唯一、窄窗口标题可见、overflowX=0、双列点击 scrollY 不变（另加 1101/1100） | `browser/postcommit-browser-results.json`、`screenshots/` |
| 5 | 截图标注 | 每张截图右下角红底“Linux合成预览，未作Windows验收” | `screenshots/*.png` |
| 6 | 提交后复验、原始日志、版本 | 全新 clone 复验；`extract_counts.sh` 从日志重算全部计数 | `logs/`、`extract_counts.sh` |

## 3. 故障类覆盖

行为测试（离线 DOM 桩，真实 components.js/views.js；可控 `matchMedia` 宽度、记录 `scrollIntoView`）：

| 故障类 | 输入 | 预期 | 结果 | 场景 |
|---|---|---|---|---|
| 无选择 | 列表加载完，未点击（760px） | 全部 `aria-pressed=false`、`data-selected=false`；不滚动；详情空 | 通过 | `no_selection` |
| A→B | 点 A，再点 B，两响应先 A 后 B（1440px） | 仅 B 选中；显示 B 详情、无 A 内容 | 通过 | `a_to_b` |
| 重复选同项 | A 点两次（760px） | 仅 A 选中；2 次详情请求、2 次定位（每次明确点击一次）；响应不再滚动 | 通过 | `repeat_same` |
| 乱序成功 | 点 A、B；B 先回，A 迟到成功（760px） | 选择仍 B；响应后滚动次数不变；不显示 A | 通过 | `out_of_order_success` |
| 迟到错误 | 点 A、B；B 回成功，A 迟到错误 | 选择仍 B；不滚动；不显示 A 错误 | 通过 | `late_error` |
| 错误 run_id | 点 B，响应携带 RUN-C | 选择仍 B；不滚动；显示 `unexpected_run_id`，不渲染 C | 通过 | `wrong_run_id` |
| 离开重进 | 点 A，切到工作台再回会话，旧响应随后到达 | 新面板无选择；旧响应不绘制、不滚动；新面板首次点击定位 1 次 | 通过 | `leave_reenter` |
| 加载/错误/空详情 | A 显示后点 B（加载中）→B 错误→点 C→C 空（1440px） | 加载/错误/空各自对应当前选择，期间不出现 A 内容或上一状态文字；双列不滚动 | 通过 | `loading_error_empty` |
| 双列/单列 + 1100/1101 | 宽度 1440、1101、1100、760、480 各点 B 并回响应 | 1440/1101：0 次滚动；1100/760/480：恰 1 次，目标为详情区、`block:"start"`；响应不滚动 | 通过 | `breakpoints_1100_1101` |
| 点击只查详情 | 点 A、B | 只有 `agentRunDetail` +2，其它 8 个 API 调用数不变；参数为所点 run_id | 通过 | `click_only_queries_detail` |
| 断点一致与范围 | 读 styles.css/views.js | CSS 含 `@media (max-width: 1100px)`，JS 同一字符串；新增选择相关选择器（5 条）全部以 `.panel-history` 开头 | 通过 | `css_breakpoint_and_scope` |

浏览器（headless Chromium 156.0.8078.4，真实 `mouse.down/up`，视口高 900，每宽度依次点 A、B；点击前先把按钮滚入视口再记 scrollY）：

| 宽度 | 列数 | 点击 | scrollY 前→后 | 详情标题 top | 可见 | overflowX | 唯一选中 | 点击产生的请求 |
|---|---|---|---|---|---|---|---|---|
| 1440 | 2 | A / B | 785→785 / 815→815 | 31 / 31 | 是 | 0 | A / B | 2 次 GET `/api/agent-runs/<id>` |
| 1101 | 2 | A / B | 839→839 / 1330→1330 | 31 / 31 | 是 | 0 | A / B | 同上 |
| 1100 | 1 | A / B | 721→2343 / 1093→2343 | 30 / 30 | 是 | 0 | A / B | 同上 |
| 760 | 1 | A / B | 724→2404 / 1116→2404 | 30 / 30 | 是 | 0 | A / B | 同上 |
| 480 | 1 | A / B | 1038→3195 / 1589→3195 | 31 / 31 | 是 | 0 | A / B | 同上 |

基线 GUI 同一探针：各宽度 overflowX 均为 0（候选无新增水平溢出）；基线 760/480 点击后标题 top 1711/2188（不可见），无 `aria-pressed`。计算样式：选中卡片边框 `rgb(29,78,216)`＋2px 阴影＋“● 当前查看”；未选中边框 `rgb(203,210,217)`、无标记。

### 未覆盖项

- Windows 本机、真实浏览器版本/缩放/字体（Segoe UI、Microsoft YaHei）、真实 GUI 后端（`src/gui_http.py`）与真实登记历史：未运行，交 Windows 集成。
- 浏览器探针只在响应即时返回的合成服务上验证；乱序/迟到/错误 run_id/离开重进只在离线行为测试中覆盖，未在浏览器里人为延迟响应。
- 键盘操作（Tab/Enter/Space）与读屏器实际播报未测；仅验证 `aria-pressed` 属性值和 `focus-visible` 样式存在。
- 窗口在点击后被拖宽/拖窄、用户在加载期间手动滚动：未测；设计上不做任何后续滚动。
- `prefers-reduced-motion`：未使用平滑滚动（默认 `behavior: auto`），未单独测。

## 4. 定向削弱（scripts/verify_hark05_selection_guards.py）

每项只在 gui/ 临时副本中单独删除一行；目标场景须以 `kind: "assertion"` 失败才算检出，异常/无报告/锚点不唯一一律不计。

| 削弱 | 锚点（各出现 1 次） | 目标场景 | 结果 | 同时失败的场景 |
|---|---|---|---|---|
| 移除选中更新 | `markSelected(button.dataset.runId);` | `a_to_b` | assertion：“after A: aria-pressed of RUN-A is false, expected true” | a_to_b、late_error、leave_reenter、loading_error_empty、out_of_order_success、repeat_same、wrong_run_id |
| 移除详情定位 | `revealDetail();` | `breakpoints_1100_1101` | assertion：“1100px single-column click positioned 0 times” | breakpoints_1100_1101、leave_reenter、repeat_same |
| 取消窄窗口限制 | `if (!isSingleColumn()) { return; }` | `breakpoints_1100_1101` | assertion：“1440px two-column click scrolled (1)” | breakpoints_1100_1101、loading_error_empty |

control（未改副本）先通过；detected=3/3，verdict pass，exit 0。

## 5. 全量 Python 套件（无 Python 改动，仅作回归对照）

| 版本 | 代理 | Ran | failures | errors | skipped | 退出码 |
|---|---|---:|---:|---:|---:|---:|
| 基线 3d6611a | 有 | 704 | 27 | 78 | 2 | 1 |
| 基线 3d6611a | 无 | 704 | 21 | 63 | 2 | 1 |
| 提交后 068afb5 | 有 | 704 | 27 | 78 | 2 | 1 |
| 提交后 068afb5 | 无 | 704 | 21 | 63 | 2 | 1 |

失败 ID 集合基线与提交后逐条一致（无代理 85、有代理 106）。均为既有 Linux 平台/代理问题（与派单-04 Linux 计数相同），不代表 Windows；Windows 上执行方此前报告为 704/OK(skipped=1)。

## 6. 环境与复现

- 平台：Linux aarch64（kernel 6.12.110+，Debian 13，glibc 2.41），GitHub 云端副本全新 clone。
- Python：全量套件 CPython 3.14.8（uv）+ jsonschema 4.26.0；变异脚本与提取脚本 CPython 3.12.15；浏览器探针 CPython 3.12.15 + Playwright 1.64.0。
- Node：v22.23.3。浏览器：Chromium headless shell 156.0.8078.4。
- 字体：Noto Sans CJK SC（云端下载安装），非 Windows 字体。
- 日志头里 `date` 的输出为沙箱 UTC 时钟（部分行显示 UTC），未换算；不据此推断本地取证时刻。
- 浏览器探针脚本随报告提交；提交后复验用它驱动全新 clone 中 068afb5 的 `gui/`（日志首行写明）。

```text
node tests/js/jh019_generation_harness.mjs
node tests/js/jh020_run_history_harness.mjs
node tests/js/hark05_run_selection_harness.mjs
python scripts/verify_jh020_run_history_frontend_guards.py
python scripts/verify_hark05_selection_guards.py
python reports/hark-gui-selection-20261020/browser_probe.py --gui gui --out <新目录> --label <名称>   # 需 playwright + chromium
bash reports/hark-gui-selection-20261020/extract_counts.sh
```

证据目录 `reports/hark-gui-selection-20261020/`：`logs/`（14 份原始 .txt，路径已脱敏为 `<WORK>`/`<HOME>`）、`browser/`（基线与提交后 JSON）、`screenshots/`（提交后 5 宽度 × viewport/selection 共 10 张）、`preview_server.py`、`browser_probe.py`、`run_suite.sh`、`extract_counts.sh`。日志不用 `.log`（仓库忽略 `*.log`）。

## 偏离清单

1. 单列详情区非空时加 `min-height: calc(100vh - 24px)`（.panel-history 限定，仅 ≤1100px）。派单只要求“标题进入可视区域”；无此规则时标题已可见但停在视口下部（top≈700/900）。属展示性新增，可由复核方要求撤回，撤回后定位判据仍成立。
2. 浏览器验证额外加了 1101/1100 两个宽度；必测的 1440/760/480 不变。
3. 新增文字“● 当前查看”（CSS 伪元素，只在选中卡片上）。不改变任何既有文字含义。
4. 为在云端渲染中文，沙箱安装了 Noto Sans CJK SC 字体和 Chromium 依赖库；不涉及仓库。
5. 无其它偏离；未触发停止条件（基线一致，无需改既有守卫/测试/后台契约或其它页面）。
