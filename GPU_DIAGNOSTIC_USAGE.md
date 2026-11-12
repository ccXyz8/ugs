# 下一次正常实验的显存诊断

## 2026-11-02 使用限制更正（优先于下文原生跟踪示例）

诊断v1持续`ltrace`模式不适合长时GPU训练、验证或测试，停止将该模式接入后续新实验。4MiB是写盘上限，不是跟踪次数或运行开销上限：饱和后仍排空管道，ltrace仍持续拦截调用；`cudaGetDevice`等高频API可能使推理严重变慢。原CPU假库验收只证明功能与文件限额，未验收长时、多线程、高频调用的生产性能。原验收及源码PIN保留为历史证据，不代表该模式可继续用于生产长时实验。

未来新实验保留内部`phase`标记，显式使用`--trace-tool none`，重新冻结清单并通过CPU准入；这不提供原生CUDA调用证据。不得以`strace`替换作为已验收的低开销方案。下文ltrace命令仅保留解释原接口，不作为当前推荐调用。

不得热改已注册实验/PIN，或单独杀tracer、关闭管道、向worker发送SIGCONT来尝试在线脱离跟踪。当前版本没有已验收的安全detach接口。经用户授权终止时使用`ugs experiment stop JOB_ID --project PROJECT`，由guardian完成整棵受管进程树及GPU资源收口；不自动重试、不重启全局服务。重新提交须另有授权、新清单/输出目录和重新验收。

后继原生跟踪方案必须单独验收实际时间/事件预算及预算耗尽后的安全行为、真实退出码、guardian取消收口、高频多线程负载与相对无跟踪开销。仅缩短符号列表或截断日志不构成运行开销上限。该后继实现尚未完成，当前未改动已部署runner。

本功能仅供未来新提交的有限实验链。已有注册job、运行阶段、PIN和服务不修改，不为诊断新建或重跑GPU实验。Time-R1项目当前暂停实现时，只交付接口，不自动恢复其任务。

将 `source/ugs_diagnostic_runner.py` 原样复制到新项目模板source，加入项目源码SHA与完整阶段CPU预检。不得从运行中模板原地修改或替换同名文件。无需修改中央服务或新增端口。

## 标记实际调用边界

项目adapter在CPU准备阶段设置显式目录（已存在、绝对路径、无符号链接）：

```python
from ugs_diagnostic_runner import configure_diagnostics, mark, phase
configure_diagnostics(DIAGNOSTIC_DIR, EVENTS_PATH)
```

所有阶段共用同一条链的 `EVENTS_PATH`。子进程继承 `UGS_DIAG_PROJECT_DIR`、`UGS_DIAG_EVENTS_FILE`；保留原UGS身份、rank、UUID环境，不覆盖授权值。

在未来的rank入口加入标记，不能把外层wrapper时间当作实际CUDA初始化时间：

```python
with phase("train.torch_import"):
    import torch
with phase("train.set_device"):
    torch.cuda.set_device(rank)
```

控制器用 `with phase("train.verify_artifacts")` 包裹原 `verify_train` 调用。导出入口在torch导入、DCP加载、比较/写出前后分别标记；export50/export100使用不同名称。验证和测试入口同样标记torch导入、模型加载、实际验证/测试及结果保存。只在粗粒度边界记录，不逐batch写标记。异常写end并保留异常类型；进程被SIGKILL时不伪造end。

记录包含UTC纳秒、monotonic_ns、PID、UGS job/run/stage/generation/nonce/manifest、rank和CUDA_VISIBLE_DEVICES。时间字段与UGS resources.jsonl按节点墙钟对齐，并检查monotonic间隔；不用共享文件系统mtime推断调用时刻。

## 原生API跟踪

在既有adapter的worker子命令位置使用CLI包装，原Popen的env/cwd/stdout/stderr保持项目约定。initial/preload仍由原adapter调用，禁止把verifier放入跟踪范围。准备目录及源码校验放在preload前，许可后立即创建受管worker。

```text
python -B /项目/新模板/source/ugs_diagnostic_runner.py \
  --project-dir /项目/本实验/output/diagnostic \
  --events-file /项目/本实验/output/diagnostic/events.jsonl \
  --trace-log /项目/本实验/output/diagnostic/train.native.log \
  --trace-tool ltrace --phase train-worker -- 原始命令 原始参数
```

train、export50、export100、eval50、eval100及门禁测试使用预先列明的不同trace文件，最多8次。不得嵌套跟踪同一子树。原实验stdout/stderr继承原日志，原生API输出走独立管道。GPU worker与导出仍是guardian受管后代；异常、kill不自动重试。ltrace本身退出码不等于worker退出码，runner通过独立退出状态管道记录实际worker结果，缺失证据返回125，不能误报成功。

ltrace跟踪精选libcuda/libcudart初始化、上下文、分配/释放函数；包含`-x`以捕获动态符号入口。不启用CUPTI，不主动调用CUDA探针，不reset GPU。strace模式只提供系统调用证据，不能当作CUDA API跟踪成功；none模式明确禁用原生跟踪。符号缺失、静态链接、ptrace限制可能造成漏采；空trace不证明没有CUDA访问。指针/参数展示受ltrace原型信息限制，不把未解码参数当作精确分配大小。

## 大小与验收

- 每个trace严格最多4MiB；超限写饱和标记，继续排空丢弃新增数据，记录丢弃字节数，不用SIGPIPE中断worker。
- 同job共用events，最多8次native trace，合计最多32MiB；events最多64KiB、128行、16个PID、每PID32条。固定文件清单与共享events路径由新清单CPU预检核实，不能换events文件绕过预算。
- 文件已存在、符号链接或配置错误拒绝开始该诊断调用，不覆盖、不删除项目文件。启动后的日志错误明确告警；不以tracer返回0冒充worker成功。
- 仅下一条链启用；不自动累积到所有后续实验，不扩大UGS维护器删除范围。项目历史诊断保留/删除按项目授权执行。
- 新模板须验收真实命令解析、guardian后代跟踪、退出码、日志分流、时间标记、有限文件预算及preload时限。CPU假库验收不能冒称生产GPU追踪已经验证。

本地测试：`python -B -m unittest discover -s source -p 'test_diagnostic*.py' -v`。Linux原生测试使用临时假libcuda.so.1，调用只返回0，不接触真实GPU。

## 诊断v2修复版

新文件`source/ugs_diagnostic_runner_v2.py`彻底移除tracer/proxy启动路径，只保留直接worker及阶段事件。run/CLI仅接受显式trace-tool=none；ltrace/strace等请求在路径处理及创建worker前抛出NATIVE_TRACE_DISABLED，不静默降级。API参数与标记接口保留，native文件为0字节，真实退出码及信号透传。未来新模板重新vendor/PIN/验收；不替换已登记job的旧文件。旧发布manifest记录的是当时快照，不改写历史SHA。
