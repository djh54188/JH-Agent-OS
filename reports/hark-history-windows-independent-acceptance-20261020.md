# 登记历史一致性 Windows 集成：独立验收

日期：2026-10-20（Asia/Shanghai）。执行方目录和报告以派单日期 2026-10-19 命名，不据文件名推断取证时刻。

## 裁决及边界

执行AI-派单-05按“登记历史跨记录一致性只读查询的 Windows 精准集成”范围验收通过，无新增整改。

- 基线：e850ec755a08048309cdab2a0acec6417b1e3711。
- 移植：e99578900ca5a9162b7cfd2b1ad404ef1ddd2021，四文件。
- 已授权旧守卫分类偏离：575b627a241d48554d6e5bb07bb1420fa2c44d94，两文件，单独提交。
- 执行方报告/状态：3f891a6f508aa2556805beb3cef5930e3acd46ab，四份文档。
- 六文件的本地磁盘字节、Git blob及前轮已独立取得的 e5f786e 候选字节完全一致。父链、文件集合和分组符合派单；规则、GUI、HTTP入口、适配器及忽略策略未改。

本裁决只保护查询路径。实际恢复/派发、时间戳单调性、指纹重算、快照内部绑定、任意 extra 完整语义、文件系统并发仍未验证；reserved→collected 的不完整历史仍拒绝，不修写入端。N2/P1、GUI派发、指定审查与有界整改闭环未完成；production complete 和自动文件应用继续关闭。

## 验收证据

架构师证据位于本地忽略目录 data/hark-history-acceptance-20261020/，不随 Git 上传。执行方证据保留于 data/hark-history-integration-20261019/。

| 核对 | 结果及证据 |
|---|---|
| 文件身份及范围 | 六文件 Git blob/磁盘 SHA-256/候选字节一致，results.json；未修改或代提交执行方实现 |
| 完整套件 | 读取执行方 precommit-suite.txt、postcommit-suite.txt，均外层 Ran 704 / OK(skipped=1)，即703通过；架构师未运行全套 |
| 定向测试日志 | 执行方提交前、后均72项OK；架构师未重复整份定向套件 |
| AST独立计数 | 本地当前tests静态计数704 = 632+63+9，与运行器计数一致 |
| stage冻结反例 | 架构师在当前正式工作树复跑77项，7正常/70 corrupt、异常0，非法项list/detail拒绝、证据不读取、源字节不变 |
| 种子反例 | 两正常项ok，绑定/指纹/重排/同键第二run四反例均corrupt/0/[]，源字节不变 |
| 分类矩阵 | 当前脚本1458格全部符合预期 |
| 新守卫独立复跑 | 24对照、24变异、24断言检出、无错误、exit0；文案比较关闭 |
| 旧守卫独立复跑 | 8 detected + 1 redundant、exit0；唯一冗余为registry_field_validation_skipped，双层隔离诊断counted=false |
| 查询位置 | check_history在字段校验后、分组/摘要/证据/任务关联之前；只捕获HistoryInconsistent |
| 健康返回字段 | 对同份真实旧历史，正常路径与临时关闭新增校验的list/detail完整返回值逐字段相同 |
| 前端日志 | 核对执行方JH019 4/4、JH020 13/13、变异2检出+2冗余；GUI字节未改变，未重新采集界面截图 |
| 旧真实证据 | 源5b1dc26历史仍ok/count=1/verified/live=false；登记及原始字节未变，磁盘摘要de7f3bb977bc6796d341461ce4943ddfa95da29af27b3d1e86044e0329b9bccf、8868字节/26帧。无模型调用 |
| 可复现性 | 执行方stage/seed/classification两次结果逐字段一致；架构师复跑写入独立新目录，未覆盖旧证据 |

环境：Windows、Python3.14.7。守卫探针仅在进程内使用继承ACL的mkdir作为临时目录创建接缝；未修改系统ACL、全局配置或产品源码。该环境处理不作产品能力证据。

可复现命令（每次使用新输出目录）：

```text
python reports/hark-history-review-20261019/probe_stage_shape.py --repo . --out <新目录>/stage --expect fixed
python reports/hark-history-consistency-20261019/probe_history_consistency.py --repo . --out <新目录>/seed --expect fixed
python reports/hark-history-review-20261019/probe_guard_classification.py --repo . --out <新目录>/classification
python scripts/verify_hark_history_consistency_guards.py
python scripts/verify_jh020_run_history_guards.py
```

本地详细取数与回读脚本为 data/hark-history-acceptance-20261020/review_probe.py，结果为 results.json、new_guards.json、old_guards.json、healthy-and-frontend-audit.json；原始私有会话证据不复制进公开报告。

## 后续

不新增历史校验整改。按既有授权同步已验收n1分支，保留main、PR#1和Hark分支。远端实际核对结果记在本地sync.json，本报告不提前假定推送成功。

下一批回到此前暂缓的只读GUI交互：历史列表当前选择提示和单列布局下点击后的详情定位，作为一个有限能力交Hark-派单-05。基线源码已确认没有选中态或滚动行为，现有单列断点为1100px；保留请求代次、响应run_id绑定和2检出+2冗余口径，不接后台执行。不恢复已作废的旧派单编号，不把新提示词生成当成送达。
