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
#include <folly/io/IOBuf.h>
#include <folly/io/async/AsyncSocket.h>
#include <folly/io/async/AsyncTimeout.h>
#include <folly/io/async/CxlMemAsyncTransport.h>
#include <folly/io/async/CxlMemPoller.h>
#include <folly/io/async/CxlMemRegion.h>
#include <folly/io/async/fdsock/AsyncFdSocket.h>
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
#include <stdexcept>
#include <thread>
#include <utility>

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

class CxlMemBenchmarkAsyncTransport final : public StubTransport {
 public:
  CxlMemBenchmarkAsyncTransport(
      folly::EventBase* evb,
      std::shared_ptr<CxlMemBenchmarkResources> resources,
      StubTransport::Config config,
      StubDoorbellQueue* inboundDataQueue)
      : StubTransport(config),
        resources_(std::move(resources)),
        pollTimeout_(this, evb),
        pollIntervalMs_(resources_->pollIntervalMs()) {
    folly::CxlMemPollerQueueOptions options;
    pollHandle_ = poller_.addQueue(
        evb,
        inboundDataQueue,
        [this](uint64_t item) { drainInbound(64, item, true); },
        options);
    pollTimeout_.scheduleTimeout(pollIntervalMs_);
  }

  ~CxlMemBenchmarkAsyncTransport() override {
    pollTimeout_.cancelTimeout();
    if (pollHandle_) {
      pollHandle_->close();
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
  folly::CxlMemPoller<StubBackend> poller_;
  std::shared_ptr<folly::CxlMemPollerQueueHandle<StubBackend>> pollHandle_;
  PollTimeout pollTimeout_;
  uint32_t pollIntervalMs_{1};
};

folly::AsyncTransport::UniquePtr makeOwnedTransport(
    folly::EventBase* evb,
    std::shared_ptr<CxlMemBenchmarkResources> resources,
    bool client) {
  StubDoorbellQueue* inboundDataQueue = client
      ? resources->clientInboundDataQueue()
      : resources->serverInboundDataQueue();
  auto config =
      client ? resources->clientConfig(evb) : resources->serverConfig(evb);
  return folly::AsyncTransport::UniquePtr(
      new CxlMemBenchmarkAsyncTransport(
          evb, std::move(resources), config, inboundDataQueue));
}

class CxlMemBenchmarkRoutingHandler final : public TransportRoutingHandler {
 public:
  CxlMemBenchmarkRoutingHandler(
      ThriftServer&,
      CxlMemBenchmarkOptions options)
      : options_(std::move(options)) {}

  void stopListening() override { listening_ = false; }

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

      auto resources = std::make_shared<CxlMemBenchmarkResources>(
          std::move(options), handshake.connId, false);
      auto* evb = sock->getEventBase();
      sock->closeNow();

      auto transport = makeOwnedTransport(evb, std::move(resources), false);
      auto* rocketHandler = findRocketHandler(worker.get());
      if (rocketHandler == nullptr) {
        throw std::runtime_error("RocketRoutingHandler not found");
      }
      rocketHandler->handleConnection(
          connectionManager,
          std::move(transport),
          peerAddress,
          tinfo,
          std::move(worker));
    } catch (const std::exception& ex) {
      LOG(ERROR) << "CXL mem benchmark server setup failed: " << ex.what();
      sock->closeNow();
    }
  }

 private:
  RocketRoutingHandler* findRocketHandler(Cpp2Worker* worker) {
    for (const auto& handler : *worker->getServer()->getRoutingHandlers()) {
      auto* rocketHandler = dynamic_cast<RocketRoutingHandler*>(handler.get());
      if (rocketHandler != nullptr) {
        return rocketHandler;
      }
    }
    return nullptr;
  }

  CxlMemBenchmarkOptions options_;
  std::atomic<bool> listening_{true};
};

} // namespace

bool isCxlMemBenchmarkTransport(folly::StringPiece transport) {
  return transport == "cxl_mem";
}

bool isCxlMemBenchmarkHandshake(const std::vector<uint8_t>& bytes) {
  return isHandshakeBytes(bytes.data(), bytes.size());
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
    return makeOwnedTransport(evb, std::move(resources), true);
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
