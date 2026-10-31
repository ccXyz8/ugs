# UGS 仓库协作规则

本仓库为用户指定的本地UGS工作区：`~/workspace/ugs`。远端为`https://github.com/ccXyz8/ugs.git`。用户后续明确指令优先。

- 涉及服务器GPU实验或执行接口前，先阅读[全局协议](GPU_SCHEDULER_PROTOCOL.md)、[部署范围](GPU_SCHEDULER_DEPLOYMENT.md)及[使用说明](GPU_SCHEDULER_USAGE.md)。
- 所有已接入项目共用唯一调度服务；默认max_experiment=1，可热调；项目间等权轮转、项目内FIFO。每轮一个有限训练/验证/门禁测试链，不捆绑多个独立候选或完整八阶段。
- 默认A100双卡BF16，训练DDP，独立验证/测试双rank推理；覆盖档必须明确且已验收。资源不足CPU等待，不擅自降级。
- 每30秒扫描；整组GPU共同连续五分钟完整空闲，使用/未知/异常/漏采失效，启动前两次复查间隔2秒。只排除核实过的驱动基础占用，不以低显存阈值放行用户进程。
- 尊重已知使用权与预约；不先占部分GPU，不驱逐外部进程。轮询不能保证他人未来不用卡。
- 仅使用既有SSH：`ssh -o BatchMode=yes a100`，在admin核实身份后进入gpu07；V100按已有授权使用`ssh -o BatchMode=yes v100`。不得读取或展示认证材料。
- gpu07使用kzhang19账户下8张A100，SSH直接调度，不使用Slurm，不新增端口，不使用ssh:post。
- 项目通过ugs自助register/validate/submit/list/show；单实验按job-id操作，--project可选且显式提供时校验归属。experiment pause/resume已删除；scheduler/project pause/resume管后续派发，remove撤回未启动实验，stop终止已启动链；失败/kill/取消不自动重启。
- max_experiment只控制容量，0不杀进程。紧急停止并阻塞用`ugs scheduler pause --stop-running`；恢复用resume。全局操作仅用户或授权管理会话执行。
- 产物、checkpoint、预测、评分与实验日志留在各项目目录；UGS只集中调度元数据与服务日志。项目路径不可用时不得回退服务目录。
- 本仓库提交不等于生产部署授权；保留已有工作，核实来源及SHA。修改生产代码必须经过相应验收，不能暗改运行阶段或冻结适配器PIN。
- Git不提交认证材料、实时队列、运行身份、设备快照、项目数据、checkpoint或日志。UGS自动维护仅限已授权白名单，不扩大到项目资产。
- source-manifest.json记录初始服务器原件SHA；后续本地修改应新增版本/变更说明，不改写该记录伪称仍与原件相同。

- 用户2026-10-31最新实验控制、job-id调用和显存收口要求见[控制补充协议](GPU_SCHEDULER_CONTROL_POLICY.md)，替代冻结旧协议对应交互条款；正常结束、失败与紧急停止均须核实全部受管进程及GPU上下文收口，空卡显存回已审基线。不提供保留显存的实验挂起模式。
