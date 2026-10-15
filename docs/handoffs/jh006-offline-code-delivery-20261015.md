# 发给 Muse：JH-006 首批，两步代码交付的最小离线实现

日期：2026-10-15。JH-005 的规格与正式夹具已通过，N2/P1 尚未完成；不是宿主应用能力已经验证。当前已接受需求见 docs/n2-acceptance-spec.md 与 docs/host-delivery-contract.md，证据见 reports/jh005-acceptance-20261015.md。

## 接手与范围

工作树 D:/JH-Agent OS/data/worktrees/n1-artifact-entry，分支 n1-artifact-entry，先查最新已提交 HEAD 与干净状态；旧 bfc7839/c4ac80e 是历史检查点，不回退。原树 80979f3 失败 WIP 保留，不复制/合并。读取 STATUS/TODO 和上述规格后直接实施本批，不再改写整套需求。

只实现固定 code_sortfix_v1 的无工具补丁生成、审核、准备/完成交付及对应离线回归。允许修改必要的 src/、CLI、SQLite 持久化/迁移和新增有意义的 tests/ 测试；不改 Task/Review/Artifact Schema、不新增权限或供应商、不启用 FileToolExecutor，不自研 Shell/Git/MCP/UI/常驻服务。records_transform_v1 的模板、两条件及生产/fixture 来源门槛保持既有语义。

## 已裁决的实现要求

1. 代码夹具对应单一授权 target/widget.py；输入基线与现有封存清单有确定绑定。模型只产生既有 Artifact v1 的 patch_text，审核分类用既有 patch。只支持本夹具可确定重建的一文件补丁；未知格式/多文件/范围外内容受控拒绝，不为此建设通用补丁或工具平台。

2. prepare_code_delivery 接收/校验当前产物版本，复用真实来源、独立审核及内容/attempt 绑定。控制层从可信基线和当前补丁独立确定 expected_after，本夹具还必须等于封存参考字节。保留实际 before 哈希、授权路径、task/attempt/artifact/review 与 delivery_id；任一不一致就拒绝，准备就绪绝不是 succeeded。

3. 在现有 reviewing 状态保存控制层拥有的等待标记，记录必须可跨进程查询。等待不是新 Task 状态，也不是模型可填元数据授权。普通 resume 在等待中只返回明确待宿主状态，不调用模型、不重放应用。保留原预算、截止时间、owner/在途/未知门；不扩大时限或重置用量。代码的三项必需验收必须最终全有证据，而数据仍走冻结流程。

4. complete_code_delivery 只接收可信宿主记录并读取/核对授权目标，不由 JH 写目标文件或运行任意 Shell。验证当前 task/delivery/调用与应用尝试、产物版本/哈希、当前有效 review、before/expected_after/actual_after、实际测试目标/输入哈希、固定命令/退出码和原始记录引用。模型文本、receipt 布尔、评分、diffstat、参考文件上的测试都不能使任务成功。明确宿主记录的可信装配来源，不把自报 provenance 当证明。

5. 实际目标字节必须等于当前审核补丁的期望字节；全部三项验收与当前独立审核及无不确定性满足后，才允许控制层转 succeeded/确认 target_applied。前置版本变动 blocked；副作用可能已发生、执行记录/检查结果不明则 needs_attention，未知不重放。重复/迟到/跨任务完成、旧审核/旧版本及正在被其他 owner 占用时均受控处理；不能按一个旧 PID 接管。

6. CLI/Skill 最小封装提供准备、查询与完成调用；输出区分 generated_artifact、等待宿主与已验证应用。不改变用户审批语义；Skill 不自动 approve/recover。完整宿主命令执行/真实模型取证留到本批离线独立审查之后，当前不调用真实 API、不写用户文件。

## 验证与交付

使用明确标识的离线适配器和隔离临时工作区；合成宿主应用/记录必须标 fixture，不是生产或真实取证。测试至少覆盖：等待不成功且 resume 零新调用；合法完整完成；跨进程准备/查询/完成；错误或附加字节（即使行为通过）；错目标/检查脚本/审核/attempt/版本；缺失或伪造来源证据；预算/期限耗尽、owner/在途与未知、重复/迟到完成、取消。新增测试放正常 tests/，不在封存 fixtures 下新增 test_*.py，不改封存夹具来迎合实现。以外部状态/调用数/数据库记录为断言，不只检查实现函数返回值。

运行既有全量（基线185项）加新增回归，JH-005 核对/14变体及相关控制守卫；新增成功/绑定守卫做变异检查，脚本异常不能算检出。只做必要测试，不无意义重复真实调用。按明确范围提交并提交后验证，直交当前架构师兼审查会话。

Shell 不可用时完成允许的文件实施并准确列未运行项，由架构师承接执行；不要反复只交计划包。没有新方向/权限/协议/验收变化或真正外部输入，不重复申请已裁决事项。不得自行宣布 N2/P1 完成或开始真实宿主应用。
