# JH-020 异构 Agent 首切片 · 2026-10-21

## 架构判断与本轮安排

将下一批目标转为首个非 Codex AgentSession 桥接，优先验证真实外部宿主可以执行工具并交付；不再以守卫或测试数量代表产品进展。819 是现阶段全项目测试总数，Hark 01–05 还包括 GUI 与历史查询能力，不全部属于固定标识试运行；但它们尚未完成异构 Agent 协作闭环。

本轮只规划及派发候选实现，不修改产品、规则或既有成功门。路线 N1–N8、模块化 GUI 与可替换 Agent 中间站定位保持；已有契约、ModelAdapter、只读查询和证据复用，不新建通用 Agent Runtime、Shell/Git 平台或工具循环。

## 派单状态与旧批处置

- 用户先前回答 Hark-派单-10 已发出；最新转发的 Hark 状态明确称尚未执行、仓库未改。本轮重新核对远端，Hark 分支仍为 `175f52b64ac3e9cfb9e409be8c2db3f20da7725f`。不由 Git SHA 推断消息是否送达，直接明确**作废旧 Hark-派单-10 整改单**，不再复用该编号。
- 最新 Hark 回报中的“正式派单10”建议不采纳编号复用；本轮新单为 **Hark-派单-11**，最终版以本轮聊天为准。生成不代表已送达。
- Hark09 原十项目录绑定按离线范围关闭；观测失败误报空、捕获非对象 JSON 异常和 Windows 测试输入仍为已确认未修尾项。冻结为未验收候选及已归档问题，不恢复“通过”，暂不安排 Windows 产品集成或再次真实 GUI 调用。它们不阻塞独立稳定基线上的 Gemini 桥接候选。

## 已核对的依据

1. [OpenAI 官方 Subagents](https://learn.chatgpt.com/docs/agent-configuration/subagents) 的 Custom agents 部分明确：可定制模型设置和指令，但继承父会话的 permissions、provider、MCP；custom agent file 中的 model_provider 不覆盖父设置。因此不能仅靠当前 Codex 的子 agent 配置把 provider 换成 Gemini。本结论针对该配置机制，不声称否定所有第三方接入方式。本机 npm Codex 为 0.160.1，未运行真实子 agent 验证。
2. [Gemini CLI headless](https://geminicli.com/docs/cli/headless/) 提供 init/session/model、message、tool_use、tool_result、error、result 事件。[CLI 文档](https://geminicli.com/docs/cli/cli-reference/) 提供 resume；这只是后续接续探测依据，不作为本批已支持能力。
3. [Gemini CLI 策略引擎](https://geminicli.com/docs/reference/policy-engine/) 有实际 deny/allow 规则与 plan 模式。该版本文档特别注明 workspace-tier policies 当前不生效；不能仅在工作树中放策略文件就宣称只读，需按固定版本验证实际生效入口。worktree 隔离 Git 改动，不等于操作系统只读沙箱。
4. 本机 PATH 只读检查未找到 gemini 命令；这是 PATH 检查结果，不等于全盘证明未安装。仓库历史 Gemini ModelAdapter/localhost Anthropic Messages 代理通道不等于带工具的 Gemini Agent 宿主，也不证明其凭据可供 Gemini CLI 使用。未读取或上传凭据，未访问模型端点。
5. `src/agent_session.py` 已提供 SessionAddress/RunBinding/SubmitRequest/CollectedResult 等；真实 Codex 适配器存在，Gemini API 适配器不是 AgentSessionAdapter。原模块的 DESIGN ONLY 注释有历史时态，不据此推翻后续适配器验收；本批不顺带修改该契约。

## 一个能力：Gemini CLI 首次任务派发与工具结果收集

代码基线采用云端 n1 已接受的 `b07213d89874a82bce9003cb3fb0b2348f0dcae2`，不采用旧 main，也不继承未验收 175f52b 的 session_probe。新分支 `hark-gemini-subagent-20261021`。基础测试计数应从该基线重新读取，不能直接沿用另一分支的 819。

目标是薄适配器驱动已有 Gemini CLI 的工具循环，提供 capabilities/submit/status/collect，供下一批本地真实执行使用。首任务在调用方提供的独立 worktree 中读文件和搜索，返回源码定位/分析与原生工具证据；不是固定标识回显，也不是 API 模型提出动作后由 JH 自建循环执行。

首批不实现 continue/cancel、不加一般 Shell/测试工具、不接 GUI/Muse/自动审查或新调度器。它们分后续切片；官方支持 resume 不等于本适配器已验证接续。未支持能力按既有契约明确拒绝。

### 云端模型与安装政策

Hark 云端真实 Gemini/其他模型调用为 **0**；不提供、申请、上传或读取用户 key，不调用本机 localhost 代理。允许在隔离临时环境安装/查看固定版本的官方 CLI、源码、help 和 schema，作不需模型的探测与进程替身验证；不更改用户全局配置，不跟踪依赖缓存。

模型为显式配置，区分请求名与宿主实报，不把历史代理别名硬编码为原生 CLI 型号。实际账户、模型可用性、调用额度及 Windows 只读策略在本地真实阶段另核；云端回放不声明“Gemini 已运行”。

### 允许改动与有限偏离

新增 Gemini CLI 会话适配器、直接相关离线夹具/测试、一个本地验证入口、适配器使用文档与无密钥示例配置。不修改既有 Codex 适配器、试运行入口、历史校验、GUI、契约、预算或成功门。

若新增真实适配器必须纳入 JH018 契约消费者白名单，准许**仅新增这一实际继承 AgentSessionAdapter 的消费者**，单独偏离提交并保留原扫描及继承检查；不得借机放宽其它模块。实际评审仍需复核该偏离。

### 完成判据与风险分级

- 首先固定官方 CLI 版本和可复查的原生事件格式，区分文档、无需模型的宿主实测、假进程/回放和待 Windows 真实确认；不能凭自创 JSON 声称原生兼容。
- 从同一个现有契约调用得到原生 session/model、工具请求/结果关联、最终声明、用量及缺失项，原始字节归档并回读核对。缺失/冲突不能补零或虚构；超时未知不自动重发，完成声明不映射任务成功。
- 仅允许目标 worktree 内读文件/搜索，写入、Shell、额外扩展和权限升级不可因提示词或模型请求被放开；限制由宿主实际配置负责。若固定版本无法做到，交付明确阻塞，不另造工具执行平台。
- 用最小集覆盖一次有效交付、未知不重发、关联错误/证据缺失和只读拒绝，检查工作树未被改写。新增关键守卫做相应定向削弱，不按新增守卫数量设通过目标，不扩到所有文件系统/全量竞态/断电恢复。
- 执行方跑适用完整回归、独立计数及提交后复验，报告 Linux 已有失败集合变化；未授权真实调用，真实路径明确待验证。候选复核后再安排本地一次真实、有任务价值的工具执行。

## 下一里程碑

最近的真实价值里程碑是：Codex 发出源码定位任务 → 外部 Gemini Agent 使用读/搜索工具 → 返回关联证据 → 当前 Codex 独立复核。达到这一项后，才追加同会话一条后续指令，再单独接入独立审查对象。Muse 若作为辅助审查端须另核可用性，不隐式替换当前指定 Codex 架构师。

轻量验收意味着缩小入口、工具与验证范围；不降低来源真实性、权限、未知不重放和独立验收底线。运行测试并非天然只读，worktree 本身也不是权限隔离，因此 Shell/测试工具后移。

## 路由与发布

本轮只归档规划及交接，发布到已有独立复核分支；不推本地未验收产品到云端 n1，不修改 Hark 产品分支/main/PR#1，不更改全局/项目规则。执行AI-派单-08暂不发行；Hark-派单-11生成尚未确认送达。下一新编号为执行AI-派单-08、Hark-派单-12。实际发布核对记录留本地 data/jh020-gemini-bridge-planning-20261021/sync.json。
