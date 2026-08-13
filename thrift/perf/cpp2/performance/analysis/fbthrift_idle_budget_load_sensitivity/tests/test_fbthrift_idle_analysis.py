from dataclasses import replace
from pathlib import Path

import pytest

from fbthrift_idle_analysis import (
    build_policy_boundaries,
    build_tradeoff_candidates,
    derive_measurements,
    generate_figures,
    load_measurements,
    load_socket_measurements,
    summarize_by_load,
    write_derived_csv,
    write_policy_boundaries_csv,
    write_summary_csv,
    write_tradeoff_candidates_csv,
)


PROJECT_DIR = Path(__file__).resolve().parent.parent
DATA_PATH = PROJECT_DIR / "data" / "fbthrift_idle_budget_load_sensitivity.csv"
SOCKET_PATH = PROJECT_DIR / "data" / "fbthrift_socket_qps_baseline.csv"


def test_archive_preserves_80_unique_ubmem_cells() -> None:
    rows = load_measurements(DATA_PATH)

    assert len(rows) == 80
    assert len(
        {(r.target_qps, r.idle_enabled, r.sleep_us, r.bud) for r in rows}
    ) == 80
    assert {r.target_qps for r in rows} == {1_600, 16_000, 40_000, 80_000, 152_000}
    assert {r.bud for r in rows} == {0, 1, 16, 256}
    assert {(r.idle_enabled, r.sleep_us) for r in rows} == {
        (False, 0),
        (True, 1),
        (True, 10),
        (True, 100),
    }


def test_selected_ubmem_rows_match_the_supplied_table() -> None:
    rows = load_measurements(DATA_PATH)
    first = next(row for row in rows if row.source_index == 1)
    middle = next(row for row in rows if row.source_index == 41)
    last = next(row for row in rows if row.source_index == 80)

    assert (
        first.target_qps,
        first.idle_enabled,
        first.bud,
        first.p99_us,
        first.client_cpu_pct,
        first.server_cpu_pct,
    ) == (1_600, False, 0, pytest.approx(224.0), pytest.approx(399.3), pytest.approx(368.9))
    assert (middle.target_qps, middle.sleep_us, middle.bud) == (40_000, 10, 0)
    assert (middle.reported_sustain_pct, middle.p99_us) == (
        pytest.approx(100.0),
        pytest.approx(225.9),
    )
    assert (last.target_qps, last.sleep_us, last.bud, last.shed) == (
        152_000,
        100,
        256,
        2_482,
    )


def test_socket_archive_preserves_five_load_anchors() -> None:
    rows = load_socket_measurements(SOCKET_PATH)

    assert [(r.target_qps, r.reported_sustain_pct) for r in rows] == [
        (1_600, 100.0),
        (16_000, 100.0),
        (40_000, 100.0),
        (80_000, 99.0),
        (152_000, 99.6),
    ]
    assert [r.p99_us for r in rows] == pytest.approx(
        [248.4, 199.8, 278.9, 466.8, 12_123]
    )


def test_derived_metrics_use_reported_sustain_and_two_endpoint_cpu() -> None:
    rows = derive_measurements(load_measurements(DATA_PATH))
    point = next(row for row in rows if row.source_index == 1)

    assert point.total_cpu_pct == pytest.approx(768.2)
    assert point.used_cores == pytest.approx(7.682)
    assert point.quota_occupancy_pct == pytest.approx(7.682 / 12 * 100)
    assert point.qps_per_used_core == pytest.approx(1_600 / 7.682)
    assert point.target_sustained is True


def test_reported_sustain_controls_socket_gate() -> None:
    rows = load_socket_measurements(SOCKET_PATH)

    assert [row.used_cores for row in rows] == pytest.approx(
        [0.077, 0.600, 1.387, 2.423, 3.871]
    )
    assert [row.target_sustained for row in rows] == [
        True,
        True,
        True,
        False,
        True,
    ]


def test_disabled_idle_rows_are_archived_but_not_presentation_candidates() -> None:
    candidates = build_tradeoff_candidates(
        derive_measurements(load_measurements(DATA_PATH)),
        load_socket_measurements(SOCKET_PATH),
    )

    assert len(candidates) == 65
    assert sum(row.transport == "Ubmem" for row in candidates) == 60
    assert sum(row.transport == "Socket" for row in candidates) == 5
    assert all(row.idle_enabled is not False for row in candidates)
    assert {row.repeat_count for row in candidates} == {1}


def test_load_summary_preserves_gate_counts_and_ranges() -> None:
    summary = summarize_by_load(derive_measurements(load_measurements(DATA_PATH)))

    assert [(row.target_qps, row.sustained_cells) for row in summary] == [
        (1_600, 12),
        (16_000, 12),
        (40_000, 12),
        (80_000, 12),
        (152_000, 9),
    ]
    assert {row.total_cells for row in summary} == {12}
    high = summary[-1]
    assert high.max_achieved_qps == pytest.approx(152_200)
    assert (high.min_p99_us, high.max_p99_us) == pytest.approx((1_633, 18_471))


def test_policy_boundaries_include_all_measured_candidates_and_attainment() -> None:
    candidates = build_tradeoff_candidates(
        derive_measurements(load_measurements(DATA_PATH)),
        load_socket_measurements(SOCKET_PATH),
    )
    boundaries = build_policy_boundaries(candidates)

    by_id = {row.candidate_id: row for row in candidates}
    assert boundaries
    assert all(
        row.reported_sustain_pct
        == pytest.approx(by_id[row.candidate_id].reported_sustain_pct)
        for row in boundaries
    )
    assert {row.target_qps for row in boundaries} == {
        row.target_qps for row in candidates
    }

    unsustained = next(row for row in candidates if not row.target_sustained)
    forced_low_cpu = replace(
        unsustained,
        used_cores=0.001,
        total_cpu_pct=0.1,
    )
    modified = [
        forced_low_cpu if row.candidate_id == unsustained.candidate_id else row
        for row in candidates
    ]
    assert any(
        row.candidate_id == unsustained.candidate_id
        for row in build_policy_boundaries(modified)
    )


def test_policy_boundaries_are_left_closed_and_contiguous() -> None:
    candidates = build_tradeoff_candidates(
        derive_measurements(load_measurements(DATA_PATH)),
        load_socket_measurements(SOCKET_PATH),
    )
    boundaries = build_policy_boundaries(candidates)

    for target_qps in {row.target_qps for row in boundaries}:
        load = [row for row in boundaries if row.target_qps == target_qps]
        assert load[-1].max_p99_budget_us is None
        assert [row.max_p99_budget_us for row in load[:-1]] == [
            row.min_p99_budget_us for row in load[1:]
        ]
        assert all(row.quota_occupancy_pct == pytest.approx(row.used_cores / 12 * 100) for row in load)


def test_csv_writers_emit_complete_lf_terminated_outputs(tmp_path: Path) -> None:
    derived = derive_measurements(load_measurements(DATA_PATH))
    summary = summarize_by_load(derived)
    candidates = build_tradeoff_candidates(
        derived, load_socket_measurements(SOCKET_PATH)
    )
    boundaries = build_policy_boundaries(candidates)
    outputs = {
        "derived": tmp_path / "derived.csv",
        "summary": tmp_path / "summary.csv",
        "candidates": tmp_path / "candidates.csv",
        "boundaries": tmp_path / "boundaries.csv",
    }

    write_derived_csv(derived, outputs["derived"])
    write_summary_csv(summary, outputs["summary"])
    write_tradeoff_candidates_csv(candidates, outputs["candidates"])
    write_policy_boundaries_csv(boundaries, outputs["boundaries"])

    assert len(outputs["derived"].read_text().splitlines()) == 81
    assert len(outputs["summary"].read_text().splitlines()) == 6
    assert len(outputs["candidates"].read_text().splitlines()) == 66
    assert len(outputs["boundaries"].read_text().splitlines()) > 6
    assert all(b"\r\n" not in path.read_bytes() for path in outputs.values())
    joined = "\n".join(path.read_text() for path in outputs.values())
    assert "16100.000000000002" not in joined
    assert "4.672999999999999" not in joined


def test_figures_are_fbthrift_only_and_state_resource_semantics(
    tmp_path: Path,
) -> None:
    candidates = build_tradeoff_candidates(
        derive_measurements(load_measurements(DATA_PATH)),
        load_socket_measurements(SOCKET_PATH),
    )
    paths = generate_figures(candidates, build_policy_boundaries(candidates), tmp_path)

    assert {path.name for path in paths} == {
        "01_fbthrift_cpu_p99_tradeoff.png",
        "01_fbthrift_cpu_p99_tradeoff.svg",
        "02_fbthrift_p99_budget_minimum_cpu_boundary.png",
        "02_fbthrift_p99_budget_minimum_cpu_boundary.svg",
    }
    tradeoff_svg = (tmp_path / "01_fbthrift_cpu_p99_tradeoff.svg").read_text(
        encoding="utf-8"
    )
    boundary_svg = (
        tmp_path / "02_fbthrift_p99_budget_minimum_cpu_boundary.svg"
    ).read_text(encoding="utf-8")
    svg = f"{tradeoff_svg}\n{boundary_svg}"
    assert "fbthrift" in svg
    assert "Server: 8 vCPU" in svg
    assert "Client: 4 vCPU" in svg
    assert "Combined quota: 12 vCPU" in svg
    assert "Total CPU" in svg
    assert "P99 budget" in svg
    assert "Socket" in svg
    assert "Netpoll" not in svg
    for load_label in ("1% load", "10% load", "25% load", "50% load", "95% load"):
        assert load_label in svg
    assert svg.count("100% load = 160K QPS") == 2
    assert "1.6K target QPS" not in svg
    assert "152K target QPS" not in svg
    assert "Empty-poll budget (color)" in tradeoff_svg
    assert "Budget=256" in tradeoff_svg
    assert "Total CPU (vCPU-equivalents)" in tradeoff_svg
    assert "P99 latency (µs, log scale)" in tradeoff_svg
    assert "relative to Socket" not in tradeoff_svg
    assert "Attainment" in tradeoff_svg
    assert "Combined quota occupancy" not in tradeoff_svg
    assert "Target not sustained" not in tradeoff_svg
    assert "Pareto frontier" not in tradeoff_svg
    assert "Polling baseline" not in svg
    assert "gate passed" not in tradeoff_svg
    assert "gate failed" not in tradeoff_svg
    assert "min–max" not in tradeoff_svg
    assert "BUD=" not in svg
    assert "B16/" not in boundary_svg
    assert "Budget=16" not in boundary_svg
    assert "b16-s1us" in boundary_svg
    assert "b0-s100us" in boundary_svg
    assert "Labels use b&lt;budget&gt;-s&lt;sleep&gt;" in boundary_svg
    assert "Attainment" not in boundary_svg
    assert all(path.stat().st_size > 10_000 for path in paths)
