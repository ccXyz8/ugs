# GPU 连续空闲窗口：120 秒

> 2026-11-12最新规则：每30秒监测，取消连续空闲等待和累计采样点；整组资源满足即可进入派发，进程/健康/基线/分配/明确预约及启动前双复查保持。旧verifier未迁移阶段明确阻塞。见[即时准入部署](GPU_CURRENT_ADMISSION_DEPLOYMENT.md)。

用户于2026-11-03明确将等待显卡的连续空闲窗口从300秒降低为120秒。本文件替代旧协议和历史部署记录中的五分钟规则；原始冻结协议文件及其SHA不改写。

## 当前规则

- 每30秒扫描；整组共同连续空闲至少120秒、至少5个有效观测点。
- 间隔超过35秒、漏采、未知、异常或使用会使对应资格失效，重新观察。多个快速复查不能代替120秒经过时间。
- 进程可见且无用户进程、GPU及显存利用率为零、健康/ECC/驱动及布局审核保持。显存4–7MiB许可与收口超过7MiB全局回退锁存保持。
- 启动前两次复查相隔2秒；initial及preload继续核实实时GPU、共同窗口与完整运行身份。
- 不改项目间轮转、项目内FIFO、容量、预约避让或失败不自动重试。不停止或重启正在运行的实验。
- 两分钟是候选资格条件，不是每个排队job固定等待两分钟，也不保证两分钟后就启动。已满足时无需额外再等；其他条件不满足仍等待。

## 实现与旧阶段兼容

执行器、查询和新verifier安装同一idle_runtime.py，读取持久policy.idle_seconds=120；仅支持120或历史300两种已验收时窗。连续时间与最低点数同时检查。原始核心、memory helper、preload及历史项目PIN均不覆盖。

旧验证器仍要求300秒/11点。因此调度器对尚未绑定新verifier及helper CPU证据的阶段保留300秒，避免120秒派发后被旧验证器误判失败；这是明确的旧阶段兼容保护，不是所有阶段已经迁移。更新使用公开experiment update及CAS，活动阶段不热改，科学参数/资源/输出/身份/队位不变。

观测记录仍使用原有基线/设备签名，以兼容旧验证器。减少时窗不改变空闲观测本身含义；显存回退/基线变化继续使旧记录失效。协调器受控交接后按新daemon身份重新累积观测。

## 新接口与PIN

服务根：`/public/home/kzhang19/kezhang/cc/global-gpu-scheduler`。

| 文件 | 服务根下路径 | SHA256 |
|---|---|---|
| 新verifier | candidates/idle_window/ad910adbc734/verify_launch_idle.py | b8e8604c99a4aeae6558bff31956956daea80cbe7aab79d6e8e43163221fdfec |
| 时窗helper | candidates/idle_window/ad910adbc734/idle_runtime.py | 9c1a23f38eeab2536f1612106903efbfcaebabdf0b77921f91594e9cc81f2f04 |
| preload依赖（不变） | candidates/memory_release/9ffee907f032/preload.py | 6ee2d1354e9af32a430ce1d18b5ddef8b5a1344216608d0797e33f4a8c36212f |
| 显存helper（不变） | candidates/memory_release/9ffee907f032/memory_runtime.py | 7b9e57be91c37434586dcaf9fbbd7da1cb03a3d5aa45cdfdf78ac16a772a5b78 |

CPU报告source_sha256绑定上述四个绝对路径。preflight.verifier的path/sha256为新verifier，dependency_path/dependency_sha256仍为上表preload。candidate_revision仍ugs-preload-v3.2-candidate1，initial_timeout_seconds=10，preload_timeout_seconds=20，基础runtime_release仍ugs-exec-v3.1。既有七项CPU检查适用范围不扩大；Sundial须用真实CPU检查绑定新调用与preflight.verifier以声明120秒能力。

公共CLI/协调器入口：`candidates/idle_window/ad910adbc734/ugs_idle_entry.py`。所有CLI保持原命令，不需要新端口或新启动方式。

## 验收与切换

13项Linux CPU合成历史测试通过，覆盖120秒边界、5点、漏采/陈旧/未知/占用/健康拒绝、整组准入、旧300秒兼容、预约及4–7/>7回退。4项实际CLI查询/中文帮助通过；新initial/preload在无许可时均拒绝。CPU测试不冒称真实GPU科学实验验收。

已于2026-11-03 21:49:05（台北）完成生产切换并恢复派发，idle_seconds=120、max_experiment=1、scheduler_paused=false，无恢复阻塞。保留job43原guardian、adapter进程和generation，未重启实验。job44和job53全部未启动阶段已通过项目真实CPU验收与公开CAS update迁移；job43当前train100原样运行，后续eval50/eval100以pending_revision登记，完整收口后自动应用。中央独立重跑job_validate、same_semantics及120秒能力识别通过。全部job/run/队位、科学参数/资源档/产物目录保持。

生产证据：`acceptance/idle-window-v1-production.json`，SHA256=`bdeaf0fbb1b08dce1287630fd07be6c2cf506f715a182d3a8d8c87a225d0f4d3`。后续自然GPU阶段的initial/preload结果另留运行证据，不为验收而抢占GPU或重启实验。
