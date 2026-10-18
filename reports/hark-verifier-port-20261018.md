# Hark 验证器补丁的 Windows 本地移植与验证 · 2026-10-18（交付架构师独立复核）

**结论先行**：把 Hark `fe0476d` 的三个文件**精准移植**到本地 HEAD `1288d8a` 并完成 **Windows 本地验证**。
移植前后 **MUTATIONS 的 AST 逐字节一致**（未删改任何变异）；12/19 条锚点在本地源码中全部唯一；
新验证器回归测试 13 项通过；两个验证器的完整变异清单在受控短路径下跑完，
**基线/恒等对照全绿、无 harness 错误、无平台阻塞**，逐项结果见下（**exit 1 是诊断结论，不是失败**）；
两向元变异在本地复现并**把行为打红**。
**本报告不恢复旧的 19/19、12/12 验收结论，也不宣称多层防护通过**——验证器仍是**诊断工具**。

- 工作树：`D:\JH-Agent OS\data\worktrees\n1-artifact-entry`（分支 `n1-artifact-entry`）
- 移植基线：`1288d8a7cc19a218d6d1af094a884e62b3ed532b`
- 来源提交：`fe0476d5807d6d562a1c290fe1b14623e53d1a46`（https://github.com/djh54188/JH-Agent-OS/commit/fe0476d5807d6d562a1c290fe1b14623e53d1a46）
- 本地候选：`data/hark-v1v2-r4-review-20261018/candidate/`（评审下载副本，**未修改**）
- 证据目录：`data/hark-port-20261018/`（原始日志见 `logs/`）

## 1. 环境

| 项 | 值 |
|---|---|
| 平台 | `win32`（Windows 10.0.26200） |
| Python | 3.14.7 |
| 受控短临时根 | `D:\JH-Agent OS\data\h4tmp`（`JH_MUTATION_TMP`/`TEMP`/`TMP` 显式指定；长度 25） |
| 子进程最坏路径长度 | 约 105 字符（远低于 260，未使用 ACL/继承技巧） |
| 运行后临时目录残留 | 0 个 |
| 两个验证器 stderr | 0 字节 |

## 2. 移植的文件（仅此三个）

| 文件 | 状态 | 移植后 sha256 |
|---|---|---|
| `scripts/verify_phase4_guards.py` | 覆盖为候选版 | `f367cdc469fae683a179f72df79ed09c259c59cafd7df3aa7e7a21ba3bca8bff` |
| `scripts/verify_jh006_guards.py` | 覆盖为候选版 | `3634c4c230925824101ab3c3867c35f139dd9659e0732e5091445da0aff0dad8` |
| `tests/test_verifier_mutation_harness.py` | **新增** | `b17e06ba8cf24117ac846066716f9c87b9c4b3fe91ba6a48026444fa26344390` |

三个文件与候选**逐字节一致**（sha256 与候选相同）。**未**整分支合并、**未**覆盖旧 main 快照、**未**搬入其他云端改动。

## 3. MUTATIONS AST 核对（裁决要求）

对每个验证器取顶层 `MUTATIONS` 赋值节点的 `ast.dump(..., include_attributes=False)` 并做 sha256：

| 验证器 | 移植前本地 | 候选 | 移植后本地 | 结论 |
|---|---|---|---|---|
| `verify_phase4_guards` | `3cc35fe6b581a8d3` | `3cc35fe6b581a8d3` | `3cc35fe6b581a8d3` | **三者一致，未变** |
| `verify_jh006_guards` | `f6a6ab4cd092472d` | `f6a6ab4cd092472d` | `f6a6ab4cd092472d` | **三者一致，未变** |

变异数量与锚点唯一性（对**本地当前源码**逐条统计）：

- `verify_phase4_guards`：**12** 条，锚点全部唯一（0 条不唯一）。
- `verify_jh006_guards`：**19** 条，锚点全部唯一（0 条不唯一）。

**与本地清单无差异**；未擅自删改任何变异。移植前的两份本地脚本已留档于
`data/hark-port-20261018/LOCAL-pre-verify_{phase4,jh006}_guards.py`。

## 4. 运行结果（命令与退出码）

```bash
export JH_MUTATION_TMP="D:\JH-Agent OS\data\h4tmp"; export TEMP="$JH_MUTATION_TMP"; export TMP="$TEMP"
python -m unittest discover -s tests -p "test_verifier_mutation_harness.py"   # exit 0
python scripts/verify_phase4_guards.py                                       # exit 1
python scripts/verify_jh006_guards.py                                        # exit 1
python data/hark-port-20261018/meta_mutation_probe.py                        # exit 0
python -m unittest discover -s tests -p "test_*.py"                          # exit 0
```

### 4.1 新验证器回归测试

`tests/test_verifier_mutation_harness.py`：**13 项全部通过**（`Ran 13 tests ... OK`，约 106 秒）。
覆盖 V-1（异常类身份）与 V-2（patch 目标）形状、措辞-only 不得计检出、红基线分类、
合成仓库上每个形状的分类，以及"harness 绝不改动源检出"。

### 4.2 `verify_phase4_guards` 完整变异清单（逐项，`logs/verify_phase4_guards.json`）

**12 条：5 detected / 3 survived / 3 message_only / 1 undetermined → verdict `fail`，exit 1。**
每条的 baseline 与 identity 均为 `pass`（无 harness 错误、无平台阻塞）。

| 变异 | baseline | identity | mutant | 判定 |
|---|---|---|---|---|
| `workspace_containment_removed` | pass | pass | pass | **survived** |
| `junction_ancestor_check_removed` | pass | pass | message_only | **not_detected_message_only_failure** |
| `apply_target_version_check_removed` | pass | pass | pass | **survived** |
| `apply_staged_hash_check_removed` | pass | pass | message_only | **not_detected_message_only_failure** |
| `apply_without_pass_verdict` | pass | pass | behavioral | detected |
| `arch01_staging_dir_reparse_removed` | pass | pass | pass | **survived** |
| `arch03_locked_handle_version_check_removed` | pass | pass | behavioral | detected |
| `arch05_staged_snapshot_binding_removed` | pass | pass | behavioral | detected |
| `workspace_ctor_reparse_removed` | pass | pass | behavioral | detected |
| `staged_unique_name_removed` | pass | pass | undetermined | **not_detected_undetermined_failure** |
| `recovered_applied_hash_check_removed` | pass | pass | message_only | **not_detected_message_only_failure** |
| `acceptance_current_turn_judgment_removed` | pass | pass | behavioral | detected |

### 4.3 `verify_jh006_guards` 完整变异清单（逐项，`logs/verify_jh006_guards.json`）

**19 条：18 detected / 1 undetermined → verdict `fail`，exit 1。** 每条的 baseline 与 identity 均为 `pass`。

18 条 `behavioral` 检出：`payload_delivery_marker`、`cli_delivery_marker`、`unregistered_host_record`、
`raw_output_digest`、`strict_uncertainty_bool`、`fixed_check_script`、`sealed_check_command`、`prepare_owner`、
`pending_approval`、`interrupted_tool`、`prepare_transaction`、`authorized_workspace_scope`、`no_model_tools`、
`generation_baseline_context`、`review_deferred_scope`、`fresh_cancel_state`、`no_extra_model_turn`、`final_deadline_gate`。

1 条未检出：`prepared_version_field` → **not_detected_undetermined_failure**（`baseline=pass, identity=pass, mutant=undetermined`）
——与独立评审"其单层证明仍不完整、保守判为 undetermined"一致。

### 4.4 元变异（定向反事实，仅内存注入，不改源码）

`data/hark-port-20261018/meta_mutation_probe.py`（结果 `logs/meta_mutation.json`）：**6/6 用例在注入后由未检出变为 `detected`**。

| 验证器 | 用例 | 注入 | 注入前 | 注入后 | 行为回归 |
|---|---|---|---|---|---|
| phase4 | `self_attribute` | 恢复旧的无条件 self 规则 | undetermined | **detected** | 是 |
| phase4 | `self_dict` | 同上 | undetermined | **detected** | 是 |
| phase4 | `bool_wrapped_text` | 关闭 `text_derived` 过滤 | undetermined | **detected** | 是 |
| jh006 | `self_attribute` | 恢复旧的无条件 self 规则 | undetermined | **detected** | 是 |
| jh006 | `self_dict` | 同上 | undetermined | **detected** | 是 |
| jh006 | `bool_wrapped_text` | 关闭 `text_derived` 过滤 | undetermined | **detected** | 是 |

即：R4 的 self 保守规则与 `text_derived` 过滤在本机**确实承担行为权重**——去掉任一条，误报（false positive）即回归。

## 5. 完整业务套件与独立计数

- `python -m unittest discover -s tests -p "test_*.py"` → **`Ran 590 tests ... OK (skipped=1)`**，exit 0（约 126 秒）。
- **独立计数（AST）**：遍历各 `tests/test_*.py` 的 `ClassDef` 内以 `test` 开头的方法，得 **590**，与运行计数一致。
- 注意：直接 `grep 'def test_'` 得 618，**偏高 28**，因为新测试文件的字符串常量里含合成 `def test_` 源码；
  以 AST 计数为准。这一差异本身记录在案，避免把 grep 数字当作测试数。

## 6. 诊断发现 vs 移植/运行错误（区分）

| 类别 | 内容 |
|---|---|
| **诊断发现**（验证器如实报告的能力边界） | phase4：3 survived、3 message_only、1 undetermined；jh006：1 undetermined（`prepared_version_field`）。**这些是"证明缺口"，不是移植或运行错误。** |
| **移植错误** | 无：三个文件 sha256 与候选一致；MUTATIONS AST 与移植前一致。 |
| **运行错误 / harness 错误** | 无：所有行的 baseline、identity 均为 `pass`；无 `harness_error` 行；两个验证器 stderr 为空。 |
| **平台阻塞** | 无：无 `platform_blocked` 行；未用 ACL 或路径长度伪装。 |

**exit 1 的正确含义**：`summarize()` 只在"全部 detected"时返回 0；否则若存在未被平台证据解释的缺口即 1。
本批两个验证器均返回 1，正是对 `survived`/`message_only`/`undetermined` 缺口的**如实表达**，
**不得为凑全绿而修改规则**（本批未改任何判定规则）。

## 7. 与旧结论的关系

- 旧本地验证器（内存 `exec` 版）报告的 **`verify_phase4_guards` 12/12 `passed:true`** 与
  **`verify_jh006_guards` 19/19**，其"检出"包含非行为性失败与身份/补丁错位导致的假阳性
  （V-1/V-2）。**本报告不恢复这些结论**，仅以运行记录看待。
- 采集守卫的 33 项 / 32 检出 / 1 冗余 / 0 异常等旧数字，同样是**运行记录**，有效性待 Hark 修复与独立复核。
- **不宣称**任何多层防护通过；真实宿主来源登记、N2/P1、JH-020 整体闭环仍**未完成**。

## 8. 保持的边界

**未改动**业务源码、既有业务测试、封存夹具、权限与生产门；**未**调用真实模型、**未**发送指定审查会话、
**未**接 GUI 执行、**未**实现 continue/cancel；**未**推送云端、**未**合并 PR #1。
本批为**独立提交**，交架构师独立复核。

## 9. 原始日志路径

| 文件 | 内容 |
|---|---|
| `data/hark-port-20261018/logs/verify_phase4_guards.json` | phase4 逐项结果与计数（exit 1） |
| `data/hark-port-20261018/logs/verify_jh006_guards.json` | jh006 逐项结果与计数（exit 1） |
| `data/hark-port-20261018/logs/verify_phase4_guards.stderr.txt` / `verify_jh006_guards.stderr.txt` | 空（0 字节） |
| `data/hark-port-20261018/logs/meta_mutation.json` | 元变异 6 用例结果 |
| `data/hark-port-20261018/logs/business-suite.txt` | 全量套件输出（590 项） |
| `data/hark-port-20261018/LOCAL-pre-verify_*.py` | 移植前本地脚本留档 |
| `data/hark-port-20261018/pre-port-*.bak` | 候选字节留档（sha256 见 §2） |
