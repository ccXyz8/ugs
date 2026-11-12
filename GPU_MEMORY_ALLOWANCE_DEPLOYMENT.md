# 显存基线范围与自动回退

> 2026-11-03时窗更新：用户将连续空闲要求降为120秒/至少5点。本文历史五分钟规则及旧verifier说明由[两分钟窗口部署](GPU_IDLE_WINDOW_DEPLOYMENT.md)对应条款替代；运行阶段不热改，未迁移旧PIN保留300秒保护。

已于2026-11-03 00:08:41（台北）生产启用并恢复派发。六个未启动job30/31/32/34/35/36已通过公开update完成工程绑定迁移，job-id/队位/科学参数/输出保持不变。生产审计：acceptance/memory-allowance-v1-production.json，SHA256=7253875e0c74ef36d1acda3ed8914f47d3dffb22cf94e63c72b4fa707549f088。启用时两张7MiB卡已通过完整五分钟观察。

范围：仅当前boot/driver下证据有效且审定memory_mib=4的A100，原审定证据不改写，新增memory_ceiling_mib=7。允许整数4/5/6/7 MiB，不是允许低显存用户进程。未审核卡不自动启用。

准入：进程列表须可见且为空；GPU/显存利用率须0；健康、ECC、GPU类型、driver、reserved/total布局检查不变。每30秒扫描、整组共同300秒完整观察至少11点、漏采/异常失效、启动两次复查相隔2秒不变。

回退：确认guardian身份和全部受管后代退出后，若本次分配的无进程卡、有效基线且driver一致，实测整数显存>7 MiB，则状态事务全局将4 MiB基线的ceiling恢复4并锁定disabled。保存触发UUID/显存、job/run/generation/时间；当前收口不完成。不是看到训练中显存>7就停止，也不对外部进程触发回退或杀进程。政策SHA改变使此前五分钟记录失效，重新观察；不会自动重新放宽，单改ceiling不能绕过锁存。

实现：不改冻结core或旧verifier文件。调度器、新verifier、收口共同安装同一memory_runtime.py；新verifier硬核helper SHA；服务release同时核所有新增文件。新预检强制新verify/preload/helper三文件绑定。旧注册作业的未启动工程阶段通过项目真实CPU准入和公开update迁移，不改变科学参数/资源档/输出/队位，旧源码和清单保留。

新项目预检verifier对象：

- path：`/public/home/kzhang19/kezhang/cc/global-gpu-scheduler/candidates/memory_release/9ffee907f032/verify_launch_memory.py`
- sha256：`4f665def9e7500104735f40d6e8f8383c028e18902cbad2ccfefaf63d49b41e6`
- dependency_path：同目录`preload.py`
- dependency_sha256：`6ee2d1354e9af32a430ce1d18b5ddef8b5a1344216608d0797e33f4a8c36212f`
- candidate_revision：`ugs-preload-v3.2-candidate1`
- initial_timeout_seconds=10，preload_timeout_seconds=20

CPU报告source_sha256额外绑定同目录memory_runtime.py：`7b9e57be91c37434586dcaf9fbbd7da1cb03a3d5aa45cdfdf78ac16a772a5b78`。基础runtime_release仍ugs-exec-v3.1；七项真实CPU证据、最终stage SHA及所有许可身份核对不变。旧报告/PIN不能手填替换，必须验收真实最终调用。

中央验收：6项实际helper准入/收口CPU回归、2项真实idle_state合成八卡历史测试、2项无授权verifier拒绝检查。不会在生产GPU制造8MiB占用来测试回退；回退用CPU故障注入验证。实际上线和迁移完成以生产审计为准。
