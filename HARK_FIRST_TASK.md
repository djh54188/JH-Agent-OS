# Hark 首轮实战：JH-006 已审查快照的独立离线复核

你正在接手 JH-Agent OS 的 GitHub 实战副本。先实际访问并检出 <https://github.com/djh54188/JH-Agent-OS>，确认仓库根目录存在 src、tests、CLOUD_START_HERE.md 和 UPLOAD_MANIFEST.json。不能读取时先报告具体权限或工具错误，不能依据仓库名字编造检查结果。

在不改变生产源码、既有测试/封存夹具、Schema、Skill、供应商或权限的范围内，直接执行下述任务。只提交新建的首轮复核报告与必要的独立探针；不要重做本地正进行的 CLI/Skill 宿主接线。当前这轮允许的代码工作仅为独立探针，不是生产修复。

1. 记录检出分支/HEAD、Python 版本、操作系统和 Shell；读 CLOUD_START_HERE.md、STATUS.md 的最新段、docs/host-delivery-contract.md、docs/n2-acceptance-spec.md、reports/jh006-binding-review-20261016.md。区分副本源提交 1f605c3 与 GitHub 当前提交号，不假设云端有 D 盘或本地模型代理。
2. 实际运行全量 unittest 及 CLOUD_START_HERE.md 列出的四个离线检查。保留退出码、测试数量、失败/错误/跳过及变异检出/冗余/异常。异常不算检出；缺平台能力的用例报告阻塞，不通过修改测试或扩大 skip 消除失败。原成绩不是本轮成绩。
3. 独立复核 src/capture_binding.py、src/host_capture.py、src/storage.py 与现有拒绝路由，重点检查：当前 target/review/attempt/artifact 漂移是否拒绝；调用者重申 echo PASS 能否伪装控制层固定检查；归档/真实来源绑定、跨任务/重复 call/item、事务回滚、预算未知、production complete 关闭是否保持。需要探针时放 scripts/hark_pilot_probe.py，只用显式 synthetic 的隔离工作区，不读取实际用户会话或操作真实用户目标。
4. 生成 reports/hark-pilot-20261016.md：结论（通过/需修复/平台阻塞）、当前 SHA、实际命令与原始结果摘要、问题的文件/行号/最小反例、未验证项、建议下一步。没有复现的问题写推测；没有真正执行的检查写未运行。不把普通通过分数或模型自检当独立架构验收。
5. 用专用分支 hark-pilot-20261016 提交范围明确的报告/探针，并正常 push 到该分支；不改 main，不 force push，不开启 production complete/FileToolExecutor，不调用真实模型 API。不在报告中放凭据、整段宿主日志或账户资料。若无法 push，保留本地成果并给出原始错误和可取回文件，不声称已远端提交。

交付回复包含：实际远端分支和提交链接、复核报告、测试/变异结果、发现项或平台阻塞。当前架构师会话收到后独立裁决，再安排下一批生产修改。
