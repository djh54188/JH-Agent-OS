# 真实宿主登记输入清单 · 2026-10-17

**范围：只盘点真实宿主登记所需输入，并给出宿主可直接执行的命令。** 不新增实现，不用合成记录补齐。production complete 与文件应用保持关闭。基线 `c6c0af57`。

登记（`register-host-capture`）的硬门（`src/capture_binding.py::_control_snapshot`，`check_target=True`）要求：`status == "reviewing"`；`metadata._delivery.state == "waiting_host"` 且 `delivery_id` 匹配；`metadata._trusted.source == "production"` 且模板为 `code_sortfix_v1`；产物为 `patch_text` 且等于封存参考字节；最近一条 completed reviewer attempt 同时是 `task_control.active_attempt_id`，其 review 内容哈希匹配且 pass 覆盖当前 patch；工作区授权目标 `target/widget.py` 当前字节 == `expected_after`。任一不满足即受控拒绝。计划（`plan-host-check`）为只读，`check_target=False`，可在应用前执行；登记永远走应用后门，无公开绕过参数。

## 逐项现状

| # | 输入 | 现状 | 依据 |
|---|---|---|---|
| 1 | 真实数据库 | **缺失** | 默认 `data/jh_agent_os.sqlite3`（相对工作树）不存在。全盘 27 个 `*.sqlite3` 无一为该生产库：`data/phase3-repair-loop.sqlite3`、`data/phase4-file-loop.sqlite3`（E 阶段，`succeeded`，无 delivery）；`reports/jh004-real-host-20261014/*.sqlite3` 与 `data/integration-evidence/jh004-20261014/*.sqlite3`（JH-004 真实宿主，`task_jh004_real_20261014_8b880432`，`succeeded`，模板 `records_transform_v1`）；其余为 `.test-tmp`、`data/test-workspaces/jh004-*` 合成库 |
| 2 | waiting_host 的 task/delivery | **缺失** | 扫描全部 27 库的 `tasks`：`_delivery.state == "waiting_host"` 计 **0** 条；模板 `code_sortfix_v1` 计 **0** 条。唯一 `production` 任务是 JH-004 的 `records_transform_v1`，无 delivery |
| 3 | 对应产物（`patch_text`） | **缺失** | 无 code-delivery 任务，故不存在 `metadata._delivery.artifact`（`version`/`artifact_id`/`sha256`/`generation_attempt_id`）与 `artifact_refs == ["mem://<task>/<artifact_id>"]` |
| 4 | 当前审核 | **缺失** | 无对应任务，故不存在 completed reviewer attempt == `delivery.review_attempt_id` == `task_control.active_attempt_id` |

**上游阻塞**：第 1、2 项需要一个**真实生产任务**——真实模型（`gemini_worker` + `muse_reviewer`）生成 patch、真实审核 pass、再 `prepare-code-delivery` 产出 `waiting_host` delivery。这属于真实模型调用，须由架构师宿主会话执行；本批不发起。第 3、4 项随之派生。真实 Codex 固定调用与采集同样由宿主执行。

## 宿主可直接执行的命令

预检（应在工作树根执行）：
```
cd "D:/JH-Agent OS/data/worktrees/n1-artifact-entry"
python -X utf8 -B -c "import src.cli; print(src.cli.__file__); print(hasattr(src.cli,'HOST_ENTRY_COMMANDS'))"
```
期望：路径位于本工作树内，且第二行 `True`。

只读确认当前无 waiting delivery（用可行路径的任意现存库替换 `<DB>`）：
```
python -X utf8 -B -c "import sqlite3,json,glob; [print(p,[ (r[0],r[1],(json.loads(r[2]).get('metadata') or {}).get('_delivery',{}).get('state')) for r in sqlite3.connect('file:'+p.replace('\\\\','/')+'?mode=ro',uri=True).execute('select task_id,status,task_json from tasks')]) for p in glob.glob('D:/JH-Agent OS/**/*.sqlite3',recursive=True)]"
```

生产前置（真实模型；`<REAL_DB>`/`<REAL_WS>`/`<TASK_JSON>` 由宿主提供；**不得加 `--test-fixture-mode`**，否则 `source != production`，登记必拒）：
```
python -X utf8 -B src/cli.py --db <REAL_DB> --workspace <REAL_WS> submit <TASK_JSON> --run
python -X utf8 -B src/cli.py --db <REAL_DB> --workspace <REAL_WS> artifact <task_id>
python -X utf8 -B src/cli.py --db <REAL_DB> --workspace <REAL_WS> prepare-code-delivery <task_id> <artifact_version>
```
`<TASK_JSON>` 须为 `code_sortfix` 形状：`metadata.fixture_id == "n2-code-sortfix/v1"`；`context.target_files == ["target/widget.py"]`；`context.workspace_root` 指向**隔离工作区副本**（其 `target/widget.py` 初始为封存基线字节）；`acceptance_criteria` 等于模板三条；`assigned_worker/assigned_reviewer == "gemini_worker"/"muse_reviewer"`。`prepare-code-delivery` 成功后状态为 `reviewing` 且 `_delivery.state == "waiting_host"`。

计划（只读，取 `invocation.argv`/`call_input`/`apply_attempt_id`/`context_sha256` 与 `workdir`）：
```
python -X utf8 -B src/cli.py --db <REAL_DB> --workspace <REAL_WS> plan-host-check <task_id> <delivery_id>
```

采集（宿主 Codex 桌面会话，`--archive-dir` 必须位于 `C:\Users\a\.codex\sessions` 子树内，否则 `source-untrusted`）：
```
python -X utf8 -B scripts/probe_host_capture.py --protocol desktop --thread-id <TID> \
  --segment <rollout.jsonl> [--segment ...] --item-id <exec-id> --call-id <call-id> \
  --expect-argv-json <argv.json> --expect-workdir <WORKDIR> --expect-exit 0 --expect-marker PASS \
  --task-id <task_id> --delivery-id <delivery_id> --apply-attempt-id <来自 plan> \
  --artifact-claim-json <json> --review-claim-json <json> --archive-dir <ARCHIVE_DIR>
```
（`expect-exit 0` / `marker PASS` 取自封存 `checks.json` 的 `post-apply-target-check`。）

登记（要求隔离工作区 `target/widget.py` 已等于 `expected_after`）：
```
python -X utf8 -B src/cli.py --db <REAL_DB> --workspace <REAL_WS> register-host-capture \
  <task_id> <delivery_id> <ARCHIVE_DIR> <TID> <exec-item_id> <call_id>
```

查询（只读）：
```
python -X utf8 -B src/cli.py --db <REAL_DB> --workspace <REAL_WS> capture-binding-status <task_id> <delivery_id>
```

## 边界

- `current=true` 与 `apply_attempt_id` 的 capture-check 标识不构成生产交付成功证据。
- 本清单不含合成回填；plumbing 档与 synthetic 测试不得代用真实数据库/真实任务/真实归档。
- production complete 与 FileToolExecutor 保持关闭；本批无真实模型调用、无用户项目补丁应用。
