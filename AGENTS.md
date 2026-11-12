# UGS 仓库协作规则

- 2026-11-12用户最新准入规则已上线：每30秒监测，idle_seconds=0；不累计连续空闲时长或采样点，最新完整有效观察满足整组资源表即可进入派发。35秒陈旧上限、5秒采集上限、启动两次相隔2秒复查、2秒最终快照及进程/健康/显存基线/明确预约保持。旧verifier未迁移阶段显示VERIFIER_MIGRATION_REQUIRED，真实CPU验收后公开update，活动阶段不热改。详见[即时准入部署](GPU_CURRENT_ADMISSION_DEPLOYMENT.md)，替代下方及旧协议对应连续空闲条款。

- 2026-11-09派发时序修复已上线：慢校验先于最终GPU复查，2秒快照有效期和全部GPU准入规则保持；重复启动前阻塞去重并显示原因。公共入口及验收见[派发时序部署](GPU_DISPATCH_TIMING_DEPLOYMENT.md)。项目无需重提或修改冻结PIN。

本仓库为用户指定的本地UGS工作区：`~/workspace/ugs`。远端为`https://github.com/ccXyz8/ugs.git`。用户后续明确指令优先。

- 涉及服务器GPU实验或执行接口前，先阅读[全局协议](GPU_SCHEDULER_PROTOCOL.md)、[部署范围](GPU_SCHEDULER_DEPLOYMENT.md)及[使用说明](GPU_SCHEDULER_USAGE.md)。
- 所有已接入项目共用唯一调度服务；默认max_experiment=1，可热调；项目间等权轮转、项目内FIFO。每轮一个有限训练/验证/门禁测试链，不捆绑多个独立候选或完整八阶段。
- 默认A100双卡BF16，训练DDP，独立验证/测试双rank推理；覆盖档必须明确且已验收。资源不足CPU等待，不擅自降级。
- 每30秒扫描；整组GPU共同连续两分钟完整空闲，使用/未知/异常/漏采失效，启动前两次复查间隔2秒。只排除核实过的驱动基础占用，不以低显存阈值放行用户进程。
- 尊重已知使用权与预约；不先占部分GPU，不驱逐外部进程。轮询不能保证他人未来不用卡。
- A100所有任务仅在gpu07执行，包括UGS查询/管理、CPU预检、只读分析和监控；admin仅用于既有SSH中转，不附加身份/资源/目录检查命令。进入gpu07后核实节点和身份。V100按已有授权使用`ssh -o BatchMode=yes v100`。不得读取或展示认证材料；发现已有admin任务只报告，不自行停止/迁移/重启。
- gpu07使用kzhang19账户下8张A100，SSH直接调度，不使用Slurm，不新增端口，不使用ssh:post。
- 项目可自助register/validate/submit/list/show；单实验按job-id定位，--project可选且显式提供时必须匹配归属。experiment pause/resume已删除；remove撤回从未启动实验，stop终止已启动链，失败/kill/取消不自动重启。
- project pause/resume控制本项目、scheduler pause/resume控制全部项目的“新阶段启动许可”；不禁止登记项目或提交新实验加入QUEUED。暂停时当前阶段继续，结束后释放显存，下一阶段等待恢复；普通pause之前已授予的STARTING许可仍可继续。紧急阻止新启动并终止已启动/正在启动的受管链，使用scheduler pause --stop-running。恢复仍须全局及项目开关均放行。
- max_experiment只控制容量，0不杀进程。紧急停止并阻塞用`ugs scheduler pause --stop-running`；恢复用resume。全局操作仅用户或授权管理会话执行。
- 产物、checkpoint、预测、评分与实验日志留在各项目目录；UGS只集中调度元数据与服务日志。项目路径不可用时不得回退服务目录。
- 本仓库提交不等于生产部署授权；保留已有工作，核实来源及SHA。修改生产代码必须经过相应验收，不能暗改运行阶段或冻结适配器PIN。
- Git不提交认证材料、实时队列、运行身份、设备快照、项目数据、checkpoint或日志。UGS自动维护仅限已授权白名单，不扩大到项目资产。
- source-manifest.json记录初始服务器原件SHA；后续本地修改应新增版本/变更说明，不改写该记录伪称仍与原件相同。

- 用户2026-10-31最新实验控制、job-id调用和显存收口要求见[控制补充协议](GPU_SCHEDULER_CONTROL_POLICY.md)，替代冻结旧协议对应交互条款；正常结束、失败与紧急停止均须核实全部受管进程及GPU上下文收口，空卡显存回已审基线。不提供保留显存的实验挂起模式。

- 两个接入项目的新提交执行[预检与失败证据合同](GPU_SCHEDULER_ADAPTER_CONTRACT.md)：七项真实CPU预检、统一已验收v3.2 verifier、initial10秒/preload20秒调用超时、项目目录内结构化失败记录。既有注册源码与PIN不覆盖；未开始阶段按用户授权经真实CPU验收和公开update迁移。2秒最终GPU快照保持，空闲窗口按最新120秒部署。

- 项目统一操作执行[项目标准](GPU_SCHEDULER_PROJECT_STANDARD.md)。repair/queue新接口已于2026-11-02 23:38:57（台北）通过现有ugs生产入口启用；全局replace仅用户或授权管理会话执行，queue move必须有用户明确重排要求。不得自行切换内部入口。诊断v2仅允许trace-tool=none；持续ltrace/strace不得用于未来正常长时GPU实验，既有注册源码/PIN不热改。

- 用户授权上线的4–7MiB许可及超7回退、新memory verifier三文件PIN见[显存策略部署](GPU_MEMORY_ALLOWANCE_DEPLOYMENT.md)，取代旧精确4默认和旧verifier路径说明；只对有效已审核4MiB基线放宽，进程/健康/两分钟/两秒复查不变。回退锁存后不得自动重新放宽。

- 2026-11-03用户将GPU连续空闲窗口调整为120秒、至少5点；旧PIN未迁移阶段保留300秒防止验证器不一致，新接口和当前部署见[两分钟窗口部署](GPU_IDLE_WINDOW_DEPLOYMENT.md)。30秒扫描、35秒漏采失效及启动两次相隔2秒复查保持。

- 2026-11-05已上线受控后缀资源变更：普通update仍冻结资源；明确用户授权登记后才可使用 `experiment update --resource-authorization`，仅无运行阶段的未开始后缀，保留身份/前缀/科学/输出/队位。gpu_uuids限制实际分配，目标卡不合格则等待。见[资源变更部署](GPU_RESOURCE_OVERRIDE_DEPLOYMENT.md)。
