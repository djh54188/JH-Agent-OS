# Hark-派单-18 交付报告：Gemini CLI 0.65.0 已知 stdout 前导兼容

来源：Hark｜Hark-派单-18｜类型：交付报告｜状态：候选，待独立复核；Windows 待用户运行（原字节回放尚未执行）；真实模型调用 0；未启动真实 CLI 或 Node。

## 0 基线、归属与提交
- 新分支 `hark-gemini-stdout-20261024`，从 `e3b2017ad5f5b2c208ca5e286dc67e7073307f42` 拉出。开工前核对：远端无该分支，也无执行AI-10 的实现分支或交付（检索 15 个 heads）；基线 AGENTS.md 的 SHA-256 为 502aa829…6cf；`src/agent_session_gemini.py` 的基线 blob 为 0d523c93…，与 29e0020 一致。工作流分支此后不再追加。
- 提交：
  1. `f6bf47c59688c43d927dede01b59f772520815d8`（父 e3b2017）：修改 `src/agent_session_gemini.py`（blob 31d3cd7c111dfdd0d33a65ad097f9cbe3d0fac38）；新增 `tests/test_hark18_gemini_stdout.py`（blob 373459c4…）和 `tests/fixtures/gemini_cli/fake_cli_h18.py`（blob 8ae04c94…）
  2. `507315d63dd650731d50e78ccb301b91a780f91c`（父 f6bf47c）：新增 `scripts/win/hark18-gemini-stdout.ps1`，SHA-256 为 `299c0b16df7d151c9aa8f3d1064d180d72cfc080a85a5c2b53fc7cba10cdcf7e`
  3. 报告提交（父 507315d）：本报告及 `reports/hark18-gemini-stdout-20261024/`
- 相对 e3b2017 的允许差异只有以上 4 个路径加报告目录。未改 main、n1、证据 ref、旧测试、守卫、fixture、AGENTS、规则或契约。

## 1 实施（一个能力）
- 提示的出处已核对：固定 CLI 包 bundle/chunk-DKZARV6U.js（sha256 4d9cf643…，与 750 审计记录相符）中，ProjectRegistry.save 在 EPERM 或 EBUSY 且尚未到最后一次时，通过 `debugLogger.debug` → `console.debug` 向 stdout 输出 `Rename failed with ${code}, retrying in ${2^attempt*50}ms (attempt ${attempt+1}/5)...`。最终失败走 `debugLogger.error` → `console.error`，输出到 stderr。getShortId 最多调用 save 两次（projects.json 不存在时先写一次空表，再写一次当前数据），之后才进入 headless init。
- 新增 `split_host_preamble` 和 `parse_stream_with_host_preamble`，只接受以下情况：从字节 0 开始，每行完全等于已知文本（fullmatch），以裸 LF 结尾；code 为 EPERM 或 EBUSY；attempt 在 1–4、/5；退避 = 50·2^(attempt−1)；每次 save 的 attempt 从 1 起连续递增；最多 2 次 save、8 行；紧接的下一行必须是 JSON 对象 init 事件。任一条件不满足，前导一律不剥离，完全回到原 parse_stream 的结果。
- `parse_stream` 的对外行为未变：循环抽成 `_parse_lines`，逐字等价，已知提示仍报 `line 1: not JSON`，127 审查冻结的 8 类基线不受影响。`_finish` 改用新函数，行号仍按原始字节计。
- 只要接受了前导，就必须能读到 stderr 证据且摘要相符，并且 stderr 中没有 `Failed to save project registry`，否则记 problem，run 判 failed。
- stdout 证据文件原字节不动。接受前导时，artifact 增加 `host_diagnostics`（逐行原文、字节数、sha256、code、delay、attempt，以及前导和整份 stdout 的 sha256），unverified 中加一条说明。其余门（会话和模型关联、工具配对及拒绝确认、工作区完整观测、exit 0、host result=success、证据存在、unknown 不重发）原样保留。declared_complete 仍注明“independent review decides success”。

## 2 分级结论
- A 已证（云端、构造输入）：同一构造输入下，基线 e3b2017 判 failed（`line 1: not JSON`）；候选判 declared_complete，保留原 stdout 字节并给出诊断。无提示正控两版都是 declared_complete，且都没有 host_diagnostics。已知提示叠加 exit 1，两版都判 failed。
- B 已证（逻辑）：把已知行加在 750 提交的 pair-node22-warm 原 stdout 前面构造输入，并覆盖钉死的摘要和会话，原件回放测试的断言逻辑通过。这只说明断言写法有效，不是原字节回放。
- C 未证：node22-run1 原件（3212 字节，sha256 4f8effca…4aa）的原字节回放，需要用户在 Windows 运行。云端拿不到原件，对应测试按 skip 计入，不算通过。
- 旧 run、旧状态和旧审计 JSON 均未改动、未重算。也没有拿旧 ok=true 当作本批的证明。

## 3 验证与原始日志（干净 worktree detached 于 507315d；Python 3.14.8 / Linux aarch64 / git 2.47.3 / PowerShell 7.4.6）
- `tests.test_hark18_gemini_stdout` 连续 3 次：每次 Ran 20，OK，skipped=1，跳过的只有 TestOriginalRun1Replay（缺原件）。
- 8 项守卫先做有效性对照、再定向削弱，3 次结果均为 control pass 8、检出 8、MISSED 0。8 项是：exact_line、delay_matches_attempt、attempt_sequence、bounds、init_follows、leading_only、final_save_failure、stderr_required。
- 相关回归：hark11、hark12、hark13、jh018 共 125 个测试，基线和候选都是 OK，逐测状态相同（Linux）。Windows 上已知的 4 条平台失败或错误债没有在本批重跑，也没有修，旧测试和守卫未改。
- Windows 入口在 Linux pwsh 上空跑（真实 fetch 固定候选，构造路径）：缺原件时 INCOMPLETE exit 3；给一个假原件时 FAIL exit 1（digest 不符，原件回放没有执行）。都不是 Windows 证据。
- PS 解析错误 0；脚本非 ASCII 行 0。
- 独立提取：`python reports/hark18-gemini-stdout-20261024/extract_counts.py`，输出另存在 logs/extract-output.txt。
- 原始日志在 logs/：hark18-tests-run1..3、weakening-run1..3、regression-base-e3b2017、regression-candidate、probe-base-vs-candidate.json、constructed-replay-logic、linux-dryrun-*、post-commit-identity。

## 4 偏离与消费者影响（待裁决）
1. 新增 artifact 键 `host_diagnostics`，只在接受前导时出现；无前导时 artifact 与基线同形。消费者如果按固定键集校验 artifact，需要知道这个键。verify_hark11_gemini_bridge.py 只读具体字段，不受影响。
2. 新增模块函数 `split_host_preamble`、`parse_stream_with_host_preamble`、`_parse_lines` 和常量。`parse_stream` 签名和返回不变。
3. 接受前导时新增 stderr 门：stderr 证据必须存在且摘要相符，且不含最终保存失败文本。这是收紧，不是放宽；无前导时不检查。
4. 上限取 2 次 save、8 行，依据是源码中 getShortId 的两个 save 调用点；同一进程更多次 save 的情况没有在源码之外实测。
5. 只接受 LF。如果原件实际是 CRLF，Windows 格会判 FAIL，按原样回报，不放宽。
6. 测试夹具是新增的包装器（fake_cli_h18.py），旧 fake_cli.py 未改。

## 5 故障类覆盖与未覆盖
- 已覆盖：已知单行；EBUSY；完整 4 次一组；混合 code；两次 save；8 行上限。拒绝的情况：未知行、相似 code、退避与 attempt 不符、未知退避、attempt 0 或 5、/6、截断、尾随文本、前导空格、CRLF、BOM、从 attempt 2 起、跳号、3 次 save、9 行、空行插入、JSON 字符串形式、后面不是 init、后面是坏 JSON 或非对象、提示后直接 EOF、行中截断、同文出现在 init 后或 result 后或已接受前导之后、坏 UTF-8、JSON 字段内同文不剥离、原行号保持。叠加已知提示仍判 failed 的组合：非零退出、会话不符、模型不符、工具不配对、拒绝未确认、工作区被改、host result=error、缺 result、init 后出现未知行、result 后出现同文、stderr 中有最终保存失败、stderr 证据缺失。另外覆盖了再次 collect 不重发。
- 未覆盖：node22-run1 原字节回放（待 Windows）；真实 CLI 或 Node；其他 CLI 版本（本批只认 0.65.0 的文本）；save 超过 2 次；Windows 上的完整产品套件；真实模型或认证。

## 6 提交后复验、阻塞与下一步
- 提交后在干净 worktree 复验：HEAD、父、src blob、允许差异、AGENTS 哈希、脚本哈希、3 次计数、回归、对照，均与本报告一致（logs/post-commit-identity.txt）。
- 阻塞：无。原件回放等用户在 Windows 运行。
- 下一步：架构师复核 f6bf47c / 507315d 和本报告；审查通过后用户运行下方命令。PASS=0、FAIL=1、INCOMPLETE=3 都原样转交，不重试。证据分支 win-evidence-hark18-20261024 由脚本在新目录中本地提交，用户确认后自行 push。下一编号 Hark-派单-19。

审查通过后的用户命令（PowerShell 5.1；需要 git 和 Python 3.10 以上，只用标准库）：
```powershell
$d='D:\JH-Agent OS\data\hark18-gemini-stdout\script'; New-Item -ItemType Directory -Force -Path $d | Out-Null
Invoke-WebRequest -UseBasicParsing -Uri 'https://raw.githubusercontent.com/djh54188/JH-Agent-OS/507315d63dd650731d50e78ccb301b91a780f91c/scripts/win/hark18-gemini-stdout.ps1' -OutFile "$d\hark18-gemini-stdout.ps1"
if ((Get-FileHash "$d\hark18-gemini-stdout.ps1" -Algorithm SHA256).Hash -ne '299C0B16DF7D151C9AA8F3D1064D180D72CFC080A85A5C2B53FC7CBA10CDCF7E') { throw 'script hash mismatch - stop' }
powershell -NoProfile -ExecutionPolicy Bypass -File "$d\hark18-gemini-stdout.ps1"; "exit=$LASTEXITCODE"
```
运行后把完整输出和 run-*\out\result.json 原样转交。只有架构师看过结果后，才执行脚本最后打印的那行 `git -C ... push ... win-evidence-hark18-20261024`。
