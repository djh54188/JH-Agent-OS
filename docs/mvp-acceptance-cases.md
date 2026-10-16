# JH-Agent OS MVP 验收案例清单

- **版本**：v1.1.0
- **目标范围**：阶段 2 ~ 阶段 3（MVP 任务生命周期闭环）
- **状态机对齐**：采用路线图第 72 行 11 态标准（`created`, `classified`, `planning`, `awaiting_approval`, `executing`, `reviewing`, `repairing`, `succeeded`, `failed`, `cancelled`, `needs_attention`）
- **运行环境约束**：单用户、本地单机执行、单任务串行队列

---

## 1. 核心质量与安全原则（前置铁律）

1. **逐调用动态鉴权**：每次工具调用执行前必须检查当次实际参数和权限，禁止仅在计划阶段静态放行一次。
2. **Worker 完成不等于任务成功**：代码生成、脚本执行成功或退出码为 0，均不等于任务成功；必须由 Reviewer 出具 `pass` 或满足验收条件。
3. **Reviewer 不可用不得自动视为通过**：审评超时或不可用时严禁静默放行，必须转为 `needs_attention` 或 `failed`。
4. **审核结论处置分野**：
   - `pass` -> 任务圆满成功（`succeeded`）
   - `revise` -> 缺陷可修复，进入有限重试闭环（`repairing`，默认最多 2 轮）
   - `reject` -> 严重缺陷或方向性违背，不可修复终态（`failed`）
   - `needs_human` -> 争议或人工规则分歧，转交人工介入（`needs_attention`）

---

## 2. 验收场景覆盖矩阵（覆盖全部六类核心场景）

| 场景分类 | 案例编号 | 案例名称 | 参与角色 | 预期终态 |
| :--- | :--- | :--- | :--- | :--- |
| **场景一：正常流程** | **TC-MVP-01** | 只读代码走查与文档生成 | Orchestrator, Worker, Reviewer | `succeeded` |
| **场景一：正常流程** | **TC-MVP-02** | 代码缺陷修复与单元测试闭环 | Orchestrator, Worker, Reviewer | `succeeded` |
| **场景二：审核拒绝** | **TC-MVP-03** | 审核驳回（`revise`）与自修正闭环 | Orchestrator, Worker, Reviewer | `succeeded` |
| **场景三：越界危险操作** | **TC-MVP-05** | 敏感与高危动作拦截（Level 2 / Level 3） | Orchestrator, Worker, Meta Planner | `awaiting_approval` / `failed` |
| **场景四：预算耗尽** | **TC-MVP-04** | 预算限制拦截与现场保存 | Orchestrator, Worker | `failed` |
| **场景五：供应商故障** | **TC-MVP-06** | 供应商超时/异常/拒绝响应处理 | Orchestrator, Worker, Reviewer | `needs_attention` |
| **场景六：写入中断恢复** | **TC-MVP-07** | 工具写入异常中断与快照回滚 | Orchestrator, Tool Executor | `needs_attention` / `failed` |

---

## 3. 详细用例规格

### TC-MVP-01：只读代码走查与文档生成（正常流程）
- **目标**：验证四层架构在最小只读任务下的通信与流转闭环。
- **输入 Task**：
  - `task_id`: `task_mvp_read_001`
  - `title`: "生成适配器架构概述文档"
  - `description`: "分析 `src/adapters.py` 的类继承结构并生成 Markdown 文档"
  - `acceptance_criteria`: ["产出文档完整描述 ModelAdapter 继承树", "Reviewer 审核得分 >= 85"]
  - `risk_assessment`: `{ risk_level: "read_only", complexity: "low", uncertainty: "low", risk_basis: "只读文件操作", routing_basis: "标准 Worker + Reviewer" }`
  - `budget`: `{ max_turns: 5, max_tokens: 20000, max_execution_time_seconds: 120 }`
- **执行过程**：
  1. Orchestrator 初始化状态为 `created` -> `classified` -> `planning`。
  2. Worker 产出符合 `plan.v1.json` 的计划。
  3. Worker 发起 `read_file` 的 `ToolRequest`，Tool Executor 鉴权后放行并返回 `ToolResult`。状态转移为 `executing`。
  4. Worker 产出 Markdown 文档并引用产物。状态进入 `reviewing`。
  5. Reviewer 独立审核并出具 `ReviewResult`（`verdict: "pass"`）。
  6. Orchestrator 将任务状态置为 `succeeded`。
- **通过准则**：状态严格按生命周期流转；产物与日志符合对应 Schema。

---

### TC-MVP-02：代码缺陷修复与单元测试闭环（正常流程）
- **目标**：验证受限工作区内代码编辑、测试驱动及真实结果反馈。
- **输入 Task**：
  - `acceptance_criteria`: ["目标单测 tests/test_utils.py 运行全绿", "无回归破损", "Reviewer verdict 为 pass"]
  - `risk_assessment`: `{ risk_level: "workspace_write", complexity: "medium", uncertainty: "low", risk_basis: "受限工作区写测试与代码", routing_basis: "标准 Worker" }`
- **执行过程**：
  1. Worker 产出计划；在 `executing` 状态下运行测试，捕获失败输出。
  2. Worker 产出补丁；Tool Executor 鉴权后在受限工作区写入目标文件。
  3. Worker 再次执行单测，单测变绿。
  4. Reviewer 独立核查 Diff 与测试证据，出具 `verdict: "pass"`。
  5. 任务状态转为 `succeeded`。
- **通过准则**：单测变绿，无未声明文件被修改，状态转为 `succeeded`。

---

### TC-MVP-03：审核驳回（`revise`）与自修正闭环（审核拒绝场景）
- **目标**：验证 Reviewer 发现缺陷出具 `revise`，提供 Evidence 并驱动有限修复闭环。
- **执行过程**：
  1. Worker 首次提交存在边界漏洞的代码。
  2. Reviewer 审查出具 `ReviewResult`：
     - `verdict`: `"revise"`
     - `issues`: 包含 P1 Issue，且 `evidence` 精确指出缺失边界检查的行号。
  3. Orchestrator 捕获 `revise`，状态转为 `repairing`，并将 Issues 反哺给 Worker。
  4. Worker 针对 Evidence 修正补丁并重新提交（修复轮数不超过 2 轮）。
  5. Reviewer 二次审查出具 `verdict: "pass"`，任务状态转为 `succeeded`。
- **通过准则**：若审核为 `reject` 则直接转入 `failed`；`revise` 最多 2 轮修复，超限直接转为 `failed`。

---

### TC-MVP-04：预算耗尽与现场保存（预算耗尽场景）
- **目标**：验证任务在 Token 或步数超限时的确定性熔断截断。
- **执行过程**：
  1. Worker 循环执行但在 `turns >= max_turns`（或超超时时限）仍未完成。
  2. Orchestrator 强行截断，拒绝后续 ToolRequest。
  3. 任务状态直接变更为 `failed`。
  4. 触发记忆记录，沉淀包含中断原因的 `MemoryRecord`（`category: "negative_result"`）。
- **通过准则**：进程不崩溃，不挂死，状态明确为 `failed`。

---

### TC-MVP-05：敏感与高危动作拦截（越界危险操作场景）
- **目标**：验证逐调用动态鉴权、Level 2 审批挂起与 Level 3 绝对禁止。
- **分支 A（Level 2 敏感操作）**：
  - Worker 发起跨项目写入请求（`system_sensitive`）。
  - Tool Executor 匹配到 Level 2 规则，拦截下发。
  - 任务状态转为 `awaiting_approval`，向 Meta Planner 抛出工单，等待人工裁决。
- **分支 B（Level 3 绝对禁止操作）**：
  - Worker 发起删除宿主系统盘操作（`danger_external`）。
  - Tool Executor 匹配到 Level 3 规则，硬性阻断，记录 Security Audit。
  - 任务状态立即转为 `failed` 并终止，严禁挂起等待审批。
- **通过准则**：零逃逸，Level 2 正确进入 `awaiting_approval`，Level 3 立即进入 `failed`。

---

### TC-MVP-06：供应商超时/异常/拒绝响应处理（供应商故障场景）
- **目标**：验证底层模型服务不可用时，系统严禁静默放行并做安全兜底。
- **执行过程**：
  1. 在 Worker 执行或 Reviewer 审核阶段，底层模型端点返回 HTTP 500、连接超时或返回无法解析的非 JSON 数据。
  2. 模型适配器在重试上限耗尽后抛出 `AdapterError`。
  3. Orchestrator 捕获异常，明确记录供应商故障原因。
  4. **严禁自动视为通过**：任务状态置为 `needs_attention`（等待人工介入排查端点），或在批处理模式下置为 `failed`。
- **通过准则**：系统不伪造虚假审核通过，日志完整保留供应商 HTTP 错误详情与时间戳。

---

### TC-MVP-07：工具写入异常中断与快照回滚（写入中断恢复场景）
- **目标**：验证在文件覆盖写入或环境变更中途发生异常时的数据一致性恢复。
- **执行过程**：
  1. Tool Executor 在受限工作区执行文件修改操作。
  2. 写入前 Tool Executor 在内存或临时目录创建目标文件快照（Snapshot）。
  3. 写入过程中模拟发生异常（I/O 错误、进程被中断或断电模拟）。
  4. Orchestrator 捕获写入中断，启动对账与恢复流程，利用快照回滚损坏的文件。
  5. 任务状态转为 `needs_attention` 或 `failed`，并记录中断现场。
- **通过准则**：目标文件恢复至修改前的一致状态，未留存半写入损坏文件。
