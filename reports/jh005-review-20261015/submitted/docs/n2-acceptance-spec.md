# N2 验收规格与复用映射（JH-005）

日期：2026-10-14。范围：只定义验收口径、合成夹具与宿主交付契约；
不改生产代码、协议、权限与 Schema，不启用文件应用，不做真实模型取证。
本文件所有 N2 案例状态均为 `not_run`；旧测试全绿不替代真实验收。

## 代表任务一：小型本地代码修改（`n2-code-sortfix`）

目标文件 `tests/fixtures/n2/code_sortfix/target/widget.py`，纯函数：

- `top_regions(records, n)` 应返回按销售额降序、销售额相同按 region 升序的前 n 个 region 名。
- 现存行为（含唯一缺陷）：实现多传了 `reverse=True`，实际按销售额升序、region 降序排列。
- 目标改动：删除 `reverse=True`，其余字节不变；授权改动范围仅该文件。

行为对照表（`check_widget.py` 逐项断言）：

| 输入 records | n | 缺陷输出 | 期望输出 |
|---|---|---|---|
| b:2, a:2, c:1 | 2 | ["c", "b"] | ["a", "b"] |
| b:2, a:2, c:1 | 9 | ["c", "b", "a"] | ["a", "b", "c"] |
| [] | 3 | [] | [] |
| a:1 | 0 | [] | [] |

必需条件（N2 目标口径；代码任务验收器尚不存在，JH-006 前仅作规格）：

1. 独立审核对补丁判 pass，且无未解决的阻塞问题。
2. 宿主应用后 `check_widget.py` 对应用后文件全过。
3. 仅授权文件被改，且应用时目标基线与记录一致。

阶段划分：生成（模型产出补丁文本）→ 独立审核（审补丁与行为表）→
宿主应用（Codex 原生改文件）→ 应用后测试（跑检查）→ 交付（对账记录）。
生成文本在应用前永远不算“已修改文件”。

## 代表任务二：数据整理（`n2-data-rollup`）

复用 N1 已验收的 `records_transform_v1` 模板与可信排序规则，不新开变换语义。
数据集 6 条（见夹具），期望输出为按 `(region, sales)` 升序排列，控制层独立重算。

必需条件沿用锁定的两条标准条件原文（见 `src/cli.py` 常量），顺序固定；
`transform_acceptance_evaluator` 的两条件能力不变。阶段与代码任务相同，
宿主应用阶段对数据任务仅做结果归档与复核，不写用户目标文件。

## N2 证据映射

| N2 要求 | 复用来源 | 缺口与本批案例 | 状态 |
|---|---|---|---|
| 正常真实双模型交付 | E3 真实闭环 + N1 真实生成闭环 | N2 代码任务尚无真实交付 | not_run |
| revise→修复→pass | E3 注入修复链（标注夹具、前后产物保留） | N2 双任务各一例 | not_run |
| 审核绑定当前 attempt/内容 | E3 绑定 + N1 绑定（attempt/版本/SHA） | 代码补丁绑定待 JH-006 取证 | not_run |
| 必需条件全过、无不确定性 | N1 双条件门 + 未知门 | 代码三条件尚无验收器 | not_run |
| 预算不足不误报成功 | 既有预算失败分支测试 | N2 预算夹具待跑 | not_run |
| 供应商不可用不误报成功 | 适配器故障分支测试 | N2 故障夹具待跑 | not_run |
| 非法输出不误报成功 | Schema 校验失败分支测试 | N2 非法输出夹具待跑 | not_run |
| 用户取消受控停止 | 取消分支测试 | N2 取消夹具待跑 | not_run |
| 副作用不确定不误报成功 | 未确认副作用门测试 | N2 不确定夹具待跑 | not_run |
| 新会话可查询任务记录 | N1 跨进程查询/接续证据 | N2 沿用同一 CLI 查询面 | 复用 |

## 故障与修复场景矩阵

每个场景定义：前置、触发、期望终态与原因码、必须收集的证据：

- `repair-injected-defect`：夹具注入单点缺陷（注明行与前后哈希），期望 revise→修复→pass，保留 v1/v2 产物与两次审核绑定。
- `budget-exhausted`：max_turns/max_tokens 压至不足，期望 failed 且原因码为预算类，无 succeeded。
- `provider-unavailable`：适配器故障注入（离线）或真实不可用，期望失败分支受控停止，从不成功。
- `illegal-output`：产物不可解析或违背 Schema，期望 needs_attention/failed，不放行。
- `user-cancel`：中途取消，期望 cancelled 且可查询终态。
- `uncertain-side-effect`：未确认副作用标记，期望成功门拦截，需人工核对。

## 验收器扩展的最小接口与兼容方案

只做接口提案，不实现、不改现有验收器：

```text
EVALUATOR_REGISTRY: template_id -> {
    criteria:   [exact strings, fixed order],
    expected_of(input) -> expected structure,  # 纯函数，控制层独立计算
    build(expected) -> evaluator callable,     # (task, tool_executions, store) -> results
}
resolve(template_id): 未知 id 在模型调用前拒绝。
```

兼容规则：

- `records_transform_v1` 冻结：两项条件、排序规则、两条件能力上限一字不改。
- `ReviewResult` v1 不变。分类映射：数据整理产物继续以 `code` 送审
  （N1 已验证做法）；代码补丁类产物使用已有的 `patch` 取值。
  不新增枚举、不放宽校验。
- 版本建议（仅建议，不在本批实施）：未来 Schema v1.1 可增补
  `data_conversion` 枚举值；届时 v1 报文仍须被接受，老证据继续有效。

## 验证口径

- 夹具一致性由 `scripts/verify_jh005_fixtures.py` 检查（纯标准库，无模型调用）。
- 仓库全量单测必须保持通过，且 `tests/fixtures/` 下不得出现 `test_*.py`
 （防止计入单测总数）；核对脚本强制此项。
- 真实取证留待 JH-006，按宿主交付契约逐项留痕；本批不产生真实调用证据。
