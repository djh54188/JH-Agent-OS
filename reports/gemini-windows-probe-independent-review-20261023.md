# 执行AI-派单-08 独立复核与 Node 运行环境裁决

日期：2026-10-23，Asia/Shanghai。角色：架构师 / 独立审查者。

## 裁决与失效条件

**批准隔离验证 Node 22 LTS；不采用已结束支持的 Node 20。执行AI08整批不验收，Windows宿主仍未就绪，真实模型保持0。** 保留已核对的安装字节、读取/搜索及局部宿主限制证据，不将其升级为完整29e0020候选或全部Windows路径通过。下一批执行AI09回到指定源码基线，用同一CLI作受控Node版本对照；不改适配器成功门，不吞掉退出崩溃。

Node官方发布表本轮显示22为LTS、20为EOL：[发布表](https://nodejs.org/en/about/previous-releases)。官方22下载目录本轮列出22.23.4：[官方目录](https://nodejs.org/download/release/latest-v22.x/)。本轮没有下载Node；固定release页/SHASUMS读取未成功，因此不填未经取得的下载摘要。执行方须从官方来源固定版本并校验SHA-256，不能把目录可见当作包已验证。

## 交付身份与实际基线

- 用户转发来源：执行AI｜执行AI-派单-08｜交付报告。08已发送且已执行，不再按“未发送”处理。
- 本地提交：`fdec9c6876e5def7432fe5e8c1e9ec99f425431d`，分支/工作树为 `gemini-windows-probe-20261023`。提交父为 `03d9a0224fd93917ed3a1b9d514e465dfc996825`，不是派单指定的 `29e00201f85063f60cc23213262fc82cf430d7ee`。本地对象库尚无29e0020对象。
- fdec9c6增量15文件，包含迁入的适配器/测试/文档及报告，不是只加验证报告。13个迁入文件与上轮固定29e0020独立快照逐字节相同，AgentSession公共契约另核也相同；因此原生CLI局部行为可以继续评估。报告本身无旧快照对应，JH018测试文件则不等价。
- JH018相对本地03只增加Gemini白名单及扫描断言，**没有070004f已接受的Gemini实际继承断言**。旧session_probe服务例外来自03既有历史，不误报成本批新加；但03及其冻结GUI历史不能被视为本次已验收基线。
- 独立小探针将两个来源中的现有继承测试方法原样编译，在内存中仅把Gemini适配器类换成普通object：固定候选方法对照通过、削弱后断言失败；08方法对照通过、削弱后仍通过，均无异常错误。这确认漏检，不能用“46测通过”证明消费者继承受检。

本轮不修改fdec9c6、不回滚或重写08、不把它合入产品；下一批从真正29e0020另建验证分支，保留08原始报告、未跟踪夹具和运行证据。

## 原始证据复核

审查入口：`data/execution08-independent-review-20261023/audit_execution08.py`；结果：同目录 `review-summary.json`。该脚本只读已有源与证据，并执行上述内存测试方法小探针，不运行真实CLI或完整套件。公开摘要副本在 `reports/gemini-windows-probe-review-20261023/`。

核对的三套完整运行：

| 证据目录（相对 data/gemini-windows-probe-20261023） | run | 合成请求 | 结论 |
|---|---|---:|---|
| evidence/first_host_probe | gemini-run-b3bc1e958599 | 5 | 搜索/读取成功，写/Shell拒绝；stdout最后为success，但进程非零，run failed |
| evidence/second_host_probe | gemini-run-c01050081e82 | 5 | 相同故障类复现，仍failed |
| boundary_tests/evidence_fd13f80c | gemini-run-b05235d3a72c | 6 | read_file外部绝对路径、经junction、../三种读取均invalid_tool_params；写/Shell均tool_not_registered；进程仍崩溃 |

以上各run的stdout、stderr和host-chat-record三文件均与collected记录的字节数/摘要相符；每套workspace-before与after的实际字节相同。两次普通探测各757个清单条目，边界夹具3条目；这证明相应采样时的完整清单相同，不冒充所有时刻或TOCTOU封闭。该junction现查确为Windows junction，并解析至自建outside目录，不是把不存在的路径当成越界证据。

已核对的完整合成运行请求为5+5+6=16，报告“5次”只能绑定单次普通host probe，不能当整批总数。还存在早期边界尝试的fetch failed和auto模型路由日志，未在本次摘要中算成通过；要求下一份报告列完整尝试索引及结果。自带90/108 token计数来自合成端点统计，不是实际模型账单。当前材料未证明发生了真实模型成功调用，也不能仅凭usage=0证明没有尝试发网络请求。

## 崩溃事实与归因限制

三套完整运行的stderr均有 `Assertion failed: !(handle->flags & UV_HANDLE_CLOSING), file src\win\async.c, line 94`，collected记录exit_code均为3221226505。独立换算为 **0xC0000409**，不是报告的0xC0000029 / STATUS_INVALID_SYSTEM_SERVICE。后者十进制为3221225513。

微软说明用户态fast fail可呈现0xC0000409：[__fastfail](https://learn.microsoft.com/en-us/cpp/intrinsics/fastfail?view=msvc-170)。本次没有崩溃dump、调用栈和异常子码，因此只记录“断言伴随0xC0000409退出”，不由该码推断具体内存破坏或根因。

现有D:\node_global\node.exe小型元数据探针实报Node24.19.0、libuv1.52.1；exe SHA-256 `3602f2bb1a10f2cbab4c36886218a33c1ab3db87290e73b033c46c77147d0237`。CLI包四个关键文件实际字节与08报告及Hark离线材料一致，package声明0.65.0、Node>=20。

能确认的是该Windows/Node24.19.0/CLI0.65.0组合在已有运行中崩溃；stdout已有final result不代表进程成功。不能据此排除其他环境因素，也不能在Node22对照前写成“Node24平台兼容根因已定位”。适配器保持declared_status=宿主success、run=failed是正确裁决，不允许把退出码强改0或只看最后一条JSON放行。

## 分级关闭与保留

局部接受：四个已查CLI文件字节、Windows实际read/grep、广告工具集合为四种读取工具、已查read_file三种越界拒绝、write/Shell拒绝、三套原生证据关联及相应清单一致。

未关闭：固定29e0020的完整Windows验证、进程正常退出、受支持Node22实际兼容性、中文目录、workspace根相对/驱动器相对输入的Windows直接证据，以及报告完整尝试/计数。read_file参数里的../不是adapter workspace相对输入的同一层。glob/list_directory未逐一执行，三种read_file拒绝不泛化为所有工具/路径组合。

不扩大到所有重解析点、网络文件系统、TOCTOU、断电恢复或通用沙箱。Hark11–13既有离线裁决不撤回；Hark09三个尾项冻结；GUI、production complete、自动应用和真实协作闭环状态不变。

## 下一步

执行AI09只交付固定候选的Node22隔离兼容验证：先纠正基线，保留原JH018扫描/实际继承检查，保持CLI包和成功判据，以同样输入作Node24/22对照，再按实际影响补Windows剩余证据。真实模型0，无凭据、登录、全局安装、用户代理、产品合并或发布。

完整唯一提示词：`reports/gemini-windows-probe-review-20261023/execution-task-09.txt`，本轮生成，未确认发送。下一未用编号执行AI10 / Hark14，Hark无需新派单。

本轮只新增独立审查产物并更新交接；未启动Gemini、未安装Node、未跑完整套件、未提交或推送。产品n1 HEAD仍03d9a02；08提交和原树WIP保留。
