---
id: EDR-0005
title: Empty-poll backoff interval 的 open-loop RPC 敏感性
status: RUNNING
created: 2026-07-29
updated: 2026-08-12
folly_branch: agent/codex/cxl-mem-rocket-benchmark
folly_commit: 1108d3b98255f6ae08dbc98f265011664133ecc6
fbthrift_branch: agent/codex/cxl-mem-rocket-benchmark
fbthrift_commit: eb77b990ef2fcc9ec6bc04bf358981289c0d03f7
components:
  - OpenLoopStressTest
  - Ubmem polling path
  - GQM private HWQueue pairs
files:
  - thrift/perf/cpp2/performance/analysis/poll_idle_openloop_load_and_thread_scaling
  - thrift/perf/cpp2/performance/analysis/fbthrift_idle_budget_load_sensitivity
mechanisms:
  - empty-poll backoff interval
  - empty-poll budget
  - periodic polling
metrics:
  - completed_qps
  - p99
  - client_cpu_pct
  - server_cpu_pct
  - total_cpu_cores
  - cpu_core_seconds_per_million_rpc
  - target_sustain_pct
  - p99_slo_minimum_cpu_boundary
workloads:
  - ubmem-openloop
  - socket-openloop
evidence:
  - ../analysis/poll_idle_openloop_load_and_thread_scaling/README.md
  - ../analysis/poll_idle_openloop_load_and_thread_scaling/data/openloop_backoff_socket_comparison_20260729.csv
  - ../analysis/poll_idle_openloop_load_and_thread_scaling/data/SOURCE.md
  - ../analysis/fbthrift_idle_budget_load_sensitivity/README.md
  - ../analysis/fbthrift_idle_budget_load_sensitivity/data/SOURCE.md
  - ../analysis/fbthrift_idle_budget_load_sensitivity/data/fbthrift_idle_budget_load_sensitivity.csv
  - ../analysis/fbthrift_idle_budget_load_sensitivity/data/fbthrift_socket_qps_baseline.csv
  - ../analysis/fbthrift_idle_budget_load_sensitivity/data/fbthrift_idle_policy_boundaries.csv
reopen_if:
  - 获得每个条件至少三次交错重复和未舍入原始结果
  - 增加同一 Ubmem transport 上的真实 HWQueue IRQ event-driven 对照
  - 补齐实际 empty-poll backoff arm notify wakeup 时序
supersedes: []
superseded_by: []
---

# EDR-0005：Empty-poll backoff interval 的 open-loop RPC 敏感性

## 摘要

本实验在 `N={1,8}` 和每线程 target QPS `{3500,10000,35000}` 的六个独立条件切片内，
只改变 poll 为空后的 backoff interval，并为每个切片增加一个 TCP Socket event-driven
系统级参考点。30 个 Ubmem 点和 6 个 TCP 点均为 `OK`，client/server CPU 已补齐。
当前单次结果显示：TCP 在低负载进入 periodic backoff 尚未覆盖的 CPU–p99 Pareto
区域；到 `N=8、35K QPS/thread` 时，Ubmem `100us` 同时具有比 TCP 更低的 Total CPU
和 p99。status 保持 `RUNNING`，等待重复 run 和同一 Ubmem 路径上的真实 HWQueue IRQ
A/B。

2026-08-12 又归档一组独立的 fbthrift `Target QPS × Sleep × BUD` 扩展矩阵：固定
Server 8 vCPU、Client 4 vCPU，包含 80 个 Ubmem 完整点和 5 个 Socket 系统级锚点。
该矩阵使用 `reported Sustain% >= 99.5%` gate，并把 idle-disabled 下四个无效 BUD 取值
作为同一 polling baseline 的四次重复。它补充了负载敏感的 CPU–P99 frontier 和
P99-SLO 最低 CPU 边界，但仍不是同 transport IRQ A/B。

## Related Experiments

- `EDR-0001`，status `REJECTED_IN_ENVELOPE`：同样研究 polling/wait 行为，但对象是 stub
  CXL hot-shard EventBase 的 busy-poll A/B，主要指标是 QPS 和 `epoll_wait_count`；本轮
  使用真实 Ubmem open-loop 路径，唯一变量改为 empty-poll backoff interval。
- `EDR-0004`，status `RUNNING`：覆盖相同 private queue-pair 生产形态和
  `N={1,8}` open-loop 压力，但主要变量是 queue-pair 数、load 或 successful-operation
  injected delay；本轮固定每个条件切片，只改变 empty-poll 后的退避时长。
- 既有 `poll_idle_poisson_350qps` 分析记录低负载下 empty-poll budget 与 sleep interval
  的联合 sweep；本轮不改变 budget，新增中高 per-thread load 和 `N=8` 对齐切片。
- 检索词：`poll`、`idle`、`openloop`、`Poisson`、`interrupt`、`IRQ`、`GQM`。

## 假设与机制

- 假设：empty-poll 后退避可以减少无效轮询 CPU，但请求到达时需要等待下一次 poll；当
  backoff interval 相对 arrival/service cadence 过长时，可能先提高 p99，再失去 capacity。
- 唯一主要变量：在固定 `(io_threads, per-thread target QPS)` 切片内，改变
  `idle_interval_us={0,10,100,1000,10000}`；`0us` 为不退避对照组。
- 支持现象：interval 增加时 client CPU 下降、p99 上升，并在高 offered load 出现吞吐
  无法维持。
- 系统级对照：TCP 不属于 interval sweep；它在相同 `(N, per-thread load)` 下回答成熟
  event-driven transport 能否进入 periodic backoff 未覆盖的 CPU–p99 区域。
- Falsifier：相同切片内 CPU、p99 和 completed QPS 不随 interval 出现可重复变化，或
  证明配置没有作用于实际 empty-poll 路径。

## 代码和版本范围

- `folly` 文档记录 snapshot：`1108d3b98255f6ae08dbc98f265011664133ecc6`。
- `fbthrift` 初始 interval 报告 snapshot：`1194b43063d404e592bb3e53379312d9b479904a`；
  2026-08-12 扩展分析的 pre-report snapshot：
  `eb77b990ef2fcc9ec6bc04bf358981289c0d03f7`。
- 本 EDR 只落盘用户提供的内部测试结果，不修改 benchmark、transport 或 firmware。
- 上述 SHA 是记录文档时的开源 worktree snapshot，不是内部真实硬件实验 binary 的精确
  build provenance。

## 实验 Envelope

- profile 标签：`ubmem-openloop`；Ubmem config：`openloop_poll_gap`；TCP config：
  `socket_poll_gap`。TCP 逐点 `client_result.transport=socket`。
- server：`root@150.0.224.132`，service `192.168.0.2`；client：
  `root@150.0.224.131`。
- IO threads：`N={1,8}`；每线程 target QPS：`{3500,10000,35000}`。
- 通信两端使用相同 empty-poll backoff interval。
- workload：`Unary64`、`poisson_exp`；warmup `5s`，measurement `30s`，
  `max_inflight=256`。
- server 使用 NUMA node 1，client 使用 NUMA node 0。
- 每个 cell 一次观测；server CPU 已采集。firmware revision、binary checksum、完整
  physical-core map、actual backoff/wakeup 时序仍未保存。

## Baseline 和实验矩阵

每个固定 `(N, per-thread target QPS)` 切片分别以 `idle_interval=0us` 为 baseline：

```text
N                        = 1, 8
per-thread target QPS    = 3500, 10000, 35000
empty-poll backoff       = 0, 10, 100, 1000, 10000 us
TCP Socket reference     = 1 point per (N, per-thread load)
repeats                  = 1 observed run/cell
```

线程数和负载定义六个条件切片，不作为同一 A/B 中与 interval 同时变化的主要变量。
正式矩阵共 `2 x 3 x (5+1)=36` 个 unique cells。

## 结果

- 36/36 点 `OK`，没有 failed 或 skipped point。
- `10us` 在六个切片中保持目标吞吐；Total CPU 相对 `0us` 下降 `46.1–79.8%`。
- `1ms/10ms` 的 p50 分别接近 `1ms/10ms`，p99 接近 `1.9–2.0x` interval，与两端
  独立周期检测等待相加的简单模型一致，但内部时序未验证。
- `10ms` 在每线程 `35K QPS` 时，`N=1/N=8` completion ratio 分别为
  `56.75%/56.76%`。
- 每线程 `3.5K QPS` 时，TCP 相比 Ubmem `10us` 将 Total CPU 降低 `63.2%/65.1%`
  （`N=1/N=8`），p99 只增加 `15.5us/25.3us`；TCP 同时优于 Ubmem `100us` 的 CPU
  和 p99。
- `N=8、35K QPS/thread` 时，Ubmem `100us` 为 `466.7us / 7.077 cores`，TCP 为
  `568.2us / 7.498 cores`，前者在两个指标上均更低。
- Ubmem `10us/100us` 的 p99 `N=8/N=1` amplification 为 `0.99–1.01x`；TCP 在
  `10K/35K QPS/thread` 时为 `1.42x/1.35x`。

完整 36 点、派生表和四张图见 evidence 中的 README/CSV。

### 2026-08-12：BUD × Sleep × Load 扩展矩阵

- 资源：Server `8 vCPU`、Client `4 vCPU`，合计 `12 vCPU`；两端 idle 配置对称。
- 矩阵：target QPS `{1.6K,16K,40K,80K,152K}`，idle disabled 或 Sleep
  `{1us,10us,100us}`，BUD `{0,1,16,256}`，共 80 个完整 Ubmem 点。
- 同 target QPS 的 Socket 锚点共 5 个；只作为系统级事件驱动参照。
- 五档 Ubmem 原始点通过 99.5% gate 的数量分别为 `16/16、16/16、15/16、16/16、9/16`。
- idle-disabled 时 BUD 不生效，因此每档四行聚合为四次 polling baseline 重复。1.6K、
  16K、80K 的四次均通过 gate；40K 有一次 `99.2%`，152K 为 `81.9–82.3%`，后二者
  不进入合格 frontier。
- Socket 在 1.6K/16K/40K/152K 通过 gate；80K 为 `99.0%`，只作容量锚点。152K Socket
  虽为 `99.6%`，但 P99 为 `12.12ms`，说明 gate 不能替代尾延迟约束。
- 80K 下 Socket 未通过 gate；Ubmem 合格 frontier 从
  `329.7us/5.304 cores` 延伸到 `430.6us/2.349 cores`。152K 下 Ubmem 代表性合格点为
  `1.633ms/4.266 cores` 和 `1.848ms/3.887 cores`。
- 完整 80+5 点、两张图和 23 段 P99-SLO 选择边界见新增 evidence 目录。

## 结论

当前 envelope 内，empty-poll backoff interval 是显著的两端 CPU–p99 取舍旋钮；TCP
证明成熟 event-driven transport 在低负载能够进入 periodic backoff 未覆盖的 Pareto
区域，但在多线程高负载下不再保持优势。硬件方向因此更接近“空闲时 notification、繁忙
时 polling”的 hybrid interface，而不是纯 IRQ 替代 polling。

2026-08-12 的 BUD 扩展矩阵进一步表明，合格的最低 CPU 候选会随 target QPS 和 P99 SLO
切换，单一静态 BUD/Sleep 不能代表完整工作区间；80K Socket 未通过 gate，而 152K Socket
虽然通过 gate，P99 已达到 12.12ms。这些结果适合定义未来同 transport IRQ A/B 需要覆盖
的边界，不是 IRQ 已达到该边界的证据。

TCP 与 Ubmem 的 transport 和软件路径不同；当前结果是 HWQueue notification/IRQ 的系统
需求证据，不是 HWQueue IRQ 的收益测量。

## 限制与置信度

- 描述性置信度：中；36 个点均有效且趋势强，但每点只有一次运行。
- 因果和硬件归因置信度：低；没有 queue counter、实际 arm/notify/backoff/wakeup
  时序和精确 build provenance。
- 主要缺失：真实 HWQueue IRQ A/B、重复交错运行、执行顺序和误差区间。
- BUD 扩展矩阵除 idle-disabled 四次偶然重复外，其余 Ubmem cell 和 Socket 锚点均为
  单次观测；P99-SLO 边界是描述性离散选择，不是统计置信区间或生产默认值。
- 不能外推：不同 payload/arrival、shared HWQueue、跨 NUMA、其他 firmware/机器，或
  event-driven IRQ 模式。

## Reopen 条件

1. 每个条件完成至少三次交错重复，保存未舍入 QPS、request count 和 latency histogram。
2. 在同一 Ubmem transport 和 resource budget 下增加真实 HWQueue IRQ/event-driven A/B。
3. 补齐实际退避次数、actual sleep duration、arm-to-notify、notify-to-run 和
   publish-to-detect latency。

## 原始证据

- 报告整理：
  [`README.md`](../analysis/poll_idle_openloop_load_and_thread_scaling/README.md)
- 规范化数据：
  [`openloop_backoff_socket_comparison_20260729.csv`](../analysis/poll_idle_openloop_load_and_thread_scaling/data/openloop_backoff_socket_comparison_20260729.csv)
- 来源和缺失字段：
  [`SOURCE.md`](../analysis/poll_idle_openloop_load_and_thread_scaling/data/SOURCE.md)
- BUD 扩展报告：
  [`README.md`](../analysis/fbthrift_idle_budget_load_sensitivity/README.md)
- BUD 扩展规范化 80 点：
  [`fbthrift_idle_budget_load_sensitivity.csv`](../analysis/fbthrift_idle_budget_load_sensitivity/data/fbthrift_idle_budget_load_sensitivity.csv)
- BUD 扩展 Socket 5 点：
  [`fbthrift_socket_qps_baseline.csv`](../analysis/fbthrift_idle_budget_load_sensitivity/data/fbthrift_socket_qps_baseline.csv)
- BUD 扩展来源和 checksum：
  [`SOURCE.md`](../analysis/fbthrift_idle_budget_load_sensitivity/data/SOURCE.md)

原始 JSON、日志、图片和 binary checksum 未进入仓库，因此当前记录不宣称具备完整
artifact provenance。

## 完成检查

- [x] 两仓库文档 snapshot full SHA 可解析。
- [x] Related Experiments 和唯一主要变量已写清。
- [x] baseline、envelope 和 36 个 observed point 已保存。
- [x] BUD 扩展矩阵的 80 个 Ubmem 点和 5 个 Socket 锚点已保存。
- [x] 两张 PNG/SVG 图和 23 段 P99-SLO 边界已生成。
- [x] status 保持 `RUNNING`，后续证据条件已列出。
- [x] Frontier 已更新。
- [ ] 每个条件至少三次交错重复。
- [x] server CPU 和 TCP Socket 系统级对照已补齐。
- [ ] 实际 backoff/wakeup 时序和真实 HWQueue IRQ A/B 已补齐。
- [ ] 完整内部 build/firmware provenance 已保存。
- [x] `validate_edr.py` 通过。
