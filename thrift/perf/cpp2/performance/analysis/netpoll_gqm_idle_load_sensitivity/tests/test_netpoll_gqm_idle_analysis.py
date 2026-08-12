from pathlib import Path

import pytest

from netpoll_gqm_idle_analysis import (
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
DATA_PATH = PROJECT_DIR / "data" / "netpoll_gqm_idle_load_sensitivity.csv"
SOCKET_PATH = PROJECT_DIR / "data" / "netpoll_socket_qps_baseline.csv"


def test_archive_preserves_125_unique_configuration_keys() -> None:
    rows = load_measurements(DATA_PATH)

    assert len(rows) == 125
    assert len({(r.target_qps, r.bud, r.sleep_us) for r in rows}) == 125
    assert {r.target_qps for r in rows} == {
        7_700,
        77_000,
        192_500,
        385_000,
        731_500,
    }
    assert {r.bud for r in rows} == {0, 1, 16, 256, 1024}
    assert {r.sleep_us for r in rows} == {1, 10, 100, 1000, 10000}
    assert {r.concurrency for r in rows} == {128}
    assert {r.body_bytes for r in rows} == {1024}


def test_only_last_cell_is_partial() -> None:
    rows = load_measurements(DATA_PATH)
    partial = [row for row in rows if row.partial]

    assert [
        (r.source_index, r.target_qps, r.bud, r.sleep_us, r.tps)
        for r in partial
    ] == [(125, 731_500, 1024, 10_000, 704_900)]
    assert partial[0].tp99_us is None
    assert partial[0].client_cpu_pct is None
    assert partial[0].status is None


def test_selected_complete_cells_match_the_supplied_table() -> None:
    rows = load_measurements(DATA_PATH)
    first = next(row for row in rows if row.source_index == 1)
    middle = next(row for row in rows if row.source_index == 63)
    last_complete = next(row for row in rows if row.source_index == 124)

    assert (first.tps, first.tp99_us, first.client_cpu_pct, first.server_cpu_pct) == (
        7_700,
        pytest.approx(1200),
        pytest.approx(156.2),
        pytest.approx(172.5),
    )
    assert (middle.target_qps, middle.bud, middle.sleep_us) == (192_500, 16, 100)
    assert (middle.tps, middle.tp99_us) == (108_300, pytest.approx(1710))
    assert (
        last_complete.tps,
        last_complete.tp99_us,
        last_complete.server_cpu_pct,
    ) == (705_500, pytest.approx(420), pytest.approx(724.8))


def test_derived_metrics_use_target_attainment_and_two_endpoint_cpu() -> None:
    rows = derive_measurements(load_measurements(DATA_PATH))
    point = next(
        row
        for row in rows
        if (row.target_qps, row.bud, row.sleep_us) == (7_700, 1, 100)
    )

    assert point.attainment_pct == pytest.approx(100.0)
    assert point.total_cpu_pct == pytest.approx(21.7)
    assert point.used_cores == pytest.approx(0.217)
    assert point.qps_per_used_core == pytest.approx(7_700 / 0.217)
    assert point.target_sustained is True


def test_partial_cell_keeps_unavailable_derived_metrics_empty() -> None:
    rows = derive_measurements(load_measurements(DATA_PATH))
    partial = next(row for row in rows if row.partial)

    assert partial.attainment_pct == pytest.approx(704_900 / 731_500 * 100)
    assert partial.total_cpu_pct is None
    assert partial.used_cores is None
    assert partial.qps_per_used_core is None
    assert partial.target_sustained is False
    assert partial.is_pareto is False


def test_pareto_excludes_unsustained_points_when_gate_is_available() -> None:
    rows = derive_measurements(load_measurements(DATA_PATH))
    load_rows = [row for row in rows if row.target_qps == 77_000]
    pareto = {(row.bud, row.sleep_us) for row in load_rows if row.is_pareto}

    assert (1, 100) in pareto
    assert pareto
    assert all(
        next(
            row
            for row in load_rows
            if (row.bud, row.sleep_us) == key
        ).target_sustained
        for key in pareto
    )


def test_summary_preserves_near_capacity_semantics() -> None:
    summary = summarize_by_load(derive_measurements(load_measurements(DATA_PATH)))
    high = next(row for row in summary if row.target_qps == 731_500)

    assert high.complete_cells == 24
    assert high.partial_cells == 1
    assert high.sustained_cells == 0
    assert high.max_tps == pytest.approx(707_500)
    assert high.max_attainment_pct == pytest.approx(707_500 / 731_500 * 100)
    assert high.load_class == "near-capacity stress"


def test_csv_writers_emit_lf_terminated_outputs(tmp_path: Path) -> None:
    rows = derive_measurements(load_measurements(DATA_PATH))
    derived_path = tmp_path / "derived.csv"
    summary_path = tmp_path / "summary.csv"

    write_derived_csv(rows, derived_path)
    write_summary_csv(summarize_by_load(rows), summary_path)

    assert derived_path.stat().st_size > 10_000
    assert summary_path.stat().st_size > 300
    assert b"\r\n" not in derived_path.read_bytes()
    assert b"\r\n" not in summary_path.read_bytes()


def test_socket_baseline_preserves_five_load_anchors_and_gate() -> None:
    rows = load_socket_measurements(SOCKET_PATH)

    assert len(rows) == 5
    assert [(row.target_qps, row.tps) for row in rows] == [
        (7_700, 7_700),
        (77_000, 77_000),
        (192_500, 192_500),
        (385_000, 380_900),
        (731_500, 381_600),
    ]
    assert {row.concurrency for row in rows} == {128}
    assert {row.body_bytes for row in rows} == {1024}
    assert [row.used_cores for row in rows] == pytest.approx(
        [0.239, 2.117, 5.136, 9.724, 9.563]
    )
    assert [row.target_sustained for row in rows] == [True, True, True, False, False]


def test_duplicate_bud_runs_are_collapsed_with_two_run_ranges() -> None:
    derived = derive_measurements(load_measurements(DATA_PATH))
    candidates = build_tradeoff_candidates(
        derived, load_socket_measurements(SOCKET_PATH)
    )
    duplicates = [
        row
        for row in candidates
        if row.transport == "GQM" and row.policy == "B0/256 duplicate"
    ]

    assert len(candidates) == 105
    assert len(duplicates) == 25
    assert {row.repeat_count for row in duplicates} == {2}
    assert all(row.repeat_gate_consistent for row in duplicates)
    assert sum(row.target_sustained for row in duplicates) == 15

    low = next(
        row
        for row in duplicates
        if (row.target_qps, row.sleep_us) == (7_700, 10)
    )
    assert low.used_cores == pytest.approx((3.006 + 1.803) / 2)
    assert (low.used_cores_min, low.used_cores_max) == pytest.approx((1.803, 3.006))

    high = next(
        row
        for row in duplicates
        if (row.target_qps, row.sleep_us) == (385_000, 10)
    )
    assert high.tp99_us == pytest.approx(335)
    assert (high.tp99_us_min, high.tp99_us_max) == pytest.approx((330, 340))
    assert high.used_cores == pytest.approx((11.457 + 11.492) / 2)
    assert high.target_sustained is True


def test_candidate_csv_preserves_socket_and_repeat_ranges(tmp_path: Path) -> None:
    candidates = build_tradeoff_candidates(
        derive_measurements(load_measurements(DATA_PATH)),
        load_socket_measurements(SOCKET_PATH),
    )
    output = tmp_path / "candidates.csv"

    write_tradeoff_candidates_csv(candidates, output)

    text = output.read_text(encoding="utf-8")
    assert len(text.splitlines()) == 106
    assert "B0/256 duplicate" in text
    assert "Socket" in text
    assert b"\r\n" not in output.read_bytes()


def test_policy_boundaries_select_minimum_cpu_under_each_p99_budget() -> None:
    candidates = build_tradeoff_candidates(
        derive_measurements(load_measurements(DATA_PATH)),
        load_socket_measurements(SOCKET_PATH),
    )
    boundaries = build_policy_boundaries(candidates)

    expected = {
        7_700: [
            (20, "GQM", "B1024", 10_000, 3.194),
            (120, "Socket", "Socket", None, 0.239),
            (1150, "GQM", "B1", 100, 0.217),
            (19240, "GQM", "B16", 10_000, 0.113),
            (19260, "GQM", "B1", 10_000, 0.112),
        ],
        77_000: [
            (30, "GQM", "B1024", 1000, 6.931),
            (40, "GQM", "B0/256 duplicate", 10, 5.8115),
            (120, "GQM", "B0/256 duplicate", 100, 5.5515),
            (250, "Socket", "Socket", None, 2.117),
            (1320, "GQM", "B16", 1000, 1.598),
            (2290, "GQM", "B1", 100, 1.571),
        ],
        192_500: [
            (50, "GQM", "B1024", 10, 8.500),
            (65, "GQM", "B0/256 duplicate", 10, 8.027),
            (80, "GQM", "B1024", 1000, 7.183),
            (340, "Socket", "Socket", None, 5.136),
        ],
        385_000: [
            (330, "GQM", "B1024", 1, 11.712),
            (335, "GQM", "B0/256 duplicate", 10, 11.4745),
        ],
    }
    assert len(boundaries) == 17
    for target_qps, expected_segments in expected.items():
        actual = [
            (
                segment.min_p99_budget_us,
                segment.transport,
                segment.policy,
                segment.sleep_us,
                segment.used_cores,
            )
            for segment in boundaries
            if segment.target_qps == target_qps
        ]
        assert actual == expected_segments


def test_policy_boundaries_are_left_closed_and_near_capacity_is_empty() -> None:
    candidates = build_tradeoff_candidates(
        derive_measurements(load_measurements(DATA_PATH)),
        load_socket_measurements(SOCKET_PATH),
    )
    boundaries = build_policy_boundaries(candidates)
    low = [segment for segment in boundaries if segment.target_qps == 7_700]

    assert [segment.max_p99_budget_us for segment in low] == [
        120,
        1150,
        19240,
        19260,
        None,
    ]
    assert not any(segment.target_qps == 731_500 for segment in boundaries)
    assert low[0].quota_occupancy_pct == pytest.approx(
        3.194 / 16 * 100, abs=0.001
    )


def test_policy_boundary_csv_has_17_lf_terminated_rows(tmp_path: Path) -> None:
    candidates = build_tradeoff_candidates(
        derive_measurements(load_measurements(DATA_PATH)),
        load_socket_measurements(SOCKET_PATH),
    )
    output = tmp_path / "boundaries.csv"

    write_policy_boundaries_csv(build_policy_boundaries(candidates), output)

    text = output.read_text(encoding="utf-8")
    assert len(text.splitlines()) == 18
    assert "0.111999" not in text
    assert "11.491999" not in text
    assert b"\r\n" not in output.read_bytes()


def test_generation_emits_two_png_svg_pairs(tmp_path: Path) -> None:
    rows = build_tradeoff_candidates(
        derive_measurements(load_measurements(DATA_PATH)),
        load_socket_measurements(SOCKET_PATH),
    )

    outputs = generate_figures(rows, tmp_path)

    assert {path.name for path in outputs} == {
        "01_sc_symmetric_cpu_p99_tradeoff.png",
        "01_sc_symmetric_cpu_p99_tradeoff.svg",
        "02_p99_budget_minimum_cpu_boundary.png",
        "02_p99_budget_minimum_cpu_boundary.svg",
    }
    assert all(path.stat().st_size > 1_000 for path in outputs)


def test_figures_expose_resource_cpu_and_decision_semantics(
    tmp_path: Path,
) -> None:
    rows = build_tradeoff_candidates(
        derive_measurements(load_measurements(DATA_PATH)),
        load_socket_measurements(SOCKET_PATH),
    )
    generate_figures(rows, tmp_path)

    tradeoff_svg = (tmp_path / "01_sc_symmetric_cpu_p99_tradeoff.svg").read_text(
        encoding="utf-8"
    )
    boundary_svg = (
        tmp_path / "02_p99_budget_minimum_cpu_boundary.svg"
    ).read_text(encoding="utf-8")

    for svg in (tradeoff_svg, boundary_svg):
        assert "S/C symmetric BUD + Sleep" in svg
        assert "Client container: 8 vCPU" in svg
        assert "Server container: 8 vCPU" in svg
        assert "Combined quota: 16 vCPU" in svg
        assert "Payload: 1 KiB (1024 B)" in svg
        assert "Concurrency: 128" in svg
        assert "sum of thread-level process CPU" in svg
        assert "100% load = 770K TPS" in svg
        for load_label in ("1% load", "10% load", "25% load", "50% load", "95% load"):
            assert load_label in svg
        assert "7.7K QPS" not in svg
        assert "731.5K QPS" not in svg
        assert all(line == line.rstrip() for line in svg.splitlines())

    assert "Combined container quota occupancy (%)" in tradeoff_svg
    assert "GQM Idle Policy CPU–P99 Trade-off" in tradeoff_svg
    assert "Socket event-driven reference" in tradeoff_svg
    assert "two-run min–max" in tradeoff_svg
    assert "P99 budget" in boundary_svg
    assert "Minimum CPU Required by P99 SLO" in boundary_svg
    assert "No policy satisfies the 99.5% attainment gate" in boundary_svg
    assert "Socket is a system-level reference, not a GQM IRQ measurement" in boundary_svg
