# GPU 当前状态准入：30秒监测，无连续空闲等待

2026-11-12用户将目标从60秒等待调整为：不再为猜测他人未来用卡而累计空闲时间；每30秒检查，整组GPU满足阶段资源表即可派发。本文件替代旧协议、两分钟部署和项目标准中对应的连续空闲条款。冻结协议文件、核心和旧项目源码/PIN不覆盖。

## 当前行为

| 条件 | 行为 |
|---|---|
| 周期监测 | scan_seconds=30 |
| 连续空闲等待 | idle_seconds=0，不累计时长或样本点数 |
| 准入依据 | 最新一次完整、有效且当前的整组GPU观察 |
| 陈旧或异常观察 | 最新有效观察超过35秒、采集超过5秒、未知或查询失败时拒绝；下次有效观察即可重新判断，不启动新的等待计时 |
| 启动前复查 | 两次实时复查相隔至少2秒；最终快照年龄不超过2秒 |
| 其他限制 | 资源档GPU数量/UUID、显存已审基线、无用户进程、利用率、健康/ECC、主机/磁盘预算、已有分配、明确预约、容量/暂停/门禁全部保持 |

“立即派发”指满足条件后无需额外累计空闲窗口，不代表跳过FIFO、合同和启动前核验。GPU刚释放时，最迟到下一次成功周期采样才会被发现；实际调用有执行耗时。一次GPU查询超时不能当作空闲，旧的合格快照不能覆盖更新的失败或占用记录。

不再依据空闲更久来额外等待。等待凑足资源时不占部分卡；不驱逐外部进程。明确登记的预约仍生效，解除后须取得新的有效观察，不重新等待两分钟。正常阶段收口后也按当前状态重新准入。

## 生产入口与兼容

服务根为 `/public/home/kzhang19/kezhang/cc/global-gpu-scheduler`，公共调用仍为该目录的 `ugs`，所有操作仅在gpu07。

- 协调器：`candidates/idle_window_v2/37b9634c7b9d/ugs_idle_v2_entry.py`。
- 公共CLI中文帮助覆盖入口：`candidates/current_admission_help/48082beff78b/ugs_current_help.py`。调用既有协调器实现，不创建第二个服务。
- policy：`idle_seconds=0`、`scan_seconds=30`、`max_sample_gap_seconds=35`。
- runtime扩展标识：`ugs-idle-window-v2`；准入模式：`current_state`。
- `max_experiment`、项目间轮转、项目内FIFO、门禁和失败不自动重试保持。

旧verifier硬编码120秒或300秒，不能靠只改全局policy兼容。未迁移阶段显示 `VERIFIER_MIGRATION_REQUIRED`，不先启动再失败；必须生成独立适配器及真实CPU报告，通过公开 `validate`、`update --expected-sha` 迁移未开始阶段。活动阶段不热改。旧helper留作CPU来源证据，不继续作为新阶段的实际准入入口。

## 项目PIN和使用方法

PIN文件：服务根下 `acceptance/current-admission-v2-pins.json`，SHA256为 `2e77ce68555cdc8c3057516d2b7133d4a46a6107758042ad38f055624e3aaac9`。

| 文件 | 服务根下路径 | SHA256 |
|---|---|---|
| verifier | candidates/idle_window_v2/37b9634c7b9d/verify_launch_idle_v2.py | ccd7d5d55dfbf57179372b44a54cd6e8095c65a9575ba82fce41274988a273c6 |
| 当前准入helper | candidates/idle_window_v2/37b9634c7b9d/idle_runtime_v2.py | b61d11de3e6cd85018092da1947f19638f17512d5d2d45e948a9181256bb07ed |
| preload依赖 | candidates/memory_release/9ffee907f032/preload.py | 6ee2d1354e9af32a430ce1d18b5ddef8b5a1344216608d0797e33f4a8c36212f |
| 显存helper | candidates/memory_release/9ffee907f032/memory_runtime.py | 7b9e57be91c37434586dcaf9fbbd7da1cb03a3d5aa45cdfdf78ac16a772a5b78 |
| 保留来源PIN | candidates/idle_window/ad910adbc734/idle_runtime.py | 9c1a23f38eeab2536f1612106903efbfcaebabdf0b77921f91594e9cc81f2f04 |

新CPU报告的source_sha256绑定上述路径。preflight.verifier使用新verifier path/SHA，依赖及 `candidate_revision=ugs-preload-v3.2-candidate1`、initial10秒/preload20秒保持。七项CPU检查须针对最终argv、源码和报告真实执行，不复制旧passed标记冒充重验。科学配置、资源档、输出及job/run/队位不因工程迁移改变。

```bash
ugs experiment validate --file /本项目/新清单.json
ugs experiment update JOB --expected-sha 旧清单SHA --file /本项目/新清单.json
ugs experiment show JOB
ugs queue list --project PROJECT
ugs resource status
```

不要修改内部入口、冻结PIN或用重新提交绕过排队。终态失败job不复活。

## 验收与生效证据

69项CPU检查通过，包含即时单次样本准入、旧占用后恢复无需冷却、最新失败不得复用旧样本、35秒陈旧和5秒采集上限、健康/进程/基线/回退、旧verifier阻塞、混合预检合同、30秒周期及活动/替换收口、最终快照与慢校验时序、并发/暂停/预约竞态、真实临时日志完整性。CPU测试不冒称模型GPU科学验收。

2026-11-12 13:38:37（台北）已在无活动实验窗口切换协调器。所有job、run、队位、项目及资源表保持；实验停止和重启均为零。max_experiment=1，scheduler_paused=false。

- CPU：`candidates/idle_window_v2/37b9634c7b9d/acceptance.json`，SHA256 `525f04af55197e7a31a47e6cc20dbd26115c56e4d3b11ceb5508b8653c0bf8c6`。
- 生产切换：`acceptance/current-admission-v2-production.json`，SHA256 `427156e1258d93af33aaaf14e66f50c0494e7b0b47c71274c3207f4b6fb632d7`。
- 新旧CPU合同与无许可拒绝：`acceptance/current-admission-v2-registration.json`，SHA256 `ceb616c5a287cfe5b51b972a0fb492c9304a52e9380e4d2caca179ef01cc85a6`。

源码和静态验收索引纳入本地仓库；生产队列、GPU快照和进程身份只留服务器证据，不写入Git。

## 项目迁移与真实采样复核

截至2026-11-12 13:45:01（台北），job103–106的五个阶段均绑定即时准入verifier。项目已执行真实CPU检查与公开validate/CAS update；中央独立核对旧新科学/资源/输出语义、job/run/队位、CPU报告及全部源码/七项预检证据SHA通过。没有重提、重排或复活失败实验。验收证据 `acceptance/current-admission-v2-migration-verified.json`，SHA256 `0524df7f7db262750fcbf1fc6ffddfa78689d0cd9c0ec87207db1432e7e48f48`。

生产前8次周期采样的实际开始间隔为30.446–32.535秒，包含调用/循环耗时；4次有效，最新有效观察单次即有4张合格卡，没有累计等待。记录在 `acceptance/current-admission-v2-live-monitoring.json`，SHA256 `dd782d96ecf8c10401da673af40af67d859a3bbb4c67635a1014ea0cd8ea33fe`。

截至上述复核时间，队首job103已尝试第一次启动前GPU复查，但采集超过5秒，按既有完整性规则拒绝，仍QUEUED；104、105、106继续项目内FIFO等待。该原因不是旧空闲窗口，也不是实验执行失败。后续自动观察不等于重启失败实验；尚未授予启动许可的队首仍可在满足实时条件后派发。

中文帮助4项真实查询通过，CPU及项目原PIN不改，帮助证据为 `candidates/current_admission_help/48082beff78b/acceptance.json`，SHA256 `d2134147f3783e6d3683b16b6ce41ffc0d72454a19d733f4f40f899ad00bd5a8`。
