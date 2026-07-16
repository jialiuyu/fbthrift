from __future__ import annotations

import argparse
import csv
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, Literal, Sequence

import matplotlib

matplotlib.use("Agg")

import matplotlib.pyplot as plt
from matplotlib.colors import LogNorm
from matplotlib.lines import Line2D


@dataclass(frozen=True)
class Result:
    name: str
    kind: Literal["hot", "idle", "socket"]
    bud: int | None
    sleep_us: float | None
    p50_us: float
    p99_us: float
    p999_us: float
    max_us: float
    client_cpu_pct: float
    server_cpu_pct: float
    drain_timeout: bool
    drain_remaining: int


EXPERIMENT_LABEL = "Poisson arrivals | 350 QPS | payload: 64 B | handler: echo"
BUD_COLORS = {
    0: "#2f6f9f",
    16: "#d17a22",
    128: "#2f8f5b",
    1024: "#a74747",
}
BASELINE_STYLES = {
    "hot": ("#292929", "*"),
    "socket": ("#7a4e9d", "D"),
}


def load_results(path: Path) -> list[Result]:
    with path.open(newline="", encoding="utf-8") as source:
        rows = []
        for raw in csv.DictReader(source):
            rows.append(
                Result(
                    name=raw["name"],
                    kind=raw["kind"],  # type: ignore[arg-type]
                    bud=int(raw["bud"]) if raw["bud"] else None,
                    sleep_us=float(raw["sleep_us"]) if raw["sleep_us"] else None,
                    p50_us=float(raw["p50_us"]),
                    p99_us=float(raw["p99_us"]),
                    p999_us=float(raw["p999_us"]),
                    max_us=float(raw["max_us"]),
                    client_cpu_pct=float(raw["client_cpu_pct"]),
                    server_cpu_pct=float(raw["server_cpu_pct"]),
                    drain_timeout=raw["drain_timeout"].lower() == "true",
                    drain_remaining=int(raw["drain_remaining"]),
                )
            )
    return rows


def pareto_frontier(
    rows: Iterable[Result],
    *,
    x: Literal["client_cpu_pct", "server_cpu_pct"],
    y: Literal["p50_us", "p99_us", "p999_us"],
) -> list[Result]:
    candidates = sorted(
        rows,
        key=lambda row: (getattr(row, x), getattr(row, y)),
    )
    frontier: list[Result] = []
    best_y = float("inf")
    for row in candidates:
        row_y = getattr(row, y)
        if row_y < best_y:
            frontier.append(row)
            best_y = row_y
    return frontier


def best_idle_under_socket(
    rows: Iterable[Result],
    *,
    metric: Literal["p99_us", "p999_us"],
    cpu_metric: Literal["client_cpu_pct", "server_cpu_pct"],
) -> Result:
    all_rows = list(rows)
    socket = next(row for row in all_rows if row.kind == "socket")
    socket_latency = getattr(socket, metric)
    candidates = [
        row
        for row in all_rows
        if row.kind == "idle" and getattr(row, metric) <= socket_latency
    ]
    if not candidates:
        raise ValueError(f"no idle configuration satisfies Socket {metric}")
    return min(
        candidates,
        key=lambda row: (getattr(row, cpu_metric), getattr(row, metric)),
    )


def _bud_color(bud: int) -> str:
    if bud in BUD_COLORS:
        return BUD_COLORS[bud]
    palette = plt.get_cmap("tab10")
    return palette(abs(hash(bud)) % 10)


def _format_sleep(sleep_us: float) -> str:
    if sleep_us >= 1000 and sleep_us % 1000 == 0:
        return f"{sleep_us / 1000:g}ms"
    return f"{sleep_us:g}us"


def _point_label(row: Result) -> str:
    assert row.bud is not None and row.sleep_us is not None
    return f"B{row.bud}/{_format_sleep(row.sleep_us)}"


def _point_label_position(row: Result, cpu_value: float) -> tuple[int, int, str]:
    assert row.bud is not None and row.sleep_us is not None
    bud_offset = {0: 12, 16: -12, 128: 12, 1024: -12}.get(row.bud, 0)
    sleep_offset = {1.0: 8, 10.0: -8}.get(row.sleep_us, 0)
    if cpu_value >= 70:
        return -4, bud_offset + sleep_offset, "right"
    return 4, bud_offset + sleep_offset, "left"


def _save_figure(fig: plt.Figure, output_dir: Path, stem: str) -> list[Path]:
    output_dir.mkdir(parents=True, exist_ok=True)
    outputs = []
    for suffix in ("png", "svg"):
        path = output_dir / f"{stem}.{suffix}"
        fig.savefig(path, dpi=180, bbox_inches="tight")
        if suffix == "svg":
            svg = path.read_text(encoding="utf-8")
            normalized = "\n".join(line.rstrip() for line in svg.splitlines()) + "\n"
            path.write_text(normalized, encoding="utf-8")
        outputs.append(path)
    plt.close(fig)
    return outputs


def _bud_legend_handles(buds: Sequence[int]) -> list[Line2D]:
    return [
        Line2D([0], [0], color=_bud_color(bud), linewidth=1.8, label=f"BUD={bud}")
        for bud in buds
    ]


def _bud_point_legend_handles(buds: Sequence[int]) -> list[Line2D]:
    return [
        Line2D(
            [0],
            [0],
            color=_bud_color(bud),
            marker="o",
            linestyle="none",
            label=f"BUD={bud}",
        )
        for bud in buds
    ]


def _baseline_legend_handles(*, hot_as_point: bool) -> list[Line2D]:
    return [
        Line2D(
            [0],
            [0],
            color=BASELINE_STYLES["hot"][0],
            marker=BASELINE_STYLES["hot"][1] if hot_as_point else None,
            linestyle="none" if hot_as_point else "--",
            markersize=9,
            label="HOT POLL",
        ),
        Line2D(
            [0],
            [0],
            color=BASELINE_STYLES["socket"][0],
            linestyle="--",
            linewidth=1.4,
            label="Socket baseline",
        ),
    ]


def _annotate_reference_line(
    axis: plt.Axes,
    row: Result,
    metric: str,
    *,
    offset_points: int,
) -> None:
    value = getattr(row, metric)
    color = BASELINE_STYLES[row.kind][0]
    axis.axhline(value, color=color, linestyle="--", linewidth=1.1, alpha=0.8)
    axis.annotate(
        f"{'Socket' if row.kind == 'socket' else 'HOT'}: {value:g}",
        xy=(0.985, value),
        xycoords=("axes fraction", "data"),
        xytext=(0, offset_points),
        textcoords="offset points",
        ha="right",
        va="bottom",
        fontsize=7.5,
        color=color,
    )


def _draw_y_axis_break(top_axis: plt.Axes, bottom_axis: plt.Axes) -> None:
    top_axis.spines["bottom"].set_visible(False)
    bottom_axis.spines["top"].set_visible(False)
    top_axis.tick_params(axis="x", which="both", bottom=False, labelbottom=False)
    bottom_axis.xaxis.tick_bottom()

    size = 0.009
    top_style = {
        "transform": top_axis.transAxes,
        "color": "#444444",
        "clip_on": False,
        "linewidth": 0.9,
    }
    top_axis.plot((-size, size), (-size, size), **top_style)
    top_axis.plot((1 - size, 1 + size), (-size, size), **top_style)

    bottom_style = {**top_style, "transform": bottom_axis.transAxes}
    bottom_axis.plot((-size, size), (1 - size, 1 + size), **bottom_style)
    bottom_axis.plot((1 - size, 1 + size), (1 - size, 1 + size), **bottom_style)


def _low_latency_limits(rows: Sequence[Result], split_us: float) -> tuple[float, float]:
    values = [row.p99_us for row in rows if row.p99_us < split_us]
    if not values:
        return split_us * 0.5, split_us * 0.9
    span = max(values) - min(values)
    padding = max(0.2, span * 0.12)
    return min(values) - padding, max(values) + padding


def _plot_cpu_latency(rows: Sequence[Result], output_dir: Path) -> list[Path]:
    fig = plt.figure(figsize=(15, 7.2), layout="constrained")
    outer_grid = fig.add_gridspec(1, 2, wspace=0.12)
    panels = (
        ("client_cpu_pct", "Client CPU"),
        ("server_cpu_pct", "Server CPU"),
    )
    idle_rows = [row for row in rows if row.kind == "idle"]
    buds = sorted({row.bud for row in idle_rows if row.bud is not None})
    hot = next(row for row in rows if row.kind == "hot")
    socket = next(row for row in rows if row.kind == "socket")
    split_us = 150.0
    low_limits = _low_latency_limits(rows, split_us)
    high_limit = max(split_us * 1.5, max(row.p99_us for row in rows) * 1.6)

    for panel_index, (cpu_metric, cpu_label) in enumerate(panels):
        panel_grid = outer_grid[panel_index].subgridspec(
            2,
            1,
            height_ratios=(3.1, 1.45),
            hspace=0.05,
        )
        top_axis = fig.add_subplot(panel_grid[0])
        bottom_axis = fig.add_subplot(panel_grid[1], sharex=top_axis)
        panel_axes = (top_axis, bottom_axis)

        for row in idle_rows:
            assert row.bud is not None and row.sleep_us is not None
            cpu_value = getattr(row, cpu_metric)
            color = _bud_color(row.bud)
            for axis in panel_axes:
                axis.scatter(
                    cpu_value,
                    row.p99_us,
                    s=52,
                    color=color,
                    marker="o",
                    edgecolors="white",
                    linewidths=0.7,
                    zorder=3,
                )
            offset_x, offset_y, alignment = _point_label_position(row, cpu_value)
            label_axis = bottom_axis if row.p99_us < split_us else top_axis
            label_axis.annotate(
                _point_label(row),
                (cpu_value, row.p99_us),
                xytext=(offset_x, offset_y),
                textcoords="offset points",
                ha=alignment,
                fontsize=6.5,
                color="#4a4a4a",
            )

        bottom_axis.scatter(
            getattr(hot, cpu_metric),
            hot.p99_us,
            s=140,
            color=BASELINE_STYLES["hot"][0],
            marker=BASELINE_STYLES["hot"][1],
            edgecolors="white",
            linewidths=0.8,
            zorder=4,
        )
        bottom_axis.annotate(
            "HOT",
            (getattr(hot, cpu_metric), hot.p99_us),
            xytext=(-4, -12),
            textcoords="offset points",
            ha="right",
            fontsize=7,
            color=BASELINE_STYLES["hot"][0],
        )

        socket_cpu = getattr(socket, cpu_metric)
        for axis in panel_axes:
            axis.axvline(
                socket_cpu,
                color=BASELINE_STYLES["socket"][0],
                linestyle="--",
                linewidth=1.0,
                alpha=0.75,
                zorder=1,
            )
        bottom_axis.axhline(
            socket.p99_us,
            color=BASELINE_STYLES["socket"][0],
            linestyle="--",
            linewidth=1.0,
            alpha=0.75,
            zorder=1,
        )
        bottom_axis.scatter(
            socket_cpu,
            socket.p99_us,
            s=90,
            color=BASELINE_STYLES["socket"][0],
            marker=BASELINE_STYLES["socket"][1],
            edgecolors="white",
            linewidths=0.8,
            zorder=5,
        )
        bottom_axis.annotate(
            "TCP Socket",
            (socket_cpu, socket.p99_us),
            xytext=(6, 5),
            textcoords="offset points",
            ha="left",
            va="bottom",
            fontsize=7.5,
            color=BASELINE_STYLES["socket"][0],
        )

        frontier = pareto_frontier(
            rows,
            x=cpu_metric,  # type: ignore[arg-type]
            y="p99_us",
        )
        for axis in panel_axes:
            axis.plot(
                [getattr(row, cpu_metric) for row in frontier],
                [row.p99_us for row in frontier],
                color="#555555",
                linewidth=1.1,
                alpha=0.75,
                zorder=2,
            )
            axis.set_xscale("log")
            axis.grid(True, which="both", linewidth=0.5, alpha=0.25)

        top_axis.set_yscale("log")
        top_axis.set_ylim(split_us, high_limit)
        bottom_axis.set_ylim(*low_limits)
        cpu_values = [getattr(row, cpu_metric) for row in rows]
        bottom_axis.set_xlim(min(cpu_values) * 0.75, max(cpu_values) * 1.4)
        bottom_axis.set_xlabel(f"{cpu_label} (%) - lower is better")
        top_axis.set_ylabel("RPC p99 (us) - lower is better")
        top_axis.set_title(f"{cpu_label} / p99 trade-off")
        _draw_y_axis_break(top_axis, bottom_axis)

    fig.suptitle(f"Poll-idle CPU / RPC p99 trade-off\n{EXPERIMENT_LABEL}")
    pareto_handle = Line2D(
        [0],
        [0],
        color="#555555",
        linewidth=1.1,
        label="Pareto frontier",
    )
    main_baseline_handles = [
        Line2D(
            [0],
            [0],
            color=BASELINE_STYLES["hot"][0],
            marker=BASELINE_STYLES["hot"][1],
            linestyle="none",
            markersize=9,
            label="HOT POLL",
        ),
        Line2D(
            [0],
            [0],
            color=BASELINE_STYLES["socket"][0],
            marker=BASELINE_STYLES["socket"][1],
            linestyle="--",
            linewidth=1.0,
            markersize=6,
            label="TCP Socket",
        ),
    ]
    fig.legend(
        handles=(
            _bud_point_legend_handles(buds)
            + main_baseline_handles
            + [pareto_handle]
        ),
        loc="outside lower center",
        ncol=4,
        frameon=False,
    )
    return _save_figure(fig, output_dir, "01_cpu_latency_pareto")


def latency_axis_windows(
    metric: Literal["p50_us", "p99_us", "p999_us"],
) -> tuple[tuple[float, float], tuple[float, float]]:
    windows = {
        "p50_us": ((30.0, 75.0), (120.0, 12_000.0)),
        "p99_us": ((98.5, 100.0), (170.0, 30_000.0)),
        "p999_us": ((95.0, 190.0), (195.0, 30_000.0)),
    }
    return windows[metric]


def _plot_latency_by_sleep(rows: Sequence[Result], output_dir: Path) -> list[Path]:
    fig = plt.figure(figsize=(17, 7.2), layout="constrained")
    outer_grid = fig.add_gridspec(1, 3, wspace=0.12)
    metrics = (
        ("p50_us", "RPC p50 (us)"),
        ("p99_us", "RPC p99 (us)"),
        ("p999_us", "RPC p99.9 (us; exploratory)"),
    )
    idle_rows = [row for row in rows if row.kind == "idle"]
    buds = sorted({row.bud for row in idle_rows if row.bud is not None})
    hot = next(row for row in rows if row.kind == "hot")
    socket = next(row for row in rows if row.kind == "socket")

    for panel_index, (metric, ylabel) in enumerate(metrics):
        panel_grid = outer_grid[panel_index].subgridspec(
            2,
            1,
            height_ratios=(1.0, 1.35),
            hspace=0.05,
        )
        context_axis = fig.add_subplot(panel_grid[0])
        focus_axis = fig.add_subplot(panel_grid[1], sharex=context_axis)

        for bud in buds:
            series = sorted(
                (row for row in idle_rows if row.bud == bud),
                key=lambda row: row.sleep_us or 0,
            )
            x_values = [row.sleep_us for row in series]
            y_values = [getattr(row, metric) for row in series]
            color = _bud_color(bud)
            context_axis.plot(
                x_values,
                y_values,
                color=color,
                linewidth=1.2,
                alpha=0.5,
            )
            context_axis.scatter(
                x_values,
                y_values,
                color=color,
                edgecolors="white",
                linewidths=0.7,
                alpha=0.65,
                zorder=3,
            )
            focus_axis.plot(
                x_values,
                y_values,
                color=color,
                linewidth=1.5,
                alpha=0.95,
            )
            focus_axis.scatter(
                x_values,
                y_values,
                color=color,
                edgecolors="white",
                linewidths=0.7,
                zorder=3,
            )

        hot_value = getattr(hot, metric)
        socket_value = getattr(socket, metric)
        focus_axis.axhspan(
            min(hot_value, socket_value),
            max(hot_value, socket_value),
            color=BASELINE_STYLES["socket"][0],
            alpha=0.08,
            zorder=0,
        )
        for baseline in (hot, socket):
            _annotate_reference_line(
                focus_axis,
                baseline,
                metric,
                offset_points=4 if baseline.kind == "socket" else -10,
            )

        focus_limits, context_limits = latency_axis_windows(metric)
        for axis in (context_axis, focus_axis):
            axis.set_xscale("log")
            axis.grid(True, which="both", linewidth=0.5, alpha=0.25)
        context_axis.set_yscale("log")
        context_axis.set_ylim(*context_limits)
        focus_axis.set_ylim(*focus_limits)
        focus_axis.set_xlabel("Configured SLEEP (us)")
        focus_axis.set_ylabel(ylabel)
        _draw_y_axis_break(context_axis, focus_axis)

    fig.suptitle(f"Latency response to configured SLEEP\n{EXPERIMENT_LABEL}")
    fig.legend(
        handles=_bud_legend_handles(buds) + _baseline_legend_handles(hot_as_point=False),
        loc="outside lower center",
        ncol=4,
        frameon=False,
    )
    return _save_figure(fig, output_dir, "02_latency_by_sleep")


def _plot_cpu_by_sleep(rows: Sequence[Result], output_dir: Path) -> list[Path]:
    fig, axes = plt.subplots(1, 2, figsize=(13, 5.5), constrained_layout=True)
    metrics = (
        ("client_cpu_pct", "Client CPU (%)"),
        ("server_cpu_pct", "Server CPU (%)"),
    )
    idle_rows = [row for row in rows if row.kind == "idle"]
    buds = sorted({row.bud for row in idle_rows if row.bud is not None})
    baselines = [row for row in rows if row.kind != "idle"]

    for axis, (metric, ylabel) in zip(axes, metrics):
        for bud in buds:
            series = sorted(
                (row for row in idle_rows if row.bud == bud),
                key=lambda row: row.sleep_us or 0,
            )
            color = _bud_color(bud)
            axis.plot(
                [row.sleep_us for row in series],
                [getattr(row, metric) for row in series],
                color=color,
                marker="o",
                linewidth=1.5,
                markersize=5,
            )
        for baseline in baselines:
            _annotate_reference_line(
                axis,
                baseline,
                metric,
                offset_points=4 if baseline.kind == "socket" else -10,
            )
        axis.set_xscale("log")
        axis.set_yscale("log")
        axis.set_xlabel("Configured SLEEP (us)")
        axis.set_ylabel(ylabel)
        axis.grid(True, which="both", linewidth=0.5, alpha=0.25)

    fig.suptitle(f"CPU response to configured SLEEP\n{EXPERIMENT_LABEL}")
    fig.legend(
        handles=_bud_legend_handles(buds) + _baseline_legend_handles(hot_as_point=False),
        loc="outside lower center",
        ncol=4,
        frameon=False,
    )
    return _save_figure(fig, output_dir, "03_cpu_by_sleep")


def _format_cell(value: float) -> str:
    if value >= 1000:
        return f"{value / 1000:.1f}k"
    if value >= 100:
        return f"{value:.0f}"
    return f"{value:.1f}"


def _plot_heatmaps(rows: Sequence[Result], output_dir: Path) -> list[Path]:
    idle_rows = [row for row in rows if row.kind == "idle"]
    buds = sorted({row.bud for row in idle_rows if row.bud is not None})
    sleeps = sorted(
        {row.sleep_us for row in idle_rows if row.sleep_us is not None}
    )
    lookup = {(row.bud, row.sleep_us): row for row in idle_rows}
    metrics = (
        ("p50_us", "RPC p50 (us)"),
        ("p99_us", "RPC p99 (us)"),
        ("p999_us", "RPC p99.9 (us)"),
        ("server_cpu_pct", "Server CPU (%)"),
    )
    fig, axes = plt.subplots(2, 2, figsize=(13, 9), constrained_layout=True)

    for axis, (metric, title) in zip(axes.flat, metrics):
        values = [
            [getattr(lookup[(bud, sleep)], metric) for sleep in sleeps]
            for bud in buds
        ]
        flat_values = [value for row in values for value in row]
        norm = LogNorm(vmin=min(flat_values), vmax=max(flat_values))
        image = axis.imshow(values, cmap="viridis", norm=norm, aspect="auto")
        axis.set_title(title)
        axis.set_xlabel("Configured SLEEP")
        axis.set_ylabel("Empty-poll BUD")
        axis.set_xticks(
            range(len(sleeps)), labels=[_format_sleep(value) for value in sleeps]
        )
        axis.set_yticks(range(len(buds)), labels=[str(value) for value in buds])
        for row_index, bud in enumerate(buds):
            for column_index, sleep in enumerate(sleeps):
                result = lookup[(bud, sleep)]
                value = getattr(result, metric)
                normalized = norm(value)
                text_color = "white" if normalized < 0.35 or normalized > 0.8 else "black"
                label = _format_cell(value)
                axis.text(
                    column_index,
                    row_index,
                    label,
                    ha="center",
                    va="center",
                    fontsize=8,
                    color=text_color,
                )
        fig.colorbar(image, ax=axis, shrink=0.82)

    fig.suptitle(f"BUD / SLEEP response matrix\n{EXPERIMENT_LABEL}")
    return _save_figure(fig, output_dir, "04_parameter_heatmaps")


def generate_figures(rows: Sequence[Result], output_dir: Path) -> list[Path]:
    outputs = []
    outputs.extend(_plot_cpu_latency(rows, output_dir))
    outputs.extend(_plot_latency_by_sleep(rows, output_dir))
    outputs.extend(_plot_cpu_by_sleep(rows, output_dir))
    outputs.extend(_plot_heatmaps(rows, output_dir))
    return outputs


def main(argv: Sequence[str] | None = None) -> int:
    project_dir = Path(__file__).resolve().parent.parent
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--input",
        type=Path,
        default=project_dir / "data" / "poisson_exp_350qps_unary64.csv",
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=project_dir / "figures",
    )
    args = parser.parse_args(argv)

    rows = load_results(args.input)
    for output in generate_figures(rows, args.output_dir):
        print(output)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
