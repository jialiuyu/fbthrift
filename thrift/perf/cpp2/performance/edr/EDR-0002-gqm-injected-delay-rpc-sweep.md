---
id: EDR-0002
title: GQM 注入延迟对 Ubmem RPC 的敏感性 sweep
status: INCONCLUSIVE
created: 2026-07-21
updated: 2026-07-21
folly_branch: agent/codex/cxl-mem-rocket-benchmark
folly_commit: 1108d3b98255f6ae08dbc98f265011664133ecc6
fbthrift_branch: agent/codex/cxl-mem-rocket-benchmark
fbthrift_commit: 041828da3c27a54824ed68e616908d7d87648493
components:
  - OpenLoopStressTest
  - Ubmem transport
  - GQM HWQueue wrapper
files:
  - thrift/perf/cpp2/performance/analysis/gqm_injected_delay_sweep
mechanisms:
  - gqm_inject_cost_ns
  - synchronous push and pop completion cost
  - open-loop queueing amplification
metrics:
  - completed_qps
  - client_shed
  - p50
  - p99
  - p999
  - process_cpu_cores
workloads:
  - Unary64
  - poisson_exp at 35000 target QPS
evidence:
  - ../analysis/gqm_injected_delay_sweep/README.md
  - ../analysis/gqm_injected_delay_sweep/data/gqm_microbenchmark.csv
  - ../analysis/gqm_injected_delay_sweep/data/rpc_baseline_qps_sweep.csv
  - ../analysis/gqm_injected_delay_sweep/data/gqm_delay_sweep_35000qps.csv
reopen_if:
  - 记录精确 build SHA 和完整 client server 命令并确认全程无 SHM fallback
  - 每个 delay 点至少重复三次并加密 13400ns 到 33500ns 区间
  - 分离 client server 以及 push pop 注入并记录每 RPC 操作次数
  - 对齐 client server CPU measurement window 并补充 queue 和 hardware counters
supersedes: []
superseded_by: []
---

# EDR-0002：GQM 注入延迟对 Ubmem RPC 的敏感性 sweep

## 摘要

现有单次 sweep 表明，在 `Unary64`、Poisson open-loop、35K target QPS、
`max_inflight=496` 的 envelope 内，GQM wrapper 成功操作的注入成本从 13.4us 增加到
33.5us 时出现明显 RPC latency cliff；67us 时完成吞吐降至约 30.60K QPS，并发生
12.74% client shed。

微基准证明 `1us` 配置在 push-only、pop-only 和 push+pop 中产生约 `1us/op` 的实测增量，
而 raw ugqm 和 empty pop 基本不受影响。但原始 artifact 缺少精确 build provenance、重复
run、完整 delay 命令、fallback 证据、端点和 push/pop 分解，因此不能把当前区间收缩为
硬件单指令 latency budget，status 为 `INCONCLUSIVE`。

## Related Experiments

- `EDR-0001`，status `REJECTED_IN_ENVELOPE`：研究 CXL hot-shard busy-poll EventBase，
  与本轮都涉及 polling/SHM transport，但 backend、主要变量和 workload 不同，不能作为
  GQM delay 对照。
- 本轮检索词：`GQM`、`gqm_inject_cost_ns`、`OpenLoopStressTest`、`Unary64`、
  `HwSinglePushOnly`、`HwSinglePopOnly`、`Ubmem`。未找到更早的 GQM latency EDR。

## 假设与机制

- 假设：同步 GQM push/pop 的完成成本增加会提高每 RPC 的直接服务成本；当 35K offered
  load 已接近系统排队边界时，该成本会先放大端到端 latency，随后降低完成吞吐并触发
  client shed。
- 唯一主要变量：当前 artifact 标题记录的 `gqm_inject_cost_ns`，从 0 扫到 67000ns。
- 预测：低 delay 区间完成吞吐保持、latency 缓慢增加；跨过边界后 latency 先跳升，
  更高 delay 下 completed QPS 下降且 shed 增加。
- Falsifier：在重复且完整隔离的相同 envelope 下，各 delay 点的 latency、completed QPS
  和 shed 与 0-delay 无统计差异；或证明 delay 没有作用于实际 RPC GQM 路径。

## 代码和版本范围

- `folly` 当前 snapshot：branch `agent/codex/cxl-mem-rocket-benchmark`，commit
  `1108d3b98255f6ae08dbc98f265011664133ecc6`。
- `fbthrift` 当前 snapshot：branch `agent/codex/cxl-mem-rocket-benchmark`，commit
  `041828da3c27a54824ed68e616908d7d87648493`。
- 本轮只新增分析 CSV、脚本、测试、图和 EDR，没有修改 benchmark 或 transport 实现。
- Provenance：上述 SHA 是整理报告时工作区的可解析 snapshot，**不是原始 benchmark
  二进制的已验证 build SHA**。原始记录没有保存构建 commit 或二进制 checksum。

## 实验 Envelope

- transport route：`transport=ubmem`、`shm_mode=ubmem`；结果 JSON 中
  `forbid_shm_fallback=false`，server baseline 命令中该 flag 为 true，缺少 route log。
- client/server：跨机器；server 地址记录为 `192.168.0.2`，机器型号未知。
- workload：`Unary64`，`poisson_exp` open loop。
- concurrency/pressure：client 1 thread、每线程 1 connection、server 1 IO thread 和
  1 CPU thread、`max_inflight=496`。
- duration：warmup 3s、measurement 15s、drain timeout 5s；server CPU 的约 21s window
  是 whole `serve()` duration。
- CPU/NUMA placement：命令显示 client/server 均绑定 NUMA node 0；精确 core pinning、
  SMT 和频率策略未知。
- 其他固定参数：baseline 使用 `gqm_inject_cost_ns=0`。delay 点完整命令缺失，无法确认
  注入端点和 push/pop scope。

## Baseline 和实验矩阵

第一阶段以 0-delay 扫 `350, 1000, 3500, 10000, 35000, 40000, 50000` target QPS。
35K 后 latency 快速上升，50K 出现 13.44% shed，因此选择 35K 作为敏感性压力锚点。

第二阶段在 35K 下扫：

```text
0, 350, 670, 1340, 3350, 6700, 13400, 33500, 67000 ns
```

每点只有 1 run。原始记录把 67000ns 标为 100x；按 335ns 基准修正为 200x。
完整矩阵和原始计数见 evidence CSV。

## 结果

| Inject | Completed QPS | Shed | p50 | p99 | p99.9 | Validity |
| ---: | ---: | ---: | ---: | ---: | ---: | --- |
| 0 | 35,068.6 | 0 | 526us | 1274us | 1619us | valid anchor |
| 13.4us | 35,068.6 | 0 | 743us | 1615us | 6391us | valid; tail spike |
| 33.5us | 35,066.1 | 0.006% | 6992us | 8784us | 9630us | latency cliff |
| 67us | 30,601.9 | 12.74% | 15781us | 16473us | 18715us | capacity loss |

0.35us、1.34us、13.4us 的 p99.9 非单调尖峰在没有重复和 slow-request 关联数据时，
不解释为 delay 的确定性响应。

微基准在 1us 注入下测得 push-only `1009.8ns/op`、pop-only `1024.1ns/op`、
push+pop `997.7ns/op` 的基线增量，支持 injection knob 已作用于成功 wrapper operation。

## 结论

事实：在当前 envelope 的单次运行中，latency cliff 被夹在 13.4us 和 33.5us 两个有效
点之间，67us 已发生完成吞吐下降和 12.74% shed；微基准对 1us 注入的校准近似线性。

推断：35K 已接近排队敏感区，额外同步 queue cost 被 queueing amplification 放大，
因而 33.5us 的 knob 变化对应数毫秒 RPC latency 跳升。

未知：每 RPC 的 push/pop 次数、注入发生的端点与方向、精确 cliff、尾部尖峰来源、
无 fallback 条件下的同样结果。当前不能决定“先优化 push 还是 pop”，也不能把 13.4us
直接当作单条硬件指令的合格 budget。

## 限制与置信度

- 置信度：low-to-medium。
- 混杂变量：单次 run、未知机器/频率/build、server CPU window 不对齐、可能的 route
  配置字段不一致、未记录操作次数和 queue occupancy。
- 缺失证据：重复分布、完整 raw JSON/log、二进制 checksum、slow-request trace、硬件和
  queue counters、push/pop 与 client/server 分解。
- 不能外推：其他 QPS、payload、连接数、线程数、queue depth、真实 firmware 版本和
  不同硬件拓扑。

## Reopen 条件

满足 frontmatter 任一条件时创建后续 EDR：

1. 记录精确 build SHA、完整命令和 route evidence，消除 provenance/fallback 歧义。
2. 每点至少重复 3 次并加密 13.4us 到 33.5us 区间，建立 cliff 和 tail 的置信区间。
3. 将 client/server 和 push/pop 分离注入，同时记录成功/empty 操作次数，形成每 RPC
   暴露模型。
4. 对齐 CPU window，并加入 queue depth/occupancy/retry 和 hardware counter，解释
   capacity loss 的机制。

## 原始证据

- 整理后的完整报告：[README.md](../analysis/gqm_injected_delay_sweep/README.md)
- 来源和 checksum：[SOURCE.md](../analysis/gqm_injected_delay_sweep/data/SOURCE.md)
- 微基准：[gqm_microbenchmark.csv](../analysis/gqm_injected_delay_sweep/data/gqm_microbenchmark.csv)
- baseline QPS sweep：[rpc_baseline_qps_sweep.csv](../analysis/gqm_injected_delay_sweep/data/rpc_baseline_qps_sweep.csv)
- 35K delay sweep：[gqm_delay_sweep_35000qps.csv](../analysis/gqm_injected_delay_sweep/data/gqm_delay_sweep_35000qps.csv)

原始 Codex attachment 没有复制到仓库；`SOURCE.md` 记录 artifact ID 和 SHA-256。由于缺少
精确 build artifact 和原始日志包，本记录不能升级为高置信 terminal conclusion。

## 完成检查

- [ ] 两仓库 SHA 是原始实验二进制的精确 build snapshot。
- [x] Related Experiments 和唯一主要变量已写清。
- [x] baseline、envelope、完整 sweep 和数据限制已记录。
- [x] status 为 `INCONCLUSIVE`，且 `reopen_if` 非空。
- [x] Frontier 已更新。
- [x] `validate_edr.py` 通过。
