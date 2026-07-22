from pathlib import Path

import pytest

from gqm_delay_analysis import (
    generate_figures,
    latency_cliff_interval,
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


def test_generate_figures_writes_expected_png_and_svg_files(tmp_path: Path) -> None:
    baseline_rows = load_rpc_results(DATA_DIR / "rpc_baseline_qps_sweep.csv")
    delay_rows = load_rpc_results(DATA_DIR / "gqm_delay_sweep_35000qps.csv")
    micro_rows = load_micro_results(DATA_DIR / "gqm_microbenchmark.csv")

    outputs = generate_figures(baseline_rows, delay_rows, micro_rows, tmp_path)

    assert {path.name for path in outputs} == {
        "01_rpc_saturation_knee.png",
        "01_rpc_saturation_knee.svg",
        "02_gqm_delay_sensitivity.png",
        "02_gqm_delay_sensitivity.svg",
        "03_gqm_delay_capacity.png",
        "03_gqm_delay_capacity.svg",
        "04_microbenchmark_calibration.png",
        "04_microbenchmark_calibration.svg",
    }
    assert all(path.stat().st_size > 1_000 for path in outputs)

    sensitivity_svg = (tmp_path / "02_gqm_delay_sensitivity.svg").read_text(
        encoding="utf-8"
    )
    assert "Observed cliff interval" in sensitivity_svg
    assert "single run" in sensitivity_svg
    assert all(line == line.rstrip() for line in sensitivity_svg.splitlines())
