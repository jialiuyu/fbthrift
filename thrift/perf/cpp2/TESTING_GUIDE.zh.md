# thrift/perf/cpp2 benchmark 测试指导书

本文说明如何在 Docker 容器内验证 `thrift/perf/cpp2/` 下两个 benchmark 的构建产物和基本运行状态。

本文假定已经按构建指导完成以下两个二进制：

```bash
/workspace/.cache/getdeps/codex-dev/build/fbthrift/bin/thrift_perf_cpp2_client
/workspace/.cache/getdeps/codex-dev/build/fbthrift/bin/thrift_perf_cpp2_server
```

## 测试环境

容器名称：

```bash
fbthrift-perf-cpp2-build
```

构建目录：

```bash
/workspace/.cache/getdeps/codex-dev/build/fbthrift
```

源码目录：

```bash
/workspace/worktrees/codex-dev/fbthrift
```

本 Docker 环境没有匹配的 `proxygen::proxygen` CMake package，因此当前构建不包含 HTTP/2 transport。测试范围是：

- `--transport=header`
- `--transport=rocket`
- `--transport=cxl_mem --cxl_mem_backend=stub`

`--transport=http2` 不是本文测试范围。

## 构建确认

先确认 client 和 server target 能在当前构建目录中重新构建：

```bash
docker exec fbthrift-perf-cpp2-build bash -lc '
  cd /workspace/.cache/getdeps/codex-dev/build/fbthrift &&
  /usr/bin/cmake --build . \
    --target thrift_perf_cpp2_client thrift_perf_cpp2_server \
    --config RelWithDebInfo \
    -j 1
'
```

成功标准：

- 命令退出码为 `0`。
- 最后能看到 `bin/thrift_perf_cpp2_client` 和 `bin/thrift_perf_cpp2_server` 链接完成。

构建日志中可能出现上游 thrift/folly 的 `deprecated` 告警，以及链接器的 `LOAD segment with RWX permissions` 告警；这些告警不阻断本次 benchmark 验证。

## 二进制和 flags 检查

确认产物存在：

```bash
docker exec fbthrift-perf-cpp2-build bash -lc '
  find /workspace/.cache/getdeps/codex-dev/build/fbthrift \
    -type f \( -name thrift_perf_cpp2_client -o -name thrift_perf_cpp2_server \) \
    -perm -111 -print
'
```

确认 client 暴露 transport 和 CXL.mem flags：

```bash
docker exec fbthrift-perf-cpp2-build bash -lc '
  CLIENT=/workspace/.cache/getdeps/codex-dev/build/fbthrift/bin/thrift_perf_cpp2_client
  "$CLIENT" --helpfull 2>&1 | grep -E "transport|cxl_mem" | head -20
'
```

确认 server 暴露 CXL.mem flags：

```bash
docker exec fbthrift-perf-cpp2-build bash -lc '
  SERVER=/workspace/.cache/getdeps/codex-dev/build/fbthrift/bin/thrift_perf_cpp2_server
  "$SERVER" --helpfull 2>&1 | grep -E "cxl_mem|port|unix_socket_path" | head -20
'
```

确认本地 `folly` 安装包含 CXL.mem transport 符号：

```bash
docker exec fbthrift-perf-cpp2-build bash -lc '
  test -f /workspace/.cache/getdeps/codex-dev/installed/folly/include/folly/io/async/CxlMemAsyncTransport.h &&
  nm -C /workspace/.cache/getdeps/codex-dev/installed/folly/lib/libfolly.a |
    grep -m1 "folly::CxlMemAsyncTransport"
'
```

## 单操作冒烟测试

下面的脚本会依次测试 `header`、`rocket` 和 `cxl_mem stub` 的 `sum` 操作。每个 case 启动一个短生命周期 server，再运行一个 3 秒 client。

```bash
docker exec fbthrift-perf-cpp2-build bash -lc '
set -euo pipefail

CLIENT=/workspace/.cache/getdeps/codex-dev/build/fbthrift/bin/thrift_perf_cpp2_client
SERVER=/workspace/.cache/getdeps/codex-dev/build/fbthrift/bin/thrift_perf_cpp2_server
LOGDIR=/tmp/fbthrift_perf_cpp2_verify_$(date +%s)
mkdir -p "$LOGDIR"

run_case() {
  local name="$1"
  local port="$2"
  local server_extra="$3"
  local client_extra="$4"
  local server_log="$LOGDIR/${name}_server.log"
  local client_log="$LOGDIR/${name}_client.log"

  "$SERVER" \
    --port="${port}" \
    --io_threads=1 \
    --cpu_threads=1 \
    --stats_interval_sec=1 \
    --terminate_sec=20 \
    --logtostderr=1 \
    ${server_extra} \
    >"${server_log}" 2>&1 &
  local server_pid=$!

  local ready=0
  for i in $(seq 1 100); do
    if grep -q "Listening on port" "${server_log}"; then
      ready=1
      break
    fi
    if ! kill -0 "${server_pid}" 2>/dev/null; then
      cat "${server_log}"
      return 1
    fi
    sleep 0.1
  done

  if [ "${ready}" -ne 1 ]; then
    cat "${server_log}"
    kill "${server_pid}" 2>/dev/null || true
    wait "${server_pid}" 2>/dev/null || true
    return 1
  fi

  set +e
  "$CLIENT" \
    --host=127.0.0.1 \
    --port="${port}" \
    --num_clients=1 \
    --terminate_sec=3 \
    --stats_interval_sec=1 \
    --max_outstanding_ops=16 \
    --sum_weight=1 \
    --logtostderr=1 \
    ${client_extra} \
    >"${client_log}" 2>&1
  local client_status=$?
  set -e

  kill "${server_pid}" 2>/dev/null || true
  wait "${server_pid}" 2>/dev/null || true

  echo "=== ${name}: client exit ${client_status} ==="
  grep -E "Client terminating|Operation: sum|TOTAL QPS|FATAL|Check failed|Critical error|Error is:" \
    "${client_log}" | tail -40 || true

  if [ "${client_status}" -ne 0 ]; then
    echo "${name}: failed; logs: ${LOGDIR}"
    return "${client_status}"
  fi
}

run_case header 19090 "" "--transport=header"
run_case rocket 19091 "" "--transport=rocket"
run_case cxl_mem_stub 19092 \
  "--cxl_mem_enable=true --cxl_mem_backend=stub --cxl_mem_path_prefix=/tmp/fbthrift_cxl_mem_verify" \
  "--transport=cxl_mem --cxl_mem_backend=stub --cxl_mem_path_prefix=/tmp/fbthrift_cxl_mem_verify"

echo "logs: ${LOGDIR}"
'
```

成功标准：

- 三个 case 的 `client exit` 都是 `0`。
- client 日志中出现 `Operation: sum` 和 `TOTAL QPS`。
- client 日志中没有 `FATAL`、`Check failed`、`Critical error` 或 `Error is:`。

实测参考值：

- `header + sum`：约 `1.8e5 - 2.0e5 QPS`。
- `rocket + sum`：约 `1.2e5 - 1.4e5 QPS`。
- `cxl_mem stub + sum`：约 `1.0e4 QPS`。

这些数字只用于确认运行路径已打通，不是稳定性能基线。

## 非 stream 混合 workload

下面的脚本测试普通 unary RPC、oneway、download、upload、`semifuture_sum` 和 `co_sum`。这里刻意不包含 `stream_weight`，原因见“已知限制”。

```bash
docker exec fbthrift-perf-cpp2-build bash -lc '
set -euo pipefail

CLIENT=/workspace/.cache/getdeps/codex-dev/build/fbthrift/bin/thrift_perf_cpp2_client
SERVER=/workspace/.cache/getdeps/codex-dev/build/fbthrift/bin/thrift_perf_cpp2_server
LOGDIR=/tmp/fbthrift_perf_cpp2_nonstream_$(date +%s)
mkdir -p "$LOGDIR"

run_case() {
  local name="$1"
  local port="$2"
  local server_extra="$3"
  local client_extra="$4"
  local server_log="$LOGDIR/${name}_server.log"
  local client_log="$LOGDIR/${name}_client.log"

  "$SERVER" \
    --port="${port}" \
    --io_threads=1 \
    --cpu_threads=1 \
    --stats_interval_sec=1 \
    --terminate_sec=20 \
    --chunk_size=256 \
    --batch_size=8 \
    --logtostderr=1 \
    ${server_extra} \
    >"${server_log}" 2>&1 &
  local server_pid=$!

  for i in $(seq 1 100); do
    grep -q "Listening on port" "${server_log}" && break
    kill -0 "${server_pid}" 2>/dev/null || {
      cat "${server_log}"
      return 1
    }
    sleep 0.1
  done

  set +e
  timeout 10s "$CLIENT" \
    --host=127.0.0.1 \
    --port="${port}" \
    --num_clients=1 \
    --terminate_sec=3 \
    --stats_interval_sec=1 \
    --max_outstanding_ops=8 \
    --chunk_size=256 \
    --batch_size=8 \
    --noop_weight=1 \
    --noop_oneway_weight=1 \
    --sum_weight=1 \
    --download_weight=1 \
    --upload_weight=1 \
    --semifuture_sum_weight=1 \
    --co_sum_weight=1 \
    --logtostderr=1 \
    ${client_extra} \
    >"${client_log}" 2>&1
  local client_status=$?
  set -e

  kill "${server_pid}" 2>/dev/null || true
  wait "${server_pid}" 2>/dev/null || true

  echo "=== ${name}: client exit ${client_status} ==="
  grep -E "Operation:|Client terminating|FATAL|Check failed|Critical error|Error is:" \
    "${client_log}" | tail -80 || true

  if [ "${client_status}" -ne 0 ]; then
    echo "${name}: failed; logs: ${LOGDIR}"
    return "${client_status}"
  fi
  if grep -E "Operation: (error|fatal)" "${client_log}" >/dev/null; then
    echo "${name}: error/fatal counter appeared; logs: ${LOGDIR}"
    return 2
  fi
}

run_case rocket_nonstream 19101 "" "--transport=rocket"
run_case cxl_mem_nonstream 19102 \
  "--cxl_mem_enable=true --cxl_mem_backend=stub --cxl_mem_path_prefix=/tmp/fbthrift_cxl_mem_nonstream" \
  "--transport=cxl_mem --cxl_mem_backend=stub --cxl_mem_path_prefix=/tmp/fbthrift_cxl_mem_nonstream"

echo "logs: ${LOGDIR}"
'
```

成功标准：

- 两个 case 的 `client exit` 都是 `0`。
- 日志中能看到 `co_sum`、`download`、`noop`、`semifuture_sum`、`sum`、`upload` 等 operation counter。
- 不出现 `Operation: error` 或 `Operation: fatal`。

## 已知限制：stream_weight

当前 `--stream_weight=1` 不适合作为短时自动化通过项。

实测现象：

- `rocket_mixed` 包含 `--stream_weight=1` 时，client 可能 abort：

```text
Check failed: expected == prevLoopTid ... Driving an EventBase ... while it is already being driven is forbidden.
```

- `rocket_stream_only` 和 `cxl_stream_only` 在外层 `timeout 8s` 下退出 `124`。

代码层面的原因是：

- server 端 `BenchmarkHandler::streamDownload()` 返回无限 `ServerStream`。
- client 端 `StreamDownload::async()` 调用 `sync_streamDownload()`，该同步调用会等待流。
- 当它在已有 EventBase 回调链路中被再次调度时，可能触发同一个 EventBase 被嵌套 drive。

因此，本文的自动化测试只把 non-stream workload 作为通过标准。测试 stream 路径时，应单独运行、加外层 `timeout`，并把退出 `124` 或 EventBase fatal 视为当前实现的已知问题，而不是 CXL.mem transport 本身的通过标准。

## 清理

如果测试被手动中断，先确认没有残留 benchmark 进程：

```bash
docker exec fbthrift-perf-cpp2-build bash -lc '
  pgrep -af "bin/thrift_perf_cpp2_(client|server)" || true
'
```

需要清理时使用精确进程名：

```bash
docker exec fbthrift-perf-cpp2-build bash -lc '
  pkill -x thrift_perf_cpp2_client 2>/dev/null || true
  pkill -x thrift_perf_cpp2_server 2>/dev/null || true
'
```

日志默认写入容器 `/tmp/fbthrift_perf_cpp2_*`，可以按需删除。
