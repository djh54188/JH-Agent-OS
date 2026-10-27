# Hark-派单-26 交付报告：首次 Gemini 任务入口的三项边界收口

来源：Hark B｜Hark-派单-26｜类型：整改交付。状态：候选，待架构师独立复核。
真实模型/Node/CLI/Gemini/用户代理调用：0。RunReal/Candidate 用户操作仍禁止；修订 Prepare 审查前禁止运行。

## 0 基线
- 分支 hark-a-gemini-task01-entry-20261026；开工本地=远端=9a0c86c0f379ac0bc1d26c06be930c8e5fff6535，工作区干净，无并行写入。
- AGENTS.md sha256 502aa8292c7b4e574156feecc7c09264992f0fea5b7092b30ec4d1cb8d9516cf（一致）。
- 功能提交 5136d657a7455dff354ad642c66e5583ce7071fd（唯一父 9a0c86c）。报告提交为其子提交（本文件所在提交）。

## 1 允许差异（相对 9a0c86c，功能提交）
- 改：scripts/gemini_task01_run/entry.py、scripts/win/hark24-gemini-task01.ps1、tests/test_hark24_gemini_task01_entry.py
- 增：tests/fixtures/hark24/boundary_run.py
- src、scripts/gemini_task01、hark21 测试/夹具、AGENTS、docs、reports（旧）差异 0。报告提交只新增本报告与 reports/hark26-entry-boundary-20261027/。
- sha256：PS1 ba730570b630b70d58af0b2548c5b1a97c0586311f29f1a6428036f2ac23eb9c（blob dec3c0f2，纯 ASCII）；entry.py dfd4938806c8d6c9981c17a7bd7cd1dc372085cd102d4f6d890b228cc90281a9；测试 a6bf603768eeb60f62cfb17915e9e37184aff105583f775f1a7723c4935a3916。
- PS1 钉住树：src eef830e7…、scripts/gemini_task01 a7cbc257…、scripts/gemini_task01_run 4675e834281dec392bda64ab42ab6c6ec9f4c204（= 5136d65 实际树）。

## 2 整改
### 一 实际磁盘字节
- PS1 新增 Assert-DiskBytes，在 Prepare（fetch/检出/Assert-Repo 之后）与 RunReal/Candidate（Assert-Repo 之后）、任何仓库代码运行前执行：对 src、scripts/gemini_task01、scripts/gemini_task01_run 递归，以及仓库顶层（非递归），用 ls-tree 取提交 blob；磁盘枚举不跟随链接，ReparsePoint 属性（含 Unix 符号链接）即拒绝；文件集合必须相等（多/少均拒绝），逐文件读取实际字节计算 git blob id 与提交比较；不可读 → 异常 → 拒绝。
- entry.py 在 `__main__` 下、导入 check_run/pins/src 之前做同样核对（相对 HEAD）；失败输出 REFUSED exit 2，不导入。entry.py 自身字节只能由 PS1 保护（Python 无法验证正在执行的自己）。
- 原 HEAD/树/干净（含 ignored）检查保留；不清理 assume-unchanged/skip-worktree，不 reset，不自动修复。
### 二 导出边界先于读取
- 新 export_tree：只 lstat/listdir，不读内容。根：lstat 非链接、无 FILE_ATTRIBUTE_REPARSE_POINT/st_reparse_tag、是目录、realpath == realpath(work_root)/target；每个子目录/文件：同样的链接/重解析判定，且 realpath == 父目录真实路径/名字；非普通文件、OSError（缺失/不可读/枚举失败）→ 立即停止。只有零问题才比较集合并按钉住名读取。
- 未扩到祖先治理、通用沙箱或 TOCTOU。
### 三 宿主/核对器派生字段
- public_host_fields：state ∈ A 的 RunState 8 值否则 unrecognized；declared_status ∈ {success, unreported, unattributed} 否则 host-other；mechanical ∈ {all_pass, fail, undetermined}；check_exit ∈ {0,1,3}；host_versions → 钉住值 / mismatch / absent；error_class → 内建或 A 模块定义的异常类名，否则 other。
- 终端（terminal_view）与候选（public_summary）共用该映射；候选 check 项只列 B 固定检查名与 tools.(read_file|grep_search)__N，其余只计数 unlisted_items；状态/points 用枚举；outcome/stage 用本入口枚举；证据文件名只保留已知名与 run_id 组成，宿主命名文件改为 file-NN（保留字节数与 sha256）。
- 私有 run-real-result.json / check-report.json 保留原字节。未知值从不映射为 success/all_pass。

## 3 测试（logs/，完整未截断）
环境 Linux aarch64，CPython 3.12.15，官方 pwsh 7.4.6 linux-arm64。
- hark26-tests-run1..3：每次 Ran 41 tests OK（25 的 31 项 + 26 新增 10 项），跳过 0。
- post-commit/tests.txt：干净 detached worktree（5136d65，status --ignored 0 行）Ran 41 OK。
- 一 磁盘字节：7 种隐藏改动（assume-unchanged 换 entry、skip-worktree 换 entry、skip-worktree 改导入组件 src/agent_session.py、替换 pins.py、info/exclude 隐藏的影子模块 scripts/gemini_task01/ast.py、skip-worktree 删除 src/memory.py、chmod 000）均使 HEAD/树不变、status 为空；PS Candidate 与 RunReal 各 7 例全部 HARK24-PS REFUSED exit 2，探针哨兵不存在；正常控制通过 PS 检查、驱动运行。Python 直跑对 5 个导入类案例 REFUSED，哨兵不存在，无 traceback。
- 二 边界（审计钩子按 realpath 统计 open）：target 根、target/src、单文件分别换成指向同字节范围外副本的符号链接 → REFUSED，范围外读 0、target 下读 0、派发 0；模拟重解析（in-process 给 lstat 结果加 0x400/junction tag，根/内部目录/文件三例）→ REFUSED，target 下读 0；缺失、chmod 000 → REFUSED（not enumerable）；正常 → COLLECTED/all_pass，派发 1。
- 三 字段：宿主 result.status 分别为 secretkey、secret_key、Zq9、长哨兵，环境保留/清空各一 → 终端与候选字节都不含该值，declared_status=host-other，state=failed，exit 非 0，候选 overall 非 all_pass；私有结果含原值。普通失败 error → host-other；正常 → success/declared_complete/all_pass，unlisted_items 0。Node 版本反射同 4 值 → REFUSED，host_versions.node=mismatch，终端无值，派发 0。
- 保留项回归通过：正控、PS 退出码传播、摘要绑定、同 Parent 冻结、派发后 UNKNOWN/异常结果、Prepare 最小环境。
- 削弱（extract-output.txt 四份日志 19 项全部 detected）：
  - disk_identity（Python 早检关闭）：探针 未运行 → 运行
  - PS disk_bytes（删 Assert-DiskBytes）：exit 2/未运行 → exit 0/运行
  - export_boundary（换回旧 walk）：REFUSED、范围外读 0 → 派发并 COLLECTED、范围外读 85
  - host_fields（declared_status 直出）：终端/候选均不含 → 均含 secret_key
  - 其余 25 项不变。
- 未重跑 113/265/全仓。

## 4 证据更正（25 原件保留不改）
- 25 报告中驱动树 67c07ced 为旧值；25 实际 PS1 与 d671411 的 scripts/gemini_task01_run 树为 24280ad051befe05416693dc1ebecaa336fac106。
- 25 提交后 WEAKENING25 行在复验日志中被截断（at "all_pas"），只能确认 Ran 31/OK，不能逐项证明“一致”；25 目录未附提取脚本。
- 26 起：日志为 unittest -v 全量输出，不截断；提取脚本 reports/hark26-entry-boundary-20261027/extract.py（仅标准库），命令：`python reports/hark26-entry-boundary-20261027/extract.py`，退出 0 = 所有日志 OK 且全部削弱被检出；输出存 logs/extract-output.txt。

## 5 偏离 / 未覆盖
1. 真实 Windows junction/重解析未测：Linux 用符号链接实测，重解析属性分支只用 in-process 模拟覆盖，不等于 Windows 实测。
2. PS5.1 未实跑；pwsh 7 不等于 5.1。PS 的 ReparsePoint 判定在 .NET Framework 上依赖文件属性（预期可用，未测）。
3. Python 层无法自证 entry.py 本身字节；该项只由 PS1 覆盖。
4. 磁盘核对只覆盖可导入目录与顶层；其他目录（docs/gui 等）不导入，未核字节（仍受 status --ignored 约束）。
5. check 项名单与证据名单是按 B/A 现有代码固定的；B/A 将来增加检查名会只计入 unlisted_items（保守）。
6. Prepare 可能因工作根祖先 .env 返回 NEEDS_SETUP（A 既有预检），不搬移/删除/放宽。
7. 无真实 GitHub fetch、真实认证、真实模型。8787 仍是 Anthropic 协议，不可作 Gemini 原生 endpoint。

## 6 修订 Prepare 命令（审查前禁止运行；离线，不派发）
见交付消息中的命令块：先下载固定提交的 PS1，机器核对 sha256 ba730570…eb9c，不符即删除并停止；工作根 D:\JH-Agent OS\data\gemini-task01。RunReal/Candidate 不准入。
