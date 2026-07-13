# CXL.mem Busy-Poll EventBase 验证结论

日期：2026-07-07

本文记录当前 `fbthrift` perf benchmark 中 CXL.mem busy-poll `EventBase` 的 A/B 验证结论。本文所说的“打平条件”是指 server/client/workload/参数保持一致，只切换 CXL hot IO shard 的 `EventBase` backend：`busy-poll backend` 对比默认 `epoll backend`。

## 总结

当前实现可以在 CXL hot IO thread 上实现 `epoll_wait` family 为 0 的热路径 loop。在线程级 `strace` 采样中，`--cxl_mem_hot_busy_poll_eventbase=true` 时 `cxl_hot_0` 线程没有出现 `epoll_wait` / `epoll_pwait` / `epoll_pwait2`。

在本轮打平条件下，busy-poll backend 没有观察到吞吐收益。`sum` 路径基本打平；会触发 CPU worker reply 的 `timeout` 路径中，busy-poll 反而低于 non-busy。这个结果可以作为“当前打平条件下 busy-poll backend 没有吞吐收益”的证据。

## 验证环境

- 容器：`fbthrift-perf-cpp2-build`
- 镜像：`meta-cpp-shm-transport-dev:ubuntu24.04`
- 源码路径：`/workspace/worktrees/codex-dev/{folly,fbthrift}`
- 构建目录：`/workspace/worktrees/codex-dev/fbthrift/_build/busypoll_ab`
- CXL backend：`--cxl_mem_backend=stub`
- server 固定参数：
  - `--cxl_mem_enable=true`
  - `--cxl_mem_hot_io_threads=1`
  - `--io_threads=1`
  - `--cpu_threads=1`
- 对比开关：
  - `--cxl_mem_hot_busy_poll_eventbase=false`
  - `--cxl_mem_hot_busy_poll_eventbase=true`

除上述对比开关外，A/B 两组保持同一 server/client 参数、同一 workload 和同一 benchmark 二进制。

## 构建和基础验证

本轮重新构建了 Linux 容器内的 `folly` 和 `fbthrift` perf benchmark 目标。最终产物：

- `bin/thrift_perf_cpp2_server`
- `bin/thrift_perf_cpp2_client`
- `bin/CxlMemBenchmarkTransportTest`

基础测试结果：

```text
Running main() from ./googletest/src/gtest_main.cc
[==========] 2 tests from 1 test suite ran. (1 ms total)
[  PASSED  ] 2 tests.
```

运行时依赖中只保留了一份 `glog`：

```text
libglog.so.0 => /workspace/.cache/getdeps/codex-dev/installed/glog-.../lib/libglog.so.0
```

server/client 的 `--helpfull` 能正常启动，说明此前 duplicate `glog` flag 问题不再阻塞 benchmark。

## 代码路径确认

busy-poll hot `EventBase` 的创建路径在：

- `thrift/perf/cpp2/server/Server.cpp`
  - `FLAGS_cxl_mem_hot_busy_poll_eventbase`
  - `cxlMemOptionsFromFlags()`
- `thrift/perf/cpp2/util/CxlMemBenchmarkTransport.cpp`
  - `makeHotIoEventBaseOptions(...)`
  - `CxlMemBenchmarkHotIoShard::run()`
  - `pollEventBaseQueue(...)`
  - `pollWorkerReplyQueue()`
  - `CxlMemBenchmarkPollRegistry::pollOnce()`

关键逻辑：

```cpp
if (options.hotBusyPollEventBase) {
  eventBaseOptions.setBackendFactory(
      [] { return std::make_unique<folly::BusyPollBackend>(); });
  eventBaseOptions.setNotificationQueueMode(
      folly::EventBase::NotificationQueueMode::ManualPoll);
}
```

hot loop 中显式轮询：

```cpp
didWork = drainHandoffs() > 0;
didWork = pollEventBaseQueue(evb) || didWork;
didWork = pollWorkerReplyQueue() || didWork;
didWork = pollRegistry_.pollOnce() > 0 || didWork;
evb.loopPoll();
```

这说明 `runInEventBaseThread()` 对应的 `NotificationQueue`、fbthrift worker `ReplyQueue`、CXL transport 自身 poll registry 都已经纳入 hot loop。

## 吞吐对比

### `sum_weight=1`, `max_outstanding_ops=64`

3 轮，每轮取客户端稳态 `TOTAL QPS` 平均值。

| 模式 | reps | avg_of_avg | median_of_avg | 结论 |
| --- | ---: | ---: | ---: | --- |
| busy=false | 3 | 39640.07 | 39649.20 | 基线 |
| busy=true | 3 | 39616.00 | 39577.60 | 基本打平 |

delta：

```text
(39616.00 - 39640.07) / 39640.07 = -0.06%
```

### `sum_weight=1`, `max_outstanding_ops=1`

3 轮，每轮取客户端稳态 `TOTAL QPS` 平均值。

| 模式 | reps | avg_of_avg | median_of_avg | 结论 |
| --- | ---: | ---: | ---: | --- |
| busy=false | 3 | 636.53 | 636.40 | 基线 |
| busy=true | 3 | 634.53 | 634.40 | 基本打平 |

delta：

```text
(634.53 - 636.53) / 636.53 = -0.31%
```

### `timeout_weight=1`, `max_outstanding_ops=64`

2 轮，每轮取客户端稳态 `TOTAL QPS` 平均值。这个路径会执行 sync `timeout()` handler，经过 CPU worker 和 reply queue，能暴露 hot EventBase 的 wakeup/reply 差异。

| 模式 | reps | avg_of_avg | median_of_avg | 结论 |
| --- | ---: | ---: | ---: | --- |
| busy=false | 2 | 7130.60 | 7130.60 | 基线 |
| busy=true | 2 | 6745.70 | 6745.70 | 更低 |

delta：

```text
(6745.70 - 7130.60) / 7130.60 = -5.40%
```

## syscall 验证

`strace` 命令范围：

```text
strace -ff -e trace=epoll_wait,epoll_pwait,epoll_pwait2
```

### `sum_weight=1`

线程级采样：

| 模式 | 线程 | epoll wait family 次数 |
| --- | --- | ---: |
| busy=false | `cxl_hot_0` | 0 |
| busy=true | `cxl_hot_0` | 0 |

这个结果说明 `sum` 路径本身不适合证明 busy-poll 收益。`sum` 对应 `BenchmarkHandler::async_eb_sum()`，直接在 IO EventBase 上 `callback->result()`，没有经过 CPU worker reply queue，也没有迫使 hot shard 进入 fd wakeup 路径。

### `timeout_weight=1`

线程级采样：

| 模式 | 线程 | epoll wait family 次数 |
| --- | --- | ---: |
| busy=false | `cxl_hot_0` | 83654 |
| busy=true | `cxl_hot_0` | 0 |

这个结果是本轮最关键的 syscall 结论：只要 workload 会触发 worker reply 或 EventBase queue，non-busy hot shard 会落回 `epoll_pwait`；busy-poll hot shard 可以把该线程上的 `epoll_wait` family 降到 0。

process-wide 采样不适合作为 hot path 结论，因为普通 Thrift IO、acceptor 和主线程仍然使用原有 epoll backend。必须按 TID 映射到 `cxl_hot_0` 后再判断。

## 为什么打平条件下没有吞吐收益

当前结果不否定 busy-poll 设计，但在本轮打平条件下，busy-poll backend 没有表现出正向吞吐收益。主要原因：

1. `sum` 是 `async_eb_sum()`，直接在 hot EventBase 上完成，基本不经过 worker reply queue，因此没有可消除的 epoll wait。
2. `timeout` 虽然能证明 busy-poll 把 `cxl_hot_0` 的 epoll wait 变成 0，但 handler 内部固定 sleep，吞吐主要受 sleep、worker 调度和 CPU 竞争影响。
3. 本轮没有做 core pinning、isolated CPU、NUMA 绑定，也没有把 server、client、hot IO shard 和 CPU worker 隔离到互不竞争的执行资源上。
4. 当前 backend 的等待成本不够高时，busy loop 消除 syscall 的收益会被额外 CPU 占用抵消。
5. 在 server 和 client 共用执行资源的情况下，benchmark 更容易测到调度竞争，而不是热路径 syscall 消除收益。

## 结论

当前代码已经满足核心功能目标：

- socket listen、accept、握手仍走普通 Thrift socket 路径。
- CXL 握手后 handoff 到独立 `cxl_hot_0` hot IO shard。
- `--cxl_mem_hot_busy_poll_eventbase=true` 时 hot shard 使用 `BusyPollBackend + ManualPoll`。
- hot loop 显式轮询 `NotificationQueue`、`ReplyQueue` 和 CXL transport poll registry。
- 在会触发 worker reply 的 workload 中，`cxl_hot_0` 线程的 `epoll_wait` family 计数为 0。

在本轮打平条件下，busy-poll backend 没有表现出吞吐收益：

- `sum` 路径 busy/non-busy 基本打平。
- `timeout` 路径 busy-poll 反而更低。

因此，当前阶段应把本结果定义为：

```text
功能验证通过；hot CXL thread 0 epoll wait 目标验证通过；
本轮打平条件下 busy-poll backend 未观察到吞吐收益。
```

## 下一步建议

如果目标是证明收益，后续性能评估需要更贴近真实热路径：

1. server、client、hot IO shard、CPU worker 分别 pin 到独立 core。
2. server 和 client 分离到不同进程隔离组，最好分离到不同 host 或至少不同 cpuset。
3. 增加内部计数器，直接在 `BusyPollBackend::eb_event_base_loop()`、`CxlMemBenchmarkHotIoShard::pollWorkerReplyQueue()`、`CxlMemBenchmarkPollRegistry::pollOnce()` 记录每轮 work/no-work 次数。
4. 增加 hot thread 启停阶段 marker，避免 `strace` 把启动/停止阶段和稳态数据面混在一起。
5. 使用不 sleep、但必须经过 CPU worker reply 的轻量 handler，单独测 reply queue wakeup 消除收益。
6. 在真实或更接近真实的 CXL queue backend 上评估，关注 p50/p99 latency、syscall count、CPU util、cache miss 和 QPS，而不是只看单一吞吐。

## 基本命令

server：

```bash
bin/thrift_perf_cpp2_server \
  --logtostderr=1 \
  --port=19080 \
  --io_threads=1 \
  --cpu_threads=1 \
  --stats_interval_sec=1 \
  --terminate_sec=14 \
  --cxl_mem_enable=true \
  --cxl_mem_backend=stub \
  --cxl_mem_path_prefix=/tmp/fbthrift_cxl_mem_ab \
  --cxl_mem_hot_io_threads=1 \
  --cxl_mem_hot_busy_poll_eventbase=true
```

client：

```bash
bin/thrift_perf_cpp2_client \
  --logtostderr=1 \
  --host=127.0.0.1 \
  --port=19080 \
  --transport=cxl_mem \
  --num_clients=1 \
  --max_outstanding_ops=64 \
  --stats_interval_sec=1 \
  --terminate_sec=6 \
  --sum_weight=1 \
  --cxl_mem_backend=stub \
  --cxl_mem_path_prefix=/tmp/fbthrift_cxl_mem_ab
```

线程级 syscall 验证时必须把 trace 文件 TID 映射到 `/proc/<server-pid>/task/<tid>/comm`，只检查 `cxl_hot_0`，不要使用 process-wide 总计判断 hot path。
