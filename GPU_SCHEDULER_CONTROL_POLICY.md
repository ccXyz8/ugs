# UGS 实验控制与资源收口补充协议

生效日期：2026-10-31。依据用户最新指令，本页替代旧协议中“单实验必须提供project_id”和“experiment pause/resume”的交互规则，补强正常结束与紧急停止的资源收口要求。公平、FIFO、准入、项目隔离、输出归属与删除边界不变。

## 运行与暂停职责

运行阶段继续执行，直到正常结束、失败收口或收到紧急停止；不提供保留GPU显存的实验挂起模式。

| 操作 | 正在运行的阶段 | 后续派发 | UGS协调器 |
|---|---|---|---|
| scheduler pause | 继续执行 | 暂停 | 继续运行 |
| scheduler pause --stop-running | 终止并回收 | 暂停 | 继续运行 |
| scheduler stop --stop-running | 终止并回收 | 暂停 | 全部收口后退出 |
| experiment stop J | 终止指定已开始链并回收 | 全局开关不变，其他队列可补位 | 继续运行 |
| project stop P | 终止该项目已开始链并回收 | 暂停该项目 | 继续运行 |

scheduler/project pause、resume保留；experiment pause和resume均删除并拒绝调用。未开始实验用experiment remove J撤回，已开始实验用experiment stop J。历史记录可能保留paused字段，不表示仍提供实验级挂起接口。

scheduler stop无--stop-running时仅在无活动名额时允许。max_experiment仍只控制容量，0不发送停止信号；降低正数让已启动有限链完成。所有终态均不自动重启。

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
