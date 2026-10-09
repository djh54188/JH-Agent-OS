# JH-Agent OS 危险动作策略与安全防御规范

- **版本**：v1.0.0
- **适用组件**：Orchestrator、Tool Executor、Codex Harness、Model Adapters
- **最高纪律**：执行权与建议权绝不混淆；宁可阻断报错，绝不越权逃逸。

---

## 1. 风险等级划分

| 等级 | 标识 | 定义 | 处理动作 |
| :--- | :--- | :--- | :--- |
| **Level 0** | `READ_ONLY` | 无状态变更的只读操作（读取受限文件、查看 Git 状态、本地目录列表） | 自动放行 |
| **Level 1** | `WORKSPACE_WRITE` | 严格限制在当前任务受限工作区（`D:\JH-Agent OS`）内的文件创建与修改 | 静态白名单校验通过后自动放行 |
| **Level 2** | `REQUIRES_APPROVAL` | 具有潜在破坏性、影响未声明文件、或跨项目写入的操作 | 挂起任务并向 Meta Planner / 人工抛出审批请求 |
| **Level 3** | `ABSOLUTE_FORBIDDEN` | 对宿主机操作系统、网络环境、安全边界具有毁灭性破坏的操作 | **立即硬性拦截、记录审计日志并终止任务** |

---

## 2. 行为策略规则清单

### 2.1 Level 3：绝对禁止行为（硬性阻断）
以下行为无论 Agent 在 Prompt 或请求中如何声明理由，Tool Executor 一律在进入底层 Shell 前直接抛弃并报警：
1. **系统盘与宿主关键路径破坏**：
   - 试图删除、格式化、修改 `C:\`、`Windows`、`System32`、`Program Files`、`Users` 根目录；
   - 试图执行 `rmdir /s /q C:\`、`del /f /s /q C:\*`、`rm -rf /` 或通配符全局擦除。
2. **注册表与系统启动项篡改**：
   - 包含 `reg add`、`reg delete`、`bcdedit`、修改组策略等指令。
3. **安全凭据外泄与未授权网络渗出**：
   - 试图将环境变量（如各类 `API_KEY`）、`~/.codex/auth.json`、系统私钥等通过 `curl`、`Invoke-WebRequest` 发往未登记的外部 IP/域名。
4. **运行时降级安全门槛（自放权）**：
   - Agent 试图在运行期间通过修改代码、配置文件或 CLI 参数关闭沙箱（如尝试拼接 `--dangerously-bypass-approvals-and-sandbox`）；
   - 试图修改或清空本策略文件 `docs/danger-action-policy.md`。

---

### 2.2 Level 2：需要 Meta Planner / 人工批准的行为
遇到以下操作，Orchestrator 必须将任务置为 `BLOCKED_ON_APPROVAL`，并输出明确的变更差异与影响评估：
1. **跨项目或跨目录写入**：
   - 试图向非当前工作区（如 `D:\JH-Agent OS` 之外的其他 D 盘项目）写入任何文件（依据 `通用规则.md` 第 3.6 节）。
2. **破坏性 Git 操作**：
   - 执行 `git reset --hard`、`git clean -fdx`、`git push --force` 或删除现有分支。
3. **超出 Task 声明范围的大规模文件删除**：
   - 试图删除非当前任务显式生成的临时文件或代码文件。
4. **外部二进制安装与执行**：
   - 试图调用 `npm install -g`、`pip install` 非预装全局包、下载并执行未知可执行程序（`.exe`、`.bat`、`.ps1`）。

---

### 2.3 Level 1：受限工作区自动放行准则
仅当满足以下全部条件时，允许 Tool Executor 自动执行写入：
1. **路径合法性**：目标绝对路径必须严格以当前任务指定的 `workspace_root` 为前缀，且解析后的规范化路径（Normalized Path）严禁出现 `..` 路径穿越逃逸。
2. **扩展名白名单**：仅限于代码与文本类文件（如 `.py`、`.json`、`.md`、`.toml`、`.txt`、`.yml`）。
3. **备份机制**：若覆盖修改已有文件，Tool Executor 必须首先在内存或临时目录创建该文件的快照（Snapshot），以便在测试失败或回退时自动还原。

---

## 3. 拦截流程与状态机转移规范

```text
Worker 发出 ToolRequest
         │
         ▼
[静态策略检查器 (Static Policy Checker)]
 ├── 匹配 Level 3 绝对禁止? ──────► [拦截] ──► 记录 Security Audit ──► 任务状态转为 FAILED
 ├── 匹配 Level 2 人工审批? ──────► [挂起] ──► 抛出 Approval Request ──► 任务状态转为 BLOCKED_ON_APPROVAL
 └── 属于 Level 0 / 1 合法操作? ──► [放行] ──► 受限沙箱执行 ──► 返回 ToolResult 给 Worker
```

### 3.1 审计日志格式（Audit Event）
任何被拦截的操作必须记录至结构化审计日志中：
- `timestamp`: ISO 8601 时间戳
- `task_id`: 关联任务 ID
- `caller_role`: 发起调用的智能体角色
- `tool_name`: 涉及工具
- `violated_rule_id`: 命中的安全规则编号
- `rejected_payload`: 包含完整参数的请求体（敏感字段脱敏）
- `decision`: `DENIED_BY_POLICY`
