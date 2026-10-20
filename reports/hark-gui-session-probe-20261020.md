# Hark-派单-06 交付报告：GUI 真实会话试运行（固定标识往返）

来源：Hark（执行角色）｜Hark-派单-06｜类型：交付报告
结论：**云端候选，真实 Windows 路径未验证。** 不是验收。本批未调用真实模型；浏览器证据全部是 Linux 合成预览（离线假传输）。

## 0. 身份与基线

- 仓库 `djh54188/JH-Agent-OS`；基线 `6725d60796256a59577d6e50e7732b5145739d4a`（核对一致）
- AGENTS.md SHA-256 `edc9846b33c908ee6f16597cd2dd920f1c0564489271713a027bbe7e24ea74b3`
- 分支 `hark-gui-session-probe-20261020`（自基线新建）
- 功能提交 `1a0c7369bdff3c2d05d2e157e5bf95052bd08275`
- 守卫偏离提交（单独） `8a20ab66eff2cc8bd746af89293db4a1390f0fd8`
- 报告提交：本文件所在提交
- 未改 main、n1-artifact-entry、PR #1；未合并；未 force push。

## 1. 实现

| 层 | 文件 | 内容 |
|---|---|---|
| 应用服务 | `src/session_probe.py`（新增） | `SessionProbeService`：状态 / prepare / start；构造固定请求交给真实 `CodexAgentSessionAdapter`；后台线程执行 submit+collect 一次 |
| 统一接口 | `src/app_api.py` | 持有服务；`session_probe_status / prepare_session_probe / start_session_probe`；能力面新增 `session_probe`（默认 unsupported） |
| 传输 | `src/gui_http.py` | `GET /api/session-probe`、`POST /prepare`、`POST /start`；动作路由在读体前做同源/环回 Host/JSON 校验；`--enable-session-probe` 等服务端开关 |
| GUI | `gui/index.html`、`data_access.js`、`views.js`、`styles.css` | “会话试运行”页签：可用性、将调用什么、勾选确认、单次按钮、回复（文本）、用量、证据、未验证项 |

流程：页面只读状态 → 用户勾选确认并点击 → `prepare` 签发一次性 16 位十六进制 probe_id（10 分钟、仅内存，不写盘、不派发）→ `start {probe_id, confirm:true}` → 服务以 probe_id 为幂等键、`new:gui-probe-<id>` 为新会话地址调用适配器 → 结果从**试运行专用登记**用已验收的只读读取器（`parse_registry_text` + `check_history` + `raw_evidence_state`）读回。

服务端决定：模型（默认 `gpt-6.1-sol`）、实例 `codex-app-server/local`、role worker、隔离工作区 `<probe_dir>/ws/<probe_id>`、登记 `<probe_dir>/probe-runs.jsonl`、证据 `<probe_dir>/raw/`、指令 `Reply with exactly this token and nothing else: JH-PROBE-<ID>. Do not use any tools, do not read or write files.`、budget_tokens=None。沙箱/网络/审批沿用适配器既有设置（thread read-only、turn readOnly+networkAccess=false、approvalPolicy never）。

未改：`agent_session.py`、`agent_session_codex.py`、`run_history_consistency.py`、核心状态机/审核/预算、既有历史只读路径；通用 dispatch/collect/review_trigger 仍 unsupported；静态会话登记仍 connected=false。

## 2. 验收重点对照

1. **零派发**：打开/刷新/查询、未启用、未确认（None/False/"true"/1/"yes"）、多余字段（instructions/workspace/session_id/model/path/command/registry/target）、未签发或过期 id、跨源/缺 Origin/null/他端口/非环回 Host/非 JSON/Sec-Fetch-Site=cross-site → thread/start 0 次，试运行目录不创建。
2. **至多一次**：同 id 重复、8 线程并发、进行中再确认、服务重开后同 id、重开后旧 prepare id → thread/start 恒为 1（或 0）。thread/start 超时、turn/start 超时、collect 超时、崩溃遗留 reserved → `unknown_manual`，阻断后续试运行（含重开），不取消、不完成、不重发。
3. **拒发**：坏 JSON、字段非法、历史矛盾、登记不可读、试运行登记与历史来源同一文件、服务端要求有限预算、运行时缺失、登记不可写（适配器 RegistryUnavailable，传输零调用）、目录为链接 → 拒发且登记字节不变。
4. **绑定**：回复与用量只取绑定本次 thread/turn 的通知；外来回复/用量不采用，用量显示“未知，不按 0 计”；指令须与 probe_id 生成的指令一致；回显须恰为固定标识；证据摘要与磁盘字节一致（篡改→mismatch）；原始流中出现工具条目→`round_trip_not_proven`；隔离工作区文件列出。
5. **无副作用**：历史登记字节不变；只写试运行目录；完成显示“试运行往返完成（不等于任务验收成功）”，状态中无 succeeded。
6. **回归与反控**：见第 3 节。

## 3. 测试与日志（`reports/hark-gui-session-probe-20261020/logs/`，计数可由 `extract_counts.sh` 重算）

| 项 | 基线 6725d60 | 提交后 8a20ab6 |
|---|---|---|
| 新 Python 测试 `tests/test_hark06_session_probe.py` | — | 40/40 OK |
| 新 JS harness `tests/js/hark06_session_probe_harness.mjs` | — | 13/13 ok |
| JH018 契约 | 42 OK | 46 OK（+4 偏离守卫测试） |
| JH019 harness | 4/4 | 4/4 |
| JH020 历史 harness | 13/13 | 13/13 |
| Hark05 选择 harness | 11/11 | 11/11 |
| 前端变异 | 2 detected + 2 redundant pass | 同 |
| Hark05 削弱 | 3/3 pass | 同 |
| JH020 会话守卫 | 17 detected pass | 同 |
| JH020 历史守卫 | exit 1（6 detected、1 redundant、2 control_failed，既有 Linux 情形） | 逐字节同基线 |
| 全量 无代理 | 704 / F21 E63 S2 | 748 / F21 E63 S2 |
| 全量 有代理 | 704 / F27 E78 S2 | 748 / F27 E78 S2 |

全量失败 ID 集合：无代理 85、有代理 106，基线与提交后逐条相同（既有 Linux/代理问题，不代表 Windows）。748 = 704 + 40 + 4。

**守卫削弱** `scripts/verify_hark06_session_probe_guards.py`：16 detected、1 redundant、verdict pass。每项先跑控制组（绿），再单独削弱，只有断言失败计为检出：

- Python（进程内 mock，不改 src）：启用开关、确认、字段白名单、同源校验、进行中阻断、未解决阻断、登记状态、历史来源冲突、预算、用量绑定、回显核对、工具条目 → 12/12 检出。
- `replay_check_removed` 记为 redundant：单次签发 id 用后即删，另有适配器幂等索引；诊断证明削弱后第二次 start 返回 `probe_not_prepared`、thread/start 仍为 1，不计入检出。
- JS（临时 gui 副本，不改 gui/）：双击保护、确认勾选、离开视图后不发 start、轮询上限 → 4/4 检出。

## 4. 合成 GUI 证据（`browser/preview-results.json`、`screenshots/`）

真实 `src.gui_http` + 真实 `gui/`，试运行启用，但 Codex 进程换成离线假传输；页面明示“适配器：离线/合成注入（非真实模型结果）”，截图标注“Linux合成预览（离线假传输，未调用真实模型），未作Windows验收”。Chromium 156.0.8078.4，真实鼠标。

| 场景 | 宽度 | POST | thread/start | 结果 |
|---|---|---|---|---|
| 打开+只读刷新 | 1440 / 760 | 0 | 0 | 按钮禁用 |
| 未勾选，按坐标点禁用按钮 | 1440 / 760 | 0 | 0 | — |
| 勾选后双击 | 1440 / 760 | prepare 1 + start 1 | 1 | round_trip_complete |
| 服务端未启用 | 1440 | 0 | 0 | 显示 probe_disabled |
| thread/start 超时 | 1440 | prepare 1 + start 1 | 1 | unknown_manual，之后阻断 |

所有场景 overflowX=0。

## 5. 偏离清单（待裁决）

1. **JH-018 守卫调整（单独提交 8a20ab6）**。旧限制：只有真实适配器可导入 `src.agent_session`。新允许：仅 `src/session_probe.py` 可用普通绝对形式导入 `RunState/SessionAddress/SubmitRequest`；不得继承 AgentSessionAdapter；只有 `src/app_api.py` 可导入该服务。反控覆盖 module/alias/parent/relative/dynamic/submodule 形式、更宽名字，以及核心模块导入服务。既有测试名与扫描形式未改；没有借道导入或动态导入。
2. **测试专用 POSIX 证据接缝**。已验收证据策略按 Win32 判定，Linux 上所有 `/...` 路径都算 root-relative，因而不可能 verified。新测试与合成预览在非 Windows 下把普通 POSIX 绝对路径放行到**同一**套包含/链接/摘要检查；UNC/设备/ADS 仍由原函数拒绝；Windows 下原函数不变。产品代码未改。
3. **能力面与页头**。新增 `session_probe` 能力行（默认 unsupported）；启用时 capabilities note 与页头徽标改为“会话试运行已启用（逐次确认后调用真实模型）”，避免继续显示“不触发执行”。
4. **一次性 probe_id 仅存内存**。服务重开后需重新签发（`probe_not_prepared`），防止重开后静默重发。
5. 沙箱装了 Noto CJK 字体、Chromium 依赖和 pwenv 里的 jsonschema，不涉及仓库。

没有放宽成功门，没有改会话契约语义，没有扩大到一般派发；未触发停止条件。

## 6. 未覆盖

- 真实 Codex app-server / 真实模型往返（按派单留到 Windows 集成批）。
- Windows 本机、Windows 路径语义（证据接缝在 Windows 下不生效，需真实验证）、Windows 字体与缩放。
- 多进程同时打开同一试运行目录（只做到单进程锁 + 登记阻断）。
- 键盘/读屏、prefers-reduced-motion。
- 真实 GUI 往返证据须在 Windows 集成批通过真实 GUI 入口重新取得，不能用旧 CLI 往返证据代替。

## 7. 下一步路由

- 架构师：独立复核 1a0c736、8a20ab6，并裁决偏离 1–4。
- 执行AI：复核通过后，在 Windows 用 `--enable-session-probe` 通过 GUI 跑一次真实固定标识往返。
- Hark：待命，不再改仓库。
- 用户：转发本报告即可，无需判断。
