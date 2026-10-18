# JH-020 首批（C-2/C-3）实施与验证记录 · 2026-10-17（待独立验收）

> **本文件部分结论已被 2026-10-18 整改更正。** 见 [jh020-rectification-20261018.md](jh020-rectification-20261018.md)：
> ① `reconcile-delivery` 已收缩为**仅记录观察**，**不再清除**未确认副作用标记（本文件 §1.2/§4 的“清除标记”“二次对账被拒”已过时）；
> ② 采集守卫计数应为 **33 项 / 32 检出 / 1 冗余 / 0 异常**（本文件 §5 的旧计数作废）；
> ③ 旧 JH-006/Phase4/采集验证器成绩标注为**运行记录**，其有效性仍待 Hark 修复与独立复核。

**结论先行**：本批完成架构师此前限定的 **C-2、C-3** 两项控制层加固，**离线验证通过**；
**真实路径未通过**（本切片不含真实派发与指定会话审查），故 **JH-020 本批闭环未通过**，
交当前 Codex 架构师独立验收。`production complete` 与 `FileToolExecutor` 保持关闭。

- 工作树：`D:\JH-Agent OS\data\worktrees\n1-artifact-entry`（分支 `n1-artifact-entry`）
- 基线：`6052cf31e056659bdc37a768e07c8123c5c8ca40`（开工核对 HEAD/status 一致；仅 `docs/review-backlog/` 未跟踪）
- 分工：**C-2/C-3** 由执行方（ZCode）负责；**V-1/V-2 验证器修复**交 Hark（本批**未改动**验证器脚本）；原树 WIP 与 Hark 三份归档保持原样。

## 1. 交付内容

### 1.1 C-3 · `complete` 校验已登记原始输出满足封存断言

**缺陷（修复前，Hark R2 探针实测）**：`complete_code_delivery` 只比对回执声明的 `output_digest` 与已登记原始输出的摘要，
**从不检查原始输出文本本身**。于是一份 `case 0: FAIL\nFAIL` 的输出（摘要自洽）可让任务 `succeeded`。

**修复**：从**同一份封存 `checks.json`** 读出具名模板条目的 `expect_stdout_contains`，在 `complete` 时校验**已登记原始输出**是否包含该断言。
控制层**不重跑宿主命令**——只读已登记输出与封存期望。

- `src/code_delivery.py`：新增 `sealed_post_apply_expectation()`、`registered_host_raw_output()`、`registered_output_satisfies_sealing()`。
- `src/orchestrator.py`：`_complete_code_delivery_locked` 在宿主记录核对之后、退出码检查之前，增加封存断言校验；不满足即 `_complete_uncertain`（`needs_attention`）。
- 只读现有能力：`fixture_host_evidence_matches`（既有摘要核对）**未改动**，以保持既有变异锚点稳定。

### 1.2 C-2 · 宿主未确认副作用的持久保存与终态后对账入口

**缺陷（修复前）**：任务在 `reviewing` 有 `waiting_host` 交付（宿主可能已应用）时，若因取消/超时/不确定离开
`reviewing`，**未被持久标记为未确认副作用**，也无对账入口。Hark R2 探针实测 `expired`/`cancelled` 两例
`has_unconfirmed_side_effect=false`。

**修复**：

1. **持久保存**（`src/storage.py::transition_task_state`）：当目标态属于 `{failed, cancelled, needs_attention}`
   且任务带 `waiting_host` 交付时，在同一事务内写入 `has_unconfirmed_side_effect=1`、
   在 `_delivery` 上记 `unconfirmed_host_effect/unconfirmed_reason`，并追加一条 `host_effect_unconfirmed` 审计事件。
   集中在此处后，CLI `cancel`、预算超时 `failed`、`_complete_uncertain` 三条路径**自动覆盖**，无需重写状态机。
2. **对账入口**（`src/code_delivery.py::reconcile_host_effect`、`host_effect_observation`；CLI `reconcile-delivery`）：
   ~~只**读目标字节**并与 `baseline`/`expected_after` 比对，得出 `matches_expected_after|matches_baseline|diverged|unreadable`，
   记 `host_effect_reconciled` 事件、写回 `reconciled`、清除持久标记。~~ **⚠ 已被 2026-10-18 整改更正**：
   现为**仅记录观察**——读目标字节得出 `matches_expected_after|matches_baseline|diverged|unreadable`，记 `host_effect_observed` 事件、
   写回 `reconciled`（= 最新观察记录），**不清除持久标记**；`unreadable/diverged` 保持未解决且允许再次观察。
   见 [整改报告](jh020-rectification-20261018.md)。
3. `src/cli.py::build_task_summary` 的 `delivery_scope` 增加 `host_effect_unconfirmed/unconfirmed_reason/reconciled` 事实字段，供 GUI 展示。

**兼容性**：仅**加法**——复用既有列 `has_unconfirmed_side_effect`（无 Schema 迁移），新增 storage 只读枚举、`_delivery` 字段、CLI 子命令与审计事件类型；不改现有成功判定与状态机。

## 2. 改动清单

| 文件 | 改动 |
|---|---|
| `src/code_delivery.py` | +C-3 三个函数；+C-2 `host_effect_observation` / `reconcile_host_effect` / `_utc_now_iso` / 事件常量 |
| `src/orchestrator.py` | `_complete_code_delivery_locked` 增加封存断言校验（+导入） |
| `src/storage.py` | `transition_task_state` 持久化未确认副作用标记与事件；+`set_unconfirmed_side_effect`；+模块常量 |
| `src/cli.py` | +`reconcile-delivery` 子命令；`build_task_summary` 增交付事实字段 |
| `tests/test_jh020_delivery_reconcile.py` | 新增 19 项正反控 |
| `scripts/verify_jh020_delivery_guards.py` | 新增 C-2/C-3 定向变异验证器（5 变异） |
| `reports/jh020-first-batch-20261017.md` | 本报告 |

## 3. 复现命令

```bash
cd "D:/JH-Agent OS/data/worktrees/n1-artifact-entry"
python -m unittest discover -s tests -p "test_*.py"          # 全量
python -m unittest discover -s tests -p "test_jh020_delivery_reconcile.py" -v
python scripts/verify_jh020_delivery_guards.py               # C-2/C-3 定向变异
python scripts/verify_jh006_guards.py                        # 既有验证器须仍通过
python data/jh020-c2c3-20261017/probe_c2c3.py                # 独立行为探针
```

## 4. 能力 / 证据 / 限制对照表

| 项 | 能力 | 证据 | 限制 |
|---|---|---|---|
| C-3 封存断言 | `complete` 拒绝不满足封存 stdout 断言的已登记输出 | 探针 `bad_stdout`→`needs_attention`；测试 5 项；变异 `sealed_output_assertion_disabled`/`sealed_marker_source_replaced` 检出 | 仅覆盖封存 `code_sortfix` 模板；**未**接入真实宿主 |
| C-2 持久标记 | 离开 `reviewing` 时持久化未确认副作用 | 探针 `expired`/`cancelled` 标记为真；测试 4 项；变异 `unconfirmed_target_states_disabled` 检出 | 单进程写入边界；`waiting_host` 是判定前提 |
| C-2 对账入口 | ~~读字节→比对→记事件→清标记~~ → **已更正**：读字节→比对→记观察，**不清标记、不抢 owner** | 探针 `after_reconcile` 状态不变；整改后反例见 [整改报告](jh020-rectification-20261018.md) | ~~`unreadable/diverged` 只如实记录~~ → 整改后保持未解决且可再次观察 |
| 既有门禁 | 未回归 | `verify_jh006_guards` 19/19、`verify_jh006_capture_guards`、`verify_jh006_host_boundary`、`verify_phase4_guards` 12/12、`verify_jh005_fixtures`/`mutations`、JH-018 契约、JH-019 前端全量随套件通过 | — |
| 真实路径 | **未做** | 指定会话仅只读核对 | **未派发、未取得真实审查输出——本批闭环未通过** |

## 5. 验证计数（以实际运行为准）

> **⚠ 本节计数与定性已被 2026-10-18 整改更正（R3）**：
> ① 采集守卫应为 **33 项 / 32 检出 / 1 冗余 / 0 异常**（原写“12/12 + 0 冗余”有误）；
> ② 旧 JH-006 / Phase4 / 采集验证器成绩一律标注为**运行记录**——它们是本机当时的运行输出，
> **不等于验证有效**；其有效性仍待 **Hark 修复验证器缺陷**与**独立复核**。本批**未修改** Hark 负责的脚本。
> ③ 修订后的口径见 [整改报告](jh020-rectification-20261018.md)。

- 全量 **511 项：510 通过 / 1 既有跳过**（`test_phase2_hardening.py:152` 平台跳过）；本批当时新增 19 项全部通过。
- 本批定向变异 **5/5 检出**（`errors=0`，断言失败计数）；5 项对照基线全绿。（整改后为 7 变异 / 7 检出。）
- 旧变异验证器的**运行记录**（有效性待 Hark 修复与独立复核）：JH-006 19/19、采集 **33 项 32 检出 / 1 冗余 / 0 异常**、Phase4 12/12。
- 独立探针同输入重跑两次，除时间戳外逐字段一致。
- 提交前全量、提交后全量各一轮（见 `data/jh020-c2c3-20261017/`）。

## 6. 指定审查会话路径（只读核对；**未发送**）

- 目标：`01a14a25-7b28-7a53-825e-a340bebb87fb`，`thread_name=接任架构师并复核 JH-Agent OS`，
  `cwd=D:\JH-Agent OS`、`originator=Codex Desktop`、`cli_version=0.163.0-alpha.10.2`；rollout 记录存在。
- **未确认可安全接续，且真实派发不在本批本切片内** → 按规则**不发送**、不改其模型/权限/全局配置、不 fork 替代会话。
- 因此：`本批 JH-020 闭环未通过`。不得以 Mock、人工复制或另建审查会话补成成功。

## 7. 边界

不做动态规划器、并行调度、通用 Runtime、流程编辑器、长期后台服务；不启动其余待办或 Hark 整改；
未补做 JH-006 真实来源登记；未宣布 N2/P1 或完整 MVP 完成；不新建命令执行器、不自行复跑宿主检查。
`production complete`、`FileToolExecutor` 继续关闭。**最终验收交当前 Codex 架构师**。

## 8. 与 V-1/V-2 / Hark 的关系（避免重复修改）

本批**未改动** `scripts/verify_jh006_guards.py`、`scripts/verify_phase4_guards.py` 等验证器（属 V-1/V-2，交 Hark）；
C-3 的改动特意保留既有变异锚点（`fixture_host_evidence_matches` 原文不动），并在其旁新增独立读取函数。Hark 改动经架构师复核后，再将认可的补丁适配到最新本地 HEAD（不做远端旧快照覆盖或整树合并）。
