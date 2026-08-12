# Netpoll GQM Idle Load Sensitivity Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Preserve the 125-cell `QPS × BUD × Sleep` input, derive attainment and exact P99-SLO policy boundaries, and publish two decision-oriented academic figures plus a bounded Chinese README.

**Architecture:** A single focused Python module loads the normalized GQM matrix and Socket baseline, preserves all raw measurements, collapses the accidentally identical `BUD=0/256` runs into repeat-aware candidates, and derives a combined Pareto frontier plus deterministic minimum-CPU policy. It emits derived, summary, candidate, and policy-boundary CSVs plus two PNG/SVG figures. Tests define repeat, gate, boundary, and figure semantics before implementation.

**Tech Stack:** Python 3.11, standard-library `csv`/`dataclasses`, Matplotlib, pytest, uv, fbthrift performance EDR validator.

---

### Task 1: Archive and validate the matrix

**Files:**
- Create: `thrift/perf/cpp2/performance/analysis/netpoll_gqm_idle_load_sensitivity/data/raw/netpoll_gqm_idle_load_sensitivity.txt`
- Create: `thrift/perf/cpp2/performance/analysis/netpoll_gqm_idle_load_sensitivity/data/netpoll_gqm_idle_load_sensitivity.csv`
- Create: `thrift/perf/cpp2/performance/analysis/netpoll_gqm_idle_load_sensitivity/tests/test_netpoll_gqm_idle_analysis.py`
- Create: `thrift/perf/cpp2/performance/analysis/netpoll_gqm_idle_load_sensitivity/pyproject.toml`

- [ ] **Step 1: Copy the supplied text verbatim and normalize all 125 configuration keys to CSV**

The CSV columns are:

```text
source_index,value,target_qps,bud,sleep_us,concurrency,body_bytes,cost_reported,tps,tp99_us,tp999_us,client_cpu_pct,server_cpu_pct,client_rss_kb,server_rss_kb,retry,status,partial
```

The final partial row keeps `tps=704900` and empty values for all unavailable fields.

- [ ] **Step 2: Write failing matrix tests**

```python
def test_archive_preserves_125_unique_configuration_keys() -> None:
    rows = load_measurements(DATA_PATH)
    assert len(rows) == 125
    assert len({(r.target_qps, r.bud, r.sleep_us) for r in rows}) == 125
    assert {r.target_qps for r in rows} == {7700, 77000, 192500, 385000, 731500}

def test_only_last_cell_is_partial() -> None:
    rows = load_measurements(DATA_PATH)
    partial = [row for row in rows if row.partial]
    assert [(r.source_index, r.target_qps, r.bud, r.sleep_us, r.tps) for r in partial] == [
        (125, 731500, 1024, 10000, 704900)
    ]
    assert partial[0].tp99_us is None
```

- [ ] **Step 3: Run tests and verify RED**

Run:

```bash
uv run pytest -q
```

Expected: collection fails because `netpoll_gqm_idle_analysis` does not exist.

### Task 2: Derive metrics and summaries with TDD

**Files:**
- Create: `thrift/perf/cpp2/performance/analysis/netpoll_gqm_idle_load_sensitivity/src/netpoll_gqm_idle_analysis.py`
- Modify: `thrift/perf/cpp2/performance/analysis/netpoll_gqm_idle_load_sensitivity/tests/test_netpoll_gqm_idle_analysis.py`
- Create: `thrift/perf/cpp2/performance/analysis/netpoll_gqm_idle_load_sensitivity/data/netpoll_gqm_idle_load_sensitivity_derived.csv`
- Create: `thrift/perf/cpp2/performance/analysis/netpoll_gqm_idle_load_sensitivity/data/netpoll_gqm_idle_load_sensitivity_summary.csv`

- [ ] **Step 1: Add failing derived-metric tests**

```python
def test_derived_metrics_use_target_attainment_and_two_endpoint_cpu() -> None:
    rows = derive_measurements(load_measurements(DATA_PATH))
    point = by_key(rows, 7700, 1, 100)
    assert point.attainment_pct == pytest.approx(100.0)
    assert point.total_cpu_pct == pytest.approx(21.7)
    assert point.used_cores == pytest.approx(0.217)
    assert point.qps_per_used_core == pytest.approx(7700 / 0.217)
    assert point.target_sustained is True

def test_near_capacity_load_has_no_sustained_cell() -> None:
    rows = derive_measurements(load_measurements(DATA_PATH))
    assert not any(row.target_sustained for row in rows if row.target_qps == 731500)
```

- [ ] **Step 2: Implement the minimal loader and derivation**

Implement immutable `Measurement` and `DerivedMeasurement` dataclasses, blank-to-`None` parsing, exact matrix validation, `attainment_pct`, `total_cpu_pct`, `used_cores`, `qps_per_used_core`, and the `99.5%` gate. Partial rows keep unavailable derived fields as `None`.

- [ ] **Step 3: Run tests and verify GREEN**

Run `uv run pytest -q`; expected: all parser and derivation tests pass.

- [ ] **Step 4: Add failing Pareto and summary tests**

```python
def test_pareto_excludes_unsustained_points_when_gate_is_available() -> None:
    rows = derive_measurements(load_measurements(DATA_PATH))
    pareto = pareto_keys(rows, target_qps=77000)
    assert (1, 100) in pareto
    assert all(by_key(rows, 77000, b, s).target_sustained for b, s in pareto)

def test_summary_preserves_near_capacity_semantics() -> None:
    summary = summarize_by_load(derive_measurements(load_measurements(DATA_PATH)))
    high = next(row for row in summary if row.target_qps == 731500)
    assert high.complete_cells == 24
    assert high.partial_cells == 1
    assert high.sustained_cells == 0
    assert high.max_tps == pytest.approx(707500)
```

- [ ] **Step 5: Implement Pareto flags, summary generation, and CSV writers**

Pareto dominance is evaluated within one QPS slice using lower `total_cpu_pct` and lower `tp99_us`. If a slice has sustained points, only those points participate. The near-capacity slice has no compliant frontier label; its complete points remain visible as stress observations.

- [ ] **Step 6: Run tests and generate both CSV outputs**

Run:

```bash
uv run pytest -q
uv run python src/netpoll_gqm_idle_analysis.py --data data/netpoll_gqm_idle_load_sensitivity.csv --derived-output data/netpoll_gqm_idle_load_sensitivity_derived.csv --summary-output data/netpoll_gqm_idle_load_sensitivity_summary.csv --policy-boundary-output data/netpoll_gqm_idle_policy_boundaries.csv --figure-dir figures
```

Expected: tests pass and both CSV files use LF line endings.

### Task 3: Derive exact P99-SLO policy boundaries with TDD

**Files:**
- Modify: `thrift/perf/cpp2/performance/analysis/netpoll_gqm_idle_load_sensitivity/src/netpoll_gqm_idle_analysis.py`
- Modify: `thrift/perf/cpp2/performance/analysis/netpoll_gqm_idle_load_sensitivity/tests/test_netpoll_gqm_idle_analysis.py`
- Create: `thrift/perf/cpp2/performance/analysis/netpoll_gqm_idle_load_sensitivity/data/netpoll_gqm_idle_policy_boundaries.csv`

- [ ] **Step 1: Write failing boundary tests**

```python
def test_policy_boundaries_select_minimum_cpu_under_each_p99_budget() -> None:
    segments = build_policy_boundaries(derive_measurements(load_measurements(DATA_PATH)))
    low = [segment for segment in segments if segment.target_qps == 7_700]
    assert [(s.min_p99_budget_us, s.bud, s.sleep_us) for s in low] == [
        (20, 1024, 10_000),
        (1150, 1, 100),
        (19240, 16, 10_000),
        (19260, 1, 10_000),
    ]

def test_near_capacity_load_has_no_policy_boundary() -> None:
    segments = build_policy_boundaries(derive_measurements(load_measurements(DATA_PATH)))
    assert not any(segment.target_qps == 731_500 for segment in segments)
```

- [ ] **Step 2: Run tests and verify RED**

Expected: failure because `build_policy_boundaries` is absent.

- [ ] **Step 3: Implement boundary derivation and CSV writer**

For every target QPS, consider only `target_sustained` points. At every unique observed P99 threshold, select the eligible point with minimum `used_cores`; emit a new segment only when the selected point changes. Segments are left-closed/right-open, with the final upper bound empty. The output includes quota occupancy as `used_cores / 16 × 100`.

- [ ] **Step 4: Run tests, write the boundary CSV, and verify GREEN**

Run `uv run pytest -q`; expected: all tests pass and the boundary CSV has 15 rows.

### Task 4: Generate two academic figures with TDD

**Files:**
- Modify: `thrift/perf/cpp2/performance/analysis/netpoll_gqm_idle_load_sensitivity/src/netpoll_gqm_idle_analysis.py`
- Modify: `thrift/perf/cpp2/performance/analysis/netpoll_gqm_idle_load_sensitivity/tests/test_netpoll_gqm_idle_analysis.py`
- Create: `thrift/perf/cpp2/performance/analysis/netpoll_gqm_idle_load_sensitivity/figures/01_sc_symmetric_cpu_p99_tradeoff.{png,svg}`
- Create: `thrift/perf/cpp2/performance/analysis/netpoll_gqm_idle_load_sensitivity/figures/02_p99_budget_minimum_cpu_boundary.{png,svg}`

- [ ] **Step 1: Write failing figure-contract tests**

Assert that generation returns exactly two PNG/SVG pairs and both SVGs state S/C symmetric policy, Client/Server `8 vCPU`, total `16 vCPU`, Payload `1 KiB`, Concurrency `128`, and summed thread-level CPU semantics. Assert the first contains the combined quota axis and the second contains `P99 budget` plus the no-feasible-policy label for `731.5K`.

- [ ] **Step 2: Run tests and verify RED**

Expected: old three-figure output violates the new contract.

- [ ] **Step 3: Implement the detailed trade-off and selection-boundary figures**

The trade-off figure uses five load panels, adaptive CPU axes, a secondary quota-occupancy axis, direct frontier labels, and muted unsustained points. The selection figure plots the stepwise minimum CPU boundary for four sustained loads and a no-feasible-policy panel for `731.5K`. Do not add placeholder Socket marks.

- [ ] **Step 4: Run tests and verify GREEN**

Run `uv run pytest -q`; expected: all tests pass and four figure files are non-empty.

- [ ] **Step 5: Render and visually inspect both PNG files**

Check that titles, metadata, dual x axes, direct annotations and labels do not overlap. Adjust layout only after keeping tests green.

### Task 5: Publish README and experiment record

**Files:**
- Create: `thrift/perf/cpp2/performance/analysis/netpoll_gqm_idle_load_sensitivity/README.md`
- Create: `thrift/perf/cpp2/performance/analysis/netpoll_gqm_idle_load_sensitivity/data/SOURCE.md`
- Create: `thrift/perf/cpp2/performance/edr/EDR-0008-netpoll-gqm-idle-load-sensitivity.md`
- Modify: `thrift/perf/cpp2/performance/FRONTIER.md`

- [ ] **Step 1: Write the README from generated values**

Use total-summary-first structure: matrix/envelope and CPU semantics, then the detailed trade-off figure, the exact P99-SLO boundary figure, the full normalized 125-row table, and limitations. Remove heatmap presentation. Do not claim Socket or IRQ benefit from this batch.

- [ ] **Step 2: Record provenance and EDR scope**

`SOURCE.md` records the attachment path, received date, exact field mapping, the partial final row and SHA-256 of the archived raw text. `EDR-0008` remains `RUNNING`, treats QPS/BUD/Sleep as a three-factor characterization matrix, and requires repeats plus a same-framework Socket reference or same-transport IRQ A/B before IRQ claims.

- [ ] **Step 3: Update Frontier without overwriting unrelated local changes**

Add one active hypothesis/EDR route and a recommended next step for repeats, stage-residency counters, Socket reference and future IRQ A/B.

- [ ] **Step 4: Run final verification**

```bash
uv run pytest -q
python3 ../../validate_edr.py --performance-dir ../.. --folly ../../../../../folly --fbthrift ../../../../..
git diff --check
```

Run the EDR validator from the analysis directory only after resolving paths to the actual linked `folly` and `fbthrift` roots; expected: all analysis tests and EDR validation pass.

- [ ] **Step 5: Review scope and commit intentionally**

Stage only the plan, new analysis directory, EDR-0008, and the exact Frontier hunks added for EDR-0008. Preserve all unrelated modified and untracked files.

### Task 6: Integrate Socket anchors and correct duplicate-policy semantics

**Files:**
- Create: `thrift/perf/cpp2/performance/analysis/netpoll_gqm_idle_load_sensitivity/data/raw/netpoll_socket_qps_baseline.txt`
- Create: `thrift/perf/cpp2/performance/analysis/netpoll_gqm_idle_load_sensitivity/data/netpoll_socket_qps_baseline.csv`
- Create: `thrift/perf/cpp2/performance/analysis/netpoll_gqm_idle_load_sensitivity/data/netpoll_idle_tradeoff_candidates.csv`
- Modify: analysis source, tests, figures, README, `SOURCE.md`, `EDR-0008`, and the exact `FRONTIER.md` EDR-0008 text.

- [x] **Step 1: Archive the five Socket points and write failing loader/gate tests**

Verify `Concurrency=128`, `Payload=1024 B`, total CPU cores, and that the `7.7K/77K/192.5K`
points pass the 99.5% gate while `385K/731.5K` do not.

- [x] **Step 2: Write failing duplicate-aggregation tests**

Preserve all 125 GQM rows. Collapse `BUD=0` and `BUD=256` at every `QPS × Sleep` into one
effective candidate whose center is the two-run mean and whose uncertainty is the min-max range.
Require both repeats to pass the gate. Verify all 25 pairs agree on gate classification.

- [x] **Step 3: Implement candidate aggregation and combined policy boundaries**

Keep `BUD=1/16/1024` as single-run candidates. Add eligible Socket points to the same per-load
Pareto and P99-budget selection rule. Use candidate center values for boundaries and emit repeat
count/ranges in the candidate CSV.

- [x] **Step 4: Regenerate both figures and update the report**

Use a five-point Socket star series with direct CPU/P99/attainment labels and projection lines.
Draw min-max x/y whiskers directly on duplicate GQM candidates. State explicitly that these are
two-run ranges, not confidence intervals, and that Socket is not an IRQ measurement.

- [x] **Step 5: Run focused tests, EDR validation, diff checks, and visual inspection**
