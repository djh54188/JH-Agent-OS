# Hark-派单-27 交付报告：ZCode 宿主的 AgentSession 薄桥（离线候选）

来源：Hark B｜Hark-派单-27｜类型：交付报告。状态：候选，待独立复核。真实模型/ZCode/Node/8787/key 调用：0。

## 0 基线与提交
- 新分支 hark-b-zcode-gemini-agent-20261028，从 8ab9fbdfcb6bcce020f791249a8091048d37d83f 新建；开工时远端无此分支，工作区干净。AGENTS sha256 502aa829…6cf 一致。
- 功能 b33eaf80788e383ca4ddeae43516e5e0eec590bb（父 8ab9fbd）；JH018 偏离 cfc239f30be0998d023c6e54b7b882769f812ceb（父 b33eaf8，单独提交）；报告为其子提交。

## 1 允许差异（相对 8ab9fbd）
- 新增：src/agent_session_zcode.py、tests/test_hark27_zcode_session.py、tests/fixtures/hark27/（fake_zcode.py、scenarios.py、workspace/）、scripts/win/hark27-zcode-prepare.ps1、scripts/win/hark27_zcode_replay.py、docs/hark27-zcode-bridge.md。
- 修改（偏离提交，仅此）：tests/test_jh018_agent_session_contract.py 消费者名单 +agent_session_zcode.py，并加 issubclass(ZCodeAgentSessionAdapter, AgentSessionAdapter)；原静态扫描与继承检查不变。
- 原 Gemini/Codex 适配器、agent_session.py 契约、B 任务包/核对器、19/24/26 入口、规则、旧报告：差异 0。
- sha256：agent_session_zcode.py b0eb06e9…e9a9；PS1 7b1261c5…3514（纯 ASCII）；replay 驱动 53b5d958…9790；测试 31ec9bf6…eed6；fake_zcode.py 28e4b6d3…dc2。PS1 钉：src 树 03929c3b…、fixtures/hark27 树 b87de7d5…、驱动 blob 0fbce16f…（= 提交实际值）。

## 2 实现（宿主事实来源：官方 zai-org/ZCode 3c5b2276 源码/文档，未在用户已装构建上核实）
- ZCodeAgentSessionAdapter 真实继承 AgentSessionAdapter；capabilities/submit/status/collect；continue/cancel 抛 UnsupportedCapability；capabilities 的 verified 为空，宿主事实全列 unverified。
- 启动：`<node> <zcode.cjs> --prompt … --cwd <ws> --mode plan --disallowed-tools <非读/搜工具> --workflow-mode disabled --output-format stream-json --no-color`；无 --model；最小环境＋隔离 HOME/USERPROFILE/ZCODE_DATA_BASE_DIR/APPDATA/LOCALAPPDATA；隔离 provider 文件只含一个 provider、一个模型和显式 defaultModelSelection；key 仅在派发时从调用方命名的变量读入隔离 home（证据根外）。宿主文件 sha256 钉；工作区及祖先 .env 拒绝；工作区与证据根/运行 home 重叠拒绝。
- 绑定：请求 app_kind/instance、session=new（不支持恢复）、session.model_id = 绑定模型、scope ⊆ {read, search}、显式幂等键。
- 登记：reserved 先 fsync 再做任何事；同键同请求重放同 run 不重启；同键异请求 IdempotencyConflict；同根其它键/attempt 拒绝；reserved 无结果、dispatched 后重开无退出 → unknown 冻结；损坏、半行、未知形状、锁残留、有痕迹无登记 → RegistryUnusable。
- 证据：stdout NDJSON 原字节保存；按 sessionId/traceId/turnId/toolCallId 关联；只允许 Read/Grep/Glob；readOnly:false 失败；路径 realpath 必须在工作区内（事后检出，不声称阻止）；任何 permission.requested 或非 deny 决议失败；model_request 必须全等绑定 provider/model，fallback 失败，未观察到为 unknown；usage 缺失记 unknown 非 0。
- 分层：派发失败（not-launched/launch-failed）、宿主声明（host_completed/host_not_completed/unreported）、机械（mechanical_problems）、语义（public_summary 固定写 required）。无 succeeded。
- public_summary 只含受控字段（run_id 正则、状态枚举、允许工具计数、问题/未核实计数、证据 sha256）；不含响应、错误文本、路径、宿主反射工具名。

## 3 测试与日志（logs/，完整）
环境 Linux aarch64，CPython 3.12.15，官方 pwsh 7.4.6；宿主为假进程（与真实启动方式相同的 argv/env/cwd）。
- run1..3：test_hark27 29 项 OK（24 Python + 5 pwsh），jh018 42 项 OK；post-commit 干净 worktree（status --ignored 0 行）同样 OK。
- 提取：`python reports/hark27-zcode-bridge-20261028/extract.py` 退出 0；输出 logs/extract-output.txt。
- 正控：Grep+Read 运行 → declared_complete/host_completed，原生 session/trace/turn、工具请求与结果、观测模型、usage 可查；stdout 字节与宿主所写一致；argv/环境/provider 文件绑定检查；证据根内无 key。
- 反例：异会话/异 trace 事件、未请求的结果、跨轮结果、permission 请求、allow 决议、Bash/Write/Edit/未知工具、readOnly:false、绝对路径/../符号链接/Grep 根/Glob 父目录越界、模型不符、fallback、未观测模型、usage 缺失、非零退出、非 JSON 行、无结果行；准入拒绝（模型/范围/恢复/无键/.env/重叠/配置非显式/宿主钉不符/key 缺失）；登记 5 类损坏。
- 削弱（4 份日志各 9 项全部检出）：duplicate_dispatch 启动 1→2；rekey_after_unknown 0→1；traces_without_registry 1→2；misassociation/unknown_approval/out_of_workspace/model_binding failed→declared_complete；public_reflection 不含→含哨兵；PS source_identity（删 Assert-Source）exit 2/未执行→exit 0/篡改驱动执行。
- 未跑 113/265/全仓。

## 4 Windows 入口（审查前禁止运行）
scripts/win/hark27-zcode-prepare.ps1：新/空 Parent、.env→NEEDS_SETUP(4)、仅拉固定提交、HEAD/树/驱动 blob/干净、可导入目录与顶层磁盘字节，均在仓库代码运行前；-ZcodeCjs 只做 sha256+字节数（a055…e1、15243891），不执行（不符→3）；最小环境运行离线回放（真实适配器＋假宿主），0 通过/5 不通过/1 内部；不读用户配置/key，不启动 ZCode。

## 5 偏离 / 覆盖与未覆盖
1. JH018 消费者名单 +1（单独提交 cfc239f）。
2. 宿主事实全部来自官方源码 3c5b2276，用户已装 zcode.cjs（a055…）未核实：参数、事件 schema、工具名、plan 行为、--workflow-mode 是否被接受都标未验证；无需模型的宿主探测因无法证明零调用未做。
3. plan 不是沙箱；越界读取只能事后检出。禁用工具名单是尽力而为，事后白名单才是判据。
4. 只覆盖新会话；不做 continue/cancel/GUI/通用 Runtime/协议翻译。
5. PS 磁盘字节检查在 PS 自建 clone 上无法注入隐藏改动做单独削弱（Hark-26 已有同构实测）；PS5.1、真实 Windows 未测；Linux 无 junction。
6. 复用用户现有 provider 的路线只在 docs 中给待审模板；未读取用户 key 或配置，未访问 8787；8787 别名 gemini-3.8-flash-high 不等于已核实的官方模型。
7. 云端结论只到假宿主层；真实路径（Windows/ZCode/代理/账号/额度）未准入。

## 6 提交后复验
父链 8ab9fbd→b33eaf8→cfc239f→报告提交；允许差异如 1；PS1 钉与提交树一致；关键反例与削弱在干净 worktree 复验通过；JSON 行不截断。
