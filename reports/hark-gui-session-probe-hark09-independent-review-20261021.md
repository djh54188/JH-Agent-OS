# Hark-派单-09 独立复核 · 2026-10-21

## 裁决

**原 R4 的十类正常/异常目录绑定反例按离线范围关闭；仍暂不准入 Windows 产品集成。** 新增观测的读取失败边界及一项 Windows 测试输入需补齐。真实消息回显和先前 R1/R2/R3 的关闭结论保留，不重做。

复核对象：功能 `dcbd832d8f71b49db1ca9e95aa1189228d48d20c`、验证器 `0efdc04deb5a76923c582a4df5c823946b34748c`、报告 `175f52b64ac3e9cfb9e409be8c2db3f20da7725f`；父基线 `dc6c5c2c17a7f0b5b07c7d41e59f43ddee75ca4f`。三者父链及范围核对，无适配器、契约、核心成功门、规则、忽略策略、旧证据改动。

本次 Windows 3.14.7 隔离副本仅运行必要的离线探针、21 项新增测试、相关验证器和前端 harness。**没有跑完整套件、启动真实 app-server/模型或操作 GUI。** Hark 的 Linux 结果不能替代 Windows；下述 I/O 故障为可重复的离线注入，不声称真实服务已经发生这些故障。

## 本轮关闭与接受的范围

- 冻结 49a7d1e 探针原样、全新目录复跑，Hark 独立断言层 10/10 通过：绝对/相对/点/空格中文一致；父级片段零派发拒绝；原生不符/缺失/外来线程/矛盾均不再展示已核实空列表。旧错误 cwd 真实往返仍只支持消息结论。
- 原样前端 harness：hark06 15/15、JH019 4/4、JH020 13/13、Hark05 11/11。新增工作区状态与回显分开，旧载荷无观测时不展示为空。
- Windows 相关验证器为 **28 detected + 1 redundant + 3 control_failed，exit 1**。三个仍是 ancestor_link_check_ignored、subdir_link_check_ignored、worker_recheck_ignored 的 POSIX 样例跳过；新增六项均检出，不凑 Linux 31+1。原有计分分类未放宽。
- 接受父级片段一律拒绝、服务构造时一次锚定、同线程原生响应捕获、工作区字段增量和旧 []→None 的限定语义调整；接受未整体验收新功能自己的 EchoTransport 夹具及 hark06 harness 增补。已验收通用适配器、契约和旧业务测试未改。服务端捕获文件不能单独掩盖原始流的矛盾或读取失败。
- 六个代码/测试/验证器文件及报告资产的快照共 826 文件与 Git blob 逐字节一致；独立 AST 819。读取云端四份完整日志：798→819；无代理 F21/E63/S2、84 个去重失败 ID；有代理 F27/E78/S2、105 个去重失败 ID，前后集合一致。不归因所有 Linux 失败，也不称全套通过。

## R4 尾项 A：读取失败仍可能宣称已核实为空

### 原生观测读取失败被当成没有通知

候选 `src/session_probe.py:906–919` 在读取/解码原始通知失败时将 frames 置空，却继续以先前的 evidence.state=verified 和服务端捕获判定目录。原始流中的矛盾因此被丢弃。

独立输入：捕获响应 cwd 与计划一致，同线程原始通知 cwd 与计划矛盾；原始文件摘要正常，完整读取的对照为 `native_conflict / files=None / verified_empty=None`。只让新增观测读取这一次报告 PermissionError、OSError 或 UnicodeDecodeError，三个结果全部变成 `verified / files=[] / verified_empty=true`。

没有改写 raw/registry/capture 字节；不是同时替换文件的竞速，不要求建设任意 TOCTOU 平台。它证明“读不到反证”被当成“没有反证”。可复用完整、已关联核实的数据，也可诚实降为未知；不能因本次读取失败提升确认程度。

### 目录枚举失败被当成空目录

`_listing` 的 os.walk 未传播枚举错误；新增 `verified_empty` 直接以返回 [] 作真值。独立工作区确实有 `nested/exists.txt`：完整读取列出该文件、verified_empty=false；稳定注入根目录 PermissionError、子目录 PermissionError、根目录 OSError，三项都变为 [] / verified_empty=true。

目录绑定核实与枚举完整性可以分别表达；即便绑定仍成立，只要枚举失败/不完整，就不能宣称工作区为空。这个反例不涉及目录逃逸、工具写入或 syscall 竞速。

## R4 尾项 B：捕获文件合法 JSON 非对象导致查询异常

候选 `src/session_probe.py:895–896` 对 facts 先作 dict 判断，随后仍无条件调用 data.get。捕获为 `null`、空数组、对象数组、字符串、整数、浮点数、布尔值七种可解析非对象 JSON 时，`session_probe_status()` 全部抛 AttributeError，没有返回“捕获无效/工作区未核实”。

独立 14 格包括正常对象、坏 JSON、七种非对象、缺键、facts 非列表、fact 非对象、错 probe id、同线程 cwd 类型无效。正常对照 verified，其余对象形状与坏 JSON 已能保守拒绝；上述七种失败复现。每次查询新增 thread/start=0，捕获字节不变。损坏输入由探针写入自己的新目录，不是执行代码修改了证据。

## Windows 新测试输入

新增 21 项实际为 **18 通过、1 失败、2 POSIX 跳过**。失败 `TestResolution.test_classify_real_windows_shape_is_not_verified` 使用 `os.sep + "srv"` 构造 Windows 根相对路径 `\srv\...`；Python 3.14 上它不是带盘符的绝对路径，因此产品返回 dispatch_not_absolute，测试却要求 native_mismatch。

这是测试输入可移植性缺口，不证明产品应放宽拒绝。须使用真正的平台绝对路径维持原断言目的，保留派发非绝对路径的独立拒绝反控；不得跳过此项或把任意返回都算通过。

首轮普通沙箱测试另受 Python TemporaryDirectory ACL 影响；随后在同一受控临时根、仅离线测试的单独执行中隔离出上述唯一真实失败。未修改 ACL，也不把沙箱拒绝访问计成产品故障。

## 冻结反例与覆盖

资产目录 [hark-gui-session-probe-hark09-independent-review-20261021](hark-gui-session-probe-hark09-independent-review-20261021/)：

- [probe_capture_boundary.py](hark-gui-session-probe-hark09-independent-review-20261021/probe_capture_boundary.py)，SHA-256 `80df75c33ded02805f6643720be8784ddc751be04ecc52ffe1264b4e06eaac18`。
- [probe_observation_reads.py](hark-gui-session-probe-hark09-independent-review-20261021/probe_observation_reads.py)，SHA-256 `2c678f53186457c7e7fe2a55f1ab1aa82afda5b9cd1c1a5f19d8f7c75e32f4e0`。
- capture-results.json（14 格）、read-results.json（8 格）、frozen-assertions.json（原十项）、verification-summary.json 和 windows-new21.txt。均为离线观测或定向日志，非真实会话证据。

两份新探针各在新目录重复运行，观测逐字段相同；捕获生成摘要随随机 probe id 改变，不作为一致性断言。全部查询零追加派发、源字节不变。复制冻结脚本复跑，不覆盖原材料。

在 PowerShell 中设置 ANA=本复核材料目录所在完整仓库，BASE=完整固定候选 175f52b，再执行（输出目录必须全新）：

```powershell
$env:PYTHONDONTWRITEBYTECODE = '1'
python -X utf8 "$ANA\reports\hark-gui-session-probe-hark09-independent-review-20261021\probe_capture_boundary.py" --repo "$BASE" --out "$BASE\data\review-capture-fresh"
python -X utf8 "$ANA\reports\hark-gui-session-probe-hark09-independent-review-20261021\probe_observation_reads.py" --repo "$BASE" --out "$BASE\data\review-reads-fresh"
```

Linux 使用明确绝对脚本路径，参数相同；探针仅以新功能默认工厂配进程内传输夹具运行，并显式使用既有测试专用 POSIX 接缝。**退出 0 只表示观测完成，不是整改断言通过。** 新捕获边界覆盖所有 JSON 顶层形状及直接关联嵌套形状；读取边界覆盖通知读取/解码、根及子目录枚举失败。未覆盖所有文件系统、任意 TOCTOU/断电、Windows UNC/设备路径完整语义、真实 app-server 新捕获字段形态和真实 GUI；不把这些未测项自动登记为新缺陷。

## 后续与发布

本轮只提交架构师报告、冻结离线反例/观测和交接，不修改候选产品、旧证据或执行方提交。只更新独立复核分支 architect-gui-workspace-review-20261021；本地 n1 未验收产品仍不推到云端 n1，Hark 产品分支、main、PR#1 保持原状。实际远端 SHA/字节核对留本地 data/hark09-workspace-review-20261021/sync.json。

本轮新 Hark-派单-10仅处理上述观测失败边界和直接相关测试输入；原十项绑定已关闭，不再次重建路径层。不允许真实模型/app-server调用。执行AI-派单-08仍未发行，待修复复核后再安排精准集成与一次新的真实 GUI 路径。Hark-派单-09已确认送达交付；派单10生成不代表送达。下一新编号执行AI-派单-08、Hark-派单-11。
