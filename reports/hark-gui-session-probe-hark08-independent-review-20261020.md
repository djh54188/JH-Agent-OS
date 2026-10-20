# Hark-派单-08独立复核及Windows集成准入

日期：2026-10-20（Asia/Shanghai）。功能R2：0cff6ee7078eba6c03eed0b0c7822d4cda9f6c4a；功能R3：99abcd71db08931143612f36c9f6953718751c79；报告dc6c5c2c17a7f0b5b07c7d41e59f43ddee75ca4f；整改基线5c466b6980fb97e7759fde79d0583e1e12974051。

## 裁决

**R2/R3两个尾项按限定范围关闭，准许Windows精准集成。** 不是候选已经安装、Windows完整套件通过、真实GUI往返通过或JH-020/N2/P1完成。没有发现需要Hark继续整改的新反例，不扩大已披露的任意TOCTOU、断电持久性等边界。

核实三提交父链、62个变更文件Git blob；再从最初产品基线6725d60796256a59577d6e50e7732b5145739d4a对比99abcd7，得到16个待移植文件，并将隔离副本16文件与该固定提交逐项比对blob和SHA-256。没有整分支合并，也没有改变本地生产源码。

## 关闭依据

### R2尾项：收尾不再越过来源拒绝

读代码确认：打开锁前由锁函数自身检查来源布局；收尾使用create=False；受理时记录目录及认领文件身份；提交前与收尾锁内复核身份、普通文件/单链接及本次accepted内容。不可信时保留认领，报告cleanup_refused，而不尝试修复或重建来源。

两份冻结探针原样在Windows隔离副本运行：旧同源、硬链接、根/祖先/raw/ws静态junction反例仍被拒绝；新的五布局收尾反例不再创建外部锁，合成认领保持accepted。R3明确inactive的main反例返回exit1/fail。

为补足POSIX样例在Windows跳过的部分，独立新增原生junction字节审计：[脚本](hark-gui-session-probe-hark08-review-20261020/probe_native_cleanup.py)、[结果](hark-gui-session-probe-hark08-review-20261020/windows-native-results.json)。六类全部符合预期：

| 原生Windows输入 | 外部调用 | 目标目录及文件字节 | 原认领 |
|---|---:|---|---|
| 根junction，目标为空 | 0 | 不变，无新锁 | accepted不变 |
| 根junction，目标有同id合成认领 | 0 | 不变 | 原件及目标均未被标finished |
| 根junction，目标另有合成锁文件 | 0 | 全部字节不变 | 原件及目标保持原样 |
| 祖先junction，目标有普通probe目录 | 0 | 不变 | accepted不变 |
| 仅raw为junction | 0 | 不变 | 在真实来源内finished/refused_before_dispatch |
| 仅ws为junction | 0 | 不变 | 在真实来源内finished/refused_before_dispatch |

审计在两个全新目录重复，六类调用/状态/保持字节等事实一致；各次随机合成认领哈希不同，各自before/after一致。原冻结cleanup JSON两次逐字段一致。所有移动、联接目标均在审查新沙箱中，原目录留存，没有删除或写入真实用户来源。

### R3尾项：明确inactive不再提供检出证据

控制通过后明确False优先失败；None仍是未取证明，普通行断言检出规则不变，声明冗余行未取证明则mutation_activity_unproven。冻结10格矩阵及main入口结果符合这一口径；既有有效检出和干净、活跃且有完整诊断的冗余仍可成立。没有把None一并误当False，也没有重开旧验证器框架。

### 偏离裁决

- 接受①：锁文件丢失但来源未重定向时，已受理的派发可执行；收尾不重建锁、accepted认领继续阻断，需人工核对。它不是清除不确定状态或自动重放。
- 接受②：cleanup_refused作为last_error内部补充，保留原错误和人工处理说明；不扩建新的API路由或GUI操作。
- 接受③：未证明与明确未生效使用不同失败分类；接受④本批新增三条守卫变异/测试辅助函数。
- 派单06的JH018限定消费者偏离、派单07祖先链接拒绝及accepted遗留人工处理继续有效，不重做。

## 验证、计数与适用范围

本地证据根data/hark-gui-session-probe-hark08-review-20261020/；Windows win32、CPython3.14.7、Node24.19.0。所有运行均使用隔离snapshot，产品工作树只将新增架构师资料入库。

- 定向41+20+21+8=90项：**80通过、10跳过、0失败/异常**。日志targeted.txt、targeted-summary.json。包含实际msvcrt四进程互斥/持锁拒绝/受理退出，以及普通目录替换、同字节认领替换、缺锁不重建、正常结束和未知阻断。跳过为POSIX权限及符号链接类，另以原生审计补布局行为；不把跳过算通过。JH018的46项此次源/扫描边界未变，承接上轮通过，没有重复跑。
- Windows新验证器原样输出**22检出+1冗余+3 control_failed，exit1**。三行仅因其POSIX符号链接目标在Windows跳过：ancestor_link_check_ignored、subdir_link_check_ignored、worker_recheck_ignored。保留原结果，不要求精准移植时改测试凑成25+1；原生junction行为已另证，但不自动折算成那三行变异检出。若本地集成出现额外失败则须停止处理。
- 独立AST798；读取Hark786/798四份全量日志，无代理F21/E63/S2、有代理F27/E78/S2；去重失败ID84/105，前后集合一致。读取三份Linux新守卫25检出+1冗余/pass原始输出，不将其称为Windows成绩。
- **架构师没有运行798全量、启动真实app-server、调用真实模型、操作真实GUI或移植候选。** 生产GUI与旧验收结论保持原样。
- 旧真实历史只读核对：登记SHA-256=0ca5c260fae77de816a55253513b3e6d9153c152939f8d96c728c64e53ba85c1；raw=de7f3bb977bc6796d341461ce4943ddfa95da29af27b3d1e86044e0329b9bccf，8868字节/26帧，均未变。
- 临时目录只在进程内使用继承ACL创建方式；不改全局ACL/产品。文件系统任意syscall竞态、断电持久性、全部文件系统类型、真实GUI/模型等仍未覆盖，不新增推测性整改。

计数回源：targeted-summary.json来自unittest实际运行；新守卫原始输出guards-windows.json；云端原始日志位于固定dc6c5c2的reports/hark-gui-session-probe-hark08-20261020/logs/。本地摘要可读[verification-summary.json](hark-gui-session-probe-hark08-review-20261020/verification-summary.json)。AST独立遍历tests/test_*.py的ClassDef直接test方法，不用文本grep计数。

## 执行AI-派单-07的固定集成范围

按[port-manifest.json](hark-gui-session-probe-hark08-review-20261020/port-manifest.json)，只取99abcd71db08931143612f36c9f6953718751c79的16文件，磁盘字节及提交blob分别核对。该提交与报告dc6c5c2的产品文件相同。源JH018守卫变化单独移植提交，其余15文件为功能提交；执行方报告/状态另交，不代提交架构师交接或历史归档。

本批只有一个能力：**将已复核的固定标识GUI会话试运行精准移植到Windows，并经真正GUI入口取得一次真实往返证据。** 不改变候选，不扩一般派发、指定审查、continue/cancel、自动整改或文件应用。

必须完成：

1. 精准移植、身份核对；Windows完整套件及定向/既有前端回归、冻结探针及原生junction字节审计，数字从实际日志和AST提取。上述三个POSIX样例control_failed单列，不改原候选或把Linux25+1当Windows目标。
2. 默认未启用及打开/刷新/未确认均零派发，保留只读历史/草案/选择行为。必要时仅重启确认归属的旧GUI服务以加载新后台，常用桌面入口继续不带enable参数；不能让新静态页面搭配仍驻留的旧后台后宣称可用。不修改快捷方式来静默开启真实调用，不终止身份不明的进程。
3. 在功能及守卫提交后，另用本批全新目录及独立临时GUI服务显式启用，真实指针勾选确认并点击**一次**。模型/权限/预算满足已有配置，固定新worker请求，不替换为Mock/CLI/手工HTTP请求，不发送任何既有或指定审查会话。等待有界、不自动重试；结束后关闭本批临时启用服务，常用入口仍默认关闭。
4. 归档这一次GUI的计划/确认前后/结果截图、HTTP动作关联、本次run/thread/turn、固定标识回显、用量（绑定本轮或明确未知）、磁盘回读的原始证据哈希/大小/帧数、工作区空/无工具事实。只表示试运行往返，不授予Task succeeded或connected=true；不上传凭据和私有原始证据。
5. 真实调用最多一次，不确定、超时、异常、工具调用或约束不满足就停止真实路径，不换新编号或自动重试。报告/状态提交后产品代码未变可沿用此功能提交下的真实证据并注明源提交，不额外耗用模型复跑。

完成判据：上述离线/文件/GUI真实证据可逐项核对，提交前后完整验证按规则完成，旧历史字节未变；结论仍为Windows集成候选、交架构师独立验收。当前production complete、N2/P1及真实协作闭环状态不因准入改变。

停止条件：基线或归属冲突、身份清单不一致、需改候选/已验收契约或放宽门、额外测试失败、真实路径不确定。保留已完成证据并报告，不为了跑通修改候选或复写旧来源。本轮只同步架构师准入报告、清单、独立探针及交接；功能由执行AI另行移植，本批不推送其产品提交，独立验收后按既有授权同步；main/PR#1不合并。
