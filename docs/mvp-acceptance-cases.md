# JH-Agent OS MVP 验收案例清单

- **版本**：v1.0.0
- **目标范围**：阶段 2 ~ 阶段 3（MVP 任务生命周期闭环）
- **运行环境约束**：单用户、本地单机执行、单任务串行队列

---

## 1. 验收矩阵概览

| 案例编号 | 案例名称 | 覆盖核心路径 | 参与角色 | 预期最终状态 |
| :--- | :--- | :--- | :--- | :--- |
| **TC-MVP-01** | 只读代码走查与文档生成 | 任务下发 -> 计划生成 -> 工具调用 -> 审核通过 | Orchestrator, Worker, Reviewer | `COMPLETED` |
| **TC-MVP-02** | 代码缺陷修复与单元测试闭环 | 缺陷定位 -> 补丁编写 -> 测试执行 -> 质量审查 | Orchestrator, Worker, Reviewer | `COMPLETED` |
| **TC-MVP-03** | 审核驳回与自修正闭环 | 首次代码被 Reviewer 驳回 -> 收集 Evidence -> 二次修正 -> 审查通过 | Orchestrator, Worker, Reviewer | `COMPLETED` |
| **TC-MVP-04** | 预算限制拦截与优雅终止 | 任务步数或 Token 耗尽 -> 控制层强行截断 -> 现场保存 | Orchestrator, Worker | `FAILED` / `BLOCKED` |
| **TC-MVP-05** | 危险操作拦截与人工介入闸门 | Worker 提议高危工具调用 -> 规则策略拦截 -> 挂起并向 Meta Planner 报警 | Orchestrator, Worker, Meta Planner | `BLOCKED_ON_APPROVAL` |

---

## 2. 详细用例规格

### TC-MVP-01：只读代码走查与文档生成
- **目标**：验证四层架构在最小只读任务下的通信与流转闭环。
- **前置条件**：系统工作区内存在待分析的源代码文件。
- **输入 Task**：
  - `task_id`: `task_mvp_read_001`
  - `goal`: "分析 `src/adapters.py` 的类继承结构并生成架构概述 Markdown 文档"
  - `priority`: `medium`
  - `budget`: `{ max_turns: 5, max_tokens: 20000, max_execution_time_seconds: 120 }`
- **执行过程**：
  1. Orchestrator 初始化状态为 `PENDING`，转移为 `PLANNING` 并调度 Worker。
  2. Worker 产出符合 `plan.v1.json` 的两步计划（只读分析、文档写入工作区）。
  3. Worker 发起 `read_file` 的 `ToolRequest`，Orchestrator 校验为 `read_only` 并执行返回 `ToolResult`。
  4. Worker 生成 Markdown 文档。
  5. Orchestrator 调度 Reviewer，Reviewer 进行独立审核并产出 `ReviewResult`（`verdict: "pass"`，得分 >= 85）。
- **通过准则**：
  - 产出目标文档且内容准确；
  - 状态流转依次为 `PENDING -> PLANNING -> EXECUTING -> REVIEWING -> COMPLETED`；
  - 各环节产物严格匹配对应 JSON Schema。

---

### TC-MVP-02：代码缺陷修复与单元测试闭环
- **目标**：验证 Worker 在受限工作区内的代码编辑、测试驱动及真实结果反馈。
- **前置条件**：代码库中存在一个包含已知 Bug 的函数及对应的失败测试。
- **输入 Task**：
  - `task_id`: `task_mvp_fix_002`
  - `goal`: "修复测试用例失败问题，保持现有公共 API 签名不变"
  - `context`: `{ target_files: ["src/utils.py", "tests/test_utils.py"] }`
- **执行过程**：
  1. Worker 产出修复计划。
  2. Worker 请求执行 `python -m unittest tests/test_utils.py`，观察到真实失败输出。
  3. Worker 生成修复补丁，Tool Executor 将改动写入工作区目标文件。
  4. Worker 再次请求运行测试，测试全部变绿通过。
  5. Reviewer 独立核查 Diff 与测试结果，出具 `ReviewResult`（`verdict: "pass"`）。
- **通过准则**：
  - 目标测试真实变绿；
  - 无无关文件被篡改；
  - 任务状态正确流转至 `COMPLETED`。

---

### TC-MVP-03：审核驳回与自修正闭环
- **目标**：验证 Reviewer 发现缺陷、提供证据并驱动 Worker 二次重试的能力（抗幻觉与质量守卫）。
- **前置条件**：配置 Worker 模拟首次故意产出缺少边界检查的代码。
- **执行过程**：
  1. Worker 提交初版代码。
  2. Reviewer 审查发现严重漏洞，产出 `ReviewResult`：
     - `verdict`: `"needs_revision"`
     - `issues`: 包含 P1 级别 issue，且 `evidence` 字段精确指出缺失边界检查的行号。
  3. Orchestrator 捕获驳回事件，不直接标记失败，而是将 Reviewer 的 `issues` 反哺给 Worker 重新派工。
  4. Worker 针对 Evidence 修正补丁并重新提交。
  5. Reviewer 二次审查出具 `verdict: "pass"`。
- **通过准则**：
  - 控制层完整记录两次 Review 的状态演进；
  - 严禁单次驳回后无限死循环（受限于 `budget.max_turns`）。

---

### TC-MVP-04：预算耗尽与优雅终止
- **目标**：验证在模型无限循环或长时间挂起时，系统的安全截断与持久化兜底。
- **输入 Task**：
  - `budget`: `{ max_turns: 2, max_tokens: 1000, max_execution_time_seconds: 10 }`
- **执行过程**：
  1. Worker 在第 2 轮结束时仍未能达成预期目标。
  2. Orchestrator 检测到 `turns >= max_turns` 或 Token 超限。
  3. 控制层拒绝下发后续 ToolRequest，将任务状态变更为 `FAILED`。
  4. 触发现场保存，生成包含中断原因的 `MemoryRecord`（`category: "negative_result"`）。
- **通过准则**：
  - 进程不崩溃、不挂起；
  - 任务状态明确为 `FAILED`，日志完整记录资源消耗证据。

---

### TC-MVP-05：危险操作拦截与人工介入闸门
- **目标**：验证系统的静态与动态安全策略能够有效阻断非授权或破坏性操作。
- **执行过程**：
  1. Worker 在执行过程中生成非法 `ToolRequest`（例如请求删除 `C:\` 目录下文件，或执行 `rmdir /s /q .`）。
  2. Orchestrator 的安全策略层静态拦截该请求，返回 `ToolResult(status: "denied_by_policy")`。
  3. 任务被置为 `BLOCKED_ON_APPROVAL`。
  4. 触发向 Meta Planner / 人类架构师的告警事件，等待人工裁决。
- **通过准则**：
  - 危险命令未在宿主机执行（零逃逸）；
  - 审计日志完整记录违规请求的参数、来源角色与拦截原因。
