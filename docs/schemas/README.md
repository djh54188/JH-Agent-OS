# JH-Agent OS 核心通信协议 Schema（v1.0.0）

本目录包含 JH-Agent OS 核心控制层与多智能体（Worker, Reviewer, Meta Planner, Orchestrator）交互所依赖的 6 个标准 JSON Schema。

## 1. 契约清单与对应关系

| Schema 文件 | 实体类型 | 主要生产者 | 主要消费者 | 核心职责 |
| :--- | :--- | :--- | :--- | :--- |
| `task.v1.json` | `Task` | 用户 / Meta Planner | Orchestrator / Worker | 描述任务目标、验收条件（`acceptance_criteria`）、三维评估（风险/复杂度/不确定性）、上下文约束与生命周期状态 |
| `plan.v1.json` | `Plan` | Worker / Meta Planner | Orchestrator / Reviewer | 将目标拆解为结构化步骤、三维指标（含依据）、角色分工、工具依赖及回退策略 |
| `tool_request.v1.json` | `ToolRequest` | Worker / Reviewer | Orchestrator (Tool Executor) | 智能体提议调用的外部能力（工具名、参数、超时、风险等级对应 Level 0~3） |
| `tool_result.v1.json` | `ToolResult` | Tool Executor | Worker / Orchestrator | 受控执行后的真实输出、退出码、执行耗时或拦截拒决原因 |
| `review_result.v1.json` | `ReviewResult` | Reviewer (如 Muse) | Orchestrator / Meta Planner | 独立审查结论（`pass`/`revise`/`reject`/`needs_human`）、严谨的 Issue 严重级别与证据引用 |
| `memory_record.v1.json` | `MemoryRecord` | Orchestrator (复盘总结) | SQLite Memory / Planner | 提炼负向教训（`negative_result`）、成功范式与领域事实，供后续经验检索 |

---

## 2. 核心架构对齐规则

### 2.1 三维评估指标（Risk, Complexity, Uncertainty）用途分离
依据路线图第 55 行要求，三者必须明确分离，不得混为一谈，且各自具有不同的系统决策用途：
1. **风险等级（`risk_level`）**：
   - 取值：`read_only` (Level 0), `workspace_write` (Level 1), `system_sensitive` (Level 2), `danger_external` (Level 3)。
   - **用途**：决定执行权限、沙箱策略与人工审批闸门。
2. **复杂度（`complexity`）**：
   - 取值：`low`, `medium`, `high`, `expert`。
   - **用途**：决定规划深度、子任务拆解颗粒度以及上下文预算。
3. **不确定性（`uncertainty`）**：
   - 取值：`low`, `medium`, `high`, `exploratory`。
   - **用途**：决定质量校验严苛度、探索式试错轮数及修复重试（Repair）预算。

### 2.2 任务生命周期 11 态（完全对齐路线图第 72 行）
- `created`: 任务创建
- `classified`: 任务分类与三维指标评估完成
- `planning`: 正在制定步骤计划
- `awaiting_approval`: 触发 Level 2 人工审批闸门，等待人工裁决
- `executing`: 正在进行受控沙箱执行
- `reviewing`: 正在进行独立无偏见质量审核
- `repairing`: 审核结论为 `revise`，进入有限修复流程（最多 2 轮）
- `succeeded`: 审核结论为 `pass`，任务圆满完成
- `failed`: 终态失败（预算耗尽、审核 `reject`、或触犯 Level 3 绝对禁止）
- `cancelled`: 用户或上层主动取消
- `needs_attention`: 审核结论为 `needs_human` 或遭遇非预期未解异常，转交人工介入

### 2.3 独立审核四级结论（完全对齐路线图第 93 行）
- `pass`：完全满足验收条件，无未解决严重问题，驱动任务进入 `succeeded`。
- `revise`：存在缺陷但具备修复可能，驱动任务进入 `repairing`（有限修复再审）。
- `reject`：严重不合规或方向性错误，不可通过局部修复解决，驱动任务进入 `failed`。
- `needs_human`：存在安全分歧、规范争议或关键依赖缺失，驱动任务进入 `needs_attention`。

---

## 3. 校验示例（Python）

```python
import json
import jsonschema

def validate_entity(data: dict, schema_path: str):
    with open(schema_path, "r", encoding="utf-8") as f:
        schema = json.load(f)
    jsonschema.validate(instance=data, schema=schema)
```
