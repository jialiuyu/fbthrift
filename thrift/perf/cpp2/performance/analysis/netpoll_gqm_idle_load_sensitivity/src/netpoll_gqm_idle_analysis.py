from __future__ import annotations

import argparse
import csv
import math
from dataclasses import asdict, dataclass, replace
from pathlib import Path
from typing import Sequence

import matplotlib

matplotlib.use("Agg")

import matplotlib.pyplot as plt
from matplotlib.lines import Line2D


TARGET_QPS_VALUES = (7_700, 77_000, 192_500, 385_000, 731_500)
BUD_VALUES = (0, 1, 16, 256, 1024)
SLEEP_US_VALUES = (1, 10, 100, 1000, 10_000)
PAYLOAD_LABEL = "Payload: 1 KiB (1024 B)"
RESOURCE_LABEL = (
    "S/C symmetric BUD + Sleep | Client container: 8 vCPU | "
    "Server container: 8 vCPU | Combined quota: 16 vCPU"
)
WORKLOAD_LABEL = f"{PAYLOAD_LABEL} | Concurrency: 128"
LOAD_TITLES = {
    7_700: "7.7K QPS",
    77_000: "77K QPS",
    192_500: "192.5K QPS",
    385_000: "385K QPS",
    731_500: "731.5K QPS",
}
BUD_COLORS = {
    1: "#66CCEE",
    16: "#228833",
    1024: "#AA3377",
}
POLICY_COLORS = {
    "B0/256 duplicate": "#4477AA",
    "B1": BUD_COLORS[1],
    "B16": BUD_COLORS[16],
    "B1024": BUD_COLORS[1024],
}
SLEEP_MARKERS = {1: "o", 10: "s", 100: "^", 1000: "D", 10_000: "P"}
SLEEP_LABELS = {1: "1 µs", 10: "10 µs", 100: "100 µs", 1000: "1 ms", 10_000: "10 ms"}


@dataclass(frozen=True)
class Measurement:
    source_index: int
    value: str
    target_qps: int
    bud: int
    sleep_us: int
    concurrency: int
    body_bytes: int
    cost_reported: int
    tps: float
    tp99_us: float | None
    tp999_us: float | None
    client_cpu_pct: float | None
    server_cpu_pct: float | None
    client_rss_kb: int | None
    server_rss_kb: int | None
    retry: int | None
    status: str | None
    partial: bool


@dataclass(frozen=True)
class DerivedMeasurement:
    source_index: int
    value: str
    target_qps: int
    bud: int
    sleep_us: int
    concurrency: int
    body_bytes: int
    cost_reported: int
    tps: float
    tp99_us: float | None
    tp999_us: float | None
    client_cpu_pct: float | None
    server_cpu_pct: float | None
    total_cpu_pct: float | None
    used_cores: float | None
    qps_per_used_core: float | None
    attainment_pct: float
    target_sustained: bool
    is_pareto: bool
    client_rss_kb: int | None
    server_rss_kb: int | None
    retry: int | None
    status: str | None
    partial: bool


@dataclass(frozen=True)
class SocketMeasurement:
    source_index: int
    target_qps: int
    concurrency: int
    body_bytes: int
    cost_reported: int | None
    tps: float
    tp99_us: float
    tp999_us: float
    client_cpu_pct: float
    server_cpu_pct: float
    total_cpu_pct: float
    used_cores: float
    attainment_pct: float
    target_sustained: bool
    client_rss_kb: int
    server_rss_kb: int
    retry: int
    status: str


@dataclass(frozen=True)
class TradeoffCandidate:
    candidate_id: str
    transport: str
    policy: str
    target_qps: int
    sleep_us: int | None
    source_indices: str
    repeat_count: int
    repeat_gate_consistent: bool
    tps: float
    tps_min: float
    tps_max: float
    tp99_us: float | None
    tp99_us_min: float | None
    tp99_us_max: float | None
    tp999_us: float | None
    total_cpu_pct: float | None
    total_cpu_pct_min: float | None
    total_cpu_pct_max: float | None
    used_cores: float | None
    used_cores_min: float | None
    used_cores_max: float | None
    attainment_pct: float
    attainment_pct_min: float
    attainment_pct_max: float
    target_sustained: bool
    is_pareto: bool
    partial: bool


@dataclass(frozen=True)
class LoadSummary:
    target_qps: int
    load_class: str
    total_cells: int
    complete_cells: int
    partial_cells: int
    sustained_cells: int
    pareto_cells: int
    max_tps: float
    max_attainment_pct: float
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
    repeat_count: int
    used_cores: float
    total_cpu_pct: float
    quota_occupancy_pct: float


def _optional_float(value: str) -> float | None:
    return float(value) if value else None


def _optional_int(value: str) -> int | None:
    return int(value) if value else None


def load_measurements(path: Path) -> list[Measurement]:
    rows: list[Measurement] = []
    with path.open(newline="", encoding="utf-8") as source:
        for raw in csv.DictReader(source):
            rows.append(
                Measurement(
                    source_index=int(raw["source_index"]),
                    value=raw["value"],
                    target_qps=int(raw["target_qps"]),
                    bud=int(raw["bud"]),
                    sleep_us=int(raw["sleep_us"]),
                    concurrency=int(raw["concurrency"]),
                    body_bytes=int(raw["body_bytes"]),
                    cost_reported=int(raw["cost_reported"]),
                    tps=float(raw["tps"]),
                    tp99_us=_optional_float(raw["tp99_us"]),
                    tp999_us=_optional_float(raw["tp999_us"]),
                    client_cpu_pct=_optional_float(raw["client_cpu_pct"]),
                    server_cpu_pct=_optional_float(raw["server_cpu_pct"]),
                    client_rss_kb=_optional_int(raw["client_rss_kb"]),
                    server_rss_kb=_optional_int(raw["server_rss_kb"]),
                    retry=_optional_int(raw["retry"]),
                    status=raw["status"] or None,
                    partial=raw["partial"].lower() == "true",
                )
            )

    keys = {(row.target_qps, row.bud, row.sleep_us) for row in rows}
    expected = {
        (target_qps, bud, sleep_us)
        for target_qps in TARGET_QPS_VALUES
        for bud in BUD_VALUES
        for sleep_us in SLEEP_US_VALUES
    }
    if len(rows) != len(keys) or keys != expected:
        missing = sorted(expected - keys)
        extras = sorted(keys - expected)
        raise ValueError(
            "incomplete or duplicate matrix: "
            f"rows={len(rows)}, unique={len(keys)}, missing={missing}, extras={extras}"
        )
    return rows


def load_socket_measurements(path: Path) -> list[SocketMeasurement]:
    rows: list[SocketMeasurement] = []
    with path.open(newline="", encoding="utf-8") as source:
        for raw in csv.DictReader(source):
            client_cpu = float(raw["client_cpu_pct"])
            server_cpu = float(raw["server_cpu_pct"])
            total_cpu = client_cpu + server_cpu
            target_qps = int(raw["target_qps"])
            tps = float(raw["tps"])
            attainment = 100.0 * tps / target_qps
            rows.append(
                SocketMeasurement(
                    source_index=int(raw["source_index"]),
                    target_qps=target_qps,
                    concurrency=int(raw["concurrency"]),
                    body_bytes=int(raw["body_bytes"]),
                    cost_reported=_optional_int(raw["cost_reported"]),
                    tps=tps,
                    tp99_us=float(raw["tp99_us"]),
                    tp999_us=float(raw["tp999_us"]),
                    client_cpu_pct=client_cpu,
                    server_cpu_pct=server_cpu,
                    total_cpu_pct=total_cpu,
                    used_cores=total_cpu / 100.0,
                    attainment_pct=attainment,
                    target_sustained=attainment >= 99.5,
                    client_rss_kb=int(raw["client_rss_kb"]),
                    server_rss_kb=int(raw["server_rss_kb"]),
                    retry=int(raw["retry"]),
                    status=raw["status"],
                )
            )
    if [row.target_qps for row in rows] != list(TARGET_QPS_VALUES):
        raise ValueError("Socket baseline must contain one ordered row per target QPS")
    if {row.concurrency for row in rows} != {128} or {
        row.body_bytes for row in rows
    } != {1024}:
        raise ValueError("Socket baseline envelope differs from GQM envelope")
    return rows


def _dominates(left: DerivedMeasurement, right: DerivedMeasurement) -> bool:
    assert left.total_cpu_pct is not None and left.tp99_us is not None
    assert right.total_cpu_pct is not None and right.tp99_us is not None
    no_worse = (
        left.total_cpu_pct <= right.total_cpu_pct
        and left.tp99_us <= right.tp99_us
    )
    strictly_better = (
        left.total_cpu_pct < right.total_cpu_pct
        or left.tp99_us < right.tp99_us
    )
    return no_worse and strictly_better


def derive_measurements(
    rows: Sequence[Measurement],
) -> list[DerivedMeasurement]:
    derived: list[DerivedMeasurement] = []
    for row in rows:
        total_cpu = (
            row.client_cpu_pct + row.server_cpu_pct
            if row.client_cpu_pct is not None and row.server_cpu_pct is not None
            else None
        )
        used_cores = total_cpu / 100.0 if total_cpu is not None else None
        attainment = 100.0 * row.tps / row.target_qps
        complete_for_tradeoff = (
            not row.partial
            and row.tp99_us is not None
            and used_cores is not None
            and used_cores > 0
        )
        derived.append(
            DerivedMeasurement(
                source_index=row.source_index,
                value=row.value,
                target_qps=row.target_qps,
                bud=row.bud,
                sleep_us=row.sleep_us,
                concurrency=row.concurrency,
                body_bytes=row.body_bytes,
                cost_reported=row.cost_reported,
                tps=row.tps,
                tp99_us=row.tp99_us,
                tp999_us=row.tp999_us,
                client_cpu_pct=row.client_cpu_pct,
                server_cpu_pct=row.server_cpu_pct,
                total_cpu_pct=total_cpu,
                used_cores=used_cores,
                qps_per_used_core=(
                    row.tps / used_cores if complete_for_tradeoff else None
                ),
                attainment_pct=attainment,
                target_sustained=complete_for_tradeoff and attainment >= 99.5,
                is_pareto=False,
                client_rss_kb=row.client_rss_kb,
                server_rss_kb=row.server_rss_kb,
                retry=row.retry,
                status=row.status,
                partial=row.partial,
            )
        )

    by_source = {row.source_index: row for row in derived}
    for target_qps in TARGET_QPS_VALUES:
        load_rows = [row for row in derived if row.target_qps == target_qps]
        candidates = [row for row in load_rows if row.target_sustained]
        if not candidates:
            continue
        for candidate in candidates:
            if not any(
                _dominates(other, candidate)
                for other in candidates
                if other.source_index != candidate.source_index
            ):
                by_source[candidate.source_index] = replace(
                    candidate, is_pareto=True
                )
    return [by_source[row.source_index] for row in derived]


def _mean(values: Sequence[float]) -> float:
    return sum(values) / len(values)


def _optional_range(
    values: Sequence[float | None],
) -> tuple[float | None, float | None, float | None]:
    complete = [value for value in values if value is not None]
    if len(complete) != len(values) or not complete:
        return None, None, None
    return _mean(complete), min(complete), max(complete)


def _gqm_candidate(
    rows: Sequence[DerivedMeasurement], policy: str
) -> TradeoffCandidate:
    if not rows:
        raise ValueError("cannot aggregate an empty GQM candidate")
    target_qps = rows[0].target_qps
    sleep_us = rows[0].sleep_us
    if any(
        row.target_qps != target_qps or row.sleep_us != sleep_us for row in rows
    ):
        raise ValueError("GQM repeat rows must share target QPS and sleep")

    tps_values = [row.tps for row in rows]
    p99, p99_min, p99_max = _optional_range([row.tp99_us for row in rows])
    p999, _, _ = _optional_range([row.tp999_us for row in rows])
    total_cpu, total_cpu_min, total_cpu_max = _optional_range(
        [row.total_cpu_pct for row in rows]
    )
    used_cores, used_cores_min, used_cores_max = _optional_range(
        [row.used_cores for row in rows]
    )
    attainment_values = [row.attainment_pct for row in rows]
    gate_values = [row.target_sustained for row in rows]
    source_indices = ";".join(str(row.source_index) for row in rows)
    return TradeoffCandidate(
        candidate_id=f"gqm:{target_qps}:{policy}:{sleep_us}",
        transport="GQM",
        policy=policy,
        target_qps=target_qps,
        sleep_us=sleep_us,
        source_indices=source_indices,
        repeat_count=len(rows),
        repeat_gate_consistent=len(set(gate_values)) == 1,
        tps=_mean(tps_values),
        tps_min=min(tps_values),
        tps_max=max(tps_values),
        tp99_us=p99,
        tp99_us_min=p99_min,
        tp99_us_max=p99_max,
        tp999_us=p999,
        total_cpu_pct=total_cpu,
        total_cpu_pct_min=total_cpu_min,
        total_cpu_pct_max=total_cpu_max,
        used_cores=used_cores,
        used_cores_min=used_cores_min,
        used_cores_max=used_cores_max,
        attainment_pct=_mean(attainment_values),
        attainment_pct_min=min(attainment_values),
        attainment_pct_max=max(attainment_values),
        target_sustained=all(gate_values),
        is_pareto=False,
        partial=any(row.partial for row in rows),
    )


def _socket_candidate(row: SocketMeasurement) -> TradeoffCandidate:
    return TradeoffCandidate(
        candidate_id=f"socket:{row.target_qps}",
        transport="Socket",
        policy="Socket",
        target_qps=row.target_qps,
        sleep_us=None,
        source_indices=str(row.source_index),
        repeat_count=1,
        repeat_gate_consistent=True,
        tps=row.tps,
        tps_min=row.tps,
        tps_max=row.tps,
        tp99_us=row.tp99_us,
        tp99_us_min=row.tp99_us,
        tp99_us_max=row.tp99_us,
        tp999_us=row.tp999_us,
        total_cpu_pct=row.total_cpu_pct,
        total_cpu_pct_min=row.total_cpu_pct,
        total_cpu_pct_max=row.total_cpu_pct,
        used_cores=row.used_cores,
        used_cores_min=row.used_cores,
        used_cores_max=row.used_cores,
        attainment_pct=row.attainment_pct,
        attainment_pct_min=row.attainment_pct,
        attainment_pct_max=row.attainment_pct,
        target_sustained=row.target_sustained,
        is_pareto=False,
        partial=False,
    )


def _candidate_dominates(
    left: TradeoffCandidate, right: TradeoffCandidate
) -> bool:
    assert left.total_cpu_pct is not None and left.tp99_us is not None
    assert right.total_cpu_pct is not None and right.tp99_us is not None
    return (
        left.total_cpu_pct <= right.total_cpu_pct
        and left.tp99_us <= right.tp99_us
        and (
            left.total_cpu_pct < right.total_cpu_pct
            or left.tp99_us < right.tp99_us
        )
    )


def build_tradeoff_candidates(
    gqm_rows: Sequence[DerivedMeasurement],
    socket_rows: Sequence[SocketMeasurement],
) -> list[TradeoffCandidate]:
    candidates: list[TradeoffCandidate] = []
    for target_qps in TARGET_QPS_VALUES:
        load_rows = [row for row in gqm_rows if row.target_qps == target_qps]
        for sleep_us in SLEEP_US_VALUES:
            duplicate_rows = [
                row
                for row in load_rows
                if row.bud in (0, 256) and row.sleep_us == sleep_us
            ]
            if len(duplicate_rows) != 2:
                raise ValueError("expected one BUD=0/256 duplicate pair per load/sleep")
            candidates.append(
                _gqm_candidate(
                    sorted(duplicate_rows, key=lambda row: row.bud),
                    "B0/256 duplicate",
                )
            )
        for bud in (1, 16, 1024):
            for sleep_us in SLEEP_US_VALUES:
                row = next(
                    row
                    for row in load_rows
                    if row.bud == bud and row.sleep_us == sleep_us
                )
                candidates.append(_gqm_candidate([row], f"B{bud}"))

        socket = next(row for row in socket_rows if row.target_qps == target_qps)
        candidates.append(_socket_candidate(socket))

    by_id = {candidate.candidate_id: candidate for candidate in candidates}
    for target_qps in TARGET_QPS_VALUES:
        eligible = [
            candidate
            for candidate in candidates
            if candidate.target_qps == target_qps
            and candidate.target_sustained
            and candidate.total_cpu_pct is not None
            and candidate.tp99_us is not None
        ]
        for candidate in eligible:
            if not any(
                _candidate_dominates(other, candidate)
                for other in eligible
                if other.candidate_id != candidate.candidate_id
            ):
                by_id[candidate.candidate_id] = replace(candidate, is_pareto=True)
    return [by_id[candidate.candidate_id] for candidate in candidates]


def summarize_by_load(
    rows: Sequence[DerivedMeasurement],
) -> list[LoadSummary]:
    summaries: list[LoadSummary] = []
    for target_qps in TARGET_QPS_VALUES:
        load_rows = [row for row in rows if row.target_qps == target_qps]
        complete = [
            row
            for row in load_rows
            if not row.partial
            and row.tp99_us is not None
            and row.used_cores is not None
        ]
        summaries.append(
            LoadSummary(
                target_qps=target_qps,
                load_class=(
                    "near-capacity stress"
                    if not any(row.target_sustained for row in load_rows)
                    else "target-controlled"
                ),
                total_cells=len(load_rows),
                complete_cells=len(complete),
                partial_cells=sum(row.partial for row in load_rows),
                sustained_cells=sum(row.target_sustained for row in load_rows),
                pareto_cells=sum(row.is_pareto for row in load_rows),
                max_tps=max(row.tps for row in load_rows),
                max_attainment_pct=max(row.attainment_pct for row in load_rows),
                min_p99_us=min(row.tp99_us for row in complete if row.tp99_us is not None),
                max_p99_us=max(row.tp99_us for row in complete if row.tp99_us is not None),
                min_used_cores=min(
                    row.used_cores for row in complete if row.used_cores is not None
                ),
                max_used_cores=max(
                    row.used_cores for row in complete if row.used_cores is not None
                ),
            )
        )
    return summaries


def build_policy_boundaries(
    rows: Sequence[TradeoffCandidate],
) -> list[PolicyBoundary]:
    boundaries: list[PolicyBoundary] = []
    for target_qps in TARGET_QPS_VALUES:
        candidates = [
            row
            for row in rows
            if row.target_qps == target_qps
            and row.target_sustained
            and row.tp99_us is not None
            and row.used_cores is not None
            and row.total_cpu_pct is not None
        ]
        load_boundaries: list[PolicyBoundary] = []
        selected_id: str | None = None
        for p99_budget_us in sorted({row.tp99_us for row in candidates}):
            eligible = [
                row
                for row in candidates
                if row.tp99_us is not None and row.tp99_us <= p99_budget_us
            ]
            selected = min(
                eligible,
                key=lambda row: (
                    row.used_cores if row.used_cores is not None else math.inf,
                    row.tp99_us if row.tp99_us is not None else math.inf,
                    row.candidate_id,
                ),
            )
            if selected.candidate_id == selected_id:
                continue
            assert selected.used_cores is not None
            assert selected.total_cpu_pct is not None
            load_boundaries.append(
                PolicyBoundary(
                    target_qps=target_qps,
                    min_p99_budget_us=p99_budget_us,
                    max_p99_budget_us=None,
                    candidate_id=selected.candidate_id,
                    transport=selected.transport,
                    policy=selected.policy,
                    sleep_us=selected.sleep_us,
                    repeat_count=selected.repeat_count,
                    used_cores=round(selected.used_cores, 4),
                    total_cpu_pct=round(selected.total_cpu_pct, 2),
                    quota_occupancy_pct=round(
                        selected.used_cores / 16.0 * 100.0, 3
                    ),
                )
            )
            selected_id = selected.candidate_id

        for index, boundary in enumerate(load_boundaries[:-1]):
            load_boundaries[index] = replace(
                boundary,
                max_p99_budget_us=load_boundaries[index + 1].min_p99_budget_us,
            )
        boundaries.extend(load_boundaries)
    return boundaries


def _write_dataclass_csv(rows: Sequence[object], path: Path) -> None:
    if not rows:
        raise ValueError("cannot write an empty CSV")
    path.parent.mkdir(parents=True, exist_ok=True)
    serialized = [asdict(row) for row in rows]
    with path.open("w", newline="", encoding="utf-8") as target:
        writer = csv.DictWriter(
            target,
            fieldnames=list(serialized[0].keys()),
            lineterminator="\n",
        )
        writer.writeheader()
        writer.writerows(serialized)


def write_derived_csv(
    rows: Sequence[DerivedMeasurement], path: Path
) -> None:
    _write_dataclass_csv(rows, path)


def write_summary_csv(rows: Sequence[LoadSummary], path: Path) -> None:
    _write_dataclass_csv(rows, path)


def write_tradeoff_candidates_csv(
    rows: Sequence[TradeoffCandidate], path: Path
) -> None:
    _write_dataclass_csv(rows, path)


def write_policy_boundaries_csv(
    rows: Sequence[PolicyBoundary], path: Path
) -> None:
    _write_dataclass_csv(rows, path)


def _academic_style() -> None:
    plt.rcParams.update(
        {
            "font.family": "DejaVu Sans",
            "font.size": 9.5,
            "axes.titlesize": 11,
            "axes.labelsize": 10,
            "legend.fontsize": 8.2,
            "xtick.labelsize": 8.7,
            "ytick.labelsize": 8.7,
            "axes.spines.top": False,
            "axes.spines.right": False,
            "axes.linewidth": 0.8,
            "grid.color": "#C8C8C8",
            "grid.linewidth": 0.55,
            "grid.alpha": 0.45,
            "svg.fonttype": "none",
        }
    )


def _save_figure(
    figure: plt.Figure, output_dir: Path, stem: str
) -> list[Path]:
    output_dir.mkdir(parents=True, exist_ok=True)
    outputs: list[Path] = []
    for suffix in ("png", "svg"):
        path = output_dir / f"{stem}.{suffix}"
        figure.savefig(path, dpi=220, bbox_inches="tight", facecolor="white")
        if suffix == "svg":
            text = path.read_text(encoding="utf-8")
            path.write_text(
                "\n".join(line.rstrip() for line in text.splitlines()) + "\n",
                encoding="utf-8",
            )
        outputs.append(path)
    plt.close(figure)
    return outputs


def _add_heading(figure: plt.Figure, title: str) -> None:
    figure.suptitle(title, fontsize=15, fontweight="bold", y=0.995)
    figure.text(
        0.5,
        0.957,
        f"{RESOURCE_LABEL}\n{WORKLOAD_LABEL}",
        ha="center",
        va="top",
        fontsize=9.2,
        linespacing=1.35,
    )


def _reserve_caption_space(figure: plt.Figure) -> None:
    layout_engine = figure.get_layout_engine()
    if layout_engine is not None:
        layout_engine.set(rect=(0.0, 0.07, 1.0, 0.835), h_pad=0.12, w_pad=0.10)


def _load_rows(
    rows: Sequence[TradeoffCandidate], target_qps: int
) -> list[TradeoffCandidate]:
    return [row for row in rows if row.target_qps == target_qps]


def _format_latency(value_us: float) -> str:
    if value_us >= 1000:
        return f"{value_us / 1000:.2f} ms"
    return f"{value_us:.0f} µs"


def _policy_label(policy: str, sleep_us: int | None) -> str:
    if policy == "Socket":
        return "Socket"
    compact_policy = "B0/256" if policy == "B0/256 duplicate" else policy
    assert sleep_us is not None
    return f"{compact_policy}/S{SLEEP_LABELS[sleep_us]}"


def _add_quota_axis(axis: plt.Axes) -> None:
    quota_axis = axis.secondary_xaxis(
        "top",
        functions=(
            lambda cores: cores / 16.0 * 100.0,
            lambda occupancy: occupancy / 100.0 * 16.0,
        ),
    )
    quota_axis.set_xlabel("Combined container quota occupancy (%)", labelpad=3)
    quota_axis.tick_params(labelsize=7.8, pad=1.5)


def _plot_cpu_p99_tradeoff(
    rows: Sequence[TradeoffCandidate], output_dir: Path
) -> list[Path]:
    figure, axes = plt.subplots(
        2, 3, figsize=(15.8, 9.2), constrained_layout=True
    )
    for axis, target_qps in zip(axes.flat[:5], TARGET_QPS_VALUES):
        load_rows = [
            row
            for row in _load_rows(rows, target_qps)
            if row.used_cores is not None and row.tp99_us is not None
        ]
        unsustained_gqm = [
            row
            for row in load_rows
            if row.transport == "GQM" and not row.target_sustained
        ]
        if unsustained_gqm:
            axis.scatter(
                [row.used_cores for row in unsustained_gqm],
                [row.tp99_us for row in unsustained_gqm],
                marker="x",
                s=28,
                color="#B0B0B0",
                linewidth=0.9,
                alpha=0.70,
                zorder=1,
            )
        for row in (row for row in load_rows if row.transport == "GQM"):
            assert row.sleep_us is not None
            color = (
                POLICY_COLORS[row.policy]
                if row.target_sustained
                else "#B0B0B0"
            )
            if row.repeat_count == 2:
                assert (
                    row.used_cores is not None
                    and row.used_cores_min is not None
                    and row.used_cores_max is not None
                    and row.tp99_us is not None
                    and row.tp99_us_min is not None
                    and row.tp99_us_max is not None
                )
                axis.errorbar(
                    row.used_cores,
                    row.tp99_us,
                    xerr=[[row.used_cores - row.used_cores_min], [row.used_cores_max - row.used_cores]],
                    yerr=[[row.tp99_us - row.tp99_us_min], [row.tp99_us_max - row.tp99_us]],
                    fmt=SLEEP_MARKERS[row.sleep_us],
                    markersize=6.2,
                    markerfacecolor=color if row.target_sustained else "white",
                    markeredgecolor="#222222" if row.target_sustained else "#999999",
                    markeredgewidth=0.75,
                    ecolor=color,
                    elinewidth=0.9,
                    capsize=2.2,
                    alpha=0.92 if row.target_sustained else 0.58,
                    zorder=4,
                )
            elif row.target_sustained:
                axis.scatter(
                    row.used_cores,
                    row.tp99_us,
                    marker=SLEEP_MARKERS[row.sleep_us],
                    s=52,
                    facecolor=color,
                    edgecolor="#222222",
                    linewidth=0.75,
                    alpha=0.92,
                    zorder=3,
                )

        socket = next(row for row in load_rows if row.transport == "Socket")
        assert socket.used_cores is not None and socket.tp99_us is not None
        socket_color = "#B2182B" if socket.target_sustained else "#777777"
        axis.axvline(
            socket.used_cores,
            color=socket_color,
            linewidth=0.8,
            linestyle=(0, (3, 3)),
            alpha=0.60,
            zorder=0,
        )
        axis.axhline(
            socket.tp99_us,
            color=socket_color,
            linewidth=0.8,
            linestyle=(0, (3, 3)),
            alpha=0.60,
            zorder=0,
        )
        axis.scatter(
            socket.used_cores,
            socket.tp99_us,
            marker="*",
            s=155,
            facecolor=socket_color if socket.target_sustained else "white",
            edgecolor=socket_color,
            linewidth=1.25,
            zorder=7,
        )
        socket_label = (
            f"Socket: {socket.used_cores:.3f} cores, "
            f"{_format_latency(socket.tp99_us)}, "
            f"{socket.attainment_pct:.2f}%"
        )
        axis.annotate(
            socket_label,
            (socket.used_cores, socket.tp99_us),
            xytext=(7, 10 if target_qps != 7_700 else -17),
            textcoords="offset points",
            fontsize=7.2,
            color=socket_color,
            ha="left",
            va="center",
            bbox={"facecolor": "white", "edgecolor": "none", "alpha": 0.82, "pad": 0.15},
            zorder=8,
        )
        frontier = sorted(
            (row for row in load_rows if row.is_pareto),
            key=lambda row: row.used_cores or 0,
        )
        if frontier:
            axis.plot(
                [row.used_cores for row in frontier],
                [row.tp99_us for row in frontier],
                color="#333333",
                linewidth=1.15,
                linestyle="--",
                zorder=2,
            )
            right_edge = max(row.used_cores or 0 for row in load_rows) * 1.11
            offsets = {
                7_700: ((7, -18), (7, 9), (7, -18), (7, 9), (-7, 9)),
                77_000: ((7, -18), (7, 9), (7, 12), (7, -16), (7, 12), (-7, 12)),
                192_500: ((7, -18), (7, 9), (7, -18), (-7, 9)),
                385_000: ((7, -17), (-7, 9)),
            }.get(target_qps, ())
            frontier_gqm = [row for row in frontier if row.transport == "GQM"]
            for index, row in enumerate(frontier_gqm):
                assert row.used_cores is not None and row.tp99_us is not None
                offset = offsets[index] if index < len(offsets) else (7, 8)
                align_right = row.used_cores > right_edge * 0.72
                axis.annotate(
                    _policy_label(row.policy, row.sleep_us),
                    (row.used_cores, row.tp99_us),
                    xytext=(-abs(offset[0]), offset[1]) if align_right else offset,
                    textcoords="offset points",
                    fontsize=7.3,
                    color="#222222",
                    ha="right" if align_right else "left",
                    va="center",
                    bbox={
                        "facecolor": "white",
                        "edgecolor": "none",
                        "alpha": 0.78,
                        "pad": 0.12,
                    },
                )
        title = LOAD_TITLES[target_qps]
        if target_qps == 731_500:
            title += " — near-capacity stress"
            axis.text(
                0.5,
                0.06,
                "No policy satisfies the 99.5% attainment gate",
                ha="center",
                transform=axis.transAxes,
                fontsize=8.2,
                color="#555555",
            )
        axis.set_title(title, fontweight="bold")
        axis.set_xlabel("Total client + server CPU (vCPU-equivalents)")
        axis.set_ylabel("P99 latency (µs, log scale)")
        axis.set_yscale("log")
        axis.grid(True, which="both")
        axis.set_xlim(0, max(row.used_cores or 0 for row in load_rows) * 1.16)
        axis.margins(y=0.20)
        _add_quota_axis(axis)

    legend_axis = axes.flat[5]
    legend_axis.axis("off")
    policy_handles = [
        Line2D(
            [0],
            [0],
            marker="o",
            linestyle="none",
            markerfacecolor=color,
            markeredgecolor=color,
            label=policy,
        )
        for policy, color in POLICY_COLORS.items()
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
        Line2D(
            [0],
            [0],
            marker="x",
            linestyle="none",
            color="#B0B0B0",
            label="Target not sustained",
        ),
        Line2D(
            [0],
            [0],
            color="#333333",
            linestyle="--",
            label="Pareto frontier",
        ),
        Line2D(
            [0],
            [0],
            marker="*",
            markersize=10,
            linestyle="none",
            markerfacecolor="#B2182B",
            markeredgecolor="#B2182B",
            label="Socket event-driven reference (gate passed)",
        ),
        Line2D(
            [0],
            [0],
            marker="*",
            markersize=10,
            linestyle="none",
            markerfacecolor="white",
            markeredgecolor="#777777",
            label="Socket reference (gate failed)",
        ),
    ]
    first_legend = legend_axis.legend(
        handles=policy_handles,
        title="GQM policy (color)",
        loc="upper left",
        bbox_to_anchor=(0.0, 1.0),
        frameon=False,
        ncol=2,
    )
    legend_axis.add_artist(first_legend)
    second_legend = legend_axis.legend(
        handles=sleep_handles,
        title="Sleep (marker)",
        loc="upper left",
        bbox_to_anchor=(0.0, 0.68),
        frameon=False,
        ncol=2,
    )
    legend_axis.add_artist(second_legend)
    legend_axis.legend(
        handles=state_handles,
        title="Attainment and reference",
        loc="upper left",
        bbox_to_anchor=(0.0, 0.34),
        frameon=False,
    )
    _add_heading(figure, "GQM Idle Policy CPU–P99 Trade-off with Socket Anchors")
    _reserve_caption_space(figure)
    figure.text(
        0.5,
        0.012,
        "CPU metric: sum of thread-level process CPU across client and server; "
        "100% = one vCPU-equivalent. Frontier uses candidates satisfying the 99.5% attainment gate. "
        "B0/256 whiskers are two-run min–max ranges, not confidence intervals.",
        ha="center",
        fontsize=8.8,
        color="#444444",
    )
    return _save_figure(figure, output_dir, "01_sc_symmetric_cpu_p99_tradeoff")


def _plot_p99_budget_boundary(
    rows: Sequence[TradeoffCandidate], output_dir: Path
) -> list[Path]:
    boundaries = build_policy_boundaries(rows)
    figure, axes = plt.subplots(
        5,
        1,
        figsize=(14.5, 11.2),
        constrained_layout=True,
        gridspec_kw={"height_ratios": (1, 1, 1, 1, 0.48)},
    )
    annotation_offsets = {
        7_700: ((6, -18), (6, 10), (6, -18), (-8, 28), (-8, -20)),
        77_000: ((6, -18), (6, 10), (6, -18), (6, 10), (6, -18), (-6, 10)),
        192_500: ((6, -18), (6, 11), (6, -18), (-6, 11)),
        385_000: ((6, -18), (-6, 12)),
    }
    for axis, target_qps in zip(axes, TARGET_QPS_VALUES):
        load_boundaries = [
            boundary
            for boundary in boundaries
            if boundary.target_qps == target_qps
        ]
        axis.set_title(LOAD_TITLES[target_qps], fontweight="bold", loc="left")
        if not load_boundaries:
            axis.set_facecolor("#F5F5F5")
            axis.text(
                0.5,
                0.66,
                "No policy satisfies the 99.5% attainment gate",
                ha="center",
                va="center",
                transform=axis.transAxes,
                fontsize=10,
                fontweight="bold",
                color="#555555",
            )
            axis.text(
                0.5,
                0.26,
                "Maximum observed attainment: 96.72%",
                ha="center",
                va="center",
                transform=axis.transAxes,
                fontsize=9,
                color="#666666",
            )
            axis.set_xticks([])
            axis.set_yticks([])
            continue

        complete_p99 = [
            row.tp99_us
            for row in _load_rows(rows, target_qps)
            if row.target_sustained and row.tp99_us is not None
        ]
        display_max = max(
            max(complete_p99) * 1.10,
            load_boundaries[-1].min_p99_budget_us * 1.55,
        )
        x_values = [
            boundary.min_p99_budget_us for boundary in load_boundaries
        ] + [display_max]
        y_values = [boundary.used_cores for boundary in load_boundaries] + [
            load_boundaries[-1].used_cores
        ]
        axis.step(
            x_values,
            y_values,
            where="post",
            color="#333333",
            linewidth=1.65,
            zorder=2,
        )
        for boundary in load_boundaries:
            is_socket = boundary.transport == "Socket"
            axis.scatter(
                boundary.min_p99_budget_us,
                boundary.used_cores,
                marker="*" if is_socket else "o",
                s=105 if is_socket else 42,
                color="#B2182B" if is_socket else "#4477AA",
                edgecolor="#222222",
                linewidth=0.7,
                zorder=4 if is_socket else 3,
            )
        offsets = annotation_offsets[target_qps]
        for index, boundary in enumerate(load_boundaries):
            offset = offsets[index]
            align_right = offset[0] < 0
            axis.annotate(
                f"≥{_format_latency(boundary.min_p99_budget_us)}\n"
                f"{_policy_label(boundary.policy, boundary.sleep_us)}; "
                f"{boundary.used_cores:.3f} cores",
                (boundary.min_p99_budget_us, boundary.used_cores),
                xytext=offset,
                textcoords="offset points",
                ha="right" if align_right else "left",
                va="center",
                fontsize=7.3,
                color="#222222",
                arrowprops={
                    "arrowstyle": "-",
                    "color": "#777777",
                    "linewidth": 0.55,
                    "shrinkA": 2,
                    "shrinkB": 2,
                },
                bbox={
                    "facecolor": "white",
                    "edgecolor": "none",
                    "alpha": 0.82,
                    "pad": 0.12,
                },
            )
        axis.set_xscale("log")
        axis.set_xlim(load_boundaries[0].min_p99_budget_us * 0.88, display_max)
        axis.set_ylim(0, max(boundary.used_cores for boundary in load_boundaries) * 1.25)
        axis.set_xlabel("Allowed P99 budget (µs, log scale)")
        axis.grid(True, which="both")
    figure.supylabel(
        "Minimum total CPU (vCPU-equivalents)",
        x=0.008,
        fontsize=10,
    )
    _add_heading(figure, "Minimum CPU Required by P99 SLO")
    _reserve_caption_space(figure)
    figure.text(
        0.5,
        0.018,
        "Selection rule: minimize total CPU subject to attainment ≥ 99.5% and measured P99 ≤ budget. "
        "Each step begins at an observed candidate center; duplicate GQM centers are two-run means.",
        ha="center",
        fontsize=8.8,
        color="#444444",
    )
    figure.text(
        0.5,
        0.004,
        "CPU metric: sum of thread-level process CPU across client and server. "
        "Socket is a system-level reference, not a GQM IRQ measurement; other candidates are single-run.",
        ha="center",
        fontsize=8.8,
        color="#444444",
    )
    return _save_figure(
        figure, output_dir, "02_p99_budget_minimum_cpu_boundary"
    )


def generate_figures(
    rows: Sequence[TradeoffCandidate], output_dir: Path
) -> list[Path]:
    _academic_style()
    outputs: list[Path] = []
    outputs.extend(_plot_cpu_p99_tradeoff(rows, output_dir))
    outputs.extend(_plot_p99_budget_boundary(rows, output_dir))
    return outputs


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--data", type=Path, required=True)
    parser.add_argument("--socket-data", type=Path, required=True)
    parser.add_argument("--derived-output", type=Path, required=True)
    parser.add_argument("--summary-output", type=Path, required=True)
    parser.add_argument("--candidate-output", type=Path, required=True)
    parser.add_argument("--policy-boundary-output", type=Path, required=True)
    parser.add_argument("--figure-dir", type=Path, required=True)
    arguments = parser.parse_args()

    derived = derive_measurements(load_measurements(arguments.data))
    candidates = build_tradeoff_candidates(
        derived, load_socket_measurements(arguments.socket_data)
    )
    write_derived_csv(derived, arguments.derived_output)
    write_summary_csv(summarize_by_load(derived), arguments.summary_output)
    write_tradeoff_candidates_csv(candidates, arguments.candidate_output)
    write_policy_boundaries_csv(
        build_policy_boundaries(candidates), arguments.policy_boundary_output
    )
    generate_figures(candidates, arguments.figure_dir)


if __name__ == "__main__":
    main()
