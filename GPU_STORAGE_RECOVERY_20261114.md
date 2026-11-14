# 2026-11-14 存储故障：恢复被阻塞

本次用户授权排查生产UGS的Incomplete observation tail并恢复既有job-000110，不重复提交。所有远端命令仅gpu07。

service.log记录resources.jsonl的fsync出现EIO，随后service-events.jsonl的fsync也EIO，协调器退出。首轮核验发布源码完整性通过、状态journal完整、active=[]、110=QUEUED。资源日志前19278条完整，offset58071538之后11594字节撕裂/零填充尾部。

恢复脚本在保存压缩原件前尝试创建备份文件，返回ENOSPC，因此尚未截断资源日志。后续启动协调器返回存活，但维护服务启动失败；停止命令超时，重新核验发现状态journal也出现未完成尾部。恢复尝试期间文件系统仍不可可靠写入。已准确核验PID/start/boot/uid后停止本次启动的协调器39634，不继续派发。

/public为LeoFS，df显示约429GiB可用，但小文件创建仍返回ENOSPC；quota -s无输出。不能仅凭df断言可写，也未证明具体账户配额或存储服务端根因。尚未修复服务、尚未派发110。

13:36:34台北取证：events.jsonl有效12条、逐条state SHA通过，有效前缀47621611字节，损坏尾4019184字节；最后有效状态active=[]、110=QUEUED。resources.jsonl有效前缀58071538，损坏后缀17520字节；service-events.jsonl完整1718条。服务器原件未截断/删除。

本机受限目录保存4个文件的完整gzip原件（events/resources/service-events/service.log），全部解压SHA核验通过：`/Users/ccxyz1/.codex/ugs-recovery-evidence/20261114-storage-failure.json`，SHA `e2cd3acfff2566e0c3998fa1a345a5981bda9b37dee3914af8572e7cd4d0d3fe`。运行数据不提交Git。

后续必须先恢复/public可靠创建/写入/fsync/readback，再保存服务器端原件及恢复记录，按完整校验的边界恢复状态journal与观察日志，保留所有坏尾原件。资源观察不得把损坏片段解释为空闲；启动后重新采集。不得重复提交110或自动重试终态作业。当前tools/repair_observation_tail.py仅针对末行撕裂并拒绝中间损坏，不适用于当前已追加记录的resources后缀或状态journal；不要直接再次应用。

## 13:40台北：429GiB与ENOSPC排查

- 已在服务root、acceptance、runtime及此次备份目录各做一次独立创建探针，全部ENOSPC；最终复查仍失败。没有进行项目目录搜索或删除。
- 指定/public的用户、组quota及repquota均明确报告该挂载未启用/未被标准quota识别。因此没有标准Linux配额超限证据，但无法从这些工具排除LeoFS原生配额。
- gpu07内核日志实际出现多次 `create: send to mds error, vol:1, err:-110, error:-110, trynum:4`，同时有LeoFS writepages调用栈。已确认文件系统元数据请求异常；服务端原生配额、元数据池、单节点可分配容量需管理员核验。普通账户无可用LeoFS管理工具，不修改挂载/内核/服务器配置。
- df的429GiB为挂载报告的全卷剩余空间，不能说明某个用户/目录/元数据服务可以成功创建。df -i全部为0，不是可靠的inode耗尽证据。
- 旧代码清理发生于11/12 14:16台北，范围仅服务目录内242个旧源码、2,882,829字节；当时release_ok和服务正常。之后观察日志仍成功至11/14 00:01台北。没有证据支持代码清理引起空间不足；它没有删除项目数据、挂载或文件系统配置。
- 最终诊断原件仅保存在本机受限证据目录 `20261114-capacity-diagnosis.json`，SHA `df5366ae1f551ecbf54e404e779af80025b0a4ba8f9a599cba4b2ea91c0802e7`。存储未恢复，仍不修剪日志、不启动UGS、不重复提交110。

## 17:45台北：存储验证通过，日志修复与派发结果

用户再次授权：文件系统恢复后修复日志，执行既有110，不重复提交。本轮先在UGS acceptance通过创建、write/fsync、原子rename、目录fsync、读回。events/resources原件SHA与此前备份完全相同。

采用 `tools/recover_storage_20261114.py`，锁定daemon/maintenance/state/observation，拒绝活动进程、运行作业和任何原件SHA变化；完整校验前缀JSON、状态SHA与连续revision。服务器保存4份完整gzip原件并解压核验后，以fsync+atomic replace恢复完整前缀；12条状态、19278条观察保留。作业状态与源码PIN完全不变；坏后缀仍在原件中。压缩证据总量约8.6MiB，目录 `acceptance/storage-recovery-20261114-184e53237435/`；completed.json SHA `c956fd2242e1b6e815a16d3dc8f4114aead471a32d1abee4fdd905aa36cd5862`。恢复工具的4项完整/截断/JSON损坏/SHA或revision损坏检查通过。

公共maintenance start、scheduler start、resource status、scheduler resume成功，观察不再报尾部错误。原job-000110于17:45:36分配index1,2并实际派发一次；17:45:38 adapter退出1，17:45:50显存和受管进程收口核验完成。最终FAILED，没有重试或重提。

这是独立的项目入口路径错误：resume109-v1/source/managed_vllm_index_candidate_v1.py的ARTIFACT_TASK仍硬编码旧storage-v1目录，与清单的resume109-v1结果路径不符，guard.identity拒绝；异常记录也写旧目录中已存在的文件而报FileExistsError。发生在initial/preload与模型加载前。已反馈项目会话修复未来入口及真实CPU路径一致性预检；未覆盖冻结源码或修改终态。job110-outcome.json SHA `a2cbabb0c00a1f43371ee510d12caa469be9bc4a65df0a79a9902dee099c5181`。

最终服务状态：daemon和maintenance存活、release_ok=true、paused=false、active_count=0、recovery=null。存储恢复的服务端措施未知，不能据此宣称已确定原ENOSPC的配额或后端根因。
