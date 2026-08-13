# 性能优化 Frontier

最后维护日期：2026-08-12

本文是当前性能认知和 EDR 检索路由，不保存完整实验过程。开始相关任务前必须先读
[AGENT_PROTOCOL.md](AGENT_PROTOCOL.md)。

## 当前目标

解释并优化 CXL/SHM transport 相对 socket Rocket 的端到端效率和 scale-out 行为，
同时把 server、client、poller、CPU worker、NUMA placement 和实际 CPU cost 纳入结论。

## 活跃假设

以下内容来自现有本地 hypothesis ledger，当前都属于待验证假设，不是实验结论：

| ID | 假设 | 主要判别证据 | 状态 |
| --- | --- | --- | --- |
| FH-001 | dedicated poller core 与 poller-to-IO handoff 降低 scale-out 效率 | `shm_poller` vs `shm_directpoll`、QPS/core、per-thread CPU | `PROPOSED` |
| FH-002 | shared queue/cursor/metadata 产生 cache-coherence hotspot | `perf c2c`、LLC miss、两条 SHM route 同点退化 | `PROPOSED` |
| FH-003 | Rocket/EventBase/CPU-worker 上层路径先出现拐点 | `sum` vs `noop`、flamegraph、worker 利用率 | `PROPOSED` |
| FH-004 | client receive/callback/poller 污染 server-side 结论 | client CPU、client saturation、分离机器对照 | `PROPOSED` |
| FH-005 | 高线程点进入 NUMA/SMT placement regime change | `numastat`、binding map、`1-16` vs `32/64` | `PROPOSED` |
| FH-006 | 同步 GQM operation 成本在高压力下被排队放大，先触发 latency cliff 再损失 capacity | push/pop 与端点分离注入、每 RPC 操作计数、重复 delay sweep | `INCONCLUSIVE` |
| FH-007 | RPC 路径的同步 GQM operation 暴露量决定 fixed-outstanding delay 敏感度 | `K/QPS` cycle slope、每端 push/pop counter、outstanding-window sweep | `INCONCLUSIVE` |
| FH-009 | Empty-poll 后的 periodic backoff 可以降低空轮询 CPU，但 interval/BUD 的静态最优值随负载与 P99 SLO 改变 | `N={1,8}` 的 load × interval sweep、8+4 vCPU 下的 load × BUD × Sleep、CPU–p99 frontier、实际 backoff/wakeup 时序、IRQ/Socket 对照 | `RUNNING` |
| FH-012 | Netpoll GQM 的 BUD 与 Sleep 具有负载敏感性，低负载 CPU–p99 取舍会在高负载收缩为 capacity 约束 | `QPS × BUD × Sleep` attainment gate、CPU–p99 frontier、P99-SLO 最低 CPU 边界、Socket/IRQ 对照 | `RUNNING` |

## 未决实验

### EDR-0002：GQM 注入延迟对 Ubmem RPC 的敏感性 sweep

- Status：`INCONCLUSIVE`
- 记录：[EDR-0002-gqm-injected-delay-rpc-sweep.md](edr/EDR-0002-gqm-injected-delay-rpc-sweep.md)
- 当前证据：在 `Unary64`、Poisson open loop、35K target QPS 的单次 sweep 中，
  latency cliff 位于 `13.4us–33.5us`；67us 时完成吞吐约 30.60K QPS，并发生
  12.74% client shed。微基准确认 1us 注入约等于 1us/successful wrapper op。
- 不能确定：精确硬件 latency budget、push/pop 优先级、端点暴露量和 p99.9 尖峰来源。
- 允许重开：补齐精确 build/route provenance；每点至少重复三次；分离 client/server 与
  push/pop；记录每 RPC 操作数、queue/hardware counters 和对齐后的 CPU window。

### EDR-0003：GQM 注入延迟在 fixed-outstanding RPC 路径中的敏感性

- Status：`INCONCLUSIVE`
- 记录：[EDR-0003-gqm-fixed-outstanding-path-sensitivity.md](edr/EDR-0003-gqm-fixed-outstanding-path-sensitivity.md)
- 当前证据：在各自 `K=100` 的单次 sweep 中，`cpp2` Ubmem `noop` 的
  `K/QPS` cycle fit 斜率约为 `1.98us/us`；VA FLAT Compressed Polling `echo 1KiB`
  从 `335ns` 即开始明显退化，effective injection exposure 在 `0.335–13.4us` 约
  `195–240x`，到 `33.5/67us` 约 `308x`。
- 不能确定：effective exposure 中有多少来自 successful GQM operation 次数，有多少来自
  二级串行化或内部工作量变化；两条路径也不是同 workload/CPU 配置的严格 A/B。
- 允许重开：保存精确 build/route 和逐点命令；每点至少重复三次；分端分方向记录
  successful/empty push/pop、notification/ACK；对齐 workload/payload/CPU 并扫
  `K={1,8,32,100}`。

### EDR-0005：Empty-poll backoff interval 的 open-loop RPC 敏感性

- Status：`RUNNING`
- 记录：[EDR-0005-empty-poll-backoff-openloop-sensitivity.md](edr/EDR-0005-empty-poll-backoff-openloop-sensitivity.md)
- 唯一变量：在固定 `(IO threads, per-thread target QPS)` 切片内，只改变 poll 为空后的
  backoff interval；`0us` 为不退避对照组。
- 当前证据：`N={1,8}`、每线程 target `{3.5K,10K,35K QPS}`、interval
  `{0,10us,100us,1ms,10ms}` 的 30 个 Ubmem 点和 6 个 TCP Socket 对照点全部 `OK`；
  client/server CPU 已采集。新增固定 Server 8 vCPU、Client 4 vCPU 的 80 个
  `Target QPS × Sleep × BUD` Ubmem 点和 5 个 Socket 锚点；排除只归档的 idle-disabled
  记录后，五档 idle-enabled Ubmem 点通过 99.5% gate 的数量为
  `12/12、12/12、12/12、12/12、9/12`。
- 初步事实：TCP 在低负载进入 periodic backoff 未覆盖的 CPU–p99 Pareto 区域；到
  `N=8、35K QPS/thread` 时，Ubmem `100us` 同时具有更低 Total CPU 和 p99。
  Ubmem `10us/100us` 的 p99 scale-out amplification 约为 `0.99–1.01x`，TCP 在
  `10K/35K QPS/thread` 时为 `1.42x/1.35x`。新增矩阵中 80K Socket 未通过 gate；
  Ubmem 合格 frontier 为 `329.7us/5.304 cores` 到 `430.6us/2.349 cores`。
- 硬件含义：支持“空闲时 notification、繁忙时 polling”的 hybrid 需求方向。
- 证据边界：重复 run、实际 arm/notify/backoff/wakeup 时序和同一 Ubmem 路径上的真实
  HWQueue IRQ A/B 缺失；TCP 是系统级 reference，不能作 IRQ 收益测量或硬件内部归因。

### EDR-0008：Netpoll GQM BUD 与 Sleep 的负载敏感性

- Status：`RUNNING`
- 记录：[EDR-0008-netpoll-gqm-idle-load-sensitivity.md](edr/EDR-0008-netpoll-gqm-idle-load-sensitivity.md)
- 当前矩阵：固定 `Concurrency=128`、`Payload=1KiB`，扫描五档 target QPS、五档 BUD 和
  五档 Sleep；125 个配置键中 124 点完整、1 点只保留 TPS。
- 当前事实：达到 99.5% target attainment 的 cell 数随负载为 `25、20、10、10、0`；
  `731.5K` 档最高为 `707.5K TPS/96.72%`，按 near-capacity stress 解释。`BUD=0/256`
  实为相同有效配置的 25 对偶然重复，gate 分类 25/25 一致。
- Socket reference：五档依次达到 `100%、100%、100%、98.94%、52.17%`；前三档进入联合
  CPU–p99 frontier，后两档因未通过 gate 只作为 capacity 锚点。
- 初步方向：按重复均值与 Socket 联合候选，在给定 P99 budget 时最小化 Total CPU，四档
  负载形成 `5/6/4/2` 个离散选择区间。Socket 展示低中负载的事件驱动参照，高负载仍缺
  同 transport IRQ A/B。
- 证据边界：除 `BUD=0/256` 偶然重复外均为单次；缺少运行 provenance、stage
  residency/empty-pop/wakeup counter 和真实 GQM IRQ A/B，Socket 差异不能归因于 IRQ。

## 已关闭方向

### EDR-0001：CXL hot shard Busy-Poll EventBase

- Status：`REJECTED_IN_ENVELOPE`
- 记录：[EDR-0001-cxl-hot-shard-busy-poll-eventbase.md](edr/EDR-0001-cxl-hot-shard-busy-poll-eventbase.md)
- 结论：在本轮打平条件下，busy-poll backend 把 `cxl_hot_0` 的
  `epoll_wait` family 降到 0，但未观察到吞吐收益；`timeout` 路径低约 5.40%。
- 有效范围：stub backend、单 hot IO shard、未做独立 core/cpuset 隔离的 2026-07-07
  A/B 实验。
- 允许重开：真实 CXL backend；server/client/hot shard/worker 独立绑核；使用不 sleep
  但必须经过 CPU worker reply 的 workload；主要目标改为 latency 或 CPU efficiency。

## 检索路由

命中以下任一关键词时必须读取对应 EDR：

| Component/File | Mechanism | Metric/Workload | EDR |
| --- | --- | --- | --- |
| `CxlMemBenchmarkHotIoShard` | `BusyPollBackend` | `epoll_wait` | `EDR-0001` |
| `CxlMemBenchmarkTransport.cpp` | `ManualPoll` | `sum_weight` | `EDR-0001` |
| `Server.cpp` CXL hot IO options | hot EventBase backend | `timeout_weight`、QPS | `EDR-0001` |
| `OpenLoopStressTest`、Ubmem | `gqm_inject_cost_ns` | `Unary64`、p50/p99、shed | `EDR-0002` |
| GQM HWQueue wrapper | synchronous push/pop cost | completed QPS、queue latency | `EDR-0002` |
| `Client_ub` / `Server_ub` | fixed outstanding、`gqm_inject_cost_ns` | `noop`、QPS、p99、`K/QPS` cycle | `EDR-0003` |
| VA FLAT Compressed Polling | effective GQM exposure | `echo 1KiB`、QPS retention、p99 | `EDR-0003` |
| Ubmem polling path、GQM private pairs、TCP Socket | empty-poll periodic backoff、BUD、event-driven reference | `N × per-thread load × interval`、`Target QPS × Sleep × BUD`、P99-SLO minimum CPU、completed QPS | `EDR-0005` |
| Netpoll GQM idle path | BUD、configured Sleep、load-sensitive idle transition | target attainment、TP99/TP999、client/server CPU、CPU–p99 Pareto | `EDR-0008` |

## 推荐下一步

1. 对 FH-007 优先补 VA FLAT 每端 successful/empty push/pop、notification/ACK 和
   outstanding-window sweep，判断约 `200–300x` effective exposure 是调用密度还是二级
   串行化；保留 `cpp2` 约 `2x` 路径作为 control。
2. 对 FH-006 加密 `13.4us–33.5us` 区间，并完成 client/server 与 push/pop 分离注入；
   只有这样才能把 open-loop RPC cliff 转换为硬件设计预算。
3. 执行 FH-001 的 `shm_poller` / `shm_directpoll` 同资源预算对照，判断 dedicated
   poller handoff 是否是主要边界。
4. 如果两条 SHM route 在相近线程点同时退化，再进入 FH-002 的 cacheline/NUMA 证据收集。
5. `io_threads=1-16` 作为主结论区间；`32/64` 单独标记为高线程 placement regime。
6. 对 FH-009 在 8+4 vCPU envelope 中补代表性 frontier/边界点的三次交错重复，并在
   同一 Ubmem transport/resource budget 下加入真实 HWQueue IRQ/event-driven A/B；记录
   arm-to-notify、notify-to-run 和 publish-to-detect。Socket 继续作为系统级 reference，
   不用于直接计算 IRQ 收益。
7. 对 FH-012 先对代表性联合 frontier/边界点补三次交错重复和 stage
   residency/empty-pop/wakeup counter；Socket reference 已补齐，真实 GQM IRQ 可用后
   在同一 transport 内执行 polling/IRQ A/B。

## 维护规则

- 新实验必须建立 EDR，再开始实现。
- 终态 EDR 必须在本文件中有索引和路由。
- 本文件只保留当前有效摘要；完整历史留在 EDR，变更历史由 Git 保存。
- 更新后必须运行 `validate_edr.py`。
