from pathlib import Path

import pytest

from netpoll_socket_payload_analysis import (
    derive_measurements,
    generate_figures,
    load_measurements,
    write_derived_csv,
)


PROJECT_DIR = Path(__file__).resolve().parent.parent
DATA_PATH = PROJECT_DIR / "data" / "netpoll_socket_baseline_raw_20260810.csv"


def test_raw_archive_preserves_the_complete_63_cell_matrix() -> None:
    rows = load_measurements(DATA_PATH)

    assert len(rows) == 63
    assert {row.payload_bytes for row in rows} == {0, 84, 168, 335, 670, 1340, 3350}
    assert {row.concurrency for row in rows} == {1, 4, 16, 64, 128, 256, 512, 1024, 2048}
    assert {row.target_qps for row in rows} == {0}
    assert {row.cost_reported for row in rows} == {"-"}
    assert {row.retry for row in rows} == {1}
    assert {row.status for row in rows} == {"OK"}

    assert next(row for row in rows if row.payload_bytes == 0 and row.concurrency == 256).tps == 479_900
    end = next(row for row in rows if row.payload_bytes == 3350 and row.concurrency == 2048)
    assert end.tp99_us == 15_110
    assert end.tp999_us == 20_310
    assert end.client_rss_kb == 649_216


def test_derived_metrics_capture_peak_retention_and_concurrency_cost() -> None:
    rows = derive_measurements(load_measurements(DATA_PATH))

    c64_1340 = next(row for row in rows if row.payload_bytes == 1340 and row.concurrency == 64)
    c128_zero = next(row for row in rows if row.payload_bytes == 0 and row.concurrency == 128)
    c64_3350 = next(row for row in rows if row.payload_bytes == 3350 and row.concurrency == 64)

    assert c64_1340.payload_peak_tps == 361_300
    assert c64_1340.tps_vs_payload_peak_pct == pytest.approx(98.09, abs=0.01)
    assert c128_zero.tps_gain_vs_previous_concurrency_pct == pytest.approx(4.64, abs=0.01)
    assert c128_zero.tp99_change_vs_previous_concurrency_pct == pytest.approx(62.07, abs=0.01)
    assert c64_3350.tps_retention_vs_zero_payload_pct == pytest.approx(36.52, abs=0.01)


def test_generation_emits_lf_csv_and_two_complete_figures(tmp_path: Path) -> None:
    derived = derive_measurements(load_measurements(DATA_PATH))
    csv_path = tmp_path / "derived.csv"
    write_derived_csv(derived, csv_path)
    outputs = generate_figures(derived, tmp_path)

    assert b"\r\n" not in csv_path.read_bytes()
    assert {path.name for path in outputs} == {
        "01_socket_payload_concurrency_surface.png",
        "01_socket_payload_concurrency_surface.svg",
        "02_socket_concurrency_scaling_by_payload.png",
        "02_socket_concurrency_scaling_by_payload.svg",
    }
    assert all(path.stat().st_size > 1_000 for path in outputs)

    surface = (tmp_path / "01_socket_payload_concurrency_surface.svg").read_text(encoding="utf-8")
    scaling = (tmp_path / "02_socket_concurrency_scaling_by_payload.svg").read_text(encoding="utf-8")
    assert "Netpoll Socket Baseline Surface" in surface
    assert "Throughput (Kops/s)" in surface
    assert "TP99 (µs)" in surface
    assert "Netpoll Socket Concurrency Scaling" in scaling
    assert "Payload = 3350 B" in scaling
    assert all(line == line.rstrip() for line in surface.splitlines())
    assert all(line == line.rstrip() for line in scaling.splitlines())
