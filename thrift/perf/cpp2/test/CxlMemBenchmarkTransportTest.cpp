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

#include <gtest/gtest.h>

#include <stdexcept>

namespace apache::thrift::perf {
namespace {

TEST(CxlMemBenchmarkTransport, HotShardUsesConnIdModuloShardCount) {
  EXPECT_EQ(0, getCxlMemBenchmarkHotShard(1, 1));
  EXPECT_EQ(1, getCxlMemBenchmarkHotShard(1, 4));
  EXPECT_EQ(0, getCxlMemBenchmarkHotShard(4, 4));
  EXPECT_EQ(3, getCxlMemBenchmarkHotShard(7, 4));
}

TEST(CxlMemBenchmarkTransport, HotShardRejectsZeroShardCount) {
  EXPECT_THROW(
      getCxlMemBenchmarkHotShard(1, 0), std::invalid_argument);
}

} // namespace
} // namespace apache::thrift::perf
