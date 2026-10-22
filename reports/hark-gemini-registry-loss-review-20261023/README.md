# 派单13独立审查材料

本目录只保存独立结果、审查脚本与执行AI08唯一提示词；不是产品安装，也不是候选完整源码。裁决见同级 `hark-gemini-registry-loss-independent-review-20261023.md`。

完整本地证据位于真正工作树的 `data/hark13-independent-review-20261023/`（Git忽略）：remote 为固定29e0020下载原字节，candidate 为独立审查快照，manifest逐文件绑定Git blob、字节数与SHA-256。基础快照承接Hark12独立取证并覆盖本批六个固定文件，没有覆盖产品树。

复现脚本在完整证据目录内运行，依赖其 candidate / remote 及旧Hark12取证；不能直接在本精简目录运行并误称源码已齐全。冻结脚本不会改判据，输出目录要求新建；不要覆盖既有结果。audit_review.py只提取已有日志，绝不运行完整套件。

架构师实际只执行登记重开、目录故障、预留失败、两目标削弱及Windows进程锁小探针。原生CLI材料来自Hark的Linux合成端点；真实模型0。Linux整套结果是日志复核，不是架构师重跑。针对原测试的runner仅用普通mkdir处理已知Python3.14临时目录ACL，候选字节保持。

执行AI08首次发行，当前仅已生成，待用户转发；Hark13已正式交付并按离线范围接受。本目录、报告和交接仅本地保存，未提交或上传。
