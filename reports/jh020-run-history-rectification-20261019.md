# JH-020 运行记录切片 R1/R2/R3 限定整改 · 2026-10-19（交付，待独立验收）

对应派单：执行AI-派单-01。基线 `11bb4e4d2dbd9411d89f51905c834c78c5aa3e5f`；工作树 `D:\JH-Agent OS\data\worktrees\n1-artifact-entry`。
规则：`AGENTS.md` v2 + `docs/collaboration-rules.md` v2（后者 SHA-256 `0c203318…` 与派单一致；`AGENTS.md` 差异见 §4）。

## 0. 范围理解

一句话：只做 R1（证据引用边界与异常输入）、R2（详情请求关联）、R3（JH-018 导入守卫），不重做已关闭项、不改派发/成功门。
歧义处理：`AGENTS.md` 当前哈希 `edc9846b…` 与派单给定的 `21af9ace…` 不一致——差值为**上一批按用户指令加入的“执行方交付规则（vN）”一节**（剥离该节后哈希精确等于派单值）。我未擅自删除该节，也未改写 `6c02c21`，按“先交可独立完成的部分并报告阻塞”处理（§4、§6）。

## 1. 分级结论

| 项 | 结论 |
|---|---|
| 实现完成 | **是**（R1/R2/R3；JH-020 整体仍未完成） |
| 离线验证通过 | **是**（套件、定向变异、前端行为台、评审探针副本） |
| 真实路径通过 | **部分**：真实浏览器以**正常指针点击**打开历史面板与详情的实机验证通过；本批未做真实派发/真实审查 |
| 待独立验收 | **是**，交当前 Codex 架构师 |

## 2. 提交号与改动清单

| 提交 | SHA | 内容 |
|---|---|---|
| 文档 | `3a23f067bcaee4439e1c2674ee182af359a4eabe` | `docs/collaboration-rules.md`、`docs/templates/execution-task.md`、`docs/handoffs/architect-current.md`（三件；`AGENTS.md` 已在 `6c02c21`） |
| 功能 | `88515030cd256b01373bfdf62d275ecaa7d0923e` | R1/R2/R3 + 测试 + 两个守卫验证脚本 + 本报告 |

功能提交 11 个文件：`src/agent_session_codex.py`、`src/app_api.py`、`src/gui_http.py`、`gui/views.js`、`tests/test_jh018_agent_session_contract.py`、`tests/test_jh020_run_history_api.py`、`tests/test_jh020_run_history_frontend.py`、`tests/js/jh020_run_history_harness.mjs`、`scripts/verify_jh020_run_history_guards.py`、`scripts/verify_jh020_run_history_frontend_guards.py`、本报告。

## 3. 计数（日志路径 + 提取命令）

环境：**Python 3.14.7 / win32 / Windows-11-10.0.26200-SP0**。

| 数字 | 值 | 日志 | 命令 |
|---|---|---|---|
| 全量测试 | 632 项（631 过 / 1 既有跳过） | `data/jh020-run-history-rectify-20261019/logs/{pre,post}commit-suite.txt` | `python -m unittest discover -s tests -p "test_*.py"` |
| 独立计数（AST） | **632** | 同上（命令内联） | 见下脚本 |
| Python 守卫变异 | 9 检出 / 9 / 0 异常 | `…/logs/postcommit-guards.json` | `python scripts/verify_jh020_run_history_guards.py` |
| 前端守卫变异 | 2 检出 / 2；另 2 项记冗余 | `…/logs/postcommit-frontend-guards.json` | `python scripts/verify_jh020_run_history_frontend_guards.py` |
| 前端行为场景 | 13/13 | `…/logs/postcommit-frontend.json` | `node tests/js/jh020_run_history_harness.mjs --json` |
| 既有 JH-019 场景 | 4/4 | `…/logs/postcommit-jh019.json` | `node tests/js/jh019_generation_harness.mjs --json` |
| R1/R3 评审探针 | exit 0；越界读取 **0** | `…/results.json`、`…/logs/postcommit-probe.txt` | `python data/jh020-run-history-rectify-20261019/probe_copy.py` |
| R2 评审探针 | exit 0；六项失败标志全为 false | `…/frontend-results.json`、`…/logs/postcommit-frontend-probe.txt` | `node data/jh020-run-history-rectify-20261019/frontend_probe_copy.mjs` |
| 实机 GUI | 正常指针点击通过 | `…/evidence/history-panel.png/.txt`、`history-detail.png/.txt`、`api-agent-runs.json`、`gui-server.log` | `python -m src.gui_http --port 8792 --runs data/jh020-r2-final-roundtrip-20261018/runs.jsonl` |

```bash
python - <<'PY'
import ast
from pathlib import Path
n=0
for f in sorted(Path('tests').glob('test_*.py')):
    for node in ast.walk(ast.parse(f.read_text(encoding='utf-8-sig'))):
        if isinstance(node, ast.ClassDef):
            n+=sum(1 for i in node.body if isinstance(i,(ast.FunctionDef,ast.AsyncFunctionDef)) and i.name.startswith('test'))
print(n)
PY
```

协议调用口径：本批**未发起任何协议调用**（无 `initialize`/`thread/start`/`turn/start`）；查询路径以 `subprocess.Popen` 触发即失败锁定。

## 4. 偏离清单

1. **修改已验收测试** `tests/test_jh018_agent_session_contract.py`（JH-018 验收件）：导入守卫由“精确两种静态形式”扩为等价形式 + 字面量动态导入；**并明确声明这不是“只收紧”**——允许只读适配器查询辅助导入与注释是**策略性放宽**（原子串禁令会误伤）。按派单要求单列，等待裁决。
2. **修改已验收测试资产** `tests/js/jh020_run_history_harness.mjs`（上一批交付件）：新增 6 个详情乱序场景；既有 7 场景断言未改。
3. **`tests/js/jh019_generation_harness.mjs` 未改**（本批）；上一批的桩兼容调整已获批准。
4. **提交归属**：`AGENTS.md` 仍为 `6c02c21` 的版本（架构师 v2 + 用户 vN 节），与派单钉住的 `21af9ace` 差该节；本批未覆盖、未删除。
5. **字段/接口**：`raw_evidence_state` 增加**可选关键字参数** `allowed_roots`/`registry_dir`；默认（不传）保持原“无包含检查”语义（旧调用者与单元测试依赖）。`verify_raw_evidence` 返回 `(bool, detail)` 不变。**登记为技术债**：同一函数名同时承担“受限”与“不受限”两种调用，靠调用方传参区分。
6. 未改派发、恢复、预算、成功门、Hark 验证器。

## 5. 故障类覆盖表

### R1 · 证据引用输入空间

| 类 | 预期 | 证据 | 状态 |
|---|---|---|---|
| 根内普通文件 | verified，且只读 1 次 | `test_inside_root_verifies_and_reads_once` | 已覆盖 |
| 绝对越界 | outside_root，**0 读取** | `test_outside_absolute_is_refused_before_read`、探针 `absolute_outside`(reads=0) | 已覆盖 |
| 绝对越界 + 错误摘要 | outside_root（不靠摘要兜底） | 探针 `absolute_wrong_digest`(outside_root, reads=0) | 已覆盖 |
| 相对越界 | 按登记目录解析后拒绝，0 读取 | `test_relative_reference_resolves_against_registry_dir_not_cwd`、探针 `relative_outside`(0) | 已覆盖 |
| 同名前缀目录 | outside_root | `test_same_prefix_sibling_is_refused`、探针 `prefix_sibling`(0) | 已覆盖 |
| 根下 junction 指向外部 | 读取前拒绝，理由含 reparse | `test_junction_under_the_root_is_refused_before_read` | 已覆盖 |
| UNC / 设备路径 / ADS | **不访问**即拒绝 | `test_special_path_forms_are_never_touched`（并断言 `realpath` 未被调用） | 已覆盖 |
| `raw` 非对象 / path 非字符串 / NUL | invalid，结构化 | `test_malformed_references_are_invalid_without_exception`、探针 `raw_string`/`path_integer`/`path_nul` | 已覆盖 |
| 摘要/帧数字段非法 | invalid | 同上（`sha256="zz"`、`frames="1"`） | 已覆盖 |
| 根内非 UTF-8 | invalid_content（不抛异常） | `test_invalid_utf8_inside_root_is_structured` | 已覆盖 |
| 显式新增根 | 允许（仅服务端配置） | `test_explicitly_configured_extra_root_is_allowed` | 已覆盖 |
| 引用缺失 | missing | 探针 `raw_absent` | 已覆盖 |
| 并发路径替换（TOCTOU） | — | — | **未覆盖**：拒绝发生在读取前，但检查与读取之间仍存在替换窗口，未声称原子文件系统安全 |
| 根外证据的字节内容 | — | — | 未读取，无法评价 |

### R2 · 详情响应输入空间

| 类 | 预期 | 证据 | 状态 |
|---|---|---|---|
| A→B 逆序返回 | 迟到 A 不覆盖 B | `detail_reverse_replies`、探针 `reverse_replies` | 已覆盖 |
| 同 ID 重复查询逆序 | 旧回复不覆盖新回复 | `detail_same_id_repeat`、探针 `same_id_repeat` | 已覆盖 |
| 迟到错误 | 不清除当前详情 | `detail_late_error`、探针 `late_error` | 已覆盖 |
| 响应携带未请求 run id | 不渲染、不回显该 id | `detail_unrequested_run_id`、探针 `wrong_identity` | 已覆盖 |
| 离开页面后迟到回调 | 不绘制 | `detail_leave_view`、探针 `leave_view` | 已覆盖 |
| 重建面板后迟到回调 | 不绘制 | `detail_reenter_view`、探针 `reenter_view` | 已覆盖 |
| 迟到空结果 | 由代次/绑定拦截 | 与上两类同路径 | 已覆盖（间接） |

### R3 · 导入形式输入空间

| 形式 | 预期 | 状态 |
|---|---|---|
| `from src.agent_session import …` / `import src.agent_session [as x]` | 检出 | 已覆盖 |
| `import src.agent_session.models` | 检出（前缀规则） | 已覆盖 |
| `from src import agent_session [as x]` | 检出 | 已覆盖 |
| `from .agent_session import …` / `from . import agent_session` | 检出 | 已覆盖 |
| `from ..agent_session import x` | 不检出（越出包） | 已覆盖（允许项） |
| 字面量 `importlib.import_module(...)` / `__import__(...)` / `getattr(importlib,"import_module")(...)` | 检出 | 已覆盖 |
| 适配器查询辅助导入、纯注释 | 不检出 | 已覆盖（允许项） |
| 计算出的模块名（变量、拼接、f-string） | — | **未覆盖**（静态不可达，已写入测试注释，不声称完整防护） |

### 延后能力（本批不做）

`#6 跨记录矛盾`、`#7 事件顺序非法`、`同幂等键跨两条记录不同指纹`：查询层仍以 `state=ok` 接受，可能把首条绑定与末条原生状态、较早 collected 的用量/证据拼在一起。**文件字节 verified 不代表登记历史语义已被验证**。共同登记为后续「历史一致性」能力，不在本批扩成恢复/运行时重写。

## 6. 未做与阻塞

- 未做：跨记录一致性与事件顺序校验、真实派发、collect RPC、continue/cancel、指定审查会话发送、GUI 执行、成功门与生产门变更、Hark 验证器修改、云端推送/PR 合并。
- **阻塞（需裁决）**：`AGENTS.md` 与派单钉住的 `21af9ace` 相差用户 vN 节；保留与否需拍板。
- **残留限制**：TOCTOU 并发替换窗口未消除；计算型动态导入不可见；`raw_evidence_state` 默认不受限（技术债，见 §4.5）。
- 状态文档（`STATUS.md`/`CHANGELOG.md`）本批未追加，避免产生第三个提交；如需入库请指示。

## 7. 下一步路由

架构师：验收 R1/R2/R3，并裁决 `AGENTS.md` 的 vN 节去留与 §4 的两处已验收测试偏离
执行AI：等待派单
Hark：无
用户：把本报告转给架构师；并拍板 `AGENTS.md` 保留还是删除用户 vN 节

白话现状：运行记录查询现在只读服务端授权的证据目录、越界一律不碰，详情面板不再被迟到回复串台；历史记录内部的跨记录一致性仍未校验。
