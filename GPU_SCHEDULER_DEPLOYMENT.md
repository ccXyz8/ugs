# UGS 源码快照与部署范围

> 2026-11-12最新规则：每30秒监测，取消连续空闲等待和累计采样点；整组资源满足即可进入派发，进程/健康/基线/分配/明确预约及启动前双复查保持。旧verifier未迁移阶段明确阻塞。见[即时准入部署](GPU_CURRENT_ADMISSION_DEPLOYMENT.md)。

> 最新入口（2026-11-09）：`candidates/dispatch/4e57ccf76fc1/ugs_dispatch_entry.py`。慢校验先于最终GPU复查，新增可见且去重的启动前阻塞证据；保留冻结文件及此前所有已验收扩展。见[派发时序部署](GPU_DISPATCH_TIMING_DEPLOYMENT.md)。

> 2026-11-03时窗更新：用户将连续空闲要求降为120秒/至少5点。本文历史五分钟规则及旧verifier说明由[两分钟窗口部署](GPU_IDLE_WINDOW_DEPLOYMENT.md)对应条款替代；运行阶段不热改，未迁移旧PIN保留300秒保护。

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

2026-11-01 15:39（台北），按用户明确授权新增可选verifier入口candidates/preload_v3_2/verify_launch_v3_2.py及preload.py并登记发布PIN/隔离CPU证据。candidate_revision=ugs-preload-v3.2-candidate1，基础runtime_release仍ugs-exec-v3.1；旧core/verifier/protocol不变。14项模拟CPU及8项真实Linux隔离CPU协议测试通过，GPU接口验收待新项目有界实验，不能冒称GPU通过。服务未重启、容量未改。登记证据acceptance/preload-v32-production-registration.json，详细说明及SHA见仓库candidates/preload_v3_2/REVIEW.md、source-manifest.json。

## 2026-11-02新提交预检启用

公共CLI及旧兼容入口现加载`source/ugs_preflight_v1.py`，只约束两个项目的新提交和未启动阶段修改，不追溯迁移已注册清单。新增七项CPU证据、完整stage SHA、已验收v3.2 verifier的双文件PIN，以及项目内有界失败记录。具体格式见适配合同。

验收：25项隔离CPU回归、5项隔离CLI检查、5项线上CLI检查；线上确认修改最终argv后validate拒绝，list表格、JSON、show和中文帮助可用。激活在状态锁内更新公共CLI及发布SHA，确认队列、活动分配、策略和协调器身份不变；没有重启协调器或实验。

服务器验收索引：`acceptance/preflight-v1-activation/result.json`及`live-cli-result.json`。旧执行器、GPU空闲规则、最终快照时限及原始source-manifest.json保持不变。新项目模板的CPU验收不替代各自正式实验所需的GPU接口和门禁证据。

## 2026-11-02 23:38:57（台北）repair-v1生产启用

现有ugs已切换到candidates/repair_release/3061d40c3131/ugs_cli_repair_v1.py，CLI和daemon共同加载ugs_repair_v1.py。在无活动实验窗口暂停派发、停止旧协调器、登记发布SHA并启动新协调器，通过检查后恢复此前未暂停状态。保留36条实验记录及所有清单/队位，Time-R1仍34→35→36；没有重启实验、修改冻结核心/verifier/PIN或GPU规则。

23项Linux CPU测试、7项线上查询/帮助、dry-run保持日志revision/清单/队位不变通过。CPU真实后代终止验收使用模拟GPU释放，不冒称新GPU科学实验验收。发布证据acceptance/repair-v1-production.json，SHA256=5adfab62901c85789858166e0ac5df68d5a5219cfada31365cbd0ecf4ab8207b。原启动入口/发布清单保留在生产切换审计中，不写入Git的实时状态。显存4–7MiB候选未随本次启用。

## 2026-11-03 00:08:41（台北）显存范围与回退生产启用

入口切换为candidates/memory_entry/b40388bc2608/ugs_memory_entry.py，保留repair-v1操作接口，统一安装memory_runtime。5张证据有效的已审4MiB卡配置ceiling=7，未审卡不放行；无进程收口>7触发全局exact4锁存及空闲窗口失效。六个原QUEUED job已通过项目CPU验收与公开CAS update迁移新验证器，未重启实验/重建job或改变队位。中央重跑job_validate及same_semantics通过，恢复prior pause=false。

证据acceptance/memory-allowance-v1-production.json SHA256=7253875e0c74ef36d1acda3ed8914f47d3dffb22cf94e63c72b4fa707549f088；完整规则与三文件PIN见GPU_MEMORY_ALLOWANCE_DEPLOYMENT.md。另按已有64MiB阈值执行日志压缩，未改维护策略；8项CPU显存/时窗测试及2项未授权拒绝检查通过，未制造生产GPU故障测试回退。

## 2026-11-03 21:49:05（台北）120秒窗口生产启用

公共入口更新为candidates/idle_window/ad910adbc734/ugs_idle_entry.py。连续120秒/至少5点，扫描30秒、漏采上限35秒、启动两次2秒复查和显存回退保持。受控交接保留job43运行进程，未重启实验；job44/job53全链及job43未开始评估后缀已完成项目真实CPU预检与公开update。已恢复派发、max_experiment仍1。13项CPU边界测试、4项CLI检查及2项未授权拒绝通过。新PIN、旧阶段兼容及证据见[两分钟窗口部署](GPU_IDLE_WINDOW_DEPLOYMENT.md)。

## 2026-11-05 15:53:09（台北）受控资源更新

公共入口更新为candidates/resource_override/a31d2dcc3d5b/ugs_resource_entry.py。新增明确授权且绑定job/run/新旧SHA的无运行阶段后缀资源变更，以及gpu_uuids选卡与最终复查约束。普通update冻结保护保留。13项CPU测试、3项CLI检查、真实后缀准入及公开dry-run通过；无活动实验时更换协调器，所有job和全局policy/轮转保持，未暂停或重启实验。详见[资源变更部署](GPU_RESOURCE_OVERRIDE_DEPLOYMENT.md)。
