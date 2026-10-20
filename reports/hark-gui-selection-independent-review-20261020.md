# Hark-派单-05：GUI选择与详情定位独立复核

日期：2026-10-20（Asia/Shanghai）。当前指定Codex架构师独立复核；未接管实现或运行完整套件。

## 裁决

固定候选068afb504bfcffdcbb9ba45ce056fd61b6a916ef通过本批云端候选复核，准许Windows精准集成。报告33d68fb5829e4b5a312279b9c40c2626eb403ddf。尚未本地移植，不代表Windows浏览器验收或完整产品闭环。

接受报告偏离①：单列非空详情区的min-height仅限.panel-history，服务于点击后的标题定位，不改变数据、请求或权限。其余附加断点验证、当前查看文字及云端字体依赖均在本批展示/取证范围内，无需返工。不要求为键盘、读屏、加载中手动滚动等已披露未测项另开整改。

## 范围及身份

- 本地/源基线3d6611a6d5d63c29b260bc00db7fee728a67a45b；工作树干净，除用户归属的未跟踪历史归档。
- 功能提交父节点为该基线；报告提交父节点为功能提交。功能仅四文件、+582/−0；报告提交仅reports下文件。没有合并main或PR#1。
- 独立取得35个文件（含截图及完整日志），逐个核对Git blob与固定提交一致；磁盘回读SHA-256留在本地data/hark-gui-selection-review-20261020/download-verified.json。
- gui/components.js、gui/data_access.js、gui/index.html、src和既有测试/守卫不在差异中。现有loadDetail函数全文逐字相同，四个关联守卫锚点各一次。

准入四文件均固定于068afb5：

| 文件 | Git blob | 磁盘SHA-256 |
|---|---|---|
| gui/views.js | 1a2a1d688517aa9caf056651c168804a69247121 | f6eba51f351e27873c7cd7f20b9431bf3ef38e01695023128fc387b6e5556215 |
| gui/styles.css | 47227e20767afc6fe8828ef1db877f87ad774840 | cac2ee9c9e3878d9b14e700e47c8f187ec996a169c3ebbdaa1984afe2d5fb7e8 |
| tests/js/hark05_run_selection_harness.mjs | 3a92829db740db4414aabd2a4ec593c24ba93c7d | d650552ed9e304b7a0d11ac51d920a78bffd1e103ca55fec7957aaa540f36aca |
| scripts/verify_hark05_selection_guards.py | 7e24bd31bba73b11f7ab76498556fd533c2a30f7 | 2e0fa55f14b2b4bcf44897909582377ae65b25ac857040bc81ce3ad28686abd1 |

## 独立证据

候选四文件只叠加在忽略目录data/hark-gui-selection-review-20261020/snapshot，不写入生产GUI。Windows/Python3.14.7/Node24.19.0下独立运行离线验证，不作为浏览器实测。

| 检查 | 结果 |
|---|---|
| 既有JH019/JH020前端 | 独立4/4、13/13通过 |
| 新选择行为 | 独立11/11通过；同份新增测试指向正式基线GUI时1通过、10项断言失败，异常0 |
| 新定向削弱 | 对照通过，3/3断言检出；所有失败场景均为assertion，没有用异常计数 |
| 既有前端变异 | 独立2检出+2冗余，pass，不恢复成4检出 |
| 源码接线 | markSelected、loadDetail、revealDetail只在明确点击中接续；响应回调无选择/滚动写入；JS与CSS断点均1100px |
| CSS范围 | 新规则均限历史面板；两列不主动定位，单列只在点击时定位一次 |
| 浏览器原始数据 | 独立解析五宽度JSON，A/B各次唯一选中、标题可见、overflowX=0；1440/1101点击前后scrollY相等；1100/760/480标题top约31；每宽度只有两次详情GET |
| 图片 | 亲自查看1440视口和选中卡、760视口、480视口和选中卡共五张。选中描边、按钮和文字标记可辨；标题定位正确；图片均有Linux合成预览标注 |
| Linux完整日志 | 读取四份全量正文：均Ran704；有代理27失败/78错误/2跳过，无代理21/63/2，基线/提交后同条件失败ID逐条相同。不是Windows通过，架构师未运行完整套件 |

已检查的时序类：无选择、A到B、同项重复、乱序成功、迟到错误、错误run_id、离开重进、加载/错误/空、宽窄与1100/1101边界、仅详情查询。新场景和原守卫分工保留，未据单一快照声称穷尽输入。

本地详细证据：scope-and-logs.json、browser-audit.json、independent-baseline-contrast.json、三个*.mjs.json及existing-guards.json/new-guards.json。临时目录仅用进程内继承ACL的mkdir接缝，未改系统ACL、全局配置或候选文件。

复跑命令（<副本>为候选隔离目录；结果写新目录，不覆盖既有证据）：

```text
node <副本>/tests/js/jh019_generation_harness.mjs --json
node <副本>/tests/js/jh020_run_history_harness.mjs --json
node <副本>/tests/js/hark05_run_selection_harness.mjs --json
python <副本>/scripts/verify_jh020_run_history_frontend_guards.py
python <副本>/scripts/verify_hark05_selection_guards.py
```

浏览器资料来自固定33d68fb的Linux合成预览。本次没有启动浏览器或真实后端，没有调用模型或发送会话。Windows字体、缩放、真实GUI服务和真实历史须由执行AI-派单-06集成验证；浏览器内乱序与键盘/读屏等未测项保持披露，不冒充已测。

## 下一批边界

执行AI-派单-06仅移植上述四文件、完成Windows离线/完整套件/浏览器复验并交付。预期Python套件仍704/703通过/1既有跳过，因为本批未新增Python测试方法。合成多记录和真实旧历史分别标来源；原5b1dc26真实历史只读沿用，不重新调用模型。main、Hark分支、PR#1不合并，不扩后台或GUI派发。

Hark暂等集成。用户已明确后续Hark派单采用目标式：说清目标、基线、边界、验收和停止条件，具体设计/实现由Hark决定；不继续逐函数指定做法。此偏好不追溯改变已完成的派单05。
