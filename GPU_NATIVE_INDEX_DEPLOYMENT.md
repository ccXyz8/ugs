# 2026-11-12 原生 index 运行包与服务器旧代码清理

本文件替代旧文档中的 UUID 分配、连续空闲等待和服务器旧 source/candidates 执行入口说明。用户所说“不在本地保留旧代码”的本地指 gpu07 服务器，Mac 工作区保留。

## 当前部署

- 公共入口保持 `/public/home/kzhang19/kezhang/cc/global-gpu-scheduler/ugs`。
- 唯一现行运行包为同目录下 `runtime/931207b36c12/`，本仓库对应 `runtime/native_index_v1/`。内部版本化模块名沿用证据协议，不提供旧 verifier 回退。
- 2026-11-12 14:15 台北切换完成；调度器与日志维护服务均从该运行包启动。切换时没有 UGS 活动实验，没有重启实验。
- 每 30 秒检查，idle_seconds=0；最新完整有效观察满足整组资源表即可进入派发。35 秒陈旧上限、5 秒采集上限、启动两次相隔 2 秒复查、最终 2 秒快照、完整进程/健康/显存基线/明确预约审核不变。
- 新资源档使用 `gpu_indices: [2, 7]`（可省略具体卡限制），值为唯一整数 0–7。`gpu_uuids` 被拒绝。授权分配、policy inventory、reservation key、CUDA_VISIBLE_DEVICES 均为 index 字符串。
- 部分内部历史记录字段名仍叫 `uuid`/`gpu_uuid`，现行记录的值是 index，不进行 UUID 查询或兼容旧作业；终态历史原样保留。不要把字段名误当 UUID 兼容入口。
- 原生采集使用选择字段的 `nvidia-smi --query-gpu` 和 `nvidia-smi -q -d PIDS,ROW_REMAPPER`，包括图形进程；名称按 index/PCI/driver 变化更新。PCI 映射变化阻塞准入。无 CUDA 初始化、无持续原生追踪。
- SHA 缓存在单次校验范围内复用相同字节的摘要；每次仍读实际字节，超过 1 MiB 文件流式校验，缓存上限 8 MiB。不能用文件元数据代替内容核验。等待循环不反复校验全部发布文件，真正启动事务仍完整校验。

## 项目接入

只从 gpu07 的公共 `ugs` 使用 register / validate / submit / list / show 等接口，不直接调用内部服务脚本。现行 verifier PIN 文件：

`/public/home/kzhang19/kezhang/cc/global-gpu-scheduler/acceptance/standalone-index-v1-pins.json`

SHA256：`e415537a298accdae56cce0b3432fa42d3ed2d8ba568e647437d025014cc5b31`。

该文件含准确 `VERIFIER_SPEC`、`VERIFIER`、`IDLE_HELPER`、`PRELOAD_DEPENDENCY`、`MEMORY_HELPER`、`EXTRA_PINS`。CPU报告与阶段源码必须绑定实际最终文件；不再绑定已删除的旧 idle helper。七项真实CPU检查、10秒initial/20秒preload、结构化失败记录不变。证据检查名 `generation_uuid_rank` 暂沿用，内容验证 index 分配、rank 与 generation。旧 verifier 不兼容，禁止用旧报告跳过重新预检。

用户在项目会话已另行授权103–105对应保存点手动验证，因此不重复提交这些实验。106替代训练由该项目以新ID和独立产物目录完成预检提交。手动批次期间用户授权的 scheduler pause 保持，由项目批次结束时恢复。该例外不改变一般项目通过UGS提交的规则。

## 服务器清理与 GitHub 归档

归档分支 `codex/archive-legacy-20261112`，最终提交 `95bc9962d5f4fb5b0234c76b17549995a73640e4`。`server-legacy-20261112/CODE_MANIFEST.json` 包含243个实际服务器源码/入口SHA。确认远端提交后，按SHA白名单删除242个过时代码文件，保留唯一当前公共ugs入口（原版本已归档）。删除源码2,882,829字节。

服务器旧source/candidates中已无 `.py/.sh/.pyc`，保留其中验收JSON证据。旧终态作业、历史发布清单、日志审计、项目资产不删除。Mac源代码不清理。

证据位于服务 `acceptance/`：

- `standalone-index-v1-production.json`：SHA `3ed3c04d83dbd56a016a6df9107b2788c91c1b3f248aad2800a35a6ea9024a7c`。
- `server-old-code-cleanup.json`：SHA `7dc2c06a2630d69c7df74dd714815577d9d5c43014eec6b9ffdcfe64f93d99d7`。
- 运行包 `acceptance.json`：60项CPU检查通过，SHA `46e886010b04d4695582e37169fb47d1637ca06ce2a5d86636a488b624d63fb0`。后续展示模块去除UUID重复列单独记录在 `acceptance/index-cli-display.json`，不更改verifier PIN；发布files采用展示模块新SHA，原CPU证据不改写。
- 未授权独立调用verifier返回拒绝；当前20个服务模块全部来自独立包；清理后发布完整性、CLI、daemon和maintenance存活通过。

实测：旧全量XML约3.66–6.08秒；原生选择采集观测0.19–3.13秒，仍可能有瞬时波动，超时按原规则拒绝。清理后list 0.803秒、status 0.553秒、help 0.155秒；不是延迟保证。

日志保留：每1800秒维护，普通7天，资源64MiB、服务16MiB、journal触发64MiB/目标32MiB，归档30天且128MiB上限；只维护UGS白名单日志，不涉及项目输出。

14:20台北最终核验：106替代训练已注册为 `job-000107`，状态QUEUED，阶段为train4000→evalabsolute；手动验证批次期间全局暂停保持。daemon、maintenance、release_ok均为真；旧目录可执行源码计数0。证据 `acceptance/standalone-final-check.json` SHA `cbd28bf2d18868a900e8de21086022141c443e60f66201daefc5598f179fb045`。尚未进入真实GPU initial/preload，不以CPU验收或注册成功代替GPU实验成功。
