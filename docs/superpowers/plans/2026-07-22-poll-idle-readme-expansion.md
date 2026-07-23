# Poll-idle Poisson 350 QPS README Expansion Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 将现有 poll-idle 绘图说明扩写为一篇包含实验目的、envelope、完整中间结果、克制结论和后续实验建议的中文报告。

**Architecture:** 只修改现有 Markdown 报告及已批准的设计说明，不改变 CSV、绘图脚本或图片。报告以 CPU–p99 主取舍为主线，再分别展开 sleep interval 和 empty-poll budget 的敏感度，并把所有判断限定在当前单次 350 QPS sweep 内。

**Tech Stack:** Markdown、CSV、现有 Python/pytest 分析包、PNG/SVG 图表

---

### Task 1: 统一 empty-poll budget 参数名称

**Files:**
- Modify: `docs/superpowers/specs/2026-07-22-poll-idle-readme-expansion-design.md`

- [ ] **Step 1: 更新设计说明中的术语约定**

将第一次出现的参数定义写成：

```markdown
原始 CSV 和图表使用字段标签 `BUD`；本文统一称为 `Empty-poll budget`
（空轮询预算），表示进入 idle sleep 前允许的 empty-poll 次数。
```

后续标题和解释优先使用 `empty-poll budget`；只有在引用原始字段、配置名或图例时保留
`BUD`，避免把未知来源的三字母标签写成未经证实的官方缩写。

- [ ] **Step 2: 检查设计说明格式**

Run:

```bash
git diff --check -- docs/superpowers/specs/2026-07-22-poll-idle-readme-expansion-design.md
```

Expected: exit code 0，无输出。

### Task 2: 扩写现有实验 README

**Files:**
- Modify: `thrift/perf/cpp2/performance/analysis/poll_idle_poisson_350qps/README.md`
- Read: `thrift/perf/cpp2/performance/analysis/poll_idle_poisson_350qps/data/poisson_exp_350qps_unary64.csv`
- Read: `thrift/perf/cpp2/performance/analysis/poll_idle_poisson_350qps/figures/*.png`

- [ ] **Step 1: 重写标题、摘要和术语定义**

使用以下章节顺序：

```markdown
# Poll-idle 在 Poisson 350 QPS 下的 CPU–尾延迟取舍
## 结论摘要
## 1. 实验目的
## 2. 参数与实验 Envelope
## 3. CPU–p99 主取舍
## 4. Sleep interval 的 latency 敏感度
## 5. Empty-poll budget 的 CPU 敏感度
## 6. 可以与不能从当前结果推断什么
## 7. 对 poll-idle 设计的含义
## 8. 下一轮实验
## 9. 复现分析
## 10. 图表输出
## 11. 结论边界
```

在参数定义中明确：

```markdown
- `Empty-poll budget`（空轮询预算，原始 CSV 和图例标为 `BUD`）：进入 idle sleep
  前允许的 empty-poll 次数。
- `Sleep interval`（原始字段为 `sleep_us`，图例标为 `SLEEP`）：进入 idle 后配置的
  sleep 时长。
```

- [ ] **Step 2: 写入实验 envelope 与结论摘要**

固定写入以下已知条件和边界：

```markdown
arrival=poisson_exp, target QPS=350, payload=64 B, handler=echo,
measurement=15 s, requests=5285,
empty-poll budget={0,16,128,1024}, sleep interval={1,10,100,1000,10000} us,
references={HOT POLL,TCP Socket}, runs per point=1
```

摘要必须包含以下事实：

```markdown
BUD=0/SLEEP=10 us: p99=99.13 us, client/server CPU=13.55%/13.29%
HOT POLL: p99=98.67 us, client/server CPU=100.25%/97.75%
TCP Socket: p99=99.65 us, client/server CPU=1.19%/0.72%
```

由这些数据描述 p99 增加 `0.46 us`（约 `0.47%`），client/server CPU 分别降低约
`86.5%`/`86.4%`。明确该点只是当前数据中满足 TCP p99 参考线的最低 CPU idle 配置，
不是跨环境默认值，也没有达到 TCP 的 CPU 水平。

- [ ] **Step 3: 写入 CPU–p99 代表点和 Pareto 图**

嵌入：

```markdown
![Client/server CPU 与 RPC p99 的 Pareto 关系](figures/01_cpu_latency_pareto.png)
```

代表点表必须包含 HOT、TCP、BUD=0/SLEEP=10 us、BUD=0/SLEEP=100 us、
BUD=0/SLEEP=1 ms，并列出 p50、p99、p99.9、client CPU、server CPU。使用 CSV 原值，
不补写无法从 artifact 确认的机器或 build provenance。

- [ ] **Step 4: 写入 sleep interval 敏感度**

嵌入：

```markdown
![不同 empty-poll budget 下的 latency–sleep interval 曲线](figures/02_latency_by_sleep.png)
```

逐档写入跨 budget 的 p99 范围：

```text
1 us:    98.72–98.97 us
10 us:   98.69–99.13 us
100 us:  179.95–270.72 us
1 ms:    1.130–1.888 ms
10 ms:   16.125–18.674 ms
```

只将其描述为当前数据中 sleep interval 对应主要 latency regime，不声称已测得实际睡眠
时长或 wakeup latency。

- [ ] **Step 5: 写入 empty-poll budget 敏感度**

嵌入：

```markdown
![不同 empty-poll budget 下的 CPU–sleep interval 曲线](figures/03_cpu_by_sleep.png)
![Empty-poll budget 与 sleep interval 参数矩阵](figures/04_parameter_heatmaps.png)
```

固定 `SLEEP=10 us` 的完整对照表使用：

```text
BUD=0:    p99=99.13 us, client/server CPU=13.55%/13.29%
BUD=16:   p99=98.88 us, client/server CPU=40.94%/40.30%
BUD=128:  p99=98.76 us, client/server CPU=82.99%/81.16%
BUD=1024: p99=98.69 us, client/server CPU=97.81%/93.90%
```

将结果描述为固定 10 us 配置下，budget 增大与 CPU 明显上升相关，而 p99 变化为
`0.44 us`；不把单次 sweep 写成严格因果证明。

- [ ] **Step 6: 写入解释边界、设计含义和下一轮实验**

明确当前结果可以用于识别候选 CPU–latency knee；不能确定跨机器默认值、重复方差、
actual sleep/wakeup 时序、publish-to-detect latency 或稳定 p99.9。下一轮列出：每点至少
3 次重复、QPS sweep、实际 idle/sleep/wakeup 指标、budget 的时间归一化、core pinning
和 CPU 统计窗口记录，以及更长运行时长。

保留并整理现有 `uv sync --group dev`、`uv run poll-idle-plot` 和 pytest 命令；只呈现
CPU 与 latency 指标，不展开 CSV 中的其他运行状态字段。

### Task 3: 验证 README 与数据一致

**Files:**
- Test: `thrift/perf/cpp2/performance/analysis/poll_idle_poisson_350qps/tests/test_poll_idle_analysis.py`
- Verify: `thrift/perf/cpp2/performance/analysis/poll_idle_poisson_350qps/README.md`

- [ ] **Step 1: 检查 Markdown diff 和图片链接**

Run:

```bash
git diff --check
for image in 01_cpu_latency_pareto 02_latency_by_sleep 03_cpu_by_sleep 04_parameter_heatmaps; do
  test -f "thrift/perf/cpp2/performance/analysis/poll_idle_poisson_350qps/figures/${image}.png"
done
```

Expected: exit code 0，无错误输出。

- [ ] **Step 2: 运行现有分析测试**

Run:

```bash
cd thrift/perf/cpp2/performance/analysis/poll_idle_poisson_350qps
uv run --group dev pytest -q
```

Expected: all tests pass。

- [ ] **Step 3: 检查变更范围**

Run:

```bash
git status --short
git diff --stat HEAD
```

Expected: 只包含目标 README、术语更新后的设计说明和本实施计划。

- [ ] **Step 4: 提交实现**

```bash
git add \
  docs/superpowers/specs/2026-07-22-poll-idle-readme-expansion-design.md \
  docs/superpowers/plans/2026-07-22-poll-idle-readme-expansion.md \
  thrift/perf/cpp2/performance/analysis/poll_idle_poisson_350qps/README.md
git commit -m "docs: expand poll-idle sensitivity report"
```
