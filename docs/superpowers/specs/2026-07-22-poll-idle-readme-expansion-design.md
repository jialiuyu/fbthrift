# Poll-idle Poisson 350 QPS README 扩写设计

## 目标

将 `thrift/perf/cpp2/performance/analysis/poll_idle_poisson_350qps/README.md`
从绘图说明扩写为可独立阅读的实验报告，使读者无需打开分析脚本即可了解：

- 实验试图回答的问题；
- `BUD` 与 `SLEEP` 的 sweep 范围和已知实验 envelope；
- CPU、p50、p99 与 p99.9 的主要观测结果；
- 当前数据支持哪些结论，以及不能外推什么；
- 下一轮实验需要补齐哪些证据。

本次只修改现有 README，不新增 EDR，不修改 CSV、分析代码或既有图表。

## 叙事原则

- 以实验事实和代表数据为主体，机制解释保持克制。
- 将结论限定在当前一次 350 QPS、15 秒 sweep 内，不表述为跨机器默认配置。
- 区分“配置值”与“实际运行行为”：`SLEEP` 是配置的 sleep interval，
  `BUD` 按本实验命名解释为进入 idle sleep 前的 empty-poll budget；当前数据未记录
  actual sleep duration、time-to-first-idle 或 publish-to-detect latency。
- 只报告 CPU 与 latency 指标，不展开 CSV 中的其他运行状态字段。
- 保留当前 `uv run` 复现命令和四张既有结果图。

## README 结构

### 1. 标题与摘要

标题改为“Poll-idle 在 Poisson 350 QPS 下的 CPU–尾延迟取舍”。摘要说明报告关注低负载下
`BUD`、`SLEEP` 如何改变 CPU 与尾延迟，以及当前 envelope 中可观察到的取舍点。

### 2. 结论摘要

使用短列表陈述以下有边界的事实：

- `BUD=0, SLEEP=10 us` 的 p99 为 99.13 us，相比 HOT POLL 的 98.67 us
  增加 0.46 us（约 0.47%）。
- 该点的 client/server CPU 为 13.55%/13.29%，相比 HOT POLL 的
  100.25%/97.75% 分别下降约 86.5%/86.4%。
- 在当前数据中，它是满足 TCP Socket p99 基线（99.65 us）的最低 CPU idle 配置；
  但 CPU 仍明显高于 TCP Socket 的 1.19%/0.72%。
- `SLEEP` 档位对应主要 latency 区间；固定 `SLEEP=10 us` 时，增大 `BUD`
  主要表现为 CPU 上升，而 p99 只发生亚微秒级变化。
- 单次运行不足以确定通用默认值或稳定的 p99.9 结论。

### 3. 实验目的与 envelope

列出已知条件：Poisson 350 QPS、64 B echo、15 秒、5285 requests，
`BUD={0,16,128,1024}`，`SLEEP={1,10,100,1000,10000} us`，以及
HOT POLL、TCP Socket 两个参考点。

同时明确当前 artifact 未包含机器、CPU、firmware、频率、拓扑、build SHA、完整命令、
core pinning 和 CPU 统计窗口，因此不补写无法验证的 provenance。

### 4. CPU–p99 主取舍

嵌入 `figures/01_cpu_latency_pareto.png`，并用代表点表展示 HOT、TCP、
`BUD=0/SLEEP=10 us`、`BUD=0/SLEEP=100 us`、`BUD=0/SLEEP=1 ms`
等配置的 p50、p99、p99.9 与两端 CPU。

正文只描述当前数据中的 Pareto 关系，不将某一点宣布为跨环境最优值。

### 5. SLEEP 的 latency 敏感度

嵌入 `figures/02_latency_by_sleep.png`，列出各 `SLEEP` 档位跨 `BUD` 的 p99 范围：

- 1 us：98.72–98.97 us；
- 10 us：98.69–99.13 us；
- 100 us：179.95–270.72 us；
- 1 ms：1.130–1.888 ms；
- 10 ms：16.125–18.674 ms。

用这些范围支持“SLEEP 档位对应主要 latency regime”的描述。

### 6. BUD 的 CPU 敏感度

嵌入 `figures/03_cpu_by_sleep.png` 和 `figures/04_parameter_heatmaps.png`。
固定 `SLEEP=10 us` 给出完整 BUD 对照表：

- `BUD=0`：p99 99.13 us，client/server CPU 13.55%/13.29%；
- `BUD=16`：p99 98.88 us，CPU 40.94%/40.30%；
- `BUD=128`：p99 98.76 us，CPU 82.99%/81.16%；
- `BUD=1024`：p99 98.69 us，CPU 97.81%/93.90%。

### 7. 解释边界与设计含义

单独区分“可以说明”和“尚不能说明”：

- 可以说明当前负载下配置参数与 CPU/latency 的观测关联，以及若干 latency budget
  下的候选配置。
- 不能说明不同机器上的最优默认值、实际 sleep/wakeup 时序、因果机制、重复运行方差，
  或 p99.9 的稳定性。

设计含义只作浅层表达：当前结果支持继续围绕短 sleep interval、低 empty-poll budget
探索 CPU–latency knee；若硬件或 firmware 希望进一步优化，需要先补充实际 idle、wakeup
和通知检测时序，而不是只依据配置值推断。

### 8. 后续实验与复现

下一轮建议覆盖：每点至少三次重复、多个 QPS 档位、实际 idle/sleep/wakeup 指标、
将 BUD 从迭代次数归一化到时间、固定并记录 CPU 统计窗口和 core pinning，以及延长运行
以提高 p99.9 样本量。

末尾保留现有分析命令、输出图清单和 CSV 的单次运行限制。

## 验证

- 对 README 中所有代表数据与原始 CSV 逐项核对。
- 检查四个相对图片链接和复现命令仍然有效。
- 运行现有 `tests/test_poll_idle_analysis.py`，确认文档改动未伴随分析回归。
- 检查最终 diff，确保只修改目标 README 和本设计说明。
