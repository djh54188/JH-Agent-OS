# 文件应用机制说明（B2-01/02/03 整改实施依据）

日期：2026-10-13（第二版，取代初版的守卫常驻发布描述）。
状态：实现草案，未验证（本会话无可用 Shell，不能运行测试）。
适用：`src/file_tools.py::FileToolExecutor.apply_staged` 提交路径重写。

## 实测结论（第二批复核，两轮一致）

- B2-01：持有目标文件守卫（GENERIC_READ + SHARE_READ|SHARE_DELETE）时，
  合法 `os.replace` 报 WinError 5；关闭守卫后同一替换成功。文件守卫与
  自身发布不兼容是实测事实，不再猜测更精细根因。
- B2-02：最后身份校验后，子进程更名旧目标并重建并发版本，随后被覆盖且
  succeeded。路径发布本身不可证明，不接受该残留。
- B2-03：新文件发布前父目录被换成 junction，兄弟目录先被创建候选文件，
  之后才报拒绝。事后检测不能代替阻止。
- R1 更正：普通预打开 O_RDWR 句柄导致守卫获取失败（共享兼容双向检查），
  初版“其能在已持有守卫时写入”的前提错误，不再沿用。
- R4：支持范围限定 Windows NTFS，但须代码落实，文档不得超前宣称。

## 目标

- B2-01：正常覆盖发布与新文件发布在守卫机制下成功，不靠提前释放文件
  守卫、不靠开放写共享换绿灯。
- B2-02：目录项级并发（更名/重建）在攻击者系统调用处响亮失败；合法
  发布继续成功。
- B2-03：父目录竞态在产生任何副作用前被拒；来源、目的地、清理路径全覆盖。
- R1/R4：说明与实现一致；未验证情形明确标注。

## 机制（按执行顺序）

1. 复核与备份不变：路径重解、授权、暂存哈希、初始版本比对、候选重读、
   核验备份。
2. 隔离暂存候选不变：短写循环 + fsync + 哈希回读，失败删临时文件后按
   I/O 错误上报。
3. 支持范围门禁（R4）：发布节开始即 `_require_publish_volume`，非 Windows
   或非 NTFS 直接拒绝，不触碰目标。
4. 新文件分支：目标必须不存在；用 `_DirGuard` 钉住目标父目录（SHARE_READ，
   从暂存校验后持有到 rename 返回）；目录项级重验证后 `os.rename` 发布。
   rename 在目标并发出现时原子失败。
5. 已有文件分支：
   a. `_TargetGuard`（SHARE_READ，不含 delete-share）建立版本基线并拒绝
      多链接；此期间新写打开与更名/删除打开均被拒。
   b. `guarded` 相位后获取 `_DirGuard` 钉住父目录；`pre_publish` 相位后做
      最终重证明（文件句柄身份 + 内容重读 + 路径归属，目录句柄身份 +
      路径归属，全部新鲜重解）。
   c. 用 `detached_reader` 保留旧对象读取器，紧邻发布调用前关闭文件守卫
      （B2-01 实测：持有则自身 replace 必败），相邻语句执行 `os.replace`。
      父守卫全程持有，目录项竞态仍被挡。
   d. 发布后经保留读取器重读旧对象：与基线不一致则记
      `publish_window_drift` 并抛 `PublishWindowDriftError`，控制层转
      needs_attention，不重放、不记成功。
6. 发布后回读确认不变：目标哈希须等于评审哈希，否则 uncertain。

## _DirGuard（Windows，新增）

- 以 `GENERIC_READ` + `FILE_SHARE_READ` + `FILE_FLAG_BACKUP_SEMANTICS`
  打开目标父目录，从暂存校验后持有到发布返回。
- 前提 P-A/P-B 由共享矩阵报告逐宿主证明：同守卫下自身 rename/replace
  成功；子进程创建/目录内更名/目录自身更名被拒。任一前提不成立则采集
  与发布调用响亮失败，永不静默跳过守卫。
- 建立时双重钉选：路径重解 + 包含性复查，以及二次按路径打开比对
  FileId；发布前同样重做一次。

## _TargetGuard（变更）

- 共享改为纯 SHARE_READ（去掉 delete-share）：既有 B2-01 表明该组合也
  不足以放行自身 replace，而去掉后更名/删除打开同样被拒，属收紧。
- 发布前释放是 B2-01 实测强制的结果，不是弱化：释放点与发布调用相邻，
  其间目录项竞态仍被父守卫阻挡；唯一暴露的是纯数据写邻接窗口。
- R1 更正：预打开不兼容句柄导致获取失败，失败本身是保护（拒绝先行）。
  映射节、备份句柄、过滤驱动等未穷尽情形标未验证。

## 失败分支与错误分类

- 校验/边界/版本/单链接/范围门禁失败 → `PathBoundaryError`（拒绝，
  目标未动）。
- 暂存/发布阶段 I/O 错误 → `OSError` 原样上抛（控制层记 `failed` 并附
  恢复分类），不包装成边界拒绝。
- 发布邻接漂移（旧对象变化或不可读）→ `PublishWindowDriftError`
  （控制层记 `failed`、不确认副作用，转 needs_attention，不重放，
  不走恢复即成功路径）。
- 发布后哈希不符 → `SimulatedInterruptionError` → `uncertain`，保留现场。

## 残留窗口（诚实边界，不宣称消除）

- W1 最终邻接纯数据写：文件守卫释放后、replace 返回前，外部新写打开
  可进入并落盘，随后被旧对象重读捕获，一律报 uncertain（不清静成功）。
  本窗口无法以纯 Win32 文件 API 消除（保持文件守卫则自身发布必败），
  是否接受由架构师裁决；候选对照见回归
  `test_replace_entry_window_write_reports_uncertain`。
- W2 守卫建立前的路径交换：两次打开比对 + 包含性复查覆盖已发生的交换，
  两次打开之间的亚调用窗口残留。
- W3 暂存清理路径：清理仅删 uuid 唯一临时名并拒绝链接；暂存目录本身被
  换的极端情形只产生垃圾文件，不产生越界写入。
- W4 未验证：非 NTFS/非 Windows 一律拒绝；映射节等其他句柄类型未穷尽；
  MoveFileEx 内部访问细节未追踪，不作定论。

## 事件与故障相位（兼容既有测试）

保留 `apply_start/backed_up/temp_written/replaced/confirmed` 相位语义与
`apply_start/backup_created/temp_written/replaced/apply_confirmed` 事件；
沿用 `guarded` 相位（守卫已钉住），新增 `pre_publish` 相位（全部重证明
完成后、发布调用前）与 `commit_parent_guarded` / `publish_window_drift`
事件。生产调用永不传入注入器（沿用既有 seam 约定）。
