from pathlib import Path

import poll_idle_analysis as analysis

from poll_idle_analysis import (
    Result,
    best_idle_under_socket,
    generate_figures,
    load_results,
    pareto_frontier,
)


def make_result(name: str, cpu: float, p99: float, *, valid: bool = True) -> Result:
    return Result(
        name=name,
        kind="idle",
        bud=0,
        sleep_us=1.0,
        p50_us=p99,
        p99_us=p99,
        p999_us=p99,
        max_us=p99,
        client_cpu_pct=cpu,
        server_cpu_pct=cpu,
        drain_timeout=not valid,
        drain_remaining=0 if valid else 1,
    )


def test_pareto_frontier_treats_drain_timeout_as_a_normal_point() -> None:
    rows = [
        make_result("lowest-cpu", 1.0, 500.0),
        make_result("knee", 5.0, 100.0),
        make_result("dominated", 6.0, 120.0),
        make_result("lowest-latency", 20.0, 90.0),
        make_result("drain-timeout", 0.5, 600.0, valid=False),
    ]

    frontier = pareto_frontier(rows, x="server_cpu_pct", y="p99_us")

    assert [row.name for row in frontier] == [
        "drain-timeout",
        "lowest-cpu",
        "knee",
        "lowest-latency",
    ]


def test_load_results_parses_baselines_and_drain_status(tmp_path: Path) -> None:
    csv_path = tmp_path / "results.csv"
    csv_path.write_text(
        "name,kind,bud,sleep_us,p50_us,p99_us,p999_us,max_us,"
        "client_cpu_pct,server_cpu_pct,drain_timeout,drain_remaining\n"
        "HOT POLL,hot,,,32.35,98.67,99.89,200,100.25,97.75,false,0\n"
        "IDLE BUD=0 / 10ms,idle,0,10000,9816.35,18614,19768,20100,"
        "1.15,0.55,true,5\n",
        encoding="utf-8",
    )

    rows = load_results(csv_path)

    assert rows[0].bud is None
    assert rows[0].sleep_us is None
    assert rows[1].sleep_us == 10_000
    assert rows[1].drain_timeout is True
    assert rows[1].drain_remaining == 5


def test_best_idle_under_socket_minimizes_the_selected_cpu_metric() -> None:
    rows = [
        Result(
            **{
                **make_result("Socket", 1.0, 100.0).__dict__,
                "kind": "socket",
                "bud": None,
                "sleep_us": None,
            }
        ),
        Result(
            **{
                **make_result("lower-client-higher-server", 10.0, 99.0).__dict__,
                "server_cpu_pct": 30.0,
            }
        ),
        Result(
            **{
                **make_result("higher-client-lower-server", 20.0, 98.0).__dict__,
                "server_cpu_pct": 8.0,
            }
        ),
        make_result("too-slow", 2.0, 101.0),
    ]

    client_best = best_idle_under_socket(
        rows,
        metric="p99_us",
        cpu_metric="client_cpu_pct",
    )
    server_best = best_idle_under_socket(
        rows,
        metric="p99_us",
        cpu_metric="server_cpu_pct",
    )

    assert client_best.name == "lower-client-higher-server"
    assert server_best.name == "higher-client-lower-server"


def test_latency_axis_windows_prioritize_the_hot_to_socket_budget() -> None:
    assert analysis.latency_axis_windows("p50_us") == (
        (30.0, 75.0),
        (120.0, 12_000.0),
    )
    assert analysis.latency_axis_windows("p99_us") == (
        (98.5, 100.0),
        (170.0, 30_000.0),
    )
    assert analysis.latency_axis_windows("p999_us") == (
        (95.0, 190.0),
        (195.0, 30_000.0),
    )


def test_generate_figures_writes_png_and_svg_files(tmp_path: Path) -> None:
    rows = [
        make_result("B0 / 1us", 40.0, 100.0),
        Result(
            **{
                **make_result("B0 / 10us", 10.0, 110.0).__dict__,
                "sleep_us": 10.0,
            }
        ),
        Result(
            **{
                **make_result("B16 / 1us", 80.0, 99.0).__dict__,
                "bud": 16,
            }
        ),
        Result(
            **{
                **make_result("B16 / 10us", 30.0, 105.0).__dict__,
                "bud": 16,
                "sleep_us": 10.0,
            }
        ),
        Result(
            **{
                **make_result("HOT", 100.0, 98.0).__dict__,
                "kind": "hot",
                "bud": None,
                "sleep_us": None,
            }
        ),
        Result(
            **{
                **make_result("Socket", 1.0, 101.0).__dict__,
                "kind": "socket",
                "bud": None,
                "sleep_us": None,
            }
        ),
    ]

    outputs = generate_figures(rows, tmp_path)

    assert {path.name for path in outputs} == {
        "01_cpu_latency_pareto.png",
        "01_cpu_latency_pareto.svg",
        "02_latency_by_sleep.png",
        "02_latency_by_sleep.svg",
        "03_cpu_by_sleep.png",
        "03_cpu_by_sleep.svg",
        "04_parameter_heatmaps.png",
        "04_parameter_heatmaps.svg",
    }
    assert all(path.stat().st_size > 1_000 for path in outputs)

    tradeoff_svg = (tmp_path / "01_cpu_latency_pareto.svg").read_text(
        encoding="utf-8"
    )
    assert all(line == line.rstrip() for line in tradeoff_svg.splitlines())
    assert "Client CPU (%)" in tradeoff_svg
    assert "Server CPU (%)" in tradeoff_svg
    assert "RPC p99 (us)" in tradeoff_svg
    assert "p99.9 trade-off" not in tradeoff_svg
    assert "B0/1us" in tradeoff_svg
    assert "B16/10us" in tradeoff_svg
    assert "Pareto frontier" in tradeoff_svg
    assert tradeoff_svg.count("TCP Socket") == 3
    assert "lowest CPU under Socket p99" not in tradeoff_svg
    assert "Poisson arrivals | 350 QPS | payload: 64 B | handler: echo" in tradeoff_svg
    assert "Unary64" not in tradeoff_svg
    assert "single run" not in tradeoff_svg
    assert "n=5,285" not in tradeoff_svg
