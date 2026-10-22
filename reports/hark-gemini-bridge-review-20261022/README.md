# 冻结独立反例与复现说明

对应报告：`../hark-gemini-bridge-independent-review-20261022.md`。
固定候选：`7a41ac1673adb5cf202b12a25c6c83ae8fdbb659`。
这些是审查材料，不是产品实现，不要求将其合入 Hark 产品分支。

`windows-round-04.json` / `windows-round-05.json` 是 Windows 11 / Python 3.14.7 的两次原始取证结果。43 格不是通过数；`probe_boundaries.py` 的 exit 0 只表示取证完成。字段中的 launches 是内存进程替身调用数，不是真实 CLI 或模型调用。`audit-summary.json` 记录独立日志读取、模板字节对照与两轮结果归一比较；`windows-selected-original-tests.txt` 是四个原始定向测试，3 通过、1 断言失败，临时目录接缝见主报告。

## 独立复跑探针

在本目录副本旁准备 `candidate/`。从可读 GitHub 副本取固定候选文件，避免覆盖正在实现的工作区。以下命令只归档固定提交，不 checkout/reset/merge：

```python
import io, pathlib, subprocess, tarfile
repo = pathlib.Path('/absolute/path/to/JH-Agent-OS')  # Windows 改为实际仓库路径
review = pathlib.Path('/absolute/path/to/review-copy')
candidate = review / 'candidate'
candidate.mkdir(parents=True, exist_ok=False)
data = subprocess.check_output([
    'git', '-C', str(repo), 'archive', '--format=tar',
    '7a41ac1673adb5cf202b12a25c6c83ae8fdbb659',
    'src', 'tests/test_hark11_gemini_session.py',
    'tests/test_jh018_agent_session_contract.py', 'tests/fixtures/gemini_cli',
])
with tarfile.open(fileobj=io.BytesIO(data)) as archive:
    archive.extractall(candidate, filter='data')
```

将冻结 `probe_boundaries.py` 放在 review 目录，然后：

```text
python -X utf8 -B probe_boundaries.py --round new-round
```

每次 round 必须是新目录，不改写本次冻结结果。Linux 上 Windows 路径转义格未必复现；应以 JSON 数据替换和合成 Windows 路径自建反例，不能声称完成本机 Windows 验收。probe 对比的模板按 JSON 对象替换路径，刻意与有缺陷的原 fake_cli 文本替换分开；不是代修产品。

## 独立审计脚本的输入

`audit_evidence.py` 的本机原始布局为同级 `remote/` 和 `candidate/`，另有 `round-04/results.json`、`round-05/results.json`。`remote/` 是 `download-manifest.jsonl` 所列固定候选文件，可从固定提交恢复；不是本机私有会话数据。复跑时将两份冻结 windows-round 文件复制到相应 round 路径，或在自己的脚本副本明确调整输入路径。脚本读取完整日志但只运行四个选定原测试；不执行完整套件、真实 CLI、网络或模型。不要把复制旧结果当作新探针运行。

原始源提交的完整日志 / 宿主记录均保留于原分支，本分支不复制依赖包、私有 data、用户 key 或真实聊天。
