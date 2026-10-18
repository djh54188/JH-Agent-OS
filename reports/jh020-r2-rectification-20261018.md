# JH-020 真实会话切片 R2 限定整改（R2-A/R2-B）· 2026-10-18（候选，**待独立验收**）

**结论先行**：按独立审查 `data/jh020-session-r2-review-20261018/` 修 **R2 两项**（R1/R3 已关闭，未重做）。
R2-A：登记历史不可读/损坏时**拒发**；R2-B：证据摘要**绑定实际磁盘字节**并由验证脚本实际核对。
离线正反控、定向变异、全量与真实只读往返（新证据）均通过。**仍为候选，未验收；未宣布协作闭环通过。**

- 工作树：`D:\JH-Agent OS\data\worktrees\n1-artifact-entry`（分支 `n1-artifact-entry`，基线 HEAD `d9e46b8`）
- 未覆盖评审原文件；旧证据原样保留；新证据写入新隔离目录。

## R2-A · 登记历史读取失败或损坏必须拒发

**问题（反例实测）**：`_load_registry` 把任何 `OSError` 当作“无历史”、并跳过无法解析的记录 ⇒ 历史丢失后同键**再次派发**（`unreadable_at_load`、`corrupt_registry` 两例均再发 3 次调用，且登记字节被改）。

**整改**（`src/agent_session_codex.py`）：
- 新增 `read_registry_text(path)`：**文件确实不存在** → 返回 `None`（首次建立，允许）；**存在但不可读**（`OSError`）→ 抛 `RegistryUnavailable`，**绝不当作缺失**。
- 新增 `parse_registry_text(text)`：**任何一行无法解析或缺少必需键即整体不可用**并抛错；**不跳过、不清空、不修复、不截断**。
- `_load_registry` 捕获该异常→记 `_registry_problem`（保留原因），**不写任何字节**；`submit` 在任何外部调用与登记写入**之前**即 `RegistryUnavailable` 拒发。
- 不扩建恢复平台；不做多进程调度。

**正反控**：新登记首次建立可派发；健康重开同 run、0 调用、字节不变；读取注入失败与解析损坏均 `RegistryUnavailable`、**0 次调用**、**文件字节不变**。

## R2-B · 证据摘要绑定实际写入字节

**问题（反例实测）**：`_write_raw_evidence` 先按 **LF 文本**算哈希，再经 **Windows 文本模式**写入产生 **CRLF** ⇒ 声明的摘要描述的不是磁盘上的文件；且验证脚本只记录该元数据、**从不核对引用文件**。
旧证据（`data/jh020-final-roundtrip-20261018/raw/…`）：declared `48b186db…`（规范化文本）≠ 磁盘 `2281a545…`，CRLF 28 行。

**整改**：
- `_write_raw_evidence` 一次性编码负载，经 `write_bytes_file` **二进制**写入，摘要对**同一份字节负载**计算；登记 `{path, sha256, bytes, frames}`。
- 新增 `verify_raw_evidence(raw)`：读取引用文件，**实际核对字节哈希与帧数**。
- `scripts/verify_jh020_codex_session.py` 调用它并新增检查 `raw_evidence_file_bytes_match`——不满足则退出非零。**不再只查哈希长度或字段存在**。

**正反控**：正例断言 `sha256 == sha256(disk bytes)`、`bytes == 文件大小`、`frames == 行数`、无 CRLF；反例篡改一个字节 / 删除文件 / 帧数不符 → 均校验失败。

## 旧证据与语义标注

- 旧目录（`data/jh020-codex-session-20261018/`、`data/jh020-final-roundtrip-20261018/`）**原样保留、不改写**。
- 其摘要语义为**换行规范化（LF）文本**，与磁盘 CRLF 字节不一致——已在证据 README 与本报告注明；**不为迁就旧哈希改写历史文件**。

## 逐项反例结果

| 反例 | 整改前 | 整改后 |
|---|---|---|
| R2-A `healthy_reopen` | 同 run、0 调用、字节不变 | **不变** |
| R2-A `unreadable_at_load` | 再派发 3 次、字节被改 | **`RegistryUnavailable`、0 调用、字节不变** |
| R2-A `corrupt_registry` | 再派发 3 次、字节被改 | **`RegistryUnavailable`、0 调用**；文件仍是被破坏的原文 |
| R2-B 旧证据哈希 | declared ≠ 磁盘字节 | 新证据：**declared == sha256(磁盘字节)**、字节数/帧数一致、无 CRLF |

## 保持范围

未接 GUI 执行、未实现 continue/cancel、未发送指定审查会话、未新建替代架构师、未改 Hark 验证器；
`production complete`、`FileToolExecutor` 自动应用与现有成功门保持关闭。

## 验证

- 全量 **569 项 568 通过 / 1 既有跳过**（独立计数一致）；契约测试 48 项。
- 定向变异 **15/15 检出**（`errors=0`），新增 4 项覆盖 R2-A（读取失败、解析跳过）与 R2-B（文本模式写入复现原缺陷、跳过字节核对）。
- 真实只读往返（整改后）：脚本自检 `raw_evidence_file_bytes_match` 通过；提交后在新隔离目录复验一次。

## 状态

**候选/待独立验收**：R2 两项已修并有正反控与变异证据；**未验收**；**未宣布真实协作闭环通过**。指定审查会话仍未发送。
