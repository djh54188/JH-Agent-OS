# JH-Agent OS 危险动作策略与安全防御规范

- **版本**：v1.1.0
- **适用组件**：Orchestrator、Tool Executor、Codex Harness、Model Adapters
- **最高纪律**：执行权与建议权绝不混淆；宁可阻断报错，绝不越权逃逸。

---

## 1. 风险分级与 Schema 映射表

控制层建立统一的双向映射标准，解决 `tool_request.v1.json` 中的 `risk_level` 与安全策略 `Level` 的判定一致性：

| 策略分级 | 策略标识 | ToolRequest 映射字段 (`risk_level`) | 定义 | 处理动作与状态机转移 |
| :--- | :--- | :--- | :--- | :--- |
| **Level 0** | `READ_ONLY` | `read_only` | 无状态变更的只读操作（读取受限文件、查看 Git 状态、本地目录列表） | 自动放行；维持 `executing` |
| **Level 1** | `WORKSPACE_WRITE` | `workspace_write` | 严格限制在当前任务受限工作区（`D:\JH-Agent OS`）内的文件创建与修改 | 静态白名单校验通过后自动放行；维持 `executing` |
| **Level 2** | `REQUIRES_APPROVAL` | `system_sensitive` | 具有潜在破坏性、影响未声明文件、跨项目写入或敏感系统调用 | **挂起任务，抛出审批请求**；状态转为 `awaiting_approval` |
| **Level 3** | `ABSOLUTE_FORBIDDEN` | `danger_external` | 对宿主机操作系统、网络环境、安全边界具有毁灭性破坏的操作 | **立即硬性拦截、记录审计日志并终止任务**；状态转为 `failed` |

---

## 2. 核心架构安全铁律

### 铁律一：每次工具调用前必须检查实际参数与权限（逐调用动态鉴权）
1. **禁止计划级单次放行**：严禁仅在计划制定阶段（`planning`）做一次性静态审批；Tool Executor 必须在**每次真实执行工具前的一瞬间**，对当次调用的实际参数（如具体文件路径、命令行字符串）执行完整的白名单与边界检查。
2. **计划变更增量重审**：若任务在执行过程中发生重规划或计划调整，任何新增的、未在初始授权范围内的动作，必须作为增量动作重新走权限校验流程；未获授权前不得执行。

### 铁律二：Level 3 绝对禁止优先于任何路由结果与模型评分
1. **零降级容忍**：Level 3 危险动作判定拥有最高执行优先级，**优先于任何多模型路由决策、优先级权重或模型打分**。
2. **严禁加权平均稀释**：任何组件不得通过所谓“综合风险加权平均”或“高置信度补偿”将 Level 3 行为降级为 Level 2 或 Level 1。放行判断必须由确定性的本地代码规则强制执行，模型无权自裁。

### 铁律三：Worker 完成不等于任务成功
Worker 产出代码、脚本执行完毕、Shell 退出码为 0 或 Git 提交成功，均**不代表任务满足目标**。任务必须通过 Reviewer 独立质量审核出具 `pass` 或满足显式 `acceptance_criteria`，方可由 Orchestrator 标记为 `succeeded`。

### 铁律四：Reviewer 不可用绝不得自动视为通过
若 Reviewer 遭遇网络超时、供应商宕机、凭据失效或返回畸形数据，Orchestrator **绝对禁止静默放行或自动标记通过**。系统必须转入 `needs_attention`（等待人工介入）或受控标记为 `failed`。

---

## 3. 行为策略规则清单

### 3.1 Level 3：绝对禁止行为（硬性阻断，终态转为 `failed`）
以下行为无论 Agent 在 Prompt 或请求中如何声明理由，Tool Executor 一律在进入底层 Shell 前直接抛弃、记录审计事件并将任务置为 `failed`：
1. **系统盘与宿主关键路径破坏**：
   - 试图删除、格式化、修改 `C:\`、`Windows`、`System32`、`Program Files`、`Users` 根目录；
   - 试图执行 `rmdir /s /q C:\`、`del /f /s /q C:\*`、`rm -rf /` 或通配符全局擦除。
2. **注册表与系统启动项篡改**：
   - 包含 `reg add`、`reg delete`、`bcdedit`、修改系统组策略等指令。
3. **安全凭据外泄与未授权网络渗出**：
   - 试图将环境变量（如各类 `API_KEY`）、`~/.codex/auth.json`、系统私钥等通过网络发往外部未登记的 IP/域名。
4. **运行时降级安全门槛（自放权）**：
   - Agent 试图在运行期间通过修改代码、配置文件或 CLI 参数关闭沙箱（如尝试拼接 `--dangerously-bypass-approvals-and-sandbox`）；
   - 试图修改或清空本策略文件 `docs/danger-action-policy.md`。

---

### 3.2 Level 2：需要 Meta Planner / 人工批准的行为（挂起，状态转为 `awaiting_approval`）
遇到以下操作，Orchestrator 必须将任务置为 `awaiting_approval`，并输出明确的变更差异与影响评估：
1. **跨项目或跨目录写入**：
   - 试图向非当前工作区（如 `D:\JH-Agent OS` 之外的其他 D 盘项目）写入任何文件（依据 `通用规则.md` 第 3.6 节）。
2. **破坏性 Git 操作**：
   - 执行 `git reset --hard`、`git clean -fdx`、`git push --force` 或删除现有分支。
3. **超出 Task 声明范围的大规模文件删除**：
   - 试图删除非当前任务显式生成的临时文件或核心代码文件。
4. **外部二进制安装与执行**：
   - 试图调用 `npm install -g`、`pip install` 非预装全局包、下载并执行未知可执行程序（`.exe`、`.bat`、`.ps1`）。

---

### 3.3 Level 1：受限工作区自动放行准则（维持 `executing`）
仅当满足以下全部条件时，允许 Tool Executor 自动执行写入：
1. **路径合法性**：目标绝对路径必须严格以当前任务指定的 `workspace_root` 为前缀，且解析后的规范化路径（Normalized Path）严禁出现 `..` 路径穿越逃逸。
2. **扩展名白名单**：仅限于代码与文本类文件（如 `.py`、`.json`、`.md`、`.toml`、`.txt`、`.yml`）。
3. **备份与暂存机制**：写入类操作优先在隔离工作区或暂存区形成产物，覆盖修改前必须在临时目录保留快照（Snapshot），确保具备快速回滚能力。

---

## 4. 拦截流程与状态机转移规范

```text
Worker 发出 ToolRequest (包含具体 parameters)
         │
         ▼
[逐调用动态鉴权 (Pre-Execution Authorization Check)]
 ├── 匹配 Level 3 绝对禁止? ──────► [硬阻断] ──► 记 Security Audit ──► 任务状态转为 failed
 ├── 匹配 Level 2 人工审批? ──────► [挂起]   ──► 抛出 Approval 工单 ──► 任务状态转为 awaiting_approval
 └── 属于 Level 0 / 1 合法操作? ──► [放行]   ──► 受限工作区沙箱执行 ──► 返回 ToolResult 给 Worker
```

### 4.1 审计日志格式（Audit Event）
任何被拦截的操作必须记录至结构化审计日志中：
- `timestamp`: ISO 8601 时间戳
- `task_id`: 关联任务 ID
- `caller_role`: 发起调用的智能体角色
- `tool_name`: 涉及工具
- `violated_rule_id`: 命中的安全规则编号
- `rejected_payload`: 包含完整参数的请求体（敏感字段脱敏）
- `decision`: `DENIED_BY_POLICY`
