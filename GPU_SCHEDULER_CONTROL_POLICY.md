# UGS 实验控制与资源收口补充协议

> 2026-11-03时窗更新：用户将连续空闲要求降为120秒/至少5点。本文历史五分钟规则及旧verifier说明由[两分钟窗口部署](GPU_IDLE_WINDOW_DEPLOYMENT.md)对应条款替代；运行阶段不热改，未迁移旧PIN保留300秒保护。

生效日期：2026-10-31。依据用户最新指令，本页替代旧协议中“单实验必须提供project_id”和“experiment pause/resume”的交互规则，补强正常结束与紧急停止的资源收口要求。公平、FIFO、准入、项目隔离、输出归属与删除边界不变。

## 提交、启动与停止的使用约定

“提交入队”和“启动执行”是两件事：project register登记项目；experiment submit通过校验后创建job-id并加入QUEUED队列；调度器随后按公平、容量、GPU和门禁条件为某个阶段授予启动许可，阶段才会进入STARTING/RUNNING。“后续派发”专指授予新的阶段启动许可，不指接收新提交。

暂停期间仍允许登记项目、校验清单、提交新实验及查询/管理队列。新提交不会因暂停而被拒绝，但对应范围内尚未获得启动许可的实验不能开始运行。暂停也阻止已有实验链的下一个阶段启动，例如训练完成后的验证、验证完成后的测试。当前阶段完成后照常回收进程和显存，不挂起GPU进程等待恢复。

| 操作 | 新实验提交入队 | 尚未获许可的阶段启动 | 已运行阶段 | 协调器 |
|---|---|---|---|---|
| ugs project pause P | 允许 | 指定项目禁止，其他项目不受此开关影响 | 继续 | 运行 |
| ugs scheduler pause | 允许 | 全部禁止 | 继续 | 运行 |
| ugs scheduler pause --stop-running | 允许 | 全部禁止 | 全部受管实验终止并回收 | 运行 |
| ugs scheduler stop --stop-running | 允许通过CLI登记入队，但不执行 | 全部禁止 | 全部受管实验终止并回收 | 全部收口后退出 |
| ugs experiment stop J | 允许 | 不改变项目/全局开关，其他队列可能补位 | 指定已启动链终止并回收 | 运行 |

普通pause保留暂停事务之前已授予的启动意图，因此少量已经处于STARTING的阶段仍可能转为RUNNING。需要“从现在起不再启动，连正在启动的也取消”时，使用scheduler pause --stop-running；它在同一事务中关闭后续启动并撤销所有已启动链的许可。之后只要无人执行scheduler resume，就不会有新的实验获准运行。命令返回不等于显存已回收，须检查STOPPING及active_count，确认收口完成。

实验级experiment pause/resume已删除。project pause/resume保留，控制单个项目的新阶段启动；scheduler pause/resume保留，控制所有项目的新阶段启动。二者不是对实验进程发送持久暂停信号。

恢复时：project resume P只解除该项目开关；scheduler resume只解除全局开关，仍须项目未暂停且满足其他准入条件。scheduler start只启动服务，不解除暂停。已失败、取消或结束的实验不会因resume/start自动重启。

max_experiment只控制实验链容量：默认1，0禁止接纳新链但不终止已启动链，也不阻止它们完成有限后续阶段；调高容量不会解除暂停。未启动实验用experiment remove J撤回，已启动实验用experiment stop J终止。单实验--project可选，填写时必须匹配job-id归属；项目agent仍只管理本项目。

## 单实验使用job-id

    ugs experiment show job-000004
    ugs experiment logs job-000004
    ugs experiment stop job-000004
    ugs experiment remove job-000004
    ugs experiment show job-000004 --project ms-swift-main

上述指令是语法示例，不是要求停止或撤回该实际实验。单实验的--project是可选归属断言：省略按全局唯一job_id查登记归属，提供时必须一致。show/logs/events/update/amend/retry/gate同样适用。

项目仍保存在注册清单和实验记录中，用于轮转、FIFO、路径约束与产物归属。项目agent仍只操作本项目，可以自愿显式带--project。无需重复提供project_id不代表扩大授权。

批量范围需明确：stop --all不带project表示全局；带--project只针对该项目。remove --all仍必须--project，避免无目标的跨项目撤回；list的--project继续作为可选筛选。

## 正常结束、失败与紧急停止的统一资源收口

- 先核实guardian、适配器和全部受管后代退出，并校验guardian的完整回收证据。
- GPU上下文检查覆盖适配器、回收证据中的所有worker及停止快照中的进程；包括可见GPU上的计算/图形上下文，不只看主进程PID。
- 当分配卡上没有其他进程时，显存须回到当前boot/驱动已审核的精确基础占用；不是要求驱动占用归零，也不是使用低显存阈值放行。
- 若外部进程已接着使用显卡，只核实本实验上下文消失，不等待外部任务结束、不清零他人显存、不向外部进程发信号。
- 回收时的GPU查询同样写入公共观察历史；发现占用、异常或查询失败会使候选空闲资格失效。
- 只有完成资源核实才记SUCCEEDED、FAILED、CANCELLED或进入无GPU的门禁/接续等待，并释放相应UUID。正常链短暂保留实验名额不等于保留GPU进程或显存。
- STOPPING是终止/回收过程，不是暂停运行。驱动异常、残留上下文、未知显存或不可中断进程导致暂不能完成时，如实保留名额与恢复阻塞，不虚报已释放，不reset GPU。
- 适配器退出但guardian/后代超过5秒仍未收口，终止可核实的剩余后代并记FAILED；即使已有成功回执也不能掩盖遗留进程，不自动重启。
- 停止路径可短暂SIGSTOP防止fork竞态，随后必须SIGKILL；写入快照异常也执行finally清理，不把SIGSTOP作为持久暂停模式。

## 执行版本与适配合同

当前控制扩展source/ugs_control_v2.py由公共CLI及协调器入口加载。既有ugs_exec_v3_1.py、verify_launch_v3_1.py和protocol-v2.md字节与SHA保持不变，runtime_release仍为ugs-exec-v3.1，现有阶段PIN不需改写。controller_revision=ugs-control-v2单独标识新控制行为，并在生产发布证据中绑定源码SHA。

GPU_SCHEDULER_PROTOCOL.md及protocol-v2.md保留冻结合同原件身份；交互冲突由本补充协议和最新使用说明明确替代，不通过改写冻结原件掩盖变化。当前适配器继续使用原许可，当前科学任务不重启。

## 2026-11-02 用户授权的4–7 MiB基础占用范围

对已有有效4 MiB审定基线的A100，用户授权将基础显存允许范围调整为4至7 MiB（整数），不是给用户进程设置7 MiB免检阈值。进程可见性、用户进程拒绝、健康/ECC、零利用率、driver/reserved/total一致性、共同五分钟及启动双复查仍保留。准入、最终preload与正常/失败/取消收口必须采用同一判断。原始基线证据仍保存4 MiB，不改写历史审定记录。

实现候选见candidates/memory_v1；以baseline条目的memory_ceiling_mib=7显式启用，缺字段保持原精确匹配。生产部署状态与代码候选分开：截至本次修改，尚未切换生产，运行中job25及旧验证器/PIN保持不动。完整发布兼容性验收与安全切换完成前，生产仍按旧规则执行。

用户追加的自动回退：7 MiB范围生效后，若结束收口时已核实受管后代退出、分配卡没有GPU进程，却出现显存>7 MiB，原子全局恢复精确4 MiB并锁存，记录触发证据，当前收口保持等待，旧空闲观察失效。不会自动重新启用7 MiB。此实现仍为候选，生产尚未启用范围及回退。

## 2026-11-03显存范围正式生效

此前4–7MiB及超7锁存回退候选已于00:08:41（台北）生产上线。以GPU_MEMORY_ALLOWANCE_DEPLOYMENT.md为准，覆盖旧候选状态说明；未审卡、用户进程、健康和五分钟窗口要求不变。正常训练显存超过7MiB不会触发回退，只针对完整收口后的本次分配无进程卡。
