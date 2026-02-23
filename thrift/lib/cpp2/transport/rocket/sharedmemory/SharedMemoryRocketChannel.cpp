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

#include <thrift/lib/cpp2/transport/rocket/sharedmemory/SharedMemoryRocketChannel.h>

#include <folly/io/async/AsyncSocket.h>
#include <folly/logging/xlog.h>

namespace apache::thrift::rocket::sharedmemory {

// ========== SharedMemoryRocketChannel Implementation ==========

SharedMemoryRocketChannel::SharedMemoryRocketChannel(
    folly::EventBase* evb,
    folly::AsyncTransport::UniquePtr socket)
    : RocketClientChannel(
          evb,
          std::move(socket),
          RequestSetupMetadata()) {
  XLOG(DBG) << "SharedMemoryRocketChannel created";
}

SharedMemoryRocketChannel::~SharedMemoryRocketChannel() {
  XLOG(DBG) << "SharedMemoryRocketChannel destroyed";
}

SharedMemoryRocketChannel::Ptr SharedMemoryRocketChannel::newChannel(
    folly::EventBase* evb,
    const std::string& host,
    uint16_t port,
    const Config& config) {
  // Create TCP socket and connect
  auto socket = folly::AsyncSocket::newSocket(evb);
  folly::SocketAddress addr(host, port);

  // Connect synchronously (in production, use async connect)
  socket->connect(nullptr, addr, config.connectTimeout.count());

  return newChannel(evb, folly::AsyncTransport::UniquePtr(socket.release()), config);
}

SharedMemoryRocketChannel::Ptr SharedMemoryRocketChannel::newChannel(
    folly::EventBase* evb,
    folly::AsyncTransport::UniquePtr socket,
    const Config& config) {
  if (!socket || !socket->good()) {
    XLOG(ERR) << "Invalid socket for SharedMemoryRocketChannel";
    return nullptr;
  }

  // Perform handshake and create shared memory transport
  auto shmTransport = performHandshake(evb, std::move(socket), config);
  if (!shmTransport) {
    XLOG(ERR) << "Failed to create shared memory transport";
    return nullptr;
  }

  // Create setup metadata for Rocket protocol
  RequestSetupMetadata setupMetadata;
  setupMetadata.protocolVersion_ref() = 2;  // Rocket protocol version

  return newChannelFromTransport(evb, std::move(shmTransport), std::move(setupMetadata));
}

SharedMemoryRocketChannel::Ptr SharedMemoryRocketChannel::newChannelFromTransport(
    folly::EventBase* evb,
    folly::SharedMemoryTransport::UniquePtr transport,
    RequestSetupMetadata setupMetadata) {
  if (!transport || !transport->good()) {
    XLOG(ERR) << "Invalid shared memory transport";
    return nullptr;
  }

  // Create RocketClientChannel with the shared memory transport
  // We need to wrap it in a regular AsyncTransport::UniquePtr
  folly::AsyncTransport::UniquePtr asyncTransport(transport.release());

  auto channel = Ptr(new SharedMemoryRocketChannel(evb, std::move(asyncTransport)));
  return channel;
}

folly::SharedMemoryTransport::UniquePtr SharedMemoryRocketChannel::performHandshake(
    folly::EventBase* evb,
    folly::AsyncTransport::UniquePtr socket,
    const Config& config) {
  // Use SharedMemoryTransport's built-in client creation
  folly::SharedMemoryTransportConfig shmConfig;
  shmConfig.dataRegionSize = config.dataRegionSize;
  shmConfig.shmNamePrefix = config.shmNamePrefix;
  shmConfig.debugLogging = config.debugLogging;

  try {
    return folly::SharedMemoryTransport::createClient(
        evb, std::move(socket), shmConfig);
  } catch (const std::exception& e) {
    XLOG(ERR) << "Handshake failed: " << e.what();
    return nullptr;
  }
}

folly::SharedMemoryTransport* SharedMemoryRocketChannel::getSharedMemoryTransport() {
  return dynamic_cast<folly::SharedMemoryTransport*>(getTransport());
}

const folly::SharedMemoryTransport* SharedMemoryRocketChannel::getSharedMemoryTransport() const {
  return dynamic_cast<const folly::SharedMemoryTransport*>(getTransport());
}

folly::SharedMemoryTransport::Stats SharedMemoryRocketChannel::getStats() const {
  if (auto* transport = getSharedMemoryTransport()) {
    return transport->getStats();
  }
  return {};
}

// ========== SharedMemoryRocketServerHandler Implementation ==========

SharedMemoryRocketServerHandler::SharedMemoryRocketServerHandler(
    folly::EventBase* evb,
    const Config& config)
    : evb_(evb), config_(config) {
  XLOG(DBG) << "SharedMemoryRocketServerHandler created";
}

SharedMemoryRocketServerHandler::~SharedMemoryRocketServerHandler() {
  XLOG(DBG) << "SharedMemoryRocketServerHandler destroyed";
}

folly::SharedMemoryTransport::UniquePtr SharedMemoryRocketServerHandler::handleConnection(
    folly::AsyncTransport::UniquePtr socket) {
  if (!socket || !socket->good()) {
    XLOG(ERR) << "Invalid socket for shared memory upgrade";
    return nullptr;
  }

  folly::SharedMemoryTransportConfig shmConfig;
  shmConfig.dataRegionSize = config_.dataRegionSize;
  shmConfig.shmNamePrefix = config_.shmNamePrefix;
  shmConfig.debugLogging = config_.debugLogging;

  try {
    auto transport = folly::SharedMemoryTransport::createServer(
        evb_, std::move(socket), shmConfig);
    activeConnections_++;
    XLOG(DBG) << "Shared memory connection established, active: " << activeConnections_;

    // Set up close callback to track connection count
    transport->setCloseCallback([this]() {
      activeConnections_--;
      XLOG(DBG) << "Shared memory connection closed, active: " << activeConnections_;
    });

    return transport;
  } catch (const std::exception& e) {
    XLOG(ERR) << "Server handshake failed: " << e.what();
    return nullptr;
  }
}

} // namespace apache::thrift::rocket::sharedmemory
