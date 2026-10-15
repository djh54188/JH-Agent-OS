# 宿主交付契约（JH-005 草案，未验证）

日期：2026-10-14，2026-10-15 按 R1 修订。适用 N2：JH 生成并审核产物，
宿主（Codex 原生文件、测试、Git 能力）执行应用与验证。本文件只定契约，
不实现、不取证；FileToolExecutor 不复活，不自研 Shell/Git 平台。

## 角色与边界

- JH 侧：任务、预算、独立审核绑定、可信验收、证据记录。只读产物，
  不直接写用户目标文件；成功门槛不变。
- 宿主侧：按授权清单改文件、跑测试、查 Git 状态。宿主原生能力只保证
  “执行了命令”，正确性由本契约的对账记录证明，不由口头确认。
- Skill/流程文档是编排说明，不授予权限、不替代对账。
- 数据任务继续 N1 冻结流程，不加宿主写入条件；以下两步交付仅针对代码任务。

## 代码任务两步交付

`准备当前已审核产物 → 等待宿主 → 宿主应用并检查 → 控制层复核证据 → 最终成功`。

- 准备就绪（产物已审核、基线已记录、宿主尚未应用）绝不是 succeeded；
  成功只在控制层复核通过后确认。
- 等待期间任务保持现有 `reviewing` 状态，另设控制层等待标记；
  不新增任务状态，不借用其他状态含义；等待标记不在模型可填字段中，
  具体存储为后续实施项。
- 等待中的普通 resume 不得重放模型调用或宿主应用；接续保留原预算、
  期限、owner/attempt 与全部绑定。

## 准备/完成接口提案（仅设计，不新增生产命令）

```text
prepare_code_delivery(task_id, artifact_version) ->
  ok {delivery_id, baseline{git_commit, files[{path, sha256_before}]},
      expected_after{files[{path, sha256}]}, authorized_files[]}
  | blocked {reason: version-changed | scope-invalid | ...}

complete_code_delivery(delivery_id, apply_receipt) ->
  ok {target_applied: true}
  | blocked {reason} | uncertain {reason, reconcile: [...]}
```

- `expected_after` 必须由可信基线及当前已审核补丁确定；本批夹具还须
  等于封存参考字节。不接受宿主自报布尔、只测参考文件、仅看 diffstat
  或模型评分作为通过依据。
- `apply_receipt` 至少包含：调用/应用尝试标识、实际目标字节哈希、
  实际测试的目标文件与输入内容哈希、命令、退出码、输出摘要。

## 对账记录（每次应用必填）

```text
delivery_id, task_id, 调用/应用尝试标识,
artifact(id/version/SHA-256), review_id, review_verdict,
target_before/expected_after/actual_after 哈希, authorized_files[],
实际测试{目标文件哈希, 输入内容哈希, 命令, 退出码, 输出摘要},
result {applied | blocked | uncertain}, recorded_at, recorder
```

- `actual_after` 必须是应用后实际目标字节的哈希，且等于 `expected_after`；
  控制层必须读取/核对实际目标字节，仅凭行为检查通过不能交付。
- 可信宿主执行记录由宿主产生、随对账提交，控制层负责引用核对：
  命令与退出码须与记录一致，摘要须可追溯到原始输出；模型文本不是执行证据。
- `applied` 仅当全部成立：当前独立审核、全部三项代码必需验收、交付绑定
  完整、无不确定性。应用前发现版本变化即 `blocked`；副作用可能已发生或
  检查结果未知时 `uncertain`/`needs_attention`，不自动重放。

## 版本变化处理

应用前必须重读目标基线（Git 提交 + 文件哈希）并与任务记录比对；
不一致则阻断本次应用，不得盲目变基或覆盖。恢复路径只有两条：
基线未变后重试，或用新基线重新生成并重新审核（旧审核自动失效，
产物改变后旧审核不能继续作为放行依据）。

## 未知结果处理

检查命令超时、无输出、输出不可解析、副作用无法确认时，记 `uncertain`
并转人工核对；未知结果永远不折算成通过。取消操作保留已有关闭语义。

## 明确的非保证

- 普通“检查后写入”不声称无竞争窗口保证；窗口风险如实记录，不美化。
- 宿主原生能力不推定具备并发保护、审核绑定或权限证明；需要就写进
  对账记录逐项验证。
- 生成文本、审核通过、准备就绪都不等于文件已应用；`target_applied`
  只在对账记录完整后由宿主据实填写。
