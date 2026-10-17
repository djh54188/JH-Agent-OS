# N1 结项裁决与 JH-004 独立验收 · 2026-10-14

**批准 JH-004 与 N1 结项**，严格限 ADR-0004 的固定数据模板生成入口。N2/P1 未验收；自动文件应用未准入，门禁关闭。实现提交 c7d7421d485619ff7c0d31cd607052bcd637b530，分支 n1-artifact-entry。后续归档只改文档/证据。原树 80979f3 的失败 JH-015 工作保留，不作为已验收实现。

## 已验证事实

- 两项遗留 P1 历史来源覆盖、P2 Skill 测试措辞关闭。实际 185 项：184 通过、0 失败/错误、1 既有跳过，没有排除测试。8/8 定向变异由断言检出、零异常。提交后再跑全量与独立探针一致，源码哈希未变。见[提交后结果](jh004-postclosure-20261014/postcommit-results.json)。
- 实际 Codex CLI 0.160.1，正常 --approve-for-me（workspace-write + 自动审批），沿用模型/认证，未关闭沙箱、改全局配置或建子智能体。宿主在正确工作树发现并读取项目 Skill，预检 Git 根、分支/提交与实际 src.cli 来源。见[原始宿主事件](jh004-real-host-20261014/host-attempt-1/host-events.jsonl)。
- 同一任务五个 CLI 解释器进程实际执行 submit→status→resume→artifact→summary，退出码均 0；查询前后数据库字节一致，接续未重置预算、期限或来源。
- 真实任务 task_jh004_real_20261014_8b880432：Gemini 规划/生成、Muse 独立审核 pass、两项可信验收通过、succeeded。产物 v1，SHA-256 fc1c5e9f464008684d34341c0e9594663293fb16f3d96f13069cda9de093c136。根审查以字面期望 east10/east20/north40/south30 独立核对，没有复用执行者验收函数。
- 三条 completed attempt 均有匹配任务/attempt/角色且 is_mock=False 的来源事件，真实适配器/模型符合批准通道。用量 1473+853+3762=6088、3 turns、0 repair、0 未知；原上限 6 turns/16000 tokens/600 秒，终态在原期限内。审核关联当前 attempt/内容哈希，必需条件均有证据，无未确认副作用、待批或残留 owner。25 项[独立核对](jh004-real-host-20261014/independent-verification.json)通过；[原始数据库记录](jh004-real-host-20261014/database-records.json)与 SQLite 快照归档。
- 零模型工具调用；产物保存于 SQLite，target_applied=false，目标工作区为空。无工具拒绝与来源门禁的离线检查、真实无工具成功分别证明对应范围，不声称本次真实任务运行过全部失败分支。

## 资源、失败与限制

首次宿主启动因互斥参数在解析阶段退出 2，尚未调用模型或提交任务；错误保留。仅保留正常 --approve-for-me 后成功，没有扩大权限、换模型或重置任务。

JH 用量为 6088；宿主另报 input_tokens=445287（cached_input_tokens=395264）、output_tokens=10464、reasoning_output_tokens=4126（输出细分，勿重复加），宿主总耗时 328.145 秒。这是一次取证宿主会话的累计统计，不是日常固定成本；未测货币成本。不能将 JH 限额当整个主控开销，日常应复用当前会话并减少无意义上下文/调用。

真实调用经用户批准的本地代理；底层模型身份/容量以代理配置为依据，未独立验证供应商身份或压测容量。当前桌面 C 盘聊天未迁移；证据证明正确 D 盘工作树中的 Codex CLI 宿主，不声称旧桌面聊天已自动加载 Skill。

非阻塞兼容性记录：产物类型 data_conversion，但 ReviewResult v1 枚举无此值，控制层既有元数据仍为 code；不改变已验证的任务/attempt/内容绑定。列入 JH-005 分类/兼容设计，本批不擅改协议。单次固定模板成功不推出通用可靠性。

## N1 条件与后续

能力归属和入口 ADR 已明确；实际发起、查询、跨进程接续、返回产物/证据通过；生产无工具、固定模板与来源绑定通过。按 ROADMAP 的 N1 当前范围结项。Skill 是流程约定，JH 约束自身受控路径，未接管宿主全部工具。[官方 Skill 文档](https://learn.chatgpt.com/docs/build-skills)提供能力说明；验收依据为实际记录。

允许 N2 首批 JH-005 验收规格/夹具与宿主交付契约准备，按[执行交接](../docs/handoffs/jh005-n2-acceptance-20261014.md)。N2 代码修改/数据任务、宿主应用/测试、真实修复及异常验收继续要求，旧 E3/N1 不改名为 P1 完成。JH-015/016 STOP 不变。
