# Hark-派单-07限定整改：独立复核

日期：2026-10-20（Asia/Shanghai）。源功能e02faf4108d1d3ebc5082bc0dc4dc493a3d321b2、验证器bddaf3f6617b976fc0295653c03724ce4dfa69d8、报告5c466b6980fb97e7759fde79d0583e1e12974051；源基线287317e7810609e7c0e80c45c364c6d3c6373f5d。本地产品仍为3d29343的代码，候选只在忽略目录隔离副本中读取/运行。

## 裁决

**暂不准入产品集成；原反例的核心整改成立，留下两项限定尾项。** 不再重做R1同源互斥、启动前的硬链接/junction拒绝或原R3的error/skip/零测试分类。新尾项是R2的拒绝后收尾写入，以及R3已知削弱未生效与断言结果矛盾时的分类。不是新增产品方向，也不要求解决任意文件系统竞态。

核实三提交父链和54个变更文件Git blob。与派单06候选相比，只改应用服务、新验证器及本批测试/辅助模块/报告，GUI、既有适配器、会话契约、历史校验器、旧守卫及规则均未改；未移入本地生产。

## 本轮关闭与偏离裁决

1. **R1原反例关闭（离线范围）。** 冻结脚本在Windows实跑：同源第二服务对象被probe_claim_unresolved拒绝，thread/start从2降为1，未解决run从2降为1。新定向测试的四进程并发、持锁拒绝、受理后退出/重开均在本机Windows通过，使用的是实际msvcrt文件锁、离线传输。没有把这些测试当真实模型或GUI验收。
2. **R2原静态输入反例关闭。** 冻结脚本在Windows真实创建根、祖先、raw、ws四类junction，均零派发、目标目录无新增文件/目录；空历史及健康历史的硬链接均拒绝、原字节不变。Hark自己未实测Windows的说明准确，此次补充独立证据不回写其Linux报告。
3. **R3原error/skip/零测试误归冗余的问题关闭。** 相同冻结结果注入现在返回非零；干净正式样例、有效诊断、削弱活跃检查的冗余新口径可接受。换用本批新增的“同id不重复派发”样例可接受，它没有改旧已验收守卫，也保留原replay响应测试。
4. 接受偏离③：祖先链接/联接也拒绝，宁可要求另配普通试运行目录；不得借此自动放宽路径检查。接受偏离④：退出遗留accepted认领保持人工处理，本批没有自动清除入口。
5. 接受测试辅助模块、默认None的_claim_hook和本批新验证器目标调整；无须再调整已接受的JH018守卫偏离、GUI视觉或既有适配器。

## 尚未关闭的两项

### R2尾项：已拒绝的路径仍被收尾重新打开并写入（P2）

定位：候选src/session_probe.py的824–830行识别不可信布局并返回；finally的854行仍调用_finish_claim；其862行直接进入acquire_source_lock，后者395行以O_CREAT重新打开当前路径。收尾没有保持工作线程已经作出的路径拒绝。

独立脚本先正常prepare/start（deferred runner），完成目录布局改变，再运行worker。期间没有并发线程替换路径，没有在lstat/open之间争抢时间窗口：**worker开工时链接已经静态存在，而且业务明确识别并拒绝它。** 缺陷是随后的确定性收尾写入，不是要求本批承诺消除任意TOCTOU。

| 布局（都在独立新沙箱内） | 原生调用 | 结束后的链接目标 |
|---|---:|---|
| 根被junction指向空目录 | 0 | 新建probe.lock（0字节） |
| 根被junction指向已有同probe_id的合成认领目录 | 0 | 新建probe.lock；目标认领从accepted被改成finished |
| 祖先junction，目标已有普通probe子目录 | 0 | 新建probe/probe.lock |
| 仅raw为junction（正控） | 0 | 目标不变 |
| 仅ws为junction（正控） | 0 | 目标不变 |

全部旧目录只做新沙箱内重命名留存，未删除、未修改任何真实历史。收尾必须遵守来源身份和路径拒绝，不能新建外部锁文件、修改别处认领或向不可信来源写“finished”。正常来源的结束、未知结果阻断和正常下一次明确确认须保留；无法安全完成收尾时保守报告待人工处理，不扩自动修复。

### R3尾项：明确inactive的断言失败仍被算作检出（P2）

定位：scripts/verify_hark06_session_probe_guards.py的169–180行先处理mutated==assertion，再检查mutation_active。结果是同一行已明确mutation_active=false，仍可verdict=detected、counted=true、整体exit0。

独立矩阵覆盖active=false/true × passed/assertion/error/skipped/zero_tests共10格。false+passed安全失败，但false+assertion被算检出。另通过main入口使用nullcontext（确实没有修改守卫），注入对照passed/正式assertion，得到**mutation_active=false、detected、counted=true、exit0**。

这里的断言失败是刻意注入的验证器结果，不是声称真实业务测试自然出现了该失败；它用于核查计分层如何处理“已知未改动却变红”的矛盾。已明确未生效的削弱不能提供检出证据。修复只需处理这类已知矛盾，不要求为全部普通守卫新增活跃度证明，也不将未设置（None）与明确false混为一谈。

不因此宣称当前Linux的22项实际检出都无效；本轮22+1是所跑样例的运行记录，分类层仍有上述缺口，不能单独作为准入保证。

## 独立验证范围与证据

本地忽略目录：data/hark-gui-session-probe-hark07-review-20261020/。Windows win32、CPython3.14.7、Node24.19.0。架构师没有运行786项完整套件，没有启动真实app-server或模型，也没有更新本地GUI。

- 原冻结脚本16项观测原样复跑，SHA及输入不改，exit0只代表取证结束。实际Windows junction、硬链接和同源行为见frozen-01/results.json。
- 定向41+20+17+46=124项：**118通过、6跳过、0失败/异常**。跳过为POSIX权限和符号链接测试；另以冻结脚本实际junction覆盖静态类，不能将剩余跳过算通过。日志targeted.txt/targeted-summary.json。进程内tempfile创建使用继承ACL目录，不改全局权限或产品。
- 新验证器Windows原样输出**19检出、1冗余、3 control_failed，exit1**：三行对应Linux符号链接测试在Windows跳过。这是平台覆盖缺口，未篡改为Windows22+1，也没有将它冒充产品失败。已单独验证的Windows junction不自动折算成那三行变异检出。
- 阅读Hark四份748/786全量原始日志，最外层F/E/S相同；无代理原始FAIL/ERROR行85、去重ID84，有代理106/105，前后逐条集合相同。独立AST786。没有靠Linux失败集合相同宣称Windows全套通过。
- GUI及前端harness字节未变；新验证器的JS控制/定向削弱已在Windows运行，未再重复已通过的全部GUI浏览器证据。Windows真实GUI仍未测。
- 新边界脚本5个收尾布局、10格分类及一次main入口观测，在两个全新目录重跑，JSON逐字段一致，无需随机值归一化。它只使用离线夹具，零真实模型调用。
- 未覆盖：任意syscall间TOCTOU、断电持久性、全部文件系统类型、真实模型/WindowsGUI/键盘读屏。不把这些未覆盖项追加为本轮整改。

## 冻结复现与后续范围

新增只读输入：[probe_cleanup.py](hark-gui-session-probe-hark07-review-20261020/probe_cleanup.py)、[Windows观测](hark-gui-session-probe-hark07-review-20261020/windows-candidate-results.json)、[核验摘要](hark-gui-session-probe-hark07-review-20261020/verification-summary.json)。

```text
python reports/hark-gui-session-probe-hark07-review-20261020/probe_cleanup.py --repo <完整候选工作树> --out <不存在的新证据目录>
```

exit0仍只表示观测结束；脚本在Windows用真实junction，Linux用符号链接，模型传输始终为离线夹具。使用只读副本复跑，冻结文件不改；若新候选以更早安全拒绝改变观测路径，另存包装和解释，不改输入凑通过。

Hark-派单-08仅续接这两个尾项，目标式整改、不指定算法；已关闭的互斥与静态身份检查不重做。执行AI-派单-07尚未发行；待尾项独立复核通过后才安排Windows精准移植与新的真实GUI往返。production complete/自动文件应用/一般派发/指定审查均保持原边界。本次云端同步仅含架构师报告、合成探针及交接，不包含候选产品。
