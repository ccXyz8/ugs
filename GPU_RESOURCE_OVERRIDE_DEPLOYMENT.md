# 已授权的未开始阶段资源变更

2026-11-05 15:53:09（台北）已上线 `ugs-resource-override-v1`。触发场景是用户明确要求 job71 的未开始 test 从双卡改为单卡GPU5，保留原700个测试窗口、随机种子、已完成训练/验证和原job身份。普通 update 继续拒绝资源语义变化；不自动降级。

## 公开操作

```text
ugs experiment update JOB --file MANIFEST --expected-sha OLD_SHA \
  --resource-authorization AUTHORIZATION_FILE [--project PROJECT] [--dry-run]
```

- 授权文件必须由获授权管理会话登记到UGS的 `resource_change_grants`，路径及SHA核验通过；项目自行写一份JSON不构成登记。
- 授权绑定job-id、run-id、project-id、新旧清单canonical SHA、当前未开始阶段索引、完整后缀stage-id及精确资源档，引用项目目录内的用户授权证据并保留用户指令出处。
- 当前存在STARTING/RUNNING/STOPPING阶段时拒绝；终态、pending revision及未完成替换也拒绝。不使用stop/replace/pause实现资源变更。
- 已完成前缀（含准入报告引用）不变，阶段数、科学合同、预算、门禁和产物路径不变。仅授权后缀的argv、source_sha256和resource_profiles可变化，submission_version需使用未占用的非空新版本。
- 原有真实CPU准入、资源报告绑定、项目路径限制、幂等和CAS检查照常执行。完整validate因已完成前缀回执存在而拒绝时，用上述公开update --dry-run执行真实后缀检查，不能删除旧回执规避校验。
- dry-run只修改内存副本，不写队列、不预留卡、不停止进程；正式执行重新检查。成功后保留job/run/队位/结果和前缀，留下resource_override_history；使用过的旧SHA授权不能重放。
- 同UNIX账户的授权记录提供审计及防误操作，不声称抵抗该账户所有者篡改状态。

## 指定GPU

资源档可声明 `gpu_uuids: ["GPU-..."]`，CPU报告的resource_profiles必须逐项同样绑定。只允许列表内GPU，仍按实际合格空闲时间选卡；目标卡被占、预约、健康异常或窗口不完整则等待。该字段同时用于调度选择和最终launch_valid复查；adapter须使用UGS分配，不自行覆盖CUDA_VISIBLE_DEVICES。

不带该字段的原资源档继续按原逻辑选卡。多个资源档仍按已经验收的声明顺序选择。物理编号需先在gpu07核对UUID，不能把临时GPU index当作稳定身份。

连续120秒/至少5点、30秒扫描、35秒漏采失效、启动前两次相隔2秒复查及4–7MiB/>7回退保持。新入口仍使用已验收的idle verifier和原初始/预加载许可，不改冻结核心与旧项目PIN。

## 部署与验收

公共入口：`candidates/resource_override/a31d2dcc3d5b/ugs_resource_entry.py`。

| 文件 | SHA256 |
|---|---|
| resource_override.py | cbf265944d17ef6e1cae629ecf2c5631b787dc013fc62ac77471318208351706 |
| ugs_resource_entry.py | 7eb9b029c01707ec61e380d387c01ccc4561df3f94ef580d8f3f29e170ccdcd0 |
| CPU验收acceptance.json | d52f940bcf8f0c94796d8d3049381a6fe9a6a2a020c53fa01968c54d133f1a2f |

13项Linux CPU测试通过：授权身份/SHA/范围、前缀和科学不变性、无运行阶段限制、默认冻结规则、验证失败恢复、授权重放拒绝、指定GPU/预约/占用/最终复查等。3项CLI帮助/查询检查通过。真实job71状态副本上的真实后缀准入通过；公共dry-run通过且全状态摘要保持。CPU单元测试中的准入替身不替代项目的实际模型与科学检查。

无活动实验时仅更换协调器；没有调用全局pause/resume，没有改变policy、ring、served、round或任何job。所有服务器动作在gpu07执行，admin只作SSH中转；本地源码工作在本地工作区进行。

生产部署证据：服务根 `acceptance/resource-override-v1-production.json`，SHA256 `bdc137c125bef1873c7e18f017ebdc93ebb2c3716066a3f17c40db23ea4348e6`。正式job更新及GPU运行证据由对应项目另行记录，部署成功本身不等于科学测试完成。

本次job71正式公开update已应用：只改test后缀，原训练/验证对象与结果、scientific_contract、run-id和队位保留。指定GPU5需重新满足完整时窗，不能将更新成功写作测试已开始。现场出现超过35秒采样间隔时照常失效；UGS日志达到64MiB触发既定压缩，未调整阈值或删除项目产物。
