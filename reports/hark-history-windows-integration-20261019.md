# 登记历史跨记录一致性只读校验 · Windows 集成验证 · 2026-10-19

## 0. 范围理解

按 [Hark-派单-04 限定整改独立复核](hark-history-rectification-independent-review-20261019.md)，从固定候选 `e5f786e35aff473d2766e6c504ed7bf07292c824` 按原字节精准移植六文件到 `n1-artifact-entry`，并完成 Windows 离线、真实只读与完整套件验证；不改候选实现、共享解析器、适配器恢复/派发、GUI、HTTP 入口、既有业务测试、规则、夹具或成功门。

## 1. 分级结论

- **实现完成**：六文件已按固定候选原字节落地；四个实现/新增验证文件提交 `e995789`，旧守卫与分类回归两文件提交 `575b627`（已授权偏离）。
- **离线验证通过**：stage 冻结探针 77 项 0 异常；seed 探针 2 项正常保留 + 4 项反例 corrupt 且源字节不变；分类矩阵 1458 项全部符合；定向测试 72 项（63 + 9）通过；新守卫 24/24 断言检出；旧守卫 8 检出 + 1 冗余（双层关闭只作隔离诊断）；完整套件 `Ran 704 / 703 通过 / 1 既有跳过`，AST 独立计数 704。
- **Windows 真实路径（只读）通过**：源提交 `5b1dc26` 的真实旧历史仍 `ok`/`count=1`/`evidence=verified`/`live=false`，登记与原始证据字节未变；未重新调用模型。
- **待独立验收**：本批为 **Windows 集成候选**，不宣布历史一致性能力已验收；恢复、派发和文件系统并发不在本批加固范围。

## 2. 提交号与改动清单

- 移植提交：`e99578900ca5a9162b7cfd2b1ad404ef1ddd2021`，父提交 `e850ec755a08048309cdab2a0acec6417b1e3711`
  - 新增 `src/run_history_consistency.py`
  - 更新 `src/app_api.py`
  - 新增 `tests/test_jh020_history_consistency.py`
  - 新增 `scripts/verify_hark_history_consistency_guards.py`
- 已授权偏离提交：`575b627a241d48554d6e5bb07bb1420fa2c44d94`，父提交 `e995789`
  - 更新 `scripts/verify_jh020_run_history_guards.py`
  - 新增 `tests/test_jh020_run_history_guard_classification.py`
- 报告提交：本文件所在提交（交付消息回填完整 SHA）；同提交含必要的 STATUS/TODO/CHANGELOG 状态同步。
- 未推送云端、未合并 main/PR #1；架构师交接与历史归档未纳入提交。

## 3. 计数与证据

平台：Windows `10.0.26200.9168`；Python `3.14.7`；Node `v24.19.0`。

| 项目 | 结果 | 原始日志 / 证据 | 提取命令 |
|---|---|---|---|
| stage 冻结探针 | 77 项，0 异常，`all_expected=true` | `data/hark-history-integration-20261019/probe-stage-01/results.json` | `python reports/hark-history-review-20261019/probe_stage_shape.py --repo . --out <新目录>/stage --expect fixed`；`python -c "import json;d=json.load(open('<目录>/results.json',encoding='utf-8'));print(d['total'],d['exceptions'],d['all_expected'])"` |
| seed 一致性探针 | 2 正常保留 + 4 corrupt，源字节不变 | `data/hark-history-integration-20261019/probe-seed-01/results.json` | `python reports/hark-history-consistency-20261019/probe_history_consistency.py --repo . --out <新目录>/seed --expect fixed`；`python -c "import json;d=json.load(open('<目录>/results.json',encoding='utf-8'));print([(c['case'],c['state'],c['passed']) for c in d['cases']])"` |
| 分类矩阵探针 | 1458 项，`all_expected=true` | `data/hark-history-integration-20261019/probe-classification-01/results.json` | `python reports/hark-history-review-20261019/probe_guard_classification.py --repo . --out <新目录>/classification`；`python -c "import json;d=json.load(open('<目录>/results.json',encoding='utf-8'));print(d['total'],d['all_expected'])"` |
| 定向测试 | Ran 72，`OK` | `data/hark-history-integration-20261019/logs/targeted-tests.txt` | `python -m unittest tests.test_jh020_history_consistency tests.test_jh020_run_history_guard_classification`；`grep -E "^(Ran |OK|FAILED)" <日志>` |
| 新守卫 | 24 控制 / 24 变异 / 24 检出，`verdict=pass` | `data/hark-history-integration-20261019/logs/new-guards.json` | `python scripts/verify_hark_history_consistency_guards.py`；`python -c "import json;d=json.load(open('<日志>',encoding='utf-8'));print(d['controls'],d['mutations'],d['detected'],d['verdict'])"` |
| 旧守卫 | 9 行：8 检出 + 1 冗余，`verdict=pass` | `data/hark-history-integration-20261019/logs/old-guards.json` | `python scripts/verify_jh020_run_history_guards.py`；`python -c "import json;d=json.load(open('<日志>',encoding='utf-8'));print(d['rows_total'],d['detected'],d['redundant'],d['classification_counts'],d['verdict'])"` |
| 完整套件 | Ran 704 / 703 通过 / 1 既有跳过 | `data/hark-history-integration-20261019/logs/precommit-suite.txt` | `python -m unittest discover -s tests -p "test_*.py"`；`grep -E "^(Ran |OK|FAILED)" <日志>` |
| AST 独立计数 | 704 | `data/hark-history-integration-20261019/logs/ast-count.txt` | 见下方脚本原文 |
| JH019 前端 | 4/4 | `data/hark-history-integration-20261019/logs/jh019.json` | `node tests/js/jh019_generation_harness.mjs --json` |
| JH020 前端 | 13/13 | `data/hark-history-integration-20261019/logs/jh020.json` | `node tests/js/jh020_run_history_harness.mjs --json` |
| 前端变异 | 1 控制 / 2 检出 / 2 冗余，`pass` | `data/hark-history-integration-20261019/logs/frontend-guards.json` | `python scripts/verify_jh020_run_history_frontend_guards.py` |
| 真实旧历史只读 | `ok` / `count=1` / `verified` / `live=false`，字节未变 | `data/hark-history-integration-20261019/logs/real-history-readonly.txt` | 见下方脚本原文 |

AST 计数脚本原文：

```python
import ast
from pathlib import Path
total = 0
for f in sorted(Path('tests').glob('test_*.py')):
    n = 0
    for node in ast.walk(ast.parse(f.read_bytes())):
        if isinstance(node, ast.ClassDef):
            n += sum(isinstance(i, (ast.FunctionDef, ast.AsyncFunctionDef))
                     and i.name.startswith('test') for i in node.body)
    total += n
print(total)  # 704 = 632 + 63 + 9
```

真实历史只读脚本原文：

```python
import hashlib, json
from pathlib import Path
from src.app_api import AppInterface
reg = Path('data/jh020-r2-final-roundtrip-20261018/runs.jsonl')
before = hashlib.sha256(reg.read_bytes()).hexdigest()
api = AppInterface(db_path=Path('data/does-not-exist.sqlite3'),
                   drafts_path=Path('data/gui/goal-drafts.json'), runs_path=reg)
payload = api.list_agent_runs()
after = hashlib.sha256(reg.read_bytes()).hexdigest()
print(payload['state'], payload['count'], payload['live'],
      payload['runs'][0]['evidence']['state'], before == after)
```

文件身份：六个文件的磁盘 SHA-256 与 Git blob 均与独立复核报告的目标表一致（`src/run_history_consistency.py` `53ddfe3d…`；`src/app_api.py` `4002df45…`；`tests/test_jh020_history_consistency.py` `243a0ca3…`；`scripts/verify_hark_history_consistency_guards.py` `d82568d4…`；`scripts/verify_jh020_run_history_guards.py` `30aaacef…`；`tests/test_jh020_run_history_guard_classification.py` `3b1d755a…`）。提取命令：

```bash
for f in src/run_history_consistency.py src/app_api.py tests/test_jh020_history_consistency.py \
         scripts/verify_hark_history_consistency_guards.py scripts/verify_jh020_run_history_guards.py \
         tests/test_jh020_run_history_guard_classification.py; do printf "%s " "$f"; git hash-object "$f"; done
git ls-tree HEAD~1 -- src/run_history_consistency.py src/app_api.py tests/test_jh020_history_consistency.py scripts/verify_hark_history_consistency_guards.py
git ls-tree HEAD -- scripts/verify_jh020_run_history_guards.py tests/test_jh020_run_history_guard_classification.py
```

计数说明：完整套件日志末尾的 `{"captured": false, ...}` 是既有跳过项诊断，不是失败；`OK (skipped=1)` 与 `Ran 704` 是权威运行结果。旧守卫的 1 项冗余为 `registry_field_validation_skipped`，其双层关闭失败只作隔离诊断（`counted=false`），不计作第九次检出。

## 4. 偏离清单

- `src/app_api.py`：在 `parse_registry_text` 之后、分组与证据读取之前调用 `check_history`；任一矛盾即整份 `corrupt`。该行为已由独立复核接受，属本批授权范围。
- `scripts/verify_jh020_run_history_guards.py` 与其新增分类回归 `tests/test_jh020_run_history_guard_classification.py`：对已验收守卫的分类语义修改，**已授权偏离**，单独提交 `575b627`；正式结果保留 8 检出 + 1 冗余。
- 未修改共享解析器、适配器恢复/派发、GUI、HTTP 入口、既有业务测试、规则、夹具或成功门；未修改候选实现字节。
- 环境记录：`core.autocrlf=true`，候选文件为 LF；磁盘字节与提交 blob 均已核对一致，未来 checkout 重建工作树时可能按仓库配置转 CRLF。

## 5. 故障类覆盖表

| 类别 | 输入 / 注入 | 预期 | 证据 | 覆盖 |
|---|---|---|---|---|
| 绑定字段 | 8 个绑定字段在后续记录被改动，含 null→值 | 整份 corrupt | `TestBindingFields`；`targeted-tests.txt` | 已覆盖 |
| 原生标识演化 | 未知→已知合法；thread/model 冲突或回退 null；`native_turns` 替换/截断 | 合法保持 ok，冲突 corrupt | `TestNativeEvolution` | 已覆盖 |
| 幂等键作用域 | 同键同/异指纹、交错、null 重复、不同键、单源作用域 | 非空键第二 run corrupt | `TestIdempotencyScope`；seed `same_key_second_run` | 已覆盖 |
| 事件顺序 | 全部合法序列、多 run 交错同时间戳、未知事件、非 reserved 起始、重排、重复、无 dispatched 的 collected、终态后追加 | 合法保持 ok，其余 corrupt | `TestEventOrder`；seed `event_reordered` | 已覆盖 |
| 分支 stage 类型 | 7 分支 × 12 类型（含缺失字段、数组/对象/数字/null/布尔/空值） | 非法 stage 一律 `HistoryInconsistent`，不抛异常、不读证据 | `probe-stage-01`（77 项 0 异常）；`TestStageJsonType` | 已覆盖 |
| 分支形状 | 各分支 `state`/`native_state`/原生字段与写入方一致 | 不一致 corrupt | `TestEventShape` | 已覆盖 |
| 好坏记录混合 | 2 份合法历史 + 4 份反例（绑定/指纹/顺序/同键） | 合法保留，反例整份 corrupt，源字节不变 | `probe-seed-01` | 已覆盖 |
| 查询接线 | 矛盾历史 | `runs=[]`、`count=0`、详情 `run=None`；拒绝在证据读取与任务关联之前；无部分摘要 | `TestApiWiring` | 已覆盖 |
| 守卫分类 | 2 声明身份 × 9 对照 × 9 变异 × 9 诊断 | 1458 格符合；未知存活/错误/跳过/零测试不折算通过 | `probe-classification-01` | 已覆盖 |
| 真实旧历史 | 源 `5b1dc26` 的 `runs.jsonl` | `ok`/`count=1`/`verified`/`live=false`，字节不变 | `real-history-readonly.txt` | 已覆盖 |
| 未覆盖 | 时间戳单调性、指纹重算、快照内部绑定、任意 extra 完整语义、实际恢复/派发、文件系统并发、`reserved→collected` 不完整历史（仍按不完整历史拒绝） | — | 独立复核已界定 | **未覆盖，保持原边界** |

## 6. 未做与阻塞

- 未调用真实模型、app-server 或任何 Agent 会话；未发送指定审查会话消息。
- 未改 GUI/HTTP 入口、未接派发/collect/continue/cancel、未改成功门、未启用 `production complete` 或 `FileToolExecutor`。
- 未推送云端、未合并 main/PR #1、未整分支合并；未提交架构师文件、历史归档或私有会话证据。
- 只加固查询路径：矛盾历史整份 `corrupt`，不声称恢复、派发或文件系统并发已加固。
- 真实旧历史只读复跑使用既有登记，未生成新记录；探针每次使用新输出目录，旧证据未覆盖。
- 结论：**Windows 集成候选，待架构师独立验收。**

下一步路由：

架构师：独立验收 `e995789`、`575b627` 与本报告；无新增裁决。
执行AI：等待验收结果；无后续动作。
Hark：等待 Windows 集成验收；无需发送。
用户：把本报告转给架构师；不需要技术决定。
一句话现状：历史一致性只读校验已按固定候选移植并通过 Windows 离线与真实只读验证，但尚未获架构师独立验收；恢复、派发和生产门仍未接通。
