# fbthrift Benchmark 服务端走读总结（Rocket 路径）

> 会话时间：2026-07-08 ~ 2026-07-09
> 范围：`thrift/perf/cpp2` benchmark 服务端 + 客户端，Rocket 协议路径
> 目的：作为 CXL 共享内存传输研究的对照实现，把 fbthrift 服务端从 listen → connect → 稳态 RPC 的完整链路走透。

## 仓库路径前缀（下文省略）

- fbthrift：`…/worktrees/main/fbthrift`
- wangle：`…/.cache/getdeps/macos-shared/repos/github.com-facebook-wangle.git/wangle`
- folly：`…/worktrees/main/folly`
- 生成代码（gen-cpp2，构建产物）：`…/.cache/getdeps/codex-dev/build/fbthrift/thrift/perf/cpp2/if/gen-cpp2/`

运行方式：
- server：`thrift_perf_cpp2_server [--port=7777 | --unix_socket_path=...]`
- client：`thrift_perf_cpp2_client --transport=rocket [--host=::1 --port=7777 | --unix_socket_path=...]`

---

## 0. 本次会话对源码的改动

- **改动文件**：`thrift/perf/cpp2/server/Server.cpp`（在 `worktrees/main/fbthrift`，**未提交**）。
- **改动内容**：移除 HTTP2 routing handler，使服务端只服务 Rocket（+ Header 兜底）。
  - 删 include：`<proxygen/httpserver/HTTPServerOptions.h>`、`<thrift/lib/cpp2/transport/http2/common/HTTP2RoutingHandler.h>`
  - 删 using：`HTTP2RoutingHandler`、`HTTPServerOptions`
  - 删 `createHTTP2RoutingHandler()` 整个函数
  - 删 `server->addRoutingHandler(createHTTP2RoutingHandler(server));` 调用
- **原因**：服务端支持 Rocket 与该 HTTP2 handler 无关——Rocket 由 `ThriftServer::setup()` 在 `ThriftServer.cpp:658-659` 自动追加的 `RocketRoutingHandler` 提供。
- **验证状态**：单文件 `-fsyntax-only` 未跑完（构建目录是 Linux 容器生成的，本机缺 gflags/boost 等 system 头）。改动为纯删除，grep 确认零 HTTP2/proxygen 残留，保留代码用到的符号均由保留的 include 提供。
- **未引入任何 Homebrew 包**（全程只 `brew --prefix` 查询已有包）；`-fsyntax-only` 无产物；未提交。

---

## 1. 关键文件清单（按层组织）

### 服务端入口与业务
- `thrift/perf/cpp2/server/Server.cpp` —— `main` 入口（本次已改）
- `thrift/perf/cpp2/server/BenchmarkHandler.h` —— handler，**header-only**，无 `.cpp`
- `thrift/perf/cpp2/if/ApiBase.thrift` —— 类型定义（`Chunk2`、`IOBuf` typedef）
- `thrift/perf/cpp2/if/Api.thrift` —— 基础服务 `Benchmark`（noop/sum 等）
- `thrift/perf/cpp2/if/StreamApi.thrift` —— `StreamBenchmark`（download/upload/streamDownload）
- `thrift/perf/cpp2/util/QPSStats.h` —— 计数器

### ThriftServer 生命周期与处理
- `thrift/lib/cpp2/server/ThriftServer.{h,cpp}` —— 服务框架主体
- `thrift/lib/cpp2/server/ThriftProcessor.{h,cpp}` —— channel→processor 桥（HTTP2/Header 路径）
- `thrift/lib/cpp2/server/Cpp2Worker.{h,cpp}` —— 每 IO 线程的 Thrift-aware Acceptor
- `thrift/lib/cpp2/server/TransportRoutingHandler.h` —— routing handler 接口
- `thrift/lib/cpp2/server/peeking/PeekingManager.h` —— 明文路由判定点
- `thrift/lib/cpp2/server/LegacyHeaderRoutingHandler.{h,cpp}` —— Header transport 路由
- `thrift/lib/cpp2/async/MultiplexAsyncProcessor.{h,cpp}` —— 多服务 multiplex
- `thrift/lib/cpp2/async/processor/AsyncProcessor.{h,cpp}` —— processor 接口
- `thrift/lib/cpp/concurrency/ThreadManager.cpp` —— CPU 线程池（任务队列）

### Rocket 协议层
- `thrift/lib/cpp2/transport/rocket/server/RocketRoutingHandler.{h,cpp}`
- `thrift/lib/cpp2/transport/rocket/server/RocketServerConnection.{h,cpp}`
- `thrift/lib/cpp2/transport/rocket/server/RocketServerConnectionFactory.{h,cpp}`
- `thrift/lib/cpp2/transport/rocket/server/ThriftRocketServerHandler.{h,cpp}` —— Rocket 帧→processor 桥
- `thrift/lib/cpp2/transport/rocket/client/RocketClient.{h,cpp}`
- `thrift/lib/cpp2/async/RocketClientChannel.{h,cpp}`
- `thrift/lib/cpp2/transport/rocket/framing/Parser.h` —— Rocket 帧解析（ReadCallback）

### 客户端
- `thrift/perf/cpp2/client/Client.cpp` —— `main` 入口
- `thrift/perf/cpp2/util/Util.{h,cpp}` —— `getSocket`/`newClient`/`newRocketClient`
- `thrift/perf/cpp2/util/Runner.h` —— 稳态 QPS 循环（header-only）
- `thrift/perf/cpp2/util/Operation.h` —— op 分发
- `thrift/perf/cpp2/util/StreamOps.h` —— `Download`/`Upload`/`StreamDownload` 操作

### wangle / folly 基础设施
- `wangle/bootstrap/ServerBootstrap.{h,cpp}` + `ServerBootstrap-inl.h` —— group/bind/worker pool
- `wangle/bootstrap/ServerSocketFactory.h` —— newSocket：bind+listen+startAccepting
- `wangle/acceptor/Acceptor.{h,cpp}` —— 通用 acceptor 基类
- `folly/executors/IOThreadPoolExecutor.cpp` —— IO 线程的 `threadRun`/`loopForever`
- `folly/executors/ThreadPoolExecutor.cpp` —— 线程 spawn、observer 机制
- `folly/io/async/AsyncServerSocket.cpp` —— accept4 + 跨线程派发
- `folly/synchronization/DelayedInit.h` —— 线程安全懒初始化

---

## 2. Server.cpp 组件解析

改后 `Server.cpp`（106 行）的组件：

**gflags**（`Server.cpp:30-37`）：`port=7777`、`unix_socket_path=""`、`io_threads=0`、`cpu_threads=0`（0 = 核数，`Server.cpp:48-54`）、`stats_interval_sec=1`、`terminate_sec=0`。

**三类核心对象**：
1. `QPSStats stats`（`:58`）—— 全局计数器表。
2. `BenchmarkHandler`（`:60`）—— header-only，继承 `ServiceHandler<StreamBenchmark>`；构造时预生成一个固定大小（`FLAGS_chunk_size` 默认 1024）随机 `Chunk2` blob 作为唯一数据源。
3. `ThriftServer server`（`:65`）—— 通用服务框架。

**包装链**（`:60-63, 79`）：
```cpp
auto handler = std::make_shared<BenchmarkHandler>(&stats);
auto cpp2PFac = std::make_shared<
    ThriftServerAsyncProcessorFactory<BenchmarkHandler>>(handler);
server->setInterface(cpp2PFac);
```

**routing handler：Server.cpp 不再显式加任何 routing handler。** Rocket + Header 都由 `setup()` 自动追加。

**logger 线程 + serve**（`:81-101`）：logger 周期 `printStats`，到点 `server->stop()`；主线程 `server->serve()` 阻塞。

---

## 3. 核心流程梳理

### 3.1 怎么 listen

反直觉点：`setPort`/`useExistingSocket` 都**不触发网络操作**，真正 `bind`/`listen`/`startAccepting` 在 `serve()→setup()` 里，实现在 wangle。

**两条配置路径**（`Server.cpp:66-76`）：
- Unix socket：自己 `new AsyncServerSocket` + `bind`，再 `useExistingSocket`（`ThriftServer.cpp:393-411` 只存成员）。
- TCP：`setPort`（`ThriftServer.h:377-381`）只写 `port_`。

**serve() → setup()**（`ThriftServer.cpp:1556-1567`）：`serve()` 极薄，`setup()`（`:591-849`）做全部初始化，主线程 `loopForever()` 阻塞。

**setup() 关键序列**：
- `:653` `setupThreadManagerImpl()`（CPU 线程池）
- `:658-663` 自动追加 `RocketRoutingHandler` + `LegacyHeaderRoutingHandler`
- `:691` `ioThreadPool_->setNumThreads(nWorkers)`
- `:692-696` 建 `acceptPool_`（默认 1 线程）
- `:705` `ServerBootstrap::childHandler(acceptorFactory)`
- `:709` `ServerBootstrap::group(acceptPool_, ioThreadPool_)`
- `:730-738` `ServerBootstrap::bind(...)` ← 真正 listen
- `:828` `thriftConfig_.freeze()`

**真正 bind+listen+startAccepting**（wangle）：
- 预 bind socket（unix）：`ServerBootstrap.h:197-198` 只 `listen`+`startAccepting`。
- 端口：`ServerSocketFactory.h:72-75` `bind`/`listen`/`startAccepting`。
- 都跑在 **acceptor 线程的 EventBase** 上。

### 3.2 怎么 connect（Rocket 客户端）

**客户端入口**（`Client.cpp:64-158`）：每个 client 一个线程 + 独立 EventBase，`newClient<...>(evb, addr, FLAGS_transport)`（`:88-89`）按 `--transport` 选；`FLAGS_transport` 默认 `"header"`（`Client.cpp:35`），走 Rocket 须显式 `--transport=rocket`。

**newClient → newRocketClient**（`Util.h:68-93`）：
```cpp
auto sock = getSocket(evb, addr, encrypted, {"rs2"});                    // Util.cpp
RocketClientChannel::Ptr channel = RocketClientChannel::newChannel(std::move(sock));
return std::make_unique<AsyncClient>(std::move(channel));
```

**getSocket = connect 起点**（`Util.cpp:23-46`）：
- AF_UNIX → `new folly::AsyncFdSocket(evb, addr)`（`:33`）
- TCP → `new folly::AsyncSocket(evb, addr)`（`:35`）
- 以 evb+远端地址构造，**构造时即发起异步连接**。

**RocketClient 构造**（`RocketClient.cpp:140-180`）—— read callback 挂载点：
```cpp
parser_(*this, THRIFT_FLAG(rocket_frame_parser), allocatorPtr);   // :149
setupFrame_(makeSetupFrame(setupMetadata));                       // :153  预生成 SETUP frame
socket_->setReadCB(&parser_);                                     // :156  ★ read callback = Parser
```
- `RocketClient` private 继承 `WriteCallback`，写完成回调回自己。
- SETUP frame 懒发送：首次 `sendRequestResponse` 时 `moveOutSetupFrame()`（`:955`）随首请求捎带。

### 3.3 稳态 download 端到端

`download()`（`StreamApi.thrift:26`，`ApiBase.Chunk2 download()`）单请求单响应。

**数据源**：`BenchmarkHandler.h:36-54` 构造时预生成 `chunk_`（`Chunk2`，`data` 是零拷贝 `folly::IOBuf`）。

**客户端稳态循环**（`Runner.h:48-56`）：维持在途请求数，`ops_->async(op, cb)`（`:54`）发起；`finishCall`（`:65-69`）每完成一个补一个。

**发起 download**：`Operation::async`（`Operation.h:69-105`）→ `Download::async`（`StreamOps.h:46-52`）→ 生成代码 `client->download(rpcOptions, cb)`。

**客户端编码发送链**：
```
client->download
 → RocketClientChannel::sendRequestResponse (RocketClientChannel.cpp:892)
 → sendThriftRequest (:977, packWithFds :1012)
 → sendSingleRequestSingleResponse (:1112)
 → RocketClient::sendRequestResponse (RocketClient.cpp:951, moveOutSetupFrame :955, makeStreamId :957)
 → scheduleWrite (:1424, 入队) → scheduleWriteLoopCallback (:1462, 挂 LoopCallback)
 → [EventBase loop 末尾] WriteLoopCallback::runLoopCallback (:1458)
 → writeScheduledRequestsToSocket (:1473, 合批)
 → socket_->writeChain(this, buf, wflags) (:1514) ★写到 AsyncSocket
 → writeSuccess (:1521)
```

**服务端接收**：`RocketServerConnection` 的 `Parser` 读完整帧 → `handleFrame` → `RocketServerFrameContext` → `ThriftRocketServerHandler`（解包成 `RequestRpcMetadata`+payload，构 `ServerRequest`，交 `AsyncProcessor`）。

> ⚠ Rocket **不走 `ThriftProcessor::onThriftRequest`**。Rocket 在 `RocketRoutingHandler::handleConnection` 建的 `ThriftRocketServerHandler` 自己持 processor（**每连接一个**），自己解帧后调 `executeRequest`/`processSerializedCompressedRequestWithMetadata`。

`StreamBenchmarkAsyncProcessor::executeRequest_download`（生成代码 `StreamBenchmark.h:171`，dispatch 在 `StreamBenchmark.tcc:131-167`）→ `async_tm_download`（调度到 CPU worker 线程）→ handler `download(Chunk2&)`（`BenchmarkHandler.h:84-87`）：
```cpp
void download(Chunk2& result) override {
  stats_->add(kUpload_);   // 计数器按网络方向命名（坑）
  result = chunk_;         // 直接赋值，依赖 IOBuf COW
}
```
结果经 `return_download` 序列化成 `SerializedResponse`，Rocket 层包成 `PAYLOAD` 帧回写。

**客户端收响应链**：
```
AsyncSocket readable
 → Parser::readDataAvailable (Parser.h:136)
 → FrameLengthParserStrategy (FrameLengthParserStrategy-inl.h:93-101)
 → owner_.handleFrame → RocketClient::handleFrame (RocketClient.cpp:208)
 → handleRequestResponseFrame (:383) → ctx.onPayloadFrame (:411) → baton_.post
 → Context::post (:993) → callback (onResponsePayload)
 → SingleRequestSingleResponseCallback::onResponsePayload (RocketClientChannel.cpp:685, unpack :726)
 → cb->onResponse(ClientReceiveState) (:776) ★唤醒用户 callback
 → LoadCallback::replyReceived (Runner.h:96) → finishCall → run()  稳态闭环
```

### 3.4 三线程模型

| pool | 数量 | 线程内部跑什么 | 职责 |
|---|---|---|---|
| **acceptor**（`acceptPool_`） | 默认 1 | EventBase 挂 `AsyncServerSocket` | accept 连接、握手/peek、路由分发 |
| **io**（`ioThreadPool_`） | 默认核数 | EventBase 挂 `Cpp2Worker`(Acceptor) | 连接级 I/O、协议帧、派发 |
| **cpu**（`ThreadManager`） | 默认核数 | 任务队列：`waitOnTask`→`task->run` | 跑业务 handler |

- acceptor/io 都是 `IOThreadPoolExecutor`（跑 EventBase `loopForever`，无 func）。
- cpu 是 `ThreadManager`（任务队列，有 func）。
- acceptor 线程 `accept4()` 后把 fd 跨线程派发到 IO worker 的 EventBase（`AtomicNotificationQueue`）；IO worker 自己不 listen。

---

## 4. 关键问题与结论（按会话提问顺序）

### 4.1 "服务端是默认走 Rocket 吗？"

**结论：不是默认。**
- 服务端**没有"默认 transport"概念**：路由是 content-based——连接进来 peek 前 13 字节，按 routing handler 顺序 first-match-wins。Rocket 客户端（发 RSocket SETUP 帧）→ `RocketRoutingHandler`；Header 客户端 → `LegacyHeaderRoutingHandler`；都不匹配 → 兜底 **Header**（不是 Rocket）。
- 客户端 `FLAGS_transport` 默认 `"header"`（`Client.cpp:35`），走 Rocket 须 `--transport=rocket`。
- 服务端"支持 Rocket"是因为 `setup()` 自动追加 `RocketRoutingHandler`（`ThriftServer.cpp:658-659`），与删掉的 HTTP2 handler 无关。

### 4.2 Chunk2 是干什么的，在哪定义？

**定义**：`thrift/perf/cpp2/if/ApiBase.thrift:38-41`（thrift IDL，非手写 C++）：
```thrift
@cpp.Type{name = "folly::IOBuf"}
typedef binary IOBuf          // :30-31  binary → folly::IOBuf（零拷贝）

struct Chunk { 1: binary header; 2: binary data; }           // :33-36  data = std::string（拷贝）
struct Chunk2 { 1: binary header; 2: IOBuf data; }           // :38-41  data = IOBuf（零拷贝）
```
**设计目的**：benchmark 数据搬运方法（`StreamApi.thrift:26,28,30` 的 download/upload/streamDownload）的载荷类型。`@cpp.Type` 注解让 `data` 映射成零拷贝 `folly::IOBuf`（配 `AsyncSocket::writeChain` 不拷贝直接发网卡），才能真实测二进制传输吞吐——用 `std::string`（Chunk）测出来全是拷贝开销。`header` 字段 handler 不填，只是给 struct 一个"元数据+载荷"形状。

### 4.3 ThriftProcessor / AsyncProcessor

**关键澄清**：`ThriftProcessor::onThriftRequest` 是 **HTTP2/Header channel 路径**（唯一调用方 `SingleRpcChannel.cpp:414`）。benchmark 走 Rocket 时**根本不经过它**——Rocket 用 `ThriftRocketServerHandler`，每连接自己持 processor。所以纯 Rocket 场景下 `ThriftProcessor` 是 dormant（构造了没人调）。

**processor 封装了什么类**：
- `getDecoratedProcessorFactory()`（`ThriftServer.cpp:1785-1789`）返回 `decoratedProcessorFactory_`（`ThriftServer.h:2232`），实际是 `MultiplexAsyncProcessorFactory`。
- "装饰"其实是 **multiplex 不是嵌套**：`createDecoratedProcessorFactory`（`ThriftServer.cpp:174-207`）把 user + status + monitoring + control + security 五个并列 factory 收进 vector。顺序图见 `ThriftServer.h:2934-2952`（User 最高、Control 最低；图漏 Security）。
- `getProcessor()`：`MultiplexAsyncProcessorFactory` 对每个子工厂调 `getProcessor()`，包成 `MultiplexAsyncProcessor`（`MultiplexAsyncProcessor.cpp:457-465`）；用户子工厂建 `StreamBenchmarkAsyncProcessor`（`ThriftServer.h:214-217` `new T::ProcessorType(svIf_.get())`），持有**裸指针** `iface_` 指向共享 handler。
- `coalesceWithServerScopedLegacyEventHandlers`（`AsyncProcessor.cpp:38-47`）：把 server 级 legacy event handler 合并进 processor。

**为什么 lazy（DelayedInit）**：纯时序问题。`setInterface`（`ThriftServer.cpp:368-383`）受 `CHECK(configMutable())` 守卫，须在 `setup()` 前（config 在 `:828` freeze）调；它构造 `ThriftProcessor`（`:382`）但此时 `decoratedProcessorFactory_` 还 null（要等 `setup()` 的 `:1226` 才建）。所以只能推迟到首次 `onThriftRequest`。`DelayedInit`（`folly/synchronization/DelayedInit.h`）线程安全、at-most-once、`call_once`，保证多线程首请求只建一次。

**为什么不是一个连接一个（分路径）**：
| 路径 | processor 粒度 |
|---|---|
| ThriftProcessor/onThriftRequest（HTTP2） | 全 server 一个（`ThriftProcessor.h:42-43`） |
| Header（`Cpp2Connection.cpp:133`） | 每连接一个 |
| Rocket（`ThriftRocketServerHandler.cpp:252,282`） | 每连接一个 |
- `AsyncProcessor.h:30-31` "created once per-connection" 描述的是 Header/Rocket，不是 ThriftProcessor。
- **单实例够用**：processor 是无状态方法分派器（`AsyncProcessor.h:52-64`，所有 per-call 输入作参数传入）；生成的辅助函数和分派表 `kOwnProcessMap_` 是 `static`；唯一非平凡成员是指向共享 handler 的裸指针。

### 4.4 三线程池生命周期（代码级，调用在哪生效）

**起池**（`ThriftServer.cpp:691-738`）：`:691 setNumThreads`、`:693-695 建 acceptPool_`、`:705 childHandler`、`:709 group`、`:730-738 bind`。

**group 挂观察者**（`ServerBootstrap.h:132-171`）：`:153-155` 建 `ServerWorkerPool`（IOObserver 子类），`:165` `io_group->addObserver(workerFactory_)`。

**setNumThreads → spawn 线程**（`ThreadPoolExecutor.cpp:227-264`）：`:229` 因 observers_ 非空一次性起全部线程；`addThreads`（`:245-267`）`:253` `newThread(bind(threadRun))` spawn OS 线程，`:264` 线程起来后通知观察者。

**线程内部**（`IOThreadPoolExecutor.cpp:222-270`）：`:227` 每线程一个 EventBase，`:251-253` `while(shouldRun) eventBase->loopForever()`。需跑 func 时 `add`（`:141-163`）`runInEventBaseThread`（`:162`）post 进同一 EventBase。

**观察者造 Acceptor**（`IOThreadPoolExecutor.cpp:312-318` `handleObserverRegisterThread` → `:316 registerEventBase`）→ `ServerWorkerPool::registerEventBase`（`ServerBootstrap.cpp:23-37`）：`:24 newAcceptor(&evb)` 每 IO 线程造一个 `Cpp2Worker`，`:33-34 addAcceptCB` 注册成 AcceptCallback。两个触发时机：新线程 spawn 完（`ThreadPoolExecutor.cpp:264`）、`addObserver` 对已有线程补发（`:453`）。

**accept 真实路径**（确认 acceptor 线程 accept 后跨线程派发）：
- `AsyncServerSocket::handlerReady`（`AsyncServerSocket.cpp:1013`）→ `accept4`（`:1037`）→ `dispatchSocket`（`:1167`）。
- `dispatchSocket`（`:1175 nextCallback` 选 IO worker）因 EVB 不同 → `NewConnMessage` 入队（`:1189-1193`）。
- IO worker `loopForever` 消费 → `NewConnMessage::operator()`（`:103`）→ `Acceptor::connectionAccepted`（`Acceptor.cpp:336`）→ `acceptConnection`（`:343`）→ `Cpp2Worker::onNewConnection`。

**CPU 池**（`ThreadManager.cpp`）：`Impl::add`（`:861-894`）`:885 入优先级队列`、`:892 waitSem_.post`；`Worker::run`（`:604-668`）`:623 waitOnTask`、`:664 task->run`。

### 4.5 Cpp2Worker 的角色

**一句话**：每个 IO 线程上的"Thrift 化的 Acceptor"——`wangle::Acceptor` 的 Thrift 专用子类，一个 IO 线程一个。

**继承**（`Cpp2Worker.h:58-61`）：`IOWorkerContext` + `wangle::Acceptor` + `PeekCallback`。文档（`:51-57`）："对已建立的连接来说，Cpp2Worker 才是真正的 server 对象"。

**四件事**：
1. **接连接**：作为 `wangle::Acceptor`，是被注册到 listening socket 的 AcceptCallback；fd 派发到本线程后 wangle 调 `onNewConnection`（`:328-333`）。
2. **协议路由判定**：`onNewConnectionThatMayThrow`（`:364-369`）里 TLS 走 ALPN、明文 peek 首 9 字节（`kPeekCount=9`，`:63`）→ 选 routing handler；都不命中调自己 `handleHeader`（`:134-137`）。
3. **持 per-thread 状态**：
   - `perServiceMetadata_`（`:380-381`）—— 每线程方法分派表，懒构造带 GC（`getMetadataForService` `:224-255`），无锁查表。
   - `ingressMemoryTracker_`/`egressMemoryTracker_`（`:411-412`）—— 总预算按 IO 线程均分（`construct` `:315-325`），Rocket 建连接时拿来背压。
   - `requestsRegistry_`（`:408`）、`activeRequests_`/`stopBaton_`（`:407,409-410`）。
4. **wangle↔Thrift 桥**：override `getFizzPeeker`/`makeNewAsyncSocket` 等注入 Thrift 行为（Fizz TLS、server 配置）。

**易混**：它不是线程，是跑在 IO 线程 EventBase 上的对象；"Worker" 指 IO-worker 上下文。一个 IO 线程 = 一个 Cpp2Worker = 一个 EventBase，三者绑定。`dispatchRequest`（`:257-266`）是它的 static 方法（不依赖 `this`）。

---

## 5. 走读坑点汇总

1. **不是"默认 Rocket"**：路由 content-based，客户端默认 `"header"`，要 `--transport=rocket`。
2. **HTTP2 已从 Server.cpp 移除**（本次改动，未提交）：routing handler 只剩 `[Rocket, Header]`，都 `setup()` 自动追加。
3. **服务端类名**：Rocket 连接是 `RocketServerConnection`（非 `RocketServerChannel`，那是客户端）；acceptor 是 `Cpp2Worker`（非 `ThriftAcceptor`）；routing handler 接口方法是 `handleConnection`（非 `makeHandler`），返回 void。
4. **listen 不在 fbthrift**：`setPort`/`useExistingSocket` 只记账，真正 bind/listen 在 wangle。
5. **connect 在 socket 构造时发起**；Rocket SETUP 帧懒发送随首请求捎带。
6. **两层 peek**：wangle 握手 peek（`kPeekCount=9`）≠ 传输路由 peek（`kPeekBytes=13`，`PeekingManager.h:36`）。
7. **Rocket 不走 ThriftProcessor**：Rocket 走 `ThriftRocketServerHandler`（每连接 processor）；ThriftProcessor 是 HTTP2/Header 路径（全 server 一个 processor），纯 Rocket 场景 dormant。
8. **计数器按网络方向命名**：`download()`→`kUpload_`、`upload()`→`kDownload_`，stream 版加 `s_` 前缀。
9. **download 走 worker 线程**（`async_tm_download`），`noop`/`sum` 走 EB 线程（`async_eb_*`，IDL 标 `@cpp.ProcessInEbThreadUnsafe`）。
10. **processor 粒度分路径**：ThriftProcessor 全 server 一个；Header/Rocket 每连接一个（都够用，因 processor 无状态）。
11. **`setNumThreads` 一次性起全部线程**：因 `group()` 先 `addObserver` 让 `observers_` 非空（`ThreadPoolExecutor.cpp:229`）。
12. **`registerEventBase` 与 `bind` 的 forEachWorker 互补**：两条路径合起来保证"IO 线程数 × socket 数"个 AcceptCallback 全注册上。
13. **accept 是跨线程派发**：acceptor 线程 `accept4` 后入 `AtomicNotificationQueue`，IO worker 消费；SO_REUSEPORT 只在多 acceptor 线程间（默认 1 个）。

---

## 6. 建议的走读顺序

```
Server.cpp → BenchmarkHandler.h + StreamApi.thrift + ApiBase.thrift
→ ThriftServer.cpp::serve/setup → wangle ServerBootstrap/ServerSocketFactory
→ Cpp2Worker.{h,cpp} + PeekingManager.h（accept + 路由判定）
→ RocketRoutingHandler.cpp → RocketServerConnection → ThriftRocketServerHandler（Rocket 实际处理路径）
→ MultiplexAsyncProcessor.{h,cpp} + AsyncProcessor.{h,cpp}（processor 是什么）
→ ThriftProcessor.{h,cpp}（HTTP2/Header 桥，对照）
→ client Client.cpp → Util.{h,cpp} → RocketClientChannel.cpp → RocketClient.cpp
→ Runner.h / StreamOps.h（客户端稳态循环）
→ folly IOThreadPoolExecutor.cpp + ThreadPoolExecutor.cpp + AsyncServerSocket.cpp（线程池 + accept 机制）
→ ThreadManager.cpp（CPU 池）
```

---

## 7. 待深入（后续可展开）

- `ThriftRocketServerHandler` 内部：每连接如何 `getProcessor`、Rocket 帧（REQUEST_RESPONSE / STREAM / SINK）如何解包后调 `executeRequest`、如何调度到 CPU 线程、streamDownload 的 `ServerStream<Chunk2>` 如何转成 Rocket stream 帧。
- `Cpp2Worker::onNewConnectionThatMayThrow` 完整实现（TLS 分支、`createSSLHelper`、Fizz 握手）。
- ResourcePool（新调度模型）vs legacy `ThreadManager` 的关系（`Cpp2Worker::dispatchRequest:649` 的 `resourcePool->accept`）。
- IOBuf COW 在 `result = chunk_` 时的实际内存行为。
