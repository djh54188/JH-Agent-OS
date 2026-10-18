# Hark 独立分析：构造器残留误报 / prepared_version_field / TODO 单行（2026-10-18）

- 分析基线：`fe0476d5807d6d562a1c290fe1b14623e53d1a46`（PR #1 候选，未合并）；TODO 参考 `main` `bc3e0f286d4a8a2863ab15daf223a2900f0dd44a`。
- 分支：`hark-analysis-20261018`（独立分析分支，未向 PR #1 分支追加提交）。
- 本批**只新增** `reports/hark-analysis-20261018.md` 与 `reports/hark-analysis-20261018/**`。未修改 `main`、`src`、`scripts`、`tests`、夹具、权限或生产门；未整改发现的问题；不升级任何验收结论，不阻塞 Windows 本地验证。
- 环境：Linux aarch64，Python 3.12.15 / 3.14.8。Windows 未执行；Linux 结果只作对照，不预设 Windows 结果。
- 缺失文件：本批所需文件（两个验证器、`tests/test_jh006_code_delivery.py`、`src/cli.py`、`src/orchestrator.py`、TODO 历史）均在 GitHub 上找到，无缺失。

## 一、构造器残留误报探针

**方法。** 探针在临时目录生成合成仓库（`src/guard.py`、`src/consumer.py`、`tests/test_probe.py`），对两个验证器的 `evaluate_mutations` 原样调用。唯一变异：`raise Refused("value over limit")` → `raise Refused("value beyond limit")`，守卫条件、异常类型和业务结果不变。两条业务对照（`consumer.allowed(11)` 仍为 False、`status(11)` 仍为 `"blocked"`）在变异下仍通过、判 `survived`，证明这是纯文案变化。因此 12 例中任何 `detected` 都是误报。

- 开启（on）：`fe0476d` 原样。
- 关闭（off，对照）：仅在内存副本里把 `if i < len(exprs) and text_derived(exprs[i]):` 换成 `if False:`，仓库文件不动。
- 每行都保留 baseline / identity / mutant 三次运行的结果及 `mutant_evidence`（分类理由）。错误、跳过、红基线、undetermined 都不计检出。本批 12 例的 baseline 与 identity 均为 `pass`。

**结果（两个验证器、两个 Python 版本完全一致）：**

| 案例 | 写法 | on | off |
|---|---|---|---|
| bool_direct | `bool(run(11).endswith(...))` | undetermined | **detected** |
| bool_helper | `bool(_ends(run(11)))` | **detected** | **detected** |
| bool_local | `hit = ...endswith(...)`；`bool(hit)` | **detected** | **detected** |
| int_direct | `int(run(11).count(...))` | undetermined | **detected** |
| int_helper | `int(_hits(run(11)))` | **detected** | **detected** |
| int_local | `hits = ...count(...)`；`int(hits)` | **detected** | **detected** |
| float_direct | `float(run(11).count(...))` | undetermined | **detected** |
| float_helper | `float(_hits(run(11)))` | **detected** | **detected** |
| float_local | `hits = ...count(...)`；`float(hits)` | **detected** | **detected** |
| str_direct | `str(run(11).endswith(...))` | undetermined | **detected** |
| str_helper | `str(_ends(run(11)))` | undetermined | **detected** |
| str_local | `str(hit)` | undetermined | **detected** |
| control ×2 | 业务结果 | survived | survived |

**本批样本中的误报数量：** text_derived 开启时，12 例里有 **6 例**（bool/int/float × 辅助函数/局部变量）；关闭时 **12 例**。这只是本批合成样本的计数，不推断总体误报率。

**开启时仍误报的原因（只读定位，未整改）。**
1. `_proven_call` 对任何 `isinstance(fn, type)` 的被调用者直接返回 `(True, "constructor")`，不检查参数；`bool(x)`、`int(x)`、`float(x)` 就此被视为“已证明来源”（两验证器同一位置：`verify_jh006_guards.py` 第 325 行、`verify_phase4_guards.py` 第 332 行）。
2. `text_derived` 只在**表达式本身**里查文本方法或文本名；文本处理一旦在测试辅助函数（`_ends`）或局部变量（`hit = ...`）里，外层的 `bool(...)` 看不出来。
3. `str_*` 三例开启时不误报，只因为 `str` 在 `_TEXT_CALLS` 的名字清单里，并非来源证明。
4. 去掉外层构造器后，同类写法（例如既有回归 `test_bool_from_helper`、`test_bool_local_from_helper`）会判 undetermined。这说明构造器分支绕过了原本会拦下它们的“辅助函数在 `src` 之外”和单次赋值检查。

## 二、prepared_version_field 可证明化方案（只读，不改测试）

**现状。** `test_cli_prepare_and_complete_roundtrip` 首个失败断言是 `self.assertEqual(code, 0)`（mutant 下 `1 != 0`）。`code` 来自 `code, out = _run_cli([...])`：一是元组解包，二是同一函数内绑定两次（prepare、complete 各一次），三是 `_run_cli` 是 `tests/` 里的辅助函数。因此判 `undetermined`（理由：`local name not bound once by a simple assignment: code`）。`code` 的真实来源是 `_run_cli` 内的 `code = cli_main(argv)`，即 `src/cli.py` 的返回值：prepare 成功且状态为 `waiting_host` 时返回 0，被拒时返回 1。保留当前 `undetermined` 裁决；以下两案都不以恢复 19/19 为目标。

**方案 A：测试侧，只改该测试的 prepare 调用（最小）。**
```python
        prepare_buf = io.StringIO()
        with redirect_stdout(prepare_buf):
            prepare_code = cli_main(["--db", str(self.db), "--workspace", str(self.ws),
                                     "--test-fixture-mode", "prepare-code-delivery", tid, "1"])
        body = json.loads(prepare_buf.getvalue())
        self.assertEqual(prepare_code, 0)
```
- 证明哪一层：单层。`prepare_code` 在函数内只有一次简单赋值，值是 `src.cli.main` 的返回值（被调用者在 `src/` 下，符合现有“system call”规则）。它证明的是 **CLI prepare 命令的退出码**变了，属行为层，但粒度粗：任何拒绝都会让它变成 1，不能单独指认是 `version` 字段比较。
- 业务覆盖：argv、断言顺序、`body` 解析与后续断言不变；complete 段保留 `_run_cli`。没有删减覆盖。
- 反例影响：验证器不改，R1–R4 合成反例与元变异结论不受影响。
- 隔离验证（仅临时副本，仓库测试未改）：Python 3.12 / 3.14 均为 baseline/identity `pass`、mutant `behavioral_failure`、该行判 `detected`。这只说明方案可行，不代表采纳，也不改变当前裁决。

**方案 B：验证器侧，补一跳“元组解包 + 最近绑定”证明。**
规则草案：断言里的名字有多次绑定时，只取**断言前、同一直线代码块内最近的一次**（中间没有分支或循环）。若该绑定是元组解包 `a, b = helper(...)`，且 `helper` 定义在 `tests/` 下、其 `return` 是元组字面量、对应位置是 helper 内仅一次简单赋值的 `src` 调用结果，则视为已证明。
- 证明哪一层：两层（测试内绑定 + 辅助函数返回位置）。正是报告所述“单层证明未完成”的那一跳。
- 业务覆盖：不动任何测试，覆盖不变。
- 反例影响（需新回归与元变异）：会改变 `scripts/`，并同时作用于 `test_jh006_code_delivery.py` 中全部 9 处 `code, out = _run_cli(...)` / `_cli_subprocess(...)` 调用，可能让其他行的判定一并变化，须重跑 19 行。需要新增的反例至少包括：`test_bool_rebound_local`（最近绑定是文本派生，必须仍为 undetermined）；辅助函数返回 `(len(out), out)` 或 `(out.count(...), out)`；在 if/for 里重新绑定；辅助函数多处 `return`。另外，第一节的“构造器不查参数”问题会与此规则叠加，例如返回位置是 `bool(...)` 的情况。
- 风险高于 A，且扩大平台规则；本批只给草案，不实现。

## 三、TODO 单行修正草稿

- 确认：`bc3e0f2:TODO.md` 中 pm-dispatch 三列表（`关联任务 | 候选借鉴点 | 评估重点`）第一行是四列的 JH-004 任务行，与上方真正任务表第 24 行逐字相同（全文出现 2 次）。
- 历史依据：该行在 `c7d7421`（2026-10-14）及更早版本为 `| JH-004 | 自包含任务卡 | 背景、输入事实与版本、依赖、授权路径、产物要求和验收方式完整；必要上下文实际传给 Worker，引用材料可获取 |`；`61389eb`（2026-10-14，docs(n1): accept verified Codex skill and real model entry）把任务表与此表中的两行 JH-004 同时替换成新文本，导致误贴。两提交位于远端分支 `n1-artifact-entry`。
- 补丁：`reports/hark-analysis-20261018/todo_pm_dispatch_row.patch`，只替换 pm-dispatch 表这一行，恢复为上述历史原文；不新增借鉴点或验收状态；任务表 JH-004 行与“状态：候选，尚未采纳”保持不变。补丁保留 `main` 的 CRLF 字节，已在 `bc3e0f2` 的 TODO.md 上 `git apply --check` 通过。未应用到 `main` 或本地。

## 复现

```bash
git checkout fe0476d5807d6d562a1c290fe1b14623e53d1a46
# 一：构造器探针（只用合成仓库，不需 jsonschema）
python3.12 reports/hark-analysis-20261018/probe_constructor.py . > constructor_probe_py312.json
python3.14 reports/hark-analysis-20261018/probe_constructor.py . > constructor_probe_py314.json
# 二：方案 A 隔离验证（需 requirements-cloud.txt 依赖，如 jsonschema）
python reports/hark-analysis-20261018/check_option_a.py . > option_a_isolated.json
# 三：TODO 补丁检查
git show bc3e0f286d4a8a2863ab15daf223a2900f0dd44a:TODO.md > /tmp/t/TODO.md
cd /tmp/t && git init -q && git apply --check <repo>/reports/hark-analysis-20261018/todo_pm_dispatch_row.patch
```
探针脚本在内存副本里关闭 text_derived，并在 `tempfile.mkdtemp` 临时目录运行，结束后删除；JSON 中 `synthetic_repo_unchanged: true` 记录合成仓库运行前后字节一致。

## 文件清单

- `reports/hark-analysis-20261018.md`：本报告
- `reports/hark-analysis-20261018/probe_constructor.py`：构造器探针
- `reports/hark-analysis-20261018/raw/constructor_probe_py312.json`、`constructor_probe_py314.json`：原始结果（含每行三次运行结果及分类理由）
- `reports/hark-analysis-20261018/check_option_a.py`：方案 A 隔离验证脚本
- `reports/hark-analysis-20261018/raw/option_a_isolated_py312.json`、`option_a_isolated_py314.json`：方案 A 原始结果
- `reports/hark-analysis-20261018/todo_pm_dispatch_row.patch`：TODO 单行补丁草稿
