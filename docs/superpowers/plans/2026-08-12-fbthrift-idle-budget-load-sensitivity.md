# fbthrift Idle Budget Load Sensitivity Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Preserve the complete fbthrift Ubmem idle matrix and Socket anchors, derive gate-aware CPU–P99 choices, and publish two standalone academic figures plus a bounded Chinese report without any Netpoll comparison.

**Architecture:** A focused Python module loads normalized Ubmem and Socket CSVs, derives reported-attainment and two-endpoint CPU metrics, aggregates idle-disabled BUD rows as four repeats, and emits summary/candidate/boundary CSVs plus two PNG/SVG figures. Tests define the 80+5 point archive, all-repeat gate, P99-budget boundary, resource labels, and the prohibition on Netpoll text before implementation.

**Tech Stack:** Python 3.11, standard-library `csv`/`dataclasses`, Matplotlib, pytest, uv, fbthrift EDR validator.

---

### Task 1: Archive the 80+5 observations and define the parser contract

**Files:**
- Create: `thrift/perf/cpp2/performance/analysis/fbthrift_idle_budget_load_sensitivity/data/raw/fbthrift_idle_budget_load_sensitivity.txt`
- Create: `thrift/perf/cpp2/performance/analysis/fbthrift_idle_budget_load_sensitivity/data/raw/fbthrift_socket_qps_baseline.txt`
- Create: `thrift/perf/cpp2/performance/analysis/fbthrift_idle_budget_load_sensitivity/data/fbthrift_idle_budget_load_sensitivity.csv`
- Create: `thrift/perf/cpp2/performance/analysis/fbthrift_idle_budget_load_sensitivity/data/fbthrift_socket_qps_baseline.csv`
- Create: `thrift/perf/cpp2/performance/analysis/fbthrift_idle_budget_load_sensitivity/tests/test_fbthrift_idle_analysis.py`
- Create: `thrift/perf/cpp2/performance/analysis/fbthrift_idle_budget_load_sensitivity/pyproject.toml`

- [ ] **Step 1: Archive the supplied text verbatim and normalize it without dropping columns**

The Ubmem CSV columns are:

```text
source_index,value,target_qps,idle_enabled,sleep_us,bud,reported_sustain_pct,achieved_qps,p50_us,p99_us,p999_us,max_us,send_lag_p99_us,scheduled,dispatched,completed,shed,client_cpu_user_pct,client_cpu_sys_pct,client_cpu_pct,server_cpu_user_pct,server_cpu_sys_pct,server_cpu_pct,client_rss_kb,server_rss_kb,retry,status
```

The Socket CSV uses the same result columns without idle parameters.

- [ ] **Step 2: Write failing archive tests**

```python
def test_archive_preserves_80_unique_ubmem_cells() -> None:
    rows = load_measurements(DATA_PATH)
    assert len(rows) == 80
    assert len({(r.target_qps, r.idle_enabled, r.sleep_us, r.bud) for r in rows}) == 80
    assert {r.target_qps for r in rows} == {1600, 16000, 40000, 80000, 152000}
    assert {r.bud for r in rows} == {0, 1, 16, 256}

def test_socket_archive_preserves_five_load_anchors() -> None:
    rows = load_socket_measurements(SOCKET_PATH)
    assert [(r.target_qps, r.reported_sustain_pct) for r in rows] == [
        (1600, 100.0), (16000, 100.0), (40000, 100.0),
        (80000, 99.0), (152000, 99.6),
    ]
```

- [ ] **Step 3: Run the tests and verify RED**

Run `uv run pytest -q`; expected: import failure because `fbthrift_idle_analysis` does not exist.

### Task 2: Implement derivation, repeat aggregation, and policy boundaries with TDD

**Files:**
- Create: `thrift/perf/cpp2/performance/analysis/fbthrift_idle_budget_load_sensitivity/src/fbthrift_idle_analysis.py`
- Modify: `thrift/perf/cpp2/performance/analysis/fbthrift_idle_budget_load_sensitivity/tests/test_fbthrift_idle_analysis.py`
- Create: `thrift/perf/cpp2/performance/analysis/fbthrift_idle_budget_load_sensitivity/data/fbthrift_idle_budget_load_sensitivity_derived.csv`
- Create: `thrift/perf/cpp2/performance/analysis/fbthrift_idle_budget_load_sensitivity/data/fbthrift_idle_budget_load_sensitivity_summary.csv`
- Create: `thrift/perf/cpp2/performance/analysis/fbthrift_idle_budget_load_sensitivity/data/fbthrift_idle_tradeoff_candidates.csv`
- Create: `thrift/perf/cpp2/performance/analysis/fbthrift_idle_budget_load_sensitivity/data/fbthrift_idle_policy_boundaries.csv`

- [ ] **Step 1: Add failing metric and reported-gate tests**

```python
def test_derived_metrics_use_reported_sustain_and_two_endpoint_cpu() -> None:
    point = by_source_index(derive_measurements(load_measurements(DATA_PATH)), 1)
    assert point.total_cpu_pct == pytest.approx(768.2)
    assert point.used_cores == pytest.approx(7.682)
    assert point.quota_occupancy_pct == pytest.approx(7.682 / 12 * 100)
    assert point.target_sustained is True

def test_reported_sustain_controls_the_gate() -> None:
    socket = load_socket_measurements(SOCKET_PATH)
    assert [r.target_sustained for r in socket] == [True, True, True, False, True]
```

- [ ] **Step 2: Implement immutable row types, loaders, metrics, and LF CSV writers**

Use `reported_sustain_pct >= 99.5` as the only gate. Calculate Total CPU from both endpoint totals,
then `used_cores`, `quota_occupancy_pct`, and `qps_per_used_core`.

- [ ] **Step 3: Run tests and verify GREEN**

Run `uv run pytest -q`; expected: archive and derivation tests pass.

- [ ] **Step 4: Add failing repeat and boundary tests**

```python
def test_disabled_idle_rows_become_four_run_candidates() -> None:
    candidates = build_tradeoff_candidates(
        derive_measurements(load_measurements(DATA_PATH)),
        load_socket_measurements(SOCKET_PATH),
    )
    disabled = [c for c in candidates if c.policy == "Polling baseline"]
    assert len(disabled) == 5
    assert {c.repeat_count for c in disabled} == {4}
    assert [c.target_sustained for c in disabled] == [True, True, False, True, False]

def test_80k_socket_is_anchor_only() -> None:
    boundaries = build_policy_boundaries(build_candidates())
    assert not any(b.target_qps == 80000 and b.transport == "Socket" for b in boundaries)
```

- [ ] **Step 5: Implement repeat-aware candidates, Pareto flags, summaries, and boundaries**

Aggregate only idle-disabled rows. Centers are arithmetic means; x/y whiskers are min–max; all four
runs must pass the gate. Idle-enabled cells remain single observations. Eligible Socket points enter
the same per-load Pareto and minimum-CPU-under-P99-budget rule.

- [ ] **Step 6: Run tests and generate all four derived CSVs**

Run the module CLI with both source CSVs and output paths. Expected: all outputs use LF endings and
contain every raw cell or every derived candidate required by their schema.

### Task 3: Generate the two standalone academic figures with TDD

**Files:**
- Modify: `thrift/perf/cpp2/performance/analysis/fbthrift_idle_budget_load_sensitivity/src/fbthrift_idle_analysis.py`
- Modify: `thrift/perf/cpp2/performance/analysis/fbthrift_idle_budget_load_sensitivity/tests/test_fbthrift_idle_analysis.py`
- Create: `thrift/perf/cpp2/performance/analysis/fbthrift_idle_budget_load_sensitivity/figures/01_fbthrift_cpu_p99_tradeoff.{png,svg}`
- Create: `thrift/perf/cpp2/performance/analysis/fbthrift_idle_budget_load_sensitivity/figures/02_fbthrift_p99_budget_minimum_cpu_boundary.{png,svg}`

- [ ] **Step 1: Write failing figure-contract tests**

```python
def test_figures_are_fbthrift_only_and_state_resource_semantics(tmp_path: Path) -> None:
    paths = generate_figures(build_candidates(), build_boundaries(), tmp_path)
    assert len(paths) == 4
    svg = "\n".join(p.read_text() for p in paths if p.suffix == ".svg")
    assert "fbthrift" in svg
    assert "Server: 8 vCPU" in svg
    assert "Client: 4 vCPU" in svg
    assert "Combined quota: 12 vCPU" in svg
    assert "Netpoll" not in svg
```

- [ ] **Step 2: Run tests and verify RED**

Expected: failure because `generate_figures` is absent.

- [ ] **Step 3: Implement the CPU–P99 and P99-budget figures**

Use five load panels, adaptive CPU axes, log P99, a top quota axis, direct Socket labels and projection
lines, repeat min–max bars, muted failed-gate points, and a combined eligible Pareto line. The second
figure uses discrete minimum-CPU step boundaries. All titles and captions remain fbthrift-specific.

- [ ] **Step 4: Run tests and visually inspect the PNGs**

Verify no overlap among titles, legends, error bars, labels, and dual axes; retain the paper-style white
background, restrained colors, and readable direct annotations.

### Task 4: Publish the report and extend the existing fbthrift EDR

**Files:**
- Create: `thrift/perf/cpp2/performance/analysis/fbthrift_idle_budget_load_sensitivity/README.md`
- Create: `thrift/perf/cpp2/performance/analysis/fbthrift_idle_budget_load_sensitivity/data/SOURCE.md`
- Modify: `thrift/perf/cpp2/performance/edr/EDR-0005-empty-poll-backoff-openloop-sensitivity.md`
- Modify: `thrift/perf/cpp2/performance/FRONTIER.md`

- [ ] **Step 1: Write README from generated values**

Use total-summary-detail-summary structure, embed only the two new figures, retain an 80-row compact raw
table, link all CSVs, and state the Socket/IRQ evidence boundary. Do not mention Netpoll as a comparison.

- [ ] **Step 2: Record source and provenance**

Record the attachment SHA-256, received date, field mapping, resource envelope, and the five pasted
Socket anchors. Do not claim missing binary SHA, NUMA, run order, or hardware revision.

- [ ] **Step 3: Update EDR-0005 and Frontier by exact hunks**

Extend EDR-0005 with the new 80+5-point BUD matrix and artifact links while preserving the older interval
sweep. Keep status `RUNNING`; state that Socket is a system-level reference and same-transport IRQ A/B is
still absent. Update only the corresponding Frontier evidence and route text.

### Task 5: Verify and review scope

**Files:**
- Verify all files under `thrift/perf/cpp2/performance/analysis/fbthrift_idle_budget_load_sensitivity/`
- Verify exact EDR-0005 and Frontier hunks

- [ ] **Step 1: Run focused tests and regenerate outputs**

Run `uv run pytest -q` and the analysis CLI from the new directory; expected: zero failures and stable
generated files.

- [ ] **Step 2: Check figure text and visual layout**

Search both SVGs for required resource/method labels and absence of `Netpoll`; inspect both PNGs at
original resolution.

- [ ] **Step 3: Run repository validation**

Run:

```bash
git diff --check
python3 thrift/perf/cpp2/performance/validate_edr.py \
  --performance-dir thrift/perf/cpp2/performance \
  --folly ../folly \
  --fbthrift .
```

Expected: no whitespace errors and all EDR records validate.

- [ ] **Step 4: Review changed-file scope**

Use `git status --short` and `git diff --stat`; preserve unrelated modified and untracked files. Do not
commit or push the generated report unless the user requests publication.
