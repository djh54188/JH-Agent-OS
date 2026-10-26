# Hark-派单-24 交付报告——task01 最小用户入口（候选）

来源：Hark A｜Hark-派单-24｜类型：交付报告。执行实例：Hark B 代 A 执行（用户 2026-10-26 19:41 CST 授权）。状态：候选，待独立复核。真实模型/CLI/Node/Gemini/代理端点调用 0；RunReal 未准入。

## 0 提交与基线
- 分支 hark-a-gemini-task01-entry-20261026，从 95d941ec1025470dad4fc1d00e9f4775361e3b91 新建（开工时远端无同名分支；AGENTS SHA-256 502aa829…6cf）。
- 功能提交 d88efc0bf26533ecb098fc5481aee91e456eff57（唯一父 95d941e）；本报告为其后的报告提交。
- 新增（仅新增，无修改）：scripts/win/hark24-gemini-task01.ps1（sha256 328cf7ff50a1c06f9db8851e3c45754843901c80c12e3ff78b81b32671c027a1，blob ba434c52bcd496c7a5c974d5c25b081f0517ffa8，纯 ASCII）、scripts/gemini_task01_run/entry.py（69aa5422…166e）、tests/test_hark24_gemini_task01_entry.py（ae960a6e…340）、tests/fixtures/hark24/fake_node.py（3097fd85…9de1）、guarded_prepare.py（fccc684b…df23）及本报告目录。
- 相对 95d941e：B 的 scripts/gemini_task01/、tests/fixtures/hark21、test_hark21、src/、verify、scripts/win 原有脚本、AGENTS、docs 差异 0。

## 1 设计
- PS1 默认 Prepare：在 -Parent 下新建 repo-<stamp>（git init，只 fetch -Commit，detached，仓库内 core.autocrlf=false）；核 HEAD=-Commit、本脚本字节=该提交里的 blob；调用 `python -m scripts.gemini_task01_run.entry prepare`。不读任何 $env 密钥变量，不启动 node/CLI。
- prepare：工作根必须不存在；核仓库 HEAD/干净/继承 95d941e 及 7 个已接受文件 sha256（B 的 check_run/export_source/pins/package/task/baseline 与 A 的 agent_session_gemini.py）；用 B 的 export_source 导出 26 文件；写 context.json（+context.sha256）：固定 workspace、task_id/sha、模型、目的地址（仅字符串校验，含用户信息/查询/片段即拒且不回显）、key 变量名、代理变量名、node/gemini.js 静态存在与 gemini.js sha256 静态比对、证据根、CLI home、固定幂等键、attempt=1；version_verified/key_value_checked/protocol_verified 均 false。缺项=NEEDS_SETUP，不猜默认。
- run-real（审阅模板，禁止现在运行）：需显式 --context、--authorize-real-model-call 与精确 --accept-boundary 短语；任一单次派发标记（父目录 task01-real-dispatch.marker、工作根 run-real.started）、证据根或结果文件已存在→FROZEN；重核上下文 sidecar、仓库、固定文件、导出字节、任务字节、地址/模型/变量名；检查 key/代理变量存在（不打印值）；node --version 必须 v22.23.4、CLI 由 A 的 default_version_probe 必须 0.65.0；O_EXCL 写标记后用现有 GeminiCliAgentSessionAdapter 一次 submit、有界 collect、status；调用 B 的 check_run.run_checks（未改）写 check-report.json；写 evidence-digests.json（原始证据留本机）。结果：COLLECTED（退出码=机械 0/1/3，非任务结论）、TIMEOUT_UNKNOWN、DISPATCH_UNKNOWN、DISPATCH_REFUSED、FROZEN、NEEDS_SETUP、REFUSED。超时不杀进程，记录“不证明进程停止或停止计费”。
- candidate：在上下文仓库新建本地分支，只提交白名单 5 个小文件（context/prepare-result/run-real-result/check-report/evidence-digests），核分支=HEAD、唯一父=entry 提交、差异路径=白名单、blob=快照字节、工作区干净；检出到密钥值即不提交；从不 push。
- 环境：沿用 A 的 build_env 白名单（PATH 等基础名 + GEMINI_API_KEY + 声明的代理名 + GOOGLE_GEMINI_BASE_URL），HOME/GEMINI_CLI_HOME 指向隔离 home；不照搬用户 env，不改全局配置。

## 2 依据与阻塞
- 变量名依据：reports/hark-gemini-bridge-20261021/host（真实 CLI 0.65.0 + 合成端点）实样：GEMINI_API_KEY + selectedType gemini-api-key + GOOGLE_GEMINI_BASE_URL → POST <base>/v1beta/models/<model>:streamGenerateContent?alt=sse。携带 key 的请求头未记录，未声称。未读 0.65.0 源码核实其他变量。
- 阻塞（真实调用前）：项目已知本机代理 localhost:8787 为 Anthropic Messages 协议（ADR-0002），不是 CLI 需要的 Gemini 原生协议；若用户只有该代理则阻塞，本批不加翻译层。需用户提供 Gemini 原生协议的明确地址（不含 /v1beta、不含凭据/查询）、显式非 auto/非别名模型、GEMINI_API_KEY 变量名、Node 22.23.4 与 0.65.0 gemini.js 路径。另需：架构师审查 PS1 与准入、Hark-19 Windows 结果独立验收、用户接受费用边界（一次 submit≠一次请求；maxSessionTurns 40 非费用上限；无硬限费）。

## 3 测试（Linux aarch64，CPython 3.12.15；临时克隆+假 node+B 的构造 CLI）
- logs/hark24-tests-run1..3.txt：每次 Ran 19，OK，跳过 0（AST 19 个 test_ 方法）。日志在提交前工作树运行；提交后复跑见 6。
- 覆盖：Prepare 不派发/不启动 node/上下文非敏感/哨兵不出现；守卫子进程证明 Prepare 对密钥/地址/代理变量零查找、只启动 git；9 类缺项 NEEDS_SETUP；含凭据/查询/片段/错 scheme 地址不回显；工作根已存在/错 entry 提交拒绝；导出与任务字节=锚。RunReal：经真实适配器一次派发→COLLECTED、机械 all_pass、semantic required、task_outcome 未判定；宿主收到 key（只记存在与长度）、地址、显式模型，env 名集合在白名单内且无无关变量；同上下文与同父目录新上下文均 FROZEN；3 个并发入口只派发 1 次；未授权/无短语/缺 key/Node v24/CLI 0.66.0 拒绝且无标记、无证据根；上下文篡改、导出篡改、NEEDS_SETUP 上下文拒绝；超时 TIMEOUT_UNKNOWN 且宿主进程仍存活、再跑 FROZEN；启动失败记 reserved+dispatch_failed→DISPATCH_REFUSED 且冻结；宿主失败原样保留非 all_pass；相反解释只得机械 all_pass、无任务 PASS；候选提交白名单/唯一父/blob 核实且不 push、无哨兵。静态：PS1 ASCII、默认 Prepare、无 $env 密钥引用、无 push、RunReal 需开关；entry 无测试钩子、无翻译层。
- 削弱（编辑临时克隆中的 entry.py 副本并提交，生产代码无钩子），logs/extract-output.txt 三次一致：authorize、single_dispatch、node_version、key_presence、endpoint、context_sidecar 对照非 0 且 0 派发，削弱后各派发 1 次并 COLLECTED/all_pass/退出 0；export_verify 对照 REFUSED/0 派发，削弱后派发 1 次但 B 的锚判 fail/退出 1（第二层，不计 all_pass）。
- logs/related-regression.txt：hark21+JH018+hark11/12/13/18/19 发现 265、通过 257、跳过 8（既有 Windows/PWSH），OK。

## 4 偏离/披露
1 B 代执行 A 单（用户授权）；分支名按派单。2 PS1 未实跑（无 pwsh），Windows/PS5.1、真实 node.exe/gemini.js、路径含空格均未覆盖；假 node 仅 Linux。3 版本探测 `gemini --version` 沿用 A 的 default_version_probe，会把 key 变量交给该探测进程（仅 RunReal）。4 标记写在版本探测之后、submit 之前；submit 前适配器拒绝也会永久冻结（保守）；换 -Parent 可绕过父目录标记，跨目录单次性依赖派单约束。5 代理值含用户信息即拒（保守）；http 地址允许。6 gemini.js sha256 只作静态信息（Linux 安装值）。7 check-report 含模型交付文本，进入候选提交；原始 stdout/会话/登记留本机。8 候选的密钥扫描只对 ≥8 字符的值。9 CLI 版本门未做削弱（适配器自带同门）。10 collect 默认 900s、上限 3600s。

## 5 未覆盖
真实 Windows 与 PS5.1；真实宿主版本/认证/协议；Gemini 原生代理兼容性；网络中断与计费；TOCTOU；用户真实 env 继承细节。

## 6 提交后复验
detached worktree @d88efc0：工作区 0 改动；相对 95d941e 只有 5 个新增；B/src/A 入口/AGENTS/docs 差异 0；AGENTS SHA-256 502aa829…6cf；19 项 OK；削弱 7 例与提交前逐项一致（logs/post-commit/tests.txt）。
