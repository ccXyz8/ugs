# preload v3.2 候选审查与接入方案

状态：已按用户授权登记为可选生产verifier；真实GPU接口验收待项目新实验。2026-11-01。候选阶段仅上传独立验收bundle完成Linux CPU集成；后续获批的生产可选入口登记见文末，未重启服务、改变容量/队列或重试旧实验。

## 解决范围

针对job-000010报告的最后GPU快照超过2秒：v3.1先完成第二次扫描，再等待最终state.lock和重放状态；本候选先取得最终状态锁、重放及复核，再进行第二次扫描。采样间隔仍至少2秒，五分钟共同空闲审核不变。最终事务内保留代次、撤销、调用者、GPU UUID、跨实验分配冲突、预约、发布/源码PIN、主机与磁盘预算检查。

不假定事发延迟一定来自锁，也不放宽2秒限制。第二次扫描后仍做完整历史和源码/资源检查，慢查询、存储或检查仍可能被安全拒绝；本候选不是所有延迟问题的保证性修复。不减少完整源码PIN，不重用preload，不自动重试。

记录initial/final状态锁及重放、历史读取、空闲窗口审核、源码、身份/发布审核、预算、两次扫描、间隔、状态提交、审计写入和最终快照年龄。锁等待与状态重放目前合并计时，不能用此字段进一步分离两者。拒绝审计进入既有service-events.jsonl白名单维护范围，JSON响应同时携带耗时。

状态提交和审计fsync之后再次检查年龄；若此时过期，返回allowed=false。此时可能已有preload_authorized审计/一次性标记，但这不构成适配器的成功许可：适配器必须检查本次调用退出码0且allowed=true。标记保留以禁止重用，按原失败路径收口，无自动重试。审计字段allowed_before_audit刻意不表示最终返回值；审计写入失败或过慢时，以返回JSON及项目失败回执为最终结论。此边界须在服务集成验收中专项检查。

最终扫描在state.lock内进行，会增加全局管理命令等待时间；该锁是原有store→observation顺序，不引入相反锁序。需要真实服务器CPU集成测试测量等待和并发取消；本地模拟测试不能证明网络文件系统延迟上界。

## 文件及版本

- preload.py：独立候选算法，不在导入时修改生产core。
- verify_launch_v3_2.py：可选新入口，校验冻结core SHA，初始许可仍调用冻结v3.1路径，--preload调用候选。
- test_preload.py：14项确定性CPU故障注入，核心接口和时钟模拟；不访问GPU/SSH/生产队列/项目文件。
- cpu-test-result.json：本地测试结果，明确server_integration_tested=false。
- source-manifest.json：上述文件及冻结原件的SHA。这不是生产发布许可。

现有runtime_release和返回verifier基础版本仍为ugs-exec-v3.1，因为状态schema、阶段合同和原core未变；新增独立candidate_revision=ugs-preload-v3.2-candidate1。不能将其宣传为已部署的ugs-exec-v3.2。旧core/verifier/protocol的字节与SHA不变。候选属于新verifier路径/PIN选择，不是悄悄改变旧入口。

本地入口为本目录verify_launch_v3_2.py。拟议服务器位置为：

    /public/home/kzhang19/kezhang/cc/global-gpu-scheduler/candidates/preload_v3_2/verify_launch_v3_2.py

此路径在候选阶段未创建；现已按文末登记记录创建。目录布局要求入口上两级根目录下存在source/ugs_exec_v3_1.py；所有候选文件须共同上传，不能只复制入口文件。

## 接入顺序与授权边界

1. 审查本候选及SHA，运行本地CPU测试。已完成；这不等于真实GPU验收。
2. 在用户确认的服务器隔离fixture目录执行真实Linux Store/锁、完整权限检查、并发取消、慢磁盘/审计和完整适配器CPU集成验收。限定测试根，不读写生产队列，不创建CUDA worker。现已执行并通过下述8项隔离Linux CPU协议集成。
3. 集成通过后，用户或获明确授权的管理会话批准生产接入：上传固定候选目录；在state.lock下将新入口及依赖SHA和验收证据加入生产executor_release；保留旧入口以供已冻结阶段继续使用。无需仅因增加可选入口而重启协调器；不改容量或暂停标记。
4. 项目在独立新清单和输出目录显式选择候选verifier，完整source_sha256及CPU准入报告纳入新verifier和preload.py的PIN，初始和preload均使用新入口；新接口实验正常入队，不修改job-000010或任何当前阶段。不得自动重新提交失败实验。
5. 真实有界接口验收通过后，才决定后续正式实验是否采用。失败仍保持终态，不自动退回旧入口、不绕过检查。

制定本方案时这些生产动作尚未执行；最新执行状态见文末登记记录。依据AGENTS“生产代码必须经过相应验收，不能暗改运行阶段或冻结适配器PIN”及本次项目会话“只准备候选，不部署”的明确范围，步骤3和4不能由本次候选开发自动触发。需要确认的具体动作是生产发布新verifier/PIN，以及由项目建立新的有界接口尝试；无需批准停止旧实验或变更全局容量，因为本方案不包含这些动作。

## 测试复现

    python3 -B candidates/preload_v3_2/test_preload.py

CPU测试涵盖成功、最终锁前6秒延迟、扫描后超时、状态提交超时、审计慢/失败、等待锁时取消/预约、UUID冲突、采样失败、历史改变、最终预算/发布PIN改变及准备期限过期。全部14项通过；模拟接口测试尚不能替代真实进程身份、真实文件锁/文件系统及GPU验证。

## 真实Linux隔离CPU集成结果（2026-11-01）

8项全部通过，证据与逐阶段耗时见linux-integration-summary.json。使用冻结core真实Store、flock、日志校验/fsync、/proc进程身份、发布和源码SHA、内存/磁盘预算以及完整初始许可→preload→条件启动CPU worker协议。以真实guardian→fixture适配器→verifier子进程建立亲子/session身份；没有运行项目适配器或CUDA。GPU硬件数据仍为8张模拟卡，扫描的记录锁/落盘及五分钟窗口判定使用真实core。

覆盖成功、真实锁竞争、等待锁时并发取消、慢状态提交、慢审计写入、错误代次身份、最终预算变化、一次性许可重用。锁竞争用例等待最终state.lock约3.000秒后重新扫描并成功；慢提交/审计及其余拒绝用例均没有启动CPU worker。

最终验收目录：/public/home/kzhang19/kezhang/cc/global-gpu-scheduler/acceptance/preload-v32-linux-1217dc945a0b4605aae543d4a6f4d5c5；完整linux-integration-result.json SHA 375dc6fbd01ab956e875c504d74d644c07fa4d2dd5af3a7d34ae943b9dd5277f。首轮fixture错误只提供2卡而被8卡检查拒绝，旧目录preload-v32-linux-be50028bbd3649ddab574c09891042ed留存；修正fixture后候选算法及入口SHA未变。

测试重定向所有Store与观察路径到bundle/fixtures，并以Python审计钩子拒绝访问bundle以外的调度状态文件；没有读取或修改生产队列。Linux CPU协议集成已完成，但真实项目适配器/有界GPU接口尚未验收；当次测试结束时步骤3生产发布及项目新尝试尚未执行；最新登记状态见下文。

## 生产可选入口登记（2026-11-01 15:39台北）

用户明确批准登记新verifier/PIN并让ms-swift-main提交新的有界接口实验优先执行。上文步骤3已完成：两文件上传生产候选路径并在state.lock内追加executor_release文件PIN与CPU证据，release_ok通过。旧入口和冻结原件不变，服务未重启、容量未调整、job-000010未复活。此为可选入口登记，不宣称已完成真实GPU接口验收。登记证据为服务根acceptance/preload-v32-production-registration.json及executor-release-preload-v32-candidate.json。项目负责新的清单、CPU报告、输出目录和正式自助提交，不改旧运行阶段。
