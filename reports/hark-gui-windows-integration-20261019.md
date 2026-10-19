# Hark GUI 候选 Windows 集成验证 · 2026-10-19

## 0. 范围理解

仅把固定功能候选 `d000f0cdbbf73781cde30fbd2174aaff6fe7248a` 的三个 GUI 文件按原字节移植到 `n1-artifact-entry`，并在 Windows 上完成前端行为、变异、浏览器矩阵和完整套件回归；不改接口、既有测试/守卫、规则、生产门或云端。

## 1. 分级结论

- **实现完成**：`gui/styles.css`、`gui/components.js`、`gui/views.js` 已按候选原字节落地并提交 `b31ce3c82d240a19a72b987b81fdb0dce856d02b`。
- **离线验证通过**：JH019 `4/4`，JH020 `13/13`，前端定向变异 `2` 检出 + `2` 冗余（冗余不计检出），完整套件提交前后均为 `Ran 632 / 631 通过 / 1 既有跳过`，AST 独立计数 `632`。
- **Windows 浏览器路径通过**：使用真实指针点击完成页面切换与运行详情选择；1440/760/480 三种宽度无页面横向溢出，长 run/thread/turn 标识可完整读取；桌面详情内部滚动可用，窄窗口可手动滚动到详情；真实登记页面证据为 `verified`。
- **待独立验收**：本批为 **Windows 集成候选**，未获架构师独立验收；不宣布 Hark 候选或界面完成。

## 2. 提交号与改动清单

- 功能提交：`b31ce3c82d240a19a72b987b81fdb0dce856d02b`
- 父提交：`1d7605dc65d853543f9702025a2f5d0e04265c16`
- 改动文件（仅三个）：
  - `gui/styles.css`
  - `gui/components.js`
  - `gui/views.js`
- 报告提交：本文件所在提交（交付消息回填完整 SHA）。
- 未纳入本批提交、保持原样：架构师未提交的 `docs/handoffs/architect-current.md`、未跟踪的 `docs/review-backlog/`。
- 未推送云端；本地无 remote 配置，交付时相对 `remotes/github/n1-artifact-entry` 的记录仅用于说明未同步。

## 3. 计数与证据

平台：Windows `10.0.26200.9168`；Python `3.14.7`；Node `v24.19.0`；浏览器为 ZCode 内置 IAB，UA 含 `ZCode/3.15.3 Chrome/146.0.7680.80 Electron/41.0.3`。

| 项目 | 结果 | 原始日志 | 提取命令 |
|---|---|---|---|
| 提交前完整套件 | Ran 632 / 631 通过 / 1 跳过 | `data/hark-gui-integration-20261019/logs/pre-suite.txt` | `grep -E "^(Ran |OK|FAILED)" data/hark-gui-integration-20261019/logs/pre-suite.txt` |
| 提交前（移植后）完整套件 | Ran 632 / 631 通过 / 1 跳过 | `data/hark-gui-integration-20261019/logs/post-suite.txt` | `grep -E "^(Ran |OK|FAILED)" data/hark-gui-integration-20261019/logs/post-suite.txt` |
| 提交后完整套件 | Ran 632 / 631 通过 / 1 跳过 | `data/hark-gui-integration-20261019/logs/post-commit-suite.txt` | `grep -E "^(Ran |OK|FAILED)" data/hark-gui-integration-20261019/logs/post-commit-suite.txt` |
| AST 独立计数 | 632 | `data/hark-gui-integration-20261019/logs/ast-count.txt` | 见下方脚本原文 |
| JH019 前端行为 | 4/4，`ok=true` | `data/hark-gui-integration-20261019/logs/jh019-postcommit.json` | `python -c "import json;d=json.load(open('data/hark-gui-integration-20261019/logs/jh019-postcommit.json',encoding='utf-8'));print(d['ok'],len(d['scenarios']),sum(1 for v in d['scenarios'].values() if v['ok']))"` |
| JH020 前端行为 | 13/13，`ok=true` | `data/hark-gui-integration-20261019/logs/jh020-postcommit.json` | `python -c "import json;d=json.load(open('data/hark-gui-integration-20261019/logs/jh020-postcommit.json',encoding='utf-8'));print(d['ok'],len(d['scenarios']),sum(1 for v in d['scenarios'].values() if v['ok']))"` |
| 前端定向变异 | 2 检出 / 2 冗余 / 1 基线控制，`verdict=pass` | `data/hark-gui-integration-20261019/logs/frontend-guards-postcommit.json` | `python -c "import json;d=json.load(open('data/hark-gui-integration-20261019/logs/frontend-guards-postcommit.json',encoding='utf-8'));print(d['controls'],d['detected'],d['redundant'],d['verdict'])"` |

AST 计数脚本原文：

```python
import ast
from pathlib import Path
n = 0
for f in sorted(Path('tests').glob('test_*.py')):
    for node in ast.walk(ast.parse(f.read_bytes())):
        if isinstance(node, ast.ClassDef):
            n += sum(isinstance(i, (ast.FunctionDef, ast.AsyncFunctionDef))
                     and i.name.startswith('test') for i in node.body)
print(n)  # 632
```

说明：完整套件日志末尾的 `{"captured": false, ...}` 是既有跳过项的诊断输出，不是测试失败；`OK (skipped=1)` 与 `Ran 632` 是权威运行结果。计数未变化，未通过修改统计或守卫凑数。

### 文件身份

| 文件 | 磁盘 SHA-256 | 提交 blob SHA-1 | 固定候选 blob |
|---|---|---|---|
| `gui/styles.css` | `b36f995605faedf1f28bfeef614ebb64b7c95511feb8968950f5ffcaa869875a` | `13b7ae9a9fada1d71ea72bea228c978dd92cbbe6` | `13b7ae9a9fada1d71ea72bea228c978dd92cbbe6` |
| `gui/components.js` | `b37ebdfc5508da87c9af313995a3324ca2cb8c087bcd369787e53a91e6c5b673` | `304bc5f68dd60196659cd566aeb5e4891929cb34` | `304bc5f68dd60196659cd566aeb5e4891929cb34` |
| `gui/views.js` | `1b198c4b9691843a10af01b6294e00a99646f6913920d0f807e7b4422079c47b` | `89f698ec678d6703de5ec955743684bf5881eed2` | `89f698ec678d6703de5ec955743684bf5881eed2` |

提取命令：

```bash
sha256sum gui/styles.css gui/components.js gui/views.js
git ls-tree HEAD -- gui/styles.css gui/components.js gui/views.js
```

`gui/data_access.js`、`gui/index.html`、`src/`、`tests/`、`scripts/`、`.gitignore` 和规则文件均未在本批改动。四个前端变异锚点在移植后仍各唯一（计数均为 1）。

## 4. 偏离清单

**无偏离**：未修改已验收测试、守卫或契约，未改变既有字段语义，未删除函数，未改业务源码/接口/规则/生产门。

环境记录：`core.autocrlf=true`，候选文件为 LF；当前磁盘字节和提交 blob 均已核对与候选一致。未来如发生 Git checkout 重建工作树，按仓库配置可能变为 CRLF，这是环境行为，不是本批语义修改。

## 5. 故障类覆盖表

| 类别 | 输入/注入 | 预期 | 证据 | 覆盖 |
|---|---|---|---|---|
| 登记 ok | 隔离合成登记，7 条 run | 来源 `ok`，历史列表 7 卡 | `evidence-1440-layout.json`、`synthetic-history-1440.png` | 已覆盖 |
| 登记 absent | 不存在的登记路径 | `absent` + 空状态，不伪造列表 | `evidence-source-absent.json`、`synthetic-source-absent-1440.png` | 已覆盖 |
| 登记 empty | 0 字节登记文件 | `empty` + 空状态 | `evidence-source-empty.json`、`synthetic-source-empty-1440.png` | 已覆盖 |
| 登记 corrupt | 非法 JSON 行 | `corrupt` + 原始错误文本 | `evidence-source-corrupt.json`、`synthetic-source-corrupt-1440.png` | 已覆盖 |
| 登记 unreadable | 以目录充当登记文件路径 | `unreadable` + 原始 OSError | `evidence-source-unreadable.json`、`synthetic-source-unreadable-1440.png` | 已覆盖 |
| 证据 verified | 摘要与帧数匹配 | `verified` | 合成列表首卡；`evidence-1440-layout.json` | 已覆盖 |
| 证据 mismatch | 错误摘要 | `mismatch` | `evidence-1440-detail.json`、分段截图 | 已覆盖 |
| 证据 missing | 无 path | `missing` | 同上 | 已覆盖 |
| 证据 outside_root | 允许根之外路径 | 拒绝读取，`outside_root` | 同上 | 已覆盖 |
| 证据 invalid | path 为整数 | `invalid`，不抛异常 | 同上 | 已覆盖 |
| 证据 invalid_content | 摘要匹配但非 UTF-8 | `invalid_content` | 同上 | 已覆盖 |
| 证据 unverifiable | 证据路径为目录 | `unverifiable` | 同上 | 已覆盖 |
| 详情未选择 | 未点击任何“查看记录详情” | 空详情占位 | `synthetic-evidence-cards-top-1440.png` | 已覆盖 |
| 详情加载态 | 点击后、响应前 | 显示加载占位 | 代码路径/Harness；本地响应过快，未截取瞬时帧 | **已知未覆盖（浏览器帧）** |
| 详情错误 | 迟到错误、错 run_id | 不覆盖/不清空当前详情 | JH020 `detail_late_error`、`detail_unrequested_run_id` 场景 | 离线已覆盖；浏览器未单列截图 |
| 详情乱序/跨页 | A/B 逆序、同 id 重复、离开/重建页面 | 最新请求生效，旧响应惰性 | JH020 `detail_reverse_replies`、`detail_same_id_repeat`、`detail_leave_view`、`detail_reenter_view` | 离线已覆盖 |
| 长标识 | 长 run/thread/turn | 完整读取、自动换行、无横向溢出 | `evidence-1440-layout.json`、`evidence-760-layout.json`、`evidence-480-layout.json` 及对应截图 | 已覆盖 |
| 三种宽度 | 1440 / 760 / 480 | `scrollWidth <= innerWidth` | 1440: 1425/1440；760: 745/760；480: 465/480 | 已覆盖 |
| 桌面详情滚动 | 1440 | 内部滚动可用 | `detailClientHeight=875`、`detailScrollHeight=1356`、`overflow-y:auto` | 已覆盖 |
| 公共 CSS 其他页 | 工作台/任务详情/审查交付，有数据 | 可读、无横向溢出 | `workbench-data-1440.png`、`task-detail-data-1440.png`、`delivery-data-1440.png` | 已覆盖（1440） |
| 真实登记 | `data/jh020-r2-final-roundtrip-20261018/runs.jsonl`（源提交 `5b1dc26`） | `ok` + `verified`，只读 | `evidence-real-registry.json`、`real-history-1440.png` | 已覆盖 |

真实登记页读数：run `codex-run-1-1779aa1b`，thread `01a14eef-1d40-7cc0-8bf6-ff268e7ad8ad`，turn `01a14eef-2206-7791-b923-1a5b747dd4c4`，证据 `verified`，`frames=26`、`bytes=8868`。未调用模型、未启动 app-server、未发送会话消息。

合成数据只写入隔离目录 `data/hark-gui-integration-20261019/`，并明确命名为合成夹具。1440 顶部截图在页内可见合成登记路径；760/480 的详情局部截图与 1440 合成来源截图成对交付，文件名为 `synthetic-*`。全页截图在 IAB 下出现重复拼接，已删除，未作为证据。

## 6. 未做与阻塞

- 未调用真实模型、app-server 或任何 Agent 会话；未发送指定审查会话消息。
- 未接 GUI 派发/collect/continue/cancel，未改成功门，未启用 `production complete` 或 `FileToolExecutor`。
- 未推送云端、未合并 main/PR #1、未整分支合并。
- 未改既有测试/守卫/规则；未提交架构师交接和用户归档。
- 480 宽度下 Playwright locator click 对该页报 `no click point`（IAB 指针探针问题）；已在 `elementFromPoint` 确认按钮可见且未被遮挡后改用 CUA 指针点击，页面切换和详情选择均生效。这是浏览器自动化工具路径差异，不是产品按钮失效；760/1440 使用 Playwright locator click。
- 已知未覆盖：浏览器瞬时加载帧、浏览器单列详情错误帧；UNC/设备路径/ADS/并发替换未在本批扩展复测；IAB 全页截图不可用。
- 本报告结论为 **Windows 集成候选，待架构师独立验收**。

下一步路由：

架构师：独立验收 `b31ce3c` 与本报告；无新增裁决。
执行AI：等待验收结果；无后续动作。
Hark：等待 Windows 集成验收；无需发送。
用户：把本报告转给架构师；不需要技术决定。
一句话现状：三个 GUI 文件已按固定候选原字节移植并提交，Windows 下离线与浏览器矩阵通过，但尚未获架构师独立验收；真实派发、自动审查和生产门仍未接通。
