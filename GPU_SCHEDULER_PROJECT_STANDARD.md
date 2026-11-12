# UGS项目标准操作（2026-11-02）

> 2026-11-12现行部署：服务器旧source/candidates执行代码已归档删除；只用公共ugs入口，原生GPU index、每30秒监测、无空闲等待、单一新verifier PIN。详见[原生index部署](GPU_NATIVE_INDEX_DEPLOYMENT.md)。本说明替代旧路径/UUID/旧verifier兼容条款。


> 2026-11-12最新规则：每30秒监测，取消连续空闲等待和累计采样点；整组资源满足即可进入派发，进程/健康/基线/分配/明确预约及启动前双复查保持。旧verifier未迁移阶段明确阻塞。见[即时准入部署](GPU_CURRENT_ADMISSION_DEPLOYMENT.md)。

## 生效范围

2026-11-02 23:38:57（台北）已完成生产切换：repair-v1的replace、replacement、queue、自动update和dry-run已通过现有ugs入口启用。不得自行切换入口或调用内部serve/guardian。原核心/verifier/实验PIN及GPU规则不变。

所有项目只使用既有SSH进入gpu07/kzhang19；不得使用Slurm、额外端口或旧项目调度器旁路执行。项目agent只管理本项目；全局暂停/恢复、并发及替换由用户或授权管理会话操作。队列顺序变更必须有用户明确指令，不自行插队。

## 生产标准流程

1. 固定独立清单、源码/PIN、输入输出、资源档及门禁；七项CPU预检通过，使用即时准入部署中统一已验收verifier（沿用v3.2许可合同，initial10秒/preload20秒）。不得热改旧job或冻结模板。
2. `ugs experiment validate --file /本项目/清单.json`，通过后`ugs experiment submit --file /本项目/清单.json`。断线后先用同清单幂等查询/提交，不创建新版本猜测性重试。
3. `ugs experiment list --project PROJECT`（默认最新15个，--page从1开始）；`ugs experiment show JOB --project PROJECT`检查状态与失败证据；`ugs resource status`仅查询，不自行预约或修改空闲规则。
4. 从未启动的清单修复：`ugs experiment update JOB --project PROJECT --file FILE [--expected-sha SHA]`。已开始链的update自动选择未执行后缀；也可继续用`amend JOB --from-stage STAGE --expected-sha SHA --file FILE`显式指定后缀；不得改变冻结科学/资源语义。新报告绑定最终源码与清单。
5. 不再需要且从未启动：`ugs experiment remove JOB --project PROJECT`。已启动需终止：`ugs experiment stop JOB --project PROJECT`。STOPPING不代表已释放，核实终态、完整后代及GPU收口；失败/取消不自动重试。显式retry创建新job、新输出并进入项目尾部，不保留原队位。
6. 项目需要阻止自身后续启动可用`ugs project pause PROJECT`，恢复用project resume；当前阶段继续，后续验证/测试等待。暂停仍允许入队；普通pause保留此前已授予的STARTING许可。
7. 全局紧急停止仅经用户授权使用`ugs scheduler pause --stop-running`。单独experiment stop不阻止其他job补位；max_experiment=0不杀进程。恢复不复活失败/取消实验。

实验结果、checkpoint、评分及日志始终保存本项目约定目录。GPU仍须无用户进程、通过健康检查、最新一次完整有效观察及启动前两次复查，无连续空闲等待；低显存不能覆盖进程判定。显存范围与回退按[显存策略部署](GPU_MEMORY_ALLOWANCE_DEPLOYMENT.md)执行：仅有效审定4MiB基线的卡允许4–7MiB；收口无进程>7则全局锁定回精确4MiB并使旧观察失效。

## 诊断器修复与禁止事项

已确认diagnostic-v1持续ltrace模式只有写盘限额，没有运行开销上限，会持续拦截高频CUDA调用。禁止在未来正常GPU实验继续启用该模式；不允许用strace替代来绕过限制。

新的ugs_diagnostic_runner_v2.py仅允许显式trace-tool=none；请求其他模式立即拒绝，不创建worker、不写诊断文件。保留UTC/monotonic阶段时间、身份、普通stdout/stderr、真实退出码和信号，native证据文件为0字节，不声称记录了CUDA调用。未来新模板可原样vendor新版本并重新固定PIN/CPU验收；原v1+none既有job不需要迁移，不得热改已注册源码。job34/35/36的清单与顺序不因本通知改变。

未实现并验收时间/事件开销预算前，不恢复原生跟踪。不得单杀ltrace、在线detach或SIGCONT尝试修复现有受管实验；由用户授权通过UGS整链终止并核实收口。

## 已上线的统一修复与队列用法

- `experiment update JOB --file FILE [--dry-run]`自动选择未执行部分；不会暂停全局。
- `experiment replace JOB --file FILE [--dry-run]`预检通过后原子暂停全局派发并停止目标，收口后新job继承原项目内队位。其他运行阶段继续，替换后保持暂停。全局操作须授权。
- `experiment replacement JOB`查询；加`--cancel`撤销未完成请求，不复活旧job。未完成请求阻止resume。
- `queue list --project PROJECT`按项目实际队位显示；不替代experiment list历史分页，也不代表GPU已合格。
- `queue move JOB --before OTHER --reason TEXT [--dry-run]`或--after：只允许同项目非活动非终态job，显式人工重排默认FIFO；其他项目轮转不变。可用--expected-revision防旧快照覆盖。替换进行中禁止重排。
- dry-run无状态写入、无停止、无队位/资源预留；正式执行重新检查。

项目接手时将本标准写入各自AGENTS.md实验规则。生产证据：acceptance/repair-v1-production.json，SHA256=5adfab62901c85789858166e0ac5df68d5a5219cfada31365cbd0ecf4ab8207b。23项CPU测试、7项线上查询/帮助及不落盘预览检查通过；未通过替换真实科学实验来做测试。

- 2026-11-03用户将GPU连续空闲窗口调整为120秒、至少5点；旧PIN未迁移阶段保留300秒防止验证器不一致，新接口和当前部署见[两分钟窗口部署](GPU_IDLE_WINDOW_DEPLOYMENT.md)。30秒扫描、35秒漏采失效及启动两次相隔2秒复查保持。

- 2026-11-05已上线受控后缀资源变更：普通update仍冻结资源；明确用户授权登记后才可使用 `experiment update --resource-authorization`，仅无运行阶段的未开始后缀，保留身份/前缀/科学/输出/队位。gpu_uuids限制实际分配，目标卡不合格则等待。见[资源变更部署](GPU_RESOURCE_OVERRIDE_DEPLOYMENT.md)。
