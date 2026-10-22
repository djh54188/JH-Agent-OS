# 派单12独立复核派生材料（仅本地，尚未发布）

裁决见 `../hark-gemini-admission-independent-review-20261022.md`。固定候选 d9cbbf7df5033663470a9912ba3b13f15157a21a，功能 be61b924b2bf7e41d5ad2d6718b927c81440676e。

本目录内容均为合成反例、独立提取结果和少量原始定向测试日志；不含用户凭据或真实模型记录。Windows 路径 / 随机 run_id 是取证原件中的实际值，不需要抹平以匹配预期。所有发布 / 复跑使用副本，不改冻结文件。

## 最小复现新对象重开尾项

把 `frozen_probe_boundaries.py`、`probe_reopen_boundary.py`、`probe_reopen_variants.py` 放到一个干净的 review 目录，在其下准备 `candidate/`。从 Hark 副本归档固定候选的 `src/` 和 `tests/fixtures/gemini_cli/host_probe_stream.template.jsonl` 到 candidate；不 checkout/reset/merge 现有工作树。

```python
import io, pathlib, subprocess, tarfile
repo = pathlib.Path('/absolute/path/to/github-copy')
review = pathlib.Path('/absolute/path/to/review-copy')
candidate = review / 'candidate'
candidate.mkdir(parents=True, exist_ok=False)
data = subprocess.check_output([
    'git', '-C', str(repo), 'archive', '--format=tar',
    'd9cbbf7df5033663470a9912ba3b13f15157a21a',
    'src', 'tests/fixtures/gemini_cli/host_probe_stream.template.jsonl',
])
with tarfile.open(fileobj=io.BytesIO(data)) as archive:
    archive.extractall(candidate, filter='data')
```

```text
python -X utf8 -B probe_reopen_boundary.py
python -X utf8 -B probe_reopen_variants.py
```

两脚本分别新建 reopen-boundary-01、02；原件目录已存在时应在新 review 副本跑。它们只调用内存 launcher，零外部进程 / 网络 / 模型。exit0表示完成取证，不表示候选通过。旧 owner 的前缀检查没有被关闭；关键是第二个对象在登记丢失后才创建。

`windows-reopen-01.json` / `windows-reopen-02.json` 是原始结果：空 / 删除 / 空白重复出现新增派发，非空合法历史丢末尾 run 也新增派发；新存储、完整历史、保留各 run 的 reserved 前缀和坏 JSON 是相应对照。

## Windows 锁与其他证据

`probe_process_lock.py --round fresh-name` 使用独立 Windows Python 进程和文件同步，不用被当前沙箱拒绝的 multiprocessing.Queue 命名管道；验证同键、不同键、锁占用三格，launcher仍为内存替身。对应 windows-process-lock.json。不是 Gemini CLI 在 Windows 上的宿主权限证明。

`review-summary.json` 由 `audit_review.py` 读取固定原始日志、两轮冻结探针和定向测试结果生成；原始输入在本地忽略目录 `data/hark12-independent-review-20261022/`。download-manifest.jsonl 固定远端候选源文件与摘要。初次环境错误及后续选择10项的记录都保留本地，不把补齐夹具 / 另建进程探针写成原11项全绿。
