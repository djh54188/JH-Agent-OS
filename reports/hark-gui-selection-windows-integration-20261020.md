# 运行记录选中提示与窄窗口详情定位 · Windows 集成验证 · 2026-10-20

## 0. 范围理解

按 [Hark-派单-05 GUI 选择与详情定位独立复核](hark-gui-selection-independent-review-20261020.md)，从固定候选 `068afb504bfcffdcbb9ba45ce056fd61b6a916ef` 按原字节精准移植四文件到 `n1-artifact-entry`，完成 Windows 离线、完整套件和真实浏览器验证；不改候选实现、后台、HTTP、规则、既有测试/守卫或成功门，不接派发/审查。

## 1. 分级结论

- **实现完成**：四文件已按固定候选原字节落地；功能提交 `d5b67d9`（+582/−0，仅四文件）。
- **离线验证通过**：JH019 `4/4`、JH020 `13/13`、新选择行为 `11/11`；新定向削弱 `3/3` 断言检出；既有前端变异 `2` 检出 + `2` 冗余；完整套件提交前后均 `Ran 704 / 703 通过 / 1 既有跳过`，AST 独立计数 704。基线对照：同一新测试指向移植前 GUI 为 `1` 通过 / `10` 断言失败。
- **Windows 浏览器真实指针通过**：当前选择唯一（1 个 `aria-pressed=true`、1 个 `data-selected=true`）；切换记录时旧详情先清为加载态、迟到/错误响应不改选择与滚动；1440/1101 双列点击前后 `scrollY` 相等；1100/760/480 单列点击后详情标题可见（`top≈31`）；三种宽度无水平溢出；真实旧历史只读 `ok`/`count=1`/`verified`/`live=false`，字节未变。
- **待独立验收**：本批为 **Windows 集成候选**，不宣布界面视觉或产品闭环完成。

## 2. 提交号与改动清单

- 基线：`bdc843616ce8ff46b2bb250e89196755283f7793`
- 功能提交：`d5b67d90cc4b3770e671ef9418be0e813ee27a59`（父 `bdc8436`）
  - 更新 `gui/views.js`、`gui/styles.css`
  - 新增 `tests/js/hark05_run_selection_harness.mjs`、`scripts/verify_hark05_selection_guards.py`
- 报告提交：本文件所在提交（交付消息回填完整 SHA）；同提交含必要的 STATUS/TODO/CHANGELOG 状态同步。
- 未推送云端、未合并 main/PR #1；架构师交接与 `docs/review-backlog/` 未纳入提交。

## 3. 计数与证据

平台：Windows `10.0.26200.9168`；Python `3.14.7`；Node `v24.19.0`；浏览器为 ZCode 内置 IAB（Chromium）。

| 项目 | 结果 | 日志 / 证据 | 提取命令 |
|---|---|---|---|
| JH019 前端 | 4/4 | `data/hark-gui-selection-integration-20261020/logs/jh019-postcommit.json` | `node tests/js/jh019_generation_harness.mjs --json` |
| JH020 前端 | 13/13 | `.../logs/jh020-postcommit.json` | `node tests/js/jh020_run_history_harness.mjs --json` |
| 新选择行为 | 11/11 | `.../logs/hark05-postcommit.json` | `node tests/js/hark05_run_selection_harness.mjs --json` |
| 新定向削弱 | 3 变异 / 3 检出 / `pass` | `.../logs/hark05-guards-postcommit.json` | `python scripts/verify_hark05_selection_guards.py` |
| 既有前端变异 | 2 检出 + 2 冗余 / `pass` | `.../logs/frontend-guards-postcommit.json` | `python scripts/verify_jh020_run_history_frontend_guards.py` |
| 基线对照 | 1 通过 / 10 断言失败 | `.../logs/hark05-baseline-contrast.json` | `JH_GUI_DIR=<移植前 GUI 副本> node tests/js/hark05_run_selection_harness.mjs --json` |
| 完整套件（提交前/后） | Ran 704 / 703 通过 / 1 既有跳过 | `.../logs/precommit-suite.txt`、`postcommit-suite.txt` | `python -m unittest discover -s tests -p "test_*.py"`；`grep -E "^(Ran |OK|FAILED)" <日志>` |
| AST 独立计数 | 704 = 632 + 63 + 9 | `.../logs/ast-count.txt` | 脚本原文见下方 |

计数提取命令：

```bash
python -c "import json;d=json.load(open('data/hark-gui-selection-integration-20261020/logs/hark05-postcommit.json',encoding='utf-8'));print(d.get('ok'),sum(1 for v in d.get('scenarios',{}).values() if v.get('ok')),len(d.get('scenarios',{})))"
python -c "import json;d=json.load(open('data/hark-gui-selection-integration-20261020/logs/hark05-guards-postcommit.json',encoding='utf-8'));print(d['mutations'],d['detected'],d['verdict'])"
python -c "import json;d=json.load(open('data/hark-gui-selection-integration-20261020/logs/frontend-guards-postcommit.json',encoding='utf-8'));print(d['detected'],d['redundant'],d['verdict'])"
grep -E "^(Ran |OK|FAILED)" data/hark-gui-selection-integration-20261020/logs/postcommit-suite.txt
```

AST 计数脚本原文：

```python
import ast
from pathlib import Path
total = 0
for f in sorted(Path('tests').glob('test_*.py')):
    n = 0
    for node in ast.walk(ast.parse(f.read_bytes())):
        if isinstance(node, ast.ClassDef):
            n += sum(isinstance(i, (ast.FunctionDef, ast.AsyncFunctionDef))
                     and i.name.startswith('test') for i in node.body)
    total += n
print(total)  # 704
```

文件身份：四文件磁盘 SHA-256 与 Git blob 均与独立复核目标表一致（`gui/views.js` `f6eba51f…` / `1a2a1d68…`；`gui/styles.css` `cac2ee9c…` / `47227e20…`；`tests/js/hark05_run_selection_harness.mjs` `d650552e…` / `3a92829d…`；`scripts/verify_hark05_selection_guards.py` `2e0fa55f…` / `7e24bd31…`），核对记录在 `.../logs/file-identity.txt`。

### 浏览器矩阵（本地 Windows，IAB）

| 宽度 | 布局 | 选中唯一 | 详情标题可见 | 点击后 scrollY | 水平溢出 |
|---|---|---|---|---|---|
| 1440 | 双列 | 1 / 1 | 是（top 31） | 1008.67 → 1008.67（不滚动） | 无（1425/1440） |
| 1101 | 双列 | 1 / 1 | 是（top 31） | 1142 → 1142（不滚动） | 无 |
| 1100 | 单列 | 1 / 1 | 是（top 30） | 838 → 3779（定位到详情） | 无 |
| 760 | 单列 | 1 / 1 | 是（top 31） | 1331 → 4543（定位到详情） | 无（745/760） |
| 480 | 单列 | 1 / 1 | 是（top 31） | 1762 → 5814（定位到详情） | 无（465/480） |

- 切换记录（A→B）：点击后立即读到 `加载中正在读取运行记录…`（旧详情已清），稳定后唯一选中 `run-mismatch`，`scrollY` 不变（1508.67 → 1508.67）。
- 合成来源：`data/hark-gui-integration-20261019/fixtures/registry/runs.jsonl`（7 条合成 run，页内可见该路径）；截图 `synthetic-selection-*.png`。
- 真实来源：`data/jh020-r2-final-roundtrip-20261018/runs.jsonl`（源提交 `5b1dc26`）；截图 `real-history-1440.png`；查询 `ok`/`count=1`/`verified`/`live=false`，登记与原始证据字节未变，未调用模型。
- 汇总：`.../browser-audit-local.json`；单次测量：`evidence-1440-noscroll.json`、`evidence-1101-click.json`、`evidence-1100-click.json`、`evidence-760-click.json`、`evidence-480-click.json`、`evidence-1440-switch.json`、`evidence-real-history.json`。

说明：浏览器点击使用真实指针（Playwright locator 或 CUA 坐标点击，均先在 `elementFromPoint`/可见性上确认目标）；1440/1101 的“不滚动”用“先手动滚动到按钮可见、记录 scrollY、再点击”排除自动化工具自身的滚动。

## 4. 偏离清单

- **无新增偏离**。四文件即固定候选字节；既有 `loadDetail` 与四个关联守卫锚点未改；未改后台、HTTP、规则、既有测试/守卫或成功门。
- 承接独立复核接受的偏离①：`min-height: calc(100vh - 24px)` 仅限 `.panel-history .run-detail:not(:empty)`，只服务于点击后的标题定位，不改变数据、请求或权限。
- 环境记录：`core.autocrlf=true`，候选文件为 LF；磁盘字节与提交 blob 均已核对一致。

## 5. 故障类覆盖表

| 类别 | 覆盖情况 | 证据 |
|---|---|---|
| 无选择 | 点击前无选中标记 | `hark05` 场景 `no_selection`；截图 1440 详情前 |
| 当前选择唯一 | A→B、同项重复只保留一个 `aria-pressed`/`data-selected` | 浏览器五宽度读数；`a_to_b`、`repeat_same` |
| 乱序/迟到响应 | 迟到成功、迟到错误、错 run_id、离开重进不改选择与滚动 | `hark05` 11/11；`hark05-guards` 3/3 |
| 加载/错误/空详情 | 旧详情先清为加载态，错误/空不显示旧内容 | 浏览器 A→B 立即读数；`loading_error_empty` |
| 断点一致性 | JS 与 CSS 单列断点同为 1100px；1101 双列、1100 单列 | `breakpoints_1100_1101`、`css_breakpoint_and_scope`；浏览器 1100/1101 读数 |
| 宽度与溢出 | 1440/760/480 无水平溢出 | 浏览器读数与截图 |
| 只发既有查询 | 点击只发一次详情 GET，不发其他 API | `hark05` 场景；五宽度详情 GET 计数 |
| 真实历史 | 只读 `ok`/`count=1`/`verified`/`live=false`，字节未变 | `evidence-real-history.json`、`real-hash-before/after.txt` |
| 未覆盖 | 浏览器内错误帧未单独复现（本地只读面无可产生详情错误的 run）；键盘/读屏、加载中手动滚动等已披露未测项 | 独立复核已界定，不另开整改 |

## 6. 未做与阻塞

- 未调用真实模型、app-server 或任何 Agent 会话；未发送指定审查会话消息。
- 未接派发/审查、未改成功门、未启用 `production complete` 或 `FileToolExecutor`；未整分支合并、未推送云端、未合并 main/PR #1。
- 未提交架构师文件、历史归档或私有会话证据；旧证据目录未被覆盖，本批证据写入新目录 `data/hark-gui-selection-integration-20261020/`。
- 结论：**Windows 集成候选，待架构师独立验收。**

下一步路由：

架构师：独立验收 `d5b67d9` 与本报告；无新增裁决。
执行AI：等待验收结果；无后续动作。
Hark：等待 Windows 集成验收；无需发送。
用户：把本报告转给架构师；不需要技术决定。
一句话现状：运行记录选中提示与窄窗口详情定位已按固定候选移植，Windows 离线与真实浏览器验证通过，但尚未获架构师独立验收；派发、审查和生产门仍未接通。
