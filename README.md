# UGS：统一 GPU 实验调度器

> 2026-11-12现行部署：服务器旧source/candidates执行代码已归档删除；只用公共ugs入口，原生GPU index、每30秒监测、无空闲等待、单一新verifier PIN。详见[原生index部署](GPU_NATIVE_INDEX_DEPLOYMENT.md)。本说明替代旧路径/UUID/旧verifier兼容条款。


UGS 将多个项目的训练、验证、测试及模型加载纳入统一队列，通过既有 SSH 在计算节点运行。默认同时执行一个有限实验链，可热调整并发容量；项目间轮转、项目内 FIFO。

本仓库保存当前已验收的 `ugs-exec-v3.1` 源码快照、中文帮助、协议和测试源码。初始快照日期：2026-10-31。每份服务器原件的 SHA256 见 [source-manifest.json](source-manifest.json)。

## 主要功能

- 每30秒扫描 GPU，不累计空闲等待；最新完整有效观察满足整组资源表才进入启动复查。
- 启动前及 CUDA 加载前复查；同一服务内按 GPU index 防止重复分配。
- 多项目自助注册、提交、查询、暂停、撤回和停止；失败、kill、取消不自动重启。
- 有限训练→验证→门禁测试链；实验产物留在各自项目目录。
- 已知预约避让、中文表格、物理 GPU 编号、三级中文帮助。
- 独立日志维护，按保留时间和容量清理 UGS 日志，保留当前队列和运行身份。

轮询无法预测其他使用者的未来计划，也不能提供跨用户硬件独占。预约来自明确已知的需求，不是自动推测。

## 中文帮助与常用指令

在已经部署 UGS 的计算节点使用：

```bash
ugs --help
ugs experiment --help
ugs experiment stop --help
ugs maintenance set --help

ugs scheduler status
ugs experiment list
ugs resource status
ugs maintenance status
```

| 目的 | 指令 |
|---|---|
| 设置最多3个实验并行 | `ugs scheduler set max_experiment=3` |
| 禁止新实验链准入，不杀当前实验 | `ugs scheduler set max_experiment=0` |
| 紧急终止受管实验并阻塞后续派发 | `ugs scheduler pause --stop-running` |
| 恢复派发 | `ugs scheduler resume` |
| 停止指定已启动实验 | `ugs experiment stop J` |
| 撤回从未启动实验 | `ugs experiment remove J` |
| 预览日志维护 | `ugs maintenance run --dry-run` |

J/P须替换为真实job_id/project_id。默认表格，机器解析用 `--json`。恢复派发不复活终态实验；`experiment stop --all` 本身不会阻塞其他队列补位。

## 文档

- [最新控制与显存收口规则](GPU_SCHEDULER_CONTROL_POLICY.md)
- [冻结阶段调度协议](GPU_SCHEDULER_PROTOCOL.md)
- [完整中文使用说明](GPU_SCHEDULER_USAGE.md)
- [项目适配与准入合同](GPU_SCHEDULER_ADAPTER_CONTRACT.md)
- [部署范围与源码快照说明](GPU_SCHEDULER_DEPLOYMENT.md)
- [仓库协作约束](AGENTS.md)

## 源码布局

| 文件 | 用途 |
|---|---|
| `ugs` | 当前服务器部署的命令入口快照 |
| `source/ugs_control_v2.py` | 当前运行控制、job-id调用与严格资源收口 |
| `source/ugs_exec_v3_1.py` | 冻结的阶段接口、队列与GPU准入核心 |
| `source/verify_launch_v3_1.py` | initial/preload启动许可 |
| `source/ugs_cli_control_v2.py` | 表格显示及CLI分发 |
| `source/ugs_help_zh_v3.py` | 中文指令、参数及常用功能 |
| `source/ugs_maintenance_v1_1.py` | 日志保留、压缩与维护进程 |
| `source/test_control_v2.py` | 当前48项服务器CPU验收测试 |
| `source/test_executor_v3_1_final.py` | 初始41项服务器CPU测试源码 |
| `source/test_maintenance_v1_1.py` | 已验收的12项维护测试源码 |
| `source/ugs_exec_v2_3.py`、`source/ugs_exec_v3.py`及对应verifier | 历史兼容验证参考，不是当前部署入口 |

## 运行范围

这是已有 Linux 服务器部署的源码快照，含固定节点、账号、解释器与服务目录约束；并非 `git clone` 后即可在任意机器启动的通用安装包。当前代码读取Linux /proc，不能直接在macOS运行执行器。根目录 `ugs` 指向服务器的已部署路径。

测试源码使用服务器上的独立fixture目录。测试重跑会生成留存证据并创建CPU测试进程，需在对应环境按协议执行；本次Git初始化只核对源码SHA和语法，没有重跑生产实验。全新部署还需登记节点身份、逐卡基线、项目迁移与发布验收，不能仅复制Git文件宣称已取得统一调度控制。

代码提交与生产部署分别管理；推送仓库不会更新、重启或改变已部署服务。

当前控制扩展为ugs-control-v2（source/ugs_control_v2.py），已通过48项服务器CPU验收并部署。单实验--project可选，实验级pause/resume删除；scheduler/project pause保留。source-manifest.json仍记录初始快照，当前新增/变更文件见releases/control-v2-source-manifest.json。旧版本源码保留追溯，不作为旁路入口。
