# Hark-派单-03：历史一致性只读校验（执行角色）

状态：架构师实施派单与合成反例交接，不是实现完成或验收。
用户已确认旧“执行AI-派单-04”“Hark-派单-02”两份均未发送，本轮作废。
用户选择 Hark 暂任执行 AI；仍仅能操作 GitHub 副本。当前 Codex 保持架构和独立审查。
本批只处理后台历史一致性，界面选中与滚动任务暂缓，不拼成双范围。

## 基线与资料

产品代码基线：`f638418d848e3df16db94cad8144dcc190b86f4f`。
此任务包所在后续提交只改文档和独立合成探针，产品代码应与该基线完全相同；
以架构师聊天中的完整同步提交 SHA 固定开工起点，不从 main 或旧 Hark 分支开工。
规则为 AGENTS.md v2（含用户 vN 节）及 docs/collaboration-rules.md v2。
新云端分支：`hark-history-consistency-20261019`，不复用作废派单的分支。

种子反例：[probe_history_consistency.py](hark-history-consistency-20261019/probe_history_consistency.py)。
Windows 派生摘要：[baseline-windows-summary.json](hark-history-consistency-20261019/baseline-windows-summary.json)。
两者使用全合成登记，不需要 D 盘、auth.json、真实数据库或原生会话日志。
摘要来自本地原始输出，移除了本机工作树路径；不是云端复现实测记录。

探针接受显式 --repo 和全新的 --out；输出目录已存在时拒绝覆盖。
它只调用 AppInterface 查询，不启动 app-server、不调用模型、不修复登记。
--expect baseline 中六项通过只表示“复现了旧行为”，不是业务安全验收通过。

在仓库根目录，基线复现示例（Linux shell）：

```bash
REPO="$(pwd)"
PROBE="$REPO/reports/hark-history-consistency-20261019/probe_history_consistency.py"
OUT="$REPO/data/hark-history-consistency-baseline-01"
python "$PROBE" --repo "$REPO" --out "$OUT" --expect baseline
```

候选实现后使用另一个全新目录：

```bash
REPO="$(pwd)"
PROBE="$REPO/reports/hark-history-consistency-20261019/probe_history_consistency.py"
OUT="$REPO/data/hark-history-consistency-candidate-01"
python "$PROBE" --repo "$REPO" --out "$OUT" --expect fixed
```

不要修改本种子探针让候选通过；新增故障类测试另写。
本地已验证六项旧行为：正常完整记录与 reserved 前缀均 ok；
绑定变更、指纹变更、事件重排和同幂等键第二个 run 也仍为 ok。
后三个类别加绑定变更共四个反控，修复后应为 corrupt、runs=[]、count=0。

## 实施范围

新增纯只读的跨记录一致性校验，由 AppInterface 的列表与详情查询消费，
在分组摘要和证据读取前拒绝矛盾历史。保留既有 absent/empty/unreadable/corrupt/ok 信封；
损坏详情不返回正常 run，正常字段结构不变。

不改共享单条解析器、适配器 _load_registry、恢复、派发、传输和协议；
不改 GUI、预算、成功门、权限、文件应用、规则、旧 Hark 验证器和封存夹具。
不接 GUI 执行、指定审查、有界整改或新运行时；不调用模型和现有会话。
本批不改变适配器恢复对历史的处理；不能把查询层校验说成派发门已加固。

## 必须覆盖的类别

1. 同 run 的 app_kind、instance_id、session_id、task_id、attempt、
   request_snapshot、request_fingerprint、idempotency_key 跨记录一致。
2. 原生 session、turn、model 标识允许未知到已知的合法演进；
   已知冲突、倒退或重排的处理须以实际写入端为依据，不任意推断恢复行为。
3. 同一登记源内沿用当前适配器幂等索引作用域，
   非空键不能矛盾绑定不同指纹或不同 run；合法 null 可以重复。
   不新增跨文件或跨产品全局键空间。
4. 逐 run 按文件追加顺序检查六类实际事件：
   reserved、dispatch_failed、dispatch_uncertain、dispatched、collect_timeout、collected。
   先审查 _persist 的全部调用点，列出事件/状态合法分支再实现。
   正常未完成前缀、失败、不确定分支、多 run 交错、相同时间戳须保留。
   未知事件、重复、逆序、终结后追加不能默默排序、去重或修复。
5. 任一语义错误使整历史按 corrupt 处理，不展示部分正常摘要；
   所有源文件字节不变，坏历史不继续核对其证据。
6. 既有可读性/JSON/字段校验结论保留；不因本批顺带重做其实现。

种子探针只是入口，须补齐以上类别的正反控。
不得仅针对四个名字硬编码；报告逐类写预期、注入、证据、已覆盖或未覆盖。

## 验证与回传

- 基线与候选分别复跑种子；正常两项不变，四项错误归 corrupt。
- 独立测试本批校验和 API 列表/详情接线；伪输入不触发模型/传输，源字节不变。
- 新增守卫逐项削弱，证明相应行为检查变红；异常、冗余、未确定不算检出。
- 完整套件在本平台跑基线与候选，提交后复验。基线 AST 632；
  保留既有测试，新增数量用运行结果和 AST 核对。
- Linux 不绿时逐项列新增/既有失败，保存两侧原始日志和对比；
  失败集合相同只证明条目未新增，不自动证明根因，更不代替 Windows 验收。
- 不修改既有测试或加 skip 来迁就平台；新语义测试应可跨平台运行。
- 如需修改已验收测试/契约，停止相关动作并交裁决，不自行放宽。
- 本批不需要本机真实会话或数据库；不要把缺少本地真实证据当作实施前提。
  Windows 上的既有真实登记兼容性留待本地集成。

仅在新分支提交并推送本批实现、新验证和报告，
不改 main/n1-artifact-entry，不合并 PR #1，不删除旧报告。
冻结的任务包/探针保留原样，新增自己的证据与报告。
交付第一行注明“Hark（执行角色）”，按 AGENTS.md vN 返回第 0—6 项与完整转发块。
最终结论为“云端实现候选，待独立复核与 Windows 集成”，不得自验收。

停止条件：基线不符、种子反例无法在基线复现、必须扩大上述授权，
或必须改派发/恢复/接口契约才能完成时，停止相关操作并交证据。
本地集成和最终验收仍由后续明确派单安排。
