#!/usr/bin/env bash
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

# =============================================================================
# Socket vs SHM Transport Benchmark Runner
# =============================================================================
# Automates running the full experiment matrix comparing socket-based
# transports (rocket/header) against shared memory (shm) transport.
#
# Configurable via environment variables:
#   TRANSPORTS       - Space-separated list: "rocket" "shm" "header" (default: "rocket shm")
#   OPS              - Space-separated operations (default: "noop sum download upload")
#   CLIENTS          - Space-separated client counts (default: "1 2 4 8")
#   PIPELINES        - Space-separated pipeline depths (default: "1 10 100")
#   CHUNK_SIZES      - Space-separated chunk sizes for download/upload (default: "64 256 1024 4096 16384 65536")
#   DURATION         - Seconds per experiment (default: 30)
#   USE_POSIX_SHM    - "true" to use POSIX shm_open fallback (default: "true")
#   SERVER_BIN       - Path to Server binary (default: auto-detect from build/)
#   CLIENT_BIN       - Path to Client binary (default: auto-detect from build/)
#   RESULTS_DIR      - Output directory for logs (default: ./benchmark_results_<timestamp>)
#   WARMUP_SEC       - Warmup seconds before measurement (default: 3)
#   IO_THREADS       - Server IO threads (default: 4)
#   CPU_THREADS      - Server CPU threads (default: 4)
#   PORT             - Server port (default: 7777)
# =============================================================================

set -euo pipefail

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------
TRANSPORTS="${TRANSPORTS:-rocket shm}"
OPS="${OPS:-noop sum download upload}"
CLIENTS="${CLIENTS:-1 2 4 8}"
PIPELINES="${PIPELINES:-1 10 100}"
CHUNK_SIZES="${CHUNK_SIZES:-64 256 1024 4096 16384 65536}"
DURATION="${DURATION:-30}"
USE_POSIX_SHM="${USE_POSIX_SHM:-true}"
WARMUP_SEC="${WARMUP_SEC:-3}"
IO_THREADS="${IO_THREADS:-4}"
CPU_THREADS="${CPU_THREADS:-4}"
PORT="${PORT:-7777}"

TIMESTAMP="$(date +%Y%m%d_%H%M%S)"
RESULTS_DIR="${RESULTS_DIR:-./benchmark_results_${TIMESTAMP}}"

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

# ---------------------------------------------------------------------------
# Binary discovery
# ---------------------------------------------------------------------------
if [[ -n "${SERVER_BIN:-}" ]]; then
    SERVER="${SERVER_BIN}"
else
    # Try common build directories
    for candidate in \
        "${SCRIPT_DIR}/../../../build/opt/thrift/perf/cpp2/server/Server" \
        "${SCRIPT_DIR}/../../../build/thrift/perf/cpp2/server/Server" \
        "${SCRIPT_DIR}/../../../../build/opt/thrift/perf/cpp2/server/Server"; do
        if [[ -x "$candidate" ]]; then
            SERVER="$candidate"
            break
        fi
    done
    if [[ -z "${SERVER:-}" ]]; then
        echo "ERROR: Cannot find Server binary. Set SERVER_BIN explicitly." >&2
        exit 1
    fi
fi

if [[ -n "${CLIENT_BIN:-}" ]]; then
    CLIENT="${CLIENT_BIN}"
else
    for candidate in \
        "${SCRIPT_DIR}/../../../build/opt/thrift/perf/cpp2/client/Client" \
        "${SCRIPT_DIR}/../../../build/thrift/perf/cpp2/client/Client" \
        "${SCRIPT_DIR}/../../../../build/opt/thrift/perf/cpp2/client/Client"; do
        if [[ -x "$candidate" ]]; then
            CLIENT="$candidate"
            break
        fi
    done
    if [[ -z "${CLIENT:-}" ]]; then
        echo "ERROR: Cannot find Client binary. Set CLIENT_BIN explicitly." >&2
        exit 1
    fi
fi

echo "============================================"
echo "  Socket vs SHM Transport Benchmark"
echo "============================================"
echo "Server:  ${SERVER}"
echo "Client:  ${CLIENT}"
echo "Results: ${RESULTS_DIR}"
echo "Transports: ${TRANSPORTS}"
echo "Operations: ${OPS}"
echo "Duration:   ${DURATION}s per experiment"
echo "Use POSIX SHM: ${USE_POSIX_SHM}"
echo "============================================"

mkdir -p "${RESULTS_DIR}"

# ---------------------------------------------------------------------------
# Helper functions
# ---------------------------------------------------------------------------

SERVER_PID=""

cleanup() {
    if [[ -n "${SERVER_PID}" ]] && kill -0 "${SERVER_PID}" 2>/dev/null; then
        echo "  Cleaning up server (PID ${SERVER_PID})..."
        kill "${SERVER_PID}" 2>/dev/null || true
        wait "${SERVER_PID}" 2>/dev/null || true
        SERVER_PID=""
    fi
}

trap cleanup EXIT

start_server() {
    local transport="$1"
    local extra_args=()

    if [[ "${transport}" == "shm" ]]; then
        extra_args+=(--shm)
        if [[ "${USE_POSIX_SHM}" == "true" ]]; then
            extra_args+=(--posix_shm)
        fi
    fi

    echo "  Starting server (transport=${transport})..."
    "${SERVER}" \
        --port="${PORT}" \
        --io_threads="${IO_THREADS}" \
        --cpu_threads="${CPU_THREADS}" \
        --warmup_sec="${WARMUP_SEC}" \
        --terminate_sec="$((DURATION + WARMUP_SEC + 5))" \
        "${extra_args[@]}" \
        > "${RESULTS_DIR}/server.log" 2>&1 &
    SERVER_PID=$!
    # Give server time to bind and start
    sleep 2
}

stop_server() {
    if [[ -n "${SERVER_PID}" ]] && kill -0 "${SERVER_PID}" 2>/dev/null; then
        # Server will self-terminate via --terminate_sec; just wait
        echo "  Waiting for server to exit..."
        wait "${SERVER_PID}" 2>/dev/null || true
        SERVER_PID=""
    fi
}

run_client() {
    local tag="$1"
    local transport="$2"
    local extra_args=()

    # Build operation weights
    local op_args=()
    local op_string="$3"
    IFS=',' read -ra OP_PAIRS <<< "${op_string}"
    for pair in "${OP_PAIRS[@]}"; do
        local op_name="${pair%%:*}"
        local op_weight="${pair##*:}"
        op_args+=("--${op_name}_weight=${op_weight}")
    done

    # Extra client args from $4
    if [[ -n "${4:-}" ]]; then
        # shellcheck disable=SC2206
        local extra=(${4})
        extra_args+=("${extra[@]}")
    fi

    if [[ "${transport}" == "shm" ]]; then
        if [[ "${USE_POSIX_SHM}" == "true" ]]; then
            extra_args+=(--posix_shm)
        fi
    fi

    local client_log="${RESULTS_DIR}/${tag}_client.log"

    echo "  Running client [${tag}]..."
    "${CLIENT}" \
        --host="::1" \
        --port="${PORT}" \
        --transport="${transport}" \
        --num_clients="${CLIENT_COUNT:-1}" \
        --warmup_sec="${WARMUP_SEC}" \
        --terminate_sec="${DURATION}" \
        --output_format=csv \
        "${op_args[@]}" \
        "${extra_args[@]}" \
        > "${client_log}" 2>&1
}

# ---------------------------------------------------------------------------
# Experiment 1: Pure transport latency (noop)
# ---------------------------------------------------------------------------
run_experiment_latency() {
    echo ""
    echo "=== Experiment 1: Pure Transport Latency (noop) ==="

    for transport in ${TRANSPORTS}; do
        for nclients in ${CLIENTS}; do
            for pipeline in ${PIPELINES}; do
                local tag="exp1_${transport}_noop_c${nclients}_p${pipeline}"
                export CLIENT_COUNT="${nclients}"
                start_server "${transport}"
                run_client "${tag}" "${transport}" "noop:1" "--max_outstanding_ops=${pipeline}"
                stop_server
            done
        done
    done
}

# ---------------------------------------------------------------------------
# Experiment 2: Payload size scan (download/upload)
# ---------------------------------------------------------------------------
run_experiment_payload() {
    echo ""
    echo "=== Experiment 2: Payload Size Scan ==="

    for transport in ${TRANSPORTS}; do
        for chunk_size in ${CHUNK_SIZES}; do
            # Download
            local tag="exp2_${transport}_download_chunk${chunk_size}"
            export CLIENT_COUNT=4
            start_server "${transport}"
            run_client "${tag}" "${transport}" "download:1" "--chunk_size=${chunk_size}"
            stop_server

            # Upload
            tag="exp2_${transport}_upload_chunk${chunk_size}"
            start_server "${transport}"
            run_client "${tag}" "${transport}" "upload:1" "--chunk_size=${chunk_size}"
            stop_server
        done
    done
}

# ---------------------------------------------------------------------------
# Experiment 3: Concurrency scaling (sum)
# ---------------------------------------------------------------------------
run_experiment_concurrency() {
    echo ""
    echo "=== Experiment 3: Concurrency Scaling (sum) ==="

    local concurrency_levels="1 2 4 8 16"

    for transport in ${TRANSPORTS}; do
        for nclients in ${concurrency_levels}; do
            local tag="exp3_${transport}_sum_c${nclients}"
            export CLIENT_COUNT="${nclients}"
            start_server "${transport}"
            run_client "${tag}" "${transport}" "sum:1"
            stop_server
        done
    done
}

# ---------------------------------------------------------------------------
# Experiment 4: Mixed workload
# ---------------------------------------------------------------------------
run_experiment_mixed() {
    echo ""
    echo "=== Experiment 4: Mixed Workload ==="

    for transport in ${TRANSPORTS}; do
        local tag="exp4_${transport}_mixed"
        export CLIENT_COUNT=8
        start_server "${transport}"
        run_client "${tag}" "${transport}" "noop:5,sum:3,download:1,upload:1"
        stop_server
    done
}

# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------
echo ""
echo "Starting benchmark suite..."
echo "Results will be written to: ${RESULTS_DIR}"
echo ""

# Record experiment metadata
cat > "${RESULTS_DIR}/experiment_config.txt" <<EOF
timestamp=${TIMESTAMP}
transports=${TRANSPORTS}
duration=${DURATION}
warmup_sec=${WARMUP_SEC}
use_posix_shm=${USE_POSIX_SHM}
io_threads=${IO_THREADS}
cpu_threads=${CPU_THREADS}
server_bin=${SERVER}
client_bin=${CLIENT}
EOF

run_experiment_latency
run_experiment_payload
run_experiment_concurrency
run_experiment_mixed

echo ""
echo "============================================"
echo "  Benchmark suite complete!"
echo "  Results: ${RESULTS_DIR}"
echo ""
echo "  To parse results, run:"
echo "    python3 ${SCRIPT_DIR}/parse_results.py ${RESULTS_DIR}"
echo "============================================"
