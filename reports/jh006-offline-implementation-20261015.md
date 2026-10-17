# JH-006 首批离线实现记录 · 2026-10-15（未运行、未提交）

本会话 Shell 不可用：只做文件实施，未运行任何测试/核对，未提交。
改动基于工作树已有未提交实现补齐，未重写、未重置工作树。
真实文件应用门禁保持关闭，原树失败 WIP 未动。

## 改动文件

- `src/code_delivery.py`：新增 `OFFLINE_FIXTURE_RECORDER`（`fixture-offline-host/v1`），
  `validate_apply_receipt` 只认该装配来源，其他 recorder 受控拒绝。
- `src/orchestrator.py`：
  - 新增缺失的 `_prepare_code_delivery`（原 `run_task` 自动准备分支调用了不存在的方法）。
  - `prepare_code_delivery` 新增：生产来源污染检查、未知来源拒绝、三项验收原文 pin、
    单一授权 target_files pin、`patch_text` 类型 pin。
  - `complete_code_delivery`：`baseline` 键名修正（`sha256` → `sha256_before`，
    原写法每次完成必 KeyError）；生产任务完成改为明确拒绝（生产宿主装配留待离线审查后）。
- `src/cli.py`：`code_sortfix_v1` 可信装配分支（固定三条件/单目标/无 input_data/无工具），
  `summary`/`status` 的 `delivery_scope` 区分 generated/waiting_host/applied，
  新增 `prepare-code-delivery` / `complete-code-delivery` 命令，
  submit/resume/approve 对 waiting 结果返回 0 并打印明确等待状态。
- `.agents/skills/jh-task/SKILL.md`：最小封装说明（模板规则、准备/查询/完成命令、
  waiting/applied 读法、fixture 标记），审批语义不变，不自动 approve/recover。
- `tests/test_jh006_code_delivery.py`：新增（见下）。封存夹具未动，
  未在 `tests/fixtures/` 下新增测试。

## 交接六项要求对照

1. 单一授权补丁：已有（信封解析/审核 patch 分类/范围外拒绝），本批加类型 pin。未运行。
2. prepare 绑定：已有（版本/审核/内容/attempt/基线/参考字节绑定），本批加来源与模板 pin。未运行。
3. 等待标记与 resume：已有（reviewing 下 `_delivery` 持久化、可跨进程查询、resume 零调用），
   本批加 CLI 查询面区分。未运行。
4. complete 核对：已有（只读目标、固定命令/哈希/退出码核对），本批修 baseline 键名 bug、
   明确装配来源、生产完成拒绝。未运行。
5. 成功门与受控分支：已有（字节相等/三验收/独立审核/无不确定性；blocked/uncertain/重复/迟到/
   跨任务/旧审核/旧版本/owner 占用分支）。未运行。
6. CLI/Skill 最小封装：原缺失，本批补上。未运行。

## 守卫→回归映射（变异式：每项守卫至少一个反例测试）

信封/目标/类型 pin：prepare 形状/目标/类型三测试 + helper 单测；
参考/基线字节：unsealed/drift 两测试；审核绑定：reject/review-drift 两测试；
来源证据：unknown/missing/mock/production 四测试；等待语义：parks/resume-零调用/
幂等/CLI-resume 四测试；应用字节：happy/extra-bytes（含行为 PASS 对照）/unapplied 三测试；
回执：target/check/exit/uncertain/recorder/command 六测试；版本 pin：new-version/old-version 两测试；
跨任务/重复/迟到：三测试；owner/在途/未知/待审批/预算：五测试；取消：两测试；
CLI 准备/完成/查询/子进程：五测试；records 回归面：既有断言 + summary-note 测试。

## 未运行项（交架构师承接）

1. `tests/test_jh006_code_delivery.py` 全文件（约 41 方法，1 行为对照子进程 + 1 CLI 子进程往返）。
2. 既有全量回归（基线 185 项）+ `scripts/verify_jh005_fixtures.py` + JH-005 十四变体与守卫检查。
3. 按明确范围提交并提交后验证；真实模型/真实宿主取证不在本批。
