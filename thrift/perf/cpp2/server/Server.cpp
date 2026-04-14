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

#include <glog/logging.h>

#include <fcntl.h>
#include <sys/mman.h>
#include <sys/stat.h>
#include <unistd.h>
#include <thread>

#include <folly/init/Init.h>
#include <folly/io/async/ImportedMemoryProvider.h>
#include <folly/io/async/ShmPollerService.h>
#include <folly/portability/GFlags.h>
#include <folly/system/HardwareConcurrency.h>

#include <proxygen/httpserver/HTTPServerOptions.h>
#include <thrift/lib/cpp2/server/ThriftServer.h>
#include <thrift/lib/cpp2/transport/http2/common/HTTP2RoutingHandler.h>
#include <thrift/perf/cpp2/server/BenchmarkHandler.h>
#include <thrift/perf/cpp2/util/QPSStats.h>

DEFINE_int32(port, 7777, "Server port");
DEFINE_string(
    unix_socket_path, "", "Unix socket to listen on, supersedes port");
DEFINE_bool(
    shm, false, "Use shared memory transport with busy-poll mode");

// CXL memory device files (stub paths — replace with actual device paths)
// memfile0: server→client direction (server writes, client reads)
// memfile1: client→server direction (client writes, server reads)
static constexpr const char* kShmDevFileS2C = "/dev/memfile0";
static constexpr const char* kShmDevFileC2S = "/dev/memfile1";
static constexpr size_t kShmPoolSize = 1UL * 1024 * 1024 * 1024; // 1 GB per direction
static constexpr size_t kShmDataRegionSize = 4 * 1024 * 1024;    // per-connection data

static void* mmapDeviceFile(const char* path, size_t size) {
  int fd = ::open(path, O_RDWR);
  if (fd < 0) {
    LOG(WARNING) << "Cannot open CXL device " << path << ": "
                 << strerror(errno) << ", falling back to anonymous mmap";
    void* ptr = ::mmap(
        nullptr, size, PROT_READ | PROT_WRITE,
        MAP_SHARED | MAP_ANONYMOUS, -1, 0);
    CHECK(ptr != MAP_FAILED) << "anonymous mmap failed: " << strerror(errno);
    return ptr;
  }
  void* ptr = ::mmap(nullptr, size, PROT_READ | PROT_WRITE, MAP_SHARED, fd, 0);
  ::close(fd);
  CHECK(ptr != MAP_FAILED) << "mmap " << path << " failed: " << strerror(errno);
  return ptr;
}

DEFINE_int32(io_threads, 0, "Number of IO threads (0 means number of cores)");
DEFINE_int32(cpu_threads, 0, "Number of CPU threads (0 means number of cores)");
DEFINE_int32(stats_interval_sec, 1, "Seconds between stats");
DEFINE_int32(terminate_sec, 0, "How long to run server (0 means forever)");

using apache::thrift::HTTP2RoutingHandler;
using apache::thrift::ThriftServer;
using apache::thrift::ThriftServerAsyncProcessorFactory;
using facebook::thrift::benchmarks::BenchmarkHandler;
using facebook::thrift::benchmarks::QPSStats;
using proxygen::HTTPServerOptions;
using std::thread;

std::unique_ptr<HTTP2RoutingHandler> createHTTP2RoutingHandler(
    std::shared_ptr<ThriftServer> server) {
  auto h2_options = std::make_unique<HTTPServerOptions>();
  h2_options->threads = static_cast<size_t>(server->getNumIOWorkerThreads());
  h2_options->idleTimeout = server->getIdleTimeout();
  h2_options->shutdownOn = {SIGINT, SIGTERM};
  return std::make_unique<HTTP2RoutingHandler>(
      std::move(h2_options), server->getThriftProcessor(), *server);
}

int main(int argc, char** argv) {
  const folly::Init init(&argc, &argv);

  int32_t numCores = folly::available_concurrency();
  if (FLAGS_io_threads == 0) {
    FLAGS_io_threads = numCores;
  }
  if (FLAGS_cpu_threads == 0) {
    FLAGS_cpu_threads = numCores;
  }
  LOG(INFO) << "Using " << FLAGS_io_threads << " IO threads";
  LOG(INFO) << "Using " << FLAGS_cpu_threads << " CPU threads";

  QPSStats stats;

  auto handler = std::make_shared<BenchmarkHandler>(&stats);
  auto cpp2PFac =
      std::make_shared<ThriftServerAsyncProcessorFactory<BenchmarkHandler>>(
          handler);

  auto server = std::make_shared<ThriftServer>();
  if (!FLAGS_unix_socket_path.empty()) {
    folly::AsyncServerSocket::UniquePtr sock{new folly::AsyncServerSocket};
    folly::SocketAddress addr;
    addr.setFromPath(FLAGS_unix_socket_path);
    sock->bind(addr);
    server->useExistingSocket(std::move(sock));
    LOG(INFO) << "Listening on " << FLAGS_unix_socket_path;
  } else if (FLAGS_shm) {
    // SHM mode requires Unix domain socket - auto-create one
    folly::AsyncServerSocket::UniquePtr sock{new folly::AsyncServerSocket};
    folly::SocketAddress addr;
    addr.setFromPath("/tmp/thrift_shm_benchmark");
    sock->bind(addr);
    server->useExistingSocket(std::move(sock));
    LOG(INFO) << "SHM mode: Listening on /tmp/thrift_shm_benchmark";
  } else {
    LOG(INFO) << "Listening on port " << FLAGS_port;
    server->setPort(FLAGS_port);
  }
  server->setNumIOWorkerThreads(FLAGS_io_threads);
  server->setNumCPUWorkerThreads(FLAGS_cpu_threads);
  server->setInterface(cpp2PFac);

  if (FLAGS_shm) {
    server->setUseShmTransport(true);

    void* s2cMem = mmapDeviceFile(kShmDevFileS2C, kShmPoolSize);
    void* c2sMem = mmapDeviceFile(kShmDevFileC2S, kShmPoolSize);

    auto provider = std::make_shared<folly::ImportedMemoryProvider>();
    provider->registerPool("s2c", s2cMem, kShmPoolSize);
    provider->registerPool("c2s", c2sMem, kShmPoolSize);

    auto pollerService = std::make_shared<folly::ShmPollerService>();
    pollerService->initFromProvider(*provider, "s2c", "c2s", true);
    pollerService->startPollers();

    server->setShmMemoryProvider(std::move(provider));
    server->setShmPollerService(std::move(pollerService));

    LOG(INFO) << "SHM mode: CXL device files " << kShmDevFileS2C
              << " / " << kShmDevFileC2S << " mapped (" << kShmPoolSize
              << " bytes each), ShmPollerService started";
  }

  server->addRoutingHandler(createHTTP2RoutingHandler(server));

  thread logger([&] {
    int32_t elapsedTimeSec = 0;
    if (FLAGS_terminate_sec == 0) {
      // Essentially infinite time.
      FLAGS_terminate_sec = 100000000;
    }
    for (;;) {
      int32_t sleepTimeSec = std::min(
          FLAGS_terminate_sec - elapsedTimeSec, FLAGS_stats_interval_sec);
      /* sleep override */
      std::this_thread::sleep_for(std::chrono::seconds(sleepTimeSec));
      stats.printStats(sleepTimeSec);
      elapsedTimeSec += sleepTimeSec;
      if (elapsedTimeSec >= FLAGS_terminate_sec) {
        server->stop();
        break;
      }
    }
  });

  server->serve();
  logger.join();

  LOG(INFO) << "Server terminating";
  return 0;
}
