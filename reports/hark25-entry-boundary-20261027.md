# Hark-派单-25 交付报告（24 入口边界整改）

来源：Hark B｜Hark-派单-25｜类型：整改交付｜状态：候选，待独立复核
真实调用（模型/CLI/Node/Gemini/用户代理）：0。RunReal/Candidate 用户操作继续禁止；Prepare 待架构师审查。

## 0 基线与提交
- 分支 hark-a-gemini-task01-entry-20261026，基线 c4b81c9（开工核对：本地=远端=c4b81c9，干净，无并行写入）。AGENTS sha256 502aa829…6cf 一致。
- 功能提交 d671411949d984c6c8f5ed492f8e82ce4d459f9a（唯一父 c4b81c9）；本报告为其后一个提交。
- 关键 sha256：PS1 178fd9b4…aa72（blob 044300ff73a8f78d3b49228dfde36f3db43e304f，ASCII）；entry.py bf88f3a2…e366；测试 e1d214fa…b7c3。fixtures 见 logs/extract-output.txt。
- PS1 钉住的树：src eef830e7…、scripts/gemini_task01 a7cbc257…、scripts/gemini_task01_run 67c07ced0102f71ae26fec28d8512b05244da41a。

## 1 差异（相对 c4b81c9）
M entry.py、PS1、fake_node.py、测试；A fixtures/hark24/{audit_run,git_spy,inproc_run,probe_entry,stub_entry}.py；D fixtures/hark24/guarded_prepare.py（父进程 lookup 守卫，已被子进程侧 git_spy 取代）。
A src/verify/19、B scripts/gemini_task01、hark21 测试/夹具、JH018、AGENTS、docs、旧报告：相对 95d941e 差异 0。

## 2 整改
R1 PS1 Invoke-Driver：`& $Python -I -B entry.py … | Out-Host`，退出码单独存 `$script:DriverExit=[int]$LASTEXITCODE`，`exit` 该整数；PS 自身拒绝固定 exit 2。
R2 来源/上下文/边界：
- PS Prepare：init→add origin（核 get-url）→只 fetch -Commit→detached；核 HEAD、自身 blob、三棵钉住树、`status --ignored --untracked-files=all` 为空，之后才运行任何仓库代码。
- PS RunReal/Candidate：要求 -ContextSha256（审阅值）；只认 <Parent>\ctx-<stamp>\context.json，repo 由路径推导为 <Parent>\repo-<stamp>，不用 JSON 字段选代码；先比对 sha，再对推导 repo 做同样的树/干净检查，然后才运行 Python（`-I -B`，不把 cwd 放进 sys.path、不写 pyc）。
- Python run-real：--context-sha256 必填；唯一布局推导所有路径（工作/目标/清单/证据/cli-home/父级标记）；context 各字段类型及与推导值相等后才继续；sidecar 不再作信任依据。
- 清单：先 digest，再形状（文件集必须等于钉住集合），只按钉住名打开导出文件，不打开清单列出的任何路径。
- 错误类型/非对象 → 稳定 REFUSED（不出 traceback）；未捕获异常 → INTERNAL_ERROR exit 9，只显示类名。
- 单次门范围：一个显式 Parent + 一个 context；手动换 Parent 不解除先前未知请求（写入文件头）。
R3 派发后：submit 抛出时，只有登记可读、校验通过且事件恰为 [reserved, dispatch_failed] 才判 LAUNCH_FAILED_RECORDED（exit 7）；缺失/不可读/无效 → DISPATCH_UNKNOWN（dispatched=null，exit 6）。collect/status/check/写报告/摘要失败 → DISPATCHED_INCOMPLETE（exit 8，记 stage 与 error_class，不记消息）。结果写不出 → stdout 非通过 + stderr，exit 9。均冻结，不重派、不杀、不补登。
Candidate：一个 candidate.json，由白名单字段+格式正则组成（不符即 "<withheld>"），阶段 prepare-archive(exit 10)/failure-archive(11)/collected(0)；用 plumbing（临时 index、commit-tree、只新建 update-ref）提交，HEAD/工作区不变；只有 origin=固定远端时才给 push 提示；从不 push。
R4：Python prepare 先把自身环境裁到 PATH/SYSTEMROOT/WINDIR/COMSPEC/PATHEXT/TEMP/TMP/LANG/LC_ALL，所以 B 导出器（未改）的 git 也只继承这些；自己的 git 显式传最小环境。PS Prepare 在进程范围做同样裁剪；如需代理访问 GitHub，可用 -GitHttpsProxy（只作 fetch 的 `-c http.proxy`，不允许凭据）。终端只打印白名单字段；完整 check-report 留本机；公开候选不含自由文本，不依赖 env 里的 key 值，也不存 key。
操作：-Parent 必填，无 D 盘默认；示例放项目 data 下。

## 3 测试（Linux aarch64，CPython 3.12.15，官方 pwsh 7.4.6 linux-arm64，tar sha256 c0159b03…8624）
- logs/hark25-tests-run1..3：每次 31 项 OK，跳过 0（AST 计 31）。
- PS 脚本层（真实 PS1，pwsh）：Prepare 真实 entry，父目录带空格，exit 0/4 与 JSON 一致；git_spy 记录 PS 自己的 fetch 和 B 导出器的 cat-file，所有子进程无哨兵、无 GEMINI/GOOGLE/HTTPS_PROXY/无关变量名。stub 驱动：RunReal 与 Candidate 各 8 种（无/单行/多行 JSON × exit 0/3/4/6/7/11，含 stderr），外层 exit 均等于驱动 exit，输出可见；Prepare stub 多行 exit 4 → 4。
- 来源绑定（Candidate/RunReal 各跑）：24 原复现 {"repo":非git探针目录}、规范布局+非 git 探针、探针 git 克隆（树不符）、sha 不符、数组 JSON → 全部 PS REFUSED exit 2，探针哨兵不存在。
- Python：正常一次派发 COLLECTED/all_pass/0，semantic required，宿主只拿白名单变量，endpoint 用 context 值；同 context 二次和同 Parent 新 context FROZEN；改 dispatch_marker/evidence_root/repo/workspace/export_manifest 字段并重算 sha → REFUSED，字段名写入 summary；三个并发只派发 1 次；未授权/无短语/sha 错/无 key/Node v24/CLI 0.66.0 → 零派发、零标记；数组/数字/字符串/坏 JSON/model 为 int/attempt 为 str/缺字段 → REFUSED 无 traceback；清单加 ../outside（含同步改 ctx digest 的变体）→ REFUSED，审计钩子显示越界路径 0 次 open；导出被改/缺失 → REFUSED。
- 派发后（真实适配器，进程内注入构造故障）：lose/corrupt_registry → DISPATCH_UNKNOWN 6；collect/status/check 抛出 → DISPATCHED_INCOMPLETE 8，有结果文件和 stage；哨兵不在终端/结果里；之后再跑 FROZEN，仍只派发 1 次。宿主侧把 registry 换成目录 → 非 0 且 dispatched≠false。work root 只读 → RESULT_NOT_RECORDED 9。超时 → TIMEOUT_UNKNOWN，进程仍活。启动失败 → LAUNCH_FAILED_RECORDED 7。
- Candidate：只 Prepare → PREPARE_ARCHIVE 10（无 check 段、HEAD 不变、无 push 提示）；超时 → FAILURE_ARCHIVE 11；正常 → COLLECTED 0。反射哨兵（长值、短值 "Zq9"，各在 env 保留/清空两种情况）：终端和 candidate.json 都没有，本机 check-report 有。
- 削弱（改临时克隆里的副本，见 extract-output）：authorize/single_dispatch/node_version/key_presence/endpoint/reviewed_context_sha/derived_paths：对照非 0、零派发 → 削弱后 1 次派发 COLLECTED/all_pass/0。manifest_names：越界 open 0→1。unknown_after_submit：DISPATCH_UNKNOWN→LAUNCH_FAILED_RECORDED（dispatched=false）。post_dispatch_stage：有结果文件 8→无结果文件 INTERNAL_ERROR。public_whitelist：哨兵公开 否→是。prepare_env_scrub：泄漏子进程 0→28。candidate_stage：10→0 COLLECTED。PS exit_separation：4→0；PS source_binding：探针未运行→已运行。三次结果一致。
- 依赖：hark21 113 项 OK（B 代码未改）。按派单未重跑 265。

## 4 偏离与披露
1 derived_paths / manifest_names 是结构性修复（路径推导、只按钉住名打开）：单删相等检查不会再开洞，所以削弱方式是"删检查 + 改回用 JSON 字段/清单条目"，即重新引入原缺陷。
2 PS 5.1 未实跑；pwsh 7 不等于 5.1（原生 stderr、编码可能不同）。Windows 真实 node.exe/gemini.js、真实 GitHub fetch 均未测（测试用带标记的本地 bare 远端，-TestConfig 是仅测试参数，生产命令不带）。
3 RunReal 的 CLI 版本探测沿用 A 的 default_version_probe（宿主行为，可能带 key 变量），仅限显式 RunReal；Node 版本探测用最小环境。冗余的 CLI 版本门被 A 遮蔽，未削弱。
4 submit 前就被适配器拒绝的情形（例如宿主预检）同样记为 DISPATCH_UNKNOWN（保守，无法凭缺失证据区分）。
5 dispatch 标记仍在 submit 之前写入；写入后任何失败都会永久冻结该 Parent。
6 fake_node 增加 sabotage_registry；guarded_prepare 删除并由 git_spy 取代（更强）。
7 沙箱原本是 blob:none 部分克隆，测试前已全量补取（只影响沙箱）。

## 5 阻塞
需架构师审查 PS1 后才能让用户跑 Prepare；真实调用需另行准入 + 用户确认费用边界；已知 8787 是 Anthropic 协议，不能当作 Gemini 原生协议用；PS5.1 实证待用户在审查后的 Prepare 中提供。
