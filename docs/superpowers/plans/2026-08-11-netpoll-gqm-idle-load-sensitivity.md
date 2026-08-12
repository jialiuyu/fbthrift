# Netpoll GQM Idle Load Sensitivity Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Preserve the 125-cell `QPS × BUD × Sleep` input, derive attainment and CPU-efficiency metrics, and publish three academic figures plus a bounded Chinese README.

**Architecture:** A single focused Python module loads a normalized CSV, validates the expected matrix including one explicitly partial cell, derives metrics and Pareto membership, then emits a derived CSV, summary CSV, and PNG/SVG figures. Tests define parsing, missing-value, metric, Pareto, and figure-contract behavior before implementation. Documentation and EDR metadata consume the generated artifacts without duplicating computation.

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
uv run python src/netpoll_gqm_idle_analysis.py --data data/netpoll_gqm_idle_load_sensitivity.csv --derived-output data/netpoll_gqm_idle_load_sensitivity_derived.csv --summary-output data/netpoll_gqm_idle_load_sensitivity_summary.csv --figure-dir figures
```

Expected: tests pass and both CSV files use LF line endings.

### Task 3: Generate three academic figures with TDD

**Files:**
- Modify: `thrift/perf/cpp2/performance/analysis/netpoll_gqm_idle_load_sensitivity/src/netpoll_gqm_idle_analysis.py`
- Modify: `thrift/perf/cpp2/performance/analysis/netpoll_gqm_idle_load_sensitivity/tests/test_netpoll_gqm_idle_analysis.py`
- Create: `thrift/perf/cpp2/performance/analysis/netpoll_gqm_idle_load_sensitivity/figures/01_throughput_attainment_heatmap.{png,svg}`
- Create: `thrift/perf/cpp2/performance/analysis/netpoll_gqm_idle_load_sensitivity/figures/02_p99_latency_heatmap.{png,svg}`
- Create: `thrift/perf/cpp2/performance/analysis/netpoll_gqm_idle_load_sensitivity/figures/03_cpu_p99_pareto.{png,svg}`

- [ ] **Step 1: Write failing figure-contract tests**

```python
def test_generation_emits_three_png_svg_pairs(tmp_path: Path) -> None:
    outputs = generate_figures(derive_measurements(load_measurements(DATA_PATH)), tmp_path)
    assert {p.name for p in outputs} == {
        "01_throughput_attainment_heatmap.png", "01_throughput_attainment_heatmap.svg",
        "02_p99_latency_heatmap.png", "02_p99_latency_heatmap.svg",
        "03_cpu_p99_pareto.png", "03_cpu_p99_pareto.svg",
    }

def test_figures_expose_payload_gate_and_missing_cell(tmp_path: Path) -> None:
    generate_figures(derive_measurements(load_measurements(DATA_PATH)), tmp_path)
    text = (tmp_path / "01_throughput_attainment_heatmap.svg").read_text()
    assert "Payload: 1 KiB (1024 B)" in text
    assert "99.5% attainment gate" in text
    assert "N/A" in text
```

- [ ] **Step 2: Run tests and verify RED**

Expected: failure because `generate_figures` is absent.

- [ ] **Step 3: Implement heatmaps and Pareto plot**

Use a consistent five-panel layout; discrete Sleep/BUD ticks; logarithmic p99 color normalization; hatched or muted unsustained cells; an explicit `N/A` cell; log-scaled p99 in the Pareto figure; direct annotation only for frontier points. Save PNG at 220 DPI and whitespace-normalized SVG.

- [ ] **Step 4: Run tests and verify GREEN**

Run `uv run pytest -q`; expected: all tests pass and six figure files are non-empty.

- [ ] **Step 5: Render and visually inspect all three PNG files**

Check that titles, color bars, cell labels, direct annotations and axes do not overlap. Adjust layout only after keeping tests green.

### Task 4: Publish README and experiment record

**Files:**
- Create: `thrift/perf/cpp2/performance/analysis/netpoll_gqm_idle_load_sensitivity/README.md`
- Create: `thrift/perf/cpp2/performance/analysis/netpoll_gqm_idle_load_sensitivity/data/SOURCE.md`
- Create: `thrift/perf/cpp2/performance/edr/EDR-0008-netpoll-gqm-idle-load-sensitivity.md`
- Modify: `thrift/perf/cpp2/performance/FRONTIER.md`

- [ ] **Step 1: Write the README from generated values**

Use total-summary-first structure: matrix/envelope and bounded findings, then one section per figure, followed by the full normalized 125-row table and limitations. Do not claim Socket or IRQ benefit from this batch.

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
