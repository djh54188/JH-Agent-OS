# Hark-派单-11 交付报告：首个 Gemini Agent 会话桥接

来源：Hark（执行角色）｜Hark-派单-11｜类型：交付报告
规则：AGENTS.md v2（SHA-256 `edc9846b33c908ee6f16597cd2dd920f1c0564489271713a027bbe7e24ea74b3`，已在基线核对）→ docs/collaboration-rules.md
仓库 djh54188/JH-Agent-OS；基线 `b07213d89874a82bce9003cb3fb0b2348f0dcae2`；分支 `hark-gemini-subagent-20261021`；架构材料 `b3217ca`（只读，未合并）。
平台：Linux aarch64 云端沙箱；Python 3.14.8（uv cpython）＋ jsonschema 4.26.0；Node v22.23.3；Gemini CLI 0.65.0（npm 隔离安装）。真实模型调用 0；未读取/申请/上传任何用户 key；未调用本机 localhost 代理；未改全局配置。

## 0. 范围理解

在现有 `AgentSessionAdapter` 契约下新增一个薄适配器，驱动固定版本 Gemini CLI 的 headless 工具循环完成首次只读源码定位任务（capabilities/submit/status/collect），收集宿主原生会话、工具请求/结果和最终交付；只读由宿主实际限制。
理解说明：派单允许“隔离安装官方 CLI，做无需模型的探测和假进程回放”。我另用了一个**合成模型端点**（本沙箱 127.0.0.1 上的脚本，不是模型、不是用户代理），让真实 CLI 跑完自己的工具循环，以实测宿主限制是否生效。它属于“宿主离线实测（合成模型输入）”，不是“真实模型通过”。若架构师认为这超出“无需模型的探测”，请裁决（见请求裁决 2）。

## 1. 分级结论

| 层级 | 结论 | 绑定提交 / 证据 |
|---|---|---|
| 实现完成 | 是：`GeminiCliAgentSessionAdapter`＋验证入口＋文档＋示例配置 | 功能 `f740849`，JH018 偏离 `070004f` |
| 离线验证通过 | 是：新测 32/32；完整套件失败集合与基线相同 | `logs/precommit-suite.txt`、`logs/postcommit-suite.txt` |
| 宿主离线实测（真实 CLI＋合成端点） | 通过：13/13 检查，提交前后各一次 | `host-probe/`、`host-probe-postcommit/`、`logs/postcommit-host-probe.txt` |
| 真实路径通过 | **未做**（真实 Gemini 模型、账户、额度、Windows） | — |
| 验收 | **待独立验收** | — |

证据目录：`reports/hark-gemini-bridge-20261021/`（下称 E）。

## 2. 提交号与改动清单

- `f740849983b6aed46a07a5492349d0edb5cce556` feat：
  - `src/agent_session_gemini.py`（新增）适配器。
  - `tests/test_hark11_gemini_session.py`（新增，32 测）。
  - `tests/fixtures/gemini_cli/`：`fake_cli.py`（进程替身）、`host_probe_stream.template.jsonl`（由真实 CLI 原生流模板化）、`synthetic_endpoint.py`（合成端点）。
  - `scripts/verify_hark11_gemini_bridge.py`（本地验证入口：`host-probe` / `real`）。
  - `docs/gemini-cli-adapter.md`、`configs/gemini-cli-bridge.example.json`（无密钥）。
- `070004fd011bebae02d88b67c2a261ac689775b5` test（偏离，单独提交）：`tests/test_jh018_agent_session_contract.py` 白名单新增 `agent_session_gemini.py`，并把继承检查与“被扫描命中”检查扩到新适配器。
- 报告提交（本文件＋E）：见交付消息中的 SHA。
- 未改：Codex 适配器、`src/agent_session.py` 契约、成功门、session_probe、旧证据、规则、main、n1、PR #1。

### 实现要点

1. **固定版本与原生格式（先核实再实现）**：0.65.0 bundle 中 `StreamJsonFormatter.emitEvent` 逐行 `JSON.stringify(event)+"\n"`；事件 `init{session_id,model}`、`message{role,content,delta}`、`tool_use{tool_name,tool_id,parameters}`、`tool_result{tool_id,status,output?,error?}`、`error{severity,message}`、`result{status,stats}`（`chunk-DKZARV6U.js`、`gemini-GLQ4MVLB.js` 的 runNonInteractive；文件哈希见 `E/host/cli-install.txt`）。测试夹具来自真实 CLI 输出原字节（`E/host-probe/bridge/runs/gemini-run-e4d091ccdd29/stdout.jsonl`，sha256 `be3ed400…99b97`），仅把会话 id 与路径替换为占位符，没有自创 JSON。
2. **会话关联**：适配器先生成 UUID，用 `--session-id` 传入，启动前写入登记；`init.session_id` 必须一致，否则 `failed`/`unattributed`。`tool_use`↔`tool_result` 按 `tool_id` 配对，缺失、重复、孤立结果都记问题。宿主自身会话记录（`<cli_home>/.gemini/tmp/*/chats/session-*.jsonl`，首行 `sessionId`）按 id 找到后逐字节复制为次级证据。原生轮次 id 不暴露 → 记“unknown”；`NativeTurn` 为适配器分配的 `<session>#invocation-1` 并注明。模型分三项记录：请求名、`init.model`、`result.stats.models` 后端名。用量照抄 `stats`，缺字段列入 `unverified`，不补零。
3. **宿主只读限制**：每次运行写 `admin-policy.toml` 经 `--admin-policy`（admin 最高层级）：`*` deny，四个读工具 allow；`--approval-mode default`；隔离 `GEMINI_CLI_HOME` 写锁定设置（`tools.core`=4 工具、关 MCP/扩展/技能/hooks、`ignoreLocalEnv`、关遥测、`maxSessionTurns=40`）；进程环境从空构造，只按名字透传变量；预检拒绝工作区 `.gemini/`、工作区及上级的 `.env`/`.gemini/.env`、证据目录或 CLI home 与工作区重叠、非 0.65.0 版本（`--version` 探测，无需模型）。
4. **等待有界、未知不重发**：先登记 reserved 再启动；collect 有界等待，超时只报告不重发；重开登记后未收集的运行变为 `unknown`，同键重放不再启动进程。
5. **工作树零修改**：启动前/收集时逐文件清单（不跟随符号链接），before 清单摘要登记并回读核对；不一致即 `failed`。
6. `declared_complete` 只代表宿主声明；任何路径都不产生 `succeeded`。

## 3. 计数

| 数字 | 值 | 原始日志 | 提取命令 |
|---|---|---|---|
| 基线完整套件（b07213d） | Ran 704；failures=27，errors=78，skipped=2 | `E/logs/baseline-suite.txt` | `grep -E "^(Ran \|OK\|FAILED)" E/logs/baseline-suite.txt` |
| 提交前完整套件 | Ran 736；failures=27，errors=78，skipped=2 | `E/logs/precommit-suite.txt` | 同上 |
| 提交后复验（干净 worktree，`070004f`） | Ran 736；failures=27，errors=78，skipped=2 | `E/logs/postcommit-suite.txt` | 同上 |
| 失败集合 | 基线/提交前/提交后各 106 行，`diff` 无差异 | `E/logs/*-failset.txt` | `grep -E "^(ERROR\|FAIL):" <套件日志> \| sort > <failset>`；`diff baseline-failset.txt postcommit-failset.txt` |
| AST 独立计数 | 基线 704；提交后 736；新文件 32；JH018 42（未增测） | `E/logs/ast-*.txt` | `python E/logs/ast_count.py tests` |
| 新测（提交后单跑） | Ran 32，OK | `E/logs/postcommit-hark11.txt` | `tail -3 E/logs/postcommit-hark11.txt` |
| JH018 契约测 | 提交 A：Ran 42 FAILED(1)（白名单未加时扫描命中新适配器）；提交 B：Ran 42 OK | `E/logs/jh018-at-commitA.txt`、`jh018-at-commitB.txt` | `tail -3 <日志>` |
| 宿主离线实测 | 提交前、提交后各 13 PASS / 0 FAIL，exit 0 | `E/host-probe/verify-output.txt`、`E/logs/postcommit-host-probe.txt` | `grep -cE "^PASS" <日志>`；`grep -E "^FAIL" <日志>` |
| 调用口径（提交后实测） | 派发 1 次（同键重放 0 次新进程）；CLI 进程 1 个（另有 1 次 `--version` 探测）；合成端点流式请求 5 次；宿主工具调用 4 次（成功 2：grep_search、read_file；拒绝 2：write_file、run_shell_command）；真实模型调用 0 | `E/host-probe-postcommit/synthetic-requests.jsonl`、`stdout.jsonl` | `python -c "import json;[print(json.loads(l)['kind']) for l in open('synthetic-requests.jsonl')]"` |

说明：106 行 = 105 个顶层失败/错误（27+78）＋ 1 行来自某个既有测试在子进程里另跑 1 个测试（日志中的 “Ran 1 test … FAILED (errors=1)”，`host_capture.verify_desktop_binding`），三份日志同样出现。Linux 既有失败集合本批未变化；不沿用其他分支的 819。

## 4. 偏离清单

1. **JH018 白名单（已验收测试，单独提交 `070004f`）**：原行为 `CONTRACT_CONSUMERS={"agent_session_codex.py"}`；新行为加 `agent_session_gemini.py`。原因：新适配器必须继承 `AgentSessionAdapter`。保留原扫描；`test_contract_consumers_are_real_adapters` 与 `test_the_real_adapter_is_flagged` 同时检查新适配器。影响：仅放开这一个确实继承契约的模块。验证：提交 A 上该测失败（证明扫描仍有效），提交 B 通过。
2. **合成模型端点**（见第 0 项与请求裁决 2）：测试/验证用夹具，不进入产品路径。
3. **`allowed_scope` 非空即拒绝**：Codex 适配器不检查该字段；本桥无法按路径限制，只能整树只读，因此拒绝而非忽略。属本适配器自己的行为，不改契约。
4. 无其他偏离。

## 5. 故障类覆盖表

| 类别 | 输入/注入 | 预期行为 | 证据 | 状态 |
|---|---|---|---|---|
| 有效交付 | 回放真实原生流 | declared_complete；4 个调用全部配对；2 个写/Shell 拒绝；源码引用 `src/agent_session.py:94` 校验通过；工作树不变 | `TestValidDelivery.*` | 已覆盖 |
| 宿主参数 | 读取进程 argv | `--admin-policy` 文件含 deny `*`＋4 工具；`--approval-mode default`；无 yolo；`--session-id` 等于登记值；策略文件不在工作区 | `test_host_restriction_flags_are_passed` | 已覆盖 |
| 关联：外来会话 | init.session_id 被换 | failed，`unattributed` | `test_foreign_session_is_not_adopted` | 已覆盖 |
| 关联：孤立结果/缺结果 | 删除全部 tool_use / tool_result | failed，列出具体 id | `test_tool_result_without_tool_use`、`test_tool_use_without_result` | 已覆盖 |
| 关联：缺 result / 重复 init / 非原生行 | 删 result、追加 init、追加 `{"type":"made_up"}` | failed | 对应 3 测 | 已覆盖 |
| 进程失败 | 退出码 3 | failed | `test_nonzero_exit` | 已覆盖 |
| 模型未被遵从 | 请求 `gemini-2.5-pro`，宿主报 `gemini-2.5-flash` | failed；argv 中 `-m` 为请求名 | `test_requested_model_not_honored` | 已覆盖 |
| 地址不符 | 其他 instance 查询 | AddressMismatch | `test_address_mismatch` | 已覆盖 |
| 证据缺失 | 收集前删 stdout | failed，`evidence missing` | `test_stdout_missing_fails` | 已覆盖 |
| 证据篡改 | 收集后同长度翻转 1 字节；收集前改 before 清单 | `verify_run_evidence` ok=False；failed | `test_evidence_bytes_are_verified_on_disk`、`test_tampered_before_manifest_fails` | 已覆盖 |
| 次级证据缺失 | 无宿主会话记录 | 列入 unverified，不伪造 | `test_chat_record_missing_is_listed` | 已覆盖 |
| 只读：工作树被改 | 替身写入新文件 | failed，workspace_unchanged=false | `test_workspace_modification_is_detected` | 已覆盖 |
| 只读：写工具“成功” | 把 write_file 结果改成 success | failed，“host restriction breached” | `test_successful_write_tool_is_a_breach` | 已覆盖 |
| 只读：宿主配置注入 | 工作区 `.gemini/`、工作区 `.env`、上级 `.env`、证据目录在工作区内 | 拒绝；零进程、零登记 | 4 测 | 已覆盖 |
| 只读：真实宿主 | 真实 CLI：无策略 / yolo 无策略 / yolo＋策略 / 策略＋越界读 / yolo＋策略＋工作区设置与策略＋用户策略放开写和 Shell | 声明工具分别 8 / 16 / 4 / 4 / 4；写、Shell 均 `tool_not_registered`；`/etc/hostname`、指向外部的符号链接、`/etc`、上级目录被拒；工作区无新文件 | `E/host/probe-{A,C0,C,D,E}.*`、`probe-p2.*` | 宿主离线实测 |
| 请求约束 | 非空 allowed_scope、有限 budget、已有会话、相对路径、非固定版本 | 拒绝，零进程 | 5 测 | 已覆盖 |
| 未知不重发 | 替身 sleep 3s，collect 0.3s；同键重放；重开登记后重放 | 有界返回；同一 run；启动次数始终 1；重开后 unknown | `test_bounded_wait_and_no_redispatch` | 已覆盖 |
| 幂等冲突 / 登记损坏 / 重开保留结果 | 同键不同指令；坏 JSON 登记；收集后重开 | IdempotencyConflict；RegistryUnavailable 零进程；结果与状态保留 | 3 测 | 已覆盖 |
| 源码引用 | 存在 / 越界行 / 不存在 / `../` | 仅工作区内且行号有效为 verified | `TestSourceRefs` | 已覆盖 |
| 守卫定向削弱 | 逐个替换 8 个谓词为放行 | 每个对应测试先通过、削弱后失败 | `TestGuardWeakening`（8 个 subTest） | 已覆盖 |

已知未覆盖：
- 真实 Gemini 模型行为（是否按指令使用工具、引用格式、用量字段真实取值）、真实账户/额度、OAuth 登录路径。
- Windows：路径、`USERPROFILE`/`GEMINI_CLI_HOME`、策略文件加载、符号链接/junction 在工作区内指向外部时的拒绝行为、进程环境最小集是否够用。
- 指令中 `@路径` 的读取路径（CLI 预处理，不经工具；是读操作）未单测。
- 工作树清单在运行中被外部并发修改、超大仓库的清单耗时、断电恢复。
- 计算型动态导入绕过 JH018（原测已记为未覆盖）。
- 未设置模型时宿主为 `auto`：第一次 host-probe（未传 `-m`）观察到 5 次非流式分类器请求（路径 `gemini-3.5-flash-lite:generateContent`）后才有 1 次流式请求（`gemini-3.1-pro-preview`），该次原始输出目录已被随后重跑覆盖、未保留，只作线索。验证入口在 host-probe 模式下因此固定 `-m gemini-2.5-flash`。真实使用的调用数/费用口径需本地真实验证时确认。

## 6. 未做与阻塞

- 未做：continue/cancel、Shell 或测试工具、GUI 接线、Muse/自动审查、自动整改、文件应用、真实模型调用、Windows 执行。
- 无阻塞。宿主限制在 Linux 离线实测中成立；Windows 上是否同样成立属待真实验证项，不是本批阻塞。
- 平台限制：系统级设置文件在 Linux 非 root 属主时被 CLI 跳过（`E/host/probe-p2.stderr.txt`），所以本桥不依赖它，只依赖 admin 策略文件＋预检。
