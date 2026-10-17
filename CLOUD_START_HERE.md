# JH-Agent OS：GitHub / Hark 实战入口

副本导出：2026-10-16；上传准备更新：2026-10-17。目标仓库：<https://github.com/djh54188/JH-Agent-OS>（用户已改为公开；连接已核验 public 与 push 权限）。

## 本副本的边界

这是已通过离线持久化绑定审查的提交 `1f605c30fbca8573b6865f9cde2a1244f8f9ae4c` 的筛选副本，不携带本地 Git 历史。导出后原工作树继续推进宿主入口等实现，未纳入本副本；Hark 首轮做独立离线复核，避免重复实施本地工作。UPLOAD_MANIFEST.json 的工作树状态记录是导出时快照，不代表本地最新进度。

N1 已结项；JH-006 仅离线绑定通过；N2/P1 未完成。production complete 与 FileToolExecutor 的生产准入继续关闭。Hark 运行测试不等于真实 Codex desktop 来源、真实模型闭环或用户文件应用已验证。

保留：src、tests（含封存夹具）、scripts、configs 示例、docs、原项目 Skill、根入口文档、报告摘要和两份隔离候选源码（用于既有拒绝回灌脚本）。数据库、原始宿主会话/账户元数据、真实配置与绝大多数历史过程证据不上传。逐文件哈希及排除清单见 UPLOAD_MANIFEST.json。原文档部分历史证据链接可能无法在云端打开；这是裁剪范围，不是历史证据已复核。

## 首次运行

在检出的仓库根目录运行，不用文档里的 `D:/...` 作为云端路径。建议先复现原 Python 3.14 环境；云端版本、OS、PowerShell 可用性必须记录。

如果缺少 jsonschema，在项目自己的虚拟环境中安装 requirements-cloud.txt；不要配置真实模型密钥或依赖本地 8787 代理。

```text
python -X utf8 -B -m unittest discover -s tests
python -X utf8 -B scripts/verify_jh005_fixtures.py
python -X utf8 -B scripts/verify_jh005_mutations.py
python -X utf8 -B scripts/verify_jh006_capture_guards.py
python -X utf8 -B scripts/verify_jh006_host_boundary.py
```

Windows 本地验证使用项目内 `.test-tmp` 作为 TEMP/TMP，避免系统临时目录 ACL 问题。云端也用独立临时目录。固定宿主调用当前只支持既有 Windows Codex desktop 包装；缺少 PowerShell 或不支持该来源时，原样报告平台阻塞，不放宽来源门或伪称真实执行。

原审查报告的 359 项 / 358 通过 / 1 跳过是上游历史成绩。本次打包验证记录由当前架构师独立保存；Hark 必须报告自己的实际成绩。需要旧提交 `git show` 的历史重演与升级兼容程序不在本副本验证范围，不补造旧提交。

## 交接与接力

首轮任务完整内容见 HARK_FIRST_TASK.md。后续换会话或合法协作者时，接同一远端分支，先读该分支最新提交和交接报告，再继续。每个可运行检查点保存任务范围、已运行命令、原始结果、未完成项与下一步；提交并推送后记录 SHA，不能只留在聊天里。

文章对免费 Token 数、模型和 GitHub 登录的说法尚未从官方页面证实：<https://github.com/54Lynnn/Guide/discussions/1>。官方定价只说明免费方案及增加使用量的付费方案：<https://hark.com/pricing>；当前登录页展示 Google、Apple、邮箱/手机号：<https://hark.com/login>。先用现有账号验证仓库访问、执行与回传能力，再决定是否扩展协作。

私有仓库需单独给 Hark 的实际 GitHub 连接授权。完成读写试验前，不能把 Codex 这边的仓库权限当作 Hark 已连接。普通仓库写入按所需最小权限配置，不根据文章给所有协作者设置 Organization Owner：<https://docs.github.com/en/organizations/managing-user-access-to-your-organizations-repositories/managing-repository-roles/repository-roles-for-an-organization>。
