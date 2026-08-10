from pathlib import Path

import pytest

from netpoll_gqm_latency_concurrency_analysis import (
    derive_measurements,
    generate_figures,
    load_measurements,
    write_derived_csv,
)


PROJECT_DIR = Path(__file__).resolve().parent.parent
DATA_PATH = (
    PROJECT_DIR
    / "data"
    / "netpoll_gqm_latency_concurrency_raw_20260810.csv"
)


def test_raw_archive_contains_the_complete_35_cell_matrix() -> None:
    rows = load_measurements(DATA_PATH)

    assert len(rows) == 35
    assert {row.concurrency for row in rows} == {1, 4, 16, 64, 128}
    assert {row.cost_ns for row in rows} == {0, 84, 168, 335, 670, 1340, 3350}
    assert {row.body_bytes for row in rows} == {1024}
    assert {row.target_qps for row in rows} == {0}
    assert {row.retry for row in rows} == {1}
    assert {row.status for row in rows} == {"OK"}


def test_derived_metrics_use_each_concurrency_zero_cost_cell_as_baseline() -> None:
    rows = derive_measurements(load_measurements(DATA_PATH))

    c16_84ns = next(
        row for row in rows if row.concurrency == 16 and row.cost_ns == 84
    )
    c128_3350ns = next(
        row for row in rows if row.concurrency == 128 and row.cost_ns == 3350
    )
    c64_zero = next(
        row for row in rows if row.concurrency == 64 and row.cost_ns == 0
    )

    assert c16_84ns.cost_multiplier == pytest.approx(0.25)
    assert c16_84ns.tps_retention_pct == pytest.approx(86.59, abs=0.01)
    assert c128_3350ns.tps_retention_pct == pytest.approx(27.55, abs=0.01)
    assert c128_3350ns.tp99_amplification == pytest.approx(2.930, abs=0.001)
    assert c64_zero.tps_vs_c16_pct == pytest.approx(112.09, abs=0.01)
    assert c64_zero.tp99_vs_c16_pct == pytest.approx(285.71, abs=0.01)


def test_generation_emits_derived_csv_and_two_separate_academic_figures(
    tmp_path: Path,
) -> None:
    derived = derive_measurements(load_measurements(DATA_PATH))
    csv_path = tmp_path / "derived.csv"
    write_derived_csv(derived, csv_path)
    outputs = generate_figures(derived, tmp_path)

    assert csv_path.stat().st_size > 2_000
    assert b"\r\n" not in csv_path.read_bytes()
    assert {path.name for path in outputs} == {
        "01_gqm_latency_sensitivity_by_concurrency.png",
        "01_gqm_latency_sensitivity_by_concurrency.svg",
        "02_concurrency_scaling_under_gqm_latency.png",
        "02_concurrency_scaling_under_gqm_latency.svg",
    }
    assert all(path.stat().st_size > 1_000 for path in outputs)

    sensitivity_svg = (
        tmp_path / "01_gqm_latency_sensitivity_by_concurrency.svg"
    ).read_text(encoding="utf-8")
    scaling_svg = (
        tmp_path / "02_concurrency_scaling_under_gqm_latency.svg"
    ).read_text(encoding="utf-8")

    assert "Netpoll GQM-Latency Sensitivity" in sensitivity_svg
    assert "TPS retained" in sensitivity_svg
    assert "TP99 amplification" in sensitivity_svg
    assert "Concurrency = 128" in sensitivity_svg
    assert "Concurrency Scaling Under Added GQM Latency" in scaling_svg
    assert "TPS (Kops/s)" in scaling_svg
    assert "TP99 (µs)" in scaling_svg
    assert "10× (3350 ns)" in scaling_svg
    assert all(line == line.rstrip() for line in sensitivity_svg.splitlines())
    assert all(line == line.rstrip() for line in scaling_svg.splitlines())
