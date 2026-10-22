# 执行AI08独立复核材料

裁决见同级 gemini-windows-probe-independent-review-20261023.md。execution-task-09.txt是本轮首次生成的唯一完整09提示词，尚未确认转发。

review-summary.json与audit_execution08.py来自本地忽略目录 data/execution08-independent-review-20261023/。脚本依赖本机08 worktree、Hark13固定快照及08原始运行目录，应在完整证据目录运行；本目录仅保存审查摘要，不是候选源码或完整运行证据包。

脚本只读已有源码/证据，执行两个冻结测试方法的控制/内存削弱共四格；不运行Gemini、完整套件或真实模型。原证据绝不覆盖，摘要中逐文件SHA-256绑定被查原件。原始证据hash一致不自动证明其采集时的全部环境或所有运行尝试已完备。

Node官方支持表及下载目录、微软fastfail文档已在线核对，链接在独立报告。官方固定版本SHASUMS本轮未读取成功，不编造摘要；下载/校验/Node22实际运行属于执行AI09，尚未执行。

本轮材料仅本地，未提交、上传或合并；08和原树现有成果保留。
