# 执行AI-派单-09 独立复核

日期：2026-10-23，Asia/Shanghai。角色：架构师 / 独立审查者。

## 裁决

1. **接受限定的Node22兼容结论**：本机Windows、官方CLI0.65.0、隔离Node22.23.4、headless及合成端点条件下，已查运行避开Node24.19.0的退出断言。不是修复Node24本身、定位根因或承诺Node22全部可靠。允许后续继续使用这一固定Node22环境。
2. **接受四条失败/错误记录暂存债务，不在本批整改**；不称它们全部是环境问题，不称Windows完整回归通过。它们涉及三个测试方法，其中一个有两个失败子测试。
3. **批准下一批窄范围兼容已定位的宿主前导重试提示**，保留原字节与诊断；未知非JSON、损坏事件及其它失败门不放宽。执行AI10单独实施，架构师不改代码。
4. **真实账号/模型路径仍须验证**，才能批准“第一个有价值的真实Gemini Agent任务”完成。OAuth不是唯一可选认证；认证方式、模型/额度及真实调用授权须后续单独确定。本轮仍不登录、索取凭据或调用真实模型。

09按固定基线、退出兼容及已查局部边界接受；stdout前导兼容、完整Windows测试、提交后原审计入口及真实任务仍未关闭。没有产品合入或发布授权。

## 基线、来源与规则字节

实际本地HEAD：`750b81673ee5b3d68bcab6c2ac85d87a32b94d72`，父为 `29e00201f85063f60cc23213262fc82cf430d7ee`，父的树 `df45ad170b378366641ad6e62ad6c631846fc220`。相对父没有src/tests/docs/AGENTS增量，只有验证脚本与报告证据。08的错误基线不再沿用，070004f的实际继承检查已在这一原始基线中保留。

报告AGENTS哈希578a977f…与约定edc9846b…之差仅为checkout的57处CRLF：固定29的Git blob仍为完整约定SHA，文本逐字节统一换行后相等；协作规则同理。本轮没有规则内容改变，不把换行差异当新规则版本。

取证目录：

- V：`D:\JH-Agent OS\data\worktrees\gemini-node22-probe-20261023`。
- E：`D:\JH-Agent OS\data\gemini-node22-probe-20261023`。
- 根审查原件：真正产品树的 `data/execution09-independent-review-20261023/`。
- 可审阅摘要：`reports/gemini-node22-review-20261023/`。

另有两路只读交叉核查支持审查；最终裁决由当前架构师作出。未替换指定架构角色，未让执行方自行验收。

## 运行证据与计数

根审查从10套registry/stdout/stderr/合成请求重新提取，并逐一校验30个登记引用的原生文件大小与SHA-256；均相符。每个run为reserved→dispatched→collected各一次。相应workspace-before/after原始字节相等。

| 运行类别 | 次数 | CLI退出 / 适配器状态 | 合成请求 / tokens |
|---|---:|---|---:|
| Node22正常干净任务run2/run3/pair22 | 3 | 全部0 / declared_complete | 15 / 270 |
| Node22首跑run1 | 1 | 0 / failed，首行非JSON | 5 / 90 |
| Node22边界boundary1/boundary2 | 2 | 全部0 / declared_complete；boundary1驱动检查不完整 | 12 / 216 |
| Node24对照含pair24 | 4 | 全部3221226505 / failed，均UV_HANDLE_CLOSING断言 | 20 / 360 |
| 合计 | 10 | Node22进程6/6退出0，但仅5个run declared_complete | 52 / 936 |

token来自合成result.stats，不是真实账单。未把硬编码real_model_calls=0本身当独立证据；本次核对的是合成协议与请求记录，仍无真实模型能力证明。

run1不能从分母消失：`E/evidence/node22-run1/bridge/runs/gemini-run-92dc87c4d59a/stdout.jsonl:1`为已知提示，最后有宿主success、进程exit0，但登记state=failed，诊断line1:not JSON。保留原件后另作明确实验可接受；“丢弃”只表示不用它证明干净交付，不能抹掉兼容性缺口。

boundary1的CLI及适配器并未失败：原生三次read_file都是invalid_tool_params，write/Shell都是tool_not_registered。失败的是驱动只匹配到parent_relative、漏找另外两个工具ID；boundary2修正检查后补证合理，两次成本均已计入52。两个原始结果都保留。

pair是**相同初始home快照、不同隔离路径**，不是两进程共用同一home。七文件初始元数据、脚本字节、请求快照和工作区清单一致；仅归一隔离目录标记与动态工具ID后的模型请求相同。它支持限定的运行时差异结论，不支持冷启动/并发/长跑保证。Node安装zip/exe及四个CLI文件摘要由只读交叉核查重算相符；本地签名日志的Good signature同时标注信任unknown，不能把它夸成完整密钥来源信任链。

## 已定位的stdout兼容缺口

本例精确行：`Rename failed with EPERM, retrying in 50ms (attempt 1/5)...`。

固定安装的 `E` 前一批共享CLI包（`data/gemini-windows-probe-20261023/cli/prefix/node_modules/@google/gemini-cli/bundle/`）源码：

- `chunk-DKZARV6U.js:252896` 的ProjectRegistry.save保存宿主projects.json；不是JH派发登记。
- `:252914`仅EPERM/EBUSY且尚未最终耗尽时可重试；`:252919`计算退避，`:252920`输出该debug提示。
- `:252925`最终保存失败记录error并抛出，不属于可降级的成功重试提示。
- `:247504`的debug最终调用console.debug，故输出到stdout。
- 初始化等待该项目登记过程完成后才进入headless原生init；本例后续完整事件/会话关联和exit0支持重试后继续成功的解释。

当前适配器parse_stream在`V/src/agent_session_gemini.py:388`将该行记not JSON；_finish在`:1381`因problems非空判failed，仍保留宿主success。这符合当前严格解析规则；下一批是在明确授权下增加已知宿主前导日志兼容，不把当前安全拒绝说成应该被普遍放宽。

根审查另冻结8类解析输入：干净流、已知前导提示、未知前导、同文插在流中、同文在result后、坏JSON、坏UTF8、JSON字符串而非事件。当前除干净流外均有解析问题，作为下一批原始边界参考。又用公开submit对六类Windows相对输入及缺失中文绝对路径检查拒绝，launcher均0；存在中文目录只查语法/预检接受，不声称已用真实CLI读取中文目录。没有启动CLI或模型。

## 四条Windows回归记录

AST独立计数H11=32、H12=33、H13=18、JH018=42，总计125。原日志为3 failures、1 error、5 skipped。74子集只含H11+JH018，因此与H12/H13的失败没有矛盾，不是同一批测试换runner后冒充全绿。

| 条目 | 精确归类 | 裁决边界 |
|---|---|---|
| H12读取两份H11历史登记（同一方法两个子测试） | 当前Windows验证器拒绝历史POSIX工作区；跨平台历史读取限制与测试适配债 | 不称随机环境故障，不为了绿灯放开Windows根相对路径；本批未承诺历史跨平台迁移 |
| H12捕获字节回放 | Git checkout将固定捕获stdout的12处LF改成CRLF | 保留字节断言；后续修fixture/证据保真策略，不能规范化原始证据后假称字节一致 |
| H13 symlink条目 | 创建夹具阶段WinError1314，尚未执行产品拒绝判据 | 权限/场景可用性限制；不记该Windows反例通过，不要求提权或顺带改系统设置 |

捕获stdout的Git blob为3056字节，SHA-256 `be3ed400936c425654f1407371e60447c624d937967716e8f337f210fd899b97`；checkout为3068字节，SHA-256 `652cd128539dd0b05eca2a2afef1f4b1c655be27a76c056ea4586c5cd6e5b119`，差异仅换行。这是原始夹具保真问题，不能归到Node22退出结论里，也不能悄悄放宽验收。

## 提交后审计缺口

提供的audit-result.json摘要确为 `836fdd7de8dbe64f8401ca6d731ebe566cac1b74ac185e52cbcbc04c299cb761`；其source记录HEAD=29、父a086195、树df45ad17，属于提交前取证。`V/scripts/audit_gemini_node22_probe.py:331`硬比较当前HEAD/父/tree；提交750后原命令会因source identity不成立而失败。没有把现有ok=true视为750提交后原审计通过。

本轮已独立用Git核对750父为29，受保护产品/测试/规则无增量，并绑定实际文件与运行证据，故不撤回相同源码下的运行时结论。旧审计入口的提交后适用性仍是验证工具缺口，不篡改旧JSON修正它。下一批必须用适用的来源检查绑定实际提交/源码差异和提交后复验，不能把HEAD检查改成恒真。

## 本轮关闭、下一批与现场

关闭09基线偏离及JH018继承缺失的续接问题、所验Node22退出兼容，以及相应代表性只读边界/工作区输入限定证据。保留所有未覆盖项，不扩大为完整Windows或真实Gemini闭环。

只发行执行AI-派单-10：固定750上受限兼容已知宿主前导重试提示，原始字节/诊断保留、其余失败门不变。完整提示词 `reports/gemini-node22-review-20261023/execution-task-10.txt`，本轮仅生成，未确认发送。四条Windows测试债另记，不同时派修；真实认证及有价值真实任务在兼容修复后单独授权。下一未用编号执行AI11/Hark14。

本轮只写独立报告、探针和交接。未安装、启动真实Gemini、跑完整套件、提交、推送或合并。产品树仍03d9a02，09仍750，08/原树WIP/review-backlog及全部旧原始证据保留。Hark09三尾项及production complete/自动应用等旧裁决不变。
