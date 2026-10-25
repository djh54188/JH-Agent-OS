# Hark-派单-23 交付报告（Hark B）——22 单剩余反例收口

来源：Hark B｜Hark-派单-23｜类型：整改交付。状态：候选，待独立复核。无 Windows 格；真实模型/CLI/Node/合成端点调用 0。

- 分支 hark-b-gemini-task01-20261025；基线 1f14c1d2edeb0316d992c12b3e48dfdd2deead6c（开工时 = 远端，工作区干净，AGENTS SHA-256 502aa829…6cf）。
- 功能提交 7ed47c5f085d725ab66f59af3072f076b8588aa0（父 1f14c1d）；本报告为其后的报告提交。
- 允许差异：check_run.py、export_source.py（仅文档字符串，AST 去 docstring 后与 1f14c1d 相同）、tests/test_hark21_gemini_task01.py、tests/fixtures/hark21/build.py，以及本报告与证据目录。task.txt / package.json / review-baseline.md / pins.py / fake_cli_t01.py 未变；src/ 相对 59b221ce 未变；A 路径、AGENTS、docs 未变；旧报告日志未覆盖。

## 改动
R2：新门 native.identity（artifact native_session_id、requested_model 与 reserved 相同；raw_refs[0] = 本运行会话）。parse_chat：同一 toolCalls 数组内同 id 重复 → fail（与不同记录间已观测重写区分，后者保留）；每个调用 result 恰一份 functionResponse，相同副本 unknown、不同副本 fail，不选第一份/最后一份；user 记录副本同理并须等于调用那份；会话头字段非字符串、$set 值非字符串、timestamp 非字符串、user content 非列表、gemini content 非字符串、内容/结果部件非对象或 functionResponse 字段错类型 → fail；其他未观测形状 → unknown。未给共享 A 代码加规则。
R3：grep 的参数边界（pattern 字符串、context 非负整数、未知参数 unknown）在所有输出分支之前核对；“No matches found…”无匹配输出因 0.65.0 格式未观测一律 unknown（不再 startswith 直通），不证明无匹配、不复制 grep 引擎。read 的 start/end 类型与未知参数也移到格式判定前。
R4：新增 extract_refs/REF_TOKEN：凡后接 :行号[-行号] 且含 . / \ 的非空白记号都作为引用交给 map_to_export，设备前缀（\\?\、\\.\）、UNC、盘符相对、根相对、含冒号（ADS）记号不再在提取层丢失。refs.exist 增加 bad_refs 全量列表。只做字符串分析。
夹具：GOOD_DELIVERY 更名为 ALIGNED_DELIVERY（构造正控，非语义验收），B7 按新版基准写磁盘三分支；解释相反、漏 B7 控制保留，三者仍为机械 all_pass + semantic_review=required。
措辞纠正：export_source.py 实际只查 --out 与直接父目录，更高祖先不查（22 文档字符串“任意组件”过度）；check_run 文档注明根以上不检查、不构成沙箱。导出运行行为未改。新增测试按当前接受范围记录祖父链接下仍可导出 26 文件。

## 计数（Linux aarch64，CPython 3.12.15；日志运行于提交前工作树，提交后在干净 worktree 复跑）
- logs/hark23-tests-run1..3.txt：每次 Ran 113，OK（AST 计 113 个 test_ 方法）。
- logs/extract-output.txt（extract_counts.py 从原始日志独立提取）：三次均 57 个削弱用例，57/57 对照非 all_pass 且退出码非 0，57/57 仅削弱对应门后 all_pass/0。R23 新增 20 例：15 例对照 fail/1，5 例对照 undetermined/3（fr_duplicate、grep_nomatch_plain、grep_nomatch_unknown_format、grep_unobserved_option、user_fr_missing）。I/O 哨兵 5 例对照 0 次、削弱 1–30 次（不变）。
- logs/related-regression.txt：JH018、hark11/12/13/18/19 发现 152、通过 144、跳过 8（既有 Windows 原件/HARK19_PWSH），unittest 结论 OK。
- logs/post-commit/tests.txt：detached worktree @7ed47c5，工作区 0 改动，src 相对 59b221ce 0 差异，A 路径/AGENTS/docs 相对 1f14c1d 0 差异，AGENTS SHA-256 502aa829…6cf，113 项 OK，WEAKENING 57 例与提交前逐项一致。

## 偏离/披露
1 无匹配 grep 输出一律 unknown：合法无匹配在拿到 0.65.0 实样前会停在 undetermined（保守，非禁止）。
2 引用提取更宽：含 . / \ 且接 :数字 的记号（如带端口的 URL、"a/b:3"）会被当作引用并判不存在 → fail；纯 "exit:0"、"10:30" 不计。
3 同一消息内重复调用无论副本相同与否均 fail（判为非已观测重写）；functionResponse 相同副本为 unknown。
4 artifact 身份只核 native_session_id、requested_model、raw_refs[0]；source_refs 等其他字段未扩展。
5 grep/read status=error 的调用不做参数边界核对（与 22 相同）。
6 共享 A 只读组件同 22；Windows 路径表仍只单元级 + posix 工作区端到端，junction 代码未在 Linux 执行。
7 日志文件头记录的 HEAD 为 1f14c1d（提交前工作树），提交后复跑见 post-commit。

## 覆盖/未覆盖
覆盖：上述 R2 缺失/重复/相同与不同副本/改写/错类型；R3 pattern 整数、context=-1、未知参数、未知无匹配格式、普通无匹配、read 参数类型（已知与未知格式）；R4 三种派单反例及 12 项已声明错误路径同时追加时 bad_refs 全量、15 项 Windows 表提取完整；已观测重写、真实会话样本、请求绑定与三种语义控制保持。
未覆盖：真实 0.65.0 无匹配输出；真实 Windows（junction、CRLF、长路径）；grep 多匹配完整语义；超大输出；TOCTOU；导出器祖先治理（按范围接受）。
