# Hark GUI Windows 集成：独立验收（2026-10-19）

## 裁决与适用范围

批准执行AI-派单-03 按“三文件视觉移植与 Windows 集成”结项。
功能提交：`b31ce3c82d240a19a72b987b81fdb0dce856d02b`。
执行方报告提交：`2c3f34729447c7b3464f4a9afc37657f7c9efcf3`。
固定云端功能候选：`d000f0cdbbf73781cde30fbd2174aaff6fe7248a`。
共同代码基线：`1d7605dc65d853543f9702025a2f5d0e04265c16`。

本次批准新版 Agent/会话历史列表与详情的展示及公共样式集成。
不授予 GUI 派发、指定独立审查、自动整改或完整协作闭环能力；
N2/P1、production complete、FileToolExecutor 自动应用状态均不因本裁决改变。
加载瞬间与详情错误没有单列浏览器截图，保留为取证局限；
不因既有异步行为守卫未改且独立复跑通过，新增阻断性整改。

## 独立核对

| 内容 | 独立结果 |
|---|---|
| 提交范围 | 功能提交只有三个 GUI 文件；后一提交只有执行方报告，无 src、data_access、index、测试、守卫、规则、.gitignore 改动 |
| 文件身份 | 三文件磁盘 SHA-256、功能提交 blob、报告提交 blob 均与固定候选完全一致 |
| 行为保持 | 规范读取 LF 后，其他视图、refresh/loadDetail/点击委托整段、交付与导航逻辑与基线一致；四个变异锚点各唯一 |
| 前端行为 | 对当前已提交 GUI 独立复跑 JH019 4/4、JH020 13/13 通过 |
| 单层变异 | 在本次隔离副本里独立复跑：2 检出、2 冗余；冗余不计入检出 |
| 完整套件证据 | 三份执行方原始日志均 Ran 632 / OK (skipped=1)，未出现 FAIL/ERROR；架构师没有运行完整套件 |
| 计数 | 独立解析当前 tests/test_*.py 的顶层测试类方法得到 632 |
| 真实历史 | 通过 AppInterface 只读核对列表及详情，run codex-run-1-1779aa1b 的证据均 verified；登记与原始证据文件字节未变 |

三个 GUI 文件的 SHA-256：

- styles.css：`b36f995605faedf1f28bfeef614ebb64b7c95511feb8968950f5ffcaa869875a`
- components.js：`b37ebdfc5508da87c9af313995a3324ca2cb8c087bcd369787e53a91e6c5b673`
- views.js：`1b198c4b9691843a10af01b6294e00a99646f6913920d0f807e7b4422079c47b`

完整套件日志分别为 pre-suite.txt（131.895 秒）、post-suite.txt（131.205 秒）、
post-commit-suite.txt（128.768 秒）；目录为本地
`data/hark-gui-integration-20261019/logs/`。
独立取证记录摘要绑定日志实际磁盘字节；读取时仅为解析统一换行，不改原文件。

## Windows 界面证据与已知局限

架构师亲自查看执行方保存的真实历史页、1440/760/480 详情、
1440 详情滚动前后、七类证据分段截图及其他三页有数据截图。
已查看截图中长标识换行、证据状态及“执行方声明，非任务验收成功”可见；
工作台/任务/交付的合成数据只作样式回归，不作真实成功或文件已应用的证据。
界面合成来源按 fixture 路径、synthetic 字段、截图命名及报告成对解释，
不将局部截图单独当作真实运行。

执行方浏览器指标：1440/760/480 下 scrollWidth 分别为 1425/745/465，
overflowX 均 false；1440 详情 clientHeight=875、scrollHeight=1356、overflow-y=auto。
这些是执行方的浏览器取证，架构师没有再次驱动浏览器独立测量。
480 从 Playwright locator click 回退至 CUA 真实指针，属于取证工具路径切换；
未发现为此修改产品代码，接受此路径，并不把“no click point”的工具报错认定为产品根因。

`evidence-1440-detail.json` 的 `hasDeclared:false` 缺少随附采集谓词，
不将该布尔量解释为通过。原始 GUI 文字、已查看列表截图及独立
declared_complete_is_not_success 场景均保留声明与成功的区别；
本项不支持“所有浏览器布尔指标均为真”的陈述。

未覆盖浏览器瞬时加载帧和详情错误帧；已有完整异步场景独立复跑通过。
未扩展 UNC/设备/ADS/TOCTOU、跨记录一致性和事件顺序；这些不属于本次视觉移植。
未重新调用模型、app-server 或指定会话。

## 真实证据的沿用

沿用源提交 `5b1dc26e425d00b2b00fac93598f3d7ff1f0760e` 的既有只读往返：

- thread：`01a14eef-1d40-7cc0-8bf6-ff268e7ad8ad`
- turn：`01a14eef-2206-7791-b923-1a5b747dd4c4`
- 原始证据：8868 字节 / 26 帧
- SHA-256：`de7f3bb977bc6796d341461ce4943ddfa95da29af27b3d1e86044e0329b9bccf`

这里只证明本次 GUI 读取并展示既有登记与原始字节核对结果；
不代表产生了新的真实派发，也不代表任务验收成功。

## 本地复现与收口

架构师独立探针：
`python data/hark-gui-acceptance-20261019/review.py`。
脚本只复跑前端及只读核对，不运行完整 Python 套件；
变异写在本次新建 mutations/ 副本中，不覆盖原 GUI 或旧探针证据。
结果为同目录 results.json、各场景 stdout/stderr。
该脚本固定本次报告 HEAD，后续仅有架构师文档提交时可通过 git show 核对代码不变，
无需为文档再次运行完整套件。

本轮只提交架构师自身验收文档与交接，不重写执行方提交。
按既有授权只对 n1-artifact-entry 做非强制快进同步；
以本轮本地 sync.json 记录的实际完整 SHA 为准。
本地忽略 data/ 证据和未跟踪的 review-backlog 不自动上传。
选中高亮、自动滚动与新能力不追加派单；无新问题不重复整改。
