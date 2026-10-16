# JH-Agent OS 文件工具契约与权限边界（阶段 4 首批）

> **最新复核 80979f3**：原 6 项反例在各自时点被阻止，但 JH-016 新增 3 项 P1 完整性阻塞与 1 项 P2 变异证据问题，见[最新报告](../reports/jh016-review-20261012.md)。下文契约不降低，真实文件写入准入仍关闭；下一批按[整改指令](handoffs/jh015-after-jh016-20261012.md)实施。下一段 ec11584 是初次复核的历史基线。

> **2026-10-12 独立复核状态**：本契约的安全不变量继续有效，但当前实现 ec11584 尚未满足其中五项，见[复核报告](../reports/n1-architecture-review-20261012.md)与[探针结果](../reports/n1-architecture-probes-20261012.json)。包括事后拒绝已产生截断、邻居临时名覆盖、并发更新被覆盖、父目录替换越界、审核与落盘内容不一致。JH-015 整改独立通过前，不用于真实工作文件；正文“已覆盖”只指原交付测试范围，不构成完整保证。不得通过降低契约来关闭问题。

- **版本**：v1.0.0（2026-10-11）
- **适用组件**：`src/file_tools.py::FileToolExecutor`、Orchestrator file_tools 模式
- **范围声明**：本批仅开放工作区受限的 `read_file` 与 `stage_write`（暂存写入）及
  审核后受控 `apply`。任意 Shell、系统权限操作、外部发布不在本批；旧 E4 自研通用 Shell/Git 平台计划已取消，见 [ADR-0003](adr/ADR-0003-codex-hosted-entry.md)。

---

## 1. 工具契约

### 1.1 read_file（Level 0，只读）
- 请求：`{"tool_name": "read_file", "parameters": {"path": <工作区内相对或绝对路径>}}`
- 行为：逐组件路径校验 → 打开（含 post-open 包含性复查）→ 返回
  `structured_data: {path, content, sha256}`。
- 失败：越界/链接/缺失 → `denied_by_policy`；文件不存在 → `failed`。

### 1.2 stage_write（Level 1，暂存写入）
- 请求：`{"tool_name": "stage_write", "parameters": {"path": <授权目标>, "content": <完整内容>}}`
- 行为：
  1. 对**最终目标路径**做逐组件边界校验（新文件要求父目录已存在）；
  2. 记录目标前置状态 `target_pre_sha256`（存在则哈希，不存在为 None）；
  3. 写入暂存区 `<workspace>/_staging/<task_id>/<name>`（绝不写目标）；
  4. 回读校验暂存哈希与内容一致；
  5. 返回 `structured_data: {target_path, staged_path, content_sha256, target_pre_sha256, bytes}`。
- 语义：暂存产物即审核对象；Worker 全程无目标文件访问权。

### 1.3 apply（控制层动作，非 Worker 提议）
- 触发：且仅当**当前审核 verdict == pass**、控制层验收全部通过、无未解决控制问题。
- 应用前逐一再确认（任一失败即拒绝，**绝不覆盖目标**）：
  1. 暂存文件存在且 `staged_sha == 审核内容 sha`（防暂存篡改）；
  2. 目标当前哈希 == 暂存时前置哈希（防目标并发变更）；
  3. 最终路径重新过全部边界检查（防路径在审核后变化）；
  4. 目标仍在 `target_files` 授权清单内（防授权漂移）；
  5. 策略引擎对应用请求再次逐调用放行。
- 应用顺序：备份原文件（校验备份哈希）→ 临时文件写入目标目录并 fsync →
  `os.replace` 原子替换 → 回读确认哈希。
- 每阶段记录事件：`apply_start / backup_created / temp_written / replaced /
  apply_confirmed`（或 `apply_uncertain`）。

---

## 2. 路径边界（逐调用，基于实际参数）

`resolve_workspace_path` 的组件行走（**禁止盲目词法归一化**）：

1. **字符串形态**：空路径、NUL 字节、尾部分隔符（表目录语义）拒绝。
2. **绝对路径预检**：normcase 后必须以工作区为前缀，且下一字符为分隔符
   （防 `ws` 与 `ws-evil` 同名前缀混淆）；盘符必须一致。
3. **组件行走**：从锚点（盘符/工作区根）出发，对每个真实组件：
   - `.` 跳过；`..` 仅在证明当前目录于工作区内后弹回；
   - 中间组件必须**已存在**、**非 reparse point**（junction/symlink）、**是目录**；
   - 最终组件不得是已存在目录。
4. **打开时复查（TOCTOU）**：
   - 打开前对最终组件再做 reparse 检查（竞态钩子植入的链接在打开前被拒）；
   - 打开后再以 `realpath` 验证包含性（检查后到打开前的亚毫秒窗口的补偿控制，
     其移除不可离线确定性测试，见 `scripts/verify_phase4_guards.py` 注记）。
5. **不能证明安全即拒绝**：任何怀疑（缺失父目录、repoint、越界、前缀碰撞）
   均以 `PathBoundaryError` 拒绝，不猜测。

---

## 3. 恢复语义

| 场景 | 判定 | 动作 |
| :--- | :--- | :--- |
| 中断于备份后/替换前 | 目标哈希 == 前置哈希 → **未生效已确证** | 允许重新走完整再确认的 apply（演练已覆盖） |
| 中断于替换后/确认前 | 目标哈希 == 候选哈希 → **已生效已确证** | 记录 applied_confirmed，任务继续 |
| 无法证明任一状态 | 目标哈希非两者 → **不确定** | 任务转 `needs_attention`，保留现场与证据，禁止盲目重放 |
| 应用被策略/边界拒绝 | 目标未被触碰 | 任务转 `needs_attention`（不掩盖并发写入者） |

---

## 4. 凭证与测试卫生

- 开发与验收使用**项目内独立测试工作区**（`data/test-workspaces/<case>`，
  Git 忽略），不覆盖项目源码与用户文件。
- junction 逃逸测试使用项目内两个隔离目录（受控工作区 + 模拟工作区外目标），
  **不使用系统目录**。
- 竞态场景以确定性 seam（`race_hook`/`fault_injector`）复现"检查后变化"，
  生产代码不设置任何 seam。
- 变异守卫：`scripts/verify_phase4_guards.py` 5 项（包含性移除、祖先 reparse
  移除、应用目标版本检查移除、暂存哈希校验移除、应用门禁移除）全部检出。
  post-open realpath 复查为补偿控制，其移除不可离线确定性测试，已在守卫脚本注记。
