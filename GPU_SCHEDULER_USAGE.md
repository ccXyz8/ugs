# UGS 统一实验管理器使用说明

当前接口：ugs-exec-v3.1；协议global-gpu-scheduler-v2.0。实际上线及验收证据见GPU_SCHEDULER_DEPLOYMENT.md。只通过既有SSH操作gpu07，不开新服务端口，不使用Slurm。

## 登录与命令入口

在Mac终端执行 ssh -o BatchMode=yes a100，按项目规则核实admin身份（who、pwd、ls、whoami、hostname），再执行 ssh -o BatchMode=yes gpu07；核实gpu07身份及GPU状态后，在当前远程shell设置：

    alias ugs=/public/home/kzhang19/kezhang/cc/global-gpu-scheduler/ugs

服务器kzhang19的.bashrc与.bash_profile已添加该别名，新开的交互Bash终端可直接使用ugs；之前已经打开的窗口需执行上面一行使别名立即生效，不必重启服务。也可直接使用上述绝对路径调用；非交互SSH应使用绝对路径，不依赖alias或PATH。该入口不会在登录时启动调度器。参数P表示实际project_id，J表示job_id，FILE表示服务器端本项目文件绝对路径，SHA表示更新前的manifest_sha256。

## 中文分层帮助

ugs --help提供全部38个公开操作、逐项参数、默认值及常用功能，默认中文表格；也可按对象或具体操作缩小范围：

    ugs --help
    ugs experiment --help
    ugs experiment stop --help
    ugs maintenance set --help
    ugs help resource reserve

-h同--help，ugs不带参数显示总览。帮助只读，不查GPU、不操作队列、不启停进程；即使带--stop-running或--all，遇到--help也只显示说明。指令说明包括用途、必填参数、适用范围、默认值、常见组合及恢复行为；具体操作另给完整语法。--help --json返回结构化帮助。帮助中的J/P/FILE/SHA/IDS需要换成实际值，方括号表示可选。

## 紧急停止与恢复

    ugs scheduler pause --stop-running
    ugs scheduler status
    ugs experiment list --status STOPPING

第一条在同一事务中暂停后续派发并请求取消所有已启动未结束的受管实验链；未运行队列保留，UGS服务继续运行。确认STOPPING全部收口及对应GPU上下文消失后才算释放完成。驱动/进程异常时不会虚报释放，也不会GPU reset或杀外部进程。强制终止只能保留此前已经落盘的产物。

恢复排队实验：

    ugs scheduler resume

被取消或失败的实验不自动重启。若上限此前为0，需要另设正数。若只执行experiment stop --all，没有暂停派发，排队实验可能立即补位，因此它不是完整的紧急阻塞操作。

## 完整公开命令

命令格式统一：ugs OBJECT ACTION [TARGET] [OPTIONS]。默认人类可读表格；脚本解析必须显式追加--json，返回完整原始结构。日志命令显示日志原文。长选项用双连字符；--all必须显式提供，不支持缺少ID时默认全选。所有终止/撤回都保留审计和文件。

| 命令 | 职责 |
|---|---|
| ugs --help | 查看语法及公共选项 |
| ugs scheduler status | 服务存活、并发上限、暂停、活动名额、draining、队列与恢复状态 |
| ugs scheduler get | 查询相同持久调度状态；脚本使用get --json |
| ugs scheduler set max_experiment=N | 热调整非负整数并发上限；默认1；0禁止新链准入，不杀任务；降低时已占名额的有限链drain |
| ugs scheduler pause | 暂停后续阶段的新派发；保留容量、队列和已承诺的启动意图/活动进程 |
| ugs scheduler resume | 恢复派发开关；不改容量、不恢复终态实验 |
| ugs scheduler pause --stop-running | 暂停派发并强制停止已启动链，UGS保持运行 |
| ugs scheduler start | 启动唯一后台服务；已运行则返回原服务；保留容量/暂停状态 |
| ugs scheduler stop | 无活动名额才安全退出服务，并保持派发暂停；有活动名额时拒绝 |
| ugs scheduler stop --stop-running | 先暂停和终止已启动链，全部受管资源收口后退出服务；未收口则保留服务处理停止 |
| ugs scheduler recover | 活动身份全部收口后清除恢复阻塞；不重跑实验 |
| ugs project register --file FILE | 按迁移证据自助登记/更新项目；固定目录与解释器身份不能暗改 |
| ugs project list | 列出登记项目及project_id |
| ugs project show P | 查询项目登记 |
| ugs project pause P | 暂停该项目后续阶段派发，已运行阶段继续 |
| ugs project resume P | 恢复该项目后续派发，仍受全局开关/容量约束 |
| ugs project stop P | 暂停该项目派发，并取消其已启动未结束链；不影响其他项目/全局容量 |
| ugs project unregister P | 仅在无未结束实验时注销登记；保留历史与文件 |
| ugs experiment validate --file FILE | 校验项目清单、源码/证据SHA及自助准入合同，不排队、不加载模型 |
| ugs experiment submit --file FILE | 提交有限实验链；项目内FIFO、项目间轮转；幂等重发不重复实验 |
| ugs experiment list | 所有已登记实验，包含历史终态；显示实验ID、项目ID、实验名、状态、阶段、GPU ID、暂停和队位 |
| ugs experiment list --project P | 只列该项目；也可加可重复的--status RUNNING、--status QUEUED等状态筛选 |
| ugs experiment show J --project P | 表格显示实验身份、状态、阶段和待应用修订；加--json读取完整冻结清单、证据与更新历史 |
| ugs experiment logs J --project P [--stage STAGE] [--tail 50] | 读取本项目阶段日志末尾；tail范围1..2000；不复制或搬迁日志 |
| ugs experiment events J --project P | 查询状态/阶段/暂停变化审计 |
| ugs experiment pause J --project P | 暂停等待中的实验；运行中的GPU进程不能用此命令暂停 |
| ugs experiment resume J --project P | 恢复等待中的实验；终态不能恢复 |
| ugs experiment remove J --project P | 撤回从未启动实验，记WITHDRAWN，不删除文件 |
| ugs experiment remove --all --project P | 撤回该项目全部从未启动实验，跳过已开始链 |
| ugs experiment stop J --project P | 强制取消指定已启动链及后代，记CANCELLED；不改队列开关或容量 |
| ugs experiment stop --all --project P | 取消该项目全部已启动未结束链；该项目排队任务仍可能补位 |
| ugs experiment stop --all | 取消全局已启动未结束链；仅用户/获授权管理会话使用；排队任务仍可能补位 |
| ugs experiment update J --project P --expected-sha SHA --file FILE | 原子更新从未启动清单，保留job_id、队位和历史 |
| ugs experiment amend J --project P --from-stage STAGE --expected-sha SHA --file FILE | 仅升级未开始的未来后缀；当前/完成前缀不变，阶段结束验收后应用；不改变冻结科学语义 |
| ugs experiment retry J --project P --file FILE | 显式新尝试，原实验须终态，必须新输出目录；新job进入队尾，保留原失败/取消记录 |
| ugs experiment gate J --project P --decision pass或fail --file FILE | 对WAITING_GATE提供本项目真实审核证据；不因训练退出码0自动宣称科学通过 |
| ugs resource status | 最近GPU观察、UUID分配及当前容量；历史观察不代替启动时复查 |
| ugs resource reserve IDS --reason TEXT | 登记已知预约避让，不占显存、不停止实验 |
| ugs resource reservations | 表格查看当前避让登记 |
| ugs resource release IDS | 解除避让，重新五分钟观察 |
| ugs maintenance status | 维护进程、阈值、大小及最后结果/错误 |
| ugs maintenance run --dry-run | 预览清理 |
| ugs maintenance run | 按阈值维护UGS日志 |
| ugs maintenance start / stop | 启停维护进程，不操作实验 |
| ugs maintenance set KEY=INTEGER | 修改全局日志保留阈值，具体键见后文 |

stop针对已开始链；对从未开始的队列任务使用remove。STOPPING仍占名额，后代/GPU上下文未清理不能当作CANCELLED。max_experiment误拼max_expriment直接报错，不兼容，不执行任何设置。

列表、详情、服务状态及资源表中的GPU ID为gpu07物理编号，根据实时nvidia-smi的index/UUID映射展示，保持分配UUID顺序；不固定或猜测历史编号。等待或已结束任务显示“-”，查询失败显示“?”，不能据此认定空闲。显示的是当前分配，启动准备期间可能尚未建立CUDA上下文；实际显存使用查看resource status。内部身份仍以UUID为准，--json保留原始结构与UUID，不将物理编号当成稳定身份。

admin是登录节点名称，不代表root身份。直接在admin执行运行或管理操作会被节点检查拒绝（只读帮助可查看）；服务与源码目录权限700、所有者kzhang19，普通其他账号不能直接进入。kzhang19从admin经SSH进入gpu07仍是同一UNIX账号，允许按授权操作，节点来源不是新的安全边界。UGS的项目归属核验防止误操作，不隔离同账号所有者，也不能限制真正的服务器root；全局stop只针对UGS登记的受管实验树，不按用户名杀全部进程。

internal serve/guardian是执行器内部入口，只允许服务托管路径调用，不是项目派发接口。不得直接运行它们、旧ugs_v1.py平面命令或旧项目调度器绕过队列。

## 服务本身的启停

仅停止协调服务而不丢失队列，要求没有活动名额：

    ugs scheduler stop
    ugs scheduler start
    ugs scheduler status
    ugs scheduler resume

如果正在运行实验，普通stop拒绝。要连实验一起停止并释放显存，再关闭UGS：

    ugs scheduler stop --stop-running

之后start仅启动协调器；resume才恢复排队派发。异常杀死协调器不是标准stop，重启时未知活动身份会进入恢复阻塞，不自动重派实验。

## 项目自助工作流

先阅读协议和本项目AGENTS，完成旧入口禁派发/已有进程对账，准备project注册JSON和标准CPU准入报告。自助register→validate→submit→list/show/logs即可，无需中央会话逐次accept。正式阶段引用真实接口证据；首次有界GPU接口或验证明确作用域，不把CPU测试冒称GPU验收。

训练、验证、门禁测试在一条有限链中自动接续；新候选/下一训练阶段/显式重试进入队尾。所有checkpoint、预测、指标、实验日志和退出证据仍保存各项目原约定目录。同UNIX账号的项目隔离是防误操作与规则边界，不是对账号所有者的安全沙箱。各项目agent只管理自己的项目；全局容量、暂停/紧急停止须用户或授权管理会话执行。

## 空闲观察与已知预约避让

resource status显示物理GPU ID、STATE、IDLE s、显存、利用率、健康、进程和受管job。WARMING表示正在积累共同五分钟记录，QUALIFIED表示当前观察资格；已分配/预约/配置阻塞仍可能阻止启动。PROCESS_PRESENT、BASELINE_UNREVIEWED、HEALTH_UNKNOWN_OR_BAD等为阻塞原因。GPU状态未知时不会猜为空闲。

预约来自明确告知或已有预约信息，UGS无法自动看出他人的未来计划。仅用户/授权管理会话登记已知需求：

    ugs resource reserve 1,2 --reason "已确认他人稍后使用"
    ugs resource reservations
    ugs resource release 1,2

编号为示例，也接受逗号分隔完整UUID。仅让UGS避让，不占显存、不拦截他人、不驱逐当前实验；已被UGS分配的卡拒绝新增预约。登记不会自动到期，release后重新五分钟观察。30秒轮询仍可能漏掉采样间短进程，最后复查不能保证他人未来不启动。

## UGS日志维护

用户授权的自动清理仅限UGS自身白名单日志，维护进程每30分钟运行一次，不清理项目产物、源码或有效验收文件。

| 项目 | 默认值 |
|---|---|
| resources.jsonl | 7天，最多64MiB |
| service-events.jsonl | 7天，最多4MiB |
| service.log | 超16MiB裁至约8MiB；7天无写入则清空 |
| events.jsonl | 超64MiB压缩，近期事件目标32MiB；完整当前队列和运行状态保留 |
| 专用压缩审计归档 | 30天，总计最多128MiB；超限优先删最旧 |

    ugs maintenance status
    ugs maintenance run --dry-run
    ugs maintenance run
    ugs maintenance start
    ugs maintenance stop
    ugs maintenance set archive_days=30
    ugs maintenance set archive_max_mib=128

set允许的键：interval_seconds、ordinary_days、resource_max_mib、service_max_mib、journal_trigger_mib、journal_target_mib、archive_days、archive_max_mib，值须正整数；interval_seconds范围60..86400，journal目标须小于触发阈值。interval修改在下一轮生效；需立即改变当前等待可stop再start维护。修改清理阈值属于全局管理操作，项目agent不自行修改。

maintenance stop仅停止日志维护，不停止协调器或实验；scheduler stop也不会停止独立维护进程。主机重启后使用scheduler start和maintenance start分别启动；未安装开机服务。status显示上次维护、错误、各日志大小；有旧error时按时间与后续成功last_run判断是否已恢复。

结构化日志使用共享锁、校验及原子替换，损坏时拒绝清理。审计压缩保留全部job/队位/manifest/幂等和PID/start/nonce/GPU身份；journal_epoch换段，本段revision从1开始，origin字段和checkpoint记录来源。旧详细审计会按归档保留规则删除；完整当前任务不会为了满足容量目标被删。阈值是周期维护目标，不是整个服务目录的硬配额。service.log采用保留inode的copytruncate，切换瞬间可能丢少量普通诊断文本，不能将其当唯一运行证据。
