/*
 * Copyright (c) Meta Platforms, Inc. and affiliates.
 *
 * Licensed under the Apache License, Version 2.0 (the "License");
 * you may not use this file except in compliance with the License.
 * You may obtain a copy of the License at
 *
 *     http://www.apache.org/licenses/LICENSE-2.0
 *
 * Unless required by applicable law or agreed to in writing, software
 * distributed under the License is distributed on an "AS IS" BASIS,
 * WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
 * See the License for the specific language governing permissions and
 * limitations under the License.
 */

#include <thrift/perf/cpp2/util/CxlMemBenchmarkTransport.h>

#include <folly/Conv.h>
#include <folly/ExceptionString.h>
#include <folly/MPMCQueue.h>
#include <folly/ScopeGuard.h>
#include <folly/io/IOBuf.h>
#include <folly/io/async/AsyncSocket.h>
#include <folly/io/async/AsyncTimeout.h>
#include <folly/io/async/BusyPollBackend.h>
#include <folly/io/async/CxlMemAsyncTransport.h>
#include <folly/io/async/CxlMemPoller.h>
#include <folly/io/async/CxlMemRegion.h>
#include <folly/io/async/fdsock/AsyncFdSocket.h>
#include <folly/portability/Asm.h>
#include <folly/synchronization/Baton.h>
#include <folly/system/ThreadName.h>
#include <glog/logging.h>
#include <thrift/lib/cpp2/server/Cpp2Worker.h>
#include <thrift/lib/cpp2/server/ThriftServer.h>
#include <thrift/lib/cpp2/server/TransportRoutingHandler.h>
#include <thrift/lib/cpp2/transport/rocket/server/RocketRoutingHandler.h>

#include <event2/event.h>

#include <algorithm>
#include <array>
#include <atomic>
#include <chrono>
#include <cstdio>
#include <exception>
#include <mutex>
#include <stdexcept>
#include <thread>
#include <utility>
#include <vector>

namespace apache::thrift::perf {
namespace {

using StubBackend = folly::CxlMemStubHWQueueBackend;
using StubDoorbellQueue = folly::CxlMemDoorbellQueue<StubBackend>;
using StubTransport = folly::CxlMemAsyncTransport<StubBackend>;

constexpr std::array<uint8_t, 6> kHandshakeMagic{
    {'C', 'X', 'L', 'M', 'E', 'M'}};
constexpr uint8_t kHandshakeVersion = 1;
constexpr uint8_t kStubBackendId = 1;
constexpr size_t kHandshakeBytes = 13;
constexpr size_t kHandshakePayloadKiBOffset = 10;

std::atomic<uint16_t> gNextConnId{1};

struct CxlMemHandshake {
  uint16_t connId{0};
  size_t payloadSliceSize{0};
  uint16_t hwQueuesPerDoorbell{0};
};

uint16_t loadU16(const uint8_t* data) {
  return static_cast<uint16_t>((data[0] << 8) | data[1]);
}

void storeU16(uint8_t* data, uint16_t value) {
  data[0] = static_cast<uint8_t>(value >> 8);
  data[1] = static_cast<uint8_t>(value);
}

std::string directionPath(
    const CxlMemBenchmarkOptions& options,
    uint16_t connId,
    folly::StringPiece direction) {
  return folly::to<std::string>(
      options.pathPrefix, ".", connId, ".", direction, ".shm");
}

size_t directionRegionSize(const CxlMemBenchmarkOptions& options) {
  return 2 * options.hwQueuesPerDoorbell * folly::kCxlMemHWQueueBytes +
      options.payloadSliceSize;
}

size_t dataDoorbellOffset() {
  return 0;
}

size_t ackDoorbellOffset(const CxlMemBenchmarkOptions& options) {
  return options.hwQueuesPerDoorbell * folly::kCxlMemHWQueueBytes;
}

size_t payloadOffset(const CxlMemBenchmarkOptions& options) {
  return 2 * options.hwQueuesPerDoorbell * folly::kCxlMemHWQueueBytes;
}

void validateOptions(const CxlMemBenchmarkOptions& options) {
  if (options.backend != "stub") {
    throw std::invalid_argument("CXL mem benchmark only supports stub backend");
  }
  if (options.pathPrefix.empty()) {
    throw std::invalid_argument("CXL mem benchmark requires path prefix");
  }
  if (options.payloadSliceSize == 0) {
    throw std::invalid_argument("CXL mem benchmark requires payload slice");
  }
  if (options.hwQueuesPerDoorbell == 0) {
    throw std::invalid_argument("CXL mem benchmark requires HW queues");
  }
  if (options.pollIntervalMs == 0) {
    throw std::invalid_argument("CXL mem benchmark requires poll interval");
  }
  if (options.handoffQueueCapacity < 2) {
    throw std::invalid_argument(
        "CXL mem benchmark handoff queue requires at least two slots");
  }
}

std::vector<folly::CxlMemHWQueueConfig> doorbellConfigs(
    folly::CxlMemRegion& region,
    size_t offset,
    uint16_t count) {
  std::vector<folly::CxlMemHWQueueConfig> configs;
  configs.reserve(count);
  auto* memory = static_cast<unsigned char*>(region.data());
  for (uint16_t i = 0; i < count; ++i) {
    configs.push_back(folly::CxlMemHWQueueConfig{
        memory + offset + i * folly::kCxlMemHWQueueBytes,
        folly::kCxlMemHWQueueBytes});
  }
  return configs;
}

std::string encodeHandshake(
    uint16_t connId,
    const CxlMemBenchmarkOptions& options) {
  const size_t payloadKiB = options.payloadSliceSize / 1024;
  if (payloadKiB == 0 || payloadKiB > 0xffff) {
    throw std::invalid_argument("CXL mem payload slice must fit in KiB field");
  }
  if (options.hwQueuesPerDoorbell > 0xff) {
    throw std::invalid_argument("CXL mem HW queues must fit in handshake");
  }

  std::string handshake(kHandshakeBytes, '\0');
  auto* data = reinterpret_cast<uint8_t*>(&handshake[0]);
  std::copy(kHandshakeMagic.begin(), kHandshakeMagic.end(), data);
  data[6] = kHandshakeVersion;
  data[7] = kStubBackendId;
  storeU16(data + 8, connId);
  storeU16(data + kHandshakePayloadKiBOffset, static_cast<uint16_t>(payloadKiB));
  data[12] = static_cast<uint8_t>(options.hwQueuesPerDoorbell);
  return handshake;
}

bool isHandshakeBytes(const uint8_t* data, size_t size) {
  return size >= kHandshakeBytes &&
      std::equal(kHandshakeMagic.begin(), kHandshakeMagic.end(), data) &&
      data[6] == kHandshakeVersion && data[7] == kStubBackendId;
}

CxlMemHandshake decodeHandshake(const folly::IOBuf& data) {
  const auto length = data.computeChainDataLength();
  if (length < kHandshakeBytes) {
    throw std::invalid_argument("CXL mem handshake is too small");
  }

  auto copy = data.clone();
  copy->coalesce();
  const auto* bytes = copy->data();
  if (!isHandshakeBytes(bytes, copy->length())) {
    throw std::invalid_argument("CXL mem handshake magic mismatch");
  }
  const uint16_t payloadKiB = loadU16(bytes + kHandshakePayloadKiBOffset);
  return CxlMemHandshake{
      loadU16(bytes + 8),
      static_cast<size_t>(payloadKiB) * 1024,
      bytes[12]};
}

class HandshakeWriteCallback : public folly::AsyncTransport::WriteCallback {
 public:
  void writeSuccess() noexcept override {
    success = true;
    done = true;
  }

  void writeErr(size_t, const folly::AsyncSocketException& ex) noexcept
      override {
    error = ex.what();
    done = true;
  }

  bool done{false};
  bool success{false};
  std::string error;
};

bool writeHandshake(
    folly::EventBase* evb,
    const folly::SocketAddress& addr,
    const std::string& handshake) {
  folly::AsyncSocket::UniquePtr sock;
  if (addr.getFamily() == AF_UNIX) {
    sock.reset(new folly::AsyncFdSocket(evb, addr));
  } else {
    sock.reset(new folly::AsyncSocket(evb, addr));
  }

  HandshakeWriteCallback callback;
  sock->writeChain(&callback, folly::IOBuf::copyBuffer(handshake));
  const auto deadline =
      std::chrono::steady_clock::now() + std::chrono::seconds(1);
  while (!callback.done && std::chrono::steady_clock::now() < deadline) {
    evb->loopOnce(EVLOOP_NONBLOCK);
    std::this_thread::sleep_for(std::chrono::milliseconds(1));
  }
  sock->closeNow();
  if (!callback.success) {
    LOG(INFO) << "CXL mem benchmark handshake write failed: "
              << (callback.error.empty() ? "timeout" : callback.error);
  }
  return callback.success;
}

class CxlMemBenchmarkResources {
 public:
  CxlMemBenchmarkResources(
      CxlMemBenchmarkOptions options,
      uint16_t connId,
      bool create)
      : options_(std::move(options)),
        connId_(connId),
        c2sPath_(directionPath(options_, connId_, "c2s")),
        s2cPath_(directionPath(options_, connId_, "s2c")),
        c2sRegion_(regionConfig(c2sPath_, create)),
        s2cRegion_(regionConfig(s2cPath_, create)),
        c2sData_(doorbellConfigs(
            c2sRegion_, dataDoorbellOffset(), options_.hwQueuesPerDoorbell)),
        c2sAck_(doorbellConfigs(
            c2sRegion_, ackDoorbellOffset(options_), options_.hwQueuesPerDoorbell)),
        s2cData_(doorbellConfigs(
            s2cRegion_, dataDoorbellOffset(), options_.hwQueuesPerDoorbell)),
        s2cAck_(doorbellConfigs(
            s2cRegion_, ackDoorbellOffset(options_), options_.hwQueuesPerDoorbell)) {}

  StubTransport::Config clientConfig(folly::EventBase* evb) {
    return makeConfig(
        evb, c2sRegion_, s2cRegion_, c2sData_, c2sAck_, s2cData_, s2cAck_);
  }

  StubTransport::Config serverConfig(folly::EventBase* evb) {
    return makeConfig(
        evb, s2cRegion_, c2sRegion_, s2cData_, s2cAck_, c2sData_, c2sAck_);
  }

  StubDoorbellQueue* clientInboundDataQueue() { return &s2cData_; }
  StubDoorbellQueue* serverInboundDataQueue() { return &c2sData_; }
  uint32_t pollIntervalMs() const { return options_.pollIntervalMs; }

 private:
  folly::CxlMemRegionConfig regionConfig(
      const std::string& path,
      bool create) const {
    if (create) {
      std::remove(path.c_str());
    }
    return folly::CxlMemRegionConfig{
        path,
        directionRegionSize(options_),
        create,
        options_.cacheCoherentMapping};
  }

  StubTransport::Config makeConfig(
      folly::EventBase* evb,
      folly::CxlMemRegion& outboundRegion,
      folly::CxlMemRegion& inboundRegion,
      StubDoorbellQueue& outboundData,
      StubDoorbellQueue& outboundAck,
      StubDoorbellQueue& inboundData,
      StubDoorbellQueue& inboundAck) {
    auto* outbound = static_cast<unsigned char*>(outboundRegion.data());
    auto* inbound = static_cast<unsigned char*>(inboundRegion.data());
    StubTransport::Config config;
    config.eventBase = evb;
    config.connId = connId_;
    config.outboundPayload = outbound + payloadOffset(options_);
    config.outboundPayloadSize = options_.payloadSliceSize;
    config.outboundPayloadBaseOffset = payloadOffset(options_);
    config.inboundPayload = inbound + payloadOffset(options_);
    config.inboundPayloadSize = options_.payloadSliceSize;
    config.inboundPayloadBaseOffset = payloadOffset(options_);
    config.outboundDataQueue = &outboundData;
    config.outboundAckQueue = &outboundAck;
    config.inboundDataQueue = &inboundData;
    config.inboundAckQueue = &inboundAck;
    return config;
  }

  CxlMemBenchmarkOptions options_;
  uint16_t connId_{0};
  std::string c2sPath_;
  std::string s2cPath_;
  folly::CxlMemRegion c2sRegion_;
  folly::CxlMemRegion s2cRegion_;
  StubDoorbellQueue c2sData_;
  StubDoorbellQueue c2sAck_;
  StubDoorbellQueue s2cData_;
  StubDoorbellQueue s2cAck_;
};

class CxlMemBenchmarkAsyncTransport;

class CxlMemBenchmarkPollRegistry {
 public:
  void add(CxlMemBenchmarkAsyncTransport* transport);
  void remove(CxlMemBenchmarkAsyncTransport* transport);
  size_t pollOnce();

 private:
  std::vector<CxlMemBenchmarkAsyncTransport*> transports_;
};

enum class CxlMemBenchmarkPollMode {
  ASYNC_TIMEOUT,
  INLINE,
};

class CxlMemBenchmarkAsyncTransport final : public StubTransport {
 public:
  CxlMemBenchmarkAsyncTransport(
      folly::EventBase* evb,
      std::shared_ptr<CxlMemBenchmarkResources> resources,
      StubTransport::Config config,
      StubDoorbellQueue* inboundDataQueue,
      CxlMemBenchmarkPollMode pollMode,
      CxlMemBenchmarkPollRegistry* pollRegistry)
      : StubTransport(config),
        resources_(std::move(resources)),
        inboundDataQueue_(inboundDataQueue),
        pollRegistry_(pollRegistry),
        pollTimeout_(this, evb),
        pollIntervalMs_(resources_->pollIntervalMs()) {
    if (pollMode == CxlMemBenchmarkPollMode::INLINE) {
      CHECK(pollRegistry_ != nullptr);
      pollRegistry_->add(this);
    } else {
      folly::CxlMemPollerQueueOptions options;
      pollHandle_ = poller_.addQueue(
          evb,
          inboundDataQueue,
          [this](uint64_t item) { drainInbound(64, item, true); },
          options);
      pollTimeout_.scheduleTimeout(pollIntervalMs_);
    }
  }

  ~CxlMemBenchmarkAsyncTransport() override {
    if (pollRegistry_ != nullptr) {
      pollRegistry_->remove(this);
    }
    pollTimeout_.cancelTimeout();
    if (pollHandle_) {
      pollHandle_->close();
    }
  }

  bool pollOnceInline() noexcept {
    try {
      bool didWork = false;
      uint64_t item = 0;
      if (inboundDataQueue_ != nullptr && inboundDataQueue_->pop(&item)) {
        drainInbound(64, item, true);
        didWork = true;
      }
      flushPendingWrites();
      return didWork;
    } catch (...) {
      LOG(ERROR) << "CXL mem benchmark inline poll failed: "
                 << folly::exceptionStr(folly::current_exception());
      closeNow();
      return true;
    }
  }

 private:
  class PollTimeout final : public folly::AsyncTimeout {
   public:
    PollTimeout(CxlMemBenchmarkAsyncTransport* transport, folly::EventBase* evb)
        : folly::AsyncTimeout(evb), transport_(transport) {}

    void timeoutExpired() noexcept override {
      transport_->poller_.scanOnce();
      transport_->flushPendingWrites();
      scheduleTimeout(transport_->pollIntervalMs_);
    }

   private:
    CxlMemBenchmarkAsyncTransport* transport_;
  };

  std::shared_ptr<CxlMemBenchmarkResources> resources_;
  StubDoorbellQueue* inboundDataQueue_{nullptr};
  CxlMemBenchmarkPollRegistry* pollRegistry_{nullptr};
  folly::CxlMemPoller<StubBackend> poller_;
  std::shared_ptr<folly::CxlMemPollerQueueHandle<StubBackend>> pollHandle_;
  PollTimeout pollTimeout_;
  uint32_t pollIntervalMs_{1};
};

void CxlMemBenchmarkPollRegistry::add(
    CxlMemBenchmarkAsyncTransport* transport) {
  transports_.push_back(transport);
}

void CxlMemBenchmarkPollRegistry::remove(
    CxlMemBenchmarkAsyncTransport* transport) {
  auto it = std::find(transports_.begin(), transports_.end(), transport);
  if (it != transports_.end()) {
    *it = transports_.back();
    transports_.pop_back();
  }
}

size_t CxlMemBenchmarkPollRegistry::pollOnce() {
  size_t work = 0;
  for (size_t i = 0; i < transports_.size();) {
    auto* transport = transports_[i];
    if (transport->pollOnceInline()) {
      ++work;
    }
    if (i < transports_.size() && transports_[i] == transport) {
      ++i;
    }
  }
  return work;
}

folly::AsyncTransport::UniquePtr makeOwnedTransport(
    folly::EventBase* evb,
    std::shared_ptr<CxlMemBenchmarkResources> resources,
    bool client,
    CxlMemBenchmarkPollMode pollMode,
    CxlMemBenchmarkPollRegistry* pollRegistry = nullptr) {
  StubDoorbellQueue* inboundDataQueue = client
      ? resources->clientInboundDataQueue()
      : resources->serverInboundDataQueue();
  auto config =
      client ? resources->clientConfig(evb) : resources->serverConfig(evb);
  return folly::AsyncTransport::UniquePtr(
      new CxlMemBenchmarkAsyncTransport(
          evb,
          std::move(resources),
          config,
          inboundDataQueue,
          pollMode,
          pollRegistry));
}

RocketRoutingHandler* findRocketHandler(ThriftServer* server) {
  for (const auto& handler : *server->getRoutingHandlers()) {
    auto* rocketHandler = dynamic_cast<RocketRoutingHandler*>(handler.get());
    if (rocketHandler != nullptr) {
      return rocketHandler;
    }
  }
  return nullptr;
}

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

struct CxlMemBenchmarkHandoff {
  CxlMemBenchmarkOptions options;
  CxlMemHandshake handshake;
  folly::SocketAddress peerAddress;
  wangle::TransportInfo tinfo;
};

class CxlMemBenchmarkHotIoShard {
 public:
  CxlMemBenchmarkHotIoShard(
      ThriftServer& server,
      CxlMemBenchmarkOptions options,
      size_t index)
      : server_(server),
        options_(std::move(options)),
        index_(index),
        handoffs_(options_.handoffQueueCapacity),
        thread_([this] { run(); }) {
    ready_.wait();
    if (startupException_) {
      stopping_.store(true, std::memory_order_release);
      if (thread_.joinable()) {
        thread_.join();
      }
      std::rethrow_exception(startupException_);
    }
  }

  ~CxlMemBenchmarkHotIoShard() {
    stop();
  }

  CxlMemBenchmarkHotIoShard(const CxlMemBenchmarkHotIoShard&) = delete;
  CxlMemBenchmarkHotIoShard& operator=(const CxlMemBenchmarkHotIoShard&) =
      delete;

  bool submit(CxlMemBenchmarkHandoff handoff) {
    return handoffs_.write(
        std::make_unique<CxlMemBenchmarkHandoff>(std::move(handoff)));
  }

  void stop() {
    if (stopStarted_.exchange(true, std::memory_order_acq_rel)) {
      return;
    }
    auto* evb = eventBase_.load(std::memory_order_acquire);
    if (evb != nullptr) {
      auto worker = worker_;
      evb->runInEventBaseThread([this, worker] {
        if (worker) {
          worker->dropAllConnections();
        }
        stopping_.store(true, std::memory_order_release);
      });
    } else {
      stopping_.store(true, std::memory_order_release);
    }
    if (thread_.joinable()) {
      thread_.join();
    }
  }

 private:
  using HandoffQueue =
      folly::MPMCQueue<std::unique_ptr<CxlMemBenchmarkHandoff>>;

  void run() noexcept {
    bool readyPosted = false;
    try {
      folly::setThreadName(folly::to<std::string>("cxl_hot_", index_));

      folly::EventBase evb(makeHotIoEventBaseOptions(options_));
      eventBase_.store(&evb, std::memory_order_release);
      worker_ = Cpp2Worker::create(&server_, &evb);
      rocketHandler_ = findRocketHandler(&server_);
      if (rocketHandler_ == nullptr) {
        throw std::runtime_error("RocketRoutingHandler not found");
      }

      evb.loopPollSetup();
      SCOPE_EXIT {
        evb.loopPollCleanup();
      };
      for (size_t i = 0; i < 4; ++i) {
        pollEventBaseQueue(evb);
        evb.loopPoll();
      }

      ready_.post();
      readyPosted = true;

      while (!stopping_.load(std::memory_order_acquire)) {
        bool didWork = drainHandoffs() > 0;
        didWork = pollEventBaseQueue(evb) || didWork;
        didWork = pollWorkerReplyQueue() || didWork;
        didWork = pollRegistry_.pollOnce() > 0 || didWork;
        evb.loopPoll();
        if (!didWork) {
          pauseIdle();
        }
      }
      for (size_t i = 0; i < 4; ++i) {
        pollEventBaseQueue(evb);
        pollWorkerReplyQueue();
        evb.loopPoll();
      }
      worker_.reset();
      eventBase_.store(nullptr, std::memory_order_release);
      pollEventBaseQueue(evb);
      evb.loopPoll();
    } catch (...) {
      startupException_ = std::current_exception();
      if (!readyPosted) {
        ready_.post();
      }
      LOG(ERROR) << "CXL mem benchmark hot IO shard failed: "
                 << folly::exceptionStr(startupException_);
    }
  }

  size_t drainHandoffs() {
    size_t count = 0;
    std::unique_ptr<CxlMemBenchmarkHandoff> handoff;
    while (handoffs_.read(handoff)) {
      acceptHandoff(std::move(*handoff));
      handoff.reset();
      ++count;
    }
    return count;
  }

  void acceptHandoff(CxlMemBenchmarkHandoff handoff) {
    try {
      auto resources = std::make_shared<CxlMemBenchmarkResources>(
          handoff.options, handoff.handshake.connId, false);
      auto* evb = eventBase_.load(std::memory_order_acquire);
      auto transport = makeOwnedTransport(
          evb,
          std::move(resources),
          false,
          CxlMemBenchmarkPollMode::INLINE,
          &pollRegistry_);
      rocketHandler_->handleConnection(
          worker_->getConnectionManager(),
          std::move(transport),
          &handoff.peerAddress,
          handoff.tinfo,
          worker_);
    } catch (const std::exception& ex) {
      LOG(ERROR) << "CXL mem benchmark hot handoff failed: " << ex.what();
    }
  }

  void pauseIdle() const {
    for (uint32_t i = 0; i < options_.hotSpinPauseIterations; ++i) {
      folly::asm_volatile_pause();
    }
  }

  bool pollEventBaseQueue(folly::EventBase& evb) const {
    if (!options_.hotBusyPollEventBase) {
      return false;
    }
    return evb.pollNotificationQueue();
  }

  bool pollWorkerReplyQueue() const {
    if (!options_.hotBusyPollEventBase || !worker_) {
      return false;
    }
    auto& replyQueue = worker_->getReplyQueue();
    auto hadReplies = !replyQueue.empty();
    replyQueue.drain();
    return hadReplies;
  }

  ThriftServer& server_;
  CxlMemBenchmarkOptions options_;
  size_t index_{0};
  HandoffQueue handoffs_;
  std::thread thread_;
  folly::Baton<> ready_;
  std::exception_ptr startupException_;
  std::atomic<bool> stopping_{false};
  std::atomic<bool> stopStarted_{false};
  std::atomic<folly::EventBase*> eventBase_{nullptr};
  std::shared_ptr<Cpp2Worker> worker_;
  RocketRoutingHandler* rocketHandler_{nullptr};
  CxlMemBenchmarkPollRegistry pollRegistry_;
};

class CxlMemBenchmarkHotIoGroup {
 public:
  CxlMemBenchmarkHotIoGroup(
      ThriftServer& server,
      CxlMemBenchmarkOptions options)
      : server_(server), options_(std::move(options)) {
    validateOptions(options_);
    size_t shardCount = options_.hotIoThreads;
    if (shardCount == 0) {
      shardCount = server_.getNumIOWorkerThreads();
    }
    if (shardCount == 0) {
      shardCount = 1;
    }
    shards_.reserve(shardCount);
    for (size_t i = 0; i < shardCount; ++i) {
      shards_.push_back(std::make_unique<CxlMemBenchmarkHotIoShard>(
          server_, options_, i));
    }
  }

  ~CxlMemBenchmarkHotIoGroup() {
    stop();
  }

  CxlMemBenchmarkHotIoGroup(const CxlMemBenchmarkHotIoGroup&) = delete;
  CxlMemBenchmarkHotIoGroup& operator=(const CxlMemBenchmarkHotIoGroup&) =
      delete;

  bool submit(CxlMemBenchmarkHandoff handoff) {
    std::lock_guard<std::mutex> guard(mutex_);
    if (stopping_ || shards_.empty()) {
      return false;
    }
    const size_t shard =
        getCxlMemBenchmarkHotShard(handoff.handshake.connId, shards_.size());
    return shards_[shard]->submit(std::move(handoff));
  }

  void stop() {
    std::vector<std::unique_ptr<CxlMemBenchmarkHotIoShard>> shards;
    {
      std::lock_guard<std::mutex> guard(mutex_);
      if (stopping_) {
        return;
      }
      stopping_ = true;
      shards.swap(shards_);
    }
    for (auto& shard : shards) {
      shard->stop();
    }
  }

 private:
  ThriftServer& server_;
  CxlMemBenchmarkOptions options_;
  std::mutex mutex_;
  bool stopping_{false};
  std::vector<std::unique_ptr<CxlMemBenchmarkHotIoShard>> shards_;
};

class CxlMemBenchmarkRoutingHandler final : public TransportRoutingHandler {
 public:
  CxlMemBenchmarkRoutingHandler(
      ThriftServer& server,
      CxlMemBenchmarkOptions options)
      : server_(server), options_(std::move(options)) {}

  void stopListening() override {
    listening_ = false;
    std::shared_ptr<CxlMemBenchmarkHotIoGroup> hotIoGroup;
    {
      std::lock_guard<std::mutex> guard(hotIoGroupMutex_);
      hotIoGroup.swap(hotIoGroup_);
    }
    if (hotIoGroup) {
      hotIoGroup->stop();
    }
  }

  bool canAcceptConnection(
      const std::vector<uint8_t>& bytes,
      const wangle::TransportInfo&) override {
    return listening_.load() && isCxlMemBenchmarkHandshake(bytes);
  }

  bool canAcceptEncryptedConnection(const std::string&) override {
    return false;
  }

  void handleConnection(
      wangle::ConnectionManager* connectionManager,
      folly::AsyncTransport::UniquePtr sock,
      const folly::SocketAddress* peerAddress,
      const wangle::TransportInfo& tinfo,
      std::shared_ptr<Cpp2Worker> worker) override {
    (void)connectionManager;
    (void)worker;
    try {
      auto preReceived = sock->takePreReceivedData();
      if (!preReceived) {
        throw std::invalid_argument("missing CXL mem prereceived handshake");
      }
      CxlMemHandshake handshake = decodeHandshake(*preReceived);
      CxlMemBenchmarkOptions options = options_;
      options.payloadSliceSize = handshake.payloadSliceSize;
      options.hwQueuesPerDoorbell = handshake.hwQueuesPerDoorbell;
      validateOptions(options);

      folly::SocketAddress address;
      if (peerAddress != nullptr) {
        address = *peerAddress;
      }
      sock->closeNow();

      auto hotIoGroup = getHotIoGroup();
      CxlMemBenchmarkHandoff handoff{
          std::move(options), handshake, std::move(address), tinfo};
      if (!hotIoGroup->submit(std::move(handoff))) {
        throw std::runtime_error("CXL mem benchmark hot handoff queue is full");
      }
    } catch (const std::exception& ex) {
      LOG(ERROR) << "CXL mem benchmark server setup failed: " << ex.what();
      sock->closeNow();
    }
  }

 private:
  std::shared_ptr<CxlMemBenchmarkHotIoGroup> getHotIoGroup() {
    std::lock_guard<std::mutex> guard(hotIoGroupMutex_);
    if (!listening_.load()) {
      throw std::runtime_error("CXL mem benchmark routing handler is stopped");
    }
    if (!hotIoGroup_) {
      hotIoGroup_ = std::make_shared<CxlMemBenchmarkHotIoGroup>(
          server_, options_);
    }
    return hotIoGroup_;
  }

  ThriftServer& server_;
  CxlMemBenchmarkOptions options_;
  std::atomic<bool> listening_{true};
  std::mutex hotIoGroupMutex_;
  std::shared_ptr<CxlMemBenchmarkHotIoGroup> hotIoGroup_;
};

} // namespace

bool isCxlMemBenchmarkTransport(folly::StringPiece transport) {
  return transport == "cxl_mem";
}

bool isCxlMemBenchmarkHandshake(const std::vector<uint8_t>& bytes) {
  return isHandshakeBytes(bytes.data(), bytes.size());
}

size_t getCxlMemBenchmarkHotShard(uint16_t connId, size_t shardCount) {
  if (shardCount == 0) {
    throw std::invalid_argument("CXL mem benchmark requires hot IO shards");
  }
  return connId % shardCount;
}

folly::AsyncTransport::UniquePtr tryCreateCxlMemBenchmarkClientTransport(
    folly::EventBase* evb,
    const folly::SocketAddress& addr,
    CxlMemBenchmarkOptions options) {
  try {
    validateOptions(options);
    if (evb->inRunningEventBaseThread()) {
      LOG(INFO) << "CXL mem benchmark client fallback: EventBase is running";
      return nullptr;
    }

    const uint16_t connId = gNextConnId.fetch_add(1);
    auto resources = std::make_shared<CxlMemBenchmarkResources>(
        options, connId, true);
    if (!writeHandshake(evb, addr, encodeHandshake(connId, options))) {
      return nullptr;
    }
    return makeOwnedTransport(
        evb,
        std::move(resources),
        true,
        CxlMemBenchmarkPollMode::ASYNC_TIMEOUT);
  } catch (const std::exception& ex) {
    LOG(INFO) << "CXL mem benchmark client setup failed: " << ex.what();
    return nullptr;
  }
}

std::unique_ptr<TransportRoutingHandler> createCxlMemBenchmarkRoutingHandler(
    ThriftServer& server,
    CxlMemBenchmarkOptions options) {
  return std::make_unique<CxlMemBenchmarkRoutingHandler>(
      server, std::move(options));
}

} // namespace apache::thrift::perf
