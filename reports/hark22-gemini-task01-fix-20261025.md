# Hark-派单-22 交付报告：21 单整改（task01 机械核对器 R1–R4）

来源：Hark B｜Hark-派单-22｜类型：整改交付
分支：hark-b-gemini-task01-20261025（只追加，未合并，未改写历史）
基线：24bd54096145fd1436c20f70f15f2d281b4d20ea
功能提交：3a90ca9b9511ed913ea3488f461608390ccf6162（父 24bd540）
报告提交：本文件所在提交（父 3a90ca9）
状态：候选，待独立复核。无 Windows 格；真实模型/CLI/Node/合成端点调用均为 0。机械通过不等于语义验收，不等于任务成功。不得据此安排真实调用。

## 0 范围理解
只关闭 21 号机械核对的四类已确认反例（R1 固定任务与全量源码缺独立锚；R2 原生事实与登记/产物/会话记录不一致仍通过；R3 请求与返回片段未绑定；R4 路径/文件类型/字段形状未在触碰前限定），并按条件改写审阅基准两处（B2 空/短登记、B7 dispatched 写失败）。
不新增自动语义判断，semantic_review=required、task_outcome="not decided by this tool" 不变；不批准 auto；LF 原字节与未知格式非通过的边界保留；不改 .gitattributes、A 文件、src/、verify、scripts/win/、JH018、规则或旧报告日志；不扩平台。开工核对：HEAD=24bd540=远端同名分支，工作区干净，AGENTS SHA-256 = 502aa829…6cf。

## 1 分级结论
- 实现：完成（候选）。
- 离线：构造输入 86 项测试连续 3 次 OK；37 个反例/削弱用例全部满足“对照非 all_pass 且退出码非 0 → 仅削弱对应门后 all_pass 且退出码 0”；I/O 门 5 例在对照中对哨兵的访问次数均为 0（削弱后 1–30 次，证明观察器能看到越界）。提交后在干净 worktree 复跑 86 项 OK，反例重放结果与提交前逐行一致。
- 真实路径：未做。独立验收：未验收。
- 三个语义控制（正确、引用正确但解释相反、漏 B7 含义）仍为机械 all_pass、semantic_review=required。

## 2 改动（仅 B 允许路径；sha256）
- scripts/gemini_task01/pins.py（新）bb774033cf248c163eb5d4da72b1a5fc20379c51f4a962581f867693acae0dbd：受信锚。包字节、task/baseline 摘要、task_id、f6bf47c 下 src/ 全部 26 个文件的 (blob, size, sha256)，由 git ls-tree/cat-file 一次算出；不从 package/manifest 读取。
- scripts/gemini_task01/check_run.py 7deab1b46262aea6eea83f338d44d5f584f65a6f4720be7910484594112621c5（schema v2）
- scripts/gemini_task01/export_source.py b76c6f027f2415ac777c6121ceca2c2b547734baf6cf2c6e51542a73fcfcb248
- scripts/gemini_task01/review-baseline.md f5ddcbad182fa91ebd48625d59743c6c07c396db47ba828b0fdb512abd58b8c7（只改 B2/B7 两处文字）
- scripts/gemini_task01/package.json 92346a5ffd6f06e07939c269b899f57eed242ddfdfe6dbe850c6abdf3abe746c（只改 review_baseline_sha256 一个值）
- scripts/gemini_task01/task.txt 5ed4f438c57918d7924ea00bf02134c529b20bf9556e6b01c6dffa7830e80518（未改）
- tests/test_hark21_gemini_task01.py 94c7dd11ad9d5242e4e61b5df6a5f513b2d76328c2fff6c786fdc1fa7e99b441
- tests/fixtures/hark21/build.py 2aef0f2e181941d378e6d1d1fa62ffd6041c54ff693ca9c491ca86e6c4204686
- tests/fixtures/hark21/fake_cli_t01.py a6448c370e50fe72902ce8c95ad9250a1a560cabe9c0566f694737b9c2e48861
src/ 与 59b221ce 相同；A 自有路径、AGENTS、docs、21 报告与日志与 24bd540 相同。

### R1 受信锚
- package.json 字节必须等于 pins.PACKAGE_SHA256，否则立即 fail 并停止（不信其自述摘要，不读其中引用的文件）；task_id / task_sha256 / review_baseline_sha256 另与 pins 比对。核心点、焦点表、函数范围、stdin 前缀、-p 文本都随包字节被锚定；函数范围另对源码 AST、焦点另对源码行摘要复核。
- 清单的 files 必须逐项等于 pins.SOURCE_FILES（路径集合与每个文件的 blob/size/sha256），再与导出目录逐字节比对。改非目标文件、增文件、删文件并同步清单和运行前后观测，均在 export.src_anchor fail。
- 导出器先校验包字节和列出的树等于 pins，不符不写任何文件。

### R2 原生/记录一致性（自己的跨证据核对，落实 A 既有门）
从已核摘要的 stdout 重新解析（只读复用 A 的解析器），不信 collected 摘要：
- native.status：result.status 必须为 success；state=declared_complete 只能对应干净运行；declared_status 必须可由原生结果推出（A 的写法：干净为 success，否则为 host 状态或 unattributed）；native_state 必须等于 exit:<退出码>;result:<状态>。
- native.model：init.model = 请求模型 = artifact.host_reported_model；backend_models = result.stats.models。
- native.usage：collected.usage 必须等于 usage_from_result 重算值；字段缺失为 unknown（用量未知不能机械全过）。
- native.host_preamble：已知前导存在时 artifact.host_diagnostics 必须与 stdout 前导一致；stderr 未核实为 unknown；stderr 含最终保存失败为 fail。
- native.errors：host_errors / denied / unconfirmed 三个列表等于原生流重算值。
- binding.model：model_id 为空或以 auto 开头为 fail；dispatched 的 argv_tail 必须带本运行的 --session-id、-p 文本、-m 模型。
- chat.record：会话记录按受支持形状解析（第 1 行会话头；之后为 $set 更新或 user/gemini 消息；同 id 重写取最后一次，但须保留类型、模型和先前的工具调用且不改其 name/args）；头部 sessionId 须等于本运行；gemini 记录模型须等于请求模型；每个 toolCall 形状合法，functionResponse 的 id/name/response 与调用一致，后随 user 记录中的 functionResponse 与之相同；同一 tool id 不得出现在两条消息下；记录中出现流里没有的工具调用为 fail；非对象/非列表等错误类型稳定给出 fail。未观测到的类型或形状为 unknown。

### R3 请求与返回绑定
- 流参数与会话记录参数逐键比较：值不同 fail；会话记录多出的键只有在“已知默认”时接受，目前唯一观测到的默认是 grep_search 未带 context 时宿主记为 context 50（host-probe 实样）；其他多出键 unknown。
- read_file：响应显示的行范围必须等于请求的 start_line/end_line；无显式范围、end 超过文件长度（截断/夹取行为未观测）为 unknown；未支持参数 unknown；流摘要须与内容一致。
- grep_search：输出头的 pattern 必须等于请求；头部路径须为 "."，不得出现请求没有的 filter；context 为 0 时不得有上下文行；context>0 时每个文件只接受单一匹配，打印行须恰为该匹配前后 context 行的窗口；多匹配合并格式未观测，记 unknown；匹配行另用 Python re（忽略大小写）做确认，确认不了记 unknown（不复制宿主正则引擎，不声称证明搜索完整性）。
- 用真实 host-probe 样本回测：grep（默认 context 50）与 read 都能按上述规则绑定通过。

### R4 触碰前的边界
- 所有读取都从根逐级 lstat：根、中间目录或目标为符号链接/junction/重解析点、非普通文件、名字含分隔符、盘符、冒号、NUL、'..' 或以点/空格结尾，均在打开前拒绝；打开后再用 fstat 的 dev/ino 与 lstat 比对。
- package.task_file / review_baseline_file 必须是包目录里的普通文件名；run_id 必须匹配 gemini-run-<12位十六进制> 才会拼进 runs/；证据根、runs/<id>、各证据文件不得为链接；导出根不得为链接（扫描时内部链接只记录不跟随、不读取）。
- 导出器：--out 或其父目录为链接/junction 即拒绝，不写任何文件；之后每级目录新建并 lstat 复核，文件以 O_CREAT|O_EXCL（及 O_NOFOLLOW）创建。
- Windows 风格工作区的路径分类不依赖 Python 版本：只接受工作区下的盘符绝对路径（前缀不区分大小写）和普通相对路径；根相对（\src、/src）、盘符相对（C:src）、UNC、设备路径一律拒绝。引用正则也把这些前缀纳入，使其被判定为不存在的引用，而不是被静默跳过。
- 包、清单、登记、聊天记录、工具字段的非对象/错类型给出稳定的 fail 诊断；另有兜底：任何残余的类型异常记为 checker.input_shape=fail，不抛出。

### 审阅基准两处条件化
- B2：区分三种情形——新存储（零字节或不存在且 runs/ 无痕迹）合法可派发；只含合法记录的有效前缀合法，重开后未收集运行恢复为 unknown、不重发；前缀被截改、文本不合法、非法历史或有未登记运行痕迹才粘滞拒绝。不再把空/短登记一律写成拒绝。
- B7 第 3 层：OSError 不保证磁盘只剩 reserved。按 _persist_locked（1161–1178，先 write 再 flush/fsync）分三种实际情形：未写入→读者 B3 unknown；完整 dispatched 已写入但 flush/fsync 报错→合法 reserved+dispatched，读者同样 B3 unknown；半行或非法历史→读者按 B2 粘滞拒绝。共同点只是不重发，不能声称所有读者只见 reserved 或总得同一种结果。
源代码未改，未新增语义打分。

## 3 计数与证据（Linux aarch64，CPython 3.12.15，git 2.47.3）
- logs/hark22-tests-run1..3.txt：每次 Ran 86，OK；WEAKENING 行 37 例。
- 提取：python reports/hark22-gemini-task01-fix-20261025/extract_counts.py → logs/extract-output.txt。三次均为：37 例对照非 all_pass 且退出码非 0（其中 R2.usage_missing / usage_field_missing 对照为 undetermined/3，其余为 fail/1）；37 例削弱后 all_pass 且退出码 0；I/O 哨兵访问（对照, 削弱）：task_outside (0,1)、run_dir_link (0,5)、evidence_root_link (0,6)、evidence_file_link (0,1)、export_root_link (0,30)；AST 独立计 test_ 方法 86 个。
- 守卫名 25 个：原 11 个 + package_anchor、src_anchor、native_status、native_model、usage、host_preamble、host_errors、chat_record、explicit_model、read_binding、grep_binding、input_paths、evidence_paths、export_root。ws_path_kinds 仅单元层削弱（见偏离③）。
- logs/replay-cases.txt（python reports/hark22-gemini-task01-fix-20261025/replay_cases.py）：3 个语义控制 + 26 个整改反例，仅对照、逐例列非通过项。
- logs/related-regression.txt：tests.test_jh018_agent_session_contract、hark11/12/13/18/19 共 152 项 OK（skipped 8：Windows 原件与 HARK19_PWSH 未设置，均为既有跳过）。
- logs/post-commit-identity.txt、post-commit-tests.txt、post-commit-replay.txt：干净 detached worktree（HEAD 3a90ca9，父 24bd540，0 个脏条目）复核允许差异、src/ 与 59b221ce 相同、A 路径未变、AGENTS 摘要、9 个文件摘要、pins 自校验；86 项 OK；反例重放与提交前逐行一致。
- 未跑完整仓库套件。

## 4 偏离（请裁决）
① 测试专用削弱钩子扩为 25 个名字；main() 在钩子非空且未设 HARK21_TEST_WEAKEN=1 时拒绝运行（退出码 2），生产命令不会带着削弱运行。
② 共享 A 组件（明确披露的只读依赖）：validate_registry_records、parse_stream_with_host_preamble、associate、usage_from_result、HOST_SAVE_FINAL_FAILURE（后两者本批新增）。跨证据比对为本文件自写，但不是完全独立实现。
③ Windows 路径类型只在单元层验证（14 个路径表＋削弱后重现被报告的误映射）。端到端无法在 Linux 上做：A 的 validate_registry_records 按宿主风格校验 workspace，Windows 路径的登记在 Linux 上本就非法，要做需改 A，未做。junction/重解析点检测代码（st_file_attributes 的 0x400 位）在 Linux 上未执行，测试用符号链接替代。
④ 保守格式假设：grep 默认 context 50 只来自 1 个实样；context>0 的多匹配、无显式范围的 read、end 超出文件、user/gemini 以外的记录类型（如 info/error）、无 user functionResponse 的调用，都记 unknown。真实运行若出现这些形态，会停在 undetermined，需要新的观测样本才能放行。
⑤ auto 判定只拒绝以 auto 开头的名字；0.65.0 是否有 pro/flash 等别名未核实，未判定。
⑥ 证据记录中的 path 字段、run_dir 不与证据根当前位置比对（证据根可能被整体拷贝），与 21 相同。
⑦ 证据根本身是链接也拒绝（比要求更严）；根以上的祖先目录不检查。
⑧ 旧测试调整：good_tools 增加 grep_search__2（默认 context 窗口）并放在末尾以保持原索引；test_unknown_grep_format 同时改两个 grep；无 grep 用例按名字过滤；旧削弱断言收紧为 all_pass 且退出码 0。未删除任何有效反例。

## 5 故障类
已覆盖（构造输入）：
- R1：改非目标源码、增 src 文件、删 src 文件（均同步清单与运行观测）；换任务正文与 task_id 并同步包摘要/请求；焦点表改到其他行且摘要自洽；导出器拒绝未锚定的包。
- R2：stdout error 而 collected 写 success；init.model 与记录不一致；usage/stats 缺失、单字段缺失（unknown）；usage 与 stats 不符；已知前导＋stderr 最终保存失败；仅已知前导（正控通过）；原生 error 列表与产物不一致；会话头 sessionId 不同；会话记录模型不同；functionResponse.name 不同；非法附加 toolCall；toolCalls 为整数；记录中有流里没有的工具调用；重写丢失工具调用；model_id=auto；真实会话记录样本按受支持形状解析无 fail/unknown。
- R3：read 请求 1–5 而返回 1060–1145；grep 头部换成别的 pattern；默认 context 窗口不符；未解释的 grep 参数（unknown）；未解释的默认值（unknown）；无显式范围的 read（unknown）；end 超出文件（unknown）；真实样本绑定通过。
- R4：task_file='../outside-task.txt'；runs/<id> 为链接；证据根为链接；证据文件为链接；导出根为链接（核对器）；导出器 --out 为链接、父目录为链接（不写入、链接目标保持为空）；导出目录内文件为链接（只记录不读取）；run_id 路径穿越；Windows 路径 14 类；根相对引用被判定而不跳过；包/清单非对象、清单条目逃逸、artifact 非对象、证据条目非对象、登记行非对象均给稳定 fail。上述 I/O 用例都由审计钩子观察器证明拒绝前无越界访问（open/scandir/listdir/mkdir 等事件）。
- 原有 40 项 21 测试全部保留并通过。
未覆盖：真实模型输出的措辞与引用变体；真实 Windows（junction、盘符大小写、CRLF 源码、长路径）；glob/list_directory 内容核对（只记录）；grep 多匹配合并格式与正则完整语义；超大输出；lstat 逐级检查与打开之间的 TOCTOU（只做打开后 dev/ino 复核）；观察器不覆盖 stat/lstat（Python 无对应审计事件）；无前导时 stderr 出现最终保存失败（A 未承诺此门，未新增）。

## 6 未做与阻塞
未做：真实调用、Windows 格、认证/代理/模型选择、19 号相关事项。阻塞：无。
真实调用前仍需：架构师对本批的独立复核；19 号完整 Windows 结果的独立验收；用户确认认证路线、显式模型、代理变量名、派发次数和费用条件；结果未知时冻结、不重发。

请求裁决：偏离①–⑧；本批是否关闭 21 的 R1–R4。
建议重点复核：check_run.py 的 parse_chat、args_relation/check_grep/check_read、safe_path/safe_read、map_to_export，以及 pins.py 是否等于 f6bf47c 的 Git 对象（test_pins_are_the_git_objects_of_the_fixed_commit 可复现）。
