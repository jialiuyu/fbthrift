---
id: EDR-0003
title: GQM 注入延迟在 fixed-outstanding RPC 路径中的敏感性
status: INCONCLUSIVE
created: 2026-07-22
updated: 2026-07-22
folly_branch: agent/codex/cxl-mem-rocket-benchmark
folly_commit: 1108d3b98255f6ae08dbc98f265011664133ecc6
fbthrift_branch: agent/codex/cxl-mem-rocket-benchmark
fbthrift_commit: 8ca4f5d6137a9dc4c784ef40c81004994ebac331
components:
  - cpp2 Client_ub and Server_ub
  - VA FLAT Compressed Polling
  - GQM HWQueue wrapper
files:
  - thrift/perf/cpp2/performance/analysis/gqm_injected_delay_sweep
mechanisms:
  - gqm_inject_cost_ns
  - fixed outstanding closed-loop cycle time
  - effective synchronous operation exposure
  - compressed polling
metrics:
  - avg_qps
  - qps_retention
  - p50
  - p99
  - p999
  - implied_cycle_us
  - effective_injection_exposure
workloads:
  - cpp2 noop at 100 outstanding
  - VA FLAT echo 1KiB at 100 outstanding
evidence:
  - ../analysis/gqm_injected_delay_sweep/README.md
  - ../analysis/gqm_injected_delay_sweep/data/cpp2_closed_loop_gqm_delay_sweep.csv
  - ../analysis/gqm_injected_delay_sweep/data/va_flat_compressed_polling_gqm_delay_sweep.csv
  - ../analysis/gqm_injected_delay_sweep/figures/05_cpp2_closed_loop_sensitivity.svg
  - ../analysis/gqm_injected_delay_sweep/figures/06_va_flat_compressed_polling_sensitivity.svg
  - ../analysis/gqm_injected_delay_sweep/figures/07_fixed_outstanding_comparison.svg
reopen_if:
  - 保存原始实验二进制的精确 build SHA checksum 和每个 delay 点的两端完整命令
  - 每个点至少重复三次并与 0-delay anchor 交错执行
  - 分端分方向记录 successful empty push pop 和 notification ACK counter
  - 在相同 workload payload CPU 配置下对齐两条路径并增加 outstanding window sweep
supersedes: []
superseded_by: []
---

# EDR-0003：GQM 注入延迟在 fixed-outstanding RPC 路径中的敏感性

## 摘要

在各自 `K=100` fixed-outstanding envelope 的单次 sweep 中，`cpp2` Ubmem `noop`
路径的 `K/QPS` request cycle 随注入近似线性增长，拟合斜率为
`1.980us cycle / us injected`，`R²=0.9988`；VA FLAT Compressed Polling `echo 1KiB`
路径则从首个 `335ns` 点开始连续损失 QPS，`0.335–13.4us` 的 effective injection
exposure 约为 `195–240x`，到 `33.5/67us` 约为 `308x`。

该结果支持“RPC 路径的同步 GQM 暴露量决定 delay 敏感度”的优先调查方向，但没有
operation counter、端点/scope 证据、重复 run 或精确 build provenance，不能把 effective
exposure 直接解释为 push/pop 次数，status 为 `INCONCLUSIVE`。

## Related Experiments

- `EDR-0002`，status `INCONCLUSIVE`：使用相同 `gqm_inject_cost_ns` knob，但测试的是
  `Unary64`、35K offered-QPS Poisson open loop。它观察到排队悬崖；本轮改为 saturated
  fixed-outstanding closed loop，用 `K/QPS` 描述 request-cycle capacity，二者不能用同一
  阈值解释。
- `EDR-0001`，status `REJECTED_IN_ENVELOPE`：涉及 SHM polling 与 EventBase，但主要变量
  是 busy-poll backend，不是 GQM operation service time，不能作为本轮 delay 对照。
- 本轮检索词：`GQM`、`gqm_inject_cost_ns`、`max_outstanding_ops`、`Client_ub`、
  `Compressed Polling`、`K/QPS`、`p99`。

## 假设与机制

- 假设：在 saturated fixed-outstanding closed loop 中，同步 GQM delay 会增加 request
  cycle；增量斜率反映路径的 effective injection exposure。Compressed Polling 若执行更多
  成功 operation 或触发二级串行化，应比低暴露路径更敏感。
- 主要变量：每条路径内部只改变 `gqm_inject_cost_ns`，从 `0` 扫到 `67000ns`。
- 预测：QPS 随 `K/QPS` cycle 增加而下降；高暴露路径在更小 delay 上出现更大 QPS 与
  p99 变化。
- Falsifier：完整重复实验显示两条路径的 normalized QPS/p99 响应无统计差异；或证明
  knob 没有作用于相同的成功 GQM operation 边界。

## 代码和版本范围

- `folly` 当前 snapshot：branch `agent/codex/cxl-mem-rocket-benchmark`，commit
  `1108d3b98255f6ae08dbc98f265011664133ecc6`。
- `fbthrift` 当前 snapshot：branch `agent/codex/cxl-mem-rocket-benchmark`，commit
  `8ca4f5d6137a9dc4c784ef40c81004994ebac331`。
- 本轮只新增规范化 CSV、分析逻辑、测试、图和记录，没有修改 benchmark、transport 或
  firmware 实现。
- Provenance：上述 SHA 是整理报告时可解析的仓库 snapshot，**不是原始实验二进制的
  已验证 build SHA**。VA FLAT benchmark 源码不在当前仓库中。

## 实验 Envelope

共同条件：client/server 分跨机器运行，命令均绑定 NUMA node 0；1 client、每线程
1 connection、`max_outstanding_ops=100`，每个 delay 只有 1 run。

`cpp2` 路径：

- route/binary：`client_ub --shm_mode=ubmem` / `server_ub --shm_mode=ubmem`。
- workload：`noop_weight=1`。
- server：1 IO thread、1 CPU thread。
- duration：total 30s、warmup 20s、active 10s。

VA FLAT 路径：

- route/binary：`client_va` / `server_va`，原报告命名为 Compressed Polling。
- workload：`echo_weight=1`、chunk 1024B。
- server：1 IO thread、0 CPU thread。
- duration：total 15s、warmup 5s、active 10s。
- memory IDs：request notify 2、response notify 6、client data 7、server data 3。

两条路径的 workload、server CPU 配置、binary 和 transport data path 不同。因此横向图只
比较各自 baseline-normalized sensitivity，不构成 transport 性能 A/B。

## Baseline 和实验矩阵

两条路径均使用：

```text
0, 335, 670, 1340, 3350, 6700, 13400, 33500, 67000 ns
0x, 1x, 2x, 4x, 10x, 20x, 40x, 100x, 200x
```

VA FLAT 原始记录把 `670ns` 标为 `1x`；按 `335ns` 基准规范化为 `2x`。所有 QPS 使用
10 秒 active window。对每点计算：

```text
implied_cycle_us = K * 1e6 / avg_qps
effective_exposure = (implied_cycle_us - baseline_cycle_us) / injected_delay_us
```

## 结果

| Path / Inject | Avg QPS | QPS retained | p50 | p99 | p99 / baseline | Effective exposure |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
| `cpp2` / 0 | 212,600 | 100.00% | 470us | 479us | 1.000x | — |
| `cpp2` / 13.4us | 200,300 | 94.21% | 498us | 509us | 1.063x | 2.16x |
| `cpp2` / 67us | 166,100 | 78.13% | 601us | 610us | 1.273x | 1.97x |
| VA FLAT / 0 | 50,090.9 | 100.00% | 1995.3us | 2021.9us | 1.000x | — |
| VA FLAT / 0.335us | 48,151.9 | 96.13% | 2070.8us | 2103.7us | 1.040x | 239.97x |
| VA FLAT / 13.4us | 19,422.0 | 38.77% | 5170.3us | 5279.1us | 2.611x | 235.26x |
| VA FLAT / 67us | 4,411.8 | 8.81% | 22743.6us | 22958.9us | 11.355x | 308.51x |

`cpp2` 全矩阵的 cycle fit 为：

```text
implied_cycle_us = 470.22 + 1.980 * injected_delay_us
R² = 0.9988
```

VA FLAT 的 p99.9 在低 delay 下非单调，保留为探索性数据，不用于 operation exposure
判断。原 artifact 没有记录 CPU、error、queue 或 hardware counter，因此本 EDR 不对
CPU efficiency 或错误率作结论。

## 结论

- 事实：`cpp2` 路径呈约 `1.98x` 的平滑 cycle sensitivity；`40x` 已对应 `5.79%`
  QPS 损失和 `6.26%` p99 增长，但没有 open-loop 式悬崖。
- 事实：VA FLAT 从 `1x` 开始连续退化，`40x` 只保留 `38.77%` QPS；其 effective
  exposure 在低中 delay 为约 `195–240x`，在 `100/200x` 为约 `308x`。
- 推断：VA FLAT 路径可能具有更高的成功 GQM operation 暴露量，或 delay 触发了额外
  串行化/内部工作量变化。两者都把硬件协同优化重点指向路径级 counter 和调用密度。
- 未知：真实 successful operations/RPC、push/pop 和 client/server 分解、Compressed
  Polling 内部扫描/批处理行为，以及相同 workload/CPU 配置下的可控 A/B。

## 限制与置信度

- 置信度：low-to-medium。
- 混杂变量：每点单次 run；两条路径 workload、payload、server CPU 和 binary 不同；
  exact build、frequency policy 和逐点 knob 命令未知。
- 缺失证据：CPU/resource cost、errors、successful/empty push/pop、notification/ACK、
  queue occupancy、硬件 counter 和 per-endpoint trace。
- 不能外推：不同 outstanding window、连接/线程数、payload、queue depth、firmware 版本，
  或把 normalized sensitivity 当成两条 transport 的绝对性能比较。

## Reopen 条件

1. 保存原始二进制的 exact build SHA/checksum、完整逐点命令和 route evidence。
2. 每点至少重复 3 次，并在实验顺序中交错 0-delay anchor，建立置信区间。
3. 在 client/server 和 push/pop 维度记录 successful/empty operation、notification 和
   ACK counter，验证 effective exposure 的来源。
4. 用相同 workload、payload、CPU 配置对齐两条路径，并扫 `K={1,8,32,100}`，区分
   per-RPC 固有调用密度与 outstanding-window 相关扫描/批处理成本。

## 原始证据

- 完整报告：[README.md](../analysis/gqm_injected_delay_sweep/README.md)
- 来源与 checksum：[SOURCE.md](../analysis/gqm_injected_delay_sweep/data/SOURCE.md)
- `cpp2` CSV：[cpp2_closed_loop_gqm_delay_sweep.csv](../analysis/gqm_injected_delay_sweep/data/cpp2_closed_loop_gqm_delay_sweep.csv)
- VA FLAT CSV：[va_flat_compressed_polling_gqm_delay_sweep.csv](../analysis/gqm_injected_delay_sweep/data/va_flat_compressed_polling_gqm_delay_sweep.csv)
- 结论图：[07_fixed_outstanding_comparison.svg](../analysis/gqm_injected_delay_sweep/figures/07_fixed_outstanding_comparison.svg)

原始 Codex attachment 未复制到仓库；`SOURCE.md` 记录 logical artifact ID、SHA-256、
采集命令和规范化规则。

## 完成检查

- [ ] 两仓库 SHA 是原始实验二进制的精确 build snapshot。
- [x] Related Experiments 和唯一主要变量已写清。
- [x] baseline、envelope、完整 sweep 和数据限制已记录。
- [x] status 为 `INCONCLUSIVE`，且 `reopen_if` 非空。
- [x] Frontier 已更新。
- [x] `validate_edr.py` 通过。
