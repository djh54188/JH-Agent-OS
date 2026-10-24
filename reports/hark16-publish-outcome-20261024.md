# Hark-派单-16 交付报告：发布结果不确定性（R4）

来源：Hark｜Hark-派单-16｜类型：交付报告。状态：候选，Windows 待用户运行，待架构师独立验收。本报告不是验收结论，没有执行真实同步发布。

## 0. 基线与归属
- 基线 `beb364e64cf37d7b8edf90774ef4039c6471506e`。开工时远端分支 HEAD 一致，工作区干净，无并行提交。AGENTS.md SHA-256 `502aa829…6cf` 一致。
- 本批没有创建两个目标 ref，也没有接触真实 GitHub 同步目标。用户若已按 f032a6a 跑过默认准备，那个结果属于旧脚本，本报告不引用它作为新提交的验证。

## 1. 提交（追加，未改写历史，未强推；15 报告和日志未改）
| 提交 | 父 | 文件 |
|---|---|---|
| `6cd4d51404faf129239076766b75c1e227cb8dcf` | `beb364e` | scripts/win/hark14-handoff-sync.ps1、tests/test_hark14_handoff_sync.py |
| 本报告提交（见转发块） | `6cd4d51` | 本报告、logs/、probe_old_vs_new.py、extract_counts.py |

新脚本 SHA-256：`fa663838fb0c733a15645e456a2f1016e64a469fcea86c16afdfff197401ee12`（f032a6a / f39beb34… 的准备授权不自动延伸到新版）。

## 2. 实施
- 推送尝试之后，结论只由两样证据共同决定：push 回执（只解析 stdout 的 porcelain）和独立复读（新建、无 alternates 的裸库；先 `ls-remote` 两个目标，再 fetch 存在的目标）。复读本身失败或不完整时记 `after.reread_complete=FAIL`。
- 判定：
  - exit 0 `PUBLISHED_VERIFIED`：push exit 0，且完整复读的身份、父、文件集合、原字节全部吻合。
  - exit 2 `TARGETS_VERIFIED_PUSH_UNCONFIRMED`：完整复读全部吻合，但 push 非零。说明目标可用，不断言是本次创建；保留原退出码和原输出。
  - exit 1 `REJECTED_VERIFIED`：回执中每个 refspec 各有一条 `!` 行、没有成功、强制、= 或删除行、以 `Done` 结尾，且完整复读中没有任何本次推送的值。
  - exit 2 `PUBLISH_OUTCOME_UNCERTAIN`：其余已尝试情况，包括复读不可用或不完整、回执缺失、截断或格式不全，以及 trap 异常。日志写明“远端可能已更新，不要重试，原样回传”。
  - exit 2 `PARTIAL_OR_MISMATCH`：push exit 0，但完整复读不符。
  - exit 10 和发布前的 exit 1 不变。
- 所有 exit 2 路径都不重试、不覆盖、不回滚、不清理。
- 仅测试模式的确定性注入：`fake_push`（`commit_hide` 真推到夹具后隐藏回执；`nocommit_hide` 不推、只给回执；`truncate` 截断真实回执）、`verify_unavailable`、`verify_fetch_fail`、`inject_after_push`。只作用于 R2 已限定的本地临时夹具。
- 顺带修正：回执原先与 stderr 合并，`error: failed to push…` 会与 `Done` 随机交错。首轮全量测试中 test_ancestor_race_b 因此偶发一次误判为不确定。现改为只用 stdout 判断，stderr 仍保留在 output 字段。

## 3. 验证（干净 worktree detached 于 6cd4d51；PowerShell 7.4.6 Linux aarch64 / git 2.47.3 / Python 3.12.15）
- 48 个测试连续 3 次 OK，其中 R4 13 个（logs/hark16-tests-run{1,2,3}.txt）。
- 削弱 10 项（新增 rejectreceipt、uncertain）：3 次都是 control pass 10、检出 10、MISSED 0、skipped 0。
- 无 PowerShell：45 skip，3 个 R3 计数测试实跑。解析错误 0，非 ASCII 行 0。
- 新旧同输入（f032a6a 注入完全相同的测试钩子，判定逻辑不改；logs/probe-old-vs-new.json）：

| 情形 | f032a6a | 6cd4d51 |
|---|---|---|
| 已提交、回执丢失、复读不可用 | exit 1 FAIL，断言未更新（实际已写入） | exit 2 UNCERTAIN |
| 未提交、回执丢失、复读不可用 | exit 1 FAIL，断言未更新 | exit 2 UNCERTAIN（与上一行同观测、同判定） |
| 已提交、回执丢失（exit 128）、复读完整 | **exit 0 PUBLISHED_VERIFIED** | exit 2 TARGETS_VERIFIED_PUSH_UNCONFIRMED |
| 完整原子拒绝、复读完整 | exit 1 | exit 1 REJECTED_VERIFIED |

  第三行是本批新发现的旧缺陷：push 非零被改写成成功。
- R4 测试另覆盖：未提交且无回执但复读完整（UNCERTAIN，保守判定）；部分成功回执，复读不可用时为 UNCERTAIN，复读完整时为 TARGETS_VERIFIED_PUSH_UNCONFIRMED；格式不全的拒绝回执（UNCERTAIN）；push 成功后一个目标复读失败（UNCERTAIN）；push 成功后 B 被删除（PARTIAL_OR_MISMATCH）；push 失败且 B 不符（UNCERTAIN）；完整拒绝但复读不可用（UNCERTAIN，既有 ref 不动）；准备模式带钩子（exit 10，push 为 null）。R1、R2 原有判据（创建拒绝、既有 ref 不动、网络地址拒绝）全部保留。
- 未运行完整产品套件、Node/Gemini 宿主，未改旧产品测试或契约。独立提取：`python reports/hark16-publish-outcome-20261024/extract_counts.py`（输出见 logs/extract-output.txt）。

## 4. 对 15 的纠正
15 报告称“拒绝时无成功行就判 FAIL（本次未更新任何 ref）”，这个结论缺少证据：回执丢失和完整拒绝在旧逻辑里是同一观测。15 还漏了 push 非零但复读吻合时会报 exit 0。15 原件不改，以本节为准。

## 5. 偏离（待裁决）
1. 15 新增的 R1 测试预期，仍是 exit 1，但判据从 updated_lines 为空改为 verdict=`REJECTED_VERIFIED`（R1 的 lease 拒绝属于完整拒绝），创建拒绝、既有 ref 不动的断言保留。test_ref_b_conflict 一类 pre-receive 拒绝同样加了 verdict 断言。
2. 未提交、无回执但复读完整显示目标不存在时，仍判 UNCERTAIN，不判 REJECTED_VERIFIED（保守）。
3. 复读改为先 ls-remote 再 fetch。原先 fetch 缺失 ref 会失败，与网络失败无法区分。
4. 回执解析只用 stdout，见第 2 节“顺带修正”。
5. 新增测试钩子写在入口脚本内，仅测试模式且仅本地夹具生效。

## 6. 故障类、未覆盖、下一步
- 未覆盖：真实 GitHub 丢回执或连接中断（只用夹具与假回执做确定性注入）；GitHub porcelain 的确切文本（以 git 2.47.3 本地输出为准，GitHub 若输出 `[remote rejected]` 也带 `!`）；Windows 本机运行（test_links_refused 在 Windows 按 control=skipped 单列）；真实并发；真实发布。
- 下一步：架构师复核 6cd4d51 → 用户按第 7 节先准备（预期 exit 10）→ 经同意后 Publish。exit 1 或 2 原样回传，不重试，不自行判断。下一编号 Hark-派单-17。

## 7. 用户命令（审查通过后才运行；不要运行 1102105 或 f032a6a 版本）
第一步：
```powershell
$d='D:\JH-Agent OS\data\hark14-handoff-sync\script'; New-Item -ItemType Directory -Force -Path $d | Out-Null
Invoke-WebRequest -UseBasicParsing -Uri 'https://raw.githubusercontent.com/djh54188/JH-Agent-OS/6cd4d51404faf129239076766b75c1e227cb8dcf/scripts/win/hark14-handoff-sync.ps1' -OutFile "$d\hark14-handoff-sync.ps1"
if ((Get-FileHash "$d\hark14-handoff-sync.ps1" -Algorithm SHA256).Hash -ne 'FA663838FB0C733A15645E456A2F1016E64A469FCEA86C16AFDFFF197401EE12') { throw 'script hash mismatch - stop' }
powershell -NoProfile -ExecutionPolicy Bypass -File "$d\hark14-handoff-sync.ps1"; "exit=$LASTEXITCODE"
```
第二步（第一步 exit=10 且架构师同意发布后）：
```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File 'D:\JH-Agent OS\data\hark14-handoff-sync\script\hark14-handoff-sync.ps1' -Publish; "exit=$LASTEXITCODE"
```
每次运行后，把完整输出和 run-* 目录里的 result.json 原样转给架构师。
