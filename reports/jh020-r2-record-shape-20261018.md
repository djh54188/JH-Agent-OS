# JH-020 R2-A 登记记录字段校验整改 · 2026-10-18（候选，**待独立验收**）

**结论先行**：按独立审查 `data/jh020-r2-final-review-20261018/` 仅补 **R2-A 登记记录的字段校验**（R1/R3/R2-B 已关闭，读取失败与坏 JSON 原反例已关闭，**未重做**）。
可解析但字段不完整的历史现已**整体不可用并在派发前拒绝**：零外部调用、零文件改写。**仍未验收，未宣布协作闭环通过。**

- 工作树：`D:\JH-Agent OS\data\worktrees\n1-artifact-entry`（分支 `n1-artifact-entry`，基线 HEAD `5b1dc26`）
- 未覆盖评审原文件；旧证据原样保留；新证据写入新隔离目录 `data/jh020-r2-shape-rectify-20261018/`。

## 剩余缺陷（反例实测，整改前）

`parse_registry_text` 只要求 `run_id`/`event` **键名存在**；`_load_registry` 随后跳过 falsy `run_id`，并把缺失的 `idempotency_key` 当作“无幂等映射”。因此把已登记 unknown 请求的 `run_id` 改为 `null`、或删除 `idempotency_key`（JSON 仍可解析）后，重开同键提交会**新建 run 并再次派发**，且追加文件字节。

## 整改

`src/agent_session_codex.py` 新增 `validate_registry_record(record)`，由 `parse_registry_text` 逐条调用（**先校验，再建索引**）：

| 字段 | 规则 |
|---|---|
| `run_id` | **非空字符串** |
| `event` | 非空字符串 |
| `idempotency_key` | **字段必须存在**；`null` 与**字段缺失**必须区分——前者是“本派发无幂等键”，后者判损坏；非 null 时必须为非空字符串 |
| 绑定 | `app_kind`/`instance_id`/`task_id` 非空字符串；`session_id` 字符串或 `null`；`attempt` 非负整数（且非 bool） |
| 幂等/恢复 | `request_fingerprint` 非空字符串；`request_snapshot` 字符串；`state` 非空字符串；`native_state`/`native_session_id`/`native_model` 字符串或 `null`；`native_turns` 非空字符串列表 |
| `event == "collected"` | `declared_status` 非空字符串；`artifacts`/`raw_refs`/`unverified` 为列表；`usage` 为对象 |

- 任一记录非法 ⇒ **整体历史不可用**（`RegistryUnavailable`），**派发前拒绝**；`_load_registry` 原“跳过 falsy run_id”已删除，改为直接使用已校验的 `run_id`。
- **不跳过、不默认、不修复、不迁移、不截断**；原字节保留；不扩建恢复平台。

## 逐项反例结果

| 形状 | 整改前 | 整改后 |
|---|---|---|
| `healthy`（正控） | 同 run、0 调用、字节不变 | **不变** |
| `null_run_id` | 接受 → 新建 run、**又一次派发**、追加字节 | **`RegistryUnavailable`、0 调用、字节不变** |
| `missing_idempotency_key` | 接受 → 新建 run、**又一次派发**、追加字节 | **`RegistryUnavailable`、0 调用、字节不变** |

> **计数口径**：`initialize`、`thread/start`、`turn/start` 是**三次协议调用**，代表**一次重新派发**，不是执行三遍。

## 补的正反控

- 正控：完整记录通过校验；**合法 `null` 幂等键**（无幂等键派发的记录）重开为**健康历史**，可恢复该 run 且字节不变。
- 反控：字段缺失（`idempotency_key`）与非法值（`run_id` 为 `null`/空串、`attempt` 为字符串或 bool、`native_turns` 非列表或含空串、`state` 非字符串、`app_kind` 空、`request_fingerprint` 空、`idempotency_key` 空串、`native_session_id` 非字符串）**全部拒绝**。
- 反例路径均断言**零外部调用**与**文件字节不变**。

## 复核与验证

- 评审探针**只读副本**复跑（原文件未改）：三例读数如上，与整改前对照成立。
- 全量 **577 项 576 通过 / 1 既有跳过**；**独立计数 577**（`grep 'def test_'`）。
- 定向变异 **17/17 检出**（`errors=0`），新增 `record_validation_bypassed`、`missing_idempotency_field_defaulted` 两条——绕过字段校验必变红。

## 真实往返证据来源

本批**仅改登记校验**，**未再调用真实模型**；沿用已核实的 `5b1dc26` 真实往返证据
（`data/jh020-r2-final-roundtrip-20261018/`，token `JH020-RT-B250E712`，thread `01a14eef-1d40-7cc0-8bf6-ff268e7ad8ad`，
turn `01a14eef-2206-7791-b923-1a5b747dd4c4`，字节摘要 `de7f3bb9…`，8868 字节 / 26 帧 / 无 CRLF）。

## 保持范围

未混入 Hark 集成、GUI 执行、continue/cancel 或指定审查会话发送；`production complete`、`FileToolExecutor` 自动应用与现有成功门保持关闭。**候选/待独立验收**，未宣布协作闭环通过。
