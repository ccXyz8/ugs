# UGS v3.1 项目适配与自助准入合同

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
