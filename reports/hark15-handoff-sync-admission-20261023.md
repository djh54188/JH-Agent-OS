# Hark-派单-15 交付报告：一次性交接同步入口准入与证据有效性

来源：Hark｜Hark-派单-15｜类型：交付报告。状态：候选，Windows 待用户运行，待架构师独立验收。本报告不是验收结论。

## 0. 基线与归属
- 固定基线 `79356b63a1d3885e6f25bc141b5050943538fa9d`；开工时远端 `hark-workflow-handoff-20261023` HEAD 与基线一致，工作区干净，无并行提交。
- 基线 AGENTS.md SHA-256 `859b44f591929c682c9b621cb83db94eeff4015dd6a72cbef99805c31b126f66`，与派单一致。
- 两个生产目标 ref 在本轮 `ls-remote` 中仍不存在；本批未发布、未创建它们。

## 1. 提交（在 79356b6 上追加，未改写 081/110/793，未强推）
| 提交 | 父 | 内容 |
|---|---|---|
| `f577ab614b494b2d881063c67c6910a496c58c0f` | `79356b6` | D1：AGENTS.md 第 14 行、交接追加 |
| `f032a6a864ceb9c32e7620160f084e7800180b6f` | `f577ab6` | R1/R2/R3：scripts/win/hark14-handoff-sync.ps1、tests/test_hark14_handoff_sync.py |
| 本报告提交（见转发块） | `f032a6a` | 报告、原始日志、比对探针、独立提取脚本 |

新脚本 SHA-256：`f39beb34e2e6cbf625a63f47448f0995222194e51cd31ff906ad9c5eb06fb6a6`（14 版 `1c6114…` 作废，不要运行）。

## 2. 实施
- **R1 创建时条件**：每个要推的 ref 都带 `--force-with-lease=<ref>:`（期望值为空，即“必须不存在”），加 `--atomic`。服务端 receive-pack 以旧值“零”做比较交换，ref 在检查后出现（祖先、分叉或任意值）就整笔拒绝；不存在无条件强推，也不会覆盖既有 ref。拒绝时解析 porcelain，无任何 `*`/空格/`+` 行就判 FAIL（exit 1，“本次未更新任何 ref”）。检查之后若他人建成**精确目标**，git 报 `=` up to date，不移动任何 ref，另一目标照常创建，最终复读通过；这符合“抢建为精确目标没有移动异主 ref”。
- **R2 测试远端**：测试模式只接受绝对本地路径，位于系统临时目录下、为裸库（HEAD/objects/refs）且带 `hark14-test-fixture` 标记、本身不是链接；任何 `scheme://`（含 file://）、scp 式 `host:path`、UNC、相对路径都在任何 git 传输前拒绝。另加第二层：所有 git 调用带 `protocol.allow=never`，测试模式只放行 `file`，正式模式只放行 `https`。不是 URL 黑名单。正式模式仍要求 RemoteUrl 与固定生产地址逐字相同（改为区分大小写），正式命令不传 -TestConfig。
- **R3 计数**：`_run_case` 返回 pass/fail/skip；跳过或未运行记为 `control=skipped weakened=not-run`，不计对照通过，也不计检出。
- **测试注入**：仅测试模式下，TestConfig 的 `inject_before_push` 在检查之后、推送之前写入本地夹具，用于确定性复现竞态。正式模式不读取此字段。
- **D1**：第 14 行改为“当前版本：实施派单采用 v3”，v2 原句保留为历史，GUI 批原范围保留。交接只追加了 10-23 Hark-派单-15 一节，注明本机较新交接优先。

## 3. 验证（干净 worktree，detached 于 f032a6a；PowerShell 7.4.6 Linux aarch64、git 2.47.3、Python 3.12.15）
- 本批测试 35 个，连续 3 次 `OK`（logs/hark15-tests-run{1,2,3}.txt）。
- 削弱检测 8 项（原 6 项加 createonly、testremote），3 次都是 8 pass 控制、8 检出、0 MISSED、0 skipped（logs/weakening-run*.txt）。
- 无 PowerShell：35 个中 32 skip，3 个 R3 计数测试实跑通过（logs/hark15-tests-no-pwsh.txt）。
- 解析错误 0，脚本非 ASCII 行 0（logs/parse-check.txt）。
- 新旧同输入比对（logs/probe-old-vs-new.json）：

| 故障类 | 1102105（加同一注入） | f032a6a |
|---|---|---|
| 检查后 A 被建为祖先 | exit 0，最终匹配，移动既有 ref 1 个 | exit 1，移动 0，B 未落地 |
| 检查后 B 被建为祖先 | exit 0，最终匹配，移动 1 个 | exit 1，移动 0，A 未落地 |
| 检查后 A、B 都是祖先 | exit 0，最终匹配，移动 2 个 | exit 1，移动 0 |
| 测试模式 https/ssh/scp 指向 127.0.0.1 | 进入 git 传输 | 传输前拒绝 |
| 削弱辅助遇到跳过用例 | 返回 True（计为通过） | 返回 skip |

- 测试另覆盖：A/B/双目标分叉抢建（拒绝，异主 ref 不变）、检查后建成精确目标（无移动，重跑幂等、零推送）、无竞态正常发布、检查前已有精确目标只推另一边、检查前 B 冲突两边都停；R2 列表：生产 URL 及其大小写和无 .git 别名、http、https、ssh、git://、scp 两种、ext::、UNC 两种写法、file://、相对路径、未标记本地裸库（字节身份不变）、临时目录外的标记库、无 -TestConfig 的非生产远端；再加一项：守卫被削弱后 git 协议层仍拒绝 https。
- 未运行完整产品套件，未改动旧产品测试、守卫或 AgentSession 契约。

独立提取：`python reports/hark15-handoff-sync-admission-20261023/extract_counts.py`（只读日志，输出见 logs/extract-output.txt）。复现比对：`HARK14_PWSH=<pwsh> python reports/hark15-handoff-sync-admission-20261023/probe_old_vs_new.py <out.json>`。

## 4. 纠正 Hark-派单-14 的推断
14 报告称“检查后被他人抢建会 exit 2”，这是错的。若抢建值是目标的祖先，普通 `push --atomic` 会把它快进，exit 0，最终复读全部匹配，旧成功门放行（上表 1102105 列）。只有分叉才会被拒。14 原件不改，以本节为准。

## 5. 偏离（待裁决）
1. R1 使用 `--force-with-lease=<ref>:`。它属于 force 选项家族，但期望值为空时语义是“仅当不存在才创建”：既有 ref 不论什么值都不会被更新，也不存在无条件强推。这是 git push 唯一能把“不存在”交给服务端原子判断的原生方式。若架构师认为该选项字面上违反“不允许强推”，替代方案只能是推送前后复查，无法关闭检查到推送之间的窗口。
2. 检查后被建成精确目标时判 PUBLISHED_VERIFIED，不判 FAIL：没有移动任何 ref，最终状态精确等于目标。
3. 正式地址比较改为区分大小写（`-cne`），只会更严。
4. 测试注入字段 `inject_before_push` 写在入口脚本里，仅测试模式且仅本地夹具生效。

## 6. 故障类、未覆盖与下一步
- 已覆盖故障类：检查后祖先、分叉、精确抢建（A/B/双）；无变化；已有精确目标；双目标整体拒绝；非夹具远端（网络、别名、scp、UNC、file、相对、未标记、临时目录外）；跳过误计。
- 未覆盖：真实并发（只做了确定性注入，未覆盖多客户端在服务端同一时刻交错，依赖 receive-pack 的 CAS 语义）；GitHub 服务端 lease 和 atomic 的实际行为（本地 file 传输已验证，GitHub 支持 atomic 推送，但本批未真实推送）；Windows 本机运行（test_links_refused 在 Windows 会记为 `control=skipped weakened=not-run`，按平台未覆盖单列，不计通过）；真实同步发布；用户本地包读取。
- 下一步：架构师独立复核新脚本 → 用户按第 7 节先准备（预期 `PREPARED_NOT_PUBLISHED`，exit 10）→ 再 `-Publish`（预期 `PUBLISHED_VERIFIED`，exit 0）。exit 1 或 2 时原样回传，不要自行判断或重试。下一未用编号 Hark-派单-16。

## 7. 用户命令（审查通过后才运行；不要运行旧脚本）
第一步：只准备，不推送
```powershell
$d='D:\JH-Agent OS\data\hark14-handoff-sync\script'; New-Item -ItemType Directory -Force -Path $d | Out-Null
Invoke-WebRequest -UseBasicParsing -Uri 'https://raw.githubusercontent.com/djh54188/JH-Agent-OS/f032a6a864ceb9c32e7620160f084e7800180b6f/scripts/win/hark14-handoff-sync.ps1' -OutFile "$d\hark14-handoff-sync.ps1"
if ((Get-FileHash "$d\hark14-handoff-sync.ps1" -Algorithm SHA256).Hash -ne 'F39BEB34E2E6CBF625A63F47448F0995222194E51CD31FF906AD9C5EB06FB6A6') { throw 'script hash mismatch - stop' }
powershell -NoProfile -ExecutionPolicy Bypass -File "$d\hark14-handoff-sync.ps1"; "exit=$LASTEXITCODE"
```
第二步：第一步得到 exit=10，且架构师同意发布后
```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File 'D:\JH-Agent OS\data\hark14-handoff-sync\script\hark14-handoff-sync.ps1' -Publish; "exit=$LASTEXITCODE"
```
每次运行后把完整输出和 `run-*` 目录里的 result.json 原样转给架构师。
