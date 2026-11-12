# UGS v3.1 项目适配与自助准入合同

> 2026-11-12最新规则：每30秒监测，取消连续空闲等待和累计采样点；整组资源满足即可进入派发，进程/健康/基线/分配/明确预约及启动前双复查保持。旧verifier未迁移阶段明确阻塞。见[即时准入部署](GPU_CURRENT_ADMISSION_DEPLOYMENT.md)。

> 2026-11-03时窗更新：用户将连续空闲要求降为120秒/至少5点。本文历史五分钟规则及旧verifier说明由[两分钟窗口部署](GPU_IDLE_WINDOW_DEPLOYMENT.md)对应条款替代；运行阶段不热改，未迁移旧PIN保留300秒保护。

协议：global-gpu-scheduler-v2.0；项目合同ugs-stage-v2；当前服务目录/public/home/kzhang19/kezhang/cc/global-gpu-scheduler。部署SHA与状态见GPU_SCHEDULER_DEPLOYMENT.md；完整CLI见GPU_SCHEDULER_USAGE.md。

项目register JSON保留project_id、project_root、allowed_roots、固定python、owner，adapter_contract为ugs-stage-v2；migration_evidence={path,sha256}指向本项目证据，包含old_dispatch_disabled=true、existing_jobs_reconciled=true及实际依据。注册不停止或重启已有实验。

实验manifest顶层必须runtime_release="ugs-exec-v3.1"，并包含project_id、experiment_id、submission_version、protocol、protocol_sha256、有限stages、训练所需evaluation_plan。每stage包含stage_id、kind、argv数组（固定python开头）、cwd、output_dir、evaluation_dir、log_path、result_path、source_sha256完整映射、resource_profiles、adapter_accepted布尔和requires_previous_gate。输出/日志/回执须位于本项目合法目录，回执不能已存在。资源档字段为profile_id、gpu_count、gpu_model=A100、precision、execution、world_size、host_start_mib、host_running_mib、disk_min_gib、acceptance_ref；按项目验收后的真实冻结语义填写，默认双卡BF16训练DDP/验证测试双rank推理。

manifest增加admission={contract:"ugs-stage-v2",automatic_retry:false,stages:{STAGE:{scope, cpu:{path,sha256},interface_evidence:[{path,sha256}]}}}。
scope可为accepted_stage（正式阶段，至少一份真实接口证据）、bounded_interface（首次有界接口）、validation_as_acceptance（首次有限验证作为验收）。每阶段CPU报告为：
contract="ugs-cpu-admission-v1"，passed=true，automatic_retry=false，
checks=[{name,passed:true},...]，
source_sha256完整覆盖该stage实际代码/配置SHA，
resource_profiles与该stage逐项一致。
报告本身SHA固定。服务自动验合同/路径/SHA/绑定；项目负责实际CPU检查、数据/科学门禁和接口证据真实性，无需中央手工accept。

适配器必须先在已登记进程中调用固定Python -B 服务/source/verify_launch_v3_1.py，确认allowed=true、verifier="ugs-exec-v3.1"及所有运行分配字段与环境一致。CPU依赖/输入/容量准备完成后，创建CUDA worker之前，再调用同一脚本加--preload，要求allowed=true且preload=true。初次登记短竞态仅按返回的登记状态在原进程内有界等待，不重新创建实验。许可失效或其它错误停止，不旁路、不初始化CUDA探测。验证器源码及core/protocol SHA应固定在项目独立版本PIN，并在项目CPU检查中核实。

执行环境UGS_MANAGED、UGS_JOB_ID、UGS_RUN_ID、UGS_STAGE_ID、UGS_STAGE_NONCE、UGS_MANIFEST_SHA256、UGS_GENERATION、UGS_PROFILE_JSON、UGS_RESULT_PATH及CUDA_VISIBLE_DEVICES由服务注入。generation是不可解析为整数的字符串。所有rank使用同一有序UUID分配，记录物理编号/UUID/rank映射。不能自行选卡、新开端口或重新运行旧项目队列。

成功回执写入UGS_RESULT_PATH，包含run_id、stage_id、stage_nonce、outcome="success"、artifacts_verified=true、gate_passed（true/false/null）和artifacts=[{path,sha256},...]。只有实际产物核实且guardian确认所有后代退出后服务才完成该阶段；缺真实preload授权、回执或产物SHA不合格均不能记成功。科学指标未审定时gate_passed=null，不能伪造true。requires_previous_gate依赖真实科学门禁。

update只用于从未开始实验；amend只能改未开始后缀。CAS必须提供当前manifest_sha256；阶段顺序、预算、资源含义、输出路径、科学合同及已启动前缀不变。活动阶段保留原manifest与许可，future revision暂存到阶段成功收口后应用。历史key及SHA保留，重复提交不会另建同一实验。

max_experiment只管容量，0不杀实验；停止/暂停/服务启停见USAGE。失败、kill、取消不自动重启；显式retry须新清单和输出，保留旧证据。所有项目输出留在项目目录，中央只保存调度元数据、服务日志与索引。

未来后缀amend内部以from-stage为范围执行准入校验。公开validate当前从stage0检验完整链；活动旧前缀没有新合同报告时，不补造或修改旧前缀，使用amend自身的后缀原子校验。

initial登记短竞态允许同adapter PID/nonce内按code3最多10秒有界pending等待；成功许可只能取得一次。preload成功一次后立即启动GPU worker，不重新申请成功许可、不重新创建实验；任何非pending错误直接停止。所有复查写共同观察历史，已知占用/失败使资格失效；超过5秒的GPU查询或最终快照超过2秒不准入。

## submission_version类型约束

submission_version必须是非空字符串，例如JSON中的`"submission_version": "1"`；整数`1`、布尔值、null、数组、对象、空串或纯空白均拒绝。validate与submit/update/amend/retry的幂等键路径使用同一类型检查，返回明确字段错误，不自动转换类型。已有合法字符串、清单SHA、job-id和幂等键保持不变；失败提交不创建实验。

## 新提交预检与失败证据（2026-11-02补充）

本节替代上文两个接入项目新提交所用旧verifier的默认说明。既有注册作业及其PIN保持冻结，同内容幂等查询不要求迁移。新提交的ms-swift-main、time-r1每个阶段必须通过`ugs-preflight-v1`，validate与submit使用同一检查；不合格不创建job。

统一使用服务目录`candidates/preload_v3_2/verify_launch_v3_2.py`，SHA256为`26e5e077924f67837c01bf0cee38b34f8bd331661dea9c48b3dd53560b5682a5`；同目录`preload.py`为`6ee2d1354e9af32a430ce1d18b5ddef8b5a1344216608d0797e33f4a8c36212f`。candidate_revision为`ugs-preload-v3.2-candidate1`；基础运行版本仍为ugs-exec-v3.1。initial子调用超时10秒，preload子调用超时20秒；最终GPU快照2秒时效及共同连续五分钟空闲规则不变。调用必须同时检查退出码、allowed、preload及全部授权身份。拒绝或超时不得初始化CUDA、重复消费许可或自动重试实验。

每阶段CPU报告增加preflight对象：contract、stage_id、stage_sha256、verifier（path/sha256/dependency_path/dependency_sha256/candidate_revision/initial_timeout_seconds/preload_timeout_seconds）、failure_record和checks。checks包含以下七项，每项提供name、passed=true及evidence的path/sha256，证据必须来自实际CPU检查，不能只手填通过声明。CPU报告source_sha256额外绑定上述两个服务文件；服务文件不加入项目allowed_roots。

| 检查名 | 实际检查内容 |
|---|---|
| final_argv_parse | 使用最终入口和参数解析，区分torchrun参数与模型脚本参数 |
| generation_uuid_rank | generation按字符串传递，GPU UUID/物理号及rank映射正确 |
| authorization_response | initial/preload超时、拒绝、异常输出均停止，保留响应 |
| model_interface | 实际模型配置、输出结构和词表/形状约定一致 |
| storage_preflight | 各项目输出目录可写、容量满足要求；inode未知采用有限写探针，不误报耗尽，也不声称证明配额充足 |
| launch_boundary | 授权完成前不得创建CUDA worker；无自动重试 |
| failure_record | 故障注入验证结构化记录及原始日志引用 |

stage.failure_record与preflight.failure_record完全一致，内容为contract=ugs-failure-v1及path；path须为本阶段output_dir内尚不存在的文件，其父目录已存在。运行失败时适配器在退出前原子写入不超过64KiB的JSON：contract、job_id、run_id、stage_id、generation、stage_nonce、manifest_sha256、automatic_retry=false、phase、category、reason、message、exit_code，以及verifier响应和error_log_paths。message最多8192字符，stdout/stderr保留有界尾部，完整日志仍留项目目录。记录失败不得遮蔽原始异常；无法归因时使用UNKNOWN，不把所有失败推断成参数错误或显存不足。

`ugs experiment list`的FAILURE列展示原因；`ugs experiment show J`展示分类、阶段、消息、证据及日志路径。CLI只读取有界记录并核对job/run/清单/generation/nonce身份，不修改历史作业。旧作业缺少结构化证据显示LEGACY_NO_STRUCTURED_RECORD；文件缺失、损坏或身份不匹配显示UNVERIFIED并保留调度器原始错误。进程被SIGKILL时适配器可能来不及写记录，不能保证每次都有细分原因。

verifier响应超过失败记录64KiB上限时，完整响应单独保存项目目录，以oversized_response/full_json_path/SHA/bytes引用，并记录ADAPTER_CONTRACT/VERIFIER_RESPONSE_TOO_LARGE后拒绝启动，不截断JSON后声称完整。initial按实际返回字段核对基础运行版本及身份，候选版本由源码PIN确认；preload还必须核对candidate_revision。

stage_sha256绑定完整最终stage对象：SHA256(json.dumps(stage, sort_keys=True, separators=(",", ":")).encode())，ensure_ascii使用默认True。变更argv、cwd、资源档或其他阶段字段后必须重新运行预检，不得复用旧报告。

## 下一次新实验的显存来源诊断（2026-11-02）

**当日使用限制更正，优先于本节后续原生跟踪说明：** 诊断v1持续ltrace只有磁盘限额，没有运行时间/事件限额，高频CUDA调用可导致严重开销；不得再用于新提交的长时GPU实验。未来新模板保留内部phase时间记录，显式trace-tool=none并重新冻结/验收；不以此声称采集了原生CUDA调用。原CPU假库验收与旧PIN不改写。现有作业不热改，停止须依授权走UGS整链回收，不单杀tracer或尝试未经验证的detach。后继有运行开销预算的原生方案待独立验收，详见[诊断使用限制](GPU_DIAGNOSTIC_USAGE.md)。

仅接入未来新提交的独立清单。不得为启用诊断改写已注册清单、运行阶段、冻结源码或PIN；不得重跑训练来补日志。诊断不是新实验派发入口，仍由现有adapter完成initial/preload并遵守原启动时限、guardian及资源准入。诊断工具不得包裹verifier，不额外初始化CUDA或调用GPU reset。

时间记录覆盖：每rank训练初始化（torch导入与set_device前后）、训练worker开始/结束、训练产物校验开始/结束、每个checkpoint导出子进程开始/结束、验证/测试worker开始/结束。使用UTC墙钟纳秒和monotonic_ns，附job_id/run_id/stage_id/generation、PID、rank及可见UUID；不用文件mtime推断时间。异常也写结束标记；SIGKILL/主机故障导致的缺失必须如实标注，不能补造成功结束。

原生跟踪采用ltrace的精选函数入口断点（-x），覆盖动态符号查找调用；不采用仅Python profiler或仅strace记录冒充完整CUDA API。仅记录初始化、上下文与显存分配/释放，不跟踪每次算子/内核发射。原生跟踪仍可能受符号版本、静态链接及ptrace限制而漏记，未采集或截断必须显示，不得以空日志断言没有CUDA调用。跟踪会引入时延，验收须确认项目的超时与最终preload边界未被破坏。

诊断留在各项目本阶段输出目录。下一条有限链最多8次原生跟踪，每次最多4MiB，合计最多32MiB；阶段时间事件共享64KiB预算，固定文件集合由新清单CPU预检核实。不得通过新建无限文件规避预算；与训练stdout/stderr分流，达到预算停止保存新增诊断但继续消费管道，不中止实验、不影响原始实验日志。记录截断、丢弃字节数与采集器错误。只启用下一条有限实验链，不默认为以后所有实验永久开启。项目历史诊断清理遵守项目删除规则，UGS维护不得自行删除项目文件；总量达到已约定预算前禁止自动扩展采集。

验收及版本PIN由未来新模板绑定，未完成原生CPU假库、日志限额、返回码、输出隔离与项目预检之前不得声称生产诊断已启用。

## memory verifier生产迁移

新提交及本次六个未启动job工程修订统一绑定[显存策略部署](GPU_MEMORY_ALLOWANCE_DEPLOYMENT.md)中的verify_launch_memory.py、同目录preload.py和memory_runtime.py。该节取代上文旧verifier路径的默认选择，其余七项CPU检查、源码/最终stage SHA、许可身份与10/20秒预算不变。项目必须实际验收新调用，不手改已签报告；旧源码与历史清单保留。调度/加载前/收口使用同一显存规则，失败不自动重试。
