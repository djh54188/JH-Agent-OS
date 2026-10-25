# Hark-派单-21 任务包 task01：独立审阅基准（仅供架构师，不进入模型工作区或 stdin）

被分析源码：提交 f6bf47c59688c43d927dede01b59f772520815d8 的 src/agent_session_gemini.py，Git blob 31d3cd7c111dfdd0d33a65ad097f9cbe3d0fac38。行号以该 blob 为准。
本基准用于人工语义审阅。check_run.py 只做机械一致性核对（引用位置是否落在下列函数/焦点范围），不判断解释是否正确，也不给出任务通过结论。

## 核心审阅点（必须全部覆盖且含义正确）

B2 登记不可证明时粘滞拒绝（结果类：A 拒绝派发）
- GeminiCliAgentSessionAdapter._reload_locked（1062–1143）：前缀被截/改（registry_prefix_intact，1069）、文本不合法（registry_text_problem 914–924，调用于 1073–1076）、不是合法历史（validate_registry_records，约 1083–1085）、runs/ 下有登记无法解释的运行目录（unaccounted_run_traces 879–911，调用于 1086–1093）。任一成立即设置对象级粘滞 _registry_problem，之后提交以 RegistryUnavailable 拒绝。
- 正确含义要点：空、零字节或缺尾的登记不被当作“从未派发”；不修复、不重建、不补发。

B3 重开后未收集的运行标为未知（结果类：C）
- _reload_locked 焦点 1134–1141：不是本对象存活进程、未收集、状态为 queued/running 的运行置为 unknown，native_state=outcome-unknown-after-reopen。
- 正确含义要点：不重发；collect 对此类运行返回 unreported（1365–1368），不会重新启动。

B4 幂等重放与冲突（结果类：B，及冲突拒绝）
- _replay_locked（1218–1233）：存在粘滞问题即 RegistryUnavailable（1221–1222）；同键同指纹返回已有运行状态并注明 “idempotent replay; not re-dispatched”（1233）；同键异指纹 IdempotencyConflict（1228）。
- submit（1235–1259）：入口先查粘滞问题（1237–1238），版本探测后在锁内重判（1256）。
- 正确含义要点：返回的是已有运行的当前状态（可能是 unknown），不是新派发。

B6 先登记后启动（结果类：A，写不成时）
- _dispatch_locked 焦点 1286–1297：reserved 记录在启动外部进程之前写入（1292–1295）；写不成以 RegistryUnavailable 拒绝，零派发（1296–1297）。
- 正确含义要点：进程启动之前 run 已有 reserved 记录，因此其他读者看到的是可判定为未知的预留，而不是空白。

B7 已启动但 dispatched 写失败（结果类：本对象后续 A；其他读者 C）
- _dispatch_locked 焦点 1318–1333：_persist_locked("dispatched") 抛 OSError 时只设置粘滞 _registry_problem（1324–1330），当前调用照常返回 RunStatus（state=running，native_state=process-started）。
- 正确含义要点（三层必须区分）：
  1. 当前已启动的这次运行仍是 running，进程不会被杀；本对象之后仍可对它 status/collect。
  2. 同一适配器对象之后的任何 submit 因粘滞问题被拒绝（RegistryUnavailable）。
  3. 其他读者或重新打开登记的对象只看到 reserved 记录：该 run 未收集，按 B3 保守标为 unknown，不重发。
- 错误理解示例（应判语义不通过）：说会自动杀进程、自动恢复/补写登记、自动重试或再次派发；或把三层混为一谈；或遗漏 B7。

## 辅助点（加分，不强制）
- B1 构造时（__init__，1028–1049）：登记或 runs/ 存在即在锁内判定，异常转为粘滞问题。
- B5 submit 两次判定，见 B4。
- B8 collect 有界等待超时（1371–1376）返回 unreported、不重发。
- B9 registry_lock（952–982）超时 RegistryBusy，不无锁继续。

## 语义审阅提示
- 机械核对只说明引用位置存在并落在范围内，不说明解释正确。架构师须逐条对照上文“正确含义要点”。
- 交付若把任何防线说成“自动重发/重试”，即使引用位置全对，也应判语义不通过。
