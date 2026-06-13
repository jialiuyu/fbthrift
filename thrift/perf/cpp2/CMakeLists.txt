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

set(PERF_CPP2_IDL_DIR "${CMAKE_CURRENT_SOURCE_DIR}/if")
set(PERF_CPP2_IDL_BUILD_DIR "${CMAKE_CURRENT_BINARY_DIR}/if")
set(PERF_CPP2_LIB_DIR "${CMAKE_SOURCE_DIR}/thrift/lib/cpp2")

option(
  THRIFT_PERF_CPP2_ENABLE_HTTP2
  "Build thrift/perf/cpp2 benchmark HTTP/2 transport support"
  ON
)
if (THRIFT_PERF_CPP2_ENABLE_HTTP2)
  find_package(proxygen CONFIG QUIET)
  if (NOT TARGET proxygen::proxygen)
    message(
      STATUS
      "proxygen::proxygen was not found; building thrift/perf/cpp2 without HTTP/2 support"
    )
    set(THRIFT_PERF_CPP2_ENABLE_HTTP2 OFF)
  endif()
endif()

thrift_generate(
  "ApiBase"
  ""
  "cpp2"
  ""
  "${PERF_CPP2_IDL_DIR}"
  "${PERF_CPP2_IDL_BUILD_DIR}"
  "thrift/perf/cpp2/if"
  THRIFT_INCLUDE_DIRECTORIES ${THRIFT_SOURCE_DIR}
)

thrift_generate(
  "Api"
  "Benchmark"
  "cpp2"
  ""
  "${PERF_CPP2_IDL_DIR}"
  "${PERF_CPP2_IDL_BUILD_DIR}"
  "thrift/perf/cpp2/if"
  THRIFT_INCLUDE_DIRECTORIES ${THRIFT_SOURCE_DIR}
)

thrift_generate(
  "StreamApi"
  "StreamBenchmark"
  "cpp2"
  ""
  "${PERF_CPP2_IDL_DIR}"
  "${PERF_CPP2_IDL_BUILD_DIR}"
  "thrift/perf/cpp2/if"
  THRIFT_INCLUDE_DIRECTORIES ${THRIFT_SOURCE_DIR}
)

set(PERF_CPP2_GENERATED_SOURCES
  ${ApiBase-cpp2-SOURCES}
  ${Api-cpp2-SOURCES}
  ${StreamApi-cpp2-SOURCES}
)
bypass_source_check("${PERF_CPP2_GENERATED_SOURCES}")

add_library(
  thrift_perf_cpp2_common
  STATIC
  ${PERF_CPP2_GENERATED_SOURCES}
  util/CxlMemBenchmarkTransport.cpp
  util/Util.cpp
)
add_dependencies(
  thrift_perf_cpp2_common
  ApiBase-cpp2-target
  Api-cpp2-target
  StreamApi-cpp2-target
  thriftcpp2
)
target_include_directories(
  thrift_perf_cpp2_common
  PUBLIC
    "${CMAKE_CURRENT_BINARY_DIR}/if"
)
target_compile_definitions(
  thrift_perf_cpp2_common
  PUBLIC
    THRIFT_PERF_CPP2_ENABLE_HTTP2=$<BOOL:${THRIFT_PERF_CPP2_ENABLE_HTTP2}>
)
target_link_libraries(
  thrift_perf_cpp2_common
  PUBLIC
    thriftcpp2
    Folly::folly
    ${LIBGFLAGS_LIBRARY}
    ${GLOG_LIBRARIES}
)

set(THRIFT_PERF_CPP2_LINK_TARGET thrift_perf_cpp2_common)
if (THRIFT_PERF_CPP2_ENABLE_HTTP2)
  add_library(
    thrift_perf_cpp2_http2
    STATIC
    ${PERF_CPP2_LIB_DIR}/transport/http2/client/H2ClientConnection.cpp
    ${PERF_CPP2_LIB_DIR}/transport/http2/client/ThriftTransactionHandler.cpp
    ${PERF_CPP2_LIB_DIR}/transport/http2/common/H2Channel.cpp
    ${PERF_CPP2_LIB_DIR}/transport/http2/common/HTTP2RoutingHandler.cpp
    ${PERF_CPP2_LIB_DIR}/transport/http2/common/SingleRpcChannel.cpp
    ${PERF_CPP2_LIB_DIR}/transport/http2/server/ThriftRequestHandler.cpp
  )
  target_link_libraries(
    thrift_perf_cpp2_http2
    PUBLIC
      thrift_perf_cpp2_common
      proxygen::proxygen
      wangle::wangle
  )
  set(THRIFT_PERF_CPP2_LINK_TARGET thrift_perf_cpp2_http2)
endif()

add_executable(
  thrift_perf_cpp2_client
  EXCLUDE_FROM_ALL
  client/Client.cpp
)
target_compile_definitions(thrift_perf_cpp2_client PRIVATE STREAM_PERF_TEST)
target_link_libraries(
  thrift_perf_cpp2_client
  PRIVATE
    ${THRIFT_PERF_CPP2_LINK_TARGET}
)

add_executable(
  thrift_perf_cpp2_server
  EXCLUDE_FROM_ALL
  server/Server.cpp
)
target_link_libraries(
  thrift_perf_cpp2_server
  PRIVATE
    ${THRIFT_PERF_CPP2_LINK_TARGET}
)
