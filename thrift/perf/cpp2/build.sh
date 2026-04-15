#!/usr/bin/env bash
#
# Build script for the Thrift SHM perf benchmark.
#
# This script uses Facebook's getdeps.py to handle the entire dependency
# chain (folly, fizz, wangle, mvfst, fbthrift, etc.), then builds the
# perf benchmark against the getdeps-installed artifacts.
#
# Usage:
#   ./build.sh deps      # Step 1: build all deps via getdeps (takes a while)
#   ./build.sh            # Step 2: build the benchmark
#   ./build.sh clean      # remove benchmark build directory
#
# After 'deps' is run once, you only need to re-run the default step.
#

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
BUILD_DIR="${SCRIPT_DIR}/build"
FBTHRIFT_ROOT="$(cd "${SCRIPT_DIR}/../../.." && pwd)"
FOLLY_ROOT="$(cd "${FBTHRIFT_ROOT}/../folly" && pwd)"
GETDEPS="${FBTHRIFT_ROOT}/build/fbcode_builder/getdeps.py"

# ---------------------------------------------------------------------------
# Step 1: build all dependencies via getdeps.py
# ---------------------------------------------------------------------------
if [[ "${1:-}" == "deps" ]]; then
  echo "=== Building fbthrift + all dependencies via getdeps.py ==="
  echo "  This will take a while on first run (downloads + builds ~27 deps)."
  echo "  getdeps.py: ${GETDEPS}"
  echo ""

  cd "${FBTHRIFT_ROOT}"

  # --allow-system-packages uses Homebrew-installed packages when possible,
  # avoiding redundant builds.
  python3 "${GETDEPS}" build fbthrift --allow-system-packages "$@"

  echo ""
  echo "=== Dependencies built.  Install prefix: ==="
  python3 "${GETDEPS}" show-inst-dir fbthrift
  echo ""
  echo "Now run: ./build.sh"
  exit 0
fi

# ---------------------------------------------------------------------------
# Clean
# ---------------------------------------------------------------------------
if [[ "${1:-}" == "clean" ]]; then
  echo "Removing ${BUILD_DIR}"
  rm -rf "${BUILD_DIR}"
  exit 0
fi

# ---------------------------------------------------------------------------
# Step 2: build the benchmark
# ---------------------------------------------------------------------------
echo "=== Thrift SHM Perf Benchmark Build ==="

# Discover getdeps install prefix
SCRATCH_DIR="$(cd "${FBTHRIFT_ROOT}" && python3 "${GETDEPS}" show-scratch-dir 2>/dev/null)" || true
INST_DIR=""

if [[ -n "${SCRATCH_DIR}" && -d "${SCRATCH_DIR}/installed" ]]; then
  INST_DIR="${SCRATCH_DIR}/installed"
  echo "  getdeps installed dir: ${INST_DIR}"
else
  echo "WARNING: getdeps scratch dir not found.  Run './build.sh deps' first."
  echo "         Attempting to build with system-installed packages..."
fi

mkdir -p "${BUILD_DIR}"
cd "${BUILD_DIR}"

CMAKE_ARGS=(
  -DCMAKE_BUILD_TYPE=RelWithDebInfo
)

# If getdeps installed dir exists, set CMAKE_PREFIX_PATH so find_package
# can locate all dependencies (folly, fbthrift, fizz, wangle, etc.)
if [[ -n "${INST_DIR}" ]]; then
  # getdeps installs each dep in its own subdirectory
  PREFIX_PATHS=""
  for d in "${INST_DIR}"/*/; do
    PREFIX_PATHS="${PREFIX_PATHS};${d}"
  done
  CMAKE_ARGS+=("-DCMAKE_PREFIX_PATH=${PREFIX_PATHS}")
fi

echo "  Source dir  : ${SCRIPT_DIR}"
echo "  Build dir   : ${BUILD_DIR}"
echo ""
echo "Running cmake..."
cmake "${CMAKE_ARGS[@]}" "${SCRIPT_DIR}"

echo ""
echo "Building..."
NJOBS="$(sysctl -n hw.ncpu 2>/dev/null || nproc 2>/dev/null || echo 4)"
cmake --build . -j "${NJOBS}"

echo ""
echo "=== Build complete ==="
echo "  Server: ${BUILD_DIR}/Server"
echo "  Client: ${BUILD_DIR}/Client"
