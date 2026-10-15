# JH-005 R1—R4 整改记录（未运行，未提交）

日期：2026-10-15。分支 n1-artifact-entry，从 WIP 检查点 bfc7839 继续，
未重置。依据复核报告与四项整改交接；只改本批文档、夹具、核对脚本及状态文档。
本会话 Shell 不可用：全部改动未运行、未封存、未提交。

## 改动文件

- `docs/host-delivery-contract.md`：R1 两步交付、等待标记、准备/完成接口提案、
  交付绑定清单、expected_after 字节规则、成功门与非保证。
- `docs/n2-acceptance-spec.md`：R1 代码任务两步交付与绑定要求；R2 材料化规则。
- `tests/fixtures/n2/code_sortfix/task.json`：R2 说明键移入 metadata。
- `tests/fixtures/n2/code_sortfix/expected/widget.py`：R4 改为基线逐字节去参。
- `tests/fixtures/n2/code_sortfix/checks.json`：R4 新增 post-apply 检查模板。
- `tests/fixtures/n2/code_sortfix/scenarios.json`：normal-delivery 加 actual_after 断言。
- `tests/fixtures/n2/data_rollup/task.json`：R2 填入六条真实记录。
- `scripts/verify_jh005_fixtures.py`：R2 材料化/Schema/装配/输入一致检查；
  R3 严格封存、闭合清单、精确集合、驱动器拒绝、布尔排除、模板规则、
  字节契约、异常结构化。
- `scripts/verify_jh005_mutations.py`（新增）：R3 独立变异脚本，密封对照 +
  八变体逐项断言对应条件，崩溃不计检出。
- `STATUS.md` / `TODO.md` / `CHANGELOG.md`：状态同步。

未动：生产 src/、Schema、适配器、权限、Skill、旧测试、独立证据原文件。

## 与复核反例的对应

- R1 反例（未审核赋值仍全过）：契约现要求 actual_after 等于 expected_after
  且由控制层核对实际字节；行为检查通过不再充分。
- 八变体：data_input_mismatch→task-input-match；manifest_empty/omits→
  manifest-inventory；absolute_target→target_files；scenarios/checks_empty→
  对应 complete 检查；boolean_sales→input-shape；manifest_string_seal→
  seal-boolean。密封对照应退出 0。
- R4 字节式：`expected == baseline.replace(b", reverse=True", b"")` 且
  基线恰含一次该片段；post-apply 模板禁止指向 expected/ 且必须 exit 0。

## 待补跑（有执行通道的会话）

1. `python scripts/verify_jh005_mutations.py`（期望对照过、八项断言检出、零异常）。
2. `python scripts/verify_jh005_fixtures.py`（期望 UNSEALED 报告哈希）。
3. 全量单测确认总数与结果不变（本批不新增单测）。
4. 审阅字节后填入两份 manifest 哈希置 sealed，重跑 1—2 至绿。
5. 适配探针复测（注意：暂存拷贝跑装配检查需含 src/，否则核对器会明确报错而非静默跳过）。
6. 明确范围提交并做提交后复核。门禁保持关闭，不宣布结项。
