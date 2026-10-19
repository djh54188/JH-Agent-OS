# Hark｜Hark-派单-01｜交付：Agent/会话 历史运行列表 + 详情 视觉与信息层级优化

结论：**云端界面候选，待独立复核与 Windows 集成。**

- 基线：`1d7605dc65d853543f9702025a2f5d0e04265c16`，已核对 `AGENTS.md` SHA-256 为 `edc9846b33c908ee6f16597cd2dd920f1c0564489271713a027bbe7e24ea74b3`，与派单一致。
- 分支：`hark-gui-sessions-20261019`，从基线新建。
- 功能提交：`d000f0cdbbf73781cde30fbd2174aaff6fe7248a`；本报告单独作为后一提交。
- 环境：Linux aarch64 云端沙箱，Python 3.14.8，Node v22.23.3，Playwright Chromium headless shell 156。**不是 Windows。**

## 0. 范围理解

只做展示层改动：让 Agent/会话 页一眼区分「模拟登记 / 历史运行 / 证据状态 / 执行方声明」，并改善布局与长标识的可读性。不改数据、接口、请求代次、响应关联、点击委托，也不改任何已有测试。
派单写的是「JH-020 十三场景」，但前端测试列表里只有 7 个行为场景，另外 6 个是 `detail_*` 关联场景。我把 `tests/js/jh020_run_history_harness.mjs` 里 `SCENARIOS` 数组的 13 项全部当作必须通过的对象。

## 1. 分级结论

| 项 | 结论 |
|---|---|
| 实现完成 | 是（仅视觉切片） |
| 离线验证通过 | 是：JH-019 4/4，JH-020 13/13，前端变异 2 检出、2 冗余，全量套件失败集合与基线逐行一致（见 §3） |
| 真实路径通过 | 不适用。本批没有任何真实派发或模型调用；预览用的是真实 `src.gui_http.make_server`，但登记数据为合成 |
| 待独立验收 | 是，交架构师复核；Windows 集成未做 |

## 2. 提交号与改动清单

功能提交 `d000f0c`，相对基线：

| 文件 | 性质 |
|---|---|
| `gui/styles.css` | 重写样式：配色变量、排版、留白、标签与状态样式、列表和详情双栏网格、窄窗口断点（1100 / 760 / 480 px）、详情空状态占位（纯 CSS `:empty::before`） |
| `gui/components.js` | 纯展示：`field()` 增加可选的 `{mono:true}`（等宽字体、任意位置折行，并带 `title` 悬浮显示全文）；新增 `kindTag`、`legend`、`stateIcon`；`connectionNotice`、`runSourceNotice`、`evidenceNotice` 增加文字符号（✓ ✕ ○ ? ◇ ●）和 `dataset.state`。所有原有文字保持不变 |
| `gui/views.js` | 只改 `sessions()`、`renderRunCard()`、`renderRunDetail()` 的 DOM 结构：模拟登记网格、历史区标题标签、`run-layout` 包裹列表和详情、详情字段按含义分组（字段集合不变）、「执行方声明」标签。`loadDetail`、`refresh`、点击委托、四个代次和关联守卫的行都没有改动 |
| `tests/visual/jh_gui_synthetic_preview.py` | 新增视觉验证工具（不是测试，不匹配 `test_*.py`）。它在临时目录写合成登记，启动真实 `make_server`，用 Chromium 截图；截图上下各注入一条「合成预览，非真实运行」横幅（只在预览页注入，不进入 `gui/`） |
| `reports/assets/hark-gui-sessions-20261019/**` | 改前和改后截图、操作记录、日志 |

未改动：`src/`、`gui/data_access.js`、`gui/index.html`、`tests/js/*`、`tests/test_*.py`、`scripts/*`、`main`、`n1-artifact-entry`、PR #1。

## 3. 计数（日志路径与提取命令）

日志目录统一为 `reports/assets/hark-gui-sessions-20261019/logs/`。本地 `data/` 已被 .gitignore 忽略，所以日志放在 reports 下。

| 数字 | 值 | 日志 | 提取命令 |
|---|---|---|---|
| JH-019 前端场景 | 4/4，改前、改后、提交后三次 | `baseline-jh019.json`、`after-jh019.json`、`postcommit-jh019.json` | `node tests/js/jh019_generation_harness.mjs --json`，然后统计 `results` 中 ok 的数量 |
| JH-020 前端场景 | 13/13，改后、提交后两次；改前 13 行均为 ok | `baseline-jh020.txt`、`after-jh020.json`、`postcommit-jh020.json` | `node tests/js/jh020_run_history_harness.mjs --json`，然后统计 `scenarios` 中 ok 的数量 |
| 详情关联前端变异 | mutations=2、detected=2、redundant=2，verdict=pass，改前、改后、提交后三次 | `baseline-guards.json`、`after-guards.json`、`postcommit-guards.json` | `python scripts/verify_jh020_run_history_frontend_guards.py` |
| 全量套件（带沙箱代理） | Ran 632；failures=27、errors=78、skipped=2，改前和改后相同 | `baseline-suite.txt`、`after-suite.txt` | `python -m unittest discover -s tests -p "test_*.py"`，然后执行 `grep -E "^(Ran\|OK\|FAILED)"` |
| 全量套件（去掉 HTTP 代理） | Ran 632；failures=21、errors=63、skipped=2，改前和改后相同 | `baseline-suite-noproxy.txt`、`after-suite-noproxy.txt` | 用 `env -u HTTP_PROXY -u HTTPS_PROXY -u http_proxy -u https_proxy` 运行同一条命令 |
| 失败集合对比 | 逐行完全相同（两组环境都一致） | `*-failset*.txt` | `grep -E "^(FAIL\|ERROR):" <suite.txt> \| sort`，然后 `diff baseline after` |
| AST 独立测试计数 | 632，与 Ran 一致 | 脚本原文：`logs/ast_count.py` | `python reports/assets/hark-gui-sessions-20261019/logs/ast_count.py tests` |

`ast_count.py` 原文（统计 `tests/test_*.py` 中顶层 TestCase 类里以 `test` 开头的方法，不使用 grep）：

```python
import ast, pathlib, sys
root = pathlib.Path(sys.argv[1] if len(sys.argv) > 1 else "tests")
total = 0
for path in sorted(root.glob("test_*.py")):
    tree = ast.parse(path.read_bytes(), filename=str(path))
    for node in tree.body:
        if isinstance(node, ast.ClassDef):
            total += sum(1 for item in node.body
                         if isinstance(item, (ast.FunctionDef, ast.AsyncFunctionDef))
                         and item.name.startswith("test"))
print(total)
```

失败的说明：Linux 下剩余的 84 项失败集中在 `test_jh006_*`、`test_phase2_hardening`、`test_phase4_file_tools`、`test_jh020_run_history_api.TestEvidence*` 等。这些测试依赖 Windows 路径语义或本地会话存储，与 GUI 无关，并且在基线上同样失败。带代理时多出的 21 项（包括 `test_jh019_gui_http`、`test_jh020_run_history_http`）是沙箱 HTTP 代理拦截了 loopback 请求，去掉代理后即通过。**这些失败不代表 Windows 结果。**

协议调用口径：本批发起的 `initialize` / `thread/start` / `turn/start` 为 0 次，没有任何真实派发，也没有调用模型。

## 4. 偏离清单

无。没有修改已验收的测试、守卫或契约，没有改变字段语义，没有删除函数，也没有增加操作能力。
需要登记的一点：`components.js` 是公共组件。`field()` 增加了可选的第三个参数；`connectionNotice` 的未连接配色从 `notice-warn` 改为新的 `notice-sim`，文字不变。另外三个视图仍然调用两参数形式，行为不变。

## 5. 故障类覆盖表（展示层输入空间）

| # | 输入类 | 覆盖 | 依据 |
|---|---|---|---|
| 1 | 登记状态 ok | 是 | 截图 `after-*-sessions*.png`；harness `history_card_fields` |
| 2 | 登记 absent / empty | 部分 | harness `absent_source_is_not_an_error` 通过；预览只截了 ok 源，没有截 absent 或 empty 的图 |
| 3 | 登记 corrupt / unreadable | 部分 | harness `corrupt_source_shows_its_message` 通过；CSS 用 `data-state` 显示红色加 ✕；没有截图 |
| 4 | 证据 verified | 是 | 截图（run 0001） |
| 5 | 证据 mismatch | 是 | 截图（长 id run，文件被篡改） |
| 6 | 证据 missing | 是 | 截图（run 0003，没有 collected 记录） |
| 7 | 证据 outside_root / invalid / invalid_content / unverifiable | 否（样式已定义） | 没有合成对应数据；颜色只做辅助，状态词和符号始终显示 |
| 8 | 长 run / thread / turn / fingerprint 标识 | 是 | 1440 和 760 两种宽度都没有横向溢出（`after-actions.json` 中 `horizontal_overflow=false`） |
| 9 | dispatch_uncertain 运行 | 是 | 截图（徽标加文字） |
| 10 | 详情：未选择 / 加载中 / 错误 / 空 | 部分 | 未选择：CSS 占位（截图 `after-desktop-sessions.png`）；加载、错误、空状态的样式已改，但只有 harness 覆盖文字，没有截图 |
| 11 | 详情：迟到或乱序响应、不符的 run_id | 是（未改逻辑） | harness 6 个 `detail_*` 场景加变异 2/2 |
| 12 | 另外三个视图的回归 | 部分 | 截图只覆盖空数据库和未选择任务的状态；没有合成任务库，所以有数据时的任务卡和交付页未截图 |

## 6. 未做与阻塞

- **Windows 未验证**：没有在 Windows 上运行套件和真实浏览器，也没有使用 Segoe UI / 微软雅黑实际渲染。云端截图用的是文泉驿正黑，粗体不明显，中文字重会和 Windows 不同。
- **选中态高亮未做**：如果要在列表里高亮当前选中的卡片，需要改点击委托或 `loadDetail`，属于禁改范围。现在靠详情标题里的完整 run_id 来对应。
- **窄窗口**（≤1100px）时详情排在列表下方，点击后需要向下滚动。自动滚动需要改点击逻辑，所以没有做。
- 预览只合成了 3 条运行和 2 个模拟会话；任务库不存在，回归覆盖范围有限（见 §5 第 12 项）。
- 没有改 `CHANGELOG.md`、`STATUS.md`、`TODO.md`（不在允许范围内）。

## 截图索引（全部为合成预览，非真实运行）

`reports/assets/hark-gui-sessions-20261019/{before,after}/`：

- `*-desktop-sessions.png`、`*-narrow-sessions.png`：会话页（宽 1440 / 760）。
- `*-desktop-sessions-detail.png`、`*-narrow-sessions-detail.png`：用真实指针点击长 id 运行的「查看记录详情」之后的整页截图。
- `*-desktop-detail-pane.png`、`*-narrow-detail-pane.png`：详情窗格的元素截图（视口高 2600，完整显示）。
- `*-{workbench,task,delivery}.png`：另外三个视图的回归截图。
- `*-actions.json`：实际操作记录（点击顺序、宽度、是否横向溢出）。

预览数据：`tests/visual/jh_gui_synthetic_preview.py` 里的 `build_registry()`，run_id 都带 `SYNTH` 字样。证据文件以字节写入，哈希对磁盘回读的字节计算；mismatch 用例是在计算摘要之后追加一行来篡改。所有合成数据随临时目录删除，不接入任何生产路径。

## 7. 下一步路由

- 架构师：独立复核 `d000f0c`（视觉候选），裁决是否准许移植到本地；另外确认 §5 中「部分」的几项是否需要补截图。
- 执行AI：等待派单。若架构师准许，再按精准移植三个 `gui/` 文件，在 Windows 跑套件和真实浏览器，确认中文字体渲染。
- Hark：无，等待裁决。
- 用户：把本报告末尾的转发块转给架构师。

白话现状：会话页的新样式已经在云端做好，能一眼分清模拟、历史、证据和声明，长 id 也不会把页面撑破；还没在你的 Windows 上跑过。
