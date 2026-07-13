# 性能优化 Frontier

最后维护日期：2026-07-13

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

## 推荐下一步

1. 优先执行 FH-001 的 `shm_poller` / `shm_directpoll` 同资源预算对照，先判断 dedicated
   poller handoff 是否是主要边界。
2. 如果两条 SHM route 在相近线程点同时退化，再进入 FH-002 的 cacheline/NUMA 证据收集。
3. `io_threads=1-16` 作为主结论区间；`32/64` 单独标记为高线程 placement regime。

## 维护规则

- 新实验必须建立 EDR，再开始实现。
- 终态 EDR 必须在本文件中有索引和路由。
- 本文件只保留当前有效摘要；完整历史留在 EDR，变更历史由 Git 保存。
- 更新后必须运行 `validate_edr.py`。
