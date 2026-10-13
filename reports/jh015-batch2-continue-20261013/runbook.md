# 继续整改运行手册（有执行能力的宿主用）— 2026-10-13

本目录对应 JH-015 第二批继续整改（B2-01/02/03 + R1/R4）。实现方本会话无
Shell，全部未运行；以下命令须在真实可执行的宿主按顺序执行。

## 强制运行顺序（不可跳步）

1. 先行门：共享矩阵（证明 P-A/P-B/P-C/P-D 前提）

    ```powershell
    python -X utf8 -B reports/jh015-batch2-continue-20261013/verify_share_matrix.py
    ```

    看 `share-matrix.json` 的 `gate.GO`。GO 才继续；STOP 则**停下**：不要发布
    重设计，不要弱化守卫，把矩阵 JSON 交架构师做范围裁决（备选只有“受控拒绝
    不可证明分支”，且不得把正常功能改为跳过/一律拒绝）。

2. 全量单测（仓库声明入口）：

    ```powershell
    python -X utf8 -B -m unittest discover -s tests
    ```

    期望 156 总计（155 通过 + 1 跳过，0 失败/错误）。新增 6 项：
    replace-entry 更名阻断、window-write 漂移上报、rename-entry 父交换阻断、
    guarded 前父交换拒绝、非 Windows 拒绝、非 NTFS 拒绝；另守卫测试已改为
    记录式断言。

3. 变异守卫（15 项）：

    ```powershell
    python -X utf8 -B scripts/verify_phase4_guards.py
    ```

    期望退出码 0：8 检出（M5/M8/M9/M10/M12/M13/M14/M15）+ 7 冗余存活
    （M1/M2/M3/M4/M6/M7/M11），0 harness_error、0 test_gap。M14/M15 是新增的
    守卫弱化变异，分别映射 replace-entry 与 guarded 测试。

4. 复核对照（架构师脚本，原样重跑看行为变化）：

    ```powershell
    python -X utf8 -B reports/jh016-batch2-20261013/verify_publish_controls.py
    ```

    预期变化：guarded_replace_control 应成功；target_recreated 的子进程更名
    应失败（returncode 非零）且任务成功；parent_at_publish 的子进程交换应
    失败且无兄弟文件。preopened 行为不变（获取失败）。

5. 通过后按明确范围提交（实现 + 测试 + 脚本 + 文档，不夹带并行文档），
   交当前架构师会话独立复核。门禁保持关闭，不宣布结项。

## 改动范围（相对 80979f3 未提交工作树）

- `src/file_tools.py`：新增 `_DirGuard`、`_volume_fs_name`、
  `_require_publish_volume`、`PublishWindowDriftError`、
  `detached_reader/read_detached/close_raw`；`_TargetGuard` 改纯 SHARE_READ
  并在发布调用前相邻释放；发布节加卷门禁、父钉选、漂移检测。
- `src/orchestrator.py`：`PublishWindowDriftError` 独立分支（记 failed、
  不确认、不重放、不记成功）。
- `scripts/verify_phase4_guards.py`：15 变异（+M14/M15）、异常类同步装配。
- `tests/test_phase4_file_tools.py`：`TestJH016Integrity` +6，guarded 改记录式。
- `docs/file-tools-apply-mechanism.md`：第二版（R1 更正、R4 落实、残留 W1—W4）。
- 本目录：`verify_share_matrix.py`（先行门）。

## 已知限制

- W1 最终邻接纯数据写残留：一律报 uncertain，不清静成功；是否接受由
  架构师裁决（见机制说明与漂移回归测试）。
- 无真实模型调用、无随机竞态 fuzz；映射节等其他句柄类型未验证。
