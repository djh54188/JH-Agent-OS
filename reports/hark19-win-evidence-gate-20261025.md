# Hark-派单-19 交付报告：18 单 Windows 入口证据门整改

状态：候选，待架构师独立复核。真实模型调用 0；未启动真实 Gemini CLI / Node / 认证。Windows 实跑待用户（审查通过后）。用户暂不运行 507315d 旧入口。

## 0 基线 / 归属 / 提交
- 分支 hark-gemini-stdout-20261024，基线 02cef57f0e9bd93456f87db7385f3a0cee15b74b（开工时本地=远端 HEAD，status 干净，无并行分支）。AGENTS.md SHA-256 502aa8292c7b4e574156feecc7c09264992f0fea5b7092b30ec4d1cb8d9516cf。
- 功能提交 e2935d4da76c96c03b8177e4bf2edda1025647cb（父 02cef57）：M scripts/win/hark18-gemini-stdout.ps1；A tests/test_hark19_win_evidence_gate.py、tests/fixtures/hark19/git_stub.py、tests/fixtures/hark19/python_stub.py。
- 新脚本 SHA-256 a7212cfafe73e8d00de152d9c96b23e4efcc4927d0eb61219613695fb40de600（507315d 旧版 299c0b16… 作废）。
- 18 产品源码/测试/夹具 blob 不变：31d3cd7c、373459c4、8ae04c94。旧报告、原始日志、证据分支、main/n1 未动。

## 1 实施（只改 Windows 入口）
- 回放源仍固定 f6bf47c；原件读取、3212B/4f8effca…、测试与削弱门、禁止真实宿主/模型均未放宽。
- 结果分两段：REPLAY（PASS/FAIL/INCOMPLETE，非最终）与 EVIDENCE（not_started→各阶段→verified/failed，记 failed_stage 与 error）。
- out\result.json 开头写 RUNNING/final=false 占位；最终 verdict 只在末尾计算一次、写一次、读回核对；终端 VERDICT 行、result.json、exit 三者一致。全文只有一处 `$verdict = 'PASS'`，条件是 evidence.verified。
- 证据提交后独立核验（命令返回 0 或打印 SHA 不算）：identity_before（HEAD=f6bf47c 且干净）→ prepare_dir → snapshot（记录每个文件的字节、sha256、git blob id）→ branch → add → commit → verify_identity（HEAD 为新提交，≠回放源，分支 ref=HEAD，HEAD 符号指向证据分支）→ verify_parent（恰一父=f6bf47c）→ verify_paths（与 f6bf47c 的 diff 恰为预期 A 文件集）→ verify_content（每个已提交 blob = 快照记录）→ verify_clean（含 untracked）。
- 提交内 replay-result.json 只记回放段（final=false，无 verdict 字段），不自引用提交 SHA；提交身份记在外层 out\result.json 与日志。
- 退出码：0 PASS；1 FAIL；3 INCOMPLETE；4 EVIDENCE_NOT_DELIVERED（新）；5 RESULT_NOT_RECORDED（新）。push 命令仅在 evidence verified 时打印。不 push、不重试、不回滚、不强推，无新平台。
- 测试模式（-TestConfig）：remote 必须是带 hark19-test-fixture 标记的本地目录，协议白名单改为 file；可替换 git 可执行、原件常量和三个非 git 注入点（prepare_dir、copy、result_write）。生产命令不传。

## 2 分级结论
- A（云端、构造输入）：同一 git 故障下，旧入口副本（仅换原件两常量）8/8 PASS exit0（7 个故障全 PASS，其中 noop/extra/mutate 还打印 push）；新入口仅正常对照 PASS，7 个故障全为 EVIDENCE_NOT_DELIVERED/4，阶段各自正确，均无 push。
- 回放结果用 python_stub 构造（不跑 Hark18 测试）；另有 1 例真解释器真跑 Hark18 20 测（无原件）→ INCOMPLETE/3、证据 verified、ran 20、detected 8。
- C（未证）：Windows PowerShell 5.1 + 真 Git + 真原件的完整入口实跑，待用户。

## 3 验证（clean worktree detached e2935d4；Py3.14.8 / Linux aarch64 / git 2.47.3 / pwsh 7.4.6）
- test_hark19 连续 3 次：Ran 7 OK（含 14 个证据故障子用例、4 个回放分类、result 写失败、正常对照、真解释器、7 门削弱）；未设 HARK19_PWSH 时 OK (skipped=7)，跳过即计跳过。
- 7 门：final、push、branch_binding、parent、paths、content、clean；每门先有效对照（4、无 push）再定向削弱，3 次均 control_pass 7 / detected 7 / MISSED 0。
- identity_before、no-new-commit、result 读回不列为独立门：其注入场景会被后续门兜住，作为故障注入覆盖。
- 适配器未变，未重跑 125 项或完整套件；未启动 Node/CLI/模型。PS 解析错误 0，脚本非 ASCII 行 0。
- 提取：`python reports/hark19-win-evidence-gate-20261025/extract_counts.py`；对照重跑：`HARK19_PWSH=<pwsh> python reports/hark19-win-evidence-gate-20261025/probe_old_vs_new.py <out.json>`（旧入口需联网取 f6bf47c）。

## 4 偏离 / 待裁
① 新增 verdict EVIDENCE_NOT_DELIVERED=4、RESULT_NOT_RECORDED=5；result.json schema v2（新增 replay/evidence/final/push_command）。
② 回放 FAIL 或 INCOMPLETE 时仍尝试提交证据（留存失败现场），最终 verdict 仍是 1/3；证据核验通过时同样打印 push 行。
③ 证据文件集：回放 PASS 必须 4 个文件齐全（replay-result.json、run-log.txt、hark18-tests.txt、weakening.txt）；非 PASS 时提交实际存在的文件。run-log 是 snapshot 时刻的副本，之后的日志只留在 out\。
④ 测试用 git/python 桩、三个注入点是测试模式专用；git 桩是 POSIX sh 启动器，Windows 下未跑这套测试。
⑤ result.json 写失败时尽力覆写为 RESULT_NOT_RECORDED 最小 JSON；若连这个也写不了，以终端行和 exit 5 为准。

## 5 故障类
- 已覆盖：证据目录准备、复制、建分支、add、commit 失败；commit 返回 0 但未提交；提交身份读取失败；分支被改绑；父不符；多余路径；内容被改；提交后不干净；回放期间 HEAD 被移动或工作树被弄脏；result 写失败；回放 FAIL/INCOMPLETE 与证据失败组合；三者一致；push 门。
- 未覆盖：Windows 实跑、PS5.1 实际行为、真实原件、磁盘满、权限 ACL、杀进程中断（占位为 RUNNING，不会是 PASS）。

## 6 18 旧 probe 说明
18 报告里的 probe_base_vs_candidate 是同类场景构造对照：其 stdout 的 SHA 与 session 都和 node22-run1 原件不同，不能说逐字节相同。probe JSON 里的 exit 是驱动解释器的退出码，不是宿主 CLI 的退出码。旧报告和原始日志保留，未改写。

下一编号 Hark-派单-20。
