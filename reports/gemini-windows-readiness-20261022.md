# Gemini Windows 真实工具任务：前置核查与验收准备

日期：2026-10-22，Asia/Shanghai。性质：架构师独立准备材料，**不是执行AI-派单-08，不授予安装、登录、真实模型调用或产品集成权限**。

## 结论与适用范围

基础运行环境已有，Gemini CLI、实际鉴权和真实工具执行尚未就绪 / 未核实。Hark-派单-12候选仍须先复核，不能因这份清单提前进入真实路径。本轮只读核对环境与固定源码，保存报告和证据；没有安装包、启动 Gemini、登录、读取凭据、调用模型、改产品或跑完整套件。

本机 HEAD 仍 `03d9a0224fd93917ed3a1b9d514e465dfc996825`。Hark12发送状态未确认；不假设其已经开工。本轮未发行新派单，下一新编号仍执行AI-派单-08 / Hark-派单-13。

## 已核实的本机条件

原始记录：`data/gemini-windows-readiness-20261022/local-observation.json`。其中配置路径仅检查存在性，没有读取内容或任何 auth/key 文件。

| 项目 | 本轮观察 | 能说明什么 / 不能说明什么 |
|---|---|---|
| 系统 | Windows 11，build 26200，AMD64 | Python platform 实报；CIM 查询被当前环境拒绝，不将它解释为产品失败 |
| Node | `D:\node_global\node.exe`，v24.19.0 | 满足固定官方 CLI 0.65.0 的 Node >=20 声明；尚未证明该 CLI 在本机运行正常 |
| npm/npx | `D:\node_global\npm.ps1/.cmd`、npx 对应入口可解析 | 找到了安装工具入口；未调用 npm 安装或读取 npm 配置，不声称依赖已就绪 |
| Git | 2.55.0.windows.3 | Git 可运行；本轮未创建 / 切换 / 合并工作树 |
| Python | 默认 3.14.7，另有 `D:\Python312\python.exe` 3.12.10 | 两个解释器都可运行；不自动切换项目解释器，不声称 3.12 已跑过产品回归 |
| Gemini CLI | PATH 无 gemini；已查五处常见 package.json 均不存在 | 当前入口未找到；不是全盘证明从未安装 |
| Windows 系统 Gemini 设置 | `C:\ProgramData\gemini-cli\settings.json` 和 system-defaults.json 当前均不存在 | 默认路径的存在性结果；不覆盖未来安装 / 系统管理变更，也不能当作权限实测 |
| 目标树配置 | 当前树没有 `.gemini`；目标树至 D:\ 的 `.env` / `.gemini/.env` 检查均不存在 | 未发现候选预检明确拒绝的这些布局；未来独立 worktree 仍须重新检查 |

已查安装位置为 `D:\node_global\node_modules`、其 lib/node_modules、用户 Roaming/npm、Local/npm 和 Program Files/nodejs 的 @google/gemini-cli/package.json。没有遍历用户凭据目录，也没有调用用户代理。

官方版本依据：[0.65.0 package.json](https://github.com/google-gemini/gemini-cli/blob/v0.65.0/packages/cli/package.json) 的 engines.node 为 >=20。只固定本项目候选已核对的版本，不自动采用 latest 或用全局 npx 临时下载替代固定安装。

## 鉴权需要分开的两个事实

官方支持 Google 登录、Gemini API Key 和 Vertex AI；headless 可使用已有缓存的认证，缺少认证时另配置其支持的环境变量路径。[官方鉴权文档](https://geminicli.com/docs/get-started/authentication/#running-in-headless-mode)

但是，新适配器给子进程设置隔离的 `GEMINI_CLI_HOME/HOME/USERPROFILE`。0.65.0 的 [homedir 实现](https://github.com/google-gemini/gemini-cli/blob/v0.65.0/packages/core/src/utils/paths.ts) 优先使用 GEMINI_CLI_HOME；[Storage 实现](https://github.com/google-gemini/gemini-cli/blob/v0.65.0/packages/core/src/config/storage.ts) 将 Gemini 配置与 OAuth 缓存定位到该 home 下的 .gemini。

因此推断：普通浏览器已登录 Google、或普通用户 home 中可能有 CLI 缓存，都不能直接证明此隔离适配器已鉴权。**本轮没有探查这些缓存是否存在或有效。**

候选通过后，由用户选择已有授权的认证方式：如果采用 Google 登录，用户应在拟使用的隔离 home 内完成官方流程；如果采用 API Key，变量只交给本地进程，不写入报告 / 聊天。不得迁移或上传正常用户 home 的 token 文件。先确认模型与额度可用，再另行授权真实任务；不从旧 ModelAdapter 或 localhost 代理推导 CLI 账户权限。

这两条只是后续选择，不在本轮发起登录或索取密钥；模型名也不沿用示例值当作当前账户可用性证明。

## 候选通过后的最小验证顺序

1. **固定候选与隔离布局**：在真正工作树之外准备调用方独立 worktree，固定 Hark12复核接受的完整提交。CLI 包、home 和证据必须与目标工作区分开；记录绝对路径，不改变原树 WIP、本地 GUI 或旧证据。具体路径由执行AI08根据当时现场确定，本轮不创建。
2. **零模型宿主确认**：固定安装官方 0.65.0 到 D 盘项目受控目录，回读版本及包来源；不用全局配置。先做无需模型的 help/version 和合成宿主限制验证。Windows 上核对真实有效策略、仅四个读工具、越界 / junction 读取拒绝以及文件字节未改；用户 settings 中写了开关不等于限制生效。该阶段也不启动用户本机代理。
3. **认证与调用授权分开**：确认隔离 home 中所选认证路径、用户账户 / 模型可用性与额度，避免误用 auto 路由。用户亲自处理所需登录；审查者不读取凭据。未获得真实调用授权前止于此。
4. **一次有价值的首次任务**：只通过现有 AgentSession capabilities/submit/status/collect 驱动一次新任务，执行下节源码分析。真实宿主必须使用搜索和文件读取，不接受固定标识回显替代；CLI 内部可能多次请求模型，不能把“一次派发”写成“一次 API 调用”。
5. **独立复核交付**：保留原生 session/model、工具请求与结果、最终交付、用量缺失项和原始字节；同请求重放不得追加派发。超时或未知即停，不自动重试；declared_complete 不等于成功。GUI 仍不接线，后续再独立派单。

安装、认证、离线验证、真实任务和独立验收分别报告，不把其中一层写成整条路径通过。这里的步骤用于未来编制执行AI08，不是本轮实施授权。

## 首个真实任务草案：为后续历史接线提供依据

选择“Gemini 新登记能否被现有 GUI 历史查询消费”的源码分析，比定位一个枚举类更能帮助后续产品接入。本轮未发送此任务，未调用模型，不改动 Hark12范围。

```text
只读分析当前仓库中 Agent 运行记录历史查询的完整调用链。
请实际使用宿主搜索和文件读取工具，定位：
1. 前端查询入口、HTTP 路由、应用层登记读取和证据呈现路径。
2. 哪些操作仅查历史，哪些位置会启动或收集 Agent 会话。
3. 新 Gemini CLI 适配器的登记和证据能否直接被该历史入口消费；
   如不能，给出有源码依据的最小后续接入清单，说明不应修改的契约和成功门。
每个核心判断附文件:行号，明确已确认和未确认项。
不要改文件，不执行 Shell/测试命令，不实现 GUI 或接线，不调用其它服务。
```

已独立核对的参考锚点仅供架构师评估答案，不把标准答案塞入任务：

| 固定基线 b07213d 的锚点 | 审查用途 |
|---|---|
| gui/data_access.js:48–50 的 listAgentRuns/agentRunDetail | 查到实际 GET 历史入口，而非凭模块命名猜路径 |
| src/gui_http.py:95–96 的 agent-runs 详情路由 | 对应真实 HTTP 到应用层的调用 |
| src/app_api.py:35 导入登记解析助手、:322 _read_runs、:424/:445 两个历史查询方法 | 区分历史读取、校验与活会话派发；不能仅因导入 Codex 模块就断言调用了适配器 |
| src/agent_session_codex.py:613/:790 的类与 submit、src/run_history_consistency.py 的事件规则 | 对照会话执行路径与历史校验的实际差别 |

上述五文件以 `git show b07213d:<path>` 保存原始字节于本地忽略目录 source-anchors，摘要见 source-manifest.json。未来候选基线变化时重新核对行号。**本轮没有判定 Gemini 历史格式兼容或不兼容**，这是首次真实任务要给出的、有待独立复核的分析，不构成新缺陷或接线实施授权。

## 本轮产物与路由

新增本报告及忽略目录 `data/gemini-windows-readiness-20261022/` 的环境观察 / 固定源码锚点。仅本地保存，未同步云端、未创建提交；现有架构报告、派单12和历史归档原样保留。

执行AI：08仍未发行；无需发送。
Hark：原派单12范围与送达状态不变；无需新增发送。
用户：本轮无需决定或操作；等候选通过再选择认证 / 真实调用条件。
一句话现状：本机基础工具可用，已把 Gemini 安装、隔离鉴权和首个有价值任务的验证顺序准备好，尚未接通真实会话。
