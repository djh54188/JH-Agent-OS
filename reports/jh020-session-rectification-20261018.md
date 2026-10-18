# JH-020 真实会话切片限定整改（R1–R3）· 2026-10-18（候选中，**待独立验收**）

**结论先行**：按独立审查 `data/jh020-real-session-review-20261018/` 对 `14e42a0` 做**限定整改**，只改三项：
R1（文本/用量绑定准确 native thread＋turn）、R2（派发前登记 + 超时不重复派发 + 可定位原始证据）、R3（派发前地址/模型/预算约束）。
实现完成、离线契约测试与定向变异通过，**真实只读往返已用整改后代码复验通过**（含显式模型核验）。
**本切片仍为“候选”，不等于验收，也不代表真实协作闭环完成**；指定审查会话仍未发送。交当前 Codex 架构师独立验收。

- 工作树：`D:\JH-Agent OS\data\worktrees\n1-artifact-entry`（分支 `n1-artifact-entry`，整改基线 HEAD `14e42a0`）
- 未合入 Hark 候选；未动原树 WIP、Hark 三份归档与既有证据；未修改评审原文件。
- 证据：整改反例 `data/jh020-session-rectify-20261018/`；真实往返 `data/jh020-codex-session-20261018/`（旧记录保留）。

## R1 · 事实绑定准确的 native thread＋turn

**问题（反例实测）**：`_assistant_texts` / `_token_usage` 接受同 thread 的其他轮、也接受无 thread 的通知，随后把文本标成本轮。

**整改**（`src/agent_session_codex.py`）：
- 新增 `notification_binding(params, thread_id, turn_id)` 与 `bound_notifications(...)`：`item/completed` 与 `thread/tokenUsage/updated` 必须 `params.threadId` **且** `params.turnId` 精确匹配本轮，否则**拒收**并计数。
- 无法关联的事实不采用，并在 `unverified` 显式记录（`ignored N … not bound to this thread and turn`）。
- 用量区分：`usage = {turn, thread_total, model_context_window, source:{thread_id,turn_id}}`；无绑定量时 `usage={}` 且记录 **`token usage for this turn not proven`**。
- **离线夹具改为携带真实字段**（`params.threadId`/`params.turnId`/`params.tokenUsage.total|last`），与服务器 schema 一致。

## R2 · 派发前登记、未知结果保留、不重复派发

**问题（反例实测）**：登记发生在 thread/start + turn/start 之后；turn/start 超时后不落盘、不占键，同键重放**再次派发**；登记缺规范化快照、用量与原始证据。

**整改**：
- 在**任何外部调用之前**登记 `reserved` 记录（run、binding、幂等键、指纹、**规范化请求快照**）；写盘通过 `append_record`，**写失败即 `RegistryUnavailable` 且不派发**。
- 超时（请求已发出）：**保留未知结果与已知原生 ID**——`state=UNKNOWN`、保留已获得的 thread id，落 `dispatch_uncertain` 记录，并**返回**该 run（不重发、不重抛）。
- 同键重放一律命中幂等索引，**绝不再次派发**；构造时 `_load_registry()` **重开同一登记文件**即重建幂等索引，阻止不确定键重发（无需自动恢复执行）。
- 持久保存：规范化快照、用量、以及**可定位的原始证据**——通知流写入 `raw/<run_id>.notifications.jsonl`，登记其**路径 + sha256 + 帧数**（原始子集，不冒充完整原生证据；完整 rollout 仍在宿主 store）。

## R3 · 派发前的地址与显式约束校验

**问题（反例实测)**：`zcode/不同实例/既有会话` 地址被接受并发出 Codex 调用，binding 却记 `app_kind=zcode`；`model_id=explicit-different-model`、`budget_tokens=1` 被静默忽略。

**整改**（`_validate_dispatch`，在任何调用前）：
- `app_kind` 必须等于本适配器的 `codex`，否则 `UnsupportedAddress`。
- 实例必须属于受支持集合（默认 `codex-app-server/local`）。
- **只支持新建隔离会话**：`session_id` 需为 `None`/`""`/`new`/`new:*`；既有/指定会话地址（如 `thread-session-1`）明确拒绝，**不偷偷新建 thread 替代**。
- **显式模型必须落实并核验**：有 `model_id` 时随 `thread/start` 下发，并与服务器回报的 `model` 比对，不一致即 `UnsupportedRequest`（并记 `native_model`）。
- **有限预算拒绝**：本切片无法执行有限 token 预算，`budget_tokens` 非空即 **`UnsupportedRequest`**，不忽略。

## 逐项反例结果（整改前后）

见 `data/jh020-session-rectify-20261018/README.md` 的对照表：R1 三项拒收、R2 超时不再派发（`turn_start_calls` 2→1）、R3 两例均 `UnsupportedAddress` 且 0 次调用。

## 保持范围

未接 GUI 执行、未实现 continue/cancel、未发送指定审查会话、未新建替代架构师；`production complete`、`FileToolExecutor` 自动应用与现有成功门保持关闭；未改 Hark 验证器。

## 验证

- 全量 **559 项 558 通过 / 1 既有跳过**（独立计数一致）；本切片新增契约测试 38 项。
- 定向变异 **11/11 检出**（`errors=0`）：含新增 `notification_binding_ignored`(R1)、`registry_reload_ignored`/`registry_write_failure_swallowed`(R2)、`new_session_address_guard_ignored`/`model_honored_check_ignored`/`budget_refusal_ignored`(R3)。
- 真实只读往返（整改后代码）：`explicit_model_honored`、`completion_matched_this_thread_and_turn`、`isolated_workspace_not_written` 全通过；用量区分本轮/线程累计；原始证据带 sha256 可定位。
- 提交后以**新的隔离证据目录**再复验一次真实只读往返，旧记录保留。

## 状态口径

**候选**：实现完成、离线与真实只读往返通过；**未验收**（待架构师独立复核）；**未宣布真实协作闭环通过**。指定审查会话仍未发送（`notLoaded` 不足以证明桌面宿主空闲）。
