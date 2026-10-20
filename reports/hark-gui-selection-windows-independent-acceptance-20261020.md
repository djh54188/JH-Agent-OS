# GUI选中提示与窄窗口详情定位：Windows独立验收

日期：2026-10-20（Asia/Shanghai）。角色：当前指定Codex架构师及独立审查者。

## 裁决与范围

执行AI-派单-06按“四文件精准移植及Windows GUI选择/定位集成”范围验收通过，无新增整改。

- 开工基线bdc843616ce8ff46b2bb250e89196755283f7793。
- 功能d5b67d90cc4b3770e671ef9418be0e813ee27a59，父为基线，四文件、+582/−0。
- 报告/状态13c9e921a14b2899d62b966501ada70708c2b338，父为功能提交，仅四份文档。
- 四文件的磁盘字节、Git blob均与固定068afb5云端候选相同；身份目标承接[云端候选独立复核](hark-gui-selection-independent-review-20261020.md)，本轮再次逐项回读核对。

本批仅改善已有只读历史页面的选择与定位，不接入派发/审查，不改变成功门。production complete与自动文件应用继续关闭；N2/P1、指定独立审查和协作闭环未完成。

## 核对及证据分层

架构师本地证据：data/hark-gui-selection-acceptance-20261020/。执行方证据：data/hark-gui-selection-integration-20261020/。两者均为本地忽略目录，不随Git同步。

| 项目 | 独立核对结果 |
|---|---|
| 身份与范围 | 四文件Git blob/磁盘SHA-256/前轮固定候选字节相同；父链、功能与文档分组正确。后台、HTTP、规则、忽略策略和既有守卫未修改 |
| 回调及关联 | loadDetail全文保持原样；选择与滚动只在明确点击中调用；承接未变的四关联守卫；新增CSS只限历史面板，接受的min-height仅在单列非空详情生效 |
| 前端独立复跑 | 当前正式工作树JH019 4/4、JH020 13/13、新行为11/11 |
| 定向削弱独立复跑 | 新对照通过，3/3断言检出，无失败场景依靠异常计分；既有2检出+2冗余，pass |
| 基线反控 | 执行方保留的baseline-gui与基线Git字节相同；架构师独立指向它运行同一新增测试，1通过、10项断言失败、异常0 |
| 全量套件 | 读取执行方precommit-suite.txt和postcommit-suite.txt，两份均Ran704、OK(skipped=1)，即703通过。架构师未运行完整套件 |
| 独立AST | 当前tests顶层测试类方法静态计数704，与运行器计数一致 |
| Windows浏览器原始值 | 读取各evidence-*.json并独立与browser-audit-local.json对照：1440/1101两列scrollY不变；1100/760/480单列标题top30–31、唯一选中、无水平溢出；A到B立即清旧详情为加载态，稳定后唯一选中B |
| 亲自查看图片 | synthetic-selection-1440-noscroll、760、480-card、480-detail及real-history-1440共五张。选中描边、当前查看文字、按钮及长标识换行可辨；窄窗口标题可见；合成来源路径/任务标识与真实旧来源有区分 |
| 真实旧历史 | 源5b1dc26历史新进程只读查询仍ok/count=1/verified/live=false。登记磁盘SHA-256为0ca5c260fae77de816a55253513b3e6d9153c152939f8d96c728c64e53ba85c1；原始证据为de7f3bb977bc6796d341461ce4943ddfa95da29af27b3d1e86044e0329b9bccf，均与执行前后记录相同，未改字节、未调用模型 |

浏览器由执行方在Windows IAB（Chromium）用指针取证；架构师本轮只读截图及原始JSON并复跑离线探针，没有再次操作浏览器。迟到成功/错误、错误run_id、旧面板、空详情等由离线harness覆盖，不能根据执行方报告中合并的表述提升为浏览器错误帧实测。错误帧、键盘/读屏、加载中手动滚动等未测项保留，不新增返工要求。

Python3.14.7、Node24.19.0。变异探针在进程内采用继承ACL的mkdir作为临时目录创建接缝，未改系统ACL、全局配置或产品文件，不将环境处理当产品能力证据。

详细结果在results.json、new_guards.json、old_guards.json、三个*.mjs.json、baseline-contrast.json、browser-and-contrast-audit.json。取数/回读脚本为本地review_probe.py。

可重复的有界前端检查：

```text
node tests/js/jh019_generation_harness.mjs --json
node tests/js/jh020_run_history_harness.mjs --json
node tests/js/hark05_run_selection_harness.mjs --json
python scripts/verify_hark05_selection_guards.py
python scripts/verify_jh020_run_history_frontend_guards.py
```

## 收口

Hark-派单-05与执行AI-派单-06的GUI选择/定位能力关闭；旧“候选/待审”属于交付时快照，不重复派发整改。按既有授权快进同步已验收n1及本架构师裁决，main、Hark分支和PR#1不合并，data及用户历史归档不上传。实际远端完整SHA核对结果记本地sync.json，不在推送前自称同步成功。

本轮只做验收归档和同步，不新增功能派单或恢复已作废任务。后续Hark仍采用用户要求的目标式派单；具体实现自主决定。
