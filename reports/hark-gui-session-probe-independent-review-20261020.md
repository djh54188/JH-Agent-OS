# Hark-派单-06：GUI会话试运行独立复核

日期：2026-10-20（Asia/Shanghai）。架构师职责：读取候选、核对证据、运行有界独立探针；没有接管实现、运行全量套件或调用真实模型。

## 裁决

**暂不验收，不进入Windows产品集成。** 候选的默认关闭、显式确认、固定请求、真实适配器接线及分层展示方向成立，但R1同源互斥、R2写入隔离、R3新增验证器分类有可复现反例。三项均限于本批GUI会话试运行准入，不重开既有历史查询、适配器或GUI选择定位的验收。

现场本地及远端n1为6725d60796256a59577d6e50e7732b5145739d4a；Hark分支头287317e7810609e7c0e80c45c364c6d3c6373f5d。main仍bc3e0f286d4a8a2863ab15daf223a2900f0dd44a。候选未覆盖本地产品；本报告同步只带架构师资料，不表示功能已安装。

固定候选：功能1a0c7369bdff3c2d05d2e157e5bf95052bd08275；独立守卫偏离8a20ab66eff2cc8bd746af89293db4a1390f0fd8；报告287317e7810609e7c0e80c45c364c6d3c6373f5d。已核实三提交父链及49个变更文件的Git blob（11个代码/测试/脚本文件，其余为报告与证据）。.gitignore、既有适配器、会话契约、历史校验器均未改。

## 已确认问题

### R1：单源互斥实际仅为单个服务对象互斥（P1）

`src/session_probe.py:241` 的锁和inflight属于对象；start在435–458行检查后排入后台任务，写入reserved发生在之后的_execute/adapter.submit。两个AppInterface共享一个登记源时，可以都先受理，再依次执行：第一个thread/start超时后，第二个仍派发。没有要求真正并行调度，也没有改生产守卫。

独立结果two_service_objects_one_source：两个start均accepted；执行第二个worker之前，状态已因第一条dispatch_uncertain而blocked；最终**thread/start=2、未解决run=2、登记state=ok**。同对象正控仍只受理一个，重开遇到已有未解决记录也仍拒绝。

原任务要求的是“同一登记源最多一个未解决”，不是“每个对象最多一个”。报告披露多进程未测不等于已获准缩小该约束；当前反例甚至在同进程两个对象即成立。整改可安全拒绝第二个同源使用者，无需建设调度平台。须覆盖多个对象及多个进程、受理到持久登记间隙、超时/退出/重开；不能只补一处锁而继续依赖不受约束的第二实例。

### R2：来源身份和写入路径隔离可绕过（P1）

`src/session_probe.py:256–277` 用realpath字符串比较同文件，只对四个末端路径做islink，漏掉硬链接、Windows目录联接和祖先组件。

| 独立输入 | 实际结果 |
|---|---|
| 登记与历史路径完全相同（正控） | 拒绝，零thread/start |
| 不同路径、os.path.samefile为true的硬链接：空历史 | 派发一次，历史字节被追加 |
| 硬链接：含完整健康历史 | 同样派发并改变原历史字节 |
| probe根为Windows junction | islink=false/isjunction=true；派发一次，登记与raw写入链接目标 |
| probe的祖先为junction | 同样写入目标目录 |
| raw目录为junction | 派发一次，raw写入目标目录 |
| ws目录为junction | 派发一次，隔离工作目录建立在目标目录 |

全部链接目标均是探针新建沙箱目录，没有触碰真实历史。修复须保护文件身份及静态路径组件，不只覆盖符号链接末端；合法独立目录保留可用，冲突/不可信布局须在外部调用前拒绝，原历史字节不变。任意并发文件替换的TOCTOU尚未验证，不将其等同为本轮静态链接反例，也不要求扩建通用文件执行器。

### R3：新增验证器把error/skip/零测试记成冗余通过（P2）

`scripts/verify_hark06_session_probe_guards.py:140–145` 的redundant分支只看旁路diagnostic的thread_starts==1，没有要求正式变异测试干净通过。

这不是推测：Hark提交的`reports/hark-gui-session-probe-20261020/logs/postcommit-hark06_guards.txt`中replay_check_removed已是**control=passed、mutated=error、verdict=redundant、整体exit=0**；架构师在Windows原样复跑得到相同分类。原因之一是正式目标仍预期replay响应，而削弱后抛出probe_not_prepared，属于error。

独立分类探针固定对照passed、旁路诊断一次派发，逐项注入正式结果passed/assertion/error/skipped/zero_tests，**五种全部得到冗余/exit0**。只有正式样例干净通过且隔离诊断成立，才可记已声明冗余；error、skip、零测试、坏对照和无效诊断不能变成通过。断言失败按真实检出证据分类，不能混入冗余。本轮不把旧Hark两个大验证器的已知问题一并重开。

## 承接与关闭

- 接受8a20ab6的有限架构偏离：仅session_probe应用服务可直接消费RunState/SessionAddress/SubmitRequest；其他核心模块的旧扫描仍保留，仅AppInterface接入该服务。**这是有意放开一个受限消费者，不称为全局只收紧。** 原扫描函数保持，新增四项测试及反控已读、已跑；计算型动态导入仍非静态扫描保证。
- 接受测试专用POSIX接缝的分级说明：产品源未改，Windows条件下接缝不启用；Linux的verified不可替代Windows真实路径。独立Windows探针未应用该接缝。
- 接受启用态页头说明、内存prepare编号和合成来源标记；无需Hark重做视觉稿。确认内存编号不能代替R1所需的单源互斥。
- 默认关闭、严格confirm true、浏览器字段限制、Host/Origin/JSON动作入口、固定worker新会话、文本输出、未知用量不置零、现有历史/选择守卫按本轮定向证据成立，不追加无依据整改。

## 独立证据与局限

本地忽略目录`data/hark-gui-session-probe-review-20261020/`保存候选、逐文件identity、隔离snapshot和日志；正式产品源码字节未变。

- Windows win32、CPython3.14.7、Node24.19.0。隔离副本定向运行新40项+JH018的46项：**Ran86、84通过、2跳过、0失败/异常**。两项是POSIX权限与符号链接创建测试；不把跳过算通过。仅临时目录创建使用进程内继承ACL方式，无全局ACL/产品改动。
- 前端原样复跑：新13/13、JH019的4/4、JH020的13/13、选择11/11。没有运行浏览器；亲看两张Linux合成截图及读取浏览器JSON，未当WindowsGUI实测。
- 新验证器原样运行表面16检出+1冗余/exit0，R3已证实该整体通过口径不成立；保留原始输出，不恢复成验收数字。
- 读取四份Hark全量日志的最外层704/748及失败ID：无代理21F/63E/2S，有代理27F/78E/2S，前后集合相同。日志含子进程Ran1/error，提取时与末尾整体摘要分开。独立AST748。架构师**没有跑748全套**。
- 独立边界探针16项观测，在两个全新目录同输入复跑；去除随机probe/run标识后逐字段一致。16是观测数量，不是16项通过。模型调用0，真实app-server0，外部协议仅离线传输夹具；thread/start计数是一条派发中的协议动作数量。
- 未覆盖：实际多进程运行、任意TOCTOU替换、真实模型/WindowsGUI、键盘读屏；多进程的同源约束仍需整改证据或明确拒绝第二使用者，不以未测直接视为通过。

## 可复制复现

冻结脚本与结果：[探针](hark-gui-session-probe-review-20261020/probe_boundaries.py)、[Windows候选观测](hark-gui-session-probe-review-20261020/windows-candidate-results.json)、[核验摘要](hark-gui-session-probe-review-20261020/verification-summary.json)。

```text
python reports/hark-gui-session-probe-review-20261020/probe_boundaries.py --repo <完整候选工作树> --out <不存在的新证据目录>
```

脚本exit0仅表示取证结束。依赖候选测试的离线Rig/EchoTransport；Windows真实建立junction但目标始终在新证据目录内，Linux对应测试符号链接并明确平台差异。不会启动真实模型。整改方使用只读副本复跑；如安全拒绝发生在更早阶段，另存包装/观测脚本并解释，不能改写冻结输入或把取证退出码当验收通过。

## 后续范围

Hark-派单-07续接已交付06，仅关闭上述三类准入问题并补故障类反控；具体实现由Hark决定。无需改变GUI视觉或放开一般派发。执行AI-派单-07尚未发行；待候选复核准入后再做Windows精准集成及一份新的真实GUI往返证据。production complete、自动文件应用、指定独立审查和完整协作闭环继续未开放。
