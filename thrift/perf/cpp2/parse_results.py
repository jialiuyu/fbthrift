#!/usr/bin/env python3
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

"""
Socket vs SHM Benchmark Result Parser.

Parses client log files from run_benchmark.sh, extracts CSV stats lines,
computes steady-state medians, and produces a summary CSV + comparison table.

Usage:
    python3 parse_results.py <results_dir> [--output summary.csv]
"""

import argparse
import csv
import os
import re
import statistics
import sys
from collections import defaultdict
from pathlib import Path
from typing import Dict, List, Optional, Tuple


# ---------------------------------------------------------------------------
# Log parsing
# ---------------------------------------------------------------------------

# STATS_CSV format: STATS_CSV,<transport>,<op>,<qps>,<max_qps>,<avg_qps>,
#                   <rt_avg_us>,<rt_p99_us>,<overall_avg_us>,<overall_p99_us>,
#                   <total_queries>
STATS_CSV_RE = re.compile(
    r"^STATS_CSV,([^,]+),([^,]+),"
    r"([^,]+),([^,]+),([^,]+),"
    r"([^,]*),([^,]*),([^,]*),([^,]*),"
    r"([^,]*)$"
)

# SHM_DIAG_CSV format: SHM_DIAG_CSV,<transport>,<dispatch_avg_us>,<write_avg_us>,
#                       <pop_empty_ratio>,<pop_yields>,<write_fc_yields>,
#                       <write_timeouts>,<shared_locks>,<iobuf_allocs>,
#                       <p50>,<p90>,<p99>
SHM_DIAG_CSV_RE = re.compile(
    r"^SHM_DIAG_CSV,([^,]+),"
    r"([^,]+),([^,]+),([^,]+),([^,]+),"
    r"([^,]+),([^,]+),([^,]+),([^,]+),"
    r"([^,]+),([^,]+),([^,]+)$"
)


def safe_float(s: str) -> float:
    try:
        return float(s)
    except (ValueError, TypeError):
        return 0.0


def parse_log_file(filepath: str) -> Tuple[List[dict], List[dict]]:
    """Parse a single client log file.

    Returns:
        (stats_rows, diag_rows) — lists of parsed dicts.
    """
    stats_rows = []
    diag_rows = []

    with open(filepath, "r") as f:
        for line in f:
            line = line.strip()
            # Strip glog prefix: I<timestamp> <file:line>]
            # e.g., "I20260419 12:34:56.789 12345 Client.cpp:323] STATS_CSV,..."
            log_match = re.search(r"\]\s+(STATS_CSV,.+)$", line)
            if not log_match:
                log_match = re.search(r"\]\s+(SHM_DIAG_CSV,.+)$", line)
            if not log_match:
                continue

            payload = log_match.group(1)

            m = STATS_CSV_RE.match(payload)
            if m:
                row = {
                    "transport": m.group(1),
                    "op": m.group(2),
                    "qps": safe_float(m.group(3)),
                    "max_qps": safe_float(m.group(4)),
                    "avg_qps": safe_float(m.group(5)),
                    "rt_avg_us": safe_float(m.group(6)),
                    "rt_p99_us": safe_float(m.group(7)),
                    "overall_avg_us": safe_float(m.group(8)),
                    "overall_p99_us": safe_float(m.group(9)),
                    "total_queries": safe_float(m.group(10)),
                }
                stats_rows.append(row)
                continue

            m = SHM_DIAG_CSV_RE.match(payload)
            if m:
                row = {
                    "transport": m.group(1),
                    "dispatch_avg_us": safe_float(m.group(2)),
                    "write_avg_us": safe_float(m.group(3)),
                    "pop_empty_ratio": safe_float(m.group(4)),
                    "pop_yields": safe_float(m.group(5)),
                    "write_fc_yields": safe_float(m.group(6)),
                    "write_timeouts": safe_float(m.group(7)),
                    "shared_locks": safe_float(m.group(8)),
                    "iobuf_allocs": safe_float(m.group(9)),
                    "p50_us": safe_float(m.group(10)),
                    "p90_us": safe_float(m.group(11)),
                    "p99_us": safe_float(m.group(12)),
                }
                diag_rows.append(row)

    return stats_rows, diag_rows


def extract_tag_info(filename: str) -> dict:
    """Extract experiment metadata from filename.

    Expected format: expN_transport_op_cN_pN_client.log
    """
    info = {"experiment": "", "transport": "", "op": "", "clients": "", "pipeline": ""}

    basename = Path(filename).stem.replace("_client", "")

    parts = basename.split("_")
    if len(parts) >= 3:
        info["experiment"] = parts[0]  # e.g., exp1
        info["transport"] = parts[1]   # e.g., rocket, shm
        info["op"] = parts[2]          # e.g., noop, sum

        for part in parts[3:]:
            if part.startswith("c") and part[1:].isdigit():
                info["clients"] = part[1:]
            elif part.startswith("p") and part[1:].isdigit():
                info["pipeline"] = part[1:]
            elif part.startswith("chunk") and part[5:].isdigit():
                info["chunk_size"] = part[5:]

    return info


# ---------------------------------------------------------------------------
# Steady-state analysis
# ---------------------------------------------------------------------------

def compute_steady_state(rows: List[dict], warmup_intervals: int = 2) -> Optional[dict]:
    """Take the median of QPS/P99 from the steady-state region.

    The first `warmup_intervals` data points are discarded as transient.
    For the TOTAL rows, we aggregate; for per-op rows, we return individual stats.
    """
    total_rows = [r for r in rows if r["op"] == "TOTAL"]
    if len(total_rows) <= warmup_intervals:
        # Not enough data — return last row if available
        return total_rows[-1] if total_rows else None

    steady = total_rows[warmup_intervals:]
    return {
        "qps_median": statistics.median(r["qps"] for r in steady),
        "max_qps": max(r["max_qps"] for r in steady),
        "avg_qps_median": statistics.median(r["avg_qps"] for r in steady),
        "rt_avg_us_median": statistics.median(r["rt_avg_us"] for r in steady) if any(r["rt_avg_us"] > 0 for r in steady) else 0,
        "rt_p99_us_median": statistics.median(r["rt_p99_us"] for r in steady) if any(r["rt_p99_us"] > 0 for r in steady) else 0,
        "overall_avg_us_median": statistics.median(r["overall_avg_us"] for r in steady) if any(r["overall_avg_us"] > 0 for r in steady) else 0,
        "overall_p99_us_median": statistics.median(r["overall_p99_us"] for r in steady) if any(r["overall_p99_us"] > 0 for r in steady) else 0,
        "total_queries": total_rows[-1]["total_queries"],
    }


def compute_shm_diag_steady(diag_rows: List[dict], warmup_intervals: int = 2) -> Optional[dict]:
    """Compute median SHM diag stats from steady-state rows."""
    if not diag_rows:
        return None
    steady = diag_rows[warmup_intervals:] if len(diag_rows) > warmup_intervals else diag_rows
    return {
        "dispatch_avg_us": statistics.median(r["dispatch_avg_us"] for r in steady),
        "write_avg_us": statistics.median(r["write_avg_us"] for r in steady),
        "pop_empty_ratio": statistics.median(r["pop_empty_ratio"] for r in steady),
        "p50_us": statistics.median(r["p50_us"] for r in steady),
        "p90_us": statistics.median(r["p90_us"] for r in steady),
        "p99_us": statistics.median(r["p99_us"] for r in steady),
    }


# ---------------------------------------------------------------------------
# Main processing
# ---------------------------------------------------------------------------

def process_results(results_dir: str, output_csv: str) -> None:
    """Process all client logs in results_dir and write summary CSV."""
    results_dir_path = Path(results_dir)

    if not results_dir_path.is_dir():
        print(f"ERROR: {results_dir} is not a directory", file=sys.stderr)
        sys.exit(1)

    # Collect all client log files
    log_files = sorted(results_dir_path.glob("*_client.log"))
    if not log_files:
        print(f"No *_client.log files found in {results_dir}", file=sys.stderr)
        sys.exit(1)

    print(f"Found {len(log_files)} client log files in {results_dir}")

    # Parse all logs
    all_summaries = []

    for log_file in log_files:
        tag_info = extract_tag_info(log_file.name)
        stats_rows, diag_rows = parse_log_file(str(log_file))

        if not stats_rows:
            print(f"  WARNING: No STATS_CSV data in {log_file.name}")
            continue

        steady = compute_steady_state(stats_rows)
        if not steady:
            print(f"  WARNING: No TOTAL rows in {log_file.name}")
            continue

        summary = {
            "transport": tag_info["transport"],
            "op": tag_info["op"],
            "clients": tag_info.get("clients", ""),
            "pipeline": tag_info.get("pipeline", ""),
            "chunk_size": tag_info.get("chunk_size", ""),
            "experiment": tag_info["experiment"],
            "max_qps": steady["max_qps"],
            "avg_qps": steady["avg_qps_median"],
            "qps_median": steady["qps_median"],
            "avg_latency_us": steady["rt_avg_us_median"],
            "p99_latency_us": steady["rt_p99_us_median"],
            "overall_avg_us": steady["overall_avg_us_median"],
            "overall_p99_us": steady["overall_p99_us_median"],
            "total_queries": steady["total_queries"],
        }

        # SHM diagnostics
        diag = compute_shm_diag_steady(diag_rows)
        if diag:
            summary.update({
                "shm_dispatch_avg_us": diag["dispatch_avg_us"],
                "shm_write_avg_us": diag["write_avg_us"],
                "shm_pop_empty_ratio": diag["pop_empty_ratio"],
                "shm_p50_us": diag["p50_us"],
                "shm_p90_us": diag["p90_us"],
                "shm_p99_us": diag["p99_us"],
            })
        else:
            summary.update({
                "shm_dispatch_avg_us": "",
                "shm_write_avg_us": "",
                "shm_pop_empty_ratio": "",
                "shm_p50_us": "",
                "shm_p90_us": "",
                "shm_p99_us": "",
            })

        all_summaries.append(summary)

    if not all_summaries:
        print("No valid results found.", file=sys.stderr)
        sys.exit(1)

    # Write CSV
    fieldnames = [
        "experiment", "transport", "op", "clients", "pipeline", "chunk_size",
        "max_qps", "avg_qps", "qps_median",
        "avg_latency_us", "p99_latency_us",
        "overall_avg_us", "overall_p99_us",
        "total_queries",
        "shm_dispatch_avg_us", "shm_write_avg_us", "shm_pop_empty_ratio",
        "shm_p50_us", "shm_p90_us", "shm_p99_us",
    ]

    with open(output_csv, "w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        for row in all_summaries:
            writer.writerow(row)

    print(f"\nSummary CSV written to: {output_csv}")

    # Print comparison table
    print_comparison_table(all_summaries)


def print_comparison_table(summaries: List[dict]) -> None:
    """Print a text table comparing socket vs SHM performance."""
    # Group by (experiment, op, clients, pipeline) for comparison
    groups = defaultdict(dict)
    for s in summaries:
        key = (s["experiment"], s["op"], s["clients"], s["pipeline"])
        groups[key][s["transport"]] = s

    # Filter to groups that have both a socket transport and shm
    comparable = []
    for key, transport_map in groups.items():
        if "shm" in transport_map and len(transport_map) > 1:
            comparable.append((key, transport_map))

    if not comparable:
        print("\n(No directly comparable socket vs SHM pairs found)")
        return

    print("\n" + "=" * 100)
    print(f"{'Experiment':<10} {'Op':<12} {'Clients':<8} {'Pipeline':<8} "
          f"{'Socket QPS':<12} {'SHM QPS':<12} {'Delta%':<10} "
          f"{'Socket P99':<12} {'SHM P99':<12} {'Delta%':<10}")
    print("-" * 100)

    for key, transport_map in sorted(comparable):
        exp, op, clients, pipeline = key
        shm = transport_map["shm"]
        # Find the other transport (rocket or header)
        socket_transport = [t for t in transport_map if t != "shm"][0]
        socket = transport_map[socket_transport]

        socket_qps = socket["qps_median"]
        shm_qps = shm["qps_median"]
        qps_delta = ((shm_qps - socket_qps) / socket_qps * 100) if socket_qps > 0 else 0

        socket_p99 = socket["p99_latency_us"]
        shm_p99 = shm["p99_latency_us"]
        p99_delta = ((shm_p99 - socket_p99) / socket_p99 * 100) if socket_p99 > 0 else 0

        print(f"{exp:<10} {op:<12} {str(clients):<8} {str(pipeline):<8} "
              f"{socket_qps:<12.1f} {shm_qps:<12.1f} {qps_delta:>+8.1f}% "
              f"{socket_p99:<12.2f} {shm_p99:<12.2f} {p99_delta:>+8.1f}%")

    print("=" * 100)


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(
        description="Parse benchmark results and produce summary CSV + comparison table"
    )
    parser.add_argument(
        "results_dir",
        help="Directory containing *_client.log files from run_benchmark.sh"
    )
    parser.add_argument(
        "--output", "-o",
        default="",
        help="Output CSV path (default: <results_dir>/summary.csv)"
    )
    args = parser.parse_args()

    output_csv = args.output or os.path.join(args.results_dir, "summary.csv")
    process_results(args.results_dir, output_csv)


if __name__ == "__main__":
    main()
