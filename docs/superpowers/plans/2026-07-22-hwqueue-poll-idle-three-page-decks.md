# HWQueue and Poll-idle Dense Three-page Decks Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Rebuild the two condensed performance decks as at-most-three-slide, content-only academic reports that retain every experiment and major result plot.

**Architecture:** Keep the existing artifact-tool builder and visual language, but replace the overview/facts/conclusion slide functions with six dense experiment-content slide functions. Use the report PNGs as immutable evidence, add only compact condition strips and data tables, export to the existing condensed PPTX paths, then render and inspect every slide.

**Tech Stack:** JavaScript ES modules, `@oai/artifact-tool`, existing PNG figures, presentation container QA tools, `unzip`.

---

### Task 1: Replace summary-oriented slide primitives

**Files:**
- Modify: `/var/folders/wc/hk3t6khd3gj6ct0bhw5kp3l40000gn/T/codex-presentations/manual-20260722/hwqueue-poll-idle-decks/tmp/build_condensed_decks.mjs`
- Reference: `docs/superpowers/specs/2026-07-22-hwqueue-poll-idle-three-page-decks-design.md`

- [ ] **Step 1: Remove the summary slide functions**

Delete `addOverviewSlide`, `addGqmFactsSlide`, `addGqmConclusionSlide`, `addPollFactsSlide`, and `addPollConclusionSlide`. Keep `tx`, `rect`, `rule`, `shell`, `bytes`, and `exportDeck`.

- [ ] **Step 2: Add neutral academic-report helpers**

Add helpers with these interfaces and fixed geometry:

```js
function conditionStrip(slide, name, textValue) {
  slide.compose(layers({ name, width: "fill", height: "fill" }, [
    rect(`${name}-bg`, 42, 118, 1196, 42, PANEL),
    tx(textValue, 58, 128, 1164, 22, 18, { color: MUTED }),
  ]), { frame: { left: 0, top: 0, width: W, height: H }, baseUnit: 1 });
}

function label(slide, name, value, left, top, width) {
  slide.compose(layers({ name, width: "fill", height: "fill" }, [
    tx(value, left, top, width, 26, 20, { bold: true }),
  ]), { frame: { left: 0, top: 0, width: W, height: H }, baseUnit: 1 });
}

function resultBand(slide, name, value, top) {
  slide.compose(layers({ name, width: "fill", height: "fill" }, [
    rect(`${name}-bg`, 42, top, 1196, 44, ACCENT_LIGHT),
    tx(value, 58, top + 10, 1164, 24, 19, { bold: true }),
  ]), { frame: { left: 0, top: 0, width: W, height: H }, baseUnit: 1 });
}
```

- [ ] **Step 3: Make all slide titles neutral**

Use only these titles:

```js
const GQM_TITLES = [
  "RPC Baseline QPS Sweep 与 35K Open-loop Delay Sweep",
  "cpp2 与 VA FLAT Fixed-outstanding Delay Sweep",
  "GQM Wrapper 注入校准",
];

const POLL_TITLES = [
  "Poll-idle Poisson 350 QPS：CPU–p99 Pareto",
  "BUD × SLEEP Latency Sweep",
  "BUD × SLEEP CPU Sweep 与参数矩阵",
];
```

- [ ] **Step 4: Run the builder syntax check**

Run:

```bash
node --check /var/folders/wc/hk3t6khd3gj6ct0bhw5kp3l40000gn/T/codex-presentations/manual-20260722/hwqueue-poll-idle-decks/tmp/build_condensed_decks.mjs
```

Expected: exit code `0` and no output.

### Task 2: Implement the three GQM experiment slides

**Files:**
- Modify: `/var/folders/wc/hk3t6khd3gj6ct0bhw5kp3l40000gn/T/codex-presentations/manual-20260722/hwqueue-poll-idle-decks/tmp/build_condensed_decks.mjs`
- Read: `thrift/perf/cpp2/performance/analysis/gqm_injected_delay_sweep/README.md`
- Read: `thrift/perf/cpp2/performance/edr/EDR-0002-gqm-injected-delay-rpc-sweep.md`
- Read: `thrift/perf/cpp2/performance/edr/EDR-0003-gqm-fixed-outstanding-path-sensitivity.md`

- [ ] **Step 1: Implement the open-loop slide**

Compose a content-only slide with this evidence and copy:

```js
const openLoopConditions = "Unary64 · Poisson open loop · 35K target QPS · max_inflight=496 · server 1 IO + 1 CPU · single run / point";
const openLoopRows = [
  ["0x", "35.07K", "526 µs", "1.274 ms"],
  ["40x", "35.07K", "743 µs", "1.615 ms"],
  ["100x", "35.07K", "6.992 ms", "8.784 ms"],
  ["200x", "30.60K", "15.781 ms", "16.473 ms"],
];
```

Place `01_rpc_saturation_knee.png` in the left column; stack `02_gqm_delay_sensitivity.png` and `03_gqm_delay_capacity.png` in the right column. Put the four-row data table below the left chart. Add one result band: `当前 35K envelope 中，100x 出现明显 latency 跳升；200x 同时出现 completed QPS 下降。`

- [ ] **Step 2: Implement the two fixed-outstanding experiments**

Use `05_cpp2_closed_loop_sensitivity.png` and `06_va_flat_compressed_polling_sensitivity.png` side by side. Add these normalized rows below each chart:

```js
const cpp2Rows = [
  ["0x", "100.00%", "1.000x"],
  ["40x", "94.21%", "1.063x"],
  ["100x", "87.54%", "1.146x"],
  ["200x", "78.13%", "1.273x"],
];
const vaRows = [
  ["0x", "100.00%", "1.000x"],
  ["40x", "38.77%", "2.611x"],
  ["100x", "16.23%", "6.208x"],
  ["200x", "8.81%", "11.355x"],
];
```

Label the conditions as `cpp2 noop · K=100 · server 1 IO + 1 CPU · 10 s active` and `VA FLAT echo 1 KiB · K=100 · server 1 IO + 0 CPU · 10 s active`. The only body conclusion is: `两条路径对相同注入倍率呈现不同的 normalized QPS 与 p99 响应；两组 workload 与 CPU 配置不同，不构成绝对性能 A/B。`

- [ ] **Step 3: Implement the calibration slide**

Place `04_microbenchmark_calibration.png` as the main left evidence and `07_fixed_outstanding_comparison.png` in the upper right. Add this table in the lower right:

```js
const calibrationRows = [
  ["push-only / 496", "85.4 ns/op", "1009.8 ns/op"],
  ["pop-only / 496", "95.0 ns/op", "1024.1 ns/op"],
  ["push+pop / 2", "83.2 ns/op", "997.7 ns/op"],
  ["fill+drain / 992", "89.5 ns/op", "1021.8 ns/op"],
];
```

Add the body conclusion: `1 µs 配置约增加 1 µs / successful wrapper operation；effective exposure 仍是路径级敏感度指标，不是已测得的 operation count。` Add a muted boundary line: `每点单次运行；缺少 client/server 与 push/pop 分方向 counter。`

- [ ] **Step 4: Replace `buildGqm()` slide calls**

The function body must add only the three GQM content slides in the order above and export to `gqm_rpc_sensitivity_sweep_3page_zh.pptx`.

### Task 3: Implement the three Poll-idle result slides

**Files:**
- Modify: `/var/folders/wc/hk3t6khd3gj6ct0bhw5kp3l40000gn/T/codex-presentations/manual-20260722/hwqueue-poll-idle-decks/tmp/build_condensed_decks.mjs`
- Read: `thrift/perf/cpp2/performance/analysis/poll_idle_poisson_350qps/README.md`
- Read: `thrift/perf/cpp2/performance/analysis/poll_idle_poisson_350qps/data/poisson_exp_350qps_unary64.csv`

- [ ] **Step 1: Implement the CPU–p99 Pareto slide**

Use `01_cpu_latency_pareto.png` as the main plot. Add the exact condition strip and representative rows:

```js
const pollConditions = "Poisson 350 QPS · 64 B echo · 15 s · 5285 requests · BUD={0,16,128,1024} · SLEEP={1,10,100,1000,10000} µs";
const paretoRows = [
  ["HOT POLL", "98.67 µs", "100.25%", "97.75%"],
  ["TCP Socket", "99.65 µs", "1.19%", "0.72%"],
  ["BUD=0 / 10 µs", "99.13 µs", "13.55%", "13.29%"],
  ["BUD=1024 / 10 µs", "98.69 µs", "97.81%", "93.90%"],
  ["BUD=0 / 1 ms", "1.888 ms", "1.13%", "0.70%"],
];
```

Add one body conclusion: `在本次 350 QPS 单次 sweep 中，BUD=0 / SLEEP=10 µs 的 p99 接近 HOT，同时 client/server CPU 明显降低。`

- [ ] **Step 2: Implement the latency sweep slide**

Use `02_latency_by_sleep.png` across most of the slide. Add a narrow table with representative p99 values:

```js
const latencyRows = [
  ["1 µs", "98.72–98.97 µs"],
  ["10 µs", "98.69–99.13 µs"],
  ["100 µs", "179.95–270.72 µs"],
  ["1 ms", "1.130–1.888 ms"],
  ["10 ms", "16.125–18.674 ms"],
];
```

Show only the factual note: `p99.9 约对应 5 个尾部样本，仅用于趋势观察。` Do not add a conclusion band.

- [ ] **Step 3: Implement the CPU sweep and heatmap slide**

Place `03_cpu_by_sleep.png` on the left and `04_parameter_heatmaps.png` on the right. Add a small representative-configuration table reusing HOT, TCP, BUD=0/10 µs, BUD=1024/10 µs, and BUD=0/1 ms. Add the body conclusion: `本次 sweep 中，SLEEP 档位对应主要 latency 区间，BUD 改变相同 SLEEP 下的 CPU 占用；不外推为跨机器默认值。`

- [ ] **Step 4: Replace `buildPoll()` slide calls**

The function body must add only the three Poll-idle content slides in the order above and export to `poll_idle_poisson_350qps_3page_zh.pptx`.

### Task 4: Render, inspect, and deliver

**Files:**
- Generate: `outputs/gqm_rpc_sensitivity_sweep_3page_zh.pptx`
- Generate: `outputs/poll_idle_poisson_350qps_3page_zh.pptx`
- Preserve: `outputs/gqm_rpc_sensitivity_sweep_zh.pptx`
- Preserve: `outputs/poll_idle_poisson_350qps_zh.pptx`

- [ ] **Step 1: Run the artifact-tool builder**

Run:

```bash
cd /var/folders/wc/hk3t6khd3gj6ct0bhw5kp3l40000gn/T/codex-presentations/manual-20260722/hwqueue-poll-idle-decks/tmp
node build_condensed_decks.mjs
```

Expected: both condensed PPTX absolute paths are printed and the command exits `0`.

- [ ] **Step 2: Render both decks**

Run the bundled `render_slides.py` against each PPTX. Expected: three PNGs per deck with no rendering error.

- [ ] **Step 3: Inspect every slide at full size**

Check the six rendered PNGs individually. Required fixes: no title wrapping, illegible chart axes, clipped legends, unintended overlaps, blank placeholders, or inconsistent source/page footers.

- [ ] **Step 4: Run structural QA**

Run:

```bash
/Users/jialiuyu/.cache/codex-runtimes/codex-primary-runtime/dependencies/python/bin/python3 \
  /Users/jialiuyu/.codex/plugins/cache/openai-primary-runtime/presentations/26.715.12143/skills/presentations/container_tools/slides_test.py \
  outputs/gqm_rpc_sensitivity_sweep_3page_zh.pptx

/Users/jialiuyu/.cache/codex-runtimes/codex-primary-runtime/dependencies/python/bin/python3 \
  /Users/jialiuyu/.codex/plugins/cache/openai-primary-runtime/presentations/26.715.12143/skills/presentations/container_tools/slides_test.py \
  outputs/poll_idle_poisson_350qps_3page_zh.pptx
```

Expected for each: `Test passed. No overflow detected.`

- [ ] **Step 5: Verify page counts and PPTX integrity**

Run:

```bash
for f in outputs/gqm_rpc_sensitivity_sweep_3page_zh.pptx outputs/poll_idle_poisson_350qps_3page_zh.pptx; do
  unzip -t "$f" | tail -1
  unzip -p "$f" ppt/presentation.xml | tr '<' '\n' | rg -c '^p:sldId '
done
```

Expected: no compressed-data errors and page count `3` for each deck.

- [ ] **Step 6: Move all render and inspection intermediates back to scratch QA**

Leave only the four final PPTX files in `outputs/`. Preserve the detailed originals and overwrite only the two condensed deliverables.
