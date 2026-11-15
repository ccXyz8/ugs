# 2026-11-15 原路径复用

用户已明确允许原项目文件复用和覆盖，不再要求两次实验使用不同文件路径。本次为 STReasoner-more 上线显式复用合同。项目科学产物、日志、预测、指标、checkpoint 及 UGS 结果/失败回执均可使用原路径；不要求新建 exp_N 或按 job 建输出目录，也不要求新建 .ugs/<attempt>。

## 使用

项目注册根固定 `/public/home/kzhang19/kezhang/cc/STReasoner-more`，推荐 project_id `streasoner-more`。清单增加：

```json
"artifact_policy": {
  "mode": "reuse",
  "root": "/public/home/kzhang19/kezhang/cc/STReasoner-more/evaluation/exp_1"
}
```

`stages` 可包含 train→validation→test，所有阶段的 output_dir/evaluation_dir 都可为同一个 exp_1；各阶段日志/结果也可顺序复用相同文件。只补跑验证时，新清单仅包含 validation，仍用原 exp_1。`submission_version` 每个新尝试用新的非空安全名称，如 `rerun2`；重复提交完全相同清单仍幂等。

```bash
ugs experiment validate --file /本项目/manifest.json
ugs experiment submit --file /本项目/manifest.json
ugs experiment retry job-000123 --file /本项目/manifest.json
ugs experiment show job-000124
```

retry 要求原 job 已终态且已完成进程/GPU收口（从未启动则无需GPU收口）；新job保留explicit_retry_of，原job/generation/结果与历史不复活。不使用失败自动重试。不要在前一链未结束时从项目外部覆盖其输入、源代码或配置；UGS只能约束受管执行。

## 运行合同

- 注册、validate、submit不改写项目文件。真正受管阶段启动后，项目程序可以覆盖原日志、预测、指标和checkpoint；UGS stdout日志默认追加，项目可自行采用覆盖写入。
- 整个有限链占有复用根的顺序写许可，包含阶段间等待。其他重叠根（包括父子路径、解析后的别名）不能并行写入；最终启动事务再次检查。同根被较早未结束链占用时，show/list/status显示 `OUTPUT_PATH_BUSY: job-id`。不同exp仍可在容量与GPU审核允许时并行。
- 原结果/失败回执可使用固定路径。UGS派发准备时仅对这两个小型控制文件检查归属：job_id、run_id、stage_id须匹配旧作业及原登记路径，旧作业已收口或属于本链已完成阶段。归档到UGS的artifact-receipts后，再清除原回执路径，避免把旧成功当成本次结果。科学产物不被UGS清理或复制。
- 回执须为普通单链接文件，单文件不超过64KiB；未知归属、符号链接、未收口等情况拒绝。保存原件SHA、位置及复用关联；历史失败视图保留。归档准备独立提交审计，后续GPU审核拒绝也不丢历史引用。
- 项目结果回执使用固定 `UGS_RESULT_PATH`，除原有outcome=success、artifacts_verified=true、artifacts路径及SHA、gate_passed外，务必写入环境中的六个身份字段：job_id、run_id、stage_id、generation、stage_nonce、manifest_sha256。新阶段只接受当次身份；失败回执同样绑定当次身份。
- 不要求不同尝试的项目文件路径不同；必要的UGS guardian/generation调度证据仍由UGS保留。项目自身科学产物会随授权覆盖改变，历史job的科学文件路径因此可能指向新内容；不能把新内容当作旧实验结果。
- 新manifest、CPU证据、源文件与资源档仍须真实校验，复用不豁免PIN/项目边界/门禁。既有verifier及全部辅助PIN保持不变。其他项目未显式授权此合同，原默认规则保持。

## 验收与生产

2026-11-15 15:47台北上线，公共ugs入口不变。运行入口为 `runtime/output-reuse-226bfa41d988/ugs_reuse_entry.py`；基础内核、GPU verifier与辅助PIN沿用 `runtime/931207b36c12/`。

75项gpu07 CPU验收通过：包含真实原路径覆盖、原回执归档后原路径重写、实际内核submit/retry复用原result_path且不在注册时删文件、源码SHA拒绝、目录冲突、旧阶段间等待、终态未收口拦截，以及原GPU/状态/启动复查/超时/占用/预约测试。未为此启动GPU训练。

验收文件 `runtime/output-reuse-226bfa41d988/acceptance.json` SHA `7914b7a58149c1116a006285cf5e9907be4afa600c1cd554945b60df4492ce3a`。

部署证据 `acceptance/output-reuse-production.json` SHA `93caecd1d706cd21cd43a84dee8969c9592ff655550ac9945f798266ac8afc5c`。切换时无活动/排队实验，原jobs、projects、policy保持；无实验重启。公共start/status/list/help通过，release_ok=true。

30秒监测、即时准入、35秒陈旧上限、5秒采集上限、双复查间隔2秒及2秒最终快照、健康/进程/显存基线/预约保持。
