# JH-004 实施记录（未验证）— 2026-10-13

分支 `n1-artifact-entry`，基线 80979f3 + 文档检查点 9a7c5dd，
工作树 `D:/JH-Agent OS/data/worktrees/n1-artifact-entry`。
本会话无 Shell：实现完成，验证与提交均未执行。

## 改动（A–E）

- A：新增 `src/production_executor.py::NoToolExecutor`（所有工具请求
  denied，无执行、无副作用，结果符合 tool_result.v1.json）；生产装配
  默认改用它，fixture 分支保留 Mock；未知 execution_mode 在模型调用前拒绝。
- B：`resolve_trusted_evaluator` 锁定 `records_transform_v1`：input_data
  仅允许 `records` 键、非空 `{region: str, sales: number}` 列表、恰好两项
  验收条件；期望由可信排序规则独立计算；其余形状一律 TrustedAssemblyError。
- C：submit 盖章 `metadata._trusted{source,template_id,execution_mode}`，
  覆盖载荷伪造；resume/approve 比对来源与模板，串换或缺章拒绝；resume 加
  owner/在途/未确认副作用/未知用量门；recover 跳过被占任务（`skipped_owner_held`），
  不再无条件清 owner。无 Schema 变更（metadata 允许附加键），协议兼容。
- D：摘要加 `source_binding` 与 `delivery_scope`（`generated_artifact` 布尔、
  `target_applied` 恒 false）；查询命令与只读语义不变。
- E：新增 `.agents/skills/jh-task/SKILL.md`（位置经官方文档核对：
  仓库技能位于 `.agents/skills`，元数据渐进披露）。宿主发现/调用未验证。

## 新增测试

`tests/test_jh004_artifact_entry.py`（20 项，均离线；含真实四进程 CLI 交接，
fixture 模式）：NoTool 拒绝与失败链、模板端到端成功、非法形状预拒、
来源盖章与双向串换拒绝、owner/在途/未知门、recover 不抢占、只读边界、
四进程交接。既有测试文件未动。

## 待有 Shell 会话执行（按序）

```powershell
cd D:\JH-Agent OS\data\worktrees\n1-artifact-entry
python -X utf8 -B -m unittest discover -s tests
python -X utf8 -B -m unittest discover -s tests -k jh004
```

基线 145 项（144 通过 + 1 跳过，已独立核对）加本批 20 项，期望全量
165 项（164 通过 + 1 跳过，0 失败/错误）。新测试类名以 Test 打头且文件名
含 jh004，`-k jh004` 可聚焦。

全绿后：真实四进程已在测试内覆盖（fixture）；再做真实双模型取证与
实际 Codex Skill 发现/调用（缺配置如实停止）；独立新报告；按明确范围
提交（src/production_executor.py、src/cli.py、src/storage.py、
tests/test_jh004_artifact_entry.py、.agents/skills/jh-task/SKILL.md、
本报告及状态文档），提交后重跑聚焦回归，交架构师复核。

## 未验证事项

全部实现与测试均未运行；真实模型、真实 Skill 宿主、C 盘绑定等外部条件
未核验。门禁保持关闭，不宣布结项。
