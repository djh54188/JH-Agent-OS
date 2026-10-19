# Hark-派单-03 交付报告：运行历史跨记录一致性只读校验

来源：Hark（执行角色）｜Hark-派单-03｜类型：交付报告
结论：云端实现候选，待独立复核与 Windows 集成。不是验收。

- 规则 / 开工基线：`b58033caef0a79b478f47a0a76612357d8dd1fd3`（AGENTS.md v2 SHA-256 `edc9846b33c908ee6f16597cd2dd920f1c0564489271713a027bbe7e24ea74b3`）
- 分支：`hark-history-consistency-20261019`
- 实现候选提交：`253d9d9087ca192d10a76bcb90fb40dda82082a5`
- 本报告与证据：本文件所在的后续提交（仅新增 `reports/` 下文件）
- 平台：Linux aarch64，CPython 3.14.8（uv）；仅依赖 jsonschema。不是 Windows 结果。

## 1. 改动范围（候选相对基线 4 个文件，+1039 行，无删除）

| 文件 | 性质 |
|---|---|
| `src/run_history_consistency.py` | 新增。纯函数 `check_history(records)`，只读已解析记录，不碰文件/进程/模型/会话 |
| `src/app_api.py` | +8 行：`_read_runs` 在 `parse_registry_text` 之后、分组/摘要/证据之前调用 `check_history`；矛盾 → `RUNS_CORRUPT, [], 原因` |
| `tests/test_jh020_history_consistency.py` | 新增 59 个测试方法（AST） |
| `scripts/verify_hark_history_consistency_guards.py` | 新增离线变异核验 |

未改：共享单条解析器（`parse_registry_text` / `validate_registry_record`）、适配器 `_load_registry`、恢复、派发、传输、预算、成功门、GUI、HTTP 入口、规则、既有测试、既有守卫脚本、封存夹具、旧 Hark 验证器、冻结任务包与探针（`git diff` 校验为零）。

## 2. `_persist` 调用点审查（写入端依据）

`src/agent_session_codex.py` 中 `_persist` 共 12 个调用点，全部在 `submit` / `collect`：

| 事件 | 调用点 / 分支 | state / native_state | native 标识 |
|---|---|---|---|
| reserved | submit 外部调用前 | queued / reserved | 全空，turns=[] |
| dispatch_uncertain | thread/start 超时 | unknown / dispatch-uncertain，stage=thread/start | session、model 空 |
| dispatch_failed | thread/start 拒绝、无 id | failed / thread/start-refused、thread/start-no-id | session、model 空 |
| dispatch_failed | 模型未兑现 | failed / model-not-honored，stage=thread/start | session 已知，model 空 |
| dispatch_uncertain | turn/start 超时 | unknown / dispatch-uncertain，stage=turn/start | session 已知，model 可空 |
| dispatch_failed | turn/start 拒绝、无 id | failed / turn/start-refused、turn/start-no-id | session 已知 |
| dispatched | turn 已启动 | running / turn-started，附 thread_id、turn_id | session 已知，恰一个 turn |
| collect_timeout | collect 未等到完成 | unknown / 沿用 turn-started | 与 dispatched 相同 |
| collected | collect 收到完成帧 | declared_complete 或 failed / 完成帧原样 | 与 dispatched 相同 |

合法文法（逐 run、按文件追加顺序）：`reserved → (dispatch_failed | dispatch_uncertain | dispatched → (collect_timeout | collected)?)`。合法未完成前缀：`[reserved]`、`[reserved, dispatched]`。幂等重放不写记录；恢复（recovered）后的 collect 不写记录；run_id 含 uuid，不会跨进程复用。

## 3. 不变量实现与覆盖

| 类 | 预期 | 注入（反控） | 正控 | 证据 | 状态 |
|---|---|---|---|---|---|
| 1 绑定一致 | 8 个字段（app_kind、instance_id、session_id、task_id、attempt、request_snapshot、request_fingerprint、idempotency_key）同 run 不变 | 每字段 × dispatched/collected 两个位置；key 空→有值 | 原样历史 | `TestBindingFields` | 已覆盖 |
| 2 原生标识演进 | 空→已知允许；已知→空、已知→另值、turns 非前缀均拒；collect 不得新引入标识或增 turn | session/model 冲突与倒退、turn 替换/丢失、collect 引入 model、多一个 turn | model 全程空、turn/start 不确定时 session 已知 | `TestNativeEvolution`、`TestEventShape` | 已覆盖 |
| 3 幂等作用域 | 同一登记源内非空键只绑一个 run；null 可重复；不跨文件 | 同键第二 run（同指纹、异指纹、交错） | null 重复、不同键、两文件同键各自 ok | `TestIdempotencyScope` | 已覆盖 |
| 4 事件顺序与分支 | 仅上表文法与分支 | 未知事件（如旧 `submitted`）、非 reserved 开头、重排、重复、跳过 dispatched 直接收集、终结后追加、各事件 state/stage/标识矛盾、thread_id/turn_id 与原生 id 矛盾 | 全部 12 种合法序列、多 run 交错、相同时间戳、dispatched 无回显字段 | `TestEventOrder`、`TestEventShape` | 已覆盖 |
| 4 写入端实证 | 真实写入端各分支产物均 ok | — | 用已验收 `FakeCodexTransport` 驱动 `CodexAgentSessionAdapter` 走 15 条分支（含幂等重放、多 run 追加） | `TestRealWriterHistoriesStayOk` | 已覆盖 |
| 5 整体 corrupt | 任一矛盾 → corrupt、runs=[]、count=0；详情 run=None、无 records；源字节不变；不读证据、不查任务关联；无进程/传输 | 好 run + 坏 run 混合历史 | 正常列表/详情字段集合不变；字段校验仍先于本层 | `TestApiWiring` | 已覆盖 |
| 6 既有校验 | 保留 | 单条字段错误仍由原解析器报出 | — | `test_field_validation_still_runs_first` | 已覆盖 |

未覆盖 / 有意不做：
- 时间戳单调性不校验（写入端用墙钟，可回拨；任务包要求保留相同时间戳）。
- 记录内 `request_fingerprint == sha256(request_snapshot)`、快照内绑定与记录字段一致：属单记录语义，未列入本批，未实现。
- `extra` 中 `stage` 以外的附加字段（error、reason、raw 等）不做跨记录比较。
- 恢复/派发路径（`_load_registry`）未接本校验；**本批只保护查询层，不宣称恢复或派发已加固**。
- 真实 Windows 登记兼容性未验证（云端无本地数据）。

## 4. 验证结果

种子探针（冻结，未改）：

| 用例 | 基线 `--expect baseline` | 候选 `--expect fixed` |
|---|---|---|
| valid | ok / 1 | ok / 1 |
| valid_reserved_prefix | ok / 1 | ok / 1 |
| binding_changed | ok / 1 | corrupt / 0 / [] |
| fingerprint_changed | ok / 1 | corrupt / 0 / [] |
| event_reordered | ok / 1 | corrupt / 0 / [] |
| same_key_second_run | ok / 2 | corrupt / 0 / [] |

云端基线与 Windows 派生摘要六项一致；源字节均未变；model_calls=0。基线通过仅代表复现旧行为。提交后在全新克隆上复跑 `--expect fixed`，退出码 0；新守卫核验 23/23，冻结文件 diff 为零。

完整套件（`python -m unittest discover -s tests -p "test_*.py"`），各跑带/不带 HTTP 代理两种环境：

| | 基线 b58033c | 候选（工作树） | 提交后全新克隆 253d9d9 |
|---|---|---|---|
| 运行数 | 632（= AST 632） | 691（= AST 691 = 632 + 59） | 691 |
| 无代理 | failures=21 errors=63 skipped=2 | 相同 | 相同 |
| 有代理 | failures=27 errors=78 skipped=2 | 相同 | 相同 |
| 失败 id 集合 | — | 与基线逐条相同（diff 为空） | 与基线逐条相同 |
| 新增测试失败 | — | 0 | 0 |

Linux 既有失败（基线即有、与本批无关）主要是 Windows 路径语义（如证据根判定 `outside_root`、调用 `cmd`）及代理相关 HTTP 用例。失败集合相同只说明未新增条目，不证明根因，更不代替 Windows 验收。

新守卫变异核验（`scripts/verify_hark_history_consistency_guards.py`）：23 个对照全部先通过；23 个变异逐项削弱，23/23 由断言失败检出、0 个以异常计。变异和对照时都关闭文案比对，只有 ok/corrupt 结果翻转才算检出。覆盖：绑定检查整体关闭 + 8 个字段逐一移除、原生演进整体关闭 / 接受倒退为空 / 不比较 turns、顺序整体关闭 / 接受未知事件 / 任意开头 / 终结后追加 / 重复 / 跳过派发收集、形状整体关闭 / 不查派发分支 / 允许收集引入标识、幂等关闭、AppInterface 跳过本校验。

## 5. 偏离与须裁决事项

**裁决项 1（影响既有守卫核验结果）**：已验收的 `scripts/verify_jh020_run_history_guards.py` 第 `registry_field_validation_skipped` 行，用宽松解析替换字段校验后期望 `test_corrupt_registry_is_reported_and_left_untouched` 变红。该测试样本把首条 `run_id` 置空，宽松解析下剩余记录变成“run 以 dispatched 开头”，被本批顺序校验拦为 corrupt，测试仍绿 → 该行变为“冗余覆盖、未检出”。

- 逐行隔离对比（`logs/jh020-rows-baseline.json` / `-candidate.json`）：9 行中只有这一行由检出变未检出，其余不变。
- 新脚本的 `redundant_not_counted` 表证明：同时关闭本层后，该行重新由断言检出，字段校验本身仍独立有效。
- 在 Windows（对照全过的平台）上，旧脚本整体 verdict 会因此从 pass 变 fail。
- 本批未改旧脚本、旧测试或其样本（规则禁止）。可选：(A) 授权在旧脚本该行同时停用本层（`check_history` 置空），保持其原意；(B) 授权给该行换一个只违反单条字段、不构成跨记录矛盾的样本（改既有测试）；(C) 接受该行改记为冗余。Hark 建议 A：改动最小，不碰已验收测试。

其他偏离 / 说明：
- 写入端可能出现的一种极端情形：`dispatched` 追加失败（OSError 抛出）后同进程仍对该 run collect，会写出“reserved → collected”。submit 抛异常时调用方拿不到 run_id，正常公共接口不可达，本批按 corrupt 处理并在此标明。
- 早期 14e42a0 写入端用 `submitted` 事件，但缺 `request_snapshot`、`idempotency_key`，已会被现行字段校验判 corrupt，本批不改变其结论。
- Linux 套件只用 jsonschema；未安装其他依赖。

## 6. 证据命令（仓库根目录）

```bash
# 基线复现（基线提交 b58033c）
python reports/hark-history-consistency-20261019/probe_history_consistency.py --repo . --out data/hark-history-baseline-01 --expect baseline
# 候选
python reports/hark-history-consistency-20261019/probe_history_consistency.py --repo . --out data/hark-history-candidate-01 --expect fixed
python -m unittest tests.test_jh020_history_consistency
python scripts/verify_hark_history_consistency_guards.py
python -m unittest discover -s tests -p "test_*.py"
python reports/hark-history-consistency-hark-20261019/ast_count.py .
# 旧守卫逐行隔离对比（Linux 上旧脚本整体对照本就因平台失败）
python reports/hark-history-consistency-hark-20261019/jh020_rows.py
```

证据目录 `reports/hark-history-consistency-hark-20261019/`：两侧探针 results.json、新守卫 JSON（工作树与提交后）、旧守卫整体与逐行输出（基线/候选）、六份完整套件原始日志（基线/候选/提交后 × 有无代理）及失败 id 清单。日志中的本机路径已替换为 BASELINE / CANDIDATE。
