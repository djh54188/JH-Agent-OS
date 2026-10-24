# Hark-派单-17 交付报告：复读证据绑定实际取回的 ref（R5）

来源：Hark｜Hark-派单-17｜类型：交付报告｜状态：候选；Windows 待用户运行；待架构师独立验收；未执行真实同步发布。

## 0 基线与归属
- 基线 `0f993899c7c6afac6ae97d63ccff0e8e29f1d5ce`。开工时远端 HEAD 与基线一致，工作区干净，无并行提交。AGENTS.md SHA-256 为 `502aa829…6cf`，与派单一致。
- 两个生产目标 ref 仍不存在。用户此前用 f032a6a 跑过的准备结果属于旧 SHA，不作为本批的验证证据。

## 1 提交（均为追加，未改写历史；16 报告与日志未改）
| 提交 | 父 | 文件 |
|---|---|---|
| `cc15029b75f52d72457e4bba4aa2ea2f62bd2c98` | `0f99389` | scripts/win/hark14-handoff-sync.ps1、tests/test_hark14_handoff_sync.py |
| 报告提交（完整 SHA 见转发块） | `cc15029` | 本报告、logs/、probe_old_vs_new.py、extract_counts.py |

新脚本 SHA-256：`39a9a898a228c8893c06b4ed1ee88c75d67894be510bc01233e5097543ed7dbc`。

## 2 实施
- 复读分两步取值：`ls-remote` 的结果只记为查询值（query_ref_a/b），随后按 ref 名 fetch，再对本地 `refs/verify/x^{commit}` 执行 `rev-parse`，得到实际取回值。
- 身份精确比对，以及 B 的父、diff-tree 文件集合、逐 blob 原字节检查，全部只用实际取回值 `ra/rb`，不读查询值。
- 取回值与查询值不一致时，判定复读不稳定：`after.reread_complete=FAIL`，原因写入 `remote_after.reasons`，同时记录双方 SHA，并注明“非本次检查造成，不归因于本次运行”。fetch 失败、取回后 rev-parse 失败一律按不完整处理。
- 判定沿用 R4：push 已尝试且复读不完整或不稳定时，结论为 `PUBLISH_OUTCOME_UNCERTAIN`（2）；push 非零时保留原退出码。新增一类：本次没有需要推送的内容（幂等），但复读未能核实时，结论为 `NOT_VERIFIED_NOTHING_PUSHED`（2），不写“已尝试发布”。成功门没有放宽，各失败路径都不重推、不回滚、不清理、不覆盖。
- 新增测试注入 `inject_between_query_and_fetch`，只在测试模式、对本地夹具生效，在 ls-remote 之后、fetch 之前执行。支持四种操作：ancestor、fork、descendant（基于 ref^{tree} 新建提交）和 delete。

## 3 验证（干净 worktree，detached 于 cc15029；PowerShell 7.4.6 / Linux aarch64 / git 2.47.3 / Python 3.12.15）
- 55 个测试连续跑 3 次全部 OK，其中 R4 13 个、R5 7 个（含 A 的三种变化子测试）。
- 削弱 12 项（新增 fetchedidentity、rereadstable）：3 次结果均为 control pass 12、检出 12、MISSED 0、skipped 0。fetchedidentity 削弱等价于恢复 6cd4d51 的行为（用查询值且不比对），目标测试 B 后代；rereadstable 只去掉不一致判定，目标测试 A+B 后代。
- 无 PowerShell 环境：52 个 skip，3 个 R3 测试实跑。PS 解析错误 0；脚本非 ASCII 行 0。
- 新旧同输入对照：6cd4d51 注入完全相同的钩子，调用点相同，判定逻辑未改（logs/probe-old-vs-new.json）。

| 布局（查询后、取回前变化） | 6cd4d51 | cc15029 |
|---|---|---|
| 无变化 | 0 PUBLISHED_VERIFIED | 0 PUBLISHED_VERIFIED |
| A 祖先 / 分叉 / 后代 | 均为 0 PUBLISHED_VERIFIED，报告的身份 ≠ 实际 | 均为 2 UNCERTAIN，报告的身份 = 实际 |
| B 后代（旧归档仍可达） | 0 PUBLISHED_VERIFIED，身份 ≠ 实际 | 2 UNCERTAIN，身份 = 实际 |
| A+B 后代 | 0 PUBLISHED_VERIFIED | 2 UNCERTAIN |
| B 消失 | 2 UNCERTAIN（报告的 rb 仍是旧值） | 2 UNCERTAIN，rb 为 null |

以上从本地实际运行完整 PS1 的结果复现了架构师给出的 5 个变更布局缺陷。
- R5 测试另外覆盖：push 非零（128）叠加 A 变化，结论 UNCERTAIN 且保留 128；幂等重跑叠加 B 变化，结论 NOT_VERIFIED_NOTHING_PUSHED，不归因于本次；无变化时查询值等于取回值，结论 PUBLISHED_VERIFIED。原有的正常发布、幂等、单目标已存在、R1/R2/R4 判据全部保留通过。
- 未跑完整产品套件，未跑 Node 或 Gemini 宿主。
- 独立提取：`python reports/hark17-reread-identity-20261024/extract_counts.py`，输出见 logs/extract-output.txt。

## 4 偏离（待裁决）
1. 新增 verdict `NOT_VERIFIED_NOTHING_PUSHED`（exit 2）：本次未推送时，如果用 PUBLISH_OUTCOME_UNCERTAIN，会把外部变化说成“发布已尝试、结果未知”，所以单独区分。
2. 取回值与查询值不一致时一律判“不稳定 → 不确定”。即使取回值本身恰好等于期望值，也不判成功，因为本次查询与取回之间存在不一致的观测。
3. 测试钩子写在入口脚本内，仅在测试模式且对本地夹具生效（与 16 的偏离⑤同类）。

## 5 故障类与未覆盖
- 已覆盖：查询后、取回前目标被改为祖先、分叉或后代，目标消失，fetch 失败，push 非零叠加变化，幂等叠加变化，正常两目标发布，已有精确目标的幂等运行。
- 未覆盖：最后一次取回之后的远端变化（本批按派单不提供锁或监控，只证明本次实际取回的 ref 及其内容）；fetch 进行中服务端 ref 的瞬时变化（以 fetch 落地的 ref 为准）；真实 GitHub 行为；Windows 本机运行（test_links_refused 在 Windows 上按 control=skipped 单列）；真实发布。

## 6 下一步与用户命令
架构师复核 cc15029 → 用户先做准备（预期 exit 10）→ 经同意后加 -Publish。exit 1 或 2 时原样回传，不重试。不要运行 1102105、f032a6a 或 6cd4d51 的版本。下一编号为 Hark-派单-18。

第一步：
```powershell
$d='D:\JH-Agent OS\data\hark14-handoff-sync\script'; New-Item -ItemType Directory -Force -Path $d | Out-Null
Invoke-WebRequest -UseBasicParsing -Uri 'https://raw.githubusercontent.com/djh54188/JH-Agent-OS/cc15029b75f52d72457e4bba4aa2ea2f62bd2c98/scripts/win/hark14-handoff-sync.ps1' -OutFile "$d\hark14-handoff-sync.ps1"
if ((Get-FileHash "$d\hark14-handoff-sync.ps1" -Algorithm SHA256).Hash -ne '39A9A898A228C8893C06B4ED1EE88C75D67894BE510BC01233E5097543ED7DBC') { throw 'script hash mismatch - stop' }
powershell -NoProfile -ExecutionPolicy Bypass -File "$d\hark14-handoff-sync.ps1"; "exit=$LASTEXITCODE"
```
第二步（仅在第一步 exit=10 且架构师同意后执行）：
```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File 'D:\JH-Agent OS\data\hark14-handoff-sync\script\hark14-handoff-sync.ps1' -Publish; "exit=$LASTEXITCODE"
```
每次运行后，把完整输出和 run-* 目录下的 result.json 原样转给架构师。
