# Hark-派单-04 限定整改独立复核

日期：2026-10-19。角色：当前 Codex 架构师、独立审查者。

## 裁决

R1 stage 类型边界、R2 日志补交、C 旧守卫分类均按本批限定范围关闭。准许本地执行 AI 将固定候选六文件精准移植并完成 Windows 集成验收；这不是本地产品已经合入或完整套件已通过。

- 本地审查基线：`0c2abd802286be5a4d7062bbdbc74f11b4a99249`。
- Hark 源基线：`5e12ead5ee3af0c7b7a63c5aa208e8c61025d687`。
- R1：`ccd9170c728a2ca0f585db13c7c2328502cc143b`；C：`e5f786e35aff473d2766e6c504ed7bf07292c824`；报告：`f6b15cce903c5176bd4221426f1fd2f20b477cd4`。
- 父链和改动范围已由 GitHub 固定提交核对。C 为单独提交的已授权偏离；R1 没有改共享解析器或扩大 API 捕获异常范围。
- 仅在隔离副本叠加候选，未移植生产工作树、未跑完整套件、未调用模型或发送会话。Windows / Python 3.14.7。

## 独立证据

本地忽略目录：`data/hark-history-r1c-review-20261019/`。这是审查产物，不随 Git 自动上传。

| 核对项 | 独立结果 |
|---|---|
| 下载身份 | 六实现/验证文件和 31 份报告证据共 37 个文件，Git blob 全部吻合固定提交；磁盘回读摘要留档 |
| 冻结 stage 反例 | 77 项：7 正常、70 corrupt，异常 0；list/detail 一致，非法项不读证据、源字节不变 |
| stage 扩展类 | 定向测试覆盖 7 分支 × 12 类共 84 格，包括缺失字段；7 正常、77 HistoryInconsistent/API corrupt |
| 定向测试 | 新历史测试 63 项 + 分类测试 9 项 = 72 项，全部通过，无跳过 |
| 新守卫 | 24 对照通过，24/24 断言失败检出，无错误；旧 23 行的 AST 原样保留，新增 1 行 |
| 旧守卫 | Windows 隔离副本实际 8 detected + 1 redundant，exit 0；九行变异表 AST 与本地原版一致 |
| 分类完整类 | 独立 2 种声明身份 × 9 种对照 × 9 种变异 × 9 种诊断 = 1458 格，全部符合预期；异常、混合失败/异常、跳过、零测试、缺结果、多测试、未知存活均不能换成通过 |
| 真实旧历史 | 源 5b1dc26 的本地 runs.jsonl 只读查询仍 ok/count=1/evidence=verified/live=false，字节未改；未重新调用模型 |
| AST | 704 = 632 + 63 + 9；这是独立静态计数，不是架构师全套运行结果 |

探针过程局部用继承 ACL 的 mkdir 替换 tempfile.mkdtemp，避免受限令牌下 Python 3.14 临时目录权限问题。没有改系统 ACL、全局环境、生产代码或候选测试。此环境处理不是产品能力证据。

可重复命令（每次使用新的输出目录）：

```text
python reports/hark-history-review-20261019/probe_stage_shape.py --repo <候选副本> --out <新目录>/stage --expect fixed
python reports/hark-history-consistency-20261019/probe_history_consistency.py --repo <候选副本> --out <新目录>/seed --expect fixed
python reports/hark-history-review-20261019/probe_guard_classification.py --repo <候选副本> --out <新目录>/classification
```

实际结果：frozen-fixed-01/results.json、seed-fixed-01/results.json、classification-fixed-01/results.json；定向与守卫结果在 targeted-tests.txt、new_guards.json、old_guards.json、results.json。下载身份在 download-verified.json；日志核对在 log-audit.json。

## R2 日志判断

固定 f6b15cc 下六份新完整日志都可读取。逐份检查提交、环境、命令、外层 Ran/FAILED 和退出码：基线 632；候选和提交后 704；有代理 27 failures/78 errors/2 skipped，无代理 21/63/2，均 exit 1。同代理条件的三方失败 ID 集合逐条一致，与所附 ids 文件吻合。

正文另有既有测试打印的内层 Ran 1/FAILED(errors=1)，不能把它作为外层套件计数。失败 ID 文本同样含该内层输出，集合对照使用同一提取口径。

03 的六份补交文件明确标为脱敏派生副本，原件摘要、mtime、大小及原先缺命令/退出码、一次覆盖重跑均已披露。原始沙箱文件本审查不可访问，所报原件摘要不能据此声称已独立回算；本地核对的是 Git 固定的派生文件及其正文。时间头的时区标签存在混用，本裁决不依赖其绝对时钟。补交解决日志可读取问题，Linux 失败不折算成 Windows 通过。

## C 分类裁决

接受 e5f786e 的有意偏离。正式单层变异保留 check_history，只有声明的 registry_field_validation_skipped 在对照绿色、单层干净通过且隔离双层关闭产生断言失败时归为冗余。隔离失败 counted=false，未恢复为第九次检出。其他存活或失败类别保持验证失败。

本次实际 Windows 隔离复跑已见 8+1，但仍须执行方在正式本地移植、完整验证和提交后复验中确认，不能将本探针当成正式安装验收。

## 准入文件与后续边界

均固定在 e5f786e35aff473d2766e6c504ed7bf07292c824；精准移植，不合并 Hark 整分支：

| 文件 | Git blob |
|---|---|
| src/run_history_consistency.py | 53ddfe3d9dcddd6718d3899d1200e4c5d1d5ab81 |
| src/app_api.py | 4002df45424f2a1f0b5183508e959a582339c3fc |
| tests/test_jh020_history_consistency.py | 243a0ca33d90d34a22d9d41f8e79c6f1f8e40cfd |
| scripts/verify_hark_history_consistency_guards.py | d82568d42f546b733b7d9a6bf4b1999f175276d2 |
| scripts/verify_jh020_run_history_guards.py | 30aaacefdc9e455a2092663773aab99fa0150e4d |
| tests/test_jh020_run_history_guard_classification.py | 3b1d755ab1b56d9d4f83a71871e5d649ff288331 |

由执行AI-派单-05完成：四个实现/新增验证文件一组；旧守卫与其新增分类回归两文件单独提交，报告另列偏离。允许必要状态文档同步，不要求搬入云端整套报告或私有原始证据。

未覆盖项保持原边界：时间戳单调性、指纹重算、快照内部绑定、任意 extra 完整语义、实际恢复/派发和文件系统并发；reserved→collected 仍按不完整历史拒绝，不顺带改写入端。

GUI 视觉批、既有证据路径边界等已验收项不重做。指定审查、GUI 派发、continue/cancel、N2/P1 整体仍未完成；production complete 与自动文件应用继续关闭。Hark 暂等本地集成，不另开并行整改。
