# Copyright (c) Meta Platforms, Inc. and affiliates.
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

cmake_minimum_required(VERSION 3.16)
project(thrift_perf_benchmark CXX C)

set(CMAKE_CXX_STANDARD 20)
set(CMAKE_CXX_STANDARD_REQUIRED ON)
set(CMAKE_CXX_EXTENSIONS OFF)

if(DEFINED ENV{PERF_CPP2_GFLAGS_INCLUDE_DIR} AND
   NOT "$ENV{PERF_CPP2_GFLAGS_INCLUDE_DIR}" STREQUAL "")
  include_directories(BEFORE SYSTEM "$ENV{PERF_CPP2_GFLAGS_INCLUDE_DIR}")
endif()

if(DEFINED ENV{PERF_CPP2_GLOG_INCLUDE_DIR} AND
   NOT "$ENV{PERF_CPP2_GLOG_INCLUDE_DIR}" STREQUAL "")
  include_directories(BEFORE SYSTEM "$ENV{PERF_CPP2_GLOG_INCLUDE_DIR}")
endif()

if(EXISTS "/opt/homebrew/opt/libsodium/lib")
  link_directories("/opt/homebrew/opt/libsodium/lib")
endif()

if(EXISTS "/opt/homebrew/opt/icu4c/lib")
  link_directories("/opt/homebrew/opt/icu4c/lib")
endif()

# ---------------------------------------------------------------------------
# Source tree roots
# ---------------------------------------------------------------------------
get_filename_component(FBTHRIFT_ROOT "${CMAKE_CURRENT_SOURCE_DIR}/../../.." ABSOLUTE)
get_filename_component(FOLLY_ROOT "${FBTHRIFT_ROOT}/../folly" ABSOLUTE)

# ---------------------------------------------------------------------------
# Find dependencies (via CMAKE_PREFIX_PATH from getdeps, or system)
# ---------------------------------------------------------------------------
find_package(folly CONFIG REQUIRED)
find_package(FBThrift CONFIG REQUIRED)
find_package(wangle CONFIG REQUIRED)
find_package(fizz CONFIG REQUIRED)
find_package(fmt CONFIG REQUIRED)
find_package(Boost REQUIRED COMPONENTS filesystem)
find_package(OpenSSL REQUIRED)
find_package(Threads REQUIRED)

find_package(glog CONFIG QUIET)
if(NOT glog_FOUND)
  find_package(Glog REQUIRED)
endif()
find_package(gflags CONFIG QUIET)
if(NOT gflags_FOUND)
  find_package(Gflags REQUIRED)
endif()

# ---------------------------------------------------------------------------
# Include paths for local source
# ---------------------------------------------------------------------------
include_directories(
  ${FBTHRIFT_ROOT}
  ${CMAKE_CURRENT_BINARY_DIR}
  ${FOLLY_ROOT}
)

# Homebrew headers for xxhash etc., but exclude conflicting glog/gflags
include_directories(
  /opt/homebrew/opt/xxhash/include
)

# ---------------------------------------------------------------------------
# Thrift code generation
# ---------------------------------------------------------------------------
find_program(THRIFT_COMPILER thrift1)
if(NOT THRIFT_COMPILER)
  message(FATAL_ERROR
    "thrift1 compiler not found.  It should have been built by getdeps.  "
    "Make sure you ran: ./build.sh deps")
endif()

set(THRIFT_IDL_DIR "${CMAKE_CURRENT_SOURCE_DIR}/if")
set(THRIFT_GEN_DIR "${CMAKE_CURRENT_BINARY_DIR}/thrift/perf/cpp2/if/gen-cpp2")
file(MAKE_DIRECTORY "${THRIFT_GEN_DIR}")

macro(thrift_gen THRIFT_FILE)
  get_filename_component(_base "${THRIFT_FILE}" NAME_WE)
  add_custom_command(
    OUTPUT "${THRIFT_GEN_DIR}/${_base}_types.cpp"
           "${THRIFT_GEN_DIR}/${_base}_types.h"
    COMMAND ${THRIFT_COMPILER}
      --gen mstch_cpp2
      -I "${FBTHRIFT_ROOT}"
      -o "${CMAKE_CURRENT_BINARY_DIR}/thrift/perf/cpp2/if"
      "${THRIFT_IDL_DIR}/${THRIFT_FILE}"
    DEPENDS "${THRIFT_IDL_DIR}/${THRIFT_FILE}"
    COMMENT "thrift1: ${THRIFT_FILE}"
    VERBATIM
  )
endmacro()

thrift_gen(ApiBase.thrift)
thrift_gen(Api.thrift)
thrift_gen(StreamApi.thrift)

file(GLOB_RECURSE THRIFT_GEN_CPPS "${THRIFT_GEN_DIR}/*.cpp")

# If the glob is empty (first cmake run before generation), list them explicitly
if(NOT THRIFT_GEN_CPPS)
  set(THRIFT_GEN_CPPS
    "${THRIFT_GEN_DIR}/ApiBase_types.cpp"
    "${THRIFT_GEN_DIR}/ApiBase_data.cpp"
    "${THRIFT_GEN_DIR}/ApiBase_metadata.cpp"
    "${THRIFT_GEN_DIR}/Benchmark.cpp"
    "${THRIFT_GEN_DIR}/BenchmarkAsyncClient.cpp"
    "${THRIFT_GEN_DIR}/Benchmark_metadata.cpp"
    "${THRIFT_GEN_DIR}/StreamBenchmark.cpp"
    "${THRIFT_GEN_DIR}/StreamBenchmarkAsyncClient.cpp"
    "${THRIFT_GEN_DIR}/StreamBenchmark_metadata.cpp"
  )
endif()

add_library(thrift_perf_gen STATIC ${THRIFT_GEN_CPPS})
target_include_directories(thrift_perf_gen PUBLIC "${CMAKE_CURRENT_BINARY_DIR}")
target_link_libraries(thrift_perf_gen PUBLIC
  FBThrift::thriftcpp2
  Folly::folly
)

# ---------------------------------------------------------------------------
# Custom SHM sources (user additions to folly, not in upstream)
# ---------------------------------------------------------------------------
set(SHM_SOURCES
  ${FOLLY_ROOT}/folly/io/async/ShmPollerService.cpp
  ${FOLLY_ROOT}/folly/io/async/BusyPollSharedMemoryTransport.cpp
  ${FOLLY_ROOT}/folly/io/async/BusyPollShmHandshake.cpp
  ${FOLLY_ROOT}/folly/io/async/GqmInterface.cpp
  ${FOLLY_ROOT}/folly/io/async/ImportedMemoryProvider.cpp
  ${FOLLY_ROOT}/folly/io/async/PosixShmProvider.cpp
  ${FOLLY_ROOT}/folly/io/async/SharedMemoryTransport.cpp
  ${FOLLY_ROOT}/folly/io/async/SharedMemoryRegion.cpp
  ${FOLLY_ROOT}/folly/io/async/ugqm_stub.c
)

add_library(folly_shm STATIC
  ${SHM_SOURCES}
)
target_include_directories(folly_shm PUBLIC ${FOLLY_ROOT})
target_link_libraries(folly_shm PUBLIC Folly::folly)

# ---------------------------------------------------------------------------
# Benchmark util
# ---------------------------------------------------------------------------
add_library(perf_util STATIC util/Util.cpp)
target_link_libraries(perf_util PUBLIC
  thrift_perf_gen
  folly_shm
  FBThrift::thriftcpp2
  Folly::folly
)

# ---------------------------------------------------------------------------
# Server
# ---------------------------------------------------------------------------
add_executable(Server server/Server.cpp)
target_link_libraries(Server PRIVATE
  perf_util
  folly_shm
  thrift_perf_gen
  FBThrift::thriftcpp2
  FBThrift::thrift-core
  FBThrift::transport
  FBThrift::rpcmetadata
  wangle::wangle
  fizz::fizz
  Folly::folly
  Boost::filesystem
  OpenSSL::SSL
  OpenSSL::Crypto
  fmt::fmt
  Threads::Threads
)

# ---------------------------------------------------------------------------
# Client
# ---------------------------------------------------------------------------
add_executable(Client client/Client.cpp)
target_link_libraries(Client PRIVATE
  perf_util
  folly_shm
  thrift_perf_gen
  FBThrift::thriftcpp2
  FBThrift::thrift-core
  FBThrift::transport
  FBThrift::rpcmetadata
  wangle::wangle
  fizz::fizz
  Folly::folly
  Boost::filesystem
  OpenSSL::SSL
  OpenSSL::Crypto
  fmt::fmt
  Threads::Threads
)

# ---------------------------------------------------------------------------
# Summary
# ---------------------------------------------------------------------------
message(STATUS "=== thrift_perf_benchmark ===")
message(STATUS "  Thrift compiler : ${THRIFT_COMPILER}")
message(STATUS "  Gen output dir  : ${THRIFT_GEN_DIR}")
