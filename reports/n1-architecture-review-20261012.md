# N1 架构裁决与 JH-002 独立复核

日期：2026-10-12（Asia/Shanghai）  
检查基线：分支 `phase4-file-tools-batch1`，HEAD `ec11584`；代码 `50a39de / 6956300 / db86bd6`。检查开始及离线探针结束时工作树均干净。

## 裁决

| 事项 | 裁决与适用范围 |
|---|---|
| 旧 E4 通用 Shell/Git 工具平台 | 取消原开发计划；日常 Shell、测试、Git 交由宿主 Codex 原生工具，遵守宿主现有权限 |
| FileToolExecutor | 批准“跨模型产物暂存、审核绑定、受控应用”这一最小职责；不批准当前实现通过验收或用于真实工作文件 |
| N1 接入形态 | 采用项目级 Skill + 结构化 CLI，Codex 主控调用 JH；见 ADR-0003 |
| JH-002 | 需整改，新增 JH-015 跟踪下述五项；历史正常闭环证据保留，但不证明边界不变量成立 |
| JH-003 | 能力归属方向通过；安全能力描述以本次复核限制为准，不能把模块存在视为能力已达标 |
| JH-004 | 可先做 ADR、只读查询、可信装配方案准备；通过 JH-015 独立复核后，接入并验证文件写入链路 |
| N1 结项 | 暂不批准；仍需修复复核与实际 Codex 内发起、查看、接续、接收结果的证据 |

执行方提供事实与实现，不决定产品边界、权限、验收标准或阶段结项。架构师依据需求、代码和可复核证据作出上述裁决。

## 本轮实际验证与限制

- 独立检查相关源码、CLI 装配、历史 JSON 报告及只读 SQLite 证据。
- 实际运行 [离线探针](n1-architecture-probes-20261012.py)；[结果](n1-architecture-probes-20261012.json)中的五项 `defect_reproduced` 均为 true。
- 探针使用项目下独立 UUID 临时目录；“工作区外”指同一临时根内、模拟工作区的兄弟目录，未触及真实用户文件。junction 是真实 Windows junction；竞态使用确定性钩子模拟交错，并非随机压力测试。
- 审核绑定探针使用 Mock Worker/Reviewer 及真实控制层、真实文件执行器，未修改风险策略；它证明控制层接受错误落盘，不证明真实 Muse 会审核错误内容。
- 没有修改共享实现、调用 API、重跑全量单测或既有变异守卫；临时探针目录已清理。执行方报告的 127 项（126 通过、1 跳过）与六套守卫不是本轮独立重跑结果。
- 历史真实文件闭环报告的 succeeded、6401 tokens、四项门禁和目标哈希能对上；这条正常路径证据仍保留。未重跑实时模型或确认当前供应商健康。
- 本轮发现不撤销旧 E1—E3 历史结项，也不扩大新阶段范围。

复现命令（Windows、本项目目录）：

```powershell
python -B reports/n1-architecture-probes-20261012.py --repo 'D:\JH-Agent OS' --temp-base 'D:\JH-Agent OS\.test-tmp'
```

脚本输出“缺陷是否复现”，退出码 0 仅表示探针跑完，不代表产品通过；整改后应新增断言安全不变量的正式回归测试。

## 五项阻塞发现

| 编号 | 基线位置 | 实际观察 | 必须恢复的不变量 |
|---|---|---|---|
| ARCH-01 | src/file_tools.py:185–219、288–295 | 暂存祖先为 junction 时返回 PathBoundaryError，但模拟工作区外文件已由 KEEP_OUTSIDE 截断成空文件 | 拒绝前不能创建或修改工作区外对象；检查必须覆盖暂存、目录创建、备份、临时文件和替换 |
| ARCH-02 | src/file_tools.py:383–394 | 固定 `.jh_tmp` 名碰到已有邻居文件；apply 成功，邻居文件消失 | 非授权邻居不能被截断、替换或删除；临时资源必须隔离、排他创建 |
| ARCH-03 | src/file_tools.py:354–394 | 版本检查后并发写入 CONCURRENT_UPDATE，随后被候选覆盖且返回 applied=true | 版本对账必须在实际修改时有效；不能把原子 rename 当作版本比较交换 |
| ARCH-04 | src/file_tools.py:383–400 | 临时文件写完后更换父目录为 junction；apply 成功修改模拟工作区外目标，原工作区目标未变 | 文件系统身份与工作区归属应覆盖整个应用动作；检查后路径变化不能使写入逃逸 |
| ARCH-05 | src/orchestrator.py:434–502、992–1015 | 控制层验收及审核看到 B，最终应用 A，任务仍 succeeded；审核 SHA 与落盘 SHA 不同 | 应用必须绑定当前可信审核的 task/attempt/产物版本与哈希，而不是历史 stage 记录或路径集合 |

ARCH-01：`os.open(..., O_TRUNC)` 先产生副作用，后做 realpath 检查，事后报拒绝无法撤销该副作用。  
ARCH-03/04：额外插入一次 exists、realpath 或哈希检查并不自动消除检查与使用之间的窗口。须提出可信的操作系统约束、文件身份/句柄或排他机制，并证明它覆盖相关写入者；仅进程内锁无法排除外部写入。无法可靠支持的操作应拒绝执行或明确升级架构师裁决，不能静默降低既有边界。  
ARCH-05：两次 validate_review 并未阻止探针；`_apply_staged_writes` 取历史工具结果中的 content_sha256，而只核对 staged_path 是否在 artifact_refs。修复须将实际候选字节、审核绑定与最终写入对齐，恢复流程同样适用。

## JH-004 的最低接入要求

现有 CLI 不能直接被包装成“已具备生产接入”：

1. `create_orchestrator` 使用默认工具/验收装配；默认工具为 Mock、目标范围校验默认关闭，部分配置缺失会退回 Mock Worker。生产入口必须显式装配可信工作区、已批准模型角色、任务类型、工具模式及可信验收器。真实模式缺配置应拒绝，不以 Mock 结果冒充真实成功。
2. 工作区和权限配置来自可信本地装配，任务 JSON、模型文本和 Skill 文案不能扩大权限。宿主 Codex 原生工具仍受其原生权限约束，JH 不宣称能强制管控宿主的一切操作。
3. `status/events/artifact` 当前也经过建库/迁移和 Orchestrator 初始化。只读查询应独立走只读路径；缺数据库/任务时返回结构化结果，不建库、不初始化模型、不触发执行。
4. 需支持后续 CLI 进程继续已有任务，沿用持久化预算、截止时间、授权、验收器和所有权；N1 允许同步、有界调用，不建设守护进程或 N7 队列系统。不得用全局 recover 当普通 resume。
5. Skill 自动调用面不包含自授权 approve。现有 operator approve 如保留，应明确其受信任本地操作员边界；仅传 approver 字符串或模型声称“已批准”不构成人工授权。
6. JSON 输出区分命令读取成功与任务 succeeded/failed/awaiting_approval/needs_attention。异常和退出码含义明确；文件任务摘要包括目标/暂存/落盘哈希、当前审核、验收、预算和阻塞信息。
7. N1 只需一个当前验收器真正支持的代表任务。任意代码任务的验收扩展在 N2；不依靠模型生成“预期结果”或模型评分放行。
8. 在真实 Codex 项目会话中验证 Skill 能被发现并实际调用 CLI；仅文件存在、Python 直接注入依赖、Shell 的 workdir 为 D 盘均不能替代此证据。聊天原绑定 C 盘旧目录这一差异须处理或如实列为外部待办。

## 方案比较更正

Skill + CLI 是现有桥接成本最低的候选；选择它不代表独立协议工具一定需要常驻 daemon。MCP 支持 stdio 子进程，与 HTTP 传输有别。是否保留现有 FileToolExecutor 也不能仅用“否则只能人工复制粘贴”来证明；宿主工具配合可信整合机制仍是可能的替代，当前不另建该替代。

官方依据：
- [Codex 自定义与项目技能](https://learn.chatgpt.com/docs/customization/overview)：项目技能使用 `.agents/skills`，技能说明引导流程，不替代执行时校验。
- [Codex CLI 参考](https://learn.chatgpt.com/docs/cli/reference)：MCP 同时支持 stdio 与 HTTP 形式。

## 实施与复核顺序

先完成 JH-015 的五项修复和外部可观察回归，再由独立审查核验；等待期间可并行完成 JH-004 的 ADR、Skill 草案、严格只读查询/摘要及其离线测试、可信装配方案。通过后完成可信 CLI 装配及真实 Codex 内最小接入，申请 N1 结项。期间不建设通用 Shell/Git、独立 UI、常驻服务或无界 Agent 调度。

两方统一使用 [执行交接](../docs/handoffs/n1-20261012-execution.md) 与 [审查交接](../docs/handoffs/n1-20261012-review.md)；新阻塞单列，常规修复不要反复请求架构师批准。
