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

#pragma once

#include <memory>

#include <folly/io/async/EventBase.h>
#include <folly/io/async/SharedMemoryTransport.h>
#include <thrift/lib/cpp2/async/RocketClientChannel.h>

namespace apache::thrift::rocket::sharedmemory {

/**
 * SharedMemoryRocketChannelConfig holds configuration for shared memory
 * Rocket channel.
 */
struct SharedMemoryRocketChannelConfig {
  // Size of each shared memory data region (default: 4MB)
  size_t dataRegionSize = 4 * 1024 * 1024;

  // Name prefix for shared memory regions
  std::string shmNamePrefix = "/thrift_rocket_shm_";

  // TCP connect timeout for handshake
  std::chrono::milliseconds connectTimeout{5000};

  // RPC timeout
  std::chrono::milliseconds rpcTimeout{60000};

  // Enable debug logging
  bool debugLogging = false;
};

/**
 * SharedMemoryRocketChannel provides a RocketClientChannel that uses
 * shared memory for communication instead of regular TCP sockets.
 *
 * Communication Flow:
 * 1. A TCP connection is established first
 * 2. Both sides exchange shared memory region information via handshake
 * 3. The TCP connection is closed after successful handshake
 * 4. Subsequent communication uses shared memory + GQM notifications
 *
 * Usage:
 *   // Client:
 *   auto channel = SharedMemoryRocketChannel::newChannel(
 *       evb, "localhost", 12345, config);
 *   auto client = std::make_unique<MyServiceClient>(std::move(channel));
 *
 *   // Server:
 *   // Need to set up a special routing handler that upgrades TCP to shared memory
 */
class SharedMemoryRocketChannel : public RocketClientChannel {
 public:
  using Ptr = std::unique_ptr<SharedMemoryRocketChannel, folly::DelayedDestruction::Destructor>;
  using Config = SharedMemoryRocketChannelConfig;

  /**
   * Create a new SharedMemoryRocketChannel by connecting to a server.
   *
   * @param evb EventBase for async operations
   * @param host Server host to connect to
   * @param port Server port
   * @param config Configuration
   * @return Ptr to the created channel
   */
  static Ptr newChannel(
      folly::EventBase* evb,
      const std::string& host,
      uint16_t port,
      const Config& config = {});

  /**
   * Create a new SharedMemoryRocketChannel from an existing TCP socket.
   * The socket will be used for handshake and then upgraded to shared memory.
   *
   * @param evb EventBase for async operations
   * @param socket Connected TCP socket
   * @param config Configuration
   * @return Ptr to the created channel
   */
  static Ptr newChannel(
      folly::EventBase* evb,
      folly::AsyncTransport::UniquePtr socket,
      const Config& config = {});

  /**
   * Create a SharedMemoryRocketChannel from already-established shared memory.
   * This is useful when the handshake was performed externally.
   *
   * @param evb EventBase for async operations
   * @param transport Already-configured SharedMemoryTransport
   * @param setupMetadata Setup metadata for Rocket protocol
   * @return Ptr to the created channel
   */
  static Ptr newChannelFromTransport(
      folly::EventBase* evb,
      folly::SharedMemoryTransport::UniquePtr transport,
      RequestSetupMetadata setupMetadata = {});

  ~SharedMemoryRocketChannel() override;

  /**
   * Get the underlying SharedMemoryTransport
   */
  folly::SharedMemoryTransport* getSharedMemoryTransport();
  const folly::SharedMemoryTransport* getSharedMemoryTransport() const;

  /**
   * Get statistics from the transport
   */
  folly::SharedMemoryTransport::Stats getStats() const;

 private:
  explicit SharedMemoryRocketChannel(
      folly::EventBase* evb,
      folly::AsyncTransport::UniquePtr socket);

  // Performs the TCP handshake and creates shared memory transport
  static folly::SharedMemoryTransport::UniquePtr performHandshake(
      folly::EventBase* evb,
      folly::AsyncTransport::UniquePtr socket,
      const Config& config);

  Config config_;
  folly::SharedMemoryTransport::UniquePtr shmTransport_;
};

/**
 * SharedMemoryRocketServerHandler handles the server-side of shared memory
 * Rocket connections. It performs the handshake and creates shared memory
 * transport for incoming connections.
 *
 * Usage:
 *   auto handler = std::make_unique<SharedMemoryRocketServerHandler>(
 *       evb, processor, config);
 *   // Use with ThriftServer or manual socket handling
 */
class SharedMemoryRocketServerHandler {
 public:
  using Config = SharedMemoryRocketChannelConfig;

  /**
   * Create a new SharedMemoryRocketServerHandler.
   *
   * @param evb EventBase for async operations
   * @param config Configuration
   */
  explicit SharedMemoryRocketServerHandler(
      folly::EventBase* evb,
      const Config& config = {});

  ~SharedMemoryRocketServerHandler();

  /**
   * Handle an incoming TCP connection and upgrade it to shared memory.
   *
   * @param socket The incoming TCP connection
   * @return SharedMemoryTransport if upgrade succeeds, nullptr otherwise
   */
  folly::SharedMemoryTransport::UniquePtr handleConnection(
      folly::AsyncTransport::UniquePtr socket);

  /**
   * Get the number of active connections
   */
  size_t getActiveConnectionCount() const { return activeConnections_; }

 private:
  folly::EventBase* evb_;
  Config config_;
  std::atomic<size_t> activeConnections_{0};
};

} // namespace apache::thrift::rocket::sharedmemory
