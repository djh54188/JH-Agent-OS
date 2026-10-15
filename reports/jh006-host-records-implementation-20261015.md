# JH-006 下一批：可信宿主记录装配 · 实施记录（未运行、未提交）

基线 e390867 已核对（`.git/refs/heads/n1-artifact-entry`）。本会话 Shell 不可用：
只做只读探针与文件实施，未运行任何测试/核对，未提交。工作树脏状态无法用 Git 核实；
本会话未做重写/重置，首批离线实现原样保留。文件应用门禁保持关闭，
FileToolExecutor 未启用，未改既有 Schema，未新增权限/供应商/服务。

## 只读探针结论

- P1（记录格式，已证实）：`reports/jh004-real-host-20261014/host-events.jsonl` 给出
  Codex 实际工具记录形状：`thread.started(thread_id)` 信封 +
  `item.completed/command_execution{id, command, aggregated_output, exit_code, status}`。
  本批生产记录即按此形状装配（命令全文、原始输出、退出码、线程/条目标识）。
- P2（实时可访问性，已证实不可行）： transcript 存储位于工作区之外
  （`C:\Users\a\.codex\...`，见已提交证据中的宿主命令路径），JH 控制层无任何
  可查询该存储的接口；`src/codex_managed.py` 仅封装 `codex exec` 模型调用，
  不提供 transcript 拉取；JH CLI 只暴露自有记录。因此桥接采用 in-band 携带
  原始输出 + 控制层重算摘要，不依赖外部日志引用。
- P3（残余信任）：摘要重算只能杀死摘要谎言；输出真实性超出机械核验，
  与 JH-004 相同口径，留作归档 transcript 的人工审查项（见下发架构师事项）。

## 桥接设计（最小）

- 新模块 `src/host_records.py`：生产记录形状（`codex-transcript/v1` 装配标识，
  绑定 task/delivery/thread/host_call/apply_attempt/workdir/artifact/review/
  命令/退出码/状态/原始输出/摘要/不确定性），精确键集合、装配标识、
  工作目录与产物/审核 pin、completed 状态、输出摘要重算、64KiB 携带上限、
  封存检查期望读取、transcript 命令标记检查（命名固定脚本与授权目标、
  拒绝 expected 树）。
- `src/orchestrator.py`：`complete_code_delivery` 按来源路由，生产任务进入
  新 `_complete_production_host_record`（来源污染复核、预算/门、记录校验、
  产物/审核 pin、退出码/命令标记/输出标记、目标字节重读、基线分支、
  范围核对、见证哈希对照、不确定性声明、事务终结与 `host_record_accepted`
  事件）；fixture 路径零改动。`uncertain`/退出码/标记/命令问题一律
  needs_attention，形状与绑定问题一律拒绝（无状态变化）。
- `src/cli.py`：`delivery_scope.evidence_kind` 区分 `offline_fixture` 与
  `host_transcript`；complete 命令描述覆盖 transcript 记录。无新命令，
  不加载模型。
- `.agents/skills/jh-task/SKILL.md`：生产宿主装配六步规程与六项禁止；
  更新两处过期表述（生产完成不再是“未实现/拒绝”，规则明确隔离工作区桥接）。
- `tests/test_jh006_host_records.py`：约 31 项离线正反控（合成 transcript
  记录均标 `test-` 前缀）：合法完成、来源隔离（双向）、摘要/标记/退出码/
  状态/命令门、产物/审核/工作区/装配标识/形状/大小/跨任务绑定门、
  基线/附加字节/范围外改动/见证哈希字节门、重复/迟到/占用/预算生命周期、
  CLI 生产往返与查询区分、封存期望与标记单测。

## 守卫→测试映射

摘要重算：篡改输出拒绝；输出标记：缺标记不确定；退出码/状态/不确定性声明：
各一正反；命令标记：缺标记与 expected 树不确定；产物/审核/工作区/装配标识/
键集合/大小/跨任务：各一拒绝；基线未应用拒绝，附加字节/范围外/见证不符不确定；
fixture 登记与回执双向隔离各一；预算/占用/重复/迟到各一；CLI 生产往返一；
evidence_kind 查询区分两处。

## 未运行项（交架构师承接）

1. `tests/test_jh006_host_records.py` 全文件。
2. 既有全量（基线 251 项）+ JH-005/006 守卫与变异抽查。
3. 按明确范围提交并提交后验证；真实取证在桥接审查通过后另行安排。

## 发给架构师的内容（需裁决）

1. 机器证据门槛：摘要重算 + 封存标记 + 全 pin + 字节/范围重读是否足够作为
   生产完成的生产证据；若要求 complete 时绑定归档 transcript 包哈希，
   需补充归档流程后再加一轮实现。
2. 残余信任确认：输出真实性超出机械核验，沿用 JH-004 人工核对归档 transcript
   的口径；是否需要额外的宿主侧防篡改措施由架构师定。
3. 本批未安排真实取证；桥接审查通过后再排期生产任务真实闭环。

实现注记：本会话编辑工具无法多行匹配 CRLF 原文，改动以单行锚点写入，
新增行 LF、原文 CRLF 混排，Python 可正常解析；提交前可由有 Shell 会话统一
换行检查。
