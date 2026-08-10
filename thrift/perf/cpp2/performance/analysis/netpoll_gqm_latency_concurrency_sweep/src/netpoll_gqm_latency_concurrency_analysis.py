from __future__ import annotations

import argparse
import csv
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Sequence

import matplotlib

matplotlib.use("Agg")

import matplotlib.pyplot as plt
from matplotlib.ticker import MaxNLocator


REFERENCE_NS = 335
CONCURRENCIES = (1, 4, 16, 64, 128)
COSTS_NS = (0, 84, 168, 335, 670, 1340, 3350)
MULTIPLIER_LABELS = ("0×", "1/4×", "1/2×", "1×", "2×", "4×", "10×")
COLORS = {
    1: "#4477AA",
    4: "#66CCEE",
    16: "#228833",
    64: "#CCBB44",
    128: "#AA3377",
}
MARKERS = {1: "o", 4: "s", 16: "^", 64: "D", 128: "P"}


@dataclass(frozen=True)
class Measurement:
    source_batch: str
    source_index: int
    value: str
    concurrency: int
    body_bytes: int
    target_qps: int
    cost_ns: int
    tps: float
    tp99_us: float
    tp999_us: float
    client_cpu_pct: float
    server_cpu_pct: float
    client_rss_kb: int
    server_rss_kb: int
    retry: int
    status: str


@dataclass(frozen=True)
class DerivedMeasurement:
    source_batch: str
    source_index: int
    value: str
    concurrency: int
    body_bytes: int
    target_qps: int
    cost_ns: int
    cost_multiplier: float
    tps: float
    tp99_us: float
    tp999_us: float
    client_cpu_pct: float
    server_cpu_pct: float
    total_cpu_pct: float
    estimated_used_cores: float
    tps_per_used_core: float
    client_rss_kb: int
    server_rss_kb: int
    retry: int
    status: str
    baseline_tps: float
    baseline_tp99_us: float
    baseline_tp999_us: float
    tps_retention_pct: float
    tp99_amplification: float
    tp999_amplification: float
    tps_vs_c16_pct: float
    tp99_vs_c16_pct: float


def load_measurements(path: Path) -> list[Measurement]:
    rows: list[Measurement] = []
    with path.open(newline="", encoding="utf-8") as source:
        for raw in csv.DictReader(source):
            rows.append(
                Measurement(
                    source_batch=raw["source_batch"],
                    source_index=int(raw["source_index"]),
                    value=raw["value"],
                    concurrency=int(raw["concurrency"]),
                    body_bytes=int(raw["body_bytes"]),
                    target_qps=int(raw["target_qps"]),
                    cost_ns=int(raw["cost_ns"]),
                    tps=float(raw["tps_kops"]) * 1000.0,
                    tp99_us=float(raw["tp99_us"]),
                    tp999_us=float(raw["tp999_us"]),
                    client_cpu_pct=float(raw["client_cpu_pct"]),
                    server_cpu_pct=float(raw["server_cpu_pct"]),
                    client_rss_kb=int(raw["client_rss_kb"]),
                    server_rss_kb=int(raw["server_rss_kb"]),
                    retry=int(raw["retry"]),
                    status=raw["status"],
                )
            )
    return rows


def derive_measurements(
    rows: Sequence[Measurement],
) -> list[DerivedMeasurement]:
    by_cell = {(row.concurrency, row.cost_ns): row for row in rows}
    expected = {(c, cost) for c in CONCURRENCIES for cost in COSTS_NS}
    missing = sorted(expected - set(by_cell))
    duplicates = len(rows) != len(by_cell)
    if missing or duplicates:
        raise ValueError(
            f"incomplete or duplicate matrix: missing={missing}, duplicates={duplicates}"
        )

    derived: list[DerivedMeasurement] = []
    for row in sorted(rows, key=lambda item: (item.cost_ns, item.concurrency)):
        baseline = by_cell[(row.concurrency, 0)]
        c16 = by_cell[(16, row.cost_ns)]
        total_cpu = row.client_cpu_pct + row.server_cpu_pct
        used_cores = total_cpu / 100.0
        derived.append(
            DerivedMeasurement(
                source_batch=row.source_batch,
                source_index=row.source_index,
                value=row.value,
                concurrency=row.concurrency,
                body_bytes=row.body_bytes,
                target_qps=row.target_qps,
                cost_ns=row.cost_ns,
                cost_multiplier=round(row.cost_ns / REFERENCE_NS, 2),
                tps=row.tps,
                tp99_us=row.tp99_us,
                tp999_us=row.tp999_us,
                client_cpu_pct=row.client_cpu_pct,
                server_cpu_pct=row.server_cpu_pct,
                total_cpu_pct=total_cpu,
                estimated_used_cores=used_cores,
                tps_per_used_core=row.tps / used_cores,
                client_rss_kb=row.client_rss_kb,
                server_rss_kb=row.server_rss_kb,
                retry=row.retry,
                status=row.status,
                baseline_tps=baseline.tps,
                baseline_tp99_us=baseline.tp99_us,
                baseline_tp999_us=baseline.tp999_us,
                tps_retention_pct=100.0 * row.tps / baseline.tps,
                tp99_amplification=row.tp99_us / baseline.tp99_us,
                tp999_amplification=row.tp999_us / baseline.tp999_us,
                tps_vs_c16_pct=100.0 * row.tps / c16.tps,
                tp99_vs_c16_pct=100.0 * row.tp99_us / c16.tp99_us,
            )
        )
    return derived


def write_derived_csv(rows: Sequence[DerivedMeasurement], path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as target:
        writer = csv.DictWriter(
            target,
            fieldnames=list(asdict(rows[0]).keys()),
            lineterminator="\n",
        )
        writer.writeheader()
        writer.writerows(asdict(row) for row in rows)


def _academic_style() -> None:
    plt.rcParams.update(
        {
            "font.family": "DejaVu Sans",
            "font.size": 10.5,
            "axes.titlesize": 13,
            "axes.labelsize": 11,
            "legend.fontsize": 9,
            "xtick.labelsize": 9.5,
            "ytick.labelsize": 9.5,
            "axes.spines.top": False,
            "axes.spines.right": False,
            "axes.linewidth": 0.8,
            "grid.color": "#C8C8C8",
            "grid.linewidth": 0.55,
            "grid.alpha": 0.45,
            "svg.fonttype": "none",
        }
    )


def _save(fig: plt.Figure, output_dir: Path, stem: str) -> list[Path]:
    output_dir.mkdir(parents=True, exist_ok=True)
    outputs: list[Path] = []
    for suffix in ("png", "svg"):
        path = output_dir / f"{stem}.{suffix}"
        fig.savefig(path, dpi=220, bbox_inches="tight", facecolor="white")
        if suffix == "svg":
            svg = path.read_text(encoding="utf-8")
            path.write_text(
                "\n".join(line.rstrip() for line in svg.splitlines()) + "\n",
                encoding="utf-8",
            )
        outputs.append(path)
    plt.close(fig)
    return outputs


def _plot_sensitivity(
    rows: Sequence[DerivedMeasurement], output_dir: Path
) -> list[Path]:
    fig, axes = plt.subplots(1, 2, figsize=(12.6, 5.1), constrained_layout=True)
    x = list(range(len(COSTS_NS)))
    for concurrency in CONCURRENCIES:
        series = [
            next(
                row
                for row in rows
                if row.concurrency == concurrency and row.cost_ns == cost
            )
            for cost in COSTS_NS
        ]
        style = {
            "color": COLORS[concurrency],
            "marker": MARKERS[concurrency],
            "linewidth": 1.8,
            "markersize": 5.5,
            "label": f"Concurrency = {concurrency}",
        }
        axes[0].plot(x, [row.tps_retention_pct for row in series], **style)
        axes[1].plot(x, [row.tp99_amplification for row in series], **style)

    axes[0].axhline(100, color="#666666", linewidth=0.9, linestyle="--")
    axes[1].axhline(1, color="#666666", linewidth=0.9, linestyle="--")
    axes[0].set_title("Throughput retention")
    axes[1].set_title("Tail-latency amplification")
    axes[0].set_ylabel("TPS retained (%)")
    axes[1].set_ylabel("TP99 amplification (× baseline)")
    axes[0].set_ylim(0, 106)
    axes[1].set_ylim(0.8, 6.35)
    for axis in axes:
        axis.set_xticks(x, MULTIPLIER_LABELS)
        axis.set_xlabel("Added GQM latency (335 ns = 1×)")
        axis.grid(True, axis="y")
    axes[0].legend(loc="upper right", frameon=True, framealpha=0.92)
    fig.suptitle("Netpoll GQM-Latency Sensitivity", fontweight="bold")
    return _save(fig, output_dir, "01_gqm_latency_sensitivity_by_concurrency")


def _plot_scaling(
    rows: Sequence[DerivedMeasurement], output_dir: Path
) -> list[Path]:
    fig, axes = plt.subplots(1, 2, figsize=(12.6, 5.1), constrained_layout=True)
    x = list(range(len(CONCURRENCIES)))
    cost_colors = plt.get_cmap("viridis")(
        [index / (len(COSTS_NS) - 1) for index in range(len(COSTS_NS))]
    )
    markers = ("o", "s", "^", "D", "v", "P", "X")
    for index, cost in enumerate(COSTS_NS):
        series = [
            next(
                row
                for row in rows
                if row.concurrency == concurrency and row.cost_ns == cost
            )
            for concurrency in CONCURRENCIES
        ]
        label = f"{MULTIPLIER_LABELS[index]} ({cost} ns)"
        style = {
            "color": cost_colors[index],
            "marker": markers[index],
            "linewidth": 1.7,
            "markersize": 5.3,
            "label": label,
        }
        axes[0].plot(x, [row.tps / 1000.0 for row in series], **style)
        axes[1].plot(x, [row.tp99_us for row in series], **style)

    axes[0].set_title("Achieved throughput")
    axes[1].set_title("TP99 latency")
    axes[0].set_ylabel("TPS (Kops/s)")
    axes[1].set_ylabel("TP99 (µs)")
    axes[0].set_ylim(bottom=0)
    axes[1].set_ylim(bottom=0)
    axes[0].yaxis.set_major_locator(MaxNLocator(nbins=6))
    axes[1].yaxis.set_major_locator(MaxNLocator(nbins=6))
    for axis in axes:
        axis.set_xticks(x, [str(value) for value in CONCURRENCIES])
        axis.set_xlabel("Closed-loop concurrency")
        axis.grid(True, axis="y")
    axes[0].legend(
        loc="upper left",
        frameon=True,
        framealpha=0.92,
        ncol=2,
        columnspacing=0.9,
        handlelength=1.8,
    )
    fig.suptitle(
        "Concurrency Scaling Under Added GQM Latency", fontweight="bold"
    )
    return _save(fig, output_dir, "02_concurrency_scaling_under_gqm_latency")


def generate_figures(
    rows: Sequence[DerivedMeasurement], output_dir: Path
) -> list[Path]:
    _academic_style()
    return _plot_sensitivity(rows, output_dir) + _plot_scaling(rows, output_dir)


def main() -> None:
    project_dir = Path(__file__).resolve().parent.parent
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--input",
        type=Path,
        default=project_dir
        / "data"
        / "netpoll_gqm_latency_concurrency_raw_20260810.csv",
    )
    parser.add_argument(
        "--derived-output",
        type=Path,
        default=project_dir
        / "data"
        / "netpoll_gqm_latency_concurrency_derived_20260810.csv",
    )
    parser.add_argument(
        "--figure-dir", type=Path, default=project_dir / "figures"
    )
    args = parser.parse_args()

    derived = derive_measurements(load_measurements(args.input))
    write_derived_csv(derived, args.derived_output)
    for output in generate_figures(derived, args.figure_dir):
        print(output)
    print(args.derived_output)


if __name__ == "__main__":
    main()
