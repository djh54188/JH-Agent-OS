# Hark-派单-14 交付报告：新分工切换与一次性 Gemini 交接同步包

来源：Hark｜Hark-派单-14｜类型：交付报告
规则：AGENTS.md v2（Git blob SHA-256 `edc9846b33c908ee6f16597cd2dd920f1c0564489271713a027bbe7e24ea74b3`，已在基线核对）→ 本批产出 v3 候选
基线：`29e00201f85063f60cc23213262fc82cf430d7ee`；分支 `hark-workflow-handoff-20261023`
平台：GitHub 云端副本，Linux aarch64；Python 3.12.15；git 2.47.3；PowerShell 7.4.6（linux-arm64 官方 tarball，仅用于测试）
是否含真实调用：无（真实模型 0；未触碰 key、代理或用户凭据）
是否含 Windows 格：有，仅一项：用户运行 `scripts/win/hark14-handoff-sync.ps1`

开工核对（2026-10-23）：远端 `main` = `bc3e0f2…`，`n1-artifact-entry` = `b07213d…`，`hark-gemini-subagent-20261021` = `29e0020…`；`win-evidence-exec09-20261023`、`handoff-local-gemini-20261023` 两个目标 ref 在远端不存在；`750b816…` 不在云端（本机独有，符合预期）。没有改 main、n1、Gemini 原分支、架构分支或 PR #1。

## 0. 范围理解

只做两件事：一是落盘用户决定的 v3 分工（独立提交）；二是交付一个由用户运行的一次性同步入口（另行提交），把本机冻结包和 750 证据提交同步到两个固定新 ref。Hark 未读过本机包的内容，入口靠固定摘要和逐文件检查来约束它。

## 1. 分级结论

| 层 | 状态 |
|---|---|
| 规则 v3 候选（4 个文档） | 实现完成，提交 `081e20f`，待架构师独立复核 |
| 同步入口与离线测试 | 实现完成，提交 `1102105`；云端离线 21/21 通过，连续 3 次；6 项守卫削弱后都能检出 |
| Windows 实际运行 | **待用户运行**（未运行，也未在 Windows 或 Windows PowerShell 5.1 上测过） |
| 远端同步（目标 A、B） | **未执行**。两个 ref 在远端都不存在，要等用户带 `-Publish` 运行并复读通过后才算完成 |
| 独立验收 | 待架构师 |

## 2. 提交与改动

| 提交 | 父提交 | 文件 |
|---|---|---|
| `081e20f5ab05d9e6859066337dd019e510ddcfaf` 规则 v3 | `29e00201f85063f60cc23213262fc82cf430d7ee` | AGENTS.md、docs/collaboration-rules.md、docs/templates/execution-task.md、docs/handoffs/architect-current.md |
| `11021059f84c46941d17bd482e33f11ff7176b5d` Windows 入口 | `081e20f…` | scripts/win/hark14-handoff-sync.ps1（新增）、tests/test_hark14_handoff_sync.py（新增） |
| 本报告提交（SHA 见推送回执） | `1102105…` | 本报告、reports/hark14-workflow-handoff-20261023/（check_h14.py、logs/） |

v3 规则落盘内容（只含已批准的 6 条，不新增业务权限）：
- Hark 是唯一实施方；执行 AI 角色撤销，不再发行其编号，历史保留。
- 架构师职责。
- 用户职责，以及脚本不得上传凭据。
- Windows 格的脚本规则；缺条件判为非通过；自检不能代替验收。
- 每批声明是否含真实调用，证据绑定代码提交、脚本提交、平台和日志。
- 路由仍列“执行AI（已撤销，无需发送）”，回传抬头改为“来源：Hark”。

两份规则文件各附“v3 变更记录”。交接文件只在末尾追加 v3 一节，下一编号写为 Hark-派单-15。

脚本固定：`scripts/win/hark14-handoff-sync.ps1` 位于提交 `11021059f84c46941d17bd482e33f11ff7176b5d`。文件 SHA-256 为 `1c61141130db3d990eb595086a67157c29b3e8ae75a84a14ea36f9ba5ec0e206`（LF 换行，纯 ASCII，PowerShell 解析错误 0）。

### 用户运行命令（架构师审查通过后，在 Windows PowerShell 中原样粘贴）

第 1 步：下载固定版本并核对摘要，然后只做准备检查，不推送。预期 `VERDICT PREPARED_NOT_PUBLISHED (exit 10)`。

```powershell
$d = 'D:\JH-Agent OS\data\hark14-handoff-sync\script'
New-Item -ItemType Directory -Force -Path $d | Out-Null
Invoke-WebRequest -UseBasicParsing -Uri 'https://raw.githubusercontent.com/djh54188/JH-Agent-OS/11021059f84c46941d17bd482e33f11ff7176b5d/scripts/win/hark14-handoff-sync.ps1' -OutFile "$d\hark14-handoff-sync.ps1"
if ((Get-FileHash "$d\hark14-handoff-sync.ps1" -Algorithm SHA256).Hash -ne '1C61141130DB3D990EB595086A67157C29B3E8AE75A84A14EA36F9BA5EC0E206') { throw 'script hash mismatch - stop' }
powershell -NoProfile -ExecutionPolicy Bypass -File "$d\hark14-handoff-sync.ps1"; "exit=$LASTEXITCODE"
```

第 2 步：第 1 步为 exit 10 时才运行。这一步发布并复读远端，预期 `VERDICT PUBLISHED_VERIFIED (exit 0)`。

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File 'D:\JH-Agent OS\data\hark14-handoff-sync\script\hark14-handoff-sync.ps1' -Publish; "exit=$LASTEXITCODE"
```

退出码：0 已发布且复读一致；10 检查全过但未发布；1 有检查失败，本次未发布任何东西；2 已尝试发布，但远端状态与预期不符（部分或不一致，见 result.json）。每次运行都在 `D:\JH-Agent OS\data\hark14-handoff-sync\run-<时间戳>\` 下新建目录，写入 `sync-log.txt`、`result.json`、`commit-message.txt` 以及两个隔离裸库。证据留在本地，不进 Git。用户回传 result.json 里的 `verdict`、`checks`、`planned`、`remote_after` 即可。

读写、网络与副作用范围：
- **读**：冻结包目录（逐文件）；源仓库 `D:\JH-Agent OS\data\worktrees\n1-artifact-entry` 的对象，只用只读 git 命令（`rev-parse`、`cat-file`、`rev-list`），外加隔离库通过 alternates 只读引用它的 objects 目录。
- **写**：只写新建的 `run-*` 目录。不在原树、n1、08/09 树中 stage、commit、切分支或清理；不改全局或系统 git 配置，所有配置都用 `-c` 或隔离库的本地配置。
- **网络**：只连 `https://github.com/djh54188/JH-Agent-OS.git`：fetch 基线 29e0020、`ls-remote` 查两个 ref。只有带 `-Publish` 才执行一次 `git push --atomic`，refspec 只有两条且不强推；之后在新的无 alternates 裸库重新 fetch 两个 ref 做核对。
- **认证**：用本机 git 原生认证，脚本不读 home 凭据，不索取 key。认证不足时 git 报错，判为非通过，交给用户处理。

## 3. 计数

| 数字 | 来源 | 提取 |
|---|---|---|
| 21 测试 × 3 次全 OK | reports/hark14-workflow-handoff-20261023/logs/hark14-tests-run{1,2,3}.txt | `python reports/hark14-workflow-handoff-20261023/check_h14.py` |
| AST 测试方法 21 | tests/test_hark14_handoff_sync.py | 同上 |
| 守卫削弱 6/6 检出 × 3 | logs/weakening-run{1,2,3}.txt | 同上 |
| 无 PowerShell 时 21 skipped | logs/hark14-tests-no-pwsh.txt | 同上 |
| 解析错误 0、非 ASCII 行 0、脚本摘要 | logs/parse-check.txt | 同上 |

运行命令：`HARK14_PWSH=<pwsh> HARK14_WEAKENING_LOG=<log> python3 -m unittest -v tests.test_hark14_handoff_sync`，在干净 worktree（detached 于 `1102105`）中执行。完整产品套件按派单不运行；新测试在没有 PowerShell 时整组 skip。

## 4. 偏离清单（请裁决）

1. **manifest 结构为假定。** Hark 没见过本机 MANIFEST.json，按架构师既有 port-manifest 风格假定为 `{"files":[{"path","sha256","bytes"},…]}`，path 相对包根，且就是目标仓库路径（以 `reports/` 开头）。不符时脚本在发布前判 `manifest.schema` FAIL，只打印字段名，不打印内容。请架构师用本机 manifest 确认，不符则告知字段名，Hark 改脚本。
2. **源仓库默认指向 n1-artifact-entry 工作树**（通过 `--git-common-dir` 找共享对象库）。如果 750b816 不在这个库里，脚本判 `source.evidence_commit_present` FAIL；请架构师指明含 750 的仓库路径，再固定到 `-SourceRepo`。
3. **归档提交元数据固定**：作者和提交者为 `JH handoff sync <handoff-sync@users.noreply.github.com>`，日期 `2026-10-23T00:00:00+08:00`，提交说明固定。这样重复运行会得到同一 SHA，可做幂等核对。
4. **远端同名分支的处理。** 已存在同名分支时只接受“SHA 与本次确定性归档提交相同”，其余一律停下，包括内容相同但 SHA 不同的情况。这比“相同归档”更严。
5. **增加了测试专用参数** `-RemoteUrl` 和 `-TestConfig`。带 TestConfig 时禁止指向正式远端；不带时禁止非正式远端。正式命令不传这两个参数。
6. **新测试放在 `tests/`。** Windows 全套件中，PowerShell 不在 PATH 时这组测试 skip，在 PATH 时会实际运行（链接测试在 nt 上 skip）。这会让套件总数加 21，请裁决是否接受这个位置。
7. **architect-current.md 只在云端旧版（10-20）末尾追加 v3 一节。** 本机更新的交接文件随归档进入 B 分支，两份需要由架构师合并、确定以哪份为准。
8. 未修改已验收的测试、守卫或契约，未删除函数，未改字段语义。

## 5. 故障类覆盖表

| 类别 | 注入 | 预期 | 证据 | 状态 |
|---|---|---|---|---|
| 正常（准备） | 合法包 | exit 10，远端无变化，源库、包、全局配置字节不变 | test_prepare_only_does_not_publish | 覆盖 |
| 正常（发布） | 合法包，全局 `core.autocrlf=true` | exit 0；A 精确；B 父为基线、只新增；CRLF、二进制、中文和空格路径 blob 字节等于原文件；MANIFEST 原字节 | test_publish_then_rerun_idempotent | 覆盖 |
| 重复运行 | 发布后再跑 | exit 0，refspec 为空，同一 SHA | 同上 | 覆盖 |
| 一个已存在且相同 | A 预先存在 | 只推 B | test_one_ref_already_present… | 覆盖 |
| 篡改 | 改 1 字节 | FAIL，零推送 | test_tampered_payload_byte | 覆盖 |
| 缺件 / 多件 | 删文件 / 加文件 | FAIL（文件集合） | test_missing…、test_extra… | 覆盖 |
| manifest 摘要、计数、总字节 | 改摘要或固定数 | FAIL，且不解析 manifest | test_manifest_digest…、test_manifest_count… | 覆盖 |
| schema 不符 | 用 size 代替 bytes | FAIL，并给出字段名 | test_schema_mismatch… | 覆盖 |
| 路径逃逸 | `..`、绝对路径、盘符、反斜杠、非 reports/、`.`段、设备名、尾点、`.git`、占用归档名，共 11 种 | FAIL（白名单） | test_path_escapes | 覆盖 |
| 大小写重名 | 大小写不同的同名路径 | FAIL | test_case_insensitive_duplicate | 覆盖 |
| 链接 / 重解析点 | 文件符号链接、目录符号链接 | FAIL，不跟随 | test_links_refused（Linux） | 覆盖；Windows junction 未实测 |
| 同名异主目标 | 目标路径已在基线 | FAIL | test_target_exists_in_base | 覆盖 |
| 源身份 | 750 缺失 / 父提交不是基线 | FAIL | test_evidence_* | 覆盖 |
| ref 冲突 | 远端 A 或 B 已指向他处（B 为祖先，否则会快进） | FAIL，两者都不推 | test_ref_*_conflict… | 覆盖 |
| 部分失败 | pre-receive 拒绝 B | atomic，A 也不留，exit 1 | test_push_rejected_is_atomic | 覆盖 |
| push 成功但远端不符 | post-receive 改写 B | exit 2，不判通过 | test_remote_differs… | 覆盖 |
| 测试配置误指正式远端 | TestConfig 加正式 URL | 拒绝 | test_production_remote… | 覆盖 |
| 守卫削弱 | digest、fileset、path、reparse、refconflict、postverify | 先跑对照通过，削弱后都检出 | TestGuardWeakening | 覆盖 |
| **未覆盖** | Windows PowerShell 5.1 实跑；NTFS junction 和 ADS；真实 GitHub 认证、代理与 atomic 支持；ls-remote 到 push 之间远端被他人建 ref（无强推，非快进会被拒；若对方建在祖先，push 后复读判 exit 2）；本机真实包与假定 schema 是否一致；本机 750 所在仓库 | — | — | 未覆盖，需 Windows 运行或架构师确认 |

## 6. 未做与阻塞

- 未运行 Windows，未发布任何远端目标，未读本机包，未做 stdout 兼容（留待 Hark-派单-15）、GUI、测试债、真实模型或产品合入。
- 偏离 1、2 需要架构师在审查时用本机现场确认；不符时不能发布，脚本也会自行判 FAIL。

## 下一步路由

架构师：审查 v3 规则候选（081e20f）和脚本（1102105，SHA-256 1c6114…），裁决偏离 1–7，确认本机 manifest 字段和 750 所在仓库。
执行AI：已撤销，无需发送。
Hark：等待裁决；之后按派单处理 Hark-派单-15。
用户：把本报告的转发块转给架构师；审查通过后再按第 2 节两步运行脚本，回传 result.json 摘要。
一句话现状：新分工规则和同步脚本已在云端写好并离线测过；还没有在 Windows 跑，也还没有同步任何东西到 GitHub。
