"""Chinese public UGS help; pure rendering with no runtime or GPU access."""
import json,sys
DATA=json.loads("{\"catalog\":{\"scheduler\":[[\"status\",\"\",\"查看协调器存活、并发上限、活动实验、队列、暂停和恢复阻塞状态。\",[],\"draining表示当前活动数暂时高于新上限，已有实验正常完成。\"],[\"get\",\"\",\"查询与status相同的持久调度状态；脚本可加--json。\",[],\"\"],[\"set\",\"max_experiment=N\",\"热调整最多并行的不同实验链数量，默认1；不会重启服务。\",[],\"N为非负整数。0只禁止新实验链准入，不杀进程；调低正数让已启动有限链完成，停止补充名额。\"],[\"pause\",\"[--stop-running]\",\"暂停后续阶段派发；加--stop-running同时终止全部已启动受管实验链。\",[\"--stop-running\"],\"普通pause保留已承诺启动意图和活动进程；紧急阻塞并释放显存用pause --stop-running，UGS继续运行。\"],[\"resume\",\"\",\"恢复派发，不改并发上限，不重启失败、取消或已完成实验。\",[],\"如果容量为0还需另设正数；服务已停止时先start。\"],[\"start\",\"\",\"启动唯一后台协调器；已运行则返回原进程。\",[],\"保留暂停和容量设置。start不等于resume；日志维护是独立进程。\"],[\"stop\",\"[--stop-running]\",\"停止协调器并保持派发暂停；普通stop在有活动名额时拒绝。\",[\"--stop-running\"],\"加--stop-running先终止已启动链，确认后代及GPU上下文收口后才退出；未收口则继续处理STOPPING。\"],[\"recover\",\"\",\"核实旧活动身份已全部收口后清除恢复阻塞。\",[],\"仍有活动身份则拒绝，不重新运行实验，不强制释放未知进程。\"]],\"project\":[[\"register\",\"--file FILE\",\"按项目注册JSON登记或更新项目、解释器、合法输出范围与迁移证据。\",[\"--file\"],\"固定项目根和解释器身份不能暗改；旧调度入口须已停止新派发、现有任务完成对账。\"],[\"list\",\"\",\"查看全部已登记项目及project_id。\",[],\"\"],[\"show\",\"P\",\"查看指定项目的登记详情。\",[],\"P是project_id，不是实验job_id。\"],[\"pause\",\"P\",\"暂停该项目后续阶段派发，已运行阶段继续。\",[],\"不释放正在使用的显存；需要终止并暂停该项目用project stop。\"],[\"resume\",\"P\",\"恢复该项目后续派发，仍受全局暂停和容量限制。\",[],\"不会重启终态实验。\"],[\"stop\",\"P\",\"暂停该项目后续派发，并终止其已启动未结束实验链。\",[],\"不影响其他项目和全局max_experiment；保留队列和项目产物。\"],[\"unregister\",\"P\",\"仅在该项目无未结束实验时注销登记。\",[],\"保留历史和文件，不删除项目目录。\"]],\"experiment\":[[\"validate\",\"--file FILE\",\"校验完整实验清单、代码和证据SHA及准入合同，不排队、不加载模型。\",[\"--file\"],\"从stage0验证全链。只升级活动实验未来后缀时，使用amend内部的from-stage校验，不补造旧阶段报告。\"],[\"submit\",\"--file FILE\",\"提交有限实验链，项目内FIFO、项目间轮转；同幂等键同内容不重复提交。\",[\"--file\"],\"manifest顶层runtime_release=ugs-exec-v3.1；训练后自动接验证及满足真实门禁的测试。产物留项目目录。\"],[\"list\",\"[--project P] [--status STATUS ...]\",\"列出全部已登记实验，包括历史终态；显示job_id、项目、阶段、物理GPU ID和队位。\",[\"--project\",\"--status\"],\"不指定项目查看全局摘要；可重复--status筛选多个状态，状态之间为或关系。\"],[\"show\",\"J --project P\",\"查看实验身份、当前阶段、分配GPU及待应用修订。\",[\"--project\"],\"加--json可读取完整manifest、manifest_sha256、证据与更新历史。\"],[\"logs\",\"J --project P [--stage STAGE] [--tail N]\",\"读取本项目指定阶段日志末尾。\",[\"--project\",\"--stage\",\"--tail\"],\"默认当前阶段；阶段索引超过末尾则取最后阶段。默认50行，范围1..2000。\"],[\"events\",\"J --project P\",\"查看该实验状态、阶段和暂停变化的审计记录。\",[\"--project\"],\"显示当前保留的审计日志，不保证包含已按保留策略归档或删除的所有旧细节。\"],[\"pause\",\"J --project P\",\"暂停等待中的本项目实验，保留队列位置。\",[\"--project\"],\"不能用SIGSTOP暂停活动GPU进程；运行中的实验需要释放显存请用stop。\"],[\"resume\",\"J --project P\",\"恢复等待中的本项目实验；配置阻塞任务重新检查准入。\",[\"--project\"],\"终态实验不能恢复；失败、被kill或取消均不自动重启。\"],[\"remove\",\"J --project P  或  --all --project P\",\"撤回本项目从未正式启动的实验，状态记WITHDRAWN。\",[\"--project\",\"--all\"],\"批量操作跳过已开始链。保留文件与审计；已开始实验用stop。\"],[\"stop\",\"J --project P  或  --all [--project P]\",\"终止指定或批量已启动实验链及其核实后代。\",[\"--project\",\"--all\"],\"先记STOPPING，确认后代和GPU上下文收口后记CANCELLED。不会阻塞队列，其他实验可能补位；全局紧急停止用scheduler pause --stop-running。\"],[\"update\",\"J --project P --expected-sha SHA --file FILE\",\"原子更新从未启动的实验清单，保留job_id、队位、run_id和历史。\",[\"--project\",\"--expected-sha\",\"--file\"],\"SHA取show --json的当前manifest_sha256，失配拒绝；不能修改已运行阶段。\"],[\"amend\",\"J --project P --from-stage STAGE --expected-sha SHA --file FILE\",\"只更新未开始的未来阶段后缀；当前阶段完成收口后应用。\",[\"--project\",\"--from-stage\",\"--expected-sha\",\"--file\"],\"FILE仍是完整新清单；已启动/完成前缀及冻结科学预算、资源语义、输出不变。SHA取当前清单，不取pending修订。\"],[\"retry\",\"J --project P --file FILE\",\"显式提交终态实验的新尝试，保留原记录，新job进入项目队尾。\",[\"--project\",\"--file\"],\"必须独立新清单及新输出目录；这不是自动重试或原进程恢复。\"],[\"gate\",\"J --project P --decision pass|fail --file FILE\",\"依据本项目真实证据处理WAITING_GATE人工门禁。\",[\"--project\",\"--decision\",\"--file\"],\"pass进入接续等待，fail记GATE_REJECTED；不能用退出码0代替真实科学验收。\"]],\"resource\":[[\"status\",\"\",\"查看GPU观察、物理编号、UUID、显存、利用率、进程、健康和空闲资格。\",[],\"WARMING积累共同五分钟；QUALIFIED仅表示观察合格，还受容量/预约等限制；未知与异常不准入。\"],[\"reserve\",\"IDS --reason \\\"原因\\\"\",\"登记明确已知的未来用卡需求，让UGS避开指定GPU。\",[\"--reason\"],\"IDS为逗号分隔物理编号或完整UUID。不占显存、不拦截别人、不停止实验；UGS已分配的GPU拒绝预约，不自动到期。\"],[\"reservations\",\"\",\"查看所有尚未解除的预约避让登记。\",[],\"预约来源是明确告知/已知计划，UGS无法推测他人的未来意图。\"],[\"release\",\"IDS\",\"解除指定GPU的预约避让，重新积累五分钟空闲观察。\",[],\"不释放任何运行进程的显存；停止实验请用experiment stop。\"]],\"maintenance\":[[\"status\",\"\",\"查看独立日志维护进程、阈值、各日志大小、上次运行及错误。\",[],\"维护只处理UGS白名单日志，保留项目产物、源码、有效验收和完整当前任务状态。\"],[\"run\",\"[--dry-run]\",\"立即按保留规则维护UGS日志；--dry-run只预览处理范围。\",[\"--dry-run\"],\"预览不删除或压缩日志。只有本操作支持--dry-run；不能给stop等命令加此参数。\"],[\"start\",\"\",\"启动独立后台日志维护进程；已运行则复用。\",[],\"默认每1800秒检查；不启动实验或协调器，未安装开机自动启动服务。\"],[\"stop\",\"\",\"停止日志维护进程。\",[],\"不会停止协调器或实验；scheduler stop也不会停止独立维护。\"],[\"set\",\"KEY=INTEGER\",\"修改日志保留时间、容量或维护周期。\",[],\"只接受列出的键和正整数；周期60..86400秒，审计目标必须小于触发阈值。周期修改在下一轮生效，立即改变等待可stop后start维护。\"]]},\"params\":{\"-h, --help\":\"查看中文帮助，不执行所查询操作；支持总览、对象和具体指令三级帮助。\",\"--json\":\"正常指令返回完整机器可读JSON，默认输出表格，logs默认原文；帮助加--json返回结构化说明。\",\"--file FILE\":\"读取服务器上本项目文件的绝对路径：register用项目注册JSON；validate/submit/update/amend/retry用实验清单JSON；gate用真实审核证据。\",\"--project P\":\"project_id；单实验查询/管理必须显式提供且匹配归属。list可选用于筛选；stop --all省略它表示全局范围。\",\"--expected-sha SHA\":\"update/amend必填，须等于show --json中的当前manifest_sha256；并发更新失配即拒绝，不用pending SHA。\",\"--all\":\"只用于experiment stop/remove，且不能同时指定J。remove --all必须带--project；stop --all可限定项目或全局。\",\"--stop-running\":\"只用于scheduler pause/stop；暂停派发并终止已启动链。pause保留UGS服务，stop在资源收口后退出服务。\",\"--status STATUS\":\"仅用于experiment list，可重复；必须使用精确大写状态名。\",\"--from-stage STAGE\":\"仅用于amend，指定要替换的第一个未开始stage_id；当前和已完成阶段不可改。\",\"--stage STAGE\":\"仅用于experiment logs，指定日志所属stage_id；不传则取当前/最后阶段。\",\"--tail N\":\"仅用于experiment logs；末尾行数，默认50，允许1..2000。\",\"--decision pass|fail\":\"仅用于experiment gate，真实门禁通过或拒绝；须同时提供本项目--file证据。\",\"--reason TEXT\":\"resource reserve必填的预约原因；含空格的中文说明请加引号。\",\"--dry-run\":\"仅用于maintenance run，预览清理范围，不执行删除或压缩。\"},\"maintenanceKeys\":[[\"interval_seconds\",\"1800\",\"每轮维护间隔，单位秒；60..86400。\"],[\"ordinary_days\",\"7\",\"GPU观察/服务事件保留天数；普通诊断连续这么多天不写入则清空。\"],[\"resource_max_mib\",\"64\",\"resources.jsonl容量上限，优先保留最新完整记录。\"],[\"service_max_mib\",\"16\",\"service.log超过此阈值裁至约一半；MiB。\"],[\"journal_trigger_mib\",\"64\",\"状态审计日志超过此值触发压缩及全状态checkpoint；MiB。\"],[\"journal_target_mib\",\"32\",\"压缩后近期原始事件目标大小；必须小于触发阈值，完整当前状态不会丢弃。\"],[\"archive_days\",\"30\",\"专用压缩审计归档保留天数。\"],[\"archive_max_mib\",\"128\",\"专用压缩审计归档总容量上限；超过时先删最旧。\"]]}")
TITLES={"scheduler":"调度与服务","project":"项目管理","experiment":"实验管理","resource":"GPU观察与预约避让","maintenance":"日志维护"}
COMMON=[
["查看所有实验及GPU","ugs experiment list"],
["查看服务/空闲卡原因","ugs scheduler status  /  ugs resource status（两条独立命令）"],
["紧急停止并阻塞后续","ugs scheduler pause --stop-running"],
["恢复后续派发","ugs scheduler resume"],
["设置最多3个实验并行","ugs scheduler set max_experiment=3"],
["停止本项目某个实验","ugs experiment stop J --project P"],
["撤回未启动实验","ugs experiment remove J --project P"],
["暂停并终止整个项目","ugs project stop P"],
["服务重新启用","依次执行ugs scheduler start、ugs scheduler resume"],
["启动日志维护","ugs maintenance start"],
["查看/预览日志清理","ugs maintenance status；ugs maintenance run --dry-run（分开执行）"],
["项目自助提交","依次执行project register、experiment validate、experiment submit（各带--file FILE）"]
]
NOTES=[
"J是UGS分配的job_id（如job-000004）；P是project_id；STAGE是清单中的stage_id。",
"FILE是服务器上本项目文件的绝对路径；SHA是当前manifest_sha256；IDS如1,2或完整GPU UUID。",
"命令语法里的[]表示可选，|或“或”表示择一；不要把这些符号、J/P/FILE等占位符原样执行。",
"max_experiment默认1；0仅禁止新实验链准入，不杀进程。实验失败、kill、取消后不会自动重启。",
"普通pause不释放当前显存；stop --all不阻塞后续排队。紧急停止并阻塞用scheduler pause --stop-running。",
"STOPPING不等于显存已释放；须核实后代退出和GPU上下文消失，之后才是CANCELLED。",
"新实验只经既有SSH进入gpu07/kzhang19执行；无Slurm、无新增端口。帮助本身不查GPU、不改状态。",
"项目agent只管理本项目。全局容量/暂停/停止、预约和日志策略由用户或授权管理会话操作。",
"实验产物继续留在各项目目录。维护阈值不是整个服务目录硬配额；当前任务及有效验收证据不清理。",
"内部serve/guardian及--nonce仅供执行器使用，不是项目调用接口。"
]
def show(args,table,as_json=False):
    words=list(args)
    if words and words[0]=="help":words=words[1:]
    words=[x for x in words if x not in ("-h","--help","--json")]
    obj=words[0] if words else None
    action=words[1] if len(words)>1 else None
    if obj is not None and obj not in DATA["catalog"]:
        print("ugs：未知帮助对象。可选：scheduler、project、experiment、resource、maintenance。",file=sys.stderr);return 2
    entries=DATA["catalog"] if obj is None else {obj:DATA["catalog"][obj]}
    if action:
        selected=[x for x in entries[obj] if x[0]==action]
        if not selected:
            print("ugs：未知指令；请使用ugs "+obj+" --help查看可用操作。",file=sys.stderr);return 2
        entries={obj:selected}
    used={"-h, --help","--json"}
    for rows in entries.values():
        for row in rows:used.update(row[3])
    selected_params=DATA["params"] if obj is None else {k:v for k,v in DATA["params"].items() if k in used}
    payload={"title":"UGS统一GPU实验管理器：中文帮助","scope":" ".join(x for x in (obj,action) if x) or "全部",
             "commands":entries,"parameters":selected_params,"common":COMMON if obj is None else [],
             "notes":NOTES,"maintenance_settings":DATA["maintenanceKeys"] if obj in (None,"maintenance") else []}
    if as_json:print(json.dumps(payload,ensure_ascii=False,indent=2));return 0
    print(payload["title"])
    print("用法：ugs <对象> <操作> [目标] [参数]")
    print("分层查看：ugs --help；ugs experiment --help；ugs experiment stop --help")
    print("别名形式：ugs help；ugs help experiment stop。默认表格；脚本用--json。")
    if obj is None:
        print("\n常用功能")
        table(["用途","指令或执行顺序"],COMMON)
    for group,rows in entries.items():
        print("\n"+TITLES[group]+"（"+group+"）")
        table(["完整指令","具体功能与行为"],[
            ["ugs "+group+" "+row[0]+(" "+row[1] if row[1] else ""),row[2]+(" "+row[4] if row[4] else "")]
            for row in rows])
    print("\n参数说明")
    table(["参数","含义、适用范围与默认值"],list(selected_params.items()))
    if obj in (None,"maintenance"):
        print("\n日志维护可设置项：ugs maintenance set KEY=INTEGER")
        table(["KEY","默认值","具体功能"],DATA["maintenanceKeys"])
        print("service-events.jsonl另有固定4MiB上限；普通日志按时间和容量优先保留最新记录。")
    if obj in (None,"experiment") and (action is None or action=="list"):
        print("\n状态筛选：QUEUED排队、STARTING启动准备、RUNNING运行、STOPPING正在收口、")
        print("WAITING_RESOURCES资源等待、WAITING_CONTINUATION接续等待、WAITING_GATE人工门禁、")
        print("BLOCKED配置阻塞、SUCCEEDED完成、FAILED失败、CANCELLED取消、WITHDRAWN撤回、GATE_REJECTED门禁拒绝。")
    print("\n使用约定")
    for note in NOTES:print("  - "+note)
    print("\n完整接入说明：/public/home/kzhang19/kezhang/cc/global-gpu-scheduler/USAGE.md")
    return 0
