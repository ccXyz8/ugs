# 4–7 MiB 显存范围候选

用户2026-11-02授权：已审定4 MiB基线的GPU允许4至7 MiB（整数），配置baseline[UUID].memory_ceiling_mib=7。不是低显存用户进程白名单：进程、健康、利用率、驱动、reserved/total、共同五分钟和启动双复查全部保留。原始4 MiB证据不改写。未审定GPU不自动获得资格。

候选核心和控制扩展共用memory_matches；候选verifier加载同一核心，故初次授权/最终preload与收口一致。旧baseline没有memory_ceiling_mib时仍精确匹配。非法/超过7/非整数上限拒绝。baseline字段改变会改变观察policy SHA，旧窗口不得复用。

状态：候选实现及6项隔离CPU回归通过，尚未激活生产。当前生产仍有运行中job25，旧verifier和项目源码固定旧core SHA。不得覆盖旧文件、修改运行中job/PIN或强制停止实验。生产切换必须先验收新完整发布及未开始阶段的验证器绑定，正常收口后无活动进程才可切换协调器。未完成前不得把仅修改baseline数值当成已生效。

运行测试：python -B -m unittest discover -s source -p test_memory_policy_v1.py -v。测试从候选实际源码抽取准入/收口函数，模拟设备和退出证据，不接触GPU。

## 超限自动回退

收口阶段先核实guardian代际与全部受管后代退出，且所有GPU无受管PID残留。若分配卡processes为空、已有有效4 MiB审定基线且正在使用7 MiB上限，实测整数显存>7 MiB时，在状态事务中全局将4 MiB基线的上限恢复4，并锁存memory_allowance_disabled。记录触发UUID/显存、时间、job/run/stage/generation。当前收口返回未完成，不虚报释放。

基线策略SHA变化使旧五分钟观察自动失效，准入与最终preload均拒绝旧窗口；重新累计完整窗口后才能派发。不会自动重新放宽，单独把ceiling改回7也不能越过锁存。外部用户进程、未知显存及未分配卡不触发此基础占用回退，不杀任何外部进程。
