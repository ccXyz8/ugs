# UGS 源码快照与部署范围

本仓库初始提交于2026-10-31导入已部署的ugs-exec-v3.1及中文帮助。SHA原件清单见[source-manifest.json](source-manifest.json)；此文件只记录源码与协议，不包含运行状态或生产发布许可。

## 当前执行路径

部署通过已有SSH进入计算节点gpu07，由kzhang19账户管理8张A100；不使用Slurm、不新增端口。服务器原部署服务根是源码中的ROOT，固定Python为源码中的PYTHON。这些值在本次归档中保持原样，避免把未经验收的可移植化修改混入源码快照。

当前核心：source/ugs_exec_v3_1.py；许可入口：source/verify_launch_v3_1.py；控制扩展：source/ugs_control_v2.py；显示：source/ugs_cli_control_v2.py；中文帮助：source/ugs_help_zh_v3.py；维护：source/ugs_maintenance_v1_1.py。旧v2.3/v3文件用于兼容验证，不作为新派发入口。

protocol-v2.md是执行器读取的协议文件，与GPU_SCHEDULER_PROTOCOL.md字节一致。用户侧详细调用方式见[使用说明](GPU_SCHEDULER_USAGE.md)，项目合同见[适配合同](GPU_SCHEDULER_ADAPTER_CONTRACT.md)。

## 已有验收

- 当前执行器在服务器完成41项CPU验收，覆盖并发、取消、kill、旧guardian接管、空闲资格、失败观察、基线、时间和预约逻辑。
- 日志维护完成12项CPU验收，覆盖状态保留、旧读者兼容、并发写入、损坏拒绝、路径隔离和参数保护。
- 中文帮助核对38个公开操作的参数及7组不进入执行分支的帮助调用。
- 这些是此前服务器验收事实；本地导入未重新运行服务器测试，不能视为新的GPU实验或科学门禁通过。

原验收记录由服务器保留。本Git仓库只纳入测试源码和源文件SHA，不纳入生产队列、实际进程身份、GPU设备快照或其他项目的实验记录。

## 首次部署限制

代码包含Linux /proc、节点/账号、解释器、目录和发布SHA约束。仅克隆仓库不构成可启动部署；首次使用须完成自身节点与存储核查、GPU基础占用复核、项目迁移对账、发布证据及适配验收。不能从现有生产状态复制出第二个并行调度器。

已有服务器执行器、运行实验和项目适配器不因Git推送被改变。后续源码修改、测试及生产部署分别记录和执行。

## 最新控制扩展

当前公共入口为source/ugs_cli_control_v2.py，协调器入口为source/ugs_control_v2.py，中文帮助为source/ugs_help_zh_v3.py。冻结core/verifier/protocol及阶段runtime_release不变。实验级pause/resume移除，单job-id操作，--project可选；正常/失败/取消统一回收校验，详见[控制补充协议](GPU_SCHEDULER_CONTROL_POLICY.md)。

最终48项CPU验收通过，含真实进程回收、正常结束GPU上下文残留等待、空卡基础显存复核、未知查询保留名额、异常写入仍清理和成功回执不能隐藏遗留worker。服务器证据acceptance/cpu-v3-2cc63eff6acf412cb43702029634d6de/result.json；控制发布acceptance/executor-release-control-v2.json；切换/实测acceptance/service-cutover-control-v2.json及control-v2-live-cli.json。回收查询及失败也写入共享GPU观察历史，避免漏记占用。另有仅job-id的真实CPU终止验收及10项生产只读CLI检查通过。现有科学实验没有重启。

中文帮助已同步提交入队与阶段启动的区别、项目/全局暂停范围及STARTING许可边界。36个公开操作的表格/JSON共72项渲染检查、8组实际CLI帮助检查通过。仅更新帮助与发布SHA，不重启协调器或实验；当前帮助清单见releases/help-clarification-source-manifest.json（Git仓库），服务器发布证据acceptance/executor-release-help-clarified.json。

submission_version类型校验修复：控制入口安装ugs_manifest_validation_v1.py，validate与幂等键查询统一拒绝非空字符串以外的值。4项CPU回归（含18组非法类型/操作组合）和2项生产CLI拒绝检查通过，未新增job，既有清单SHA、协调器与并发设置保持。冻结core/verifier/protocol不变，项目无需重新提交或修改PIN。发布证据acceptance/executor-release-manifest-validation-v1.json；新源码SHA见Git仓库releases/manifest-validation-v1-source-manifest.json。
