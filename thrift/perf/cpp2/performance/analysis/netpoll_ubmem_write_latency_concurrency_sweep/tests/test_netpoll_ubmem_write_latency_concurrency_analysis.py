from pathlib import Path

import pytest
from matplotlib.figure import Figure

from netpoll_ubmem_write_latency_concurrency_analysis import (
    _add_figure_heading,
    derive_measurements,
    generate_figures,
    load_measurements,
    write_derived_csv,
)


PROJECT_DIR = Path(__file__).resolve().parent.parent
DATA_PATH = (
    PROJECT_DIR
    / "data"
    / "netpoll_ubmem_write_latency_concurrency_raw_20260810.csv"
)


def test_figure_heading_keeps_payload_inside_layout_managed_suptitle() -> None:
    figure = Figure()

    _add_figure_heading(figure, "Experiment title")

    assert figure._suptitle is not None
    assert figure._suptitle.get_text() == (
        "Experiment title\nPayload: 1 KiB (1024 B)"
    )


def test_raw_archive_contains_the_complete_42_cell_matrix() -> None:
    rows = load_measurements(DATA_PATH)

    assert len(rows) == 42
    assert {row.concurrency for row in rows} == {1, 4, 16, 64, 256, 512}
    assert {row.write_cost_ns for row in rows} == {
        0,
        179,
        358,
        716,
        1432,
        2864,
        7160,
    }
    assert {row.body_bytes for row in rows} == {1024}
    assert {row.target_qps for row in rows} == {0}
    assert {row.cost_reported for row in rows} == {0}
    assert {row.retry for row in rows} == {1}
    assert {row.status for row in rows} == {"OK"}

    last = next(
        row
        for row in rows
        if row.concurrency == 512 and row.write_cost_ns == 7160
    )
    assert last.tps == pytest.approx(532_500)
    assert last.tp99_us == pytest.approx(2960)
    assert last.tp999_us == pytest.approx(12730)
    assert last.client_rss_kb == 332800
    assert last.server_rss_kb == 137216


def test_derived_metrics_use_same_concurrency_zero_cost_baseline() -> None:
    rows = derive_measurements(load_measurements(DATA_PATH))

    c64_1432ns = next(
        row
        for row in rows
        if row.concurrency == 64 and row.write_cost_ns == 1432
    )
    c1_2864ns = next(
        row
        for row in rows
        if row.concurrency == 1 and row.write_cost_ns == 2864
    )
    c256_7160ns = next(
        row
        for row in rows
        if row.concurrency == 256 and row.write_cost_ns == 7160
    )
    c16_716ns = next(
        row
        for row in rows
        if row.concurrency == 16 and row.write_cost_ns == 716
    )

    assert c16_716ns.cost_multiplier == pytest.approx(1.0)
    assert c64_1432ns.tps_retention_pct == pytest.approx(100.04, abs=0.01)
    assert c1_2864ns.tps_retention_pct == pytest.approx(73.37, abs=0.01)
    assert c256_7160ns.tps_retention_pct == pytest.approx(50.89, abs=0.01)
    assert c256_7160ns.tp99_amplification == pytest.approx(1.835, abs=0.001)


def test_generation_emits_derived_csv_and_two_separate_academic_figures(
    tmp_path: Path,
) -> None:
    derived = derive_measurements(load_measurements(DATA_PATH))
    csv_path = tmp_path / "derived.csv"
    write_derived_csv(derived, csv_path)
    outputs = generate_figures(derived, tmp_path)

    assert csv_path.stat().st_size > 3_000
    assert b"\r\n" not in csv_path.read_bytes()
    assert {path.name for path in outputs} == {
        "01_ubmem_write_latency_sensitivity_by_concurrency.png",
        "01_ubmem_write_latency_sensitivity_by_concurrency.svg",
        "02_concurrency_scaling_under_ubmem_write_latency.png",
        "02_concurrency_scaling_under_ubmem_write_latency.svg",
    }
    assert all(path.stat().st_size > 1_000 for path in outputs)

    sensitivity_svg = (
        tmp_path / "01_ubmem_write_latency_sensitivity_by_concurrency.svg"
    ).read_text(encoding="utf-8")
    scaling_svg = (
        tmp_path / "02_concurrency_scaling_under_ubmem_write_latency.svg"
    ).read_text(encoding="utf-8")

    assert "Ubmem Write-Latency Sensitivity" in sensitivity_svg
    assert "TPS retained" in sensitivity_svg
    assert "TP99 amplification" in sensitivity_svg
    assert "Concurrency = 512" in sensitivity_svg
    assert "Payload: 1 KiB (1024 B)" in sensitivity_svg
    assert "Concurrency Scaling Under Added Ubmem Write Latency" in scaling_svg
    assert "TPS (Kops/s)" in scaling_svg
    assert "TP99 (µs)" in scaling_svg
    assert "10× (7160 ns)" in scaling_svg
    assert "Payload: 1 KiB (1024 B)" in scaling_svg
    assert all(line == line.rstrip() for line in sensitivity_svg.splitlines())
    assert all(line == line.rstrip() for line in scaling_svg.splitlines())
