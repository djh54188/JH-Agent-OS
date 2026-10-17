# JH-004 两项遗留整改记录（未运行，未提交）

日期：2026-10-14。分支 n1-artifact-entry，从 WIP 检查点 58d6058 继续。
依据 [复核](../reports/jh004-rereview-20261014.md) 及
[精简交接](../docs/handoffs/jh004-r02-followup-20261014.md)。
本会话 Shell 不可用：源码与测试已改完，未运行、未提交。

## 改动文件

- `src/cli.py`：`_production_taint_blocker` 改写为以持久化 completed
  attempt 为基准的全覆盖校验（函数名与签名不变，探针可直接复用）。
- `tests/test_jh004_artifact_entry.py`：新增 `TestHistoricalSourceCoverage`
  5 项；补齐旧污染测试的被覆盖 attempt；P2 措辞对齐一处。
- `STATUS.md` / `TODO.md` / 本文件：状态同步。

未动：探针与旧证据原文件、其余源码、旧测试期望、预算与来源数据。

## P1：历史来源覆盖

每条 completed 调用必须有本任务、本 attempt_id、正确 actor role 的来源
事件，且 `is_mock` 严格为 `False`；缺失、错关联、矛盾、缺标记一律在装配
前阻断。零调用任务与全覆盖历史不受影响；旧历史不补造、不删 attempt、
不重置额度，无法确认即要求重新提交或人工核对。

回归镜像探针 `historical_missing_source`：缺失、错角色、矛盾、缺标记四种
经 CLI 全部拒绝，且断言无适配器加载、无新增 attempt、无状态与预算变化；
全覆盖在 helper 层通过。

## P2：Skill 测试措辞

测试标记改为与技能正文一致的 `contains THIS skill file`，约束与预检不变，
不删测、不跳过。

## 变异覆盖说明（待补跑会话执行）

- 删全覆盖循环（退回仅扫坏事件）→ 缺失/错角色测试失败。
- 放宽 `is not False` 为真值判断 → 缺标记测试失败。
- 去掉 resume 污染挂钩 → 全部四项 CLI 测试失败。
- 异常与装配错误不计检出。

## 待补跑

全量回归（期望 185 项：184 通过、0 失败、0 错误、1 跳过；旧跳过保留）、适配探针复测
（`historical_missing_source` 不得再 succeeded）、定向变异、明确范围提交。
通过后才做真实双模型/宿主取证。门禁保持关闭，不宣布结项。
