# SHM Busy-Poll Transport 设计文档

本文档描述 `feature/shm-busypoll-transport` 分支在 folly 和 fbthrift 两个仓库中的修改，
涵盖 Transport 层注入机制、轮询模式设计、环形缓冲区实现及握手协议。

---

## 1. 整体架构

```
Client                              Server
  │                                    │
  ├─ Unix Socket ─────────────────────┤  (仅用于 SHM 握手)
  │  交换 shm 区域名 + GQM 队列名        │
  │                                    │
  │         ── 关闭 socket ──           │
  │                                    │
  ├─ SharedMemoryRegion A ────────────┤  (Client写 → Server读)
  ├─ SharedMemoryRegion B ────────────┤  (Server写 → Client读)
  │                                    │
  ├─ GQM Queue (push/pop) ────────────┤  (纯用户态原子操作通知)
  │                                    │
  └─ BusyPollSharedMemoryTransport ───┘  (NAPI-style 自适应轮询)
          │
          ▼
    Rocket 协议帧 (SETUP, REQUEST_RESPONSE, ...)
```

核心设计思路：**先在原始 socket 上完成 SHM 握手，关闭 socket，然后 Rocket 协议的所有帧都走共享内存**。

---

## 2. fbthrift: Transport 层注入

### 2.1 连接接受与注入时序

正常的 fbthrift 连接接受流程：

1. `AsyncServerSocket` accept → 拿到 socket fd
2. `TransportPeekingManager` peek 前 13 字节判断协议（Header/Rocket/HTTP2）
3. 进入 `handleHeader` → 调用 `createThriftTransport` 构造传输层
4. 创建 `Cpp2Connection`，后续走 Rocket 解帧逻辑

SHM 修改的注入点在 **第 3 步**：`Cpp2Worker::createThriftTransport` 内部在构造普通
`AsyncSocket` transport 之前，优先尝试 SHM 升级。

### 2.2 服务端注入：`Cpp2Worker::createThriftTransport`

```cpp
// thrift/lib/cpp2/server/Cpp2Worker.cpp
std::shared_ptr<folly::AsyncTransport> Cpp2Worker::createThriftTransport(
    folly::AsyncTransport::UniquePtr sock) {
  if (server_ && server_->getUseShmTransport()) {
    try {
      folly::BusyPollSharedMemoryTransport::Config shmConfig;
      shmConfig.pollingMode =
          folly::BusyPollSharedMemoryTransport::PollingMode::ADAPTIVE;
      auto shmResult = folly::shmHandshakeServer(
          getEventBase(), sock.get(), shmConfig);
      auto shmTransport = folly::BusyPollSharedMemoryTransport::create(
          getEventBase(),
          std::move(shmResult.writeRegion),
          std::move(shmResult.readRegion),
          std::move(shmResult.gqmWrite),
          std::move(shmResult.gqmRead),
          shmConfig);
      return convertToShared(UniquePtr(shmTransport.release()));
    } catch (const std::exception& ex) {
      FB_LOG(ERROR) << "SHM handshake failed, falling back to TCP: "
                    << ex.what();
    }
  }
  // 正常的 AsyncSocket / AsyncFdSocket 路径 ...
}
```

`shmHandshakeServer` 内部会用 `syncRead`/`syncWrite` 在原始 socket 上完成帧交换，
最后 `sock->close()` 关闭原始 socket。返回的 `ShmHandshakeResult` 包含打开好的
共享内存区域和 GQM 队列。失败时会 catch 异常并 **回退到普通 TCP**。

### 2.3 客户端注入：`newShmClient`

```cpp
// thrift/perf/cpp2/util/Util.h
template <typename AsyncClient>
static std::unique_ptr<AsyncClient> newShmClient(
    folly::EventBase* evb, const folly::SocketAddress& addr) {
  // 1. 连接 Unix socket (仅用于握手)
  auto sock = apache::thrift::perf::getSocket(evb, addr, false);
  // 2. SHM 握手
  auto shmResult = folly::shmHandshakeClient(evb, sock.get(), shmConfig);
  // 3. 创建 SHM transport
  auto transport = folly::BusyPollSharedMemoryTransport::create(...);
  // 4. 包装为 RocketClientChannel
  auto chan = RocketClientChannel::newChannel(
      folly::AsyncTransport::UniquePtr(transport.release()));
  return std::make_unique<AsyncClient>(std::move(chan));
}
```

客户端握手完成后直接把 SHM transport 传给 `RocketClientChannel::newChannel`。
Rocket 的 SETUP frame 会作为第一个 RPC 的 prepend 发出。

### 2.4 SHM 握手与 Rocket 握手的关系

| 阶段 | 协议 | 传输载体 |
|------|------|----------|
| TCP/Unix 连接建立 | 三次握手 | 内核网络栈 |
| SHM 握手 | 自定义帧协议（magic + version + shm 名 + GQM 名） | 原始 socket |
| 关闭原始 socket | — | — |
| Rocket SETUP | RSocket SETUP frame | **BusyPollSharedMemoryTransport** |
| 业务 RPC | Rocket REQUEST_RESPONSE 等 | **BusyPollSharedMemoryTransport** |

**SHM 握手和 Rocket 握手是两个完全独立的协议，SHM 先于 Rocket 完成。**
Rocket 看到的只是一个实现了 `AsyncTransport` 接口的对象，感知不到底层是 socket
还是共享内存。Rocket 代码中 `RocketServerConnection::handleFrame` 要求第一个帧
必须是 `FrameType::SETUP`，由于 SHM transport 实现了完整的 `AsyncTransport` 接口，
Rocket 的帧解析和序列化完全不需要修改。

### 2.5 潜在风险

1. **Peek 阶段的数据冲突**：Plaintext accept 会 peek 前 13 字节。如果 SHM 握手的
   客户端在 peek 之前就发了 SHM 帧（magic `0x53484D54`），peeking manager 可能
   无法识别，导致路由异常
2. **`AsyncSocket` 特定 API**：`RocketClient` 构造时 `dynamic_cast<folly::AsyncSocket*>`
   设置 `setCloseOnFailedWrite(false)`。SHM transport 不是 `AsyncSocket`，这个优化
   不会生效（影响较小）
3. **不支持 TLS 路径**：当前设计只走 plaintext Unix socket + SHM，与 TLS/ALPN 路由
   不兼容

---

## 3. folly: 轮询模式设计

### 3.1 四种轮询模式对比

| 模式 | 线程 | CPU 占用 | 尾延迟 | 适用场景 |
|------|------|----------|--------|----------|
| **BUSY_POLL** | 独立线程死循环 | 100% 一个核 | 最低（纳秒级） | 对延迟极度敏感，可以牺牲 CPU |
| **ADAPTIVE** | 独立线程 + 自适应休眠 | 高负载时高，空闲时低 | 空闲→忙：~100μs | 大多数场景的推荐默认值 |
| **DEDICATED_CORE** | 独立线程 + CPU 绑核 | 100% 指定核 | 最低且最稳定 | 有专用核隔离的生产环境 |
| **EVENTBASE** | 无额外线程 | 取决于 EventBase 负载 | 取决于 loop 周期 | 资源受限或连接数多的场景 |

### 3.2 BUSY_POLL — 纯忙轮询

```cpp
void pollerLoopBusyPoll() {
  while (pollerRunning_.load(std::memory_order_relaxed)) {
    pollCycles_++;
    pollAndDeliver();
    __builtin_ia32_pause();   // x86: 减少流水线惩罚
    // asm volatile("yield"); // aarch64
  }
}
```

独立线程无限循环调用 `pollAndDeliver()`，每次循环用 `pause`/`yield` 指令减少
CPU 流水线浪费。**不涉及任何 syscall**，延迟完全取决于 GQM pop 和 memcpy 速度。

### 3.3 DEDICATED_CORE — 绑核忙轮询

与 BUSY_POLL 相同的循环，但在 Linux 上通过 `pthread_setaffinity_np` 将线程
绑定到 `config.pinnedCore` 指定的 CPU 核：

```cpp
cpu_set_t cpuset;
CPU_ZERO(&cpuset);
CPU_SET(config_.pinnedCore, &cpuset);
pthread_setaffinity_np(pthread_self(), sizeof(cpu_set_t), &cpuset);
```

优势：避免线程被调度器迁移到其他核，减少 cache miss，延迟更稳定。

### 3.4 ADAPTIVE — NAPI-style 自适应轮询

这是整个设计中最核心的算法，灵感来自 Linux NAPI（New API）的中断/轮询混合机制。

#### 状态机

```
                      ┌─────────────────────────────┐
                      │                             │
                      ▼                             │
              ┌──────────────┐                      │
              │ pollAndDeliver()│                    │
              └──────┬───────┘                      │
                     │                              │
              found? ├── Yes ──► consecutiveHits++   │
                     │           spinCount = 0       │
                     │                │              │
                     │    consecutiveHits >= highLoadThreshold?
                     │         │              │      │
                     │        Yes            No      │
                     │         │              │      │
                     │    ┌────▼────┐   ┌────▼────┐  │
                     │    │ SPIN    │   │ SPIN    │  │
                     │    │ (pause) │   │ (pause) │  │
                     │    └─────────┘   └─────────┘  │
                     │                               │
                     └── No ──► consecutiveHits = 0  │
                                spinCount++          │
                                      │              │
                          spinCount > spinLimit?     │
                               │            │        │
                              Yes           No ──────┘
                               │
                        ┌──────▼──────┐
                        │   SLEEP     │
                        │ poll(fd,ms) │
                        │ sleepCount++│
                        └──────┬──────┘
                               │
                          spinCount = 0
                               └─────────────────────┘
```

#### 三个关键参数

| 参数 | 默认值 | 含义 |
|------|--------|------|
| `spinLimit` | 1000 | 连续空轮询多少次后进入休眠 |
| `highLoadThreshold` | 10 | 连续命中多少次后进入"高负载"自旋模式 |
| `sleepTimeoutUs` | 100μs | 休眠时的 `poll()` 超时（最小 1ms，受 `poll` 精度限制）|

#### 状态转换逻辑

- 每次 `pollAndDeliver()` 有数据 → `consecutiveHits++`，`spinCount` 归零
- 连续命中达到 `highLoadThreshold` → 进入纯自旋（相当于 BUSY_POLL 行为）
- 任何一次空轮询 → `consecutiveHits` 归零
- 连续空轮询超过 `spinLimit` → 进入 `poll(wakeupFd, timeout)` 休眠
- 休眠结束（超时或被唤醒） → `spinCount` 归零，重新开始自旋

#### 唤醒机制

- Linux 使用 `eventfd`（readFd == writeFd）
- macOS 使用 `pipe`（readFd != writeFd）
- 休眠时通过 `poll(wakeupFd, timeoutMs)` 等待，`stopPollerThread()` 时写
  `wakeupFd` 唤醒

**已知限制**：writer 端的 `signalPeer()` 只 push GQM 通知，不写 `wakeupFd`。
当 ADAPTIVE 模式的 poller 正在 `poll()` 休眠时，新到达的 GQM push 不能立即唤醒它，
必须等到 `sleepTimeoutUs` 超时才能检测到新数据。

### 3.5 EVENTBASE — EventBase 集成轮询

```cpp
class PollLoopCallback : public EventBase::LoopCallback {
  void runLoopCallback() noexcept override {
    transport_.pollAndDeliver();
    if (transport_.evbPollRegistered_ && transport_.state_ == State::CONNECTED)
      transport_.evb_->runInLoop(this);  // 每次 loop 重新注册
  }
};
```

无额外线程，通过 `EventBase::LoopCallback` 在每次 EventBase 事件循环迭代时检查
GQM 通知。`pollAndDeliver()` 在 EventBase 线程上直接执行，数据交付零跨线程开销。

#### EVENTBASE vs 线程模式的区别

| 特性 | 线程模式 | EVENTBASE |
|------|---------|-----------|
| 轮询频率 | 独立于 EventBase，由 spin/sleep 控制 | 每次 EventBase loop 迭代一次 |
| 数据交付 | `runInEventBaseThread()` 跨线程投递 | 直接在 loop callback 中调用，零开销 |
| 额外资源 | 独立 `std::thread`（+ 可能的 eventfd/pipe） | 无额外线程/fd |
| 延迟特性 | 不受 EventBase 上其他 fd/timer 影响 | 受限于 loop 中其他 callback 的执行时间 |

---

## 4. 环形缓冲区（SharedMemoryRegion）

### 4.1 内存布局

```
共享内存 (POSIX shm_open):
┌──────────────────────────────────────────┐
│ Header (sizeof SharedMemoryRegionHeader)  │
│   writeOffset (alignas 64, 独立 cacheline)│
│   readOffset  (alignas 64, 独立 cacheline)│
│   dataSize, flags, readerEventFd          │
├──────────────────────────────────────────┤
│ Data Region (dataSize bytes, 默认 4MB)    │
│   环形缓冲区                               │
└──────────────────────────────────────────┘
```

### 4.2 读写机制

- `writeOffset` 和 `readOffset` 是**单调递增**的 64 位逻辑偏移量
- 物理位置通过 `offset % dataSize` 映射到环形缓冲区
- 可读字节数 = `writeOffset - readOffset`
- 可写字节数 = `dataSize - (writeOffset - readOffset) - 1`（留 1 字节区分满/空）
- 环绕写入时分两段 `memcpy`：先写到 buffer 末尾，再从 buffer 开头写剩余部分
- `writeOffset` 和 `readOffset` 在不同 cacheline 上（`alignas(64)`），
  避免 writer/reader 之间的 false sharing

### 4.3 GQM 通知与环形缓冲的协作

GQM 通知的作用是**纯信号机制**，不携带实际数据内容：

```
Writer 端:
  1. writeRegion_->write(data, len)     ← 数据写入环形缓冲
  2. signalPeer() → gqmWrite_->push()   ← GQM push 通知

Reader 端 (pollAndDeliver):
  1. while (gqmRead_->pop()) { ... }    ← 排空 GQM 通知队列
  2. if (有通知 || availableToRead > 0)
  3.     deliverReadData()               ← 从环形缓冲读数据
```

`pollAndDeliver()` 不依赖 GQM 通知中的 offset/length 字段来定位数据，
**只把 GQM pop 成功当作"有新数据"的信号**。真正的读取位置由 `readOffset` 原子变量
控制。即使 GQM 通知丢失或被合并，只要 `availableToRead() > 0` 就能正确读取数据。

---

## 5. SHM 握手协议

### 5.1 帧格式

```
┌──────────┬──────────┬─────────┬──────────┬──────────────┬──────────────┬──────────┬──────────────┬──────────┐
│ frameLen │  magic   │ version │ nameLen  │ writeShmName │ dataRegionSz │ gqmNmLen │ gqmWriteName │ gqmDepth │
│ 4 bytes  │ 4 bytes  │ 4 bytes │ 4 bytes  │ N bytes      │ 8 bytes      │ 4 bytes  │ M bytes      │ 4 bytes  │
│ (BE)     │0x53484D54│   1     │          │              │              │          │              │          │
└──────────┴──────────┴─────────┴──────────┴──────────────┴──────────────┴──────────┴──────────────┴──────────┘
```

### 5.2 时序图

```
  Client                                Server
    │                                      │
    │  connect (Unix socket)               │
    │─────────────────────────────────────>│
    │                                      │
    │  SHM Handshake Frame                 │
    │  (my writeShmName, gqmWriteName,     │
    │   dataRegionSize, gqmQueueDepth)     │
    │─────────────────────────────────────>│
    │                                      │  parse clientInfo
    │                                      │  build serverInfo
    │  SHM Handshake Frame                 │
    │  (server writeShmName, gqmWriteName) │
    │<─────────────────────────────────────│
    │                                      │
    │  双方各自:                             │
    │  - shm_open 创建自己的写区域           │
    │  - shm_open 打开对方的写区域作为读区域  │
    │  - GQM create/open 同理              │
    │                                      │
    │  close socket                        │
    │──────────────X──────────────────────>│
    │                                      │
    │  === 以下走 SHM transport ===         │
    │                                      │
    │  Rocket SETUP frame                  │
    │═════════════════════════════════════>│
    │                                      │
    │  Rocket 请求/响应                     │
    │<════════════════════════════════════>│
```

### 5.3 命名规则

SHM 区域和 GQM 队列的名称由以下规则生成：

```
{prefix}{c|s}_{timestamp_hex}_{pid}
```

- `prefix`: 配置的前缀（默认 `/thrift_shm_` 或 `/thrift_gqm_`）
- `c|s`: client 或 server
- `timestamp_hex`: `steady_clock` 时间戳的十六进制
- `pid`: 进程 PID（防止容器环境下碰撞）

---

## 6. 修改文件清单

### folly 仓库

| 文件 | 状态 | 功能 |
|------|------|------|
| `folly/io/async/GqmInterface.h` | 新增 | GQM 通知接口抽象 |
| `folly/io/async/GqmInterface.cpp` | 新增 | DefaultGqmInterface + SharedMemoryGqm 实现 |
| `folly/io/async/SharedMemoryRegion.h` | 新增 | 共享内存区域头部定义 + 环形缓冲 API |
| `folly/io/async/SharedMemoryRegion.cpp` | 新增 | POSIX shm 创建/映射 + 环形缓冲读写 |
| `folly/io/async/BusyPollSharedMemoryTransport.h` | 新增 | AsyncTransport 实现，4 种轮询模式 |
| `folly/io/async/BusyPollSharedMemoryTransport.cpp` | 新增 | 轮询线程、数据交付、生命周期管理 |
| `folly/io/async/BusyPollShmHandshake.h` | 新增 | 握手协议定义 |
| `folly/io/async/BusyPollShmHandshake.cpp` | 新增 | Client/Server 握手实现 |
| `folly/io/async/BUCK` | 修改 | 新增 3 个 Buck 构建目标 |

### fbthrift 仓库

| 文件 | 修改内容 |
|------|----------|
| `thrift/lib/cpp2/server/ThriftServer.h` | 新增 `useShmTransport_` 开关和 getter/setter |
| `thrift/lib/cpp2/server/Cpp2Worker.cpp` | `createThriftTransport` 中添加 SHM 升级逻辑 |
| `thrift/perf/cpp2/server/Server.cpp` | 新增 `--shm` flag，自动创建 Unix socket |
| `thrift/perf/cpp2/util/Util.h` | 新增 `newShmClient<>()` 模板函数 |
| `thrift/perf/cpp2/client/Client.cpp` | transport 选项增加 `shm` |
