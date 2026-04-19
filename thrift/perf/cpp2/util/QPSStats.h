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
#include <cstdint>
#include <map>
#include <string>
#include <utility>
#include <mutex>
#include <glog/logging.h>
#include <thrift/perf/cpp2/util/Counter.h>

namespace facebook::thrift::benchmarks {

class QPSStats {
 public:
  explicit QPSStats(int32_t warmupSec = 0)
      : warmupSec_(std::max(0, warmupSec)),
        measurementStarted_(warmupSec_ == 0) {}

  void printStats(double secsSinceLastPrint) {
    if (!measurementStarted_) {
      elapsedSec_ += secsSinceLastPrint;
      if (elapsedSec_ < warmupSec_) {
        LOG(INFO) << " | Warmup: " << elapsedSec_ << "/" << warmupSec_
                  << " sec";
        return;
      }

      measurementStarted_ = true;
      measurementElapsedSec_ = 0;
      for (auto& pair : counters_) {
        pair.second->startMeasurement();
      }
      LOG(INFO) << " | Warmup complete, starting QPS measurement";
      return;
    }

    measurementElapsedSec_ += secsSinceLastPrint;
    double totalCurrentQPS = 0;
    double totalMeasuredQueries = 0;
    double totalLatencySum = 0;
    double totalLatencyCount = 0;
    double totalOverallLatencySum = 0;
    double totalOverallLatencyCount = 0;

    for (auto& pair : counters_) {
      auto snapshot = pair.second->getStatsSnapshot(secsSinceLastPrint);
      totalCurrentQPS += snapshot.currentQPS;
      totalMeasuredQueries += snapshot.measuredQueries;
      if (!snapshot.shouldPrint) {
        continue;
      }

      // Only print latency for operation counters (skip error/timeout/fatal)
      bool hasLatency = snapshot.currentAvgLatencyUs > 0 ||
          snapshot.overallAvgLatencyUs > 0;
      if (hasLatency) {
        LOG(INFO) << std::scientific
                  << " | QPS: " << snapshot.currentQPS
                  << " | Max QPS: " << snapshot.maxQPS
                  << " | Avg QPS: " << snapshot.avgQPS
                  << " | (RT)avg(us): " << snapshot.currentAvgLatencyUs
                  << " | (RT)P99(us): " << snapshot.currentP99LatencyUs
                  << " | (Avg)avg(us): " << snapshot.overallAvgLatencyUs
                  << " | (Avg)P99(us): " << snapshot.overallP99LatencyUs
                  << " | Total Queries: " << snapshot.totalQueries
                  << " | Operation: " << snapshot.name;
        totalLatencySum += snapshot.currentAvgLatencyUs * snapshot.currentQPS;
        totalLatencyCount += snapshot.currentQPS;
        totalOverallLatencySum +=
            snapshot.overallAvgLatencyUs * snapshot.measuredQueries;
        totalOverallLatencyCount += snapshot.measuredQueries;
      } else {
        LOG(INFO) << std::scientific
                  << " | QPS: " << snapshot.currentQPS
                  << " | Max QPS: " << snapshot.maxQPS
                  << " | Avg QPS: " << snapshot.avgQPS
                  << " | Total Queries: " << snapshot.totalQueries
                  << " | Operation: " << snapshot.name;
      }
    }

    totalMaxQPS_ = std::max(totalMaxQPS_, totalCurrentQPS);
    auto totalAvgQPS = measurementElapsedSec_ > 0
        ? totalMeasuredQueries / measurementElapsedSec_
        : 0;
    double totalAvgLatency = totalLatencyCount > 0
        ? totalLatencySum / totalLatencyCount
        : 0;
    double totalOverallAvgLatency = totalOverallLatencyCount > 0
        ? totalOverallLatencySum / totalOverallLatencyCount
        : 0;

    if (totalAvgLatency > 0) {
      LOG(INFO) << std::scientific
                << " | TOTAL QPS: " << totalCurrentQPS
                << " | TOTAL Max QPS: " << totalMaxQPS_
                << " | TOTAL Avg QPS: " << totalAvgQPS
                << " | TOTAL (RT)avg(us): " << totalAvgLatency
                << " | TOTAL (Avg)avg(us): " << totalOverallAvgLatency;
    } else {
      LOG(INFO) << std::scientific
                << " | TOTAL QPS: " << totalCurrentQPS
                << " | TOTAL Max QPS: " << totalMaxQPS_
                << " | TOTAL Avg QPS: " << totalAvgQPS;
    }
  }

  void registerCounter(std::string name) {
    // TODO: Each thread in the Runner creates an instance of an Operation.
    // Each instance of the operation calls registerCounter with given name.
    // So this function is being called as the number of threads.
    // We should make this function to be called per type of the Operation, not
    // per instance.
    std::lock_guard<std::mutex> guard(mutex_);
    auto [it, inserted] = counters_.emplace(name, std::make_unique<Counter>(name));
    if (inserted && measurementStarted_) {
      it->second->startMeasurement();
    }
  }

  void add(std::string& name) { ++(*counters_[name]); }

  void add(std::string& name, uint32_t sz) { (*counters_[name]) += sz; }

  void recordLatency(const std::string& name, uint64_t latencyNs) {
    auto it = counters_.find(name);
    if (it != counters_.end()) {
      it->second->recordLatency(latencyNs);
    }
  }

  void recordLatency(const char* name, uint64_t latencyNs) {
    recordLatency(std::string(name), latencyNs);
  }

 private:
  std::map<std::string, std::unique_ptr<Counter>> counters_;
  std::mutex mutex_;
  double elapsedSec_{0};
  double measurementElapsedSec_{0};
  double totalMaxQPS_{0};
  int32_t warmupSec_{0};
  bool measurementStarted_{false};
};

} // namespace facebook::thrift::benchmarks
