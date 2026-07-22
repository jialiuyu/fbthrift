from pathlib import Path

import pytest

from gqm_delay_analysis import (
    fit_cycle_sensitivity,
    generate_figures,
    latency_cliff_interval,
    load_fixed_outstanding_results,
    load_micro_results,
    load_rpc_results,
    normalized_micro_delta_ns,
)


PROJECT_DIR = Path(__file__).resolve().parent.parent
DATA_DIR = PROJECT_DIR / "data"


def test_baseline_rates_and_shed_are_derived_from_measured_counts() -> None:
    rows = load_rpc_results(DATA_DIR / "rpc_baseline_qps_sweep.csv")
    fifty_k = next(row for row in rows if row.target_qps == 50_000)

    assert fifty_k.scheduled_qps == pytest.approx(50_076.2, abs=0.1)
    assert fifty_k.completed_qps == pytest.approx(43_347.1, abs=0.1)
    assert fifty_k.shed_pct == pytest.approx(13.4378, abs=0.0001)


def test_delay_metadata_corrects_67us_multiplier_and_shed_rate() -> None:
    rows = load_rpc_results(DATA_DIR / "gqm_delay_sweep_35000qps.csv")
    sixty_seven_us = next(row for row in rows if row.inject_ns == 67_000)

    assert sixty_seven_us.multiplier == 200
    assert "corrected to 200x" in sixty_seven_us.source_label
    assert sixty_seven_us.shed_pct == pytest.approx(12.7358, abs=0.0001)


def test_p50_cliff_is_bracketed_by_13_4us_and_33_5us() -> None:
    rows = load_rpc_results(DATA_DIR / "gqm_delay_sweep_35000qps.csv")

    assert latency_cliff_interval(rows) == (13_400, 33_500)


def test_microbenchmark_delay_tracks_successful_wrapper_operations() -> None:
    rows = load_micro_results(DATA_DIR / "gqm_microbenchmark.csv")

    push = normalized_micro_delta_ns(rows, "HwSinglePushOnly")
    pop = normalized_micro_delta_ns(rows, "HwSinglePopOnly")
    ping_pong = normalized_micro_delta_ns(rows, "HwSinglePushPopPingPong")

    assert push[100] == pytest.approx(85.40, abs=0.01)
    assert push[1000] == pytest.approx(1009.76, abs=0.01)
    assert pop[100] == pytest.approx(95.04, abs=0.01)
    assert pop[1000] == pytest.approx(1024.11, abs=0.01)
    assert ping_pong[100] == pytest.approx(83.165, abs=0.001)
    assert ping_pong[1000] == pytest.approx(997.745, abs=0.001)


def test_cpp2_closed_loop_has_two_effective_injected_ops_on_critical_cycle() -> None:
    rows = load_fixed_outstanding_results(
        DATA_DIR / "cpp2_closed_loop_gqm_delay_sweep.csv"
    )
    baseline = rows[0]
    forty_x = next(row for row in rows if row.inject_ns == 13_400)
    two_hundred_x = next(row for row in rows if row.inject_ns == 67_000)
    fit = fit_cycle_sensitivity(rows)

    assert baseline.implied_cycle_us == pytest.approx(470.37, abs=0.01)
    assert forty_x.qps_retention_pct(baseline) == pytest.approx(94.214, abs=0.001)
    assert forty_x.latency_p99_us / baseline.latency_p99_us == pytest.approx(
        1.0626, abs=0.0001
    )
    assert two_hundred_x.implied_cycle_us == pytest.approx(602.05, abs=0.01)
    assert fit.slope_us_per_injected_us == pytest.approx(1.980, abs=0.001)
    assert fit.r_squared == pytest.approx(0.9988, abs=0.0001)


def test_va_flat_is_immediately_and_far_more_sensitive_than_cpp2_closed_loop() -> None:
    rows = load_fixed_outstanding_results(
        DATA_DIR / "va_flat_compressed_polling_gqm_delay_sweep.csv"
    )
    baseline = rows[0]
    one_x = next(row for row in rows if row.inject_ns == 335)
    two_x = next(row for row in rows if row.inject_ns == 670)
    forty_x = next(row for row in rows if row.inject_ns == 13_400)
    two_hundred_x = next(row for row in rows if row.inject_ns == 67_000)

    assert one_x.qps_retention_pct(baseline) == pytest.approx(96.129, abs=0.001)
    assert one_x.effective_exposure(baseline) == pytest.approx(239.97, abs=0.01)
    assert two_x.multiplier == 2
    assert "corrected to 2x" in two_x.source_label
    assert forty_x.qps_retention_pct(baseline) == pytest.approx(38.774, abs=0.001)
    assert forty_x.latency_p99_us / baseline.latency_p99_us == pytest.approx(
        2.6110, abs=0.0001
    )
    assert two_hundred_x.effective_exposure(baseline) == pytest.approx(
        308.51, abs=0.01
    )


def test_generate_figures_writes_expected_png_and_svg_files(tmp_path: Path) -> None:
    baseline_rows = load_rpc_results(DATA_DIR / "rpc_baseline_qps_sweep.csv")
    delay_rows = load_rpc_results(DATA_DIR / "gqm_delay_sweep_35000qps.csv")
    micro_rows = load_micro_results(DATA_DIR / "gqm_microbenchmark.csv")
    closed_loop_rows = load_fixed_outstanding_results(
        DATA_DIR / "cpp2_closed_loop_gqm_delay_sweep.csv"
    )
    va_flat_rows = load_fixed_outstanding_results(
        DATA_DIR / "va_flat_compressed_polling_gqm_delay_sweep.csv"
    )

    outputs = generate_figures(
        baseline_rows,
        delay_rows,
        micro_rows,
        closed_loop_rows,
        va_flat_rows,
        tmp_path,
    )

    assert {path.name for path in outputs} == {
        "01_rpc_saturation_knee.png",
        "01_rpc_saturation_knee.svg",
        "02_gqm_delay_sensitivity.png",
        "02_gqm_delay_sensitivity.svg",
        "03_gqm_delay_capacity.png",
        "03_gqm_delay_capacity.svg",
        "04_microbenchmark_calibration.png",
        "04_microbenchmark_calibration.svg",
        "05_cpp2_closed_loop_sensitivity.png",
        "05_cpp2_closed_loop_sensitivity.svg",
        "06_va_flat_compressed_polling_sensitivity.png",
        "06_va_flat_compressed_polling_sensitivity.svg",
        "07_fixed_outstanding_comparison.png",
        "07_fixed_outstanding_comparison.svg",
    }
    assert all(path.stat().st_size > 1_000 for path in outputs)

    sensitivity_svg = (tmp_path / "02_gqm_delay_sensitivity.svg").read_text(
        encoding="utf-8"
    )
    assert "Observed cliff interval" in sensitivity_svg
    assert "single run" in sensitivity_svg
    assert all(line == line.rstrip() for line in sensitivity_svg.splitlines())

    closed_loop_svg = (
        tmp_path / "05_cpp2_closed_loop_sensitivity.svg"
    ).read_text(encoding="utf-8")
    va_flat_svg = (
        tmp_path / "06_va_flat_compressed_polling_sensitivity.svg"
    ).read_text(encoding="utf-8")
    comparison_svg = (
        tmp_path / "07_fixed_outstanding_comparison.svg"
    ).read_text(encoding="utf-8")
    assert "1.98 us cycle / us injected" in closed_loop_svg
    assert "Effective injection exposure is not an operation count" in va_flat_svg
    assert "Same delay knob, very different RPC sensitivity" in comparison_svg

    for stem in (
        "02_gqm_delay_sensitivity",
        "03_gqm_delay_capacity",
        "05_cpp2_closed_loop_sensitivity",
        "06_va_flat_compressed_polling_sensitivity",
        "07_fixed_outstanding_comparison",
    ):
        svg = (tmp_path / f"{stem}.svg").read_text(encoding="utf-8")
        assert "Added GQM latency (1x ≈ 335 ns; symlog scale)" in svg
        assert "40x" in svg
        assert "Configured injected delay (us; symlog)" not in svg
