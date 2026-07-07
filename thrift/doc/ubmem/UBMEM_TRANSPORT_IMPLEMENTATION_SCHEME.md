# ubmem::transport 实现方案与 V1 对齐说明

> 生成日期：2026-07-06
> 用途：项目汇报、方案评审、V1 需求对齐
> 输入：`ubmem::transport` 当前实现总结与 `ub.mem` 用户态双边通信库 V1 需求说明

## 1. 汇报摘要

`ubmem::transport` 当前实现已经从早期 `folly` / `fbthrift` 里的多套 shared-memory transport 形态，收敛为一个独立用户态通信库雏形。它的核心路线是：控制面用 TCP 完成跨机器建链和参数协商，数据面用 ub.mem / CXL shared memory 承载 payload，通知面用 GQM 硬件队列传递 64-bit descriptor。

当前实现和 V1 需求存在口径差异：需求文档偏 Core / Channel / Completion 模型，强调显式 buffer ownership、bounded slot-based atomic message 和两个 e2e adapter proof；实现材料偏工程落地，采用 `Connection` / `Server` / `Client` socket-like Facade，同时保留 `DataRegion`、`Lane`、`Poller`、`GqmQueue` 等可组合原语。这个差异不是方向冲突，而是 API 分层不同：Facade 用来降低用户接入门槛，原语层用来承接性能调优、benchmark 和 RPC adapter。

截至 2026-07-06，原语层、Lane 编排层、GQM/Poller、握手状态机和共享内存布局已经基本完成并有单测覆盖；主要缺口集中在 Facade 全链路组装、Server / Client 接线、独立 benchmark、以及 `netpoll + Kitex` 和 `fbthrift + folly` 两个 e2e adapter proof。

## 2. 总体架构

当前实现采用三层结构：

```text
消费者
  -> Facade: Connection / Server / Client
      -> Lane: 连接注册、写入编排、descriptor 路由、错误回调
          -> Poller: GQM 轮询、connId 路由、回调分发
          -> DataRegion: SlotPool 或 RingRegion 的数据生命周期
          -> GqmQueue: 64-bit descriptor 通知通道
          -> ShmPool / MemoryMapper: ub.mem / CXL shared memory 映射
      -> Handshaker: TCP bootstrap、参数协商、fallback fd 返回
```

这套结构的关键不是“把 socket 重新实现一遍”，而是把用户态通信拆成四类职责：

| 职责 | 模块 | 设计要点 |
|---|---|---|
| 用户接入 | `Connection` / `Server` / `Client` | 提供类 socket 心智，覆盖快速接入和 e2e proof。 |
| 数据生命周期 | `DataRegion` / `SlotPool` / `RingRegion` / `Message` | 管理 claim / commit / acquire / release，避免用户直接操作共享内存状态机。 |
| 通知与进展 | `GqmQueue` / `BusyPoller` / `Lane` | 用 GQM 传 descriptor，用 Poller 做批量 poll 和路由。 |
| 建链与资源 | `Handshaker` / `ShmPool` / `MemoryMapper` | 用 TCP control plane 协商 lane、slot、region、connId 和 fallback。 |

## 3. 模块方案简述

### 3.1 Facade：`Connection` / `Server` / `Client`

**定位**：给普通消费者一个类 socket 的入口，隐藏 Lane、GQM、mapper 和共享内存布局，支撑第一轮 e2e 证明。

**Knowhow**：

- `Connection::write()` 是同步路径，空间不足时自旋等待，适合最小可用链路和高性能 demo。
- `Connection::tryWrite()` 是非阻塞路径，适合事件循环或 adapter 自己管理 backpressure。
- `onData()` 回调返回后自动 release，适合同步处理。
- `onMessage()` 返回 move-only `Message`，通过 RAII 延后 release，适合跨线程处理。
- `ConnectResult` 支持 `conn` 和 `fallbackFd` 双结果：ub.mem 建链失败时可以把原始 fd 交回上层，但库本身不封装 TCP payload fallback。

**当前状态**：`Message` 完整；`Connection` 的 I/O 委托已实现；`connect()` / `createPair()`、`Server`、`Client` 全链路组装仍未完成。

**与 V1 对齐**：Facade 对应需求文档里的 thin `Channel` wrapper。它可以比 `Channel` 更 socket-like，但不能承诺 Core 层没有的语义，例如无界 byte stream、全局 FIFO、自动重传或静默 TCP payload fallback。

### 3.2 Lane：连接与数据面的编排者

**定位**：Lane 是唯一编排者，负责连接表、写入路径、poll event 分发和错误路由。`DataRegion`、`Poller`、`GqmQueue` 都不直接互相知道对方。

**Knowhow**：

- 一个 Lane 绑定一对数据区域、一对 GQM 和一个 Poller，不是每个连接一个 Poller。
- `connId` 完全内部化，由握手协商，不暴露给公共 API。
- Lane 内部使用固定连接表做 `connId -> callback` 路由，避免热路径动态查找成本。
- 写路径是 `claim -> memcpy -> commit -> pushNotification`。
- 读路径是 `pop descriptor -> decode connId -> acquire -> callback -> release`。

**当前状态**：`LaneImpl` 已完成，单测覆盖连接注册、写入、事件处理和错误路径。

**与 V1 对齐**：Lane 是 V1 fixed multi-lane 的基本单元。当前实现符合“per-lane 独立资源”的需求，但还需要在上层补齐 multi-lane attach、lane selection 和 benchmark 记录。

### 3.3 DataRegion：统一数据生命周期

**定位**：`DataRegion` 把数据面统一抽象为四阶段生命周期：

```text
claim -> commit -> acquire -> release
```

**Knowhow**：

- 写端只在 `claim()` 后得到可写地址，`commit()` 后 descriptor 才对对端可见。
- 读端只根据 descriptor `acquire()`，处理完必须 `release()`。
- `DataRegion` 不知道 Poller、Lane、connId，只管理内存状态。
- 这个边界让 Slot Pool 和 Ring Buffer 可以共享上层 Lane / Facade 逻辑。

**当前状态**：接口和两种实现均已完成并有单测。

**与 V1 对齐**：V1 需求主线是 bounded slot-based atomic message，`DataRegion` 能表达该模型；Ring Buffer 应作为可选实验或回退模式，不应改变 V1 的主语义。

### 3.4 Slot Pool：V1 主数据模式

**定位**：固定大小 slot 池，适合高频小包和明确消息边界，是 V1 推荐主线。

**Knowhow**：

- 每个 slot 是一条完整消息的承载单元，descriptor 编码 `connId(16) | slotId(16) | payloadLen(32)`。
- 发送端从本地 FreeList 分配空闲 slot，写入 payload 后 commit。
- 接收端根据 descriptor acquire slot，处理后 release，把 slot 归还到对端 FreeList。
- slot 之间独立，支持 out-of-order release，适合多 consumer 或异步处理。
- 固定 slot size 会牺牲部分空间利用率，但换来 descriptor 简洁、释放简单和 cache 行为稳定。

**当前状态**：`SlotPool`、`ShmFreeList`、slot descriptor 编解码均已完成。

**与 V1 对齐**：与需求里的 bounded slot-based atomic message 最匹配。后续需要补齐 header integrity validation、结构化 backpressure 统计和 e2e benchmark artifact。

### 3.5 Ring Buffer：可选数据模式

**定位**：变长消息模式，适合大块或尺寸波动明显的 payload。

**Knowhow**：

- 通过虚拟内存双重映射让 ring 回绕区域在虚拟地址上连续，降低跨边界读写复杂度。
- descriptor 编码 `connId(16) | offset(32) | length(16)`。
- ring 模式仅支持顺序 release，因为 read cursor 必须按序推进。
- 对用户心智更接近 stream / variable-frame，但不适合表达任意 out-of-order release。

**当前状态**：`RingRegion` 与 `RingMapper` 已完成并有单测。

**与 V1 对齐**：这是实现上的增强能力，但 V1 需求不应被 ring 语义牵引。汇报时建议表述为“已具备变长消息实验路径”，不要把它定义为 V1 主验收面。

### 3.6 ShmPool 与 MemoryMapper：共享内存资源组织

**定位**：统一管理 export pool / import pool、每 lane 子视图、control area 和 mapper 生命周期。

**Knowhow**：

- 每个进程持有 export pool 和 import pool：本端 export 给对端写，本端 import 对端借出的区域来写。
- `ShmPool` 一次性 mmap 全局内存，再按 lane 切分子视图，减少 mmap 次数和硬件映射表压力。
- Slot 模式使用单次映射；Ring 模式使用 header + 双映射数据区。
- Export 端和 Import 端可以使用不同 open flags，适配 `/dev/obmm_shmdev<memid>` 设备语义。

**当前状态**：`ShmPool`、`SlotMapper`、`RingMapper` 和子视图适配已完成。

**与 V1 对齐**：满足 fixed multi-lane attach 的资源基础，但握手到 `ShmPool` 再到 `Lane` 的完整组装还需要接通。

### 3.7 GqmQueue：硬件通知通道

**定位**：封装 ARM Kunpeng GQM 硬件队列，用 64-bit descriptor 通知对端有数据可读。

**Knowhow**：

- GQM 只传 descriptor，不传 payload。
- 硬件队列深度上限约 496，`size()` 只能做 profile，不应作为精确流控依据。
- `ugqm_push` / `ugqm_pop` 返回值包含 error 和 remaining 字段，需要统一解析。
- 每条 Lane 一对 GQM：`txGqm` 通知对端，`rxGqm` 接收对端通知。

**当前状态**：`DefaultGqmQueue` 已完成并有单测。

**与 V1 对齐**：对应需求里的 notification queue / ready event。需求中提到的 reclaim queue 在当前实现中主要由 receiver release 后归还 FreeList 表达，后续需要在文档和 API 上明确它与 `ReclaimCompletion` 的映射关系。

### 3.8 Poller：进展与事件分发

**定位**：从 GQM 批量 pop descriptor，按 connId 分发给 Lane 的连接回调。

**Knowhow**：

- `BusyPoller` 自带线程，支持 batch poll、绑核和三级 idle policy。
- idle policy 是 `HOT spin -> WARM yield -> COLD nanosleep`，收到事件后回到 HOT。
- Poller 不知道 Lane，只通过 callback 交付 `PollEvent`。
- `notifyFd()` 当前返回 `-1`，表示没有 fd 驱动事件源，未来可扩展 `EvbPoller`。

**当前状态**：`BusyPoller` 已完成；`EvbPoller` 未实现。

**与 V1 对齐**：满足 V1 “Core non-blocking poll / benchmark busy-poll / 可用性模式 backoff” 的方向。当前 Facade 使用 callback 交付事件，和需求文档里的 `pollCompletion()` 事件流需要在 API 设计中进一步统一。

### 3.9 Handshake：TCP bootstrap 与降级边界

**定位**：用 TCP 做建链和参数协商，产出共享内存资源信息、connId 和可用性结果。

**Knowhow**：

- 握手帧格式是 `magic | type | length | payload`，magic 为 `UBMH`。
- Hello payload 包含 version、shmName、shmSize、numLanes、laneDataSize、laneControlSize、slotSize、slotCount、gqmQueueDepth、baseConnId 和 dataMode。
- 参数协商中，`numLanes` / `shmSize` 可取 min，`slotSize` / `slotCount` / `gqmQueueDepth` / `dataMode` 必须匹配。
- 失败时可以通过 `ConnectResult::fallbackFd` 交还原始 TCP fd，但 payload fallback 是否启用由上层显式决定。

**当前状态**：`HandshakeStateMachine`、`HandshakerImpl`、`TcpHandshakeChannel` 已完成并有单测。

**与 V1 对齐**：符合“TCP bootstrap control plane”的要求；但 V1 还要求握手后 control connection 保持打开，用于 close / error / heartbeat / 后续降级协商。当前 `fallbackFd` 设计需要进一步明确：它是失败返回给上层的 fd，不等同于库内自动 TCP payload fallback。

### 3.10 Message：安全释放语义

**定位**：对 `ReadView` 做 RAII 包装，解决异步处理时 release 时机不安全的问题。

**Knowhow**：

- `Message` move-only，禁止拷贝，防止 double release。
- 析构自动 release，也允许用户显式提前 release。
- `onData()` 是简单同步路径，`onMessage()` 是更安全的异步路径。

**当前状态**：已完整实现。

**与 V1 对齐**：对应需求里的 buffer ownership 和 completion 前复用约束。后续需要把 `Message` 生命周期和 `RecvCompletion` / `ReclaimCompletion` 的用户心智对齐。

## 4. 数据流汇报口径

### 4.1 发送路径

```text
Connection::write(data, len)
  -> Lane::write(connId, data, len)
  -> DataRegion::claim(len)
  -> memcpy(claim.addr, data, len)
  -> DataRegion::commit(claim)
  -> Poller::pushNotification(connId, descriptor)
  -> GqmQueue::push(64-bit descriptor)
```

一句话说明：发送端把 payload 写入共享内存，只通过 GQM 发送一个 descriptor 通知对端。

### 4.2 接收路径

```text
BusyPoller thread
  -> GqmQueue::pop()
  -> decode connId + descriptor
  -> Lane::handlePollEvent()
  -> DataRegion::acquire(descriptor)
  -> onData() or onMessage()
  -> DataRegion::release()
```

一句话说明：接收端通过 descriptor 找到完整消息视图，业务处理完成后 release 资源。

### 4.3 建链路径

```text
TCP connected
  -> client/server exchange Hello
  -> negotiate lane / shm / slot / GQM parameters
  -> map export/import ShmPool
  -> init Lane + GQM + Poller
  -> Connection Ready
```

一句话说明：TCP 只负责控制面，payload 不走内核 socket 数据路径。

## 5. 当前实现进展

| 层级 | 模块 | 状态 | 汇报口径 |
|---|---|---|---|
| 原语层 | `ShmFreeList`、`SlotPool`、`RingRegion` | 已完成 | 数据生命周期和内存结构已打底。 |
| 映射层 | `SlotMapper`、`RingMapper`、`ShmPool` | 已完成 | 支持全局分区和 per-lane 子视图。 |
| 通知层 | `DefaultGqmQueue` | 已完成 | GQM 64-bit descriptor 通道已封装。 |
| Progress | `BusyPoller` | 已完成 | 支持 busy-poll、batch、绑核和 idle backoff。 |
| 编排层 | `LaneImpl` | 已完成 | 连接注册、写入、descriptor 路由闭环。 |
| 控制面 | `HandshakeStateMachine`、`HandshakerImpl`、`TcpHandshakeChannel` | 已完成 | TCP bootstrap 协议和协商状态机已具备。 |
| Facade | `Message` | 已完成 | RAII release 支持跨线程处理。 |
| Facade | `Connection` | 部分完成 | I/O 委托已有，全链路 connect / createPair 待接线。 |
| Facade | `Server` / `Client` | 骨架 | e2e 前必须补齐 listen / accept / connect。 |
| Adapter | `netpoll + Kitex`、`fbthrift + folly` | 未完成 | V1 立项说服力的关键补齐项。 |
| Benchmark | 独立 ping-pong / throughput | 未完成 | V1 性能 gating 的关键补齐项。 |

## 6. 与 V1 需求的主要差异

| 维度 | V1 需求口径 | 当前实现口径 | 方案判断 |
|---|---|---|---|
| 用户心智 | Core verbs-like + thin `Channel` | socket-like `Connection` / `Server` / `Client` + 原语层 | 可兼容。Facade 用于接入，原语层用于性能路线；需避免 Facade 承诺 TCP byte-stream 语义。 |
| 消息模型 | bounded slot-based atomic message | Slot Pool 为主，Ring Buffer 可选 | V1 主线应锁定 Slot Pool，Ring 作为扩展能力。 |
| Completion | `RecvCompletion` / `ReclaimCompletion` / `ControlCompletion` | callback + `Message` RAII + FreeList release | 需要补一个面向 Core/benchmark 的 completion adapter，或者在 API 文档中明确 callback 与 completion 的映射。 |
| Backpressure | `NoBuffer` / `QueueFull` 等结构化返回 | `write()` 自旋，`tryWrite()` bool | e2e proof 可先用现状；benchmark 和 Core API 应补结构化错误与计数。 |
| TCP control | 握手后保持 control connection | 握手与 fallback fd 已有，长连接控制语义待明确 | 需要把 close / error / heartbeat 的 control event 路径补齐。 |
| 多 lane | fixed multi-lane，握手协商 | lane 原语完成，全链路组装待接线 | 需要在 Facade / ShmPool / Handshake 之间完成 multi-lane assembly。 |
| e2e adapter | `netpoll + Kitex`、`fbthrift + folly` 两个 P0 proof | 尚未实现 | 这是 V1 立项前必须补齐的说服力证据。 |
| benchmark | 独立 benchmark 覆盖 `1,2,4,8,16` | 尚未实现 | 原语层已足够支撑，但需要 artifact 和 socket baseline。 |

## 7. V1 补齐路径

### 7.1 先完成库内闭环

目标是让独立库不依赖 RPC 栈也能完成真实进程间收发：

```text
Server::listen()
Client::connect()
TCP handshake
ShmPool map/init
Lane/GQM/Poller start
Connection::write()
onData/onMessage receive
graceful close/error event
```

验收口径：本地或跨机进程可以跑通 ping-pong，payload 只走 ub.mem data plane，TCP 只做控制面。

### 7.2 再补 benchmark gating

目标是建立可复现性能证据：

```text
lane_count = 1,2,4,8,16
latency: p50 / p99 / p999
throughput
qps_per_total_core
CPU utilization
NoBuffer / QueueFull / GQM full rate
payload checksum on/off
socket baseline
```

验收口径：benchmark artifact 能解释每次结果的配置、资源、CPU 和 backpressure 状态。

### 7.3 最后补两个 e2e proof

目标是证明它不只是 micro benchmark，而能进入真实 RPC 依赖链路：

| Adapter | 最小目标 | 非目标 |
|---|---|---|
| `netpoll + Kitex` | unary echo 或等价 request/response，payload 经 ub.mem data plane | 不做完整 RPC feature parity。 |
| `fbthrift + folly` | Rocket unary request/response 或等价 benchmark 路径 | 不做 streaming、TLS、完整 fallback。 |

验收口径：真实 client/server 进程启动，TCP bootstrap/control 可观测，request 和 response payload 走 ub.mem，保留 socket baseline 对比。

## 8. 汇报版结论

可以在 PPT 中用三句话收束：

1. `ubmem::transport` 已经具备独立用户态通信库的核心原语：共享内存布局、slot/ring 数据区、GQM 通知、BusyPoller、Lane 编排和 TCP 握手都已完成单测验证。
2. 当前实现的主要缺口不在底层机制，而在全链路组装、benchmark 证据和两个 RPC e2e proof；这些缺口直接对应 V1 立项所需的可用性和说服力。
3. 第一轮迭代建议坚持 Slot Pool 主线，用 `Connection` Facade 降低接入成本，用原语层保留性能调优空间，并用独立 benchmark + `netpoll + Kitex` + `fbthrift + folly` 三类证据完成闭环。

## 9. PPT 拆页建议

| 页码 | 标题 | 内容 |
|---|---|---|
| 1 | 项目定位 | 独立用户态通信库，TCP control + ub.mem data + GQM notify。 |
| 2 | 总体架构 | Facade / Lane / Poller / DataRegion / GQM / ShmPool / Handshake 分层图。 |
| 3 | 数据面方案 | Slot Pool 主线，Ring Buffer 作为扩展。 |
| 4 | 控制面方案 | TCP handshake、参数协商、fallback fd 边界。 |
| 5 | Progress 方案 | BusyPoller、batch poll、idle policy、未来 EvbPoller。 |
| 6 | 当前进展 | 已完成模块表和 60% 进度口径。 |
| 7 | 需求差异 | Facade vs Channel、callback vs Completion、Ring vs Slot、fallback 边界。 |
| 8 | V1 补齐计划 | 库内闭环、benchmark gating、两个 e2e adapter proof。 |
