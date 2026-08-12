# Idle Load Normalized Labels Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Present fbthrift and Netpoll idle sweeps as normalized load percentages while preserving absolute QPS/TPS in raw data.

**Architecture:** Keep all measurement keys and calculations unchanged. Add fixed no-idle capacity constants used only by presentation labels, assert the five normalized load labels in figure tests, regenerate PNG/SVG artifacts, and update the two Markdown reports to use percentages in decision-facing sections.

**Tech Stack:** Python 3, Matplotlib, pytest, Markdown, uv

---

### Task 1: Lock the normalized figure-label contract

**Files:**
- Modify: `thrift/perf/cpp2/performance/analysis/fbthrift_idle_budget_load_sensitivity/tests/test_fbthrift_idle_analysis.py`
- Modify: `thrift/perf/cpp2/performance/analysis/netpoll_gqm_idle_load_sensitivity/tests/test_netpoll_gqm_idle_analysis.py`

- [ ] **Step 1: Add failing SVG assertions**

Assert that each figure contains `1% load`, `10% load`, `25% load`, `50% load`, and `95% load`; assert a single capacity statement of `100% load = 160K QPS` for fbthrift and `100% load = 770K TPS` for Netpoll.

- [ ] **Step 2: Run focused tests and verify failure**

```bash
uv run pytest -q tests/test_fbthrift_idle_analysis.py -k figures
uv run pytest -q tests/test_netpoll_gqm_idle_analysis.py -k figures
```

Expected: both fail because current SVG facet titles use absolute QPS.

### Task 2: Implement normalized presentation labels

**Files:**
- Modify: `thrift/perf/cpp2/performance/analysis/fbthrift_idle_budget_load_sensitivity/src/fbthrift_idle_analysis.py`
- Modify: `thrift/perf/cpp2/performance/analysis/netpoll_gqm_idle_load_sensitivity/src/netpoll_gqm_idle_analysis.py`

- [ ] **Step 1: Add fixed capacity labels and percentage facet titles**

Use `160_000 QPS` and `770_000 TPS` as presentation-only 100% baselines. Map the existing five target values to `1/10/25/50/95% load` and add the full-capacity statement once in each figure heading block.

- [ ] **Step 2: Run focused tests and verify pass**

```bash
uv run pytest -q tests/test_fbthrift_idle_analysis.py -k figures
uv run pytest -q tests/test_netpoll_gqm_idle_analysis.py -k figures
```

Expected: PASS.

### Task 3: Align reports and regenerate artifacts

**Files:**
- Modify: `thrift/perf/cpp2/performance/analysis/fbthrift_idle_budget_load_sensitivity/README.md`
- Modify: `thrift/perf/cpp2/performance/analysis/netpoll_gqm_idle_load_sensitivity/README.md`
- Regenerate: both analysis directories' two PNG/SVG figure pairs

- [ ] **Step 1: Replace decision-facing load labels**

Use normalized percentages in summary, Socket anchor, observations, and policy-boundary tables. State the 100% no-idle capacity once near the experiment envelope. Keep absolute target and achieved values in source CSV files.

- [ ] **Step 2: Regenerate both figure sets**

Run each README's documented plotting command without changing input CSV files.

- [ ] **Step 3: Run complete verification**

```bash
uv run pytest -q
python3 thrift/perf/cpp2/performance/validate_edr.py \
  --performance-dir thrift/perf/cpp2/performance \
  --folly ../folly \
  --fbthrift .
git diff --check
```

Expected: all tests pass, all EDRs validate, and no whitespace errors are reported.
