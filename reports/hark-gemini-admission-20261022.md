# Hark-派单-12 交付报告：Gemini 首次派发/收集准入整改（R1–R3）

来源：Hark（执行角色）｜Hark-派单-12｜类型：交付报告

环境：GitHub 云端副本 djh54188/JH-Agent-OS，Linux aarch64 沙箱，CPython 3.14.8（uv），Gemini CLI 0.65.0（隔离安装，仅宿主探测用，经沙箱合成端点）。真实模型调用 0；未读取/申请任何 key；未访问用户本机。**无 Windows 实测。**
规则：AGENTS.md v2（SHA-256 edc9846b33c908ee6f16597cd2dd920f1c0564489271713a027bbe7e24ea74b3，已核）→ docs/collaboration-rules.md v2。
基线：7a41ac1673adb5cf202b12a25c6c83ae8fdbb659；分支 hark-gemini-subagent-20261021，开工时远端 HEAD = 基线，无并行后续工作。

## 0. 范围理解

只关闭架构师给出的 R1（登记/幂等）、R2（观测/证据形状）、R3（路径/夹具编码）反例，使 Gemini 薄桥成为可交本地验证的候选；不新增能力，不动 Codex 适配器、AgentSession 契约、核心门、旧测试与守卫，不重做已接受的 070004f。

## 1. 分级结论

| 层级 | 结论 |
|---|---|
| 实现 | 完成（候选，待独立复核）。 |
| 离线 | 新增 32 测连续 3 次全过；派单-11 原 32 测＋JH018 契约 42 测＝74 全过；7 个新守卫定向削弱 7/7 被检出且对照先有效；完整套件 736→768，Linux 既有失败集合与基线日志逐行相同。 |
| 宿主探测 | 真实 CLI 0.65.0＋沙箱合成端点（离线宿主方法，非真实模型通过）：提交后 13/13 PASS，合成端点请求 5 次，真实模型 0。 |
| 真实路径 | 未做（Windows 本地、真实 Gemini 均未做）。 |
| 独立验收 | 未验收；Hark 不自行验收。 |

## 2. 提交号与改动清单

- 功能提交：`be61b924b2bf7e41d5ad2d6718b927c81440676e`（父 7a41ac1）。
  - 说明：该提交最初生成为 1a45a57（作者邮箱不一致，未推送），仅改作者元数据后成为 be61b92；树对象相同 `abb1234d13f3dbd3efdbdcf47cb6a8dfbdd19941`，提交后复验在该树上执行。
- 报告提交：本文件及 `reports/hark-gemini-admission-20261022/`（父 be61b92）。

改动文件（功能提交）：

| 文件 | 内容 |
|---|---|
| src/agent_session_gemini.py | R1 锁内按当前登记准入、登记合法性校验、只追加前缀检查；R2 严格清单、事件形状校验、拒绝确认判据、聊天记录首行形状；R3 输入绝对路径判据。 |
| tests/test_hark12_gemini_admission.py | 新增 32 测（含 7 项定向削弱）。 |
| tests/fixtures/gemini_cli/fake_cli.py | 解码后替换占位符并按 JSON.stringify 兼容格式重编码；新增 workspace_display / edit_events / chat_head 注入项。 |
| tests/fixtures/gemini_cli/mp_submit.py | 多进程同键提交辅助（合成）。 |
| scripts/repro_hark12_counterexamples.py | 只用基线已有 API 的反例复现脚本，可在基线树与候选树上各跑一次对比。 |
| scripts/verify_hark11_gemini_bridge.py | `--workspace` 非绝对路径直接退出 2，不再 abspath。 |
| docs/gemini-cli-adapter.md | 追加“准入整改（Hark-派单-12）”一节。 |

### 实现要点

- **R1 准入**：`registry_lock` 对 `evidence_root/registry.lock` 加排他锁（进程内按路径一把 `threading.Lock` ＋ POSIX `flock` / Windows `msvcrt.locking`），从幂等判定持有到 `dispatched` 记录落盘；超时（`registry_lock_timeout`，默认 30s）以 `RegistryBusy` 拒绝，绝不无锁继续。版本探测在锁外（不启动模型、不写登记），锁内重新判定幂等并重做预检。每次 submit 在锁内重读磁盘登记重建视图；本对象自己启动的 run 保留内存中的进程句柄，其他 run 未收集即为 unknown，永不补发。
- **登记合法性**（`validate_registry_records`）：每条记录字段集合（缺失/多余均拒）与类型（attempt 排除 bool）、run_id 与原生会话 UUID 格式、指纹＝sha256(快照)、快照 schema 与 app/instance/session/task/attempt/workspace/model 一致、各事件允许写入的 state/native_state、每 run 事件顺序 reserved → dispatched|dispatch_failed → collected、后续记录必须重复保留时的绑定、一个幂等键/一个原生会话只对应一个 run、run_dir 必须是本登记的 runs/<run_id>。末行无换行视为损坏。
- **只追加**（`registry_prefix_intact`）：本对象读过/写过的字节必须仍是当前文件前缀；截断、删除、改写即拒。
- 任一问题对该对象粘滞拒绝（不修复、不重排、不补发）；新对象按磁盘现状重新判定。合法前缀、同键不同请求冲突、重开为 unknown 不重发均保持。
- **R2 观测**：`tree_manifest` 的 `os.walk` 不再静默吞错（`on_walk_error` 抛出），stat/读取失败同样抛出；特殊文件（FIFO 等）只记类型不打开。启动前失败 → `WorkspaceUnobservable`，零派发、零登记；收集时失败 → failed，`workspace_unchanged = null`（未证明）。
- **R2 事件形状**（`event_shape_problems`）：按 0.65.0 实际字段（源码 gemini-GLQ4MVLB.js 的 INIT/MESSAGE/TOOL_USE/TOOL_RESULT/ERROR/RESULT 构造）校验必填/可选字段类型；错形状给行号诊断并失败，不抛异常。type 非字符串先判型再查集合。
- **R2 拒绝确认**（`tool_status_confirms_denial`）：非只读工具只有宿主自己的 `status:"error"` 才计入 `denied_tool_requests`；其他（缺失、null、未知串、数组）列入新字段 `unconfirmed_tool_requests` 并失败。只读工具出现未知状态也失败。
- **R2 聊天记录**：`find_chat_record` 首行非对象只给诊断（进 unverified），返回值改为 `(path, notes)`。
- **R3 路径**（`workspace_path_problem`）：在任何解析之前按输入本身判定；POSIX 须以 `/` 开头；Windows 须有盘符且盘符后为根，`\ws` 根相对、`C:ws`/`C:` 驱动器相对、相对路径、`\\?\`/`\\.\` 设备路径、含 `..` 一律拒绝；UNC 共享路径视为绝对。submit 不再 abspath，登记里的 workspace 即输入原值。
- **R3 夹具**：占位符在解码后的 JSON 值内替换后重编码；对派单-11 捕获的原生流逐字节复现（sha256 be3ed400…，测试 `test_reencoding_reproduces_captured_native_bytes`）。产品判据未改。

## 3. 计数

全部数字可由 `python reports/hark-gemini-admission-20261022/check_h12.py`（仓库根目录执行）从下列原始日志独立提取。平台：Linux aarch64，CPython 3.14.8。

| 项 | 数字 | 原始日志（reports/hark-gemini-admission-20261022/logs/） |
|---|---|---|
| 基线完整套件（7a41ac1，本批重跑） | Ran 736，FAILED (failures=27, errors=78, skipped=2) | baseline-suite.txt |
| 提交后完整套件（树 abb1234） | Ran 768，FAILED (failures=27, errors=78, skipped=2) | postcommit-suite.txt |
| 既有失败集合 | 两侧逐行相同（106 行 / 去重 105 项）；基线集合亦与派单-11 的 postcommit-failset.txt 相同 | baseline-failset.txt、postcommit-failset.txt |
| 新增测试连续 3 次 | 32 / 32 / 32 OK | postcommit-hark12-run1..3.txt |
| 派单-11 原测＋JH018 契约 | 74 OK | postcommit-hark11-jh018.txt |
| 新守卫定向削弱 | 7 项：对照 7/7 通过，削弱 7/7 检出 | postcommit-weakening.txt、postcommit-weakening-detail.txt |
| 反例复现（同一脚本） | 32 案：基线 30 案出现缺陷，2 案为对照（合法前缀）或基线已拒（run_id 为 int）；候选 32 案均符合预期 | repro-baseline.txt、repro-postcommit.txt |
| 宿主探测（提交后） | 13 PASS / 0 FAIL；合成端点请求 5；真实模型 0 | host-probe-postcommit.txt、../host-probe-postcommit/ |

提取命令示例：`grep -E "^(Ran|OK|FAILED)" logs/postcommit-suite.txt`；`diff logs/baseline-failset.txt logs/postcommit-failset.txt`。
`counterexamples-on-baseline.txt` 是把新测试放到基线树上逐个运行的结果（29 未通过、1 超时、2 通过）；其中大量 error 来自基线没有新 API（如 `registry_lock_timeout`），**不作为反例证据**，反例证据以 repro-baseline.txt 为准。FIFO 测试在基线超时，正是基线打开 FIFO 会阻塞。

## 4. 偏离清单

已验收测试/守卫/契约/核心门：**无改动**。070004f 未再放宽。以下为本批允许范围内、但可能需要裁决的语义变化：

1. `scripts/verify_hark11_gemini_bridge.py` 的 `--workspace` 由“abspath 后使用”改为“非绝对即退出 2”。
2. 收集产物 `workspace_unchanged` 可为 `null`（无法证明）；新增 `unconfirmed_tool_requests` 字段；`find_chat_record` 返回 `(path, notes)`。
3. 登记问题对同一适配器对象粘滞拒绝，需要人工处置登记后新建对象；新建对象若登记仍不合法同样拒绝（不提供自动修复）。
4. 版本探测在锁外执行（预检在锁内重做）；同键第二使用者在锁内看到首个保留后得到 replay，锁超时则得到 `RegistryBusy`。
5. 观察到但未改的既有语义：流内有形状错误而会话关联本身无误时，`declared_status` 仍照录宿主 `result.status`（如 "success"），但 run 状态为 failed。是否改为 unattributed 请裁决。

## 5. 故障类覆盖表

| 类别 | 输入或注入 | 预期 | 证据 | 状态 |
|---|---|---|---|---|
| R1 同源多对象 | 同一 evidence_root 先建 A/B，依次同键同请求 | 启动 1 次，B 得 replay（unknown） | repro 案1；TestR1SharedRegistry.test_two_adapters… | 已覆盖（基线 2 次） |
| R1 同对象并发 | 两线程在 version_probe 用 Barrier 同步后提交；清单处再设屏障 | 启动 1 次 | repro 案2；test_threads…；削弱 registry_lock 被检出 | 已覆盖（基线 2 次） |
| R1 多进程 | spawn 两进程各建适配器，Barrier 后同键提交 | 启动 1 次 | repro 案3；test_processes_dispatch_once | 已覆盖 Linux flock；Windows msvcrt 未测 |
| R1 构造后损坏 | 构造后写坏 JSON / 追加半行 | 拒绝，零启动 | repro 案4；两项测试 | 已覆盖（基线继续启动） |
| R1 不可读 | chmod 000 登记 | 拒绝，零新启动 | test_unreadable_registry_refuses | 已覆盖（POSIX 非 root） |
| R1 截断/删除 | 读者见过保留后清空；属主提交后删除登记 | 拒绝，不补发 | test_truncated…、test_deleted…；削弱 prefix 被检出 | 已覆盖 |
| R1 派生非法历史 | 从合法 reserved/dispatched 派生：缺 instance/session/app/task/attempt/原生会话/快照/指纹；run_id 为 int/空/null/数组；attempt 为字符串/bool；未知 event；非法 state；指纹不符；快照改写；快照绑定伪造；跨记录原生会话/任务矛盾；逆序；重复保留；非对象记录；多余字段；相对 workspace（共 26 例） | 每例新请求与同键请求均拒绝，零启动 | repro 12 例；test_derived_illegal_histories…（26 子例）；削弱 validator 被检出 | 已覆盖。注：run_id 为 int 基线已拒，空 run_id 基线未拒 |
| R1 合法前缀/冲突/重开 | 仅 reserved、reserved+dispatched、完整历史；同键不同请求 | unknown 不重发、replay、冲突、新键可派发 | repro 对照；test_legal_prefix…、test_reserved_only…、派单-11 原测、test_committed_h11_registries_are_legal | 已保持 |
| R2 启动前不可观测 | 根目录或 src 子目录 scandir 抛 PermissionError（启动前且收集时）；真实 chmod 000 子目录 | WorkspaceUnobservable，零派发零登记 | repro 案；4 项测试；削弱 on_walk_error 被检出 | 已覆盖（基线 declared_complete 且 unchanged=true） |
| R2 仅收集时不可观测 | 收集时 src scandir 失败 | failed，unchanged=null | test_unlistable_at_collect_only… | 已覆盖 |
| R2 特殊文件 | 工作区内 FIFO | 记为 special，不打开 | test_fifo… | 已覆盖（基线阻塞） |
| R2 工具状态 | write_file 的 tool_result.status 缺失/null/"maybe"/["error"] | failed，write_file 不在 denied；未知串列入 unconfirmed | repro 4 案；2 项测试；削弱判据被检出 | 已覆盖 |
| R2 字段形状 | tool_result.tool_id 数组、tool_use.tool_id 数组、事件 type 数组、init.session_id 对象、result.stats 数组 | 返回 failed＋行号诊断，无异常 | repro 2 案；2 项测试；削弱形状校验被检出（回到 TypeError） | 已覆盖 |
| R2 聊天记录首行 | [] / null / "s" / 3 / true | 返回，诊断进 unverified | repro 5 案；测试 | 已覆盖（基线 AttributeError） |
| R2 正常交付 | 原生回放 | declared_complete，stdout 字节摘要与登记一致 | test_valid_delivery_keeps_raw_bytes；宿主探测 13/13 | 已保持 |
| R3 相对路径 | 先建真实 ws 后以 "ws" 提交；./ws、ws/src、不存在相对、`\ws`、`C:ws`、`C:\ws`（POSIX 下）、含 ..、空串 | 派发前拒绝，零登记 | repro 案；2 项测试；削弱判据被检出 | 已覆盖（基线接受真实相对目录） |
| R3 Windows 形式 | 分类器 nt 风格：根相对、驱动器相对、`C:`、设备路径、含 ..；正控 `C:\…`、`C:/…`、UNC、`D:\` | 拒/收如左 | test_classifier_windows_and_posix_forms | 仅 Linux 上分类器验证；**Windows 实测未覆盖** |
| R3 夹具编码 | 合成 Windows 工作区串（含反斜杠与引号）回放；Linux 下真实含反斜杠目录 | 合法 JSON，declared_complete | test_windows_workspace…；test_old_text_substitution_would_be_invalid（复现 Invalid escape）；repro 末案（基线 stderr 含 Invalid \escape） | 已覆盖 |
| R3 字节保真 | 新编码对派单-11 原始 stdout 重建 | 逐字节相等 | test_reencoding_reproduces_captured_native_bytes | 已覆盖 |
| 底线 | 宿主权限、来源关联、未知不重放、continue/cancel 不支持 | 不变 | 派单-11 原 32 测（含 8 项原削弱）＋宿主探测 13/13 | 已保持 |

已知未覆盖：Windows 上的 msvcrt 锁、ntpath 实际解析、junction/重解析点下的清单行为；网络文件系统上的 flock 语义；清单与启动之间的工作区变化（TOCTOU，仍由收集时比对发现）；非属主对象的 status/collect 不重读登记（只在 submit 时重读）。

## 6. 未做与阻塞

- 未做：Windows 本地验证、真实 Gemini 模型调用、产品集成、continue/cancel、GUI、自动整改；旧 Hark09 三尾项保持冻结。
- 沿用证据：原生事件格式与未改动的 Linux 宿主限制证据来自 7a41ac1 的 reports/hark-gemini-bridge-20261021/（源提交 7a41ac1）；本批提交后重跑宿主探测，结果在 host-probe-postcommit/。
- 阻塞：无。架构师新审查材料因 403 未上云，本批按派单内的摘要自建全部反例。
- 是否进入执行AI-派单-08 的 Windows 本地验证，待架构师复核后决定。
