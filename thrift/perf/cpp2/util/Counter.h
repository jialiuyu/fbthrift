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

#include <algorithm>
#include <string>
#include <glog/logging.h>
#include <folly/ThreadCachedInt.h>

namespace facebook::thrift::benchmarks {

struct CounterStatsSnapshot {
  std::string name;
  double currentQPS{0};
  double maxQPS{0};
  double avgQPS{0};
  double totalQueries{0};
  double measuredQueries{0};
  bool shouldPrint{false};

  // E2E latency in microseconds
  double currentAvgLatencyUs{0};
  double currentP99LatencyUs{0};
  double overallAvgLatencyUs{0};
  double overallP99LatencyUs{0};
};

class Counter {
 public:
  explicit Counter(std::string name) : name_(std::move(name)), value_(0, 10000) {}

  Counter& operator+=(uint32_t inc) {
    value_ += inc;
    return *this;
  }

  Counter& operator++() {
    ++value_;
    return *this;
  }

  void recordLatency(uint64_t latencyNs) {
    latencySumNs_.fetch_add(latencyNs, std::memory_order_relaxed);
    latencyCount_.fetch_add(1, std::memory_order_relaxed);
    int bucket = latencyBucketIndex(latencyNs);
    latencyBuckets_[bucket].fetch_add(1, std::memory_order_relaxed);
  }

  void startMeasurement() {
    auto queryCount = value_.readFull();
    lastQueryCount_ = queryCount;
    measurementStartQueryCount_ = queryCount;
    measurementElapsedSec_ = 0;
    maxPerSec_ = 0;
    measurementStarted_ = true;

    lastLatencySumNs_ = latencySumNs_.load(std::memory_order_relaxed);
    measurementStartLatencySumNs_ = lastLatencySumNs_;
    lastLatencyCount_ = latencyCount_.load(std::memory_order_relaxed);
    measurementStartLatencyCount_ = lastLatencyCount_;
    for (int i = 0; i < kNumLatencyBuckets; ++i) {
      lastLatencyBuckets_[i] = latencyBuckets_[i].load(std::memory_order_relaxed);
      measurementStartBuckets_[i] = lastLatencyBuckets_[i];
    }
  }

  CounterStatsSnapshot getStatsSnapshot(double secsSinceLastPrint) {
    auto queryCount = static_cast<double>(value_.readFull());

    CounterStatsSnapshot snapshot;
    snapshot.name = name_;
    snapshot.totalQueries = queryCount;

    if (!measurementStarted_) {
      return snapshot;
    }

    auto lastSecAvg = (queryCount - lastQueryCount_) / secsSinceLastPrint;
    lastQueryCount_ = queryCount;
    measurementElapsedSec_ += secsSinceLastPrint;
    maxPerSec_ = std::max(maxPerSec_, lastSecAvg);

    snapshot.currentQPS = lastSecAvg;
    snapshot.maxQPS = maxPerSec_;
    snapshot.measuredQueries = queryCount - measurementStartQueryCount_;
    snapshot.avgQPS = measurementElapsedSec_ > 0
        ? snapshot.measuredQueries / measurementElapsedSec_
        : 0;
    snapshot.shouldPrint = queryCount > 0;

    // Current interval latency
    uint64_t curSum = latencySumNs_.load(std::memory_order_relaxed);
    uint64_t curCount = latencyCount_.load(std::memory_order_relaxed);
    uint64_t intervalSum = curSum - lastLatencySumNs_;
    uint64_t intervalCount = curCount - lastLatencyCount_;
    if (intervalCount > 0) {
      snapshot.currentAvgLatencyUs =
          static_cast<double>(intervalSum) / intervalCount / 1000.0;
      snapshot.currentP99LatencyUs =
          computeP99From(lastLatencyBuckets_);
    }
    lastLatencySumNs_ = curSum;
    lastLatencyCount_ = curCount;
    for (int i = 0; i < kNumLatencyBuckets; ++i) {
      lastLatencyBuckets_[i] = latencyBuckets_[i].load(std::memory_order_relaxed);
    }

    // Overall latency since measurement started
    uint64_t overallCount = curCount - measurementStartLatencyCount_;
    if (overallCount > 0) {
      snapshot.overallAvgLatencyUs =
          static_cast<double>(curSum - measurementStartLatencySumNs_) /
          overallCount / 1000.0;
      snapshot.overallP99LatencyUs =
          computeP99From(measurementStartBuckets_);
    }

    return snapshot;
  }

 private:
  // Latency histogram: power-of-2 nanosecond buckets
  // Bucket 0: [0, 512ns)   Bucket 1: [512ns, 1us)
  // Bucket k (k>=2): [2^(k+8), 2^(k+9)) ns
  static constexpr int kBucketOffset = 9; // 2^9 = 512
  static constexpr int kNumLatencyBuckets = 24;

  int latencyBucketIndex(uint64_t latencyNs) const {
    if (latencyNs < (1ULL << kBucketOffset)) return 0;
    int idx = 63 - __builtin_clzll(latencyNs) - kBucketOffset + 1;
    if (idx < 0) idx = 0;
    if (idx >= kNumLatencyBuckets) idx = kNumLatencyBuckets - 1;
    return idx;
  }

  double computeP99From(const uint64_t startBuckets[kNumLatencyBuckets]) const {
    uint64_t total = 0;
    uint64_t diffs[kNumLatencyBuckets];
    for (int i = 0; i < kNumLatencyBuckets; ++i) {
      diffs[i] = latencyBuckets_[i].load(std::memory_order_relaxed) -
          startBuckets[i];
      total += diffs[i];
    }
    if (total == 0) return 0;

    uint64_t target = static_cast<uint64_t>(total * 99ULL / 100ULL);
    uint64_t cumulative = 0;
    for (int i = 0; i < kNumLatencyBuckets; ++i) {
      cumulative += diffs[i];
      if (cumulative >= target) {
        // Upper bound of this bucket as P99 estimate (in microseconds)
        uint64_t upperNs = (i == 0) ? (1ULL << kBucketOffset)
                                     : (1ULL << (i + kBucketOffset));
        return static_cast<double>(upperNs) / 1000.0;
      }
    }
    return 0;
  }

  std::string name_;
  folly::ThreadCachedInt<uint32_t> value_;
  double lastQueryCount_{0};
  double measurementStartQueryCount_{0};
  double measurementElapsedSec_{0};
  double maxPerSec_{0};
  bool measurementStarted_{false};

  // Latency tracking
  std::atomic<uint64_t> latencySumNs_{0};
  std::atomic<uint64_t> latencyCount_{0};
  std::atomic<uint64_t> latencyBuckets_[kNumLatencyBuckets]{};

  uint64_t lastLatencySumNs_{0};
  uint64_t measurementStartLatencySumNs_{0};
  uint64_t lastLatencyCount_{0};
  uint64_t measurementStartLatencyCount_{0};
  uint64_t lastLatencyBuckets_[kNumLatencyBuckets]{};
  uint64_t measurementStartBuckets_[kNumLatencyBuckets]{};
};

} // namespace facebook::thrift::benchmarks
