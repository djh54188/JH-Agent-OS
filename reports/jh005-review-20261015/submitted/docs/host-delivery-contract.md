# 宿主交付契约（JH-005 草案，未验证）

日期：2026-10-14。适用 N2：JH 生成并审核产物，宿主（Codex 原生文件、
测试、Git 能力）执行应用与验证。本文件只定契约，不实现、不取证；
FileToolExecutor 不复活，不自研 Shell/Git 平台。

## 角色与边界

- JH 侧：任务、预算、独立审核绑定、可信验收、证据记录。只读产物，
  不直接写用户目标文件；成功门槛不变。
- 宿主侧：按授权清单改文件、跑测试、查 Git 状态。宿主原生能力只保证
  “执行了命令”，正确性由本契约的对账记录证明，不由口头确认。
- Skill/流程文档是编排说明，不授予权限、不替代对账。

## 对账记录（每次应用必填）

```text
delivery_id, task_id, artifact(id/version/SHA-256), review_id, review_verdict,
target_baseline {git_commit, files[{path, sha256_before}]},
authorized_files[], actual_diffstat, post_apply_checks[{command, exit, digest}],
result {applied | blocked | uncertain}, recorded_at, recorder
```

`applied` 仅当全部成立：审核为当前产物版本的 pass、无阻塞问题；
基线在应用前重读一致；实际 diff 只触及授权文件；
应用后检查命令退出码与预期一致。任一不成立即 `blocked` 或 `uncertain`。

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
- 生成文本、审核通过都不等于文件已应用；`target_applied` 只在对账
  记录完整后由宿主据实填写。
