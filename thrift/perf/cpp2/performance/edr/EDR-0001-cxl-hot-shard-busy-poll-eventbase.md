---
id: EDR-0001
title: CXL hot shard Busy-Poll EventBase A/B 验证
status: REJECTED_IN_ENVELOPE
created: 2026-07-07
updated: 2026-07-13
folly_branch: agent/codex/cxl-mem-rocket-benchmark
folly_commit: b0f2ac351e90ad27309ef0135745841e446a4ea8
fbthrift_branch: agent/codex/cxl-mem-rocket-benchmark
fbthrift_commit: ab6959f9b5ffd03c9c029d7882397193dbd26bad
components:
  - CxlMemBenchmarkHotIoShard
  - BusyPollBackend
files:
  - folly/io/async/BusyPollBackend.cpp
  - thrift/perf/cpp2/server/Server.cpp
  - thrift/perf/cpp2/util/CxlMemBenchmarkTransport.cpp
mechanisms:
  - BusyPollBackend
  - ManualPoll
  - explicit NotificationQueue and ReplyQueue polling
metrics:
  - qps
  - epoll_wait_count
workloads:
  - sum_weight
  - timeout_weight
evidence:
  - ../../CXL_MEM_BUSY_POLL_EVENTBASE_VALIDATION_CONCLUSION.md
reopen_if:
  - 使用真实 CXL backend 代替 stub backend
  - server client hot shard 和 CPU worker 分别绑定独立 core 或 cpuset
  - 使用不 sleep 但必须经过 CPU worker reply 的 workload
  - 主要目标改为 latency 或 CPU efficiency 并补齐对应指标
supersedes: []
superseded_by: []
---

# EDR-0001：CXL hot shard Busy-Poll EventBase A/B 验证

## 摘要

功能目标已经验证：CXL hot shard 使用 `BusyPollBackend + ManualPoll` 后，触发 worker
reply 的 workload 中 `cxl_hot_0` 线程的 `epoll_wait` family 次数从 83654 降到 0。
但在本轮打平条件下没有观察到吞吐收益，status 为 `REJECTED_IN_ENVELOPE`。

本记录由 2026-07-07 的结论文档迁移而来。原文没有保存构建时两个仓库的精确 SHA。
frontmatter 中的 commit 是分别引入 busy-poll backend 和 CXL hot shard 集成的可解析
代码 commit，属于历史迁移推断，不是对当时 dirty build tree 的精确重建。

## Related Experiments

- 这是知识库初始化时迁移的第一条 EDR，没有更早的 EDR 可关联。
- 历史搜索依据：`BusyPollBackend`、`ManualPoll`、`epoll_wait`、
  `CxlMemBenchmarkTransport.cpp`、`timeout_weight`。

## 假设与机制

假设：把 CXL hot IO shard 从默认 epoll backend 切换到 busy-poll backend，并显式轮询
EventBase NotificationQueue、worker ReplyQueue 和 CXL poll registry，可以消除 hot thread
的阻塞 syscall，并提高吞吐。

唯一主要变量是：

```text
--cxl_mem_hot_busy_poll_eventbase=false/true
```

其他 server/client/workload 参数和 benchmark 二进制保持一致。

## 代码和版本范围

- `folly` code commit：`b0f2ac351e90ad27309ef0135745841e446a4ea8`
- `fbthrift` code commit：`ab6959f9b5ffd03c9c029d7882397193dbd26bad`
- 关键符号：`BusyPollBackend`、`makeHotIoEventBaseOptions()`、
  `CxlMemBenchmarkHotIoShard::run()`、`pollEventBaseQueue()`、
  `pollWorkerReplyQueue()`、`CxlMemBenchmarkPollRegistry::pollOnce()`。

上述 SHA 只证明对应代码已经引入。原始实验没有记录精确 build snapshot，因此不能据此
声称当时工作区与这些 commit 完全一致。

## 实验 Envelope

- 容器镜像：`meta-cpp-shm-transport-dev:ubuntu24.04`
- CXL backend：`stub`
- server：`cxl_mem_hot_io_threads=1`、`io_threads=1`、`cpu_threads=1`
- 未执行 core pinning、isolated CPU 或 NUMA 隔离。
- 对比 workload：`sum_weight=1` 和 `timeout_weight=1`。
- `max_outstanding_ops`：1 或 64，取决于矩阵点。

## 结果

### 吞吐

| Workload | Pressure | busy=false | busy=true | Delta |
| --- | --- | ---: | ---: | ---: |
| `sum_weight=1` | `max_outstanding_ops=64` | 39640.07 | 39616.00 | -0.06% |
| `sum_weight=1` | `max_outstanding_ops=1` | 636.53 | 634.53 | -0.31% |
| `timeout_weight=1` | `max_outstanding_ops=64` | 7130.60 | 6745.70 | -5.40% |

### Syscall

| Workload | Mode | `cxl_hot_0` epoll wait family |
| --- | --- | ---: |
| `sum_weight=1` | busy=false | 0 |
| `sum_weight=1` | busy=true | 0 |
| `timeout_weight=1` | busy=false | 83654 |
| `timeout_weight=1` | busy=true | 0 |

`sum` 直接在 IO EventBase 上完成，不适合证明消除 epoll wakeup 的收益；`timeout` 会经过
CPU worker reply queue，能够证明 busy-poll backend 确实消除了 hot shard 的 epoll wait，
但 handler sleep 和 CPU 竞争主导了吞吐。

## 结论

功能验证通过；hot CXL thread 0 epoll wait 目标验证通过；本轮打平条件下 busy-poll
backend 未观察到吞吐收益。

该结论只适用于上述 envelope，不能外推为“busy-poll 在真实 CXL 环境中没有价值”。

## 限制与置信度

- 置信度：medium。
- 没有独立 core/cpuset 和 NUMA 隔离。
- 使用 stub backend，不是真实 CXL queue backend。
- `timeout` handler 包含 sleep，会把调度和等待成本混入吞吐。
- 原始 raw logs 和构建 snapshot 没有作为耐久 artifact 保存。
- 迁移时只能确认引入代码的 commit，不能恢复精确 build SHA。

## Reopen 条件

满足 frontmatter 中任一条件时可以创建新 EDR 重开，但不得把本记录改回 `RUNNING`：

1. 切换真实 CXL backend。
2. 对 server、client、hot shard 和 CPU worker 做独立绑核或 cpuset 隔离。
3. 使用不 sleep、但必须经过 CPU worker reply 的轻量 workload。
4. 评估目标改为 p50/p99 latency、CPU efficiency 或 cache/NUMA 指标，并补齐相应证据。

## 原始证据

- 结论文档：
  [CXL_MEM_BUSY_POLL_EVENTBASE_VALIDATION_CONCLUSION.md](../../CXL_MEM_BUSY_POLL_EVENTBASE_VALIDATION_CONCLUSION.md)
- 原文保存了 A/B 参数、QPS 汇总、线程级 syscall 数据和基本命令。
- 原始 trace/log artifact 未耐久保存，这是本记录置信度不能设为 high 的原因。
