# Gemini CLI 会话桥接（Hark-派单-11）

状态：云端候选，待独立复核；未做真实模型调用，Windows 真实工具执行未验证。

`src/agent_session_gemini.py` 的 `GeminiCliAgentSessionAdapter` 是第一个非 Codex 的 `AgentSessionAdapter`。它只是薄适配器：启动已安装、固定版本的 Gemini CLI，让 CLI 自己完成“思考 → 工具 → 结果 → 继续”的循环，然后收集宿主实际产生的事件和记录。它不执行工具、不跑模型循环、不调度。

## 支持面

| 能力 | 状态 | 说明 |
|---|---|---|
| capabilities | 支持 | 静态声明，另附 `host` 字段（版本、工具、限制方式） |
| submit | 支持 | 只新建会话（`session_id` 为 `new`/空/`new:*`）；先登记后启动进程 |
| status | 支持 | 本地登记 ＋ 进程是否退出 |
| collect | 支持 | 有界等待；事件绑定会话、配对工具调用、比对工作树清单 |
| continue | 不支持 | `--resume` 有官方文档，但本桥未验证，抛 `UnsupportedCapability` |
| cancel | 不支持 | 抛 `UnsupportedCapability` |

拒绝而不是忽略：非空 `allowed_scope`（路径级范围无法由宿主落实）、有限 `budget_tokens`、已有会话地址、版本不是 0.65.0、工作区含 `.gemini/`、工作区或任一上级目录有 `.env` 或 `.gemini/.env`、证据目录或 CLI home 与工作区重叠。

## 依据分级

| 事项 | 依据类别 | 位置 |
|---|---|---|
| stream-json 事件 init/message/tool_use/tool_result/error/result 及字段 | 官方源码（npm 包 0.65.0 bundle 中 `StreamJsonFormatter`、`runNonInteractive`） | 交付报告“源码核对”节 |
| `--session-id`、`--admin-policy`、`--approval-mode`、`--skip-trust`、`-o stream-json` | 官方 CLI `--help`（0.65.0，无需模型） | `reports/hark-gemini-bridge-20261021/host/cli-help.txt` |
| admin 策略 `deny *`＋允许 4 个读工具后，模型看到的工具声明只剩 read_file/grep_search/glob/list_directory；`yolo`、工作区设置/策略、用户策略均放不开；写/Shell 调用返回 `tool_not_registered`；工作区外路径与指向外部的符号链接被拒 | 宿主离线实测：真实 CLI ＋ 合成模型端点（`tests/fixtures/gemini_cli/synthetic_endpoint.py`，不是模型） | `reports/hark-gemini-bridge-20261021/host/` |
| 适配器的关联、缺失、未知不重发、只读拒绝 | 合成证据：进程替身 `fake_cli.py` 回放上面实测得到的原生流（会话 id 与路径模板化） | `tests/test_hark11_gemini_session.py` |
| 真实模型、真实账户、配额、Windows 路径与策略文件行为 | 待真实验证 | — |

## 宿主限制如何落实

1. 每次运行在证据目录写 `admin-policy.toml`，以 `--admin-policy` 传入（最高层级 admin tier）：`toolName="*"` deny，四个读工具 allow。
2. 隔离的 CLI home（`GEMINI_CLI_HOME`）写入锁定的用户设置：`tools.core` 只列四个读工具，关闭 MCP/扩展/技能/hooks，`advanced.ignoreLocalEnv`，关遥测，`model.maxSessionTurns=40`。
3. 预检拒绝工作区 `.gemini/`：其中的 `settings.json` 可声明 MCP 服务器、hooks、发现命令，这些会在工具路径之外启动进程，策略文件挡不住。
4. 进程环境从空开始，只传 PATH 等基础变量和配置中按名字列出的变量。
5. 启动前、收集时对工作树做逐文件清单（不跟随符号链接），不一致即 `failed`。

注意：headless 模式要求可信目录，本桥传 `--skip-trust`（仅本会话）。系统级设置文件（`GEMINI_CLI_SYSTEM_SETTINGS_PATH`）在 Linux 上要求 root 属主，非 root 时被 CLI 跳过，所以本桥不依赖它。

## 输入与输出

- 指令走标准输入，前缀一行固定文字，保证输入不以 `/` 开头（避免斜杠命令）；`-p` 只放一句固定提示。CLI 实际输入为“stdin 内容 ＋ 空行 ＋ -p 内容”（实测）。指令中的 `@路径` 仍会被 CLI 当作工作区内文件引用读取，这是读操作，不放开写入。
- 证据：`runs/<run_id>/stdout.jsonl`（CLI 标准输出原字节）、`stderr.txt`、`host-chat-record.jsonl`（CLI 自己的会话记录，按 sessionId 找到后逐字节复制）、`workspace-before.json`/`workspace-after.json`、`admin-policy.toml`；`registry.jsonl` 记录尺寸与 SHA-256。`verify_run_evidence()` 回读磁盘字节比对。
- 模型：分别记录请求名（`-m`）、宿主 init 报告名、`result.stats.models` 里的后端名。未指定模型时宿主为 `auto`，会先调用路由分类器（额外的非流式模型调用）。
- 用量：照抄 `result.stats`；缺字段记入 `unverified`，不补零。原生轮次 id 不暴露，`NativeTurn` 用适配器分配的 `<session>#invocation-1` 并注明。
- `declared_complete` 只是宿主声明，从不等于任务成功。

## 本地验证入口

```
python scripts/verify_hark11_gemini_bridge.py --mode host-probe --cli-js <gemini.js> --workspace <worktree> --out <证据目录>
python scripts/verify_hark11_gemini_bridge.py --mode real --authorize-real-model-call --env-name GEMINI_API_KEY --auth-type gemini-api-key --model <型号> --cli-js <gemini.js> --workspace <worktree> --out <证据目录>
```

`host-probe` 不调用模型；`real` 会产生真实模型调用和费用，需另行授权，云端未运行。退出码：0 全部检查通过，1 运行完成但有检查未过，2 无法运行。

## 准入整改（Hark-派单-12）

- 登记准入：`evidence_root/registry.lock` 排他锁（线程内按路径一把锁＋跨进程 flock / Windows msvcrt.locking），从幂等判定持有到 `dispatched` 记录写入；等待超过 `registry_lock_timeout` 秒即以 `RegistryBusy` 拒绝，不无锁继续。每次 submit 在锁内按磁盘上的**当前**登记重建视图。
- 登记合法性：逐条校验字段集合与类型、run_id/原生会话/摘要格式、指纹＝sha256(快照)、快照与绑定一致、事件允许的状态、每个 run 的事件顺序 reserved → dispatched|dispatch_failed → collected、后续记录重复保留的绑定、键与原生会话各只对应一个 run。登记只追加：本对象读过/写过的字节必须仍是前缀。任何不合规或不可读即对本对象粘滞拒绝，不修复、不重排、不补发。
- 工作区观测：清单遍历遇到无法列出/读取一律报错；启动前失败 → `WorkspaceUnobservable`，零派发零登记；收集时失败 → failed，`workspace_unchanged` 为 null（未证明），不宣称未修改。FIFO 等特殊文件只记类型不打开。
- 原生事件：按 0.65.0 字段形状校验，错形状给出诊断并失败，不抛未处理异常；非只读工具只有宿主自己的 `status:"error"` 才算确认拒绝，其他状态列入 `unconfirmed_tool_requests` 并失败。宿主聊天记录首行非对象时只给诊断。
- 路径：在任何解析之前按输入本身要求绝对路径；相对（不论目录是否存在）、Windows 根相对 `\ws`、驱动器相对 `C:ws`、含 `..` 均在派发前拒绝。Windows 形式仅在 Linux 上以分类器和合成字符串验证，**不是 Windows 实测**。
- 回放夹具：占位符在解码后的 JSON 值里替换并以 JSON.stringify 兼容格式重编码；对 11 捕获的原生流逐字节复现（sha256 be3ed400…）。
