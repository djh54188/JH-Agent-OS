# 执行AI-派单-08 交付报告：Gemini CLI Windows 隔离宿主验证

日期：2026-10-23，Asia/Shanghai。执行角色：执行AI。

## 0. 范围理解
承接 Hark 派单 11–13 的离线验收裁决，在独立的 Windows 验证 worktree（`gemini-windows-probe-20261023`）中，验证固定候选在 Windows 上以官方 Gemini CLI 0.65.0 作为受限只读宿主，完成零真实模型的派发/收集，并给出真实任务前的就绪与阻塞证据。
本批严格保持真实模型调用为 0，不读取凭据、不连接用户既有代理、不启用自动文件应用，不整分支合并或推送到远端。

## 1. 分级结论
- **实现 / 安装完成**：
  在受控 D 盘目录 `D:\JH-Agent OS\data\gemini-windows-probe-20261023\cli` 隔离安装官方 `@google/gemini-cli@0.65.0`，无全局安装，无继承环境。
  关键文件哈希与 Linux 离线证据逐字节完全一致：
  - `package.json`: `ea3543b010281115990c43095124eb27a6a0428bc6aa41349261f52675378058`
  - `bundle/gemini.js`: `41847af2158ced805c4a3e098a05b9a2c26fb1a49b12583009b2eaf6a4059a59`
  - `bundle/chunk-DKZARV6U.js`: `4d9cf6435503e5280ecb0f55078793531c8041fe435f60fa8dfce92317c8a28e`
  - `bundle/gemini-GLQ4MVLB.js`: `1c5053102d2204335a96a71f930a0419d2b153cd6d17e1d1ee96753b5272fd9f`
- **无需模型探测通过**：
  `node gemini.js --version` 输出 `0.65.0`（exit 0）；`--help` 成功输出使用说明（exit 0）。适配器 `capabilities()` 成功输出工具限制与版本。
- **离线合成回环与宿主限制验证通过**：
  - 通过本地独立合成端点，真实 CLI 完成 4 次工具调用，返回 `path:line` 引用（`src/agent_session.py:94`），用量统计（90 tokens）完整对应；
  - 宿主只读限制生效：`grep_search` 与 `read_file` 成功；`write_file` 与 `run_shell_command` 均被真实 CLI 宿主直接拒绝（`Tool not found. Did you mean one of: "read_file", "grep_search", "glob"?`）；
  - Windows 路径越界保护生效：直接外部绝对路径、Windows Junction 越界软链接、相对路径（`../`）均被宿主明确拦截并报错 `Path not in workspace`；
  - 零模型调用、工作区（worktree）前后字节完全未变（manifest match，Git clean）。
- **阻塞与未通过项（重大发现）**：
  **Windows 宿主未能判定为就绪（阻塞未通过）**：
  真实 Gemini CLI 0.65.0 在 Windows + Node v24.19.0 下运行 `stream-json` 模式结束退出时，触发 Node/libuv 内部断言失败：
  `Assertion failed: !(handle->flags & UV_HANDLE_CLOSING), file src\win\async.c, line 94`，导致 Windows 进程退出码为 `3221226505`（`0xC0000029 / STATUS_INVALID_SYSTEM_SERVICE`）。
  因此适配器判定 `clean = False`，run state 归为 `failed`（`declared_complete = False`）。
  此非网络或模型问题，属 Windows 下 Node v24 与该 CLI bundle 关闭异步 handle 时的平台兼容性缺陷。

## 2. 提交号与改动清单
- 基线 HEAD：`03d9a0224fd93917ed3a1b9d514e465dfc996825`（`docs(plan): prioritize first Gemini agent bridge over probe hardening`）
- 独立验证分支：`gemini-windows-probe-20261023`，在独立工作树 `D:\JH-Agent OS\data\worktrees\gemini-windows-probe-20261023` 中工作。
- 产品树（`n1-artifact-entry`）及原树（`80979f3`）未受任何修改、未混入任何文件。

## 3. 计数与证据
- 环境：Windows 11 build 26200.9168, Python 3.14.7, Node v24.19.0, npm 11.17.0
- 真实模型调用：0
- 合成回环请求：5 次（均有完整 JSON 记录）
- 原始证据目录：`D:\JH-Agent OS\data\gemini-windows-probe-20261023\evidence`
- 边界测试目录：`D:\JH-Agent OS\data\gemini-windows-probe-20261023\boundary_tests`
- 宿主工具日志：
  - 读越界拦截：`EVENT: tool_result error {'type': 'invalid_tool_params', 'message': 'Path not in workspace: Attempted path ... resolves outside the allowed workspace directories...'}`
  - Junction 越界拦截：同上拦截
  - 相对路径越界拦截：同上拦截
  - 写拦截：`Tool "write_file" not found`
  - Shell 拦截：`Tool "run_shell_command" not found`

## 4. 偏离清单
- `tests/test_jh018_agent_session_contract.py`：允许 `agent_session_gemini.py` 作为已授权消费方白名单（沿用 Hark-11 偏离裁决），46 项通过。

## 5. 故障类覆盖
- Windows 绝对路径及空格：覆盖
- 越界绝对路径、Junction 越界、相对路径越界：真实宿主均阻断
- 只读工具集：四工具受限，写与 Shell 均被拒绝
- 无模型探测（help/version）：通过
- 进程正常退出码：未通过（Node v24 Windows libuv 断言崩溃 3221226505）

## 6. 未做与阻塞
- 真实调用：未做（0 调用，无 key，未登录）
- 阻塞原因：Node v24 在 Windows 上运行 Gemini CLI 退出崩溃，导致 `declared_complete` 无法置为 True。下一次真实任务前需评估降级 Node（例如 Node 22/20 LTS）或排查 libuv 异步关闭问题。

## 结论
**Windows 离线宿主存在已知平台阻塞项（Node v24 退出断言崩溃），尚未完全就绪，不可直接进入真实模型任务。**
