# Hark-派单-03 独立复核：暂不批准 Windows 集成

日期：2026-10-19。基线 b58033caef0a79b478f47a0a76612357d8dd1fd3。
功能候选 253d9d9087ca192d10a76bcb90fb40dda82082a5；
报告/证据提交 5e12ead5ee3af0c7b7a63c5aa208e8c61025d687。
本地生产工作树未移植候选，仍是 b58033c；本轮不跑完整套件、不调用模型。

## 裁决

暂不验收、不直接集成。R1 是已复现的输入边界缺陷，须限定整改；
R2 是完整验证日志缺失，须补齐可读取证据。
旧变异遮蔽采用 C：按实际组合记 8 检出 + 1 冗余，不能恢复 9/9。
Hark-派单-03 已交付；后续限定整改用 Hark-派单-04，其他能力不扩围。

## 已核实并承接

- compare 的父提交和范围符合派单：四个实现/验证文件；后一提交仅 reports。
  GUI、共享解析器、适配器恢复/派发、既有测试/守卫、规则和冻结探针无改动。
- 四文件从固定候选下载到本轮隔离快照，并按 Git blob 对照 compare 验证身份。
- 在 Windows / Python 3.14.7 对隔离候选定向运行新增测试文件：59 项通过；
  新变异器 23 对照和 23 变异通过，未把运行错误算作检出。
- 独立 AST：691 = 632 + 59。这里只核对计数并跑定向测试，不声称亲跑 691 项全套。
- 候选 AppInterface 只读核对既有真实历史，仍 ok/verified，源字节不变；
  来源是既有 5b1dc26，只证明此次查询兼容，不是新模型运行或 Windows 集成结项。
- 因 Windows Python 临时目录 ACL 差异，定向探针只在自身进程把 mkdtemp
  换成项目证据目录内继承 ACL 的普通 mkdir；未改测试、系统 ACL 或安全策略。
- 核对 _persist 的实际调用分支；正常未完成前缀、失败和不确定分支被保留。

## R1：stage 的数组/对象使查询抛 TypeError

位置：候选 src/run_history_consistency.py 的第 130、141 行。
dispatch_failed 用 (stage, native_state) 查询字典；
dispatch_uncertain 直接用 stage 查询字典。

现行 parse_registry_text 不校验 stage 这个附加字段的类型。
因此 stage=[]、["thread/start"]、{}、{"value":"turn/start"}
均能通过字段解析，随后触发 unhashable type；
AppInterface 只捕获 HistoryInconsistent，无法按契约返回 corrupt。

独立矩阵：7 个失败/不确定分支 × 11 类值 = 77 项。
7 个正确 stage 正控为 ok；42 个非法标量反控为 corrupt；
28 个容器反控全部抛 TypeError。所有源文件字节均未变。
这不是模型、Windows 路径或原有字段校验失败。

要求：在新增历史校验层处理该输入类，使非法 stage 转为 HistoryInconsistent。
不要通过 GUI 隐藏、在 API 中吞掉全部 Exception，或修改共享单条解析器绕过。
补数组/对象的空与非空、标量及正确 stage 的测试，覆盖两类事件和七个分支；
列表/详情均须稳定 corrupt，零证据/关联读取，源字节不变。
API 抛出与正常返回应成为明确断言的观测结果；
测试加载/框架错误仍不能拿来凑变异检出。

云端可复制的独立纯合成反例：
[probe_stage_shape.py](hark-history-review-20261019/probe_stage_shape.py)。
它不需要本地真实登记；--out 必须全新，不能改冻结探针凑通过。
candidate 模式退出 0 只代表复现这 28 个错误，不能称修复通过。

候选基线：
python reports/hark-history-review-20261019/probe_stage_shape.py --repo CANDIDATE --out NEW_BEFORE --expect candidate

修复后：
python reports/hark-history-review-20261019/probe_stage_shape.py --repo FIXED --out NEW_AFTER --expect fixed

修复后的 77 项应为 7 个 ok、70 个 corrupt、0 个异常；
损坏列表无正常摘要，详情 run=None，源文件不变。

## R2：声称的六份完整套件日志未出现在固定提交

已独立读取固定 5e12ead5 的 GitHub logs 目录：
有六份 ids-* 清单、新守卫 JSON、旧守卫逐行记录、探针输出等，
没有报告称已归档的六份完整套件日志。
compare 的全部文件列表同样没有这些原始运行日志。

因此完整 Linux 套件的 Ran/FAILED/skipped 读数目前仍属执行方自报，
不能只凭失败 ID 清单独立确认完整运行数、失败数、错误数和跳过数。
本轮已独立验证 AST 691 和新增 59 项，不把它们代替全套运行证据。

要求：补齐可在固定提交读取的基线/候选/提交后 × 有/无代理六份完整日志，
并给出命令、环境和实际退出码。
若提供路径脱敏副本，明确其派生性质、原件摘要及转换方式；
不能把失败清单当完整日志，不能倒填历史输出。
旧日志若已丢失如实说明，按需要在固定版本重跑，记录新的取证时间。
R1 修复后的验证必须绑定新候选，不沿用旧候选的通过标签。

## 旧守卫裁决：C，正式分类为冗余

独立四格复核 registry_field_validation_skipped 对应的旧测试：

| 字段解析 | 新一致性层 | 断言结果 |
|---|---|---|
| 正常 | 正常 | 通过 |
| 宽松变异 | 正常 | 通过：新层遮蔽 |
| 正常 | 隔离关闭 | 通过：隔离正控 |
| 宽松变异 | 隔离关闭 | 断言失败，0 errors |

这证明旧字段校验仍可在隔离情况下检出该样本，
但生产组合中的这一单层变异当前是冗余。
另在包含 schemas 的完整隔离快照中逐行复跑原九行，9 个控制全绿、
8 行断言检出、1 行被遮蔽、0 errors。首次隔离漏拷 docs/schemas 导致
最后一行控制无法加载；该探针装配问题已补齐重核，未改候选代码或测试。
A 可以作为隔离诊断，不在正式成绩中把双层关闭计成原单层检出；
本批正式口径采用 C，不改既有业务测试或目标样本。

授权仅调整旧 scripts/verify_jh020_run_history_guards.py 的分类与报告：
保留原九个变异、目标样本和生产检查；只有已证明的这一行可列冗余。
要求控制先绿；异常、skip、零测试或其他未知存活均不能归冗余或通过。
报告实际检出、冗余和总行数，不能删这一行，也不能宣称九行全部检出。
此已验收守卫的修改作为明确偏离单独提交，和 R1 产品修复分开。
新增分类回归要证明其他未知存活/异常不会被计成冗余。

## 其余说明与边界

报告所述“dispatched 追加失败后再 collect”缺口继续按历史不完整处理；
不授权重写适配器或放宽事件顺序。它的“公共接口不可达”措辞不能当严格证明：
已登记的 run_id 可被调用方读到，异常后也不能假定无人再调用 collect。
独立 Fake 传输探针已注入 dispatched 追加失败：submit 抛 OSError 后，
从 reserved 文件读取 run_id，再调用公共 collect，确实写出 reserved → collected；
候选查询返回 corrupt，符合日志不完整的处理，未要求扩大本批修复范围。
不要求为了该措辞另建恢复平台，最终报告收紧为适用条件说明即可。

未覆盖/未扩围：时间戳单调性、快照内容与指纹重算、
单条快照内绑定比较、任意 extra 的完整语义、实际恢复/派发及文件系统并发替换。
本轮故障类重点为分支 stage 的 JSON 类型空间，未声称穷尽所有输入。

完整独立本地证据位于忽略目录 data/hark-history-review-20261019/：
review_probe.py、results.json、targeted-tests.txt、new-guards.json、隔离快照与 77 个输入。
上述私有历史只读兼容证据不上传；此报告和云端反例不包含私有会话字节。
