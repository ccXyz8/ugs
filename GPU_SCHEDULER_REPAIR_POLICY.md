# 更新与替换方案（ugs-repair-v1）

**最新生效状态：2026-11-02 23:38:57（台北）已生产启用。** 下文候选/待切换段落保留历史；以本段和GPU_SCHEDULER_PROJECT_STANDARD.md为准。生产完成23项CPU及线上查询、预览验证，原暂停状态已恢复，未重启实验或改变GPU规则。

## 简化后的命令

| 命令 | 行为 |
|---|---|
| `ugs experiment update JOB --file FILE` | 自动选择从未执行部分；保留job-id/队位，禁止改运行及完成阶段；不暂停全局 |
| `ugs experiment replace JOB --file FILE` | 管理操作：预检后原子暂停全局派发、登记替换并请求停止目标；收口后创建新job继承项目内队位 |
| `ugs experiment replacement JOB` | 查询WAITING_CLEANUP、COMPLETED、BLOCKED、ABORTED及新job-id |
| `ugs experiment replacement JOB --cancel` | 撤销未完成替换，不撤销已发出的停止，不复活旧job，不恢复派发 |
| `ugs scheduler resume` | 明确恢复派发；未完成替换时拒绝 |

单job的--project可选，提供时必须匹配。update/replace可选--expected-sha提供客户端比较条件；省略时在持锁事务中检查当前状态并确定允许范围，不代表替客户端判断旧清单意图。自动update沿用same_semantics限制，科学身份、资源语义、输出路径等不允许暗改；amend保留兼容。replace允许新尝试使用修复后的清单，但必须重新通过完整准入、同项目、独立产物路径，已成功job不支持替换。

replace具有全局暂停和终止目标的明确职责，不另加--stop-running。仅用户或授权管理会话调用，项目agent不自行全局暂停；同UNIX账号仍是规则边界，不构成安全沙箱。未指定job不得批量替换。

## 持久化和失败语义

- 清单检查失败不暂停、不停止。通过后在单一状态事务中暂停并登记停止意图，随后复用原stop_owned及guardian收口；不在锁内等待进程退出。
- 其他RUNNING阶段及已获批STARTING许可继续；所有新的阶段启动禁止，仍允许提交入队。其他链的下一阶段同样等待resume。
- 目标active_runs未释放或存在recovery时不创建替代job。GPU判定、完整五分钟窗口、停止身份检查及旧verifier不变。
- 新job保留新job-id/run_id/queue_seq；queue_position单独继承项目内顺序。项目轮转ring/served不变，不抢占已启动后项。列表仍按注册job-id分页。
- 完全相同的replace请求幂等；不同清单不能覆盖进行中请求。收口后重新校验PIN及准入，失败记BLOCKED、保留暂停；取消请求后可重新replace。
- 已完成替换只能按新job-id使用remove/stop/update/replace，不能通过取消旧请求删除新job。
- SSH断开后daemon负责推进。取消不撤回旧job停止；旧job和所有产物均保留，失败/取消不自动重跑。
- 替换完成或撤销后仍保持暂停。取消后可显式resume；尚在STOPPING的目标继续占原名额，不能假称已释放。

## 实现和上线边界

新增ugs_repair_v1.py和ugs_cli_repair_v1.py入口，不改冻结核心和control-v2文件。必须由新入口同时启动CLI和daemon，登记发布SHA及验收证据后启用；仅替换CLI不成立。CLI写操作检查daemon的repair_revision，旧运行时拒绝接收新修复请求。

当前为候选实现，生产未切换。实际观察2026-11-02 23:13（台北）job24仍RUNNING；没有为本次开发暂停、终止或重启它。不得把候选测试通过表述为生产可调用。后续须完成隔离CLI/daemon集成与进程收口验收，并在无活动实验的安全切换窗口绑定发布清单与入口；不能修改旧job的冻结PIN。

## 队列灵活性与预览补充（候选）

新增queue list/move，实际派发按项目内queue_position排序；默认未调整时仍FIFO，跨项目轮转不变。人工move仅允许同项目非活动非终态job，必须reason，可选expected-revision；替换进行中禁止移动。注册queue_seq及job-id不改，历史列表仍按注册排序。queue list报告控制阻塞，不冒充完整资源审核。

update/replace/queue move支持--dry-run，在独立状态副本上预演，不写日志事务、不停止进程、不预留。正式操作必须重新验收。中文帮助与表格视图见候选入口；生产上线条件不变。

## 跨进程验收补充

2026-11-02在gpu07独立CPU夹具中新增5项通过：并发相同replace只生成一个新job；两个客户端基于同revision移动仅一个成功；客户端退出后重新读取请求/推进/恢复；旧运行时写操作拒绝且日志不变；现有control-v2停止函数真实回收夹具CPU后代并由现有finalizer等待释放检查后才替换。释放检查使用注入的GPU结果，不是生产GPU验收；未接管或重启生产daemon。证据见releases/repair-integration-cpu.json。
