# JH-Agent OS 核心通信协议 Schema（v1.0.0）

本目录包含 JH-Agent OS 核心控制层与多智能体（Worker, Reviewer, Meta Planner, Orchestrator）交互所依赖的 6 个标准 JSON Schema。

## 1. 契约清单与对应关系

| Schema 文件 | 实体类型 | 主要生产者 | 主要消费者 | 核心职责 |
| :--- | :--- | :--- | :--- | :--- |
| `task.v1.json` | `Task` | 用户 / Meta Planner | Orchestrator / Worker | 描述任务目标、上下文约束、预算上限与生命周期状态 |
| `plan.v1.json` | `Plan` | Worker / Meta Planner | Orchestrator / Reviewer | 将目标拆解为结构化步骤、角色分配、工具依赖及回退策略 |
| `tool_request.v1.json` | `ToolRequest` | Worker / Reviewer | Orchestrator (Tool Executor) | 智能体提议调用的外部能力（工具名、参数、超时、风险等级） |
| `tool_result.v1.json` | `ToolResult` | Tool Executor | Worker / Orchestrator | 受控执行后的真实输出、退出码、执行耗时或拦截拒决原因 |
| `review_result.v1.json` | `ReviewResult` | Reviewer (如 Muse) | Orchestrator / Meta Planner | 独立代码/方案审查结论、严谨的问题严重级别与证据引用 |
| `memory_record.v1.json` | `MemoryRecord` | Orchestrator (复盘总结) | SQLite Memory / Planner | 提炼负向教训、成功范式与领域事实，供后续任务经验检索 |

---

## 2. 通用设计原则

1. **显式版本控制**：
   - 每个实体均包含 `schema_version: "1.0.0"`，且严格匹配 SemVer。
   - 文件命名采用 `<entity>.v<major>.json` 格式，确保向前兼容与向后平滑演进。
2. **严防非法字段漂移**：
   - 所有顶层及子实体均设置 `additionalProperties: false`（除特定业务扩展 metadata 容器外），杜绝模型自由发挥产生未知字段。
3. **强类型与格式约束**：
   - ID 统一采用带业务前缀的正则校验（如 `task_*`, `plan_*`, `treq_*`, `tres_*`, `rev_*`, `mem_*`）。
   - 时间字段严格符合 ISO 8601 `date-time` 格式。
   - 风险等级与生命周期状态采用严格受限的 Enum 范围。
4. **负面与证据优先（依据 AGENTS.md 准则）**：
   - `ReviewResult` 要求每个 issue 必须附带具体的 `evidence`（代码片段、日志截取或错误复现步骤），禁止泛泛而谈。
   - `MemoryRecord` 显式支持 `negative_result`（负结果入册）与 `verification_status`（已验证/未验证/已推翻），禁止将推测当做事实持久化。

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
