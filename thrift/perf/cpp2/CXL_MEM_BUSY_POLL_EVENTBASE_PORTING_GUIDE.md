# CXL.mem Busy-Poll EventBase 移植方案

本文档面向内部重开发场景：`folly` 与 `fbthrift` 的基础代码结构基本一致，但 CXL.mem 数据面、资源管理、硬件队列和握手实现可能不同。因此本文把 **北向 CXL 能力** 抽象成可替换接口，把 **南向 folly/fbthrift 接入点** 精确到源码文件、类和函数。

路径约定：

- `fbthrift` 源码路径相对当前 `fbthrift` 仓库根目录。
- `folly` 源码路径相对配套的 `folly` 仓库根目录。
- 当前 benchmark 参考实现位于 `fbthrift/thrift/perf/cpp2/`，但内部产品化时不要求沿用 benchmark 的目录结构。

## 目标

目标是在 CXL.mem 热路径 IO thread 上实现 fd-free busy-poll `EventBase`：

- socket listen、accept、握手和普通 Rocket socket 路径保持原有 epoll backend。
- socket 只作为 CXL 控制面，用于识别连接、交换 CXL 资源描述、分配 `connId`。
- 握手完成后，CXL transport 交给独立 hot IO shard。
- hot IO shard 使用 `BusyPollBackend + ManualPoll`，稳定数据面不调用 `epoll_wait` / `epoll_pwait` / `epoll_pwait2`。
- `runInEventBaseThread()`、fbthrift `ReplyQueue`、CXL transport inbound/outbound poll 都必须纳入显式轮询。

本文档不要求内部 CXL transport 复用当前 benchmark 的 stub backend 或共享内存文件布局；只要求 transport 满足 fd-free、可轮询、可交给 Rocket server pipeline 的语义。

## 架构边界

```text
普通 socket 路径:
  ThriftServer
    -> acceptPool_ / ioThreadPool_
    -> DefaultThriftAcceptorFactory
    -> Cpp2Worker on default epoll EventBase
    -> AsyncSocket / Rocket

CXL hot 路径:
  socket accept / CXL handshake on normal path
    -> close or detach socket
    -> CxlHotIoGroup submit(handoff)
    -> CxlHotIoShard owns busy EventBase
    -> Cpp2Worker on busy EventBase
    -> fd-free CXL AsyncTransport
    -> RocketRoutingHandler::handleConnection(...)
```

不要把 CXL hot shard 接回默认 `ioThreadPool_`。默认 IO pool 的 `EventBase` 仍然需要 epoll，用来服务 socket accept、普通 socket transport 和现有 fbthrift 机制。CXL hot shard 是一个额外 IO 域，必须独立表达和计数。

## 北向抽象接口

内部 CXL 实现可以不同，但需要提供以下抽象能力。

### CXL 控制面握手

握手结果至少要能生成一个 handoff descriptor：

```cpp
struct CxlHotHandoff {
  uint16_t connId;
  CxlEndpointResources resources;
  folly::SocketAddress peerAddress;
  wangle::TransportInfo transportInfo;
};
```

要求：

- `connId` 能稳定映射到 hot shard：`connId % shardCount`。
- `resources` 包含创建 fd-free CXL transport 所需的所有内存、队列、doorbell、lane 或硬件上下文。
- socket 上的 pre-received handshake 数据必须在交给普通 Rocket handler 前被 CXL routing handler 消费掉。
- CXL handoff 成功后，原 socket 不再进入 hot data path。

当前 benchmark 的参考点：

- `thrift/perf/cpp2/util/CxlMemBenchmarkTransport.cpp`
  - `CxlMemBenchmarkRoutingHandler::canAcceptConnection(...)`
  - `CxlMemBenchmarkRoutingHandler::handleConnection(...)`
  - `CxlMemBenchmarkHandoff`
  - `decodeHandshake(...)`

### CXL fd-free transport

内部 transport 必须实现或适配 `folly::AsyncTransport`，并提供可内联轮询的数据面入口：

```cpp
class CxlHotAsyncTransport : public folly::AsyncTransport {
 public:
  bool pollOnceInline() noexcept;
  void flushPendingWrites();
  void closeNow() override;
};
```

要求：

- 不向 hot `EventBase` 注册 socket fd、eventfd、timerfd、pipe fd。
- inbound poll 能从 CXL doorbell/completion queue 或共享内存状态中发现新数据。
- outbound flush 能推进 pending write，不依赖 `EventHandler::WRITE` readiness。
- 错误时关闭连接，不尝试透明回退到 socket。

当前 benchmark 的参考点：

- `CxlMemBenchmarkAsyncTransport::pollOnceInline()`
- `CxlMemBenchmarkPollRegistry::pollOnce()`
- `makeOwnedTransport(..., CxlMemBenchmarkPollMode::INLINE, &pollRegistry_)`

内部实现可以替换 `CxlMemBenchmarkAsyncTransport` 的资源布局、doorbell queue 和 payload 管理，但应保留 `pollOnceInline()` 这种 hot loop 入口。

### Hot IO pool

建议把当前 benchmark 的 `CxlMemBenchmarkHotIoGroup` / `CxlMemBenchmarkHotIoShard` 产品化为内部组件，例如：

```cpp
class CxlHotIoGroup {
 public:
  bool submit(CxlHotHandoff handoff);
  void stop();
  size_t numHotShards() const;
};

class CxlHotIoShard {
 public:
  void run() noexcept;
  bool submit(CxlHotHandoff handoff);
  void stop();
};
```

注意命名：不要把 `CxlHotIoGroup` 混称为 `ThriftServer` 默认 IO worker pool。它是额外 hot IO domain。

## folly 南向接口

### `BusyPollBackend`

新增文件：

- `folly/folly/io/async/BusyPollBackend.h`
- `folly/folly/io/async/BusyPollBackend.cpp`

核心类：

```cpp
class BusyPollBackend : public EventBaseBackendBase {
 public:
  event_base* getEventBase() override { return nullptr; }

  int eb_event_base_loop(int flags) override;
  int eb_event_base_loopbreak() override;
  int eb_event_add(Event& event, const struct timeval* timeout) override;
  int eb_event_del(Event& event) override;
  bool eb_event_active(Event& event, int res) override;
};
```

行为要求：

- `getPollableFd()` 保持 `EventBaseBackendBase` 默认值 `-1`。
- `eb_event_base_loop(EVLOOP_NONBLOCK)` 必须快速返回，不能在 backend 内部无限 busy spin，否则 `EventBase::loopPoll()` 外层无法继续 drain queue。
- 只支持 timer event 和 active callback。
- 非 timer fd event add 返回 `ENOTSUP`，用于早发现误把 socket/eventfd 注册进 hot backend 的错误。
- timer 使用用户态 `steady_clock` 管理，不引入 `timerfd`。

当前实现参考：

- `BusyPollBackend::eb_event_base_loop(...)`
- `BusyPollBackend::eb_event_add(...)`
- `BusyPollBackend::processExpiredTimers()`

### `EventBase` manual notification queue

修改文件：

- `folly/folly/io/async/EventBase.h`
- `folly/folly/io/async/EventBase.cpp`

新增 API：

```cpp
enum class NotificationQueueMode { FdWakeup, ManualPoll };

struct EventBase::Options {
  Options& setNotificationQueueMode(NotificationQueueMode mode);
};

NotificationQueueMode getNotificationQueueMode() const;
bool pollNotificationQueue();
```

行为要求：

- 默认保持 `FdWakeup`，普通 socket 路径行为不变。
- 只有 CXL hot `EventBase` 显式设置 `ManualPoll`。
- `pollNotificationQueue()` 只能在 EventBase 线程调用，用于 drain `runInEventBaseThread()` 队列。
- `initNotificationQueue()` 根据 `notificationQueueMode_` 构造 queue wakeup mode。

当前实现参考：

- `EventBase::Options::setNotificationQueueMode(...)`
- `EventBase::pollNotificationQueue()`
- `EventBase::initNotificationQueue()`

### `EventBaseAtomicNotificationQueue` manual wakeup

修改文件：

- `folly/folly/io/async/EventBaseAtomicNotificationQueue.h`
- `folly/folly/io/async/EventBaseAtomicNotificationQueue-inl.h`

新增 API：

```cpp
enum class WakeupMode { FdWakeup, ManualPoll };

explicit EventBaseAtomicNotificationQueue(
    Consumer&& consumer,
    WakeupMode wakeupMode = WakeupMode::FdWakeup);
```

`ManualPoll` 行为要求：

- 构造时不创建 eventfd。
- eventfd 不可用时也不创建 pipe。
- `putMessage()` / `tryPutMessage()` 只入队，不调用 `notifyFd()`。
- `startConsuming()` / `startConsumingInternal()` 只记录 `evb_`，不注册 `EventHandler`。
- 消费端必须显式调用 `drain()` 或上层封装的 `pollNotificationQueue()`。

当前实现参考：

- `EventBaseAtomicNotificationQueue::EventBaseAtomicNotificationQueue(...)`
- `EventBaseAtomicNotificationQueue::putMessage(...)`
- `EventBaseAtomicNotificationQueue::tryPutMessage(...)`
- `EventBaseAtomicNotificationQueue::startConsumingImpl(...)`
- `EventBaseAtomicNotificationQueue::execute()`

### folly 测试

新增测试：

- `folly/folly/io/async/test/BusyPollBackendTest.cpp`

至少覆盖：

- 默认 `EventBase` 在 epoll 平台保持 pollable backend。
- busy backend `getPollableFd() == -1`。
- manual `runInEventBaseThread()` 入队后不会自动执行，调用 `pollNotificationQueue()` 后执行。

构建系统：

- `folly/CMakeLists.txt` 增加 `io_async_busy_poll_backend_test`。

## fbthrift 南向接口

### 普通路径中的 `Cpp2Worker`

普通 socket 路径不是由 CPU worker pool 创建 `Cpp2Worker`。创建链是：

```text
ThriftServer::setup()
  -> ioThreadPool_->setNumThreads(getNumIOWorkerThreads())
  -> ServerBootstrap::group(acceptPool_, ioThreadPool_)
  -> DefaultThriftAcceptorFactory::newAcceptor(eventBase)
  -> Cpp2Worker::create(server_, eventBase)
```

源码参考：

- `thrift/lib/cpp2/server/ThriftServer.cpp`
  - `ThriftServer::setup()`
  - `ioThreadPool_->setNumThreads(nWorkers)`
  - `ServerBootstrap::group(acceptPool_, ioThreadPool_)`
- `thrift/lib/cpp2/server/ThriftServer.h`
  - `ThriftAcceptorFactory::newAcceptor(...)`
  - `using DefaultThriftAcceptorFactory = ThriftAcceptorFactory<Cpp2Worker, void>`
- `thrift/lib/cpp2/server/Cpp2Worker.h`
  - `Cpp2Worker::create(...)`
  - `Cpp2Worker::init(...)`

`setNumCPUWorkerThreads()` 影响的是 handler execution 的 `ThreadManager` / `ResourcePool`，不是 `Cpp2Worker` 创建数量。

### CXL hot path 中的 `Cpp2Worker`

CXL hot shard 绕过了 `ServerBootstrap` 和默认 `ioThreadPool_`，因此需要在自己的 busy `EventBase` 上创建并持有 `Cpp2Worker`：

```cpp
folly::EventBase evb(makeHotIoEventBaseOptions(options_));
worker_ = Cpp2Worker::create(&server_, &evb);
rocketHandler_ = findRocketHandler(&server_);
```

当前源码参考：

- `thrift/perf/cpp2/util/CxlMemBenchmarkTransport.cpp`
  - `makeHotIoEventBaseOptions(...)`
  - `CxlMemBenchmarkHotIoShard::run()`
  - member `std::shared_ptr<Cpp2Worker> worker_`

原因：

- `RocketRoutingHandler::handleConnection(...)` 需要 `worker_->getConnectionManager()` 和 `worker_`。
- `Cpp2Connection` / Rocket server connection 需要 `IOWorkerContext`、server context、connection lifecycle 和 request dispatch glue。
- request 真正执行仍然通过同一个 `ThriftServer` 的 `ResourcePool` / `ThreadManager`，CXL hot `Cpp2Worker` 不替代 CPU worker pool。

### `IOWorkerContext::ReplyQueue`

修改文件：

- `thrift/lib/cpp2/server/IOWorkerContext.h`

关键点：

```cpp
auto wakeupMode =
    eventBase.getNotificationQueueMode() ==
        folly::EventBase::NotificationQueueMode::ManualPoll
    ? ReplyQueue::WakeupMode::ManualPoll
    : ReplyQueue::WakeupMode::FdWakeup;
replyQueue_ = std::make_unique<ReplyQueue>(
    ReplyInfoConsumer(), wakeupMode);
```

原因：

- fbthrift `IOWorkerContext` 内部有独立 `ReplyQueue`。
- 如果 hot `EventBase` 是 `ManualPoll`，但 `ReplyQueue` 仍默认 `FdWakeup`，它会创建 eventfd 并尝试注册到 `BusyPollBackend`。
- 这个路径会触发 `failed to register event handler ... Operation not supported`，也破坏 hot path fd-free 目标。

### CXL hot EventBase options

当前源码：

- `thrift/perf/cpp2/util/CxlMemBenchmarkTransport.cpp`
  - `makeHotIoEventBaseOptions(...)`

参考实现：

```cpp
folly::EventBase::Options makeHotIoEventBaseOptions(
    const CxlMemBenchmarkOptions& options) {
  folly::EventBase::Options eventBaseOptions;
  if (options.hotBusyPollEventBase) {
    eventBaseOptions.setBackendFactory(
        [] { return std::make_unique<folly::BusyPollBackend>(); });
    eventBaseOptions.setNotificationQueueMode(
        folly::EventBase::NotificationQueueMode::ManualPoll);
  }
  return eventBaseOptions;
}
```

内部版本建议不要把它藏在 CXL transport 内部。应由 hot IO shard 构造 `EventBase` 时显式传入，便于审计 socket 路径不会受影响。

### Hot shard 主循环

当前源码：

- `CxlMemBenchmarkHotIoShard::run()`
- `CxlMemBenchmarkHotIoShard::drainHandoffs()`
- `CxlMemBenchmarkHotIoShard::pollEventBaseQueue(...)`
- `CxlMemBenchmarkHotIoShard::pollWorkerReplyQueue()`
- `CxlMemBenchmarkPollRegistry::pollOnce()`

推荐循环顺序：

```cpp
while (!stopping) {
  bool didWork = drainHandoffs() > 0;
  didWork = evb.pollNotificationQueue() || didWork;
  didWork = drainWorkerReplyQueue(worker) || didWork;
  didWork = pollRegistry.pollOnce() > 0 || didWork;
  evb.loopPoll();
  if (!didWork) {
    cpu_relax_or_pause();
  }
}
```

每一项的作用：

| 步骤 | 作用 | 不做的后果 |
|------|------|------------|
| `drainHandoffs()` | 接收 socket 握手线程交来的 CXL 连接 | 新连接无法进入 hot shard |
| `evb.pollNotificationQueue()` | 执行 `runInEventBaseThread()` 入队函数 | shutdown、connection 操作、跨线程任务卡住 |
| `pollWorkerReplyQueue()` | drain fbthrift worker reply queue | reply 跨线程投递卡住，或 fallback 到 eventfd |
| `pollRegistry.pollOnce()` | 轮询 CXL transport data plane | 收包/发包不前进 |
| `evb.loopPoll()` | 运行 timer/active callback/loop callback | EventBase 内部 callback 不前进 |
| `pause` | 降低空转流水线压力 | 空闲时 CPU pipeline 浪费更高 |

### CXL routing handler

当前源码：

- `CxlMemBenchmarkRoutingHandler::canAcceptConnection(...)`
- `CxlMemBenchmarkRoutingHandler::handleConnection(...)`
- `CxlMemBenchmarkHotIoGroup::submit(...)`

职责：

- 在 normal socket/accept path 上识别 CXL handshake。
- 解析 pre-received handshake。
- 关闭原 socket。
- 构造 handoff。
- 按 `connId % shardCount` 提交到 hot shard。

这个 routing handler 仍然运行在普通 fbthrift socket 路径里，因此它可以使用原来的 epoll `EventBase`。只有 handoff 之后的数据面进入 busy hot shard。

### Hot shard 数量和 `getNumIOWorkerThreads()`

当前 benchmark 行为：

```cpp
size_t shardCount = options_.hotIoThreads;
if (shardCount == 0) {
  shardCount = server_.getNumIOWorkerThreads();
}
```

这只是默认值选择，不代表 CXL hot shard 被加入 `ThriftServer` 默认 IO worker pool。

语义上应区分：

- `ThriftServer::getNumIOWorkerThreads()`：默认 socket IO pool 的配置值。
- `CxlHotIoGroup::numHotShards()`：CXL hot IO domain 的独立线程数。
- `CPU worker threads` / `ResourcePool worker count`：handler execution capacity。

内部产品化时建议新增独立指标：

```text
socket_io_threads = server.getNumIOWorkerThreads()
cxl_hot_io_threads = cxlHotIoGroup.numHotShards()
total_io_threads_for_capacity_planning = socket_io_threads + cxl_hot_io_threads
```

不要直接修改 `getNumIOWorkerThreads()` 让它包含 CXL hot shard，否则普通 socket observer、connection limit、pending connection 计算和 HTTP2 thread 配置会被污染。

## 迁移步骤

### 阶段 1：先迁移 folly fd-free EventBase 能力

1. 增加 `BusyPollBackend`。
2. 给 `EventBase` 增加 `NotificationQueueMode` 和 `pollNotificationQueue()`。
3. 给 `EventBaseAtomicNotificationQueue` 增加 `WakeupMode::ManualPoll`。
4. 增加 busy backend/manual queue 单测。
5. 验证默认 socket `EventBase` 仍使用原 backend。

建议提交：

```bash
git add folly/io/async/BusyPollBackend.* \
        folly/io/async/EventBase.* \
        folly/io/async/EventBaseAtomicNotificationQueue* \
        folly/io/async/test/BusyPollBackendTest.cpp \
        CMakeLists.txt
git commit -m "Add busy-poll EventBase backend"
```

### 阶段 2：让 fbthrift internal queues 支持 ManualPoll

1. 修改 `IOWorkerContext::init(...)`。
2. 根据 `eventBase.getNotificationQueueMode()` 创建 `ReplyQueue`。
3. 确认默认 `FdWakeup` 路径保持不变。

建议单独验证普通 Rocket socket smoke，避免误伤默认路径。

### 阶段 3：新增 CXL hot IO group/shard

1. 新增或改造 CXL routing handler，只在 socket path 上做 handshake。
2. 新增 hot group，管理多个 hot shard。
3. hot shard 内创建 busy/manual `EventBase`。
4. hot shard 内创建 `Cpp2Worker::create(&server, &evb)`。
5. handoff 后构造 fd-free CXL transport。
6. 调用 `RocketRoutingHandler::handleConnection(...)` 接入 Rocket。
7. 主循环显式轮询 handoff queue、EventBase queue、ReplyQueue、transport poll registry。

### 阶段 4：加配置开关

配置项建议：

```text
cxl_hot_io_threads
cxl_hot_busy_poll_eventbase
cxl_hot_spin_pause_iterations
cxl_handoff_queue_capacity
```

要求：

- 默认 socket transport 不读取这些配置。
- 只有 CXL routing handler/hot shard 读取。
- 支持关闭 busy backend 回退到普通 EventBase，用于 A/B 和调试。

## 验证方案

### 编译与单测

folly：

```bash
cmake --build <folly-build-dir> --target io_async_busy_poll_backend_test -j2
ctest --test-dir <folly-build-dir> -R io_async_busy_poll_backend_test --output-on-failure
```

fbthrift：

```bash
cmake --build <fbthrift-build-dir> \
  --target thrift_perf_cpp2_server thrift_perf_cpp2_client CxlMemBenchmarkTransportTest -j2
<fbthrift-build-dir>/bin/CxlMemBenchmarkTransportTest
```

### Socket 路径 smoke

验证普通 Rocket socket 仍可用：

```bash
thrift_perf_cpp2_server --transport 默认 socket 参数 ...
thrift_perf_cpp2_client --transport=rocket --sum_weight=1 ...
```

要求：

- client/server 正常退出。
- 有 `Operation: sum` QPS。
- 不需要也不应该出现 CXL hot shard 日志。

### CXL 路径 smoke

验证 CXL handoff 和 hot shard 可用：

```bash
thrift_perf_cpp2_server \
  --cxl_mem_enable=true \
  --cxl_mem_hot_io_threads=1 \
  --cxl_mem_hot_busy_poll_eventbase=true \
  ...

thrift_perf_cpp2_client \
  --transport=cxl_mem \
  --sum_weight=1 \
  ...
```

要求：

- client/server 正常退出。
- 有 `Operation: sum` QPS。
- server/client 日志无：
  - `failed to register event handler`
  - `Operation not supported`
  - `CXL mem benchmark hot IO shard failed`
  - `Check failed`
  - `FATAL`

### syscall 验证

用 `strace -ff` 包住 server，定位 `cxl_hot_N` 线程对应 trace 文件。

只对 hot thread 文件检查：

```bash
grep -E 'epoll_(wait|pwait|pwait2)' /tmp/trace.<hot_tid>
```

要求：

- hot thread `epoll_wait` / `epoll_pwait` / `epoll_pwait2` 计数为 0。
- 全进程其它线程可以有 epoll 调用，因为 accept/socket/default IO pool 仍走 epoll。

如果做全 syscall trace，注意区分阶段：

- 线程启动阶段可能有 `rseq`、`set_robust_list`、`prctl(PR_SET_NAME)`。
- CXL resource setup 可能有 `openat`、`mmap`、`mprotect`。
- teardown 可能有 `munmap`、`close`。
- 稳定 hot data loop 不应出现 epoll wait 类 syscall；更严格的 0-syscall 稳态验证需要容器允许 `ptrace attach` 或在程序内加阶段 marker/counter。

## 失败模式与排查

| 症状 | 高概率原因 | 排查点 |
|------|------------|--------|
| `failed to register event handler ... Operation not supported` | 某个 queue 仍是 `FdWakeup`，试图向 `BusyPollBackend` 注册 eventfd | 检查 `EventBase` queue mode 和 `IOWorkerContext::ReplyQueue` wakeup mode |
| CXL client 有握手但无请求 QPS | handoff 未 drain 或 transport 未注册到 poll registry | 检查 `drainHandoffs()`、`pollRegistry_.add()`、`pollRegistry_.pollOnce()` |
| `runInEventBaseThread()` 回调不执行 | hot loop 未调用 `pollNotificationQueue()` | 检查 loop 顺序 |
| reply 卡住 | 未 drain `worker_->getReplyQueue()` | 检查 `pollWorkerReplyQueue()` |
| 普通 socket 路径异常 | busy/manual options 泄漏到默认 `EventBase` | 检查只有 CXL hot shard 调用 `makeHotIoEventBaseOptions()` |
| hot thread 有 epoll wait | fd event 被注册进 hot EventBase，或没有使用 BusyPollBackend | 检查 backend factory、trace hot TID、`BusyPollBackend::eb_event_add()` 日志 |

## 推荐内部重构形态

benchmark 代码可以继续内联在 `CxlMemBenchmarkTransport.cpp`，但内部产品化建议拆成：

```text
folly:
  BusyPollBackend
  EventBase manual queue support
  CXL transport / queue / region implementation

fbthrift:
  CxlRoutingHandler
  CxlHotIoGroup
  CxlHotIoShard
  CxlRocketHandoff
  CxlHotTransportFactory
```

其中 `CxlHotTransportFactory` 是北向泛化接口，封装内部 CXL 资源差异：

```cpp
class CxlHotTransportFactory {
 public:
  virtual folly::AsyncTransport::UniquePtr createServerTransport(
      folly::EventBase* evb,
      CxlHotHandoff handoff,
      CxlHotPollRegistry* registry) = 0;
};
```

fbthrift 南向只依赖：

- `folly::EventBase`
- `folly::BusyPollBackend`
- `folly::EventBaseAtomicNotificationQueue`
- `apache::thrift::Cpp2Worker`
- `apache::thrift::RocketRoutingHandler`
- `apache::thrift::TransportRoutingHandler`

CXL 资源如何分配、doorbell 如何编码、硬件 queue 如何 poll，全部留在 factory/transport 内部。

## 当前实现提交

参考分支：

- `folly`: `agent/codex/cxl-mem-rocket-benchmark`
- `fbthrift`: `agent/codex/cxl-mem-rocket-benchmark`

参考提交：

- `folly`: `b0f2ac351 Add busy-poll EventBase backend`
- `fbthrift`: `ab6959f9b5 Use busy-poll EventBase for CXL hot shards`
