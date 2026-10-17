# 阶段 2 控制层代码审查与补强

日期：2026-10-11（Asia/Shanghai）。现场检查点：`1de0a71`；补充规格的原始基线为 `08aae16`。

## 结论与边界

本次完成阶段 2 离线补强：30 项补充规格中，29 项适用于阶段 2 且通过，OPT-14 的真实 junction、文件写入竞争窗口验证留在阶段 4。全量单元测试运行 79 项，78 项通过、1 项按阶段跳过。新增 14 项变异均触发断言失败，未以运行异常充当检出。

本结论不代表真实模型与真实工具闭环已经通过。本轮没有调用真实供应商、执行真实文件工具或进入阶段 3/4；历史 `phase2-verification.json` 保留为此前验证记录。输入 token 预留仍为估算，不提供硬 token/美元成本上限保证；HTTP 层落实单次请求超时，尚无独立进程级网络截止 watchdog。实际任务须注入可信验收器，缺失时进入 `needs_attention`；测试中的 `mock_acceptance` 仅为显式夹具。

## 审查发现与修复

| 级别 | 原实现缺口 | 本次行为 | 验证 |
| --- | --- | --- | --- |
| P1 | Reviewer 的 `pass` 可直接导致成功，未核对验收证据 | 最终写入核对当前独立审核、逐条验收证据、未确认副作用、未知用量和待审批动作 | OPT-01—03、07；缺失验收器/未确认副作用测试 |
| P1 | 合法格式的审核未绑定任务、尝试和内容 | 控制层生成本次 review ID，持久化 attempt ID 和 SHA-256；向 Reviewer 提供实际快照；结果关联检查与当前内容复核 | OPT-04—08；旧 review ID 测试 |
| P1 | 只按 request ID 授权，批准后改参数可沿用批准 | 完整动作指纹绑定授权；请求和授权存入 SQLite；审批与审计事件同事务 | OPT-15；审批事件回滚测试 |
| P1 | 模型容量和任务预算混用，传入 timeout 在部分 HTTP 适配器被忽略 | 每次调用扣除输入预留、限制输出及超时；修复共用原截止时间；结果返回后再次检查预算 | OPT-17—20、22；三种 HTTP 适配器传输参数测试 |
| P1 | 未知用量后仍可能继续，混合已知/未知用量可显示部分总数 | 总用量保持 NULL，阻断下一次调用；非法负数、布尔值和字符串用量按未知处理并停止 | OPT-21；混合用量、非法用量测试 |
| P1 | 状态更新和事件写入各自提交；没有模型调用的预写记录 | SQLite 外层事务统一提交状态与事件；先登记模型尝试，再接受响应；不确定尝试禁止重放 | OPT-23—24；崩溃后直接重跑测试 |
| P1 | 同一任务可能重复领取、完成事件重复计费；工具记录可被覆盖 | SQLite 原子领取、attempt 完成去重；已完成工具请求复用确认结果，参数冲突拒绝 | OPT-25—26；两个调度入口并行测试 |
| P2 | 终态收到请求、取消与响应交错处理不完整 | 拒绝终态工具请求并留事件；取消不被迟到响应覆盖；恢复不依赖 PID | OPT-16、27—28；执行/审核期间取消测试 |
| P2 | 未知工具默认按只读处理，空目标范围未被约束 | 无法判定的工具或写入目标等待授权；空的授权文件集合不等于无限授权 | 额外策略测试 |
| P2 | 原真实 Reviewer 验证脚本把部分非成功状态也视为通过 | 集成用例必须同时 `succeeded` 与 `pass`；明确标记工具和验收器是 Mock | `scripts/verify_phase2.py`；本轮未重跑真实调用 |

已有按路径组件判断工作区归属、危险动作优先级和规划深度独立判断的实现予以保留，新增 OPT-09—13 验证。没有修改既有危险动作策略、路线图或协议 Schema 的措辞。

## 架构约束

- `orchestrator.py` 组织调用与状态转换；`control_guards.py` 计算调用额度、校验快照、审核关联和验收证据；存储层对最终成功再次把关。
- `task_control` 保存截止时间、执行所有者、当前尝试及审批；`model_attempts` 保存调用前记录和结果；`review_bindings` 绑定审核、尝试、产物与验收证据。新增表兼容现有任务表，旧任务没有可信截止时间时不会凭空获得新预算。
- 单任务领取用于阻止两个入口重复执行，没有引入并发调度平台。`recover` 仅适用于旧执行器已停止后的独占启动恢复，不提供运行中跨进程接管。
- Schema 校验只负责消息形状；格式合格、模型自评分、工具返回成功均不能单独触发 `succeeded`。

## 验证与复现

逐项结果、源码 SHA-256、运行计数及变异记录位于 `reports/phase2-hardening-verification.json`。原规格文件仍是规格，没有把其 `not_run` 字段改写成历史上已经运行。

从项目目录执行：

```powershell
New-Item -ItemType Directory -Force -Path .test-tmp | Out-Null
$env:TEMP = Join-Path (Get-Location) '.test-tmp'
$env:TMP = $env:TEMP
$env:PYTHONUTF8 = '1'
python scripts/verify_phase2_hardening.py
python scripts/verify_phase2_guards.py
python scripts/verify_responses_guards.py
```

本机 Python 3.14 的 `TemporaryDirectory` ACL 与 Codex 受限令牌冲突，测试经自动审批后在普通本机权限下运行，临时文件位于项目的 `.test-tmp`。原有阶段 2 守卫 5 项、Responses 守卫 8 项亦通过；它们的计数与新增 14 项分开报告。

Responses 输出额度包含可见输出和推理 token；本次沿用供应商返回的总用量口径，不再额外叠加推理 token。[OpenAI 官方文档](https://developers.openai.com/api/docs/guides/reasoning)
