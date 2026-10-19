# Hark-派单-04 交付报告：历史一致性限定整改（R1 / R2 / C）

来源：Hark（执行角色）｜Hark-派单-04｜类型：交付报告
结论：**限定整改候选，待独立复核与 Windows 集成。** 不是验收。

## 0. 基线与提交

- 规则依据：b58033caef0a79b478f47a0a76612357d8dd1fd3（AGENTS.md v2 + vN 节，协作规则 v2）。
- 源代码基线：5e12ead5ee3af0c7b7a63c5aa208e8c61025d687；分支 hark-history-consistency-20261019（续接，未从 main/n1 重开）。
- 复核材料：0c2abd802286be5a4d7062bbdbc74f11b4a99249。只取 reports/hark-history-independent-review-20261019.md
  和 probe_stage_shape.py（git show 到 data/hark04-review-input/，sha256 abd1445d46bc8ca12c38025b44f03690c5efeb52ddfe89847f184828cd7b9930）；未合并 n1。
- 提交（按要求分开）：
  - R1 实现与验证：ccd9170c728a2ca0f585db13c7c2328502cc143b
  - C 旧守卫分类（**偏离已验收守卫**）：e5f786e35aff473d2766e6c504ed7bf07292c824 ← 当前候选
  - 本报告与证据：只新增 reports/ 下文件（SHA 见交付消息）
- 5e12ead..e5f786e 只改动 5 个文件：src/run_history_consistency.py（+12）、tests/test_jh020_history_consistency.py（+104）、
  scripts/verify_hark_history_consistency_guards.py（+4）、scripts/verify_jh020_run_history_guards.py（+93/−17）、
  新增 tests/test_jh020_run_history_guard_classification.py（+85）。
  未改适配器恢复/派发、共享字段解析器、app_api、GUI、HTTP 入口、预算、成功门、文件应用、规则、封存夹具、旧业务测试和冻结探针。

## 1. R1：stage 的 JSON 类型边界

- 根因：共享解析器不校验附加字段 stage 的类型；候选在 dispatch_failed 用 (stage, native_state) 查字典，
  在 dispatch_uncertain 直接用 stage 查字典，所以数组或对象会抛出 TypeError。
- 修复：新增 check_stage_type。只在新增的历史层里，查字典之前要求 stage 必须是 str，否则抛 HistoryInconsistent。
  原有的字符串取值表不变，所以非法字符串仍按“不是写入端会写的值”处理。API 仍然只捕获 HistoryInconsistent，
  没有改成捕获全部异常。
- 冻结反例（独立输出目录，未修改反例）：
  - 改前，在 5e12ead 工作树上运行 `--expect candidate`：退出码 0，77 项，28 个 TypeError（与复核一致）。
  - 改后，工作树：`--expect fixed` → 退出码 0，77 项，exceptions 0，all_expected true。
  - 提交后，在全新克隆的 e5f786e 上运行 `--expect fixed`：退出码 0，结果同上（probes/probe-stage-after-postcommit*.txt/json）。
  - 种子探针 probe_history_consistency.py：在全新克隆的 e5f786e 上运行 `--expect fixed`，退出码 0。

### 覆盖表（新增 TestStageJsonType，4 个方法）

| 维度 | 取值 |
|---|---|
| 分支（7） | dispatch_failed：thread/start-refused、thread/start-no-id、model-not-honored、turn/start-refused、turn/start-no-id；dispatch_uncertain：thread/start、turn/start |
| stage（12） | 正确值；null；缺失；""；"other"；true；7；1.5；[]；["thread/start"]；{}；{"value":"turn/start"} |
| 直接校验 | 84 格：7 格 ok，77 格 HistoryInconsistent。异常作为观测值断言，0 个其他异常 |
| API 反控 | 77 格非法值：list 为 corrupt/[]/0，detail 为 corrupt 且 run=None；读证据和查任务关联的 mock 在被调用时会直接抛出，实际 0 次调用；源字节不变 |
| API 正控 | 7 个正确值：list 为 ok/1，detail 返回该 run；源字节不变 |
| 前置 | 84 份输入全部能通过共享 parse_registry_text |

复核矩阵的 11 类值全部包含在内，另外加了“stage 字段缺失”这一类（7 格），所以总数是 84，不是 77。
API 的返回值和抛出的异常都转成一个明确的观测值再断言：如果修复被削弱，结果会是断言失败，不会变成测试错误。

### 新守卫

在 verify_hark_history_consistency_guards.py 中新增一行变异 stage_type_unchecked（把 check_stage_type 改成空操作），
目标是直接校验和 API 反控两个方法。全新克隆上的结果：24 个对照全部通过，**24/24 以断言失败检出**，errors 0，
退出码 0。原有 23 行没有改动，并且全部仍被检出。

## 2. R2：完整套件日志

- **03 时期日志缺失的原因**：仓库 .gitignore 中有 `*.log`，03 的六份 suite-*.log 在 `git add` 时被静默忽略，
  所以没有进入 5e12ead。报告当时写的“已归档”不准确，在此更正。
- **03 原件仍保留在 sandbox 中**，本次以脱敏派生副本补交：hark03-original-logs/*.redacted.txt。
  - 转换方式：只用 sed 把绝对路径替换成 CANDIDATE、BASELINE、POSTCOMMIT，其余字节不变。
  - 原件 sha256、mtime（UTC）、大小见 ORIGINAL-SHA256-AND-MTIME.txt。
  - 这些原件**没有记录命令头和退出码**；从内容看是 `python -m unittest discover -s tests -p "test_*.py"`，结果为 FAILED，
    退出码不补写、不推断。
  - postcommit-noproxy 原件是第二次运行的输出：第一次运行在 120 秒工具超时处被截断，随后重跑并覆盖，mtime 为 14:22:24Z。
  - 这些日志只证明 03 的旧候选，不给新候选背书。
- **本次新取证**：2026-10-19 22:45–22:49 CST（各文件头记录了开始和结束时间；sandbox 的 `date -u` 输出的是 CST 格式，
  文件头已如实标为 CST）。三方并行，每一方依次跑有代理、无代理：
  - 基线 b58033c（worktree）、候选 e5f786e（工作树，干净）、提交后 e5f786e（全新克隆）。
  - 命令：`[env -u HTTP_PROXY -u HTTPS_PROXY -u http_proxy -u https_proxy] python -m unittest discover -s tests -p "test_*.py"`
  - 环境：CPython 3.14.8，Linux aarch64，jsonschema（版本见文件头），脚本 run_suite.sh 一并提交。

| 方 | 提交 | 代理 | Ran | failures | errors | skipped | 退出码 |
|---|---|---|---|---|---|---|---|
| 基线 | b58033c | 有 | 632 | 27 | 78 | 2 | 1 |
| 基线 | b58033c | 无 | 632 | 21 | 63 | 2 | 1 |
| 候选 | e5f786e | 有 | 704 | 27 | 78 | 2 | 1 |
| 候选 | e5f786e | 无 | 704 | 21 | 63 | 2 | 1 |
| 提交后 | e5f786e | 有 | 704 | 27 | 78 | 2 | 1 |
| 提交后 | e5f786e | 无 | 704 | 21 | 63 | 2 | 1 |

- 失败 ID 清单（ids-*.txt）：候选、提交后与基线逐条相同，新基线也与 03 的基线清单相同。新增测试失败数为 0。
- 日志中有一行“Ran 1 test … FAILED (errors=1)”，在基线中同样存在，是某个既有测试捕获并打印的子输出，
  不是额外的一轮运行。
- 日志是路径脱敏的派生副本，原件 sha256 见 suite-logs/ORIGINAL-SHA256.txt。
- AST 独立核对：704 = 632 + 63（test_jh020_history_consistency，原 59 + 新 4）+ 9（新的分类回归）。实际运行数与 AST 一致。
- Linux 下的既有失败（Windows 路径语义、cmd、代理相关 HTTP）在基线中就存在。失败集合相同只说明没有新增，不证明根因，
  也不代替 Windows 验证。

## 3. C：旧守卫按实际组合分类（偏离已验收守卫，单独提交 e5f786e）

- 九个变异、目标样本和生产检查都保留。改动只涉及分类和报告：
  - detected：对照通过；变异下恰好跑 1 个测试，以断言失败告终，无 error，无 skip。
  - redundant：只允许 DECLARED_REDUNDANT 中声明的 registry_field_validation_skipped 一行，并且要同时满足三个条件：
    对照通过；单层变异下干净通过（1 个测试，无失败、错误或跳过）；隔离诊断（同时关闭 check_history）以断言失败告终。
    隔离诊断不计入检出数。
  - 其余情况一律是失败：control_failed、error、skipped、no_tests、survived（未知存活）。
  - 总裁决只有在每一行都是 detected 或 redundant 时才为 pass。
- 正式单层变异中不关闭 check_history，双层关闭只出现在隔离诊断里。
- **Linux 实际结果**（全新克隆 e5f786e）：detected 6，redundant 1，control_failed 2（reparse_check_ignored 和
  evidence_state_ignored 的对照在 Linux 上本身不通过，属于既有平台失败），verdict fail，退出码 1。
  如实报告，**不声称 Windows 上的 8+1**；Windows 上是否为 8 检出 + 1 冗余，由执行AI 在本地复跑确认。
- 分类回归 tests/test_jh020_run_history_guard_classification.py（9 个测试，纯函数，使用构造的结果摘要）：
  - 证明九行和目标测试保留，只有一行被声明为冗余；
  - 对照不通过、error、skip、零测试、未声明行存活、已声明行缺少隔离确认，都不会被记成冗余或通过；
  - 总裁决逻辑同样覆盖。

## 4. 说明收紧

03 报告中“dispatched 追加失败后公共接口不可达”的说法不成立，收紧为：调用方可以从已落盘的 reserved 记录拿到 run_id，
再调用公共 collect，从而写出 reserved → collected。本层把这种历史当作不完整，返回 corrupt；本批不修改写入或恢复机制。

## 5. 未覆盖 / 未扩围

时间戳单调性；快照内容与指纹重算；单条快照内的绑定比较；任意 extra 字段的完整语义；实际恢复和派发；文件系统并发替换。
本批只穷举了分支 stage 的 JSON 类型空间，没有声称穷尽所有输入。没有调用模型，没有发送会话。

## 6. 证据目录 reports/hark-history-consistency-hark04-20261019/

- suite-logs/：六份完整日志（脱敏），ids-*，ORIGINAL-SHA256.txt
- hark03-original-logs/：03 原件的脱敏副本，以及原件摘要
- probes/：stage 探针改前、改后、提交后的输出和 results，冻结输入的 sha256，种子探针输出
- guards/：新守卫（24/24）和旧守卫分类结果（全新克隆）
- run_suite.sh：取证脚本
