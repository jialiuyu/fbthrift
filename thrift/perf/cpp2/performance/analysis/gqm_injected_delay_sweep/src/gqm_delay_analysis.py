from __future__ import annotations

import argparse
import csv
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, Sequence

import matplotlib

matplotlib.use("Agg")

import matplotlib.pyplot as plt
from matplotlib.lines import Line2D


EXPERIMENT_LABEL = (
    "Ubmem | OpenLoop Poisson | Unary64 | 1 client thread / connection | "
    "max_inflight=496 | single run"
)
COLORS = {
    "p50": "#2f6f9f",
    "p99": "#d17a22",
    "p999": "#a74747",
    "completed": "#2f8f5b",
    "shed": "#a74747",
    "client": "#2f6f9f",
    "server": "#7a4e9d",
}
CLIFF_LOW_MULTIPLIER = 40
CLIFF_HIGH_MULTIPLIER = 100


@dataclass(frozen=True)
class RpcResult:
    target_qps: int
    runtime_s: float
    scheduled: int
    dispatched: int
    completed: int
    succeeded: int
    failed: int
    client_shed: int
    inflight_final: int
    drain_timed_out: bool
    send_lag_p50_us: float
    send_lag_p99_us: float
    send_lag_p999_us: float
    send_lag_max_us: float
    latency_p50_us: float
    latency_p99_us: float
    latency_p999_us: float
    latency_max_us: float
    client_cores: float
    server_cores: float
    server_wall_s: float
    inject_ns: int = 0
    multiplier: int = 0
    source_label: str = ""

    @property
    def scheduled_qps(self) -> float:
        return self.scheduled / self.runtime_s

    @property
    def completed_qps(self) -> float:
        return self.completed / self.runtime_s

    @property
    def shed_pct(self) -> float:
        return 100.0 * self.client_shed / self.scheduled if self.scheduled else 0.0

@dataclass(frozen=True)
class MicroResult:
    inject_ns: int
    benchmark: str
    latency_ns: float
    reported_rate_per_s: float
    successful_hw_ops_per_iteration: int
    path: str
    injection_scope: str


@dataclass(frozen=True)
class FixedOutstandingResult:
    suite: str
    workload: str
    inject_ns: int
    multiplier: int
    total_time_s: float
    warmup_s: float
    active_time_s: float
    total_requests: int
    avg_qps: float
    max_outstanding_ops: int
    chunk_size_bytes: int | None
    latency_p50_us: float
    latency_p90_us: float
    latency_p99_us: float
    latency_p999_us: float
    latency_max_us: float
    source_label: str

    @property
    def implied_cycle_us(self) -> float:
        return self.max_outstanding_ops * 1_000_000.0 / self.avg_qps

    def qps_retention_pct(self, baseline: "FixedOutstandingResult") -> float:
        return 100.0 * self.avg_qps / baseline.avg_qps

    def effective_exposure(self, baseline: "FixedOutstandingResult") -> float:
        if self.inject_ns <= 0:
            raise ValueError("effective exposure is undefined at zero injected delay")
        return (self.implied_cycle_us - baseline.implied_cycle_us) / (
            self.inject_ns / 1000.0
        )


@dataclass(frozen=True)
class CycleSensitivityFit:
    intercept_us: float
    slope_us_per_injected_us: float
    r_squared: float


RPC_NUMERIC_FIELDS = {
    "target_qps": int,
    "runtime_s": float,
    "scheduled": int,
    "dispatched": int,
    "completed": int,
    "succeeded": int,
    "failed": int,
    "client_shed": int,
    "inflight_final": int,
    "send_lag_p50_us": float,
    "send_lag_p99_us": float,
    "send_lag_p999_us": float,
    "send_lag_max_us": float,
    "latency_p50_us": float,
    "latency_p99_us": float,
    "latency_p999_us": float,
    "latency_max_us": float,
    "client_cores": float,
    "server_cores": float,
    "server_wall_s": float,
}


def _parse_bool(value: str) -> bool:
    normalized = value.strip().lower()
    if normalized not in {"true", "false"}:
        raise ValueError(f"expected true/false, got {value!r}")
    return normalized == "true"


def load_rpc_results(path: Path) -> list[RpcResult]:
    rows: list[RpcResult] = []
    with path.open(newline="", encoding="utf-8") as source:
        for raw in csv.DictReader(source):
            parsed: dict[str, object] = {
                field: parser(raw[field]) for field, parser in RPC_NUMERIC_FIELDS.items()
            }
            parsed["drain_timed_out"] = _parse_bool(raw["drain_timed_out"])
            if "inject_ns" in raw:
                parsed["inject_ns"] = int(raw["inject_ns"])
                parsed["multiplier"] = int(raw["multiplier"])
                parsed["source_label"] = raw["source_label"]
            rows.append(RpcResult(**parsed))  # type: ignore[arg-type]
    return rows


def load_micro_results(path: Path) -> list[MicroResult]:
    rows: list[MicroResult] = []
    with path.open(newline="", encoding="utf-8") as source:
        for raw in csv.DictReader(source):
            rows.append(
                MicroResult(
                    inject_ns=int(raw["inject_ns"]),
                    benchmark=raw["benchmark"],
                    latency_ns=float(raw["latency_ns"]),
                    reported_rate_per_s=float(raw["reported_rate_per_s"]),
                    successful_hw_ops_per_iteration=int(
                        raw["successful_hw_ops_per_iteration"]
                    ),
                    path=raw["path"],
                    injection_scope=raw["injection_scope"],
                )
            )
    return rows


def load_fixed_outstanding_results(path: Path) -> list[FixedOutstandingResult]:
    rows: list[FixedOutstandingResult] = []
    with path.open(newline="", encoding="utf-8") as source:
        for raw in csv.DictReader(source):
            chunk_size = raw.get("chunk_size_bytes", "").strip()
            rows.append(
                FixedOutstandingResult(
                    suite=raw["suite"],
                    workload=raw["workload"],
                    inject_ns=int(raw["inject_ns"]),
                    multiplier=int(raw["multiplier"]),
                    total_time_s=float(raw["total_time_s"]),
                    warmup_s=float(raw["warmup_s"]),
                    active_time_s=float(raw["active_time_s"]),
                    total_requests=int(raw["total_requests"]),
                    avg_qps=float(raw["avg_qps"]),
                    max_outstanding_ops=int(raw["max_outstanding_ops"]),
                    chunk_size_bytes=int(chunk_size) if chunk_size else None,
                    latency_p50_us=float(raw["latency_p50_us"]),
                    latency_p90_us=float(raw["latency_p90_us"]),
                    latency_p99_us=float(raw["latency_p99_us"]),
                    latency_p999_us=float(raw["latency_p999_us"]),
                    latency_max_us=float(raw["latency_max_us"]),
                    source_label=raw["source_label"],
                )
            )
    return sorted(rows, key=lambda row: row.inject_ns)


def fit_cycle_sensitivity(
    rows: Sequence[FixedOutstandingResult],
) -> CycleSensitivityFit:
    if len(rows) < 2:
        raise ValueError("cycle sensitivity fit requires at least two points")
    x = [row.inject_ns / 1000.0 for row in rows]
    y = [row.implied_cycle_us for row in rows]
    x_mean = sum(x) / len(x)
    y_mean = sum(y) / len(y)
    x_variance = sum((value - x_mean) ** 2 for value in x)
    if x_variance == 0:
        raise ValueError("cycle sensitivity fit requires distinct delays")
    slope = sum(
        (x_value - x_mean) * (y_value - y_mean)
        for x_value, y_value in zip(x, y)
    ) / x_variance
    intercept = y_mean - slope * x_mean
    residual_sum = sum(
        (y_value - (intercept + slope * x_value)) ** 2
        for x_value, y_value in zip(x, y)
    )
    total_sum = sum((value - y_mean) ** 2 for value in y)
    r_squared = 1.0 - residual_sum / total_sum if total_sum else 1.0
    return CycleSensitivityFit(intercept, slope, r_squared)


def latency_cliff_interval(
    rows: Iterable[RpcResult],
    *,
    metric: str = "latency_p50_us",
    adjacent_ratio: float = 2.0,
) -> tuple[int, int] | None:
    ordered_rows = sorted(rows, key=lambda row: row.inject_ns)
    for previous, current in zip(ordered_rows, ordered_rows[1:]):
        if getattr(current, metric) / getattr(previous, metric) >= adjacent_ratio:
            return previous.inject_ns, current.inject_ns
    return None


def normalized_micro_delta_ns(
    rows: Iterable[MicroResult], benchmark: str
) -> dict[int, float]:
    series = sorted(
        (row for row in rows if row.benchmark == benchmark),
        key=lambda row: row.inject_ns,
    )
    if not series:
        raise ValueError(f"benchmark not found: {benchmark}")
    op_count = series[0].successful_hw_ops_per_iteration
    if op_count <= 0:
        raise ValueError(f"benchmark has no successful injected operations: {benchmark}")
    baseline = next(row.latency_ns for row in series if row.inject_ns == 0)
    return {
        row.inject_ns: (row.latency_ns - baseline) / op_count for row in series
    }


def _save_figure(fig: plt.Figure, output_dir: Path, stem: str) -> list[Path]:
    output_dir.mkdir(parents=True, exist_ok=True)
    outputs: list[Path] = []
    for suffix in ("png", "svg"):
        path = output_dir / f"{stem}.{suffix}"
        fig.savefig(path, dpi=180, bbox_inches="tight")
        if suffix == "svg":
            svg = path.read_text(encoding="utf-8")
            path.write_text(
                "\n".join(line.rstrip() for line in svg.splitlines()) + "\n",
                encoding="utf-8",
            )
        outputs.append(path)
    plt.close(fig)
    return outputs


def _style_axis(axis: plt.Axes) -> None:
    axis.grid(True, which="both", linewidth=0.55, alpha=0.24)
    axis.spines[["top", "right"]].set_visible(False)


def _shade_baseline_knee(axis: plt.Axes) -> None:
    axis.axvspan(35, 40, color="#e5a54b", alpha=0.12, zorder=0)


def _plot_rpc_saturation(
    rows: Sequence[RpcResult], output_dir: Path
) -> list[Path]:
    rows = sorted(rows, key=lambda row: row.target_qps)
    x = [row.target_qps / 1000 for row in rows]
    target = [row.target_qps / 1000 for row in rows]
    completed = [row.completed_qps / 1000 for row in rows]

    fig, axes = plt.subplots(
        3,
        1,
        figsize=(11.5, 10.5),
        sharex=True,
        gridspec_kw={"height_ratios": (1.0, 1.25, 0.8)},
        constrained_layout=True,
    )
    throughput_axis, latency_axis, reliability_axis = axes

    throughput_axis.plot(
        x,
        target,
        color="#777777",
        linestyle="--",
        linewidth=1.3,
        label="Ideal: completed = target",
    )
    throughput_axis.plot(
        x,
        completed,
        color=COLORS["completed"],
        marker="o",
        linewidth=2.0,
        label="Measured completed QPS",
    )
    throughput_axis.set_ylabel("Completed QPS (k)")
    throughput_axis.legend(frameon=False, loc="upper left")
    throughput_axis.annotate(
        f"{completed[-1]:.1f}k completed",
        (x[-1], completed[-1]),
        xytext=(-8, -28),
        textcoords="offset points",
        ha="right",
        color=COLORS["completed"],
        fontsize=9,
    )

    metrics = (
        ("latency_p50_us", "p50", "p50", "o"),
        ("latency_p99_us", "p99", "p99", "s"),
        ("latency_p999_us", "p99.9 (exploratory)", "p999", "^"),
    )
    for field, label, color_key, marker in metrics:
        latency_axis.plot(
            x,
            [getattr(row, field) for row in rows],
            color=COLORS[color_key],
            marker=marker,
            linewidth=1.8,
            label=label,
        )
    latency_axis.set_yscale("log")
    latency_axis.set_ylabel("RPC latency (us, log scale)")
    latency_axis.legend(frameon=False, ncol=3, loc="upper left")

    reliability_axis.bar(
        x,
        [row.shed_pct for row in rows],
        width=2.2,
        color=COLORS["shed"],
        alpha=0.78,
        label="Client shed / scheduled",
    )
    reliability_axis.set_ylabel("Client shed (%)")
    reliability_axis.set_xlabel("Configured target QPS (k)")
    reliability_axis.set_ylim(0, max(15.5, max(row.shed_pct for row in rows) * 1.15))
    reliability_axis.annotate(
        f"{rows[-1].shed_pct:.2f}% shed",
        (x[-1], rows[-1].shed_pct),
        xytext=(-6, 6),
        textcoords="offset points",
        ha="right",
        fontsize=9,
        color=COLORS["shed"],
    )

    for axis in axes:
        _style_axis(axis)
        _shade_baseline_knee(axis)
    knee_handle = Line2D(
        [0], [0], color="#e5a54b", linewidth=7, alpha=0.3, label="35k-40k stress range"
    )
    reliability_axis.legend(handles=[knee_handle], frameon=False, loc="upper left")
    fig.suptitle(f"RPC saturation sweep: the knee starts before capacity loss\n{EXPERIMENT_LABEL}")
    return _save_figure(fig, output_dir, "01_rpc_saturation_knee")


def _set_multiplier_x_axis(
    axis: plt.Axes,
    rows: Sequence[RpcResult | FixedOutstandingResult],
) -> None:
    ticks = [row.multiplier for row in rows]
    axis.set_xscale("symlog", linthresh=1.0, linscale=0.65)
    axis.set_xticks(ticks)
    axis.set_xticklabels([f"{value}x" for value in ticks])
    axis.set_xlim(-0.15, max(ticks) * 1.12)
    axis.set_xlabel("Added GQM latency (1x ≈ 335 ns; symlog scale)")


def _shade_cliff(axis: plt.Axes) -> None:
    axis.axvspan(
        CLIFF_LOW_MULTIPLIER,
        CLIFF_HIGH_MULTIPLIER,
        color="#e5a54b",
        alpha=0.14,
        zorder=0,
        label="Observed cliff interval",
    )


def _plot_delay_sensitivity(
    rows: Sequence[RpcResult], output_dir: Path
) -> list[Path]:
    rows = sorted(rows, key=lambda row: row.inject_ns)
    x = [row.multiplier for row in rows]
    fig, axes = plt.subplots(
        2,
        1,
        figsize=(12, 8.5),
        sharex=True,
        gridspec_kw={"height_ratios": (1.3, 0.9)},
        constrained_layout=True,
    )
    latency_axis, ratio_axis = axes

    metrics = (
        ("latency_p50_us", "p50", "p50", "o"),
        ("latency_p99_us", "p99", "p99", "s"),
        ("latency_p999_us", "p99.9 (exploratory)", "p999", "^"),
    )
    for field, label, color_key, marker in metrics:
        latency_axis.plot(
            x,
            [getattr(row, field) for row in rows],
            color=COLORS[color_key],
            marker=marker,
            linewidth=1.8,
            label=label,
        )
    latency_axis.set_yscale("log")
    latency_axis.set_ylabel("RPC latency (us, log scale)")
    latency_axis.legend(frameon=False, ncol=3, loc="upper left")
    latency_axis.annotate(
        "p50 = 6.99 ms",
        (100, next(row.latency_p50_us for row in rows if row.inject_ns == 33_500)),
        xytext=(-8, 12),
        textcoords="offset points",
        ha="right",
        color=COLORS["p50"],
    )

    baseline = rows[0]
    for field, label, color_key, marker in metrics[:2]:
        base_value = getattr(baseline, field)
        ratio_axis.plot(
            x,
            [getattr(row, field) / base_value for row in rows],
            color=COLORS[color_key],
            marker=marker,
            linewidth=1.8,
            label=f"{label} / 0-delay anchor",
        )
    ratio_axis.axhline(1.0, color="#777777", linestyle="--", linewidth=1.0)
    ratio_axis.set_yscale("log")
    ratio_axis.set_ylabel("Latency multiplier (log scale)")
    ratio_axis.legend(frameon=False, loc="upper left")

    for axis in axes:
        _style_axis(axis)
        _shade_cliff(axis)
    ratio_axis.legend(frameon=False, loc="upper left")
    _set_multiplier_x_axis(ratio_axis, rows)
    fig.suptitle(
        f"GQM injected-delay sensitivity at 35k target QPS\n{EXPERIMENT_LABEL}"
    )
    return _save_figure(fig, output_dir, "02_gqm_delay_sensitivity")


def _plot_delay_capacity(
    rows: Sequence[RpcResult], output_dir: Path
) -> list[Path]:
    rows = sorted(rows, key=lambda row: row.inject_ns)
    x = [row.multiplier for row in rows]
    fig, axes = plt.subplots(
        3,
        1,
        figsize=(12, 10),
        sharex=True,
        gridspec_kw={"height_ratios": (1.0, 0.9, 0.9)},
        constrained_layout=True,
    )
    throughput_axis, reliability_axis, cpu_axis = axes

    throughput_axis.axhline(
        rows[0].target_qps / 1000,
        color="#777777",
        linestyle="--",
        linewidth=1.2,
        label="Configured target: 35k",
    )
    throughput_axis.plot(
        x,
        [row.completed_qps / 1000 for row in rows],
        color=COLORS["completed"],
        marker="o",
        linewidth=2.0,
        label="Completed QPS",
    )
    throughput_axis.set_ylabel("Completed QPS (k)")
    throughput_axis.legend(frameon=False, loc="lower left")

    reliability_axis.bar(
        x,
        [row.shed_pct for row in rows],
        color=COLORS["shed"],
        width=[max(0.18, value * 0.14) for value in x],
        alpha=0.78,
        label="Client shed / scheduled",
    )
    reliability_axis.set_ylabel("Client shed (%)")
    reliability_axis.set_ylim(0, 15.5)
    last = rows[-1]
    reliability_axis.annotate(
        f"{last.shed_pct:.2f}% shed",
        (last.multiplier, last.shed_pct),
        xytext=(-6, 6),
        textcoords="offset points",
        ha="right",
        color=COLORS["shed"],
    )

    cpu_axis.plot(
        x,
        [row.client_cores for row in rows],
        color=COLORS["client"],
        marker="o",
        linewidth=1.7,
        label="Client process cores (15s measurement)",
    )
    cpu_axis.plot(
        x,
        [row.server_cores for row in rows],
        color=COLORS["server"],
        marker="s",
        linewidth=1.7,
        label="Server cores (whole serve duration)",
    )
    cpu_axis.set_ylabel("Process CPU (cores)")
    cpu_axis.legend(frameon=False, loc="lower left")
    cpu_axis.text(
        0.995,
        0.04,
        "CPU windows differ; compare trends only",
        transform=cpu_axis.transAxes,
        ha="right",
        va="bottom",
        fontsize=8,
        color="#666666",
    )

    for axis in axes:
        _style_axis(axis)
        _shade_cliff(axis)
    _set_multiplier_x_axis(cpu_axis, rows)
    fig.suptitle(f"GQM delay: capacity and reliability boundary\n{EXPERIMENT_LABEL}")
    return _save_figure(fig, output_dir, "03_gqm_delay_capacity")


def _plot_micro_calibration(
    rows: Sequence[MicroResult], output_dir: Path
) -> list[Path]:
    fig, axes = plt.subplots(1, 2, figsize=(13.5, 5.8), constrained_layout=True)
    calibration_axis, control_axis = axes
    calibrated = (
        ("HwSinglePushPopPingPong", "Push+pop / 2", "o"),
        ("HwSinglePushOnly", "Push-only / 496", "s"),
        ("HwSinglePopOnly", "Pop-only / 496", "^"),
        ("HwSingleFill496Drain496", "Fill+drain / 992", "D"),
    )
    for benchmark, label, marker in calibrated:
        deltas = normalized_micro_delta_ns(rows, benchmark)
        x = sorted(deltas)
        calibration_axis.plot(
            x,
            [deltas[value] for value in x],
            marker=marker,
            linewidth=1.7,
            label=label,
        )
    calibration_axis.plot(
        [0, 1000],
        [0, 1000],
        color="#333333",
        linestyle="--",
        linewidth=1.2,
        label="Ideal: measured delta = configured delay",
    )
    calibration_axis.set_xlabel("Configured injected delay (ns)")
    calibration_axis.set_ylabel("Added latency per successful wrapper op (ns)")
    calibration_axis.set_title("Successful wrapper operations track injection")
    calibration_axis.legend(frameon=False, fontsize=8)

    controls = (
        ("RawUgqmPushPopPingPong", "Raw ugqm push+pop", "o"),
        ("HwSingleEmptyPop", "Empty wrapper pop", "s"),
        ("BlkringPushPopPingPong", "Software ring push+pop", "^"),
    )
    for benchmark, label, marker in controls:
        series = sorted(
            (row for row in rows if row.benchmark == benchmark),
            key=lambda row: row.inject_ns,
        )
        control_axis.plot(
            [row.inject_ns for row in series],
            [row.latency_ns for row in series],
            marker=marker,
            linewidth=1.7,
            label=label,
        )
    control_axis.set_xlabel("Configured injected delay (ns)")
    control_axis.set_ylabel("Measured iteration latency (ns)")
    control_axis.set_title("Control paths remain approximately flat")
    control_axis.legend(frameon=False, fontsize=8)

    for axis in axes:
        _style_axis(axis)
    fig.suptitle(
        "Microbenchmark calibration: delay is visible on successful wrapper operations\n"
        "bm_min_iters=100000 | one run per point"
    )
    return _save_figure(fig, output_dir, "04_microbenchmark_calibration")


def _plot_cpp2_closed_loop_sensitivity(
    rows: Sequence[FixedOutstandingResult], output_dir: Path
) -> list[Path]:
    rows = sorted(rows, key=lambda row: row.inject_ns)
    baseline = rows[0]
    fit = fit_cycle_sensitivity(rows)
    x = [row.multiplier for row in rows]
    fig, axes = plt.subplots(
        2,
        1,
        figsize=(12, 8.5),
        sharex=True,
        gridspec_kw={"height_ratios": (1.25, 0.9)},
        constrained_layout=True,
    )
    latency_axis, normalized_axis = axes

    metrics = (
        ("latency_p50_us", "p50", "p50", "o"),
        ("latency_p99_us", "p99", "p99", "s"),
        ("latency_p999_us", "p99.9", "p999", "^"),
    )
    for field, label, color_key, marker in metrics:
        latency_axis.plot(
            x,
            [getattr(row, field) for row in rows],
            color=COLORS[color_key],
            marker=marker,
            linewidth=1.8,
            label=label,
        )
    latency_axis.plot(
        x,
        [row.implied_cycle_us for row in rows],
        color="#555555",
        linestyle="--",
        linewidth=1.4,
        label="K / QPS implied cycle",
    )
    latency_axis.set_ylabel("Latency / cycle time (us)")
    latency_axis.legend(frameon=False, ncol=4, loc="upper left", fontsize=8)
    latency_axis.text(
        0.99,
        0.07,
        (
            f"Cycle fit: {fit.intercept_us:.1f} us + "
            f"{fit.slope_us_per_injected_us:.2f} us cycle / us injected\n"
            f"R² = {fit.r_squared:.4f}"
        ),
        transform=latency_axis.transAxes,
        ha="right",
        va="bottom",
        fontsize=9,
        color="#444444",
    )

    normalized_axis.plot(
        x,
        [row.qps_retention_pct(baseline) for row in rows],
        color=COLORS["completed"],
        marker="o",
        linewidth=2.0,
        label="Average QPS / baseline",
    )
    normalized_axis.plot(
        x,
        [100.0 * row.latency_p99_us / baseline.latency_p99_us for row in rows],
        color=COLORS["p99"],
        marker="s",
        linewidth=1.8,
        label="p99 / baseline",
    )
    normalized_axis.axhline(100.0, color="#777777", linestyle="--", linewidth=1.0)
    normalized_axis.set_ylabel("Baseline-normalized (%)")
    normalized_axis.legend(frameon=False, loc="upper left")

    for axis in axes:
        _style_axis(axis)
    _set_multiplier_x_axis(normalized_axis, rows)
    fig.suptitle(
        "cpp2 closed loop: smooth cycle-time sensitivity, no open-loop cliff\n"
        "Ubmem | noop | K=100 fixed outstanding | 10s active window | single run"
    )
    return _save_figure(fig, output_dir, "05_cpp2_closed_loop_sensitivity")


def _plot_va_flat_sensitivity(
    rows: Sequence[FixedOutstandingResult], output_dir: Path
) -> list[Path]:
    rows = sorted(rows, key=lambda row: row.inject_ns)
    baseline = rows[0]
    x = [row.multiplier for row in rows]
    fig, axes = plt.subplots(
        3,
        1,
        figsize=(12, 10.5),
        sharex=True,
        gridspec_kw={"height_ratios": (1.2, 0.9, 0.9)},
        constrained_layout=True,
    )
    latency_axis, normalized_axis, exposure_axis = axes

    metrics = (
        ("latency_p50_us", "p50", "p50", "o"),
        ("latency_p99_us", "p99", "p99", "s"),
        ("latency_p999_us", "p99.9 (exploratory)", "p999", "^"),
    )
    for field, label, color_key, marker in metrics:
        latency_axis.plot(
            x,
            [getattr(row, field) for row in rows],
            color=COLORS[color_key],
            marker=marker,
            linewidth=1.8,
            label=label,
        )
    latency_axis.set_yscale("log")
    latency_axis.set_ylabel("RPC latency (us, log scale)")
    latency_axis.legend(frameon=False, ncol=3, loc="upper left", fontsize=8)

    normalized_axis.plot(
        x,
        [row.qps_retention_pct(baseline) for row in rows],
        color=COLORS["completed"],
        marker="o",
        linewidth=2.0,
        label="Average QPS / baseline",
    )
    normalized_axis.plot(
        x,
        [100.0 * row.latency_p99_us / baseline.latency_p99_us for row in rows],
        color=COLORS["p99"],
        marker="s",
        linewidth=1.8,
        label="p99 / baseline",
    )
    normalized_axis.axhline(100.0, color="#777777", linestyle="--", linewidth=1.0)
    normalized_axis.set_yscale("log")
    normalized_axis.set_ylabel("Baseline-normalized (%, log scale)")
    normalized_axis.legend(frameon=False, loc="upper left")

    nonzero_rows = [row for row in rows if row.inject_ns]
    exposure_axis.plot(
        [row.multiplier for row in nonzero_rows],
        [row.effective_exposure(baseline) for row in nonzero_rows],
        color="#7a4e9d",
        marker="D",
        linewidth=1.8,
        label="Δ(K/QPS cycle) / injected delay",
    )
    exposure_axis.set_ylabel("Effective injection exposure")
    exposure_axis.legend(frameon=False, loc="upper left")
    exposure_axis.text(
        0.99,
        0.06,
        "Effective injection exposure is not an operation count",
        transform=exposure_axis.transAxes,
        ha="right",
        va="bottom",
        fontsize=8,
        color="#555555",
    )

    for axis in axes:
        _style_axis(axis)
    _set_multiplier_x_axis(exposure_axis, rows)
    fig.suptitle(
        "VA FLAT Compressed Polling: sensitivity begins at the first delay point\n"
        "echo 1KiB | K=100 fixed outstanding | 10s active window | single run"
    )
    return _save_figure(
        fig, output_dir, "06_va_flat_compressed_polling_sensitivity"
    )


def _plot_fixed_outstanding_comparison(
    closed_loop_rows: Sequence[FixedOutstandingResult],
    va_flat_rows: Sequence[FixedOutstandingResult],
    output_dir: Path,
) -> list[Path]:
    closed_loop_rows = sorted(closed_loop_rows, key=lambda row: row.inject_ns)
    va_flat_rows = sorted(va_flat_rows, key=lambda row: row.inject_ns)
    datasets = (
        (closed_loop_rows, "cpp2 closed loop / noop", COLORS["p50"], "o"),
        (va_flat_rows, "VA FLAT compressed polling / echo 1KiB", "#7a4e9d", "D"),
    )
    fig, axes = plt.subplots(1, 2, figsize=(13.5, 5.9), constrained_layout=True)
    throughput_axis, latency_axis = axes

    for rows, label, color, marker in datasets:
        baseline = rows[0]
        x = [row.multiplier for row in rows]
        throughput_axis.plot(
            x,
            [row.qps_retention_pct(baseline) for row in rows],
            color=color,
            marker=marker,
            linewidth=2.0,
            label=label,
        )
        latency_axis.plot(
            x,
            [row.latency_p99_us / baseline.latency_p99_us for row in rows],
            color=color,
            marker=marker,
            linewidth=2.0,
            label=label,
        )

    throughput_axis.axhline(100.0, color="#777777", linestyle="--", linewidth=1.0)
    throughput_axis.set_ylabel("Average QPS retained (%)")
    throughput_axis.set_title("Capacity sensitivity")
    throughput_axis.legend(frameon=False, fontsize=8)

    latency_axis.axhline(1.0, color="#777777", linestyle="--", linewidth=1.0)
    latency_axis.set_yscale("log")
    latency_axis.set_ylabel("p99 / 0-delay baseline (log scale)")
    latency_axis.set_title("Tail-latency sensitivity")
    latency_axis.legend(frameon=False, fontsize=8)

    for axis in axes:
        _style_axis(axis)
        _set_multiplier_x_axis(axis, closed_loop_rows)
    fig.suptitle(
        "Same delay knob, very different RPC sensitivity\n"
        "Both are K=100 fixed-outstanding, single-run sweeps"
    )
    return _save_figure(fig, output_dir, "07_fixed_outstanding_comparison")


def generate_figures(
    baseline_rows: Sequence[RpcResult],
    delay_rows: Sequence[RpcResult],
    micro_rows: Sequence[MicroResult],
    closed_loop_rows: Sequence[FixedOutstandingResult],
    va_flat_rows: Sequence[FixedOutstandingResult],
    output_dir: Path,
) -> list[Path]:
    outputs: list[Path] = []
    outputs.extend(_plot_rpc_saturation(baseline_rows, output_dir))
    outputs.extend(_plot_delay_sensitivity(delay_rows, output_dir))
    outputs.extend(_plot_delay_capacity(delay_rows, output_dir))
    outputs.extend(_plot_micro_calibration(micro_rows, output_dir))
    outputs.extend(_plot_cpp2_closed_loop_sensitivity(closed_loop_rows, output_dir))
    outputs.extend(_plot_va_flat_sensitivity(va_flat_rows, output_dir))
    outputs.extend(
        _plot_fixed_outstanding_comparison(
            closed_loop_rows, va_flat_rows, output_dir
        )
    )
    return outputs


def main(argv: Sequence[str] | None = None) -> int:
    project_dir = Path(__file__).resolve().parent.parent
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--baseline-input",
        type=Path,
        default=project_dir / "data" / "rpc_baseline_qps_sweep.csv",
    )
    parser.add_argument(
        "--delay-input",
        type=Path,
        default=project_dir / "data" / "gqm_delay_sweep_35000qps.csv",
    )
    parser.add_argument(
        "--micro-input",
        type=Path,
        default=project_dir / "data" / "gqm_microbenchmark.csv",
    )
    parser.add_argument(
        "--closed-loop-input",
        type=Path,
        default=project_dir / "data" / "cpp2_closed_loop_gqm_delay_sweep.csv",
    )
    parser.add_argument(
        "--va-flat-input",
        type=Path,
        default=(
            project_dir
            / "data"
            / "va_flat_compressed_polling_gqm_delay_sweep.csv"
        ),
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=project_dir / "figures",
    )
    args = parser.parse_args(argv)

    baseline_rows = load_rpc_results(args.baseline_input)
    delay_rows = load_rpc_results(args.delay_input)
    micro_rows = load_micro_results(args.micro_input)
    closed_loop_rows = load_fixed_outstanding_results(args.closed_loop_input)
    va_flat_rows = load_fixed_outstanding_results(args.va_flat_input)
    for output in generate_figures(
        baseline_rows,
        delay_rows,
        micro_rows,
        closed_loop_rows,
        va_flat_rows,
        args.output_dir,
    ):
        print(output)
    cliff = latency_cliff_interval(delay_rows)
    if cliff is not None:
        print(f"p50 cliff interval: {cliff[0] / 1000:g}-{cliff[1] / 1000:g} us")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
