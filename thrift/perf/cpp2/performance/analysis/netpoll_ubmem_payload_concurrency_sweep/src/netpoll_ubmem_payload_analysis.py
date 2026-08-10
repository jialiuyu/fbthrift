from __future__ import annotations

import argparse
import csv
from dataclasses import asdict, dataclass
from pathlib import Path
from statistics import mean, stdev
from typing import Sequence

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib.colors import LogNorm


PAYLOADS = (0, 84, 168, 335, 670, 1340, 3350)
CONCURRENCIES = (1, 4, 16, 64, 128, 256, 512, 1024, 2048)


@dataclass(frozen=True)
class Measurement:
    source_index: int
    repeat_id: int
    value: str
    concurrency: int
    payload_bytes: int
    target_qps: int
    cost_reported: str
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
    source_index: int
    value: str
    concurrency: int
    payload_bytes: int
    target_qps: int
    cost_reported: str
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
    sample_count: int
    tps_stddev: float
    tp99_stddev_us: float
    tp999_stddev_us: float
    payload_peak_tps: float
    payload_peak_concurrency: int
    tps_vs_payload_peak_pct: float
    tps_retention_vs_zero_payload_pct: float
    tp99_amplification_vs_c64: float
    tp999_amplification_vs_c64: float
    tps_gain_vs_previous_concurrency_pct: float | None
    tp99_change_vs_previous_concurrency_pct: float | None


def load_measurements(path: Path) -> list[Measurement]:
    rows: list[Measurement] = []
    with path.open(newline="", encoding="utf-8") as source:
        for raw in csv.DictReader(source):
            rows.append(
                Measurement(
                    source_index=int(raw["source_index"]),
                    repeat_id=1 if int(raw["source_index"]) <= 63 else 2,
                    value=raw["value"],
                    concurrency=int(raw["concurrency"]), payload_bytes=int(raw["payload_bytes"]),
                    target_qps=int(raw["target_qps"]), cost_reported=raw["cost_reported"],
                    tps=float(raw["tps_kops"]) * 1000.0, tp99_us=float(raw["tp99_us"]),
                    tp999_us=float(raw["tp999_us"]), client_cpu_pct=float(raw["client_cpu_pct"]),
                    server_cpu_pct=float(raw["server_cpu_pct"]), client_rss_kb=int(raw["client_rss_kb"]),
                    server_rss_kb=int(raw["server_rss_kb"]), retry=int(raw["retry"]), status=raw["status"],
                )
            )
    return rows


def derive_measurements(rows: Sequence[Measurement]) -> list[DerivedMeasurement]:
    grouped: dict[tuple[int, int], list[Measurement]] = {}
    for row in rows:
        grouped.setdefault((row.payload_bytes, row.concurrency), []).append(row)
    expected = {(p, c) for p in PAYLOADS for c in CONCURRENCIES}
    missing = sorted(expected - set(grouped))
    if missing:
        raise ValueError(f"incomplete or duplicate matrix: missing={missing}")

    def stats(payload: int, concurrency: int) -> dict[str, float]:
        samples = grouped[(payload, concurrency)]
        result: dict[str, float] = {"sample_count": float(len(samples))}
        for field in (
            "tps", "tp99_us", "tp999_us", "client_cpu_pct", "server_cpu_pct",
            "client_rss_kb", "server_rss_kb",
        ):
            values = [float(getattr(sample, field)) for sample in samples]
            result[field] = mean(values)
            result[f"{field}_stddev"] = stdev(values) if len(values) >= 2 else 0.0
        return result

    cell_stats = {(p, c): stats(p, c) for p in PAYLOADS for c in CONCURRENCIES}
    peaks = {
        payload: max(CONCURRENCIES, key=lambda c: cell_stats[(payload, c)]["tps"])
        for payload in PAYLOADS
    }
    derived: list[DerivedMeasurement] = []
    for payload in PAYLOADS:
      for concurrency in CONCURRENCIES:
        samples = grouped[(payload, concurrency)]
        row = samples[0]
        current = cell_stats[(payload, concurrency)]
        peak_concurrency = peaks[payload]
        peak = cell_stats[(payload, peak_concurrency)]
        zero_payload = cell_stats[(0, concurrency)]
        c64 = cell_stats[(payload, 64)]
        position = CONCURRENCIES.index(concurrency)
        previous = None if position == 0 else cell_stats[(payload, CONCURRENCIES[position - 1])]
        total_cpu = current["client_cpu_pct"] + current["server_cpu_pct"]
        used_cores = total_cpu / 100.0
        derived.append(
            DerivedMeasurement(
                source_index=min(sample.source_index for sample in samples), value=row.value,
                concurrency=concurrency, payload_bytes=payload, target_qps=row.target_qps,
                cost_reported=row.cost_reported, tps=current["tps"], tp99_us=current["tp99_us"],
                tp999_us=current["tp999_us"], client_cpu_pct=current["client_cpu_pct"],
                server_cpu_pct=current["server_cpu_pct"],
                total_cpu_pct=total_cpu, estimated_used_cores=used_cores,
                tps_per_used_core=current["tps"] / used_cores,
                client_rss_kb=current["client_rss_kb"], server_rss_kb=current["server_rss_kb"],
                retry=max(sample.retry for sample in samples), status="OK",
                sample_count=len(samples), tps_stddev=current["tps_stddev"],
                tp99_stddev_us=current["tp99_us_stddev"], tp999_stddev_us=current["tp999_us_stddev"],
                payload_peak_tps=peak["tps"], payload_peak_concurrency=peak_concurrency,
                tps_vs_payload_peak_pct=100.0 * current["tps"] / peak["tps"],
                tps_retention_vs_zero_payload_pct=100.0 * current["tps"] / zero_payload["tps"],
                tp99_amplification_vs_c64=current["tp99_us"] / c64["tp99_us"],
                tp999_amplification_vs_c64=current["tp999_us"] / c64["tp999_us"],
                tps_gain_vs_previous_concurrency_pct=(None if previous is None else 100.0 * (current["tps"] / previous["tps"] - 1.0)),
                tp99_change_vs_previous_concurrency_pct=(None if previous is None else 100.0 * (current["tp99_us"] / previous["tp99_us"] - 1.0)),
            )
        )
    return derived


def write_derived_csv(rows: Sequence[DerivedMeasurement], path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as target:
        writer = csv.DictWriter(target, fieldnames=list(asdict(rows[0]).keys()), lineterminator="\n")
        writer.writeheader()
        writer.writerows(asdict(row) for row in rows)


def _style() -> None:
    plt.rcParams.update({
        "font.family": "DejaVu Sans", "font.size": 10.5, "axes.titlesize": 13,
        "axes.labelsize": 11, "legend.fontsize": 8.5, "axes.spines.top": False,
        "axes.spines.right": False, "grid.color": "#C8C8C8", "grid.linewidth": 0.55,
        "grid.alpha": 0.45, "svg.fonttype": "none",
    })


def _save(fig: plt.Figure, output_dir: Path, stem: str) -> list[Path]:
    output_dir.mkdir(parents=True, exist_ok=True)
    outputs: list[Path] = []
    for suffix in ("png", "svg"):
        path = output_dir / f"{stem}.{suffix}"
        fig.savefig(path, dpi=220, bbox_inches="tight", facecolor="white")
        if suffix == "svg":
            svg = path.read_text(encoding="utf-8")
            path.write_text("\n".join(line.rstrip() for line in svg.splitlines()) + "\n", encoding="utf-8")
        outputs.append(path)
    plt.close(fig)
    return outputs


def _matrix(rows: Sequence[DerivedMeasurement], field: str) -> list[list[float]]:
    return [[float(getattr(next(row for row in rows if row.payload_bytes == p and row.concurrency == c), field)) for c in CONCURRENCIES] for p in PAYLOADS]


def _plot_surface(rows: Sequence[DerivedMeasurement], output_dir: Path) -> list[Path]:
    fig, axes = plt.subplots(1, 2, figsize=(16.2, 6.4), constrained_layout=True)
    tps = [[value / 1000.0 for value in row] for row in _matrix(rows, "tps")]
    tp99 = _matrix(rows, "tp99_us")
    images = [
        axes[0].imshow(tps, aspect="auto", cmap="YlGnBu"),
        axes[1].imshow(tp99, aspect="auto", cmap="YlOrRd", norm=LogNorm(vmin=50, vmax=33000)),
    ]
    for axis, image, title, matrix in zip(axes, images, ("Throughput (Kops/s)", "TP99 (µs)"), (tps, tp99)):
        axis.set_title(title)
        axis.set_xticks(range(len(CONCURRENCIES)), [str(value) for value in CONCURRENCIES])
        axis.set_yticks(range(len(PAYLOADS)), [f"{value:,}" for value in PAYLOADS])
        axis.set_xlabel("Closed-loop concurrency")
        axis.set_ylabel("Payload size (B)")
        fig.colorbar(image, ax=axis, shrink=0.82, pad=0.02)
        midpoint = 550 if title.startswith("Throughput") else 700
        for row_index, matrix_row in enumerate(matrix):
            for column_index, value in enumerate(matrix_row):
                label = f"{value:.1f}" if title.startswith("Throughput") else f"{value:,.0f}"
                axis.text(column_index, row_index, label, ha="center", va="center", fontsize=7.2,
                          color="white" if value > midpoint else "#202020")
    fig.suptitle("Netpoll Ubmem Baseline Surface", fontweight="bold")
    return _save(fig, output_dir, "01_ubmem_payload_concurrency_surface")


def _plot_scaling(rows: Sequence[DerivedMeasurement], output_dir: Path) -> list[Path]:
    fig, axes = plt.subplots(1, 2, figsize=(13.4, 5.4), constrained_layout=True)
    x = list(range(len(CONCURRENCIES)))
    colors = plt.get_cmap("viridis")([i / (len(PAYLOADS) - 1) for i in range(len(PAYLOADS))])
    markers = ("o", "s", "^", "D", "v", "P", "X")
    for index, payload in enumerate(PAYLOADS):
        series = [next(row for row in rows if row.payload_bytes == payload and row.concurrency == c) for c in CONCURRENCIES]
        repeated = max(row.sample_count for row in series) >= 2
        style = {"color": colors[index], "marker": markers[index], "linewidth": 1.7,
                 "markersize": 4.8, "label": f"Payload = {payload} B" + (" (n=2)" if repeated else "")}
        if repeated:
            axes[0].errorbar(x, [row.tps / 1000.0 for row in series],
                             yerr=[row.tps_stddev / 1000.0 for row in series], capsize=3, **style)
            axes[1].errorbar(x, [row.tp99_us for row in series],
                             yerr=[row.tp99_stddev_us for row in series], capsize=3, **style)
        else:
            axes[0].plot(x, [row.tps / 1000.0 for row in series], **style)
            axes[1].plot(x, [row.tp99_us for row in series], **style)
    axes[0].set_title("Achieved throughput")
    axes[1].set_title("TP99 latency")
    axes[0].set_ylabel("TPS (Kops/s)")
    axes[1].set_ylabel("TP99 (µs, log scale)")
    axes[1].set_yscale("log")
    for axis in axes:
        axis.set_xticks(x, [str(value) for value in CONCURRENCIES], rotation=20)
        axis.set_xlabel("Closed-loop concurrency")
        axis.grid(True, axis="y")
    handles, labels = axes[0].get_legend_handles_labels()
    order = sorted(
        range(len(labels)),
        key=lambda index: PAYLOADS.index(int(labels[index].split()[2])),
    )
    axes[0].legend(
        [handles[index] for index in order],
        [labels[index] for index in order],
        loc="upper left",
        frameon=True,
        framealpha=0.92,
        ncol=2,
    )
    fig.suptitle("Netpoll Ubmem Concurrency Scaling", fontweight="bold")
    return _save(fig, output_dir, "02_ubmem_concurrency_scaling_by_payload")


def generate_figures(rows: Sequence[DerivedMeasurement], output_dir: Path) -> list[Path]:
    _style()
    return _plot_surface(rows, output_dir) + _plot_scaling(rows, output_dir)


def main() -> None:
    project_dir = Path(__file__).resolve().parent.parent
    parser = argparse.ArgumentParser()
    parser.add_argument("--input", type=Path, default=project_dir / "data" / "netpoll_ubmem_baseline_raw_20260810.csv")
    parser.add_argument("--derived-output", type=Path, default=project_dir / "data" / "netpoll_ubmem_baseline_derived_20260810.csv")
    parser.add_argument("--figure-dir", type=Path, default=project_dir / "figures")
    args = parser.parse_args()
    rows = derive_measurements(load_measurements(args.input))
    write_derived_csv(rows, args.derived_output)
    for output in generate_figures(rows, args.figure_dir):
        print(output)
    print(args.derived_output)


if __name__ == "__main__":
    main()
