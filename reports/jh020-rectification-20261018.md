# JH-020 首批 5f604fba 限定整改（R1–R3）· 2026-10-18（待独立验收）

**结论先行**：按独立反例 `data/jh020-review-20261018/` 对 `5f604fba` 做**限定整改**，**未重做已通过的 C-3**。
R1（对账入口收缩为观察）、R2（终态未解决副作用可发现）、R3（采集计数与定性更正）已完成，**离线验证通过**；
**真实协作闭环仍未完成**（本切片不含真实派发与指定会话审查），交当前 Codex 架构师独立验收。
`production complete` 与 `FileToolExecutor` 保持关闭；JH-020 能力探测可继续。

- 工作树：`D:\JH-Agent OS\data\worktrees\n1-artifact-entry`（分支 `n1-artifact-entry`）
- 整改基线：`5f604fba09102f8dfe9f39b3d7356d3b5f80bd49`（父 = `6052cf31…`）；开工核对 HEAD/status 一致，仅 `docs/review-backlog/` 未跟踪
- 独立反例：`data/jh020-review-20261018/probe.py`、`results.json`（**只读引用，未修改**）
- 未改动 Hark 负责的脚本（`verify_jh006_guards.py`、`verify_jh006_capture_guards.py`、`verify_phase4_guards.py` 等）；原树 WIP 与三份归档保持原样。

## 1. 独立反例指出的缺陷（整改前，5f604fba）

`data/jh020-review-20261018/results.json` 记录：

| 反例 | 现象 | 问题 |
|---|---|---|
| `cancel_unreadable` | `observation=unreadable`，`after_unconfirmed=false` | 一次读取失败却**清除了**标记 |
| `cancel_diverged` | `observation=diverged`，`after_unconfirmed=false` | 目标已分叉却**清除了**标记 |
| `cancel_tool_inflight` | 仍有 in_flight 副作用工具，`after_unconfirmed=false` | 其他未决副作用**被一并解除** |
| `cancel_owner_held` | `owner_after=independent_active_owner`，`after_unconfirmed=false` | 在他人 owner 下**仍清除**标记 |
| 全部 | `recover_mentions_unresolved_task=false` | failed/cancelled 的未解决副作用**不可发现** |
| 全部 | `second_reconcile_refused=true` | 只允许观察一次 |

## 2. R1 · reconnect-delivery 收缩为“仅记录观察”

**改动**（`src/code_delivery.py::reconcile_host_effect`、`src/cli.py`）：

- 入口只**读目标字节**并**追加**一条观察记录（`_delivery.reconciled` = 最新观察，`_delivery.observations` = 追加式历史），
  记 `host_effect_observed` 事件（`marker_released: false`）。
- **不再清除** `has_unconfirmed_side_effect`；**不再**调用任何清除方法（已删除上一次新增的 `set_unconfirmed_side_effect`，避免死代码）。
- `unreadable/diverged/matches_*` 一律**保持未解决**；重复观察**允许**（每次追加一条、记一条事件）。
- **不抢 owner、不覆盖其他 in_flight 副作用**：入口不 claim、不触碰 owner 与其他工具记录。
- CLI `reconcile-delivery` 说明与回包同步为“observation only … does NOT release the marker”。
- **真正解除标记**留待**明确、绑定来源的处置条件**——本批不实现，不扩建宿主 Runtime。

**更正的错误预期与变异**：删除“`diverged` 也清标记”“`matches_expected_after` 才清标记”“二次对账被拒”等断言；
变异 `reconcile_clear_marker_disabled` 替换为 `reconcile_releases_marker_again`（重放旧缺陷须被检出）。

## 3. R2 · 终态未解决副作用的可发现性

**改动**：

- `src/storage.py::TaskStorage.list_unresolved_host_effects()`：**只读** SELECT，列出 `has_unconfirmed_side_effect=1` 的任务
  （含 `failed/cancelled` 等终态），返回 `task_id/status/is_terminal/delivery_id/delivery_state/unconfirmed_reason/reconciled/updated_at`。
- `recover_interrupted_tasks()` 末尾追加**只读**行：终态且未解决的任务以 `reason="host_side_effect_unresolved"`、
  `auto_replay_allowed=false`、`recovered_state=<原终态>` 返回——**保持原终态、禁止自动重放、不改任何状态**。
- 新增 CLI `pending-reconciliation`（**只读命令**）枚举 `list_unresolved_host_effects()`。
- **查询/枚举不顺带清标记、不抢 owner、不改状态**——`list_*` 为纯 SELECT，`recover` 对该批任务零写入。

## 4. R3 · 计数与定性更正

- 采集守卫按原始日志更正为 **33 项、32 检出、1 冗余、0 异常**（`data/jh020-c2c3-20261017/{pre,post}commit/verify_jh006_capture_guards.json`）。
- 旧 JH-006 / Phase4 / 采集验证器成绩**一律标注为“运行记录”**，其**有效性待 Hark 修复与独立复核**——不再表述为“保持通过”。
- **未修改** Hark 负责的脚本。

## 5. 保留项（未改动）

- **C-3 正反控**：正常 PASS → `succeeded`；`FAIL` 文本（摘要自洽）→ `needs_attention`；缺封存模板断言 → 拒绝；登记输出与回执**摘要不匹配** → `needs_attention`；`registered_output_satisfies_sealing` 真值表。
- **C-2 持久化**：离开 `reviewing` 时标记与 `host_effect_unconfirmed` 事件**同事务**提交（新增原子性反例：注入审计失败 → 标记与状态一并回滚）。
- C-2 的取消/超时/不确定三路径持久化与“无 waiting 交付不误报”负例。

## 6. 改动清单

| 文件 | 改动 |
|---|---|
| `src/code_delivery.py` | `reconcile_host_effect` 改观察-only（不清标记/不抢 owner/可重复）；事件改 `host_effect_observed` |
| `src/storage.py` | 删 `set_unconfirmed_side_effect`；新增只读 `list_unresolved_host_effects`；`recover` 追加终态未解决只读行 |
| `src/cli.py` | `reconcile-delivery` 语义/文案更正；新增只读 `pending-reconciliation`；摘要字段改 `reconciled/observation_count` |
| `tests/test_jh020_delivery_reconcile.py` | 重写为 28 项（观察-only、重复观察、owner/在途不侵占、R2 可发现性、原子性、C-3 四类正反控） |
| `scripts/verify_jh020_delivery_guards.py` | 7 变异（含“重放清标记”“抢占 owner”“隐藏待对账”三个新守卫） |
| `reports/jh020-first-batch-20261017.md` | 加更正横幅并修正过时结论/计数 |
| `reports/jh020-rectification-20261018.md` | 本报告 |

## 7. 验证（提交前 / 提交后各一轮）

- 全量 **520 项 519 通过 / 1 既有跳过**；本批整改文件新增/改写后共 28 项全部通过。
- 本批定向变异 **7/7 检出**（`errors=0`，均断言失败）；7 项对照基线全绿。
- **独立反例复跑**（只读副本，未覆盖原文件）：`cancel_expected/unreadable/diverged/tool_inflight/owner_held`
  五例均 `after_unconfirmed=true`、`persisted_unconfirmed=true`、`host_effect_unconfirmed=true`、
  `recover_mentions_unresolved_task=true`、`second_reconcile_refused=false`；`owner_after` 与 `inflight_side_effect_tools` 均未被改动。
- 旧验证器**运行记录**（有效性待 Hark 修复与独立复核）：JH-006 19/19、采集 33/32/1/0、Phase4 12/12、JH-005 夹具/变体。
- 证据目录：`data/jh020-rectify-20261018/`（含反例副本输出、变异结果、全量日志）。

## 8. 复现命令

```bash
cd "D:/JH-Agent OS/data/worktrees/n1-artifact-entry"
python -m unittest discover -s tests -p "test_*.py"
python -m unittest discover -s tests -p "test_jh020_delivery_reconcile.py" -v
python scripts/verify_jh020_delivery_guards.py
python data/jh020-rectify-20261018/review_probe_copy.py   # 独立反例副本（不覆盖原文件）
```

## 9. 边界

不做动态规划器、并行调度、通用 Runtime、流程编辑器、长期后台服务，不扩建宿主 Runtime；
不重做 C-3、不改 Hark 负责脚本、不启动其余待办。`production complete`、`FileToolExecutor` 继续关闭；
**本整改不代表真实协作闭环完成**，最终验收交当前 Codex 架构师。
