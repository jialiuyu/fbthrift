from pathlib import Path

import pytest

from netpoll_ubmem_payload_analysis import (
    derive_measurements,
    generate_figures,
    load_measurements,
    write_derived_csv,
)


PROJECT_DIR = Path(__file__).resolve().parent.parent
DATA_PATH = PROJECT_DIR / "data" / "netpoll_ubmem_baseline_raw_20260810.csv"


def test_raw_archive_preserves_63_cells_and_nine_partial_repeats() -> None:
    rows = load_measurements(DATA_PATH)

    assert len(rows) == 72
    assert {row.payload_bytes for row in rows} == {0, 84, 168, 335, 670, 1340, 3350}
    assert {row.concurrency for row in rows} == {1, 4, 16, 64, 128, 256, 512, 1024, 2048}
    assert {row.target_qps for row in rows} == {0}
    assert {row.cost_reported for row in rows} == {"0"}
    assert {row.retry for row in rows} == {1}
    assert {row.status for row in rows} == {"OK"}
    assert sum(row.repeat_id == 2 for row in rows) == 9
    assert {row.payload_bytes for row in rows if row.repeat_id == 2} == {0}

    assert next(row for row in rows if row.payload_bytes == 0 and row.concurrency == 256).tps == 970_900
    end = next(row for row in rows if row.payload_bytes == 3350 and row.concurrency == 2048)
    assert end.tp99_us == 32_920
    assert end.tp999_us == 50_570
    assert end.client_rss_kb == 1_041_408
    assert end.server_rss_kb == 718_848
    repeat_end = next(row for row in rows if row.repeat_id == 2 and row.concurrency == 2048)
    assert repeat_end.tps == 871_200
    assert repeat_end.tp99_us == 10_780
    assert repeat_end.tp999_us == 21_660


def test_derived_metrics_capture_peak_retention_and_concurrency_cost() -> None:
    rows = derive_measurements(load_measurements(DATA_PATH))

    assert len(rows) == 63

    c64_1340 = next(row for row in rows if row.payload_bytes == 1340 and row.concurrency == 64)
    c128_zero = next(row for row in rows if row.payload_bytes == 0 and row.concurrency == 128)
    c64_3350 = next(row for row in rows if row.payload_bytes == 3350 and row.concurrency == 64)

    c64_zero = next(row for row in rows if row.payload_bytes == 0 and row.concurrency == 64)

    assert c64_1340.payload_peak_tps == 787_000
    assert c64_1340.tps_vs_payload_peak_pct == pytest.approx(100.0)
    assert c64_zero.sample_count == 2
    assert c64_zero.tps == pytest.approx(954_350)
    assert c64_zero.tps_stddev == pytest.approx(2_899.14, abs=0.01)
    assert c128_zero.tps_gain_vs_previous_concurrency_pct == pytest.approx(1.90, abs=0.01)
    assert c128_zero.tp99_change_vs_previous_concurrency_pct == pytest.approx(116.67, abs=0.01)
    assert c64_3350.tps_retention_vs_zero_payload_pct == pytest.approx(73.06, abs=0.01)


def test_generation_emits_lf_csv_and_two_complete_figures(tmp_path: Path) -> None:
    derived = derive_measurements(load_measurements(DATA_PATH))
    csv_path = tmp_path / "derived.csv"
    write_derived_csv(derived, csv_path)
    outputs = generate_figures(derived, tmp_path)

    assert b"\r\n" not in csv_path.read_bytes()
    assert {path.name for path in outputs} == {
        "01_ubmem_payload_concurrency_surface.png",
        "01_ubmem_payload_concurrency_surface.svg",
        "02_ubmem_concurrency_scaling_by_payload.png",
        "02_ubmem_concurrency_scaling_by_payload.svg",
    }
    assert all(path.stat().st_size > 1_000 for path in outputs)
    surface = (tmp_path / "01_ubmem_payload_concurrency_surface.svg").read_text(encoding="utf-8")
    scaling = (tmp_path / "02_ubmem_concurrency_scaling_by_payload.svg").read_text(encoding="utf-8")
    assert "Netpoll Ubmem Baseline Surface" in surface
    assert "Throughput (Kops/s)" in surface
    assert "TP99 (µs)" in surface
    assert "Netpoll Ubmem Concurrency Scaling" in scaling
    assert "Payload = 3350 B" in scaling
    assert "Payload = 0 B (n=2)" in scaling
    assert all(line == line.rstrip() for line in surface.splitlines())
    assert all(line == line.rstrip() for line in scaling.splitlines())
