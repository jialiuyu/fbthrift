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

#include <algorithm>
#include <cerrno>
#include <cstring>
#include <fcntl.h>
#include <stdexcept>
#include <sys/mman.h>
#include <sys/stat.h>

#include <folly/io/async/ImportedMemoryProvider.h>
#include <folly/io/async/ShmPollerService.h>
#include <folly/system/HardwareConcurrency.h>
#include <thrift/perf/cpp2/if/gen-cpp2/StreamBenchmark.h>
#include <thrift/perf/cpp2/util/Operation.h>
#include <thrift/perf/cpp2/util/QPSStats.h>
#include <thrift/perf/cpp2/util/Runner.h>
#include <thrift/perf/cpp2/util/Util.h>

using facebook::thrift::benchmarks::StreamBenchmarkAsyncClient;

// Server Settings
DEFINE_string(host, "::1", "Server host");
DEFINE_int32(port, 7777, "Server port");
DEFINE_string(
    unix_socket_path, "", "Unix socket to connect to, supersedes host:port");

// Client Settings
DEFINE_int32(num_clients, 0, "Number of clients to use. (Default: 1 per core)");
DEFINE_string(transport, "header", "Transport to use: header, rocket, http2, shm");

// CXL memory device files (stub paths — must match Server.cpp)
// memfile0: server→client direction (server writes, client reads)
// memfile1: client→server direction (client writes, server reads)
static constexpr const char* kShmDevFileS2C = "/dev/memfile0";
static constexpr const char* kShmDevFileC2S = "/dev/memfile1";
static constexpr size_t kShmPoolSize = 1UL * 1024 * 1024 * 1024; // 1 GB per direction
static constexpr size_t kShmDataRegionSize = 4 * 1024 * 1024;    // per-connection data

static void* mmapDeviceFile(const char* path, size_t size) {
  int fd = ::open(path, O_RDWR);
  if (fd < 0) {
    throw std::runtime_error(
        std::string("Cannot open CXL device ") + path + ": " +
        std::strerror(errno));
  }
  void* ptr = ::mmap(nullptr, size, PROT_READ | PROT_WRITE, MAP_SHARED, fd, 0);
  int savedErrno = errno;
  ::close(fd);
  if (ptr == MAP_FAILED) {
    throw std::runtime_error(
        std::string("mmap failed for CXL device ") + path + ": " +
        std::strerror(savedErrno));
  }
  return ptr;
}

static std::shared_ptr<folly::ShmPollerService> makeShmPollerService() {
  void* c2sMem = mmapDeviceFile(kShmDevFileC2S, kShmPoolSize);
  void* s2cMem = mmapDeviceFile(kShmDevFileS2C, kShmPoolSize);

  auto provider = std::make_shared<folly::ImportedMemoryProvider>();
  provider->registerPool("c2s", c2sMem, kShmPoolSize);
  provider->registerPool("s2c", s2cMem, kShmPoolSize);

  auto pollerService = std::make_shared<folly::ShmPollerService>();
  pollerService->initFromProvider(*provider, "c2s", "s2c", false);
  pollerService->startPollers();

  LOG(INFO) << "SHM client: CXL device files " << kShmDevFileS2C
            << " / " << kShmDevFileC2S << " mapped (" << kShmPoolSize
            << " bytes each), ShmPollerService started";
  return pollerService;
}

// General Settings
DEFINE_int32(stats_interval_sec, 1, "Seconds between stats");
DEFINE_int32(terminate_sec, 0, "How long to run client (0 means forever)");

// Operations Settings
DEFINE_bool(sync, false, "Perform synchronous calls to the server");
DEFINE_int32(max_outstanding_ops, 100, "Max number of outstanding async ops");

// Operations - Match with OP_TYPE enum
DEFINE_int32(noop_weight, 0, "Test with a no operation");
DEFINE_int32(noop_oneway_weight, 0, "Test with a oneway no operation");
DEFINE_int32(sum_weight, 0, "Test with a sum operation");
DEFINE_int32(timeout_weight, 0, "Test for timeout functionality");
DEFINE_int32(download_weight, 0, "Test for download functionality");
DEFINE_int32(upload_weight, 0, "Test for upload functionality");
DEFINE_int32(stream_weight, 0, "Test stream download functionality");
DEFINE_int32(semifuture_sum_weight, 0, "Test with a semifuture_sum operation");
DEFINE_int32(co_sum_weight, 0, "Test with a co_sum operation");

DEFINE_uint32(chunk_size, 1024, "Number of bytes per chunk");
DEFINE_uint32(batch_size, 16, "Flow control batch size");

/*
 * This starts num_clients threads with a unique client in each thread.
 * Each client also contains its own eventbase which handles both
 * outgoing and incoming connections.
 */
int main(int argc, char** argv) {
  const folly::Init init(&argc, &argv);
  if (FLAGS_num_clients == 0) {
    int32_t numCores = folly::available_concurrency();
    FLAGS_num_clients = numCores;
  }

  std::shared_ptr<folly::ShmPollerService> shmPollerService;
  if (FLAGS_transport == "shm") {
    shmPollerService = makeShmPollerService();
  }

  QPSStats stats;
  std::vector<std::thread> threads;
  std::vector<std::shared_ptr<folly::EventBase>> evbs;
  for (int i = 0; i < FLAGS_num_clients; ++i) {
    auto evb = std::make_shared<folly::EventBase>();
    evbs.push_back(evb);
    threads.emplace_back([&, evb = std::move(evb)]() {
      folly::SocketAddress addr;
      if (!FLAGS_unix_socket_path.empty()) {
        LOG(INFO) << "Connecting to bootstrap UDS " << FLAGS_unix_socket_path;
        addr.setFromPath(FLAGS_unix_socket_path);
      } else {
        LOG(INFO) << (FLAGS_transport == "shm"
                          ? "Connecting to SHM bootstrap TCP "
                          : "Connecting ")
                  << "[" << FLAGS_host << "]:" << FLAGS_port;
        addr.setFromHostPort(FLAGS_host, FLAGS_port);
      }
      auto client = newClient<StreamBenchmarkAsyncClient>(
          evb.get(), addr, FLAGS_transport, false, shmPollerService.get());

      // Create the Operations and their Discrete Distributions
      // Every time a new operation is added, the distribution needs to
      // be updated. Otherwise, it will never be chosen.
      auto ops = std::make_unique<Operation<StreamBenchmarkAsyncClient>>(
          std::move(client), &stats);
      auto weights = std::vector<int32_t>{
          FLAGS_noop_weight,
          FLAGS_noop_oneway_weight,
          FLAGS_sum_weight,
          FLAGS_timeout_weight,
          FLAGS_download_weight,
          FLAGS_upload_weight,
          FLAGS_stream_weight,
          FLAGS_semifuture_sum_weight,
          FLAGS_co_sum_weight,
      };
      int32_t sum = std::accumulate(weights.begin(), weights.end(), 0);
      if (sum == 0) {
        weights[0] = 1;
      }
      auto distribution = std::make_unique<std::discrete_distribution<int32_t>>(
          weights.begin(), weights.end());

      // Create the runner and execute multiple operations
      auto r = std::make_unique<Runner<StreamBenchmarkAsyncClient>>(
          std::move(ops), std::move(distribution), FLAGS_max_outstanding_ops);
      r->run();

      // Drain the evb before destructing the operations that might still be
      // referenced by it.
      SCOPE_EXIT {
        LOG(INFO) << "Requesting thread exit";
        r->loopUntilExit(evb.get());
      };

      // Run eventbase loop for async operations
      if (!FLAGS_sync) {
        evb->loopForever();
      }
    });
  }

  // Closing connections
  int32_t elapsedTimeSec = 0;
  if (FLAGS_terminate_sec == 0) {
    // Essentially infinite time.
    FLAGS_terminate_sec = 100000000;
  }
  while (true) {
    int32_t sleepTimeSec = std::min(
        FLAGS_terminate_sec - elapsedTimeSec, FLAGS_stats_interval_sec);
    /* sleep override */
    std::this_thread::sleep_for(std::chrono::seconds(sleepTimeSec));
    stats.printStats(sleepTimeSec);
    elapsedTimeSec += sleepTimeSec;
    if (elapsedTimeSec >= FLAGS_terminate_sec) {
      break;
    }
  }
  for (auto& evb : evbs) {
    evb->terminateLoopSoon();
  }
  for (auto& thr : threads) {
    thr.join();
  }
  LOG(INFO) << "Client terminating";
  return 0;
}
