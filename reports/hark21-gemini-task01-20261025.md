# Hark-派单-21 交付报告：首次 Gemini 源码任务包 task01（固定输入＋离线机械核对）

来源：Hark B｜Hark-派单-21｜类型：交付报告｜状态：候选，待架构师独立复核。真实模型 0、合成端点 0、CLI/Node 宿主 0；无 Windows 格。机械核对通过不等于语义验收，也不等于任务成功。

## 0 范围理解
只交付一个固定输入、可离线核对证据的任务包：任务正文与审阅基准分开存放并绑定 SHA-256；被分析源码为 f6bf47c 的 src/ 原字节导出；一个只做机械一致性核对的工具（不判语义、不给任务结论、不重发）。不改 src/、A 的测试/夹具、verify 脚本、scripts/win/、JH018、规则或旧报告。

## 1 分级结论
- 实现：完成（候选）。
- 离线：构造输入下 40 测 ×3 次全 OK；11 个机械门“有效对照→定向削弱”3 次均 11/11（对照均 fail，削弱后均不再 fail）。另 1 次提交后干净 worktree 复跑 40 OK。
- 真实路径：未做（真实调用 0）。旧 host-probe 只用作 CLI 0.65.0 工具输出格式样本和异任务反例，不是本任务正控。
- 独立验收：未验收。

## 2 提交与改动
- 分支 hark-b-gemini-task01-20261025，基线 59b221cec6d4bbac8f8ee8fd6386167dff6edf08（开工时 HEAD=基线，status 干净；远端无同名分支）。
- 功能提交 2d47d3a7c878b7af15c8116b5208af8216a13088（父 59b221ce），只新增 8 个文件：
  - scripts/gemini_task01/task.txt（sha256 5ed4f438…0518）：交给模型的正文，不含答案；含 B7 问题（第 3 条）。
  - scripts/gemini_task01/review-baseline.md（e07841a8…d57f）：B2/B3/B4/B6/B7 独立审阅基准，仅供架构师。
  - scripts/gemini_task01/package.json（ebf8e8ed…f7f1）：固定 f6bf47c / blob 31d3cd7c、两份文件摘要、task_id、stdin 前缀与 -p 文本、核心点函数范围及 B3/B6/B7 焦点行摘要。
  - scripts/gemini_task01/export_source.py（1422f5d8…9d24）：git ls-tree/cat-file 读对象原字节，写入新空目录，回读校验 blob；拒绝链接/子模块；清单写在目录外。
  - scripts/gemini_task01/check_run.py（95d17fe4…da22）：机械核对，见第 3、5 节。
  - tests/test_hark21_gemini_task01.py（d27b9be1…0648）、tests/fixtures/hark21/build.py（b38dd032…fa23）、tests/fixtures/hark21/fake_cli_t01.py（eeb5aefb…c7fa）。
- 报告提交：本报告与 reports/hark21-gemini-task01-20261025/（日志、提取脚本、f6bf47c src/ 导出清单样例）。
- src/ 树与 59b221ce 相同；AGENTS.md blob SHA-256 仍为 502aa829…6cf。

## 3 机械核对做什么、不做什么
输入（只读）：任务包、导出清单与导出目录、一个适配器证据根和 run_id。逐项给 pass/fail/unknown：
1. package：正文和基准字节 = 包内摘要（CRLF 转换会报 fail 并提示）。
2. export：目录恰为清单文件集，字节/大小/blob 一致，目标为固定 blob。
3. registry：用适配器自身 validate_registry_records 判合法历史。
4. binding：该 run 恰为 reserved→dispatched→collected；快照 instructions=包内正文、task_id、新会话、显式模型、CLI 0.65.0；宿主回显的 user 消息 = 前缀＋正文＋空行＋-p 文本。未收集记 unknown。
5. workspace：登记的 before（摘要绑定）/after 清单恰等于导出文件集。
6. evidence：stdout/stderr/宿主会话记录字节 = 登记尺寸与摘要；缺失记 unknown，改字节记 fail。按 runs/<run_id>/ 定位，记录登记时路径，便于 Windows 证据搬运后核对。
7. adapter：登记的适配器门（declared_complete、exit 0、模型一致、workspace_unchanged、无未确认请求）不放宽；stdout 用适配器解析器重解析须与记录产物一致。
8. tools：stream-json 的 grep_search 成功结果不带输出、read_file 只带摘要行，实际内容在宿主会话记录 functionResponse 中。逐个按 tool_id 配对，参数一致；grep 每行“L<n>: / L<n>- ”文本与导出源码对应行逐字相等、匹配数与头一致；read 截断格式内容与导出源码切片相等、行数一致，且与流内摘要行一致。只认 0.65.0 已观测的格式，其他格式（含未截断整文件读取）记 unknown。工作区外路径直接 fail，不读取。
9. required：至少 1 个已核实的目标文件 grep 命中和 1 次已核实的目标文件 read；存在 unknown 工具时记 unknown。
10. refs：交付中每个 path:line 在导出中存在；B2/B3/B4/B6/B7 各至少一个引用落在其函数范围或焦点行（包内范围与源码 AST、焦点行摘要先自校）。
输出永远带 semantic_review.status=required、task_outcome="not decided by this tool"、各点 meaning="not judged by this tool"，无 approved/succeeded/accepted 字段。退出码：0 机械全过（仍需语义审阅）；1 有 fail；3 无 fail 但有 unknown；2 无法运行。不启动进程、不联网、不写证据根或导出目录、不重发。

## 4 计数与原始日志（Linux aarch64，CPython 3.12.15，git 2.47.3）
- logs/hark21-tests-run1..3.txt：每次 Ran 40，OK；WEAKENING 行 11 门均 [fail, all_pass]。
- logs/extract-output.txt：`python reports/hark21-gemini-task01-20261025/extract_counts.py`（运行器 Ran 数、OK 行、削弱 JSON；AST 独立计 test_ 方法 40）。
- logs/post-commit-identity.txt、logs/post-commit-tests.txt：干净 worktree detached 2d47d3a，HEAD/父/允许差异/文件摘要/包摘要自校/40 OK。
- logs/export-demo.txt 与 export-manifest-f6bf47c-src.json：导出 26 个文件，清单 sha256 f364401a…9bb1。
- logs/constructed-sample-check.txt：三份构造样本（正确、解释相反、B7 含义遗漏）的输出，三者机械均 all_pass、semantic_review 均 required。
- logs/jh018-regression.txt：既有 JH018 契约测试在本分支 OK（只扫 src/，本批未改 src/）。
- 未跑 20 项/125 项或产品完整套件。

## 5 偏离与待裁
1. check_run.py 有模块级 WEAKEN 集合，仅供测试做定向削弱；生产命令不设置。
2. 核对器以只读方式导入适配器的 validate_registry_records / parse_stream_with_host_preamble / associate，与适配器口径一致，但不是完全独立实现。
3. 强制要求显式模型：快照 model_id 为空记 fail（避免 auto 路由额外调用）。若架构师接受 auto，需另行裁决。
4. .gitattributes 未覆盖 scripts/gemini_task01/（本批不得改）。Windows 若开 autocrlf，task.txt 会被转为 CRLF，核对会 fail 并提示 CRLF。建议真实调用单从固定提交读取正文对象字节（git cat-file），或另行裁决加 -text 属性。
5. tools 格式依据的是 Linux 上真实 CLI 0.65.0＋合成端点的样本；Windows 上的路径与格式未观测，遇到会记 unknown 而不是通过。

## 6 故障类覆盖
已覆盖（均为构造输入）：正文不同、task_id 不同、run_id 不存在、无显式模型、宿主收到的提示不同、run 未收集（unknown，不改登记）、旧 host-probe 异任务（fail）；导出字节改动、运行时多出文件、清单指向他提交、CRLF 正文；会话记录缺失或收集时不存在（unknown）、stdout 改字节、登记字段类型错、tool_result 字段形状错、运行中改工作区、非零退出；grep 行与源码不符、read 内容错位、流内摘要不符、未知 grep 格式（unknown）、未截断 read（unknown）、读工作区外路径（fail 且不读）、会话记录缺调用（unknown）、参数不符、无 grep、引用不存在的行、核心点未引用、B7 未引用；引用全对但解释相反、引用全对但 B7 含义遗漏（机械 all_pass，语义仍 required，无任何通过结论）；核对器不含进程/网络调用。
削弱：package_digest、export_tree、binding_request、stdin、workspace_manifest、evidence_digest、adapter_gates、tool_content、required_tools、refs_exist、refs_core，各有对照。
未覆盖：真实模型输出（措辞、引用格式变体，如绝对路径或 Windows 反斜杠）；Windows 路径与 CRLF 源码；glob/list_directory 内容核对（只记录）；grep 匹配本身是否满足正则（各 grep 实现方言不同，只核对行文本）；多次 save 前导与真实 stderr；超大输出截断；TOCTOU。

## 未做与阻塞
未做：真实调用、Windows、认证/代理/模型选择、19 号 Windows 结果相关事项。无阻塞。真实调用前仍需：19 完整 Windows 结果独立验收；用户确认认证方式（API key 环境变量名或其他路线，OAuth 未验证）、显式模型、网络/代理变量名白名单、派发次数（一次派发≠一次 API 请求；maxSessionTurns=40 是轮次配置，不是请求数、token 或费用上限；适配器拒绝 budget_tokens，不能限费）及费用接受条件；未知结果冻结、不重发。下一编号由架构师分配。
