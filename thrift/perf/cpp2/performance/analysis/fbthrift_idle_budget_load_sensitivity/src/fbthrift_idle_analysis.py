from __future__ import annotations

import argparse
import csv
from dataclasses import asdict, dataclass, replace
from pathlib import Path
from statistics import fmean

import matplotlib

matplotlib.use("Agg")

import matplotlib.pyplot as plt
from matplotlib.lines import Line2D


TARGET_QPS_VALUES = (1_600, 16_000, 40_000, 80_000, 152_000)
MAX_NO_IDLE_QPS = 160_000
BUD_VALUES = (0, 1, 16, 256)
IDLE_CONFIGS = ((False, 0), (True, 1), (True, 10), (True, 100))
RESOURCE_LABEL = (
    f"fbthrift Ubmem | 100% load = {MAX_NO_IDLE_QPS // 1000}K QPS | "
    "Server: 8 vCPU | Client: 4 vCPU | "
    "Combined quota: 12 vCPU"
)
CPU_LABEL = "Total CPU: summed thread-level process usage across client and server"
LOAD_TITLES = {
    1_600: "1% load",
    16_000: "10% load",
    40_000: "25% load",
    80_000: "50% load",
    152_000: "95% load",
}
BUD_COLORS = {
    0: "#4477AA",
    1: "#228833",
    16: "#AA3377",
    256: "#CCBB44",
}
SLEEP_MARKERS = {1: "o", 10: "s", 100: "^"}
SLEEP_LABELS = {1: "1 µs", 10: "10 µs", 100: "100 µs"}
FRONTIER_LABEL_IDS = {
    1_600: {"ubmem-1600-b1-s1", "ubmem-1600-b1-s10"},
    16_000: {"ubmem-16000-b16-s1", "ubmem-16000-b1-s10"},
    40_000: {
        "ubmem-40000-b1-s10",
        "ubmem-40000-b16-s10",
    },
    80_000: {
        "ubmem-80000-b0-s100",
        "ubmem-80000-b0-s10",
        "ubmem-80000-b16-s10",
    },
    152_000: {"ubmem-152000-b0-s100", "ubmem-152000-b16-s100"},
}
FRONTIER_LABEL_OFFSETS = {
    "ubmem-1600-b1-s1": (-7, -17),
    "ubmem-1600-b1-s10": (7, 10),
    "ubmem-16000-b16-s1": (-7, -17),
    "ubmem-16000-b1-s10": (7, 10),
    "ubmem-40000-b1-s10": (7, -17),
    "ubmem-40000-b16-s10": (7, 10),
    "ubmem-80000-b0-s100": (12, -18),
    "ubmem-80000-b0-s10": (7, -17),
    "ubmem-80000-b16-s10": (-7, 10),
    "ubmem-152000-b0-s100": (7, 10),
    "ubmem-152000-b16-s100": (7, -17),
}
BOUNDARY_ANNOTATION_POSITIONS = {
    1_600: ((0.08, 0.88), (0.25, 0.52), (0.45, 0.85), (0.76, 0.16)),
    16_000: ((0.07, 0.86), (0.31, 0.48), (0.52, 0.84), (0.75, 0.16)),
    40_000: (
        (0.05, 0.88),
        (0.18, 0.52),
        (0.31, 0.86),
        (0.44, 0.46),
        (0.57, 0.82),
        (0.70, 0.38),
        (0.84, 0.76),
    ),
    80_000: ((0.05, 0.88), (0.24, 0.48), (0.43, 0.84), (0.64, 0.44), (0.83, 0.78)),
    152_000: ((0.06, 0.86), (0.38, 0.48), (0.80, 0.80)),
}


@dataclass(frozen=True)
class Measurement:
    source_index: int
    value: str
    target_qps: int
    idle_enabled: bool
    sleep_us: int
    bud: int
    reported_sustain_pct: float
    achieved_qps: float
    p50_us: float
    p99_us: float
    p999_us: float
    max_us: float
    send_lag_p99_us: float
    scheduled: int
    dispatched: int
    completed: int
    shed: int
    client_cpu_user_pct: float
    client_cpu_sys_pct: float
    client_cpu_pct: float
    server_cpu_user_pct: float
    server_cpu_sys_pct: float
    server_cpu_pct: float
    client_rss_kb: int
    server_rss_kb: int
    retry: int
    status: str


@dataclass(frozen=True)
class DerivedMeasurement(Measurement):
    total_cpu_pct: float
    used_cores: float
    quota_occupancy_pct: float
    qps_per_used_core: float
    target_sustained: bool


@dataclass(frozen=True)
class SocketMeasurement:
    source_index: int
    value: str
    target_qps: int
    reported_sustain_pct: float
    achieved_qps: float
    p50_us: float
    p99_us: float
    p999_us: float
    max_us: float
    send_lag_p99_us: float
    scheduled: int
    dispatched: int
    completed: int
    shed: int
    client_cpu_user_pct: float
    client_cpu_sys_pct: float
    client_cpu_pct: float
    server_cpu_user_pct: float
    server_cpu_sys_pct: float
    server_cpu_pct: float
    client_rss_kb: int
    server_rss_kb: int
    retry: int
    status: str
    total_cpu_pct: float
    used_cores: float
    quota_occupancy_pct: float
    qps_per_used_core: float
    target_sustained: bool


@dataclass(frozen=True)
class TradeoffCandidate:
    candidate_id: str
    transport: str
    policy: str
    target_qps: int
    idle_enabled: bool | None
    sleep_us: int | None
    bud: int | None
    source_indices: str
    repeat_count: int
    repeat_gate_consistent: bool
    achieved_qps: float
    achieved_qps_min: float
    achieved_qps_max: float
    reported_sustain_pct: float
    reported_sustain_pct_min: float
    reported_sustain_pct_max: float
    p99_us: float
    p99_us_min: float
    p99_us_max: float
    p999_us: float
    total_cpu_pct: float
    total_cpu_pct_min: float
    total_cpu_pct_max: float
    used_cores: float
    used_cores_min: float
    used_cores_max: float
    target_sustained: bool
    is_pareto: bool


@dataclass(frozen=True)
class LoadSummary:
    target_qps: int
    total_cells: int
    sustained_cells: int
    max_achieved_qps: float
    max_reported_sustain_pct: float
    min_p99_us: float
    max_p99_us: float
    min_used_cores: float
    max_used_cores: float


@dataclass(frozen=True)
class PolicyBoundary:
    target_qps: int
    min_p99_budget_us: float
    max_p99_budget_us: float | None
    candidate_id: str
    transport: str
    policy: str
    sleep_us: int | None
    bud: int | None
    repeat_count: int
    p99_us: float
    used_cores: float
    total_cpu_pct: float
    quota_occupancy_pct: float


def _read_common(raw: dict[str, str]) -> dict[str, object]:
    return {
        "source_index": int(raw["source_index"]),
        "value": raw["value"],
        "target_qps": int(raw["target_qps"]),
        "reported_sustain_pct": float(raw["reported_sustain_pct"]),
        "achieved_qps": float(raw["achieved_qps"]),
        "p50_us": float(raw["p50_us"]),
        "p99_us": float(raw["p99_us"]),
        "p999_us": float(raw["p999_us"]),
        "max_us": float(raw["max_us"]),
        "send_lag_p99_us": float(raw["send_lag_p99_us"]),
        "scheduled": int(raw["scheduled"]),
        "dispatched": int(raw["dispatched"]),
        "completed": int(raw["completed"]),
        "shed": int(raw["shed"]),
        "client_cpu_user_pct": float(raw["client_cpu_user_pct"]),
        "client_cpu_sys_pct": float(raw["client_cpu_sys_pct"]),
        "client_cpu_pct": float(raw["client_cpu_pct"]),
        "server_cpu_user_pct": float(raw["server_cpu_user_pct"]),
        "server_cpu_sys_pct": float(raw["server_cpu_sys_pct"]),
        "server_cpu_pct": float(raw["server_cpu_pct"]),
        "client_rss_kb": int(raw["client_rss_kb"]),
        "server_rss_kb": int(raw["server_rss_kb"]),
        "retry": int(raw["retry"]),
        "status": raw["status"],
    }


def load_measurements(path: Path) -> list[Measurement]:
    rows: list[Measurement] = []
    with path.open(newline="", encoding="utf-8") as source:
        for raw in csv.DictReader(source):
            rows.append(
                Measurement(
                    **_read_common(raw),
                    idle_enabled=raw["idle_enabled"].lower() == "true",
                    sleep_us=int(raw["sleep_us"]),
                    bud=int(raw["bud"]),
                )
            )

    keys = {
        (row.target_qps, row.idle_enabled, row.sleep_us, row.bud) for row in rows
    }
    expected = {
        (target_qps, idle_enabled, sleep_us, bud)
        for target_qps in TARGET_QPS_VALUES
        for idle_enabled, sleep_us in IDLE_CONFIGS
        for bud in BUD_VALUES
    }
    if len(rows) != len(keys) or keys != expected:
        raise ValueError(
            "incomplete or duplicate matrix: "
            f"rows={len(rows)}, unique={len(keys)}, "
            f"missing={sorted(expected - keys)}, extras={sorted(keys - expected)}"
        )
    return rows


def load_socket_measurements(path: Path) -> list[SocketMeasurement]:
    rows: list[SocketMeasurement] = []
    with path.open(newline="", encoding="utf-8") as source:
        for raw in csv.DictReader(source):
            common = _read_common(raw)
            total_cpu_pct = float(common["client_cpu_pct"]) + float(
                common["server_cpu_pct"]
            )
            achieved_qps = float(common["achieved_qps"])
            used_cores = total_cpu_pct / 100.0
            rows.append(
                SocketMeasurement(
                    **common,
                    total_cpu_pct=total_cpu_pct,
                    used_cores=used_cores,
                    quota_occupancy_pct=used_cores / 12.0 * 100.0,
                    qps_per_used_core=achieved_qps / used_cores,
                    target_sustained=float(common["reported_sustain_pct"]) >= 99.5,
                )
            )

    if [row.target_qps for row in rows] != list(TARGET_QPS_VALUES):
        raise ValueError("Socket anchors must contain one ordered row per target QPS")
    return rows


def derive_measurements(rows: list[Measurement]) -> list[DerivedMeasurement]:
    derived: list[DerivedMeasurement] = []
    for row in rows:
        total_cpu_pct = row.client_cpu_pct + row.server_cpu_pct
        used_cores = total_cpu_pct / 100.0
        derived.append(
            DerivedMeasurement(
                **asdict(row),
                total_cpu_pct=total_cpu_pct,
                used_cores=used_cores,
                quota_occupancy_pct=used_cores / 12.0 * 100.0,
                qps_per_used_core=row.achieved_qps / used_cores,
                target_sustained=row.reported_sustain_pct >= 99.5,
            )
        )
    return derived


def _candidate_from_ubmem(
    rows: list[DerivedMeasurement], policy: str
) -> TradeoffCandidate:
    if not rows:
        raise ValueError("candidate requires at least one measurement")
    target_qps = rows[0].target_qps
    if any(row.target_qps != target_qps for row in rows):
        raise ValueError("candidate rows must share target QPS")
    gates = [row.target_sustained for row in rows]
    idle_enabled = rows[0].idle_enabled
    sleep_us = rows[0].sleep_us if idle_enabled else 0
    bud = rows[0].bud if len(rows) == 1 else None
    return TradeoffCandidate(
        candidate_id=(
            f"ubmem-{target_qps}-polling"
            if not idle_enabled
            else f"ubmem-{target_qps}-b{bud}-s{sleep_us}"
        ),
        transport="Ubmem",
        policy=policy,
        target_qps=target_qps,
        idle_enabled=idle_enabled,
        sleep_us=sleep_us,
        bud=bud,
        source_indices=",".join(str(row.source_index) for row in rows),
        repeat_count=len(rows),
        repeat_gate_consistent=len(set(gates)) == 1,
        achieved_qps=fmean(row.achieved_qps for row in rows),
        achieved_qps_min=min(row.achieved_qps for row in rows),
        achieved_qps_max=max(row.achieved_qps for row in rows),
        reported_sustain_pct=fmean(row.reported_sustain_pct for row in rows),
        reported_sustain_pct_min=min(row.reported_sustain_pct for row in rows),
        reported_sustain_pct_max=max(row.reported_sustain_pct for row in rows),
        p99_us=fmean(row.p99_us for row in rows),
        p99_us_min=min(row.p99_us for row in rows),
        p99_us_max=max(row.p99_us for row in rows),
        p999_us=fmean(row.p999_us for row in rows),
        total_cpu_pct=fmean(row.total_cpu_pct for row in rows),
        total_cpu_pct_min=min(row.total_cpu_pct for row in rows),
        total_cpu_pct_max=max(row.total_cpu_pct for row in rows),
        used_cores=fmean(row.used_cores for row in rows),
        used_cores_min=min(row.used_cores for row in rows),
        used_cores_max=max(row.used_cores for row in rows),
        target_sustained=all(gates),
        is_pareto=False,
    )


def _candidate_from_socket(row: SocketMeasurement) -> TradeoffCandidate:
    return TradeoffCandidate(
        candidate_id=f"socket-{row.target_qps}",
        transport="Socket",
        policy="Socket",
        target_qps=row.target_qps,
        idle_enabled=None,
        sleep_us=None,
        bud=None,
        source_indices=str(row.source_index),
        repeat_count=1,
        repeat_gate_consistent=True,
        achieved_qps=row.achieved_qps,
        achieved_qps_min=row.achieved_qps,
        achieved_qps_max=row.achieved_qps,
        reported_sustain_pct=row.reported_sustain_pct,
        reported_sustain_pct_min=row.reported_sustain_pct,
        reported_sustain_pct_max=row.reported_sustain_pct,
        p99_us=row.p99_us,
        p99_us_min=row.p99_us,
        p99_us_max=row.p99_us,
        p999_us=row.p999_us,
        total_cpu_pct=row.total_cpu_pct,
        total_cpu_pct_min=row.total_cpu_pct,
        total_cpu_pct_max=row.total_cpu_pct,
        used_cores=row.used_cores,
        used_cores_min=row.used_cores,
        used_cores_max=row.used_cores,
        target_sustained=row.target_sustained,
        is_pareto=False,
    )


def _mark_pareto(candidates: list[TradeoffCandidate]) -> list[TradeoffCandidate]:
    marked: list[TradeoffCandidate] = []
    for candidate in candidates:
        eligible = [
            other
            for other in candidates
            if other.target_qps == candidate.target_qps and other.target_sustained
        ]
        dominated = any(
            other.candidate_id != candidate.candidate_id
            and other.used_cores <= candidate.used_cores
            and other.p99_us <= candidate.p99_us
            and (
                other.used_cores < candidate.used_cores
                or other.p99_us < candidate.p99_us
            )
            for other in eligible
        )
        marked.append(
            replace(
                candidate,
                is_pareto=candidate.target_sustained and not dominated,
            )
        )
    return marked


def build_tradeoff_candidates(
    rows: list[DerivedMeasurement], socket_rows: list[SocketMeasurement]
) -> list[TradeoffCandidate]:
    candidates: list[TradeoffCandidate] = []
    for target_qps in TARGET_QPS_VALUES:
        load_rows = [row for row in rows if row.target_qps == target_qps]
        baseline = sorted(
            (row for row in load_rows if not row.idle_enabled),
            key=lambda row: row.bud,
        )
        candidates.append(_candidate_from_ubmem(baseline, "Polling baseline"))
        for row in sorted(
            (row for row in load_rows if row.idle_enabled),
            key=lambda row: (row.sleep_us, row.bud),
        ):
            candidates.append(_candidate_from_ubmem([row], f"BUD={row.bud}"))
        candidates.extend(
            _candidate_from_socket(row)
            for row in socket_rows
            if row.target_qps == target_qps
        )
    return _mark_pareto(candidates)


def summarize_by_load(rows: list[DerivedMeasurement]) -> list[LoadSummary]:
    summaries: list[LoadSummary] = []
    for target_qps in TARGET_QPS_VALUES:
        load_rows = [row for row in rows if row.target_qps == target_qps]
        summaries.append(
            LoadSummary(
                target_qps=target_qps,
                total_cells=len(load_rows),
                sustained_cells=sum(row.target_sustained for row in load_rows),
                max_achieved_qps=max(row.achieved_qps for row in load_rows),
                max_reported_sustain_pct=max(
                    row.reported_sustain_pct for row in load_rows
                ),
                min_p99_us=min(row.p99_us for row in load_rows),
                max_p99_us=max(row.p99_us for row in load_rows),
                min_used_cores=min(row.used_cores for row in load_rows),
                max_used_cores=max(row.used_cores for row in load_rows),
            )
        )
    return summaries


def build_policy_boundaries(
    candidates: list[TradeoffCandidate],
) -> list[PolicyBoundary]:
    boundaries: list[PolicyBoundary] = []
    for target_qps in TARGET_QPS_VALUES:
        eligible = [
            row
            for row in candidates
            if row.target_qps == target_qps and row.target_sustained
        ]
        selected: list[TradeoffCandidate] = []
        thresholds: list[float] = []
        for threshold in sorted({row.p99_us for row in eligible}):
            feasible = [row for row in eligible if row.p99_us <= threshold]
            best = min(
                feasible,
                key=lambda row: (row.used_cores, row.p99_us, row.candidate_id),
            )
            if not selected or selected[-1].candidate_id != best.candidate_id:
                selected.append(best)
                thresholds.append(threshold)

        for index, (threshold, best) in enumerate(zip(thresholds, selected)):
            boundaries.append(
                PolicyBoundary(
                    target_qps=target_qps,
                    min_p99_budget_us=threshold,
                    max_p99_budget_us=(
                        thresholds[index + 1] if index + 1 < len(thresholds) else None
                    ),
                    candidate_id=best.candidate_id,
                    transport=best.transport,
                    policy=best.policy,
                    sleep_us=best.sleep_us,
                    bud=best.bud,
                    repeat_count=best.repeat_count,
                    p99_us=best.p99_us,
                    used_cores=best.used_cores,
                    total_cpu_pct=best.total_cpu_pct,
                    quota_occupancy_pct=best.used_cores / 12.0 * 100.0,
                )
            )
    return boundaries


def _write_dataclass_csv(rows: list[object], path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if not rows:
        raise ValueError(f"refusing to write empty CSV: {path}")
    def serialize(value: object) -> object:
        if value is None:
            return ""
        if isinstance(value, float):
            return f"{value:.12g}"
        return value

    dictionaries = [
        {key: serialize(value) for key, value in asdict(row).items()}
        for row in rows
    ]
    with path.open("w", newline="", encoding="utf-8") as output:
        writer = csv.DictWriter(
            output,
            fieldnames=list(dictionaries[0]),
            lineterminator="\n",
        )
        writer.writeheader()
        writer.writerows(dictionaries)


def write_derived_csv(rows: list[DerivedMeasurement], path: Path) -> None:
    _write_dataclass_csv(rows, path)


def write_summary_csv(rows: list[LoadSummary], path: Path) -> None:
    _write_dataclass_csv(rows, path)


def write_tradeoff_candidates_csv(
    rows: list[TradeoffCandidate], path: Path
) -> None:
    _write_dataclass_csv(rows, path)


def write_policy_boundaries_csv(rows: list[PolicyBoundary], path: Path) -> None:
    _write_dataclass_csv(rows, path)


def _academic_style() -> None:
    plt.rcParams.update(
        {
            "font.family": "DejaVu Sans",
            "font.size": 9.2,
            "axes.titlesize": 10.5,
            "axes.labelsize": 9.6,
            "legend.fontsize": 8.0,
            "xtick.labelsize": 8.4,
            "ytick.labelsize": 8.4,
            "axes.spines.top": False,
            "axes.spines.right": False,
            "axes.linewidth": 0.8,
            "grid.color": "#C8C8C8",
            "grid.linewidth": 0.55,
            "grid.alpha": 0.42,
            "svg.fonttype": "none",
        }
    )


def _save_figure(figure: plt.Figure, output_dir: Path, stem: str) -> list[Path]:
    output_dir.mkdir(parents=True, exist_ok=True)
    outputs: list[Path] = []
    for suffix in ("png", "svg"):
        path = output_dir / f"{stem}.{suffix}"
        figure.savefig(path, dpi=240, bbox_inches="tight", facecolor="white")
        if suffix == "svg":
            text = path.read_text(encoding="utf-8")
            path.write_text(
                "\n".join(line.rstrip() for line in text.splitlines()) + "\n",
                encoding="utf-8",
            )
        outputs.append(path)
    plt.close(figure)
    return outputs


def _format_latency(value_us: float) -> str:
    if value_us >= 1_000:
        return f"{value_us / 1_000:.2f} ms"
    return f"{value_us:.1f} µs" if value_us % 1 else f"{value_us:.0f} µs"


def _policy_label(row: TradeoffCandidate | PolicyBoundary) -> str:
    if row.transport == "Socket":
        return "Socket"
    if row.policy == "Polling baseline":
        return "Polling"
    assert row.bud is not None and row.sleep_us is not None
    return f"B{row.bud}/S{SLEEP_LABELS[row.sleep_us]}"


def _add_heading(figure: plt.Figure, title: str) -> None:
    figure.suptitle(title, fontsize=15, fontweight="bold", y=0.995)
    figure.text(
        0.5,
        0.958,
        f"{RESOURCE_LABEL}\n{CPU_LABEL}",
        ha="center",
        va="top",
        fontsize=9.0,
        linespacing=1.35,
    )


def _reserve_caption_space(figure: plt.Figure) -> None:
    layout_engine = figure.get_layout_engine()
    if layout_engine is not None:
        layout_engine.set(rect=(0.0, 0.075, 1.0, 0.825), h_pad=0.12, w_pad=0.10)


def _add_quota_axis(axis: plt.Axes) -> None:
    quota_axis = axis.secondary_xaxis(
        "top",
        functions=(
            lambda cores: cores / 12.0 * 100.0,
            lambda occupancy: occupancy / 100.0 * 12.0,
        ),
    )
    quota_axis.set_xlabel("Combined quota occupancy (%)", labelpad=3)
    quota_axis.tick_params(labelsize=7.6, pad=1.5)


def _plot_cpu_p99_tradeoff(
    candidates: list[TradeoffCandidate], output_dir: Path
) -> list[Path]:
    figure, axes = plt.subplots(
        2, 3, figsize=(15.6, 9.1), constrained_layout=True
    )
    for axis, target_qps in zip(axes.flat[:5], TARGET_QPS_VALUES):
        rows = [row for row in candidates if row.target_qps == target_qps]
        ubmem = [row for row in rows if row.transport == "Ubmem"]
        for row in ubmem:
            if not row.target_sustained:
                axis.scatter(
                    row.used_cores,
                    row.p99_us,
                    marker="x",
                    s=31,
                    color="#AAAAAA",
                    linewidth=0.9,
                    alpha=0.72,
                    zorder=1,
                )
                continue
            if row.policy == "Polling baseline":
                axis.errorbar(
                    row.used_cores,
                    row.p99_us,
                    xerr=[
                        [row.used_cores - row.used_cores_min],
                        [row.used_cores_max - row.used_cores],
                    ],
                    yerr=[
                        [row.p99_us - row.p99_us_min],
                        [row.p99_us_max - row.p99_us],
                    ],
                    fmt="D",
                    markersize=6.0,
                    markerfacecolor="#222222",
                    markeredgecolor="#222222",
                    ecolor="#444444",
                    elinewidth=0.9,
                    capsize=2.2,
                    zorder=5,
                )
            else:
                assert row.bud is not None and row.sleep_us is not None
                axis.scatter(
                    row.used_cores,
                    row.p99_us,
                    marker=SLEEP_MARKERS[row.sleep_us],
                    s=51,
                    facecolor=BUD_COLORS[row.bud],
                    edgecolor="#222222",
                    linewidth=0.7,
                    alpha=0.92,
                    zorder=3,
                )

        socket = next(row for row in rows if row.transport == "Socket")
        socket_color = "#B2182B" if socket.target_sustained else "#777777"
        axis.axvline(
            socket.used_cores,
            color=socket_color,
            linewidth=0.8,
            linestyle=(0, (3, 3)),
            alpha=0.62,
            zorder=0,
        )
        axis.axhline(
            socket.p99_us,
            color=socket_color,
            linewidth=0.8,
            linestyle=(0, (3, 3)),
            alpha=0.62,
            zorder=0,
        )
        axis.scatter(
            socket.used_cores,
            socket.p99_us,
            marker="*",
            s=158,
            facecolor=socket_color if socket.target_sustained else "white",
            edgecolor=socket_color,
            linewidth=1.25,
            zorder=7,
        )
        socket_offset = (7, 10)
        if target_qps in (1_600, 152_000):
            socket_offset = (7, -17)
        axis.annotate(
            f"Socket: {socket.used_cores:.3f} cores, "
            f"{_format_latency(socket.p99_us)}, "
            f"{socket.reported_sustain_pct:.1f}%",
            (socket.used_cores, socket.p99_us),
            xytext=socket_offset,
            textcoords="offset points",
            fontsize=7.2,
            color=socket_color,
            ha="left",
            va="center",
            bbox={"facecolor": "white", "edgecolor": "none", "alpha": 0.84, "pad": 0.15},
            zorder=8,
        )

        frontier = sorted(
            (row for row in rows if row.is_pareto), key=lambda row: row.used_cores
        )
        axis.plot(
            [row.used_cores for row in frontier],
            [row.p99_us for row in frontier],
            color="#333333",
            linewidth=1.15,
            linestyle="--",
            zorder=2,
        )
        labeled_frontier = [
            row
            for row in frontier
            if row.transport == "Ubmem"
            and row.candidate_id in FRONTIER_LABEL_IDS[target_qps]
        ]
        for row in labeled_frontier:
            x_offset, y_offset = FRONTIER_LABEL_OFFSETS[row.candidate_id]
            align_right = x_offset < 0
            axis.annotate(
                _policy_label(row),
                (row.used_cores, row.p99_us),
                xytext=(x_offset, y_offset),
                textcoords="offset points",
                fontsize=7.1,
                color="#222222",
                ha="right" if align_right else "left",
                va="center",
                bbox={"facecolor": "white", "edgecolor": "none", "alpha": 0.78, "pad": 0.12},
            )

        axis.set_title(LOAD_TITLES[target_qps], fontweight="bold")
        axis.set_xlabel("Total CPU (vCPU-equivalents)")
        axis.set_ylabel("P99 latency (µs, log scale)")
        axis.set_yscale("log")
        axis.grid(True, which="both")
        axis.set_xlim(0, max(row.used_cores for row in rows) * 1.16)
        axis.margins(y=0.20)
        _add_quota_axis(axis)

    legend_axis = axes.flat[5]
    legend_axis.axis("off")
    bud_handles = [
        Line2D(
            [0],
            [0],
            marker="o",
            linestyle="none",
            markerfacecolor=color,
            markeredgecolor=color,
            label=f"BUD={bud}",
        )
        for bud, color in BUD_COLORS.items()
    ]
    sleep_handles = [
        Line2D(
            [0],
            [0],
            marker=marker,
            linestyle="none",
            markerfacecolor="#777777",
            markeredgecolor="#777777",
            label=SLEEP_LABELS[sleep],
        )
        for sleep, marker in SLEEP_MARKERS.items()
    ]
    state_handles = [
        Line2D([0], [0], marker="D", linestyle="none", color="#222222", label="Polling baseline (4-run mean ± min–max)"),
        Line2D([0], [0], marker="x", linestyle="none", color="#AAAAAA", label="Target not sustained"),
        Line2D([0], [0], color="#333333", linestyle="--", label="Eligible Pareto frontier"),
        Line2D([0], [0], marker="*", markersize=10, linestyle="none", color="#B2182B", label="Socket anchor (gate passed)"),
        Line2D([0], [0], marker="*", markersize=10, linestyle="none", markerfacecolor="white", markeredgecolor="#777777", label="Socket anchor (gate failed)"),
    ]
    first = legend_axis.legend(
        handles=bud_handles,
        title="Idle BUD (color)",
        loc="upper left",
        bbox_to_anchor=(0.0, 1.0),
        frameon=False,
        ncol=2,
    )
    legend_axis.add_artist(first)
    second = legend_axis.legend(
        handles=sleep_handles,
        title="Configured Sleep (marker)",
        loc="upper left",
        bbox_to_anchor=(0.0, 0.68),
        frameon=False,
        ncol=2,
    )
    legend_axis.add_artist(second)
    legend_axis.legend(
        handles=state_handles,
        title="Baseline, gate, and anchor",
        loc="upper left",
        bbox_to_anchor=(0.0, 0.38),
        frameon=False,
    )
    _add_heading(figure, "fbthrift Ubmem Idle CPU–P99 Trade-off")
    _reserve_caption_space(figure)
    figure.text(
        0.5,
        0.014,
        "Eligible frontier requires reported Sustain ≥ 99.5%. Polling baseline whiskers are four-run "
        "min–max ranges, not confidence intervals. Socket is a system-level anchor, not a GQM IRQ A/B.",
        ha="center",
        fontsize=8.7,
        color="#444444",
    )
    return _save_figure(figure, output_dir, "01_fbthrift_cpu_p99_tradeoff")


def _plot_p99_budget_boundary(
    candidates: list[TradeoffCandidate],
    boundaries: list[PolicyBoundary],
    output_dir: Path,
) -> list[Path]:
    figure, axes = plt.subplots(
        5, 1, figsize=(14.8, 12.2), constrained_layout=True
    )
    for axis, target_qps in zip(axes, TARGET_QPS_VALUES):
        load = [row for row in boundaries if row.target_qps == target_qps]
        axis.set_title(LOAD_TITLES[target_qps], fontweight="bold", loc="left")
        display_max = max(
            row.p99_us
            for row in candidates
            if row.target_qps == target_qps and row.target_sustained
        ) * 1.18
        x_values = [row.min_p99_budget_us for row in load] + [display_max]
        y_values = [row.used_cores for row in load] + [load[-1].used_cores]
        axis.step(
            x_values,
            y_values,
            where="post",
            color="#333333",
            linewidth=1.65,
            zorder=2,
        )
        annotation_positions = BOUNDARY_ANNOTATION_POSITIONS[target_qps]
        if len(annotation_positions) != len(load):
            raise ValueError(
                f"boundary annotation layout mismatch for {target_qps}: "
                f"positions={len(annotation_positions)}, boundaries={len(load)}"
            )
        for index, row in enumerate(load):
            is_socket = row.transport == "Socket"
            axis.scatter(
                row.min_p99_budget_us,
                row.used_cores,
                marker="*" if is_socket else "o",
                s=108 if is_socket else 43,
                color="#B2182B" if is_socket else "#4477AA",
                edgecolor="#222222",
                linewidth=0.7,
                zorder=4 if is_socket else 3,
            )
            axis.annotate(
                f"≥{_format_latency(row.min_p99_budget_us)}\n"
                f"{_policy_label(row)}; {row.used_cores:.3f} cores",
                (row.min_p99_budget_us, row.used_cores),
                xytext=annotation_positions[index],
                textcoords="axes fraction",
                ha="center",
                va="center",
                fontsize=7.2,
                color="#222222",
                arrowprops={"arrowstyle": "-", "color": "#777777", "linewidth": 0.5},
                bbox={"facecolor": "white", "edgecolor": "none", "alpha": 0.84, "pad": 0.12},
            )
        axis.set_xscale("log")
        axis.set_xlim(load[0].min_p99_budget_us * 0.90, display_max)
        axis.set_ylim(0, max(row.used_cores for row in load) * 1.28)
        axis.set_xlabel("Allowed P99 budget (µs, log scale)")
        axis.set_ylabel("Minimum Total CPU\n(vCPU-equivalents)")
        axis.grid(True, which="both")
    _add_heading(figure, "fbthrift Minimum CPU Required by P99 SLO")
    _reserve_caption_space(figure)
    figure.text(
        0.5,
        0.018,
        "Selection rule: minimize Total CPU subject to reported Sustain ≥ 99.5% and measured P99 ≤ budget.",
        ha="center",
        fontsize=8.7,
        color="#444444",
    )
    figure.text(
        0.5,
        0.005,
        "Each step begins at an observed candidate; Socket is a system-level anchor, not a GQM IRQ measurement.",
        ha="center",
        fontsize=8.7,
        color="#444444",
    )
    return _save_figure(
        figure, output_dir, "02_fbthrift_p99_budget_minimum_cpu_boundary"
    )


def generate_figures(
    candidates: list[TradeoffCandidate],
    boundaries: list[PolicyBoundary],
    output_dir: Path,
) -> list[Path]:
    _academic_style()
    outputs: list[Path] = []
    outputs.extend(_plot_cpu_p99_tradeoff(candidates, output_dir))
    outputs.extend(_plot_p99_budget_boundary(candidates, boundaries, output_dir))
    return outputs


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Analyze fbthrift Ubmem idle budget load sensitivity"
    )
    parser.add_argument("--data", type=Path, required=True)
    parser.add_argument("--socket-data", type=Path, required=True)
    parser.add_argument("--derived-output", type=Path, required=True)
    parser.add_argument("--summary-output", type=Path, required=True)
    parser.add_argument("--candidate-output", type=Path, required=True)
    parser.add_argument("--policy-boundary-output", type=Path, required=True)
    parser.add_argument("--figure-dir", type=Path, required=True)
    return parser.parse_args()


def main() -> None:
    args = _parse_args()
    derived = derive_measurements(load_measurements(args.data))
    candidates = build_tradeoff_candidates(
        derived, load_socket_measurements(args.socket_data)
    )
    write_derived_csv(derived, args.derived_output)
    write_summary_csv(summarize_by_load(derived), args.summary_output)
    write_tradeoff_candidates_csv(candidates, args.candidate_output)
    write_policy_boundaries_csv(
        build_policy_boundaries(candidates), args.policy_boundary_output
    )
    generate_figures(candidates, build_policy_boundaries(candidates), args.figure_dir)


if __name__ == "__main__":
    main()
