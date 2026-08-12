# fbthrift Ubmem Idle 负载敏感性报告设计

## 目标

把用户提供的 fbthrift `Target QPS × Sleep × BUD` 结果整理为一份可复现的性能分析目录，
说明固定 Server 8 vCPU、Client 4 vCPU 时，不同单层 empty-poll 退避配置如何影响目标负载
维持率、P99 和两端总 CPU。五个同 target QPS 的 Socket 结果仅作为系统级事件驱动锚点；
报告不引入 Netpoll 数据，不进行跨框架横向比较，也不把 Socket 差异解释为 GQM IRQ 收益。

## 数据范围

- fbthrift Ubmem：`5 × 4 × 4 = 80` 个完整观测点。
- Target QPS：`1.6K, 16K, 40K, 80K, 152K`。
- Sleep：idle disabled，以及 idle enabled 下的 `1us, 10us, 100us`。
- BUD：`0, 1, 16, 256`。
- Socket：同五档 target QPS 的 5 个锚点。
- 所有原始字段完整保留：吞吐、Sustain%、P50/P99/P99.9/Max、SendLagP99、
  `Scheduled→Dispatched→Completed`、两端 CPU、RSS、Retry 和 Status。

idle disabled 时 BUD 不参与实际退避决策，因此同一 target QPS 下四个 disabled 观测按
“相同有效配置的重复测量”处理：原始表保留四行，候选中心使用均值，误差棒使用 min–max；
只有四次都达到 attainment gate，聚合候选才可进入合格 frontier。该范围表示重复波动，
不是置信区间。idle enabled 的 12 个 `Sleep × BUD` 组合仍作为独立配置。

## 输出目录

```text
thrift/perf/cpp2/performance/analysis/fbthrift_idle_budget_load_sensitivity/
  README.md
  data/SOURCE.md
  data/raw/fbthrift_idle_budget_load_sensitivity.txt
  data/raw/fbthrift_socket_qps_baseline.txt
  data/fbthrift_idle_budget_load_sensitivity.csv
  data/fbthrift_idle_budget_load_sensitivity_derived.csv
  data/fbthrift_idle_budget_load_sensitivity_summary.csv
  data/fbthrift_socket_qps_baseline.csv
  data/fbthrift_idle_tradeoff_candidates.csv
  data/fbthrift_idle_policy_boundaries.csv
  figures/01_fbthrift_cpu_p99_tradeoff.{png,svg}
  figures/02_fbthrift_p99_budget_minimum_cpu_boundary.{png,svg}
  src/fbthrift_idle_analysis.py
  tests/test_fbthrift_idle_analysis.py
  pyproject.toml
  uv.lock
```

## 派生指标与合格条件

每个观测计算：

```text
total_cpu_pct = client_cpu_pct + server_cpu_pct
used_cores = total_cpu_pct / 100
quota_occupancy_pct = used_cores / 12 × 100
qps_per_used_core = achieved_qps / used_cores
target_sustained = reported_sustain_pct >= 99.5%
```

使用原始报告的 `Sustain%` 作为 gate 依据，避免用已四舍五入的 `Achieved KQPS` 反推。
`40K` 档 idle-disabled 四次重复中有一次为 `99.2%`，因此该聚合候选不通过 gate；
`152K` 档 idle-disabled 重复约为 `82%`，同样不通过。原始点仍完整展示。

Pareto 只在同一 target QPS 内计算。候选先满足 gate，再以 `used_cores` 和 `p99_us`
均越低越好判定支配关系。Socket 使用相同 gate：`80K` Socket 的 `99.0%` 只作为容量锚点，
不进入合格 frontier；其余达到 `99.5%` 的 Socket 点可作为系统级候选。

## CPU 与资源口径

- Server 容器：`8 vCPU`；Client 容器：`4 vCPU`；合计 `12 vCPU`。
- `Total CPU cores` 是 Client 与 Server 进程所有线程的 CPU% 相加后除以 100 得到的
  vCPU-equivalent，不表示单线程或物理核占用。
- 图的下方横轴显示 Total CPU cores，上方横轴显示相对 12 vCPU 总配额的占用率。

## 图表

### 图 1：fbthrift Ubmem CPU–P99 取舍

- 五个 target QPS 子图，横轴按各负载自适应，P99 使用对数轴。
- idle enabled 点按 BUD 着色、按 Sleep 使用点形；未通过 gate 的点弱化。
- idle-disabled 重复候选绘制均值与横纵 min–max error bar。
- Socket 使用五角星和水平/垂直投影虚线，直接标注 CPU、P99 与 attainment。
- 黑色虚线连接该负载下 Ubmem 与合格 Socket 的联合 Pareto frontier。
- 图中标题、图注和图例只写 fbthrift；不出现 Netpoll 或跨框架比较语句。

### 图 2：给定 P99 budget 的最低 CPU 边界

对每个 target QPS，按下式生成离散阶梯边界：

```text
BestPolicy(QPS, P99_budget)
= argmin TotalCPU(policy)
  subject to target_sustained
             p99 <= P99_budget
```

边界只由合格实测候选组成，区间左闭右开，最后一段延伸到无穷。每次策略切换直接标注
配置、P99 起点和 CPU；Socket 若赢得某个区间则显示为 `Socket`。边界只表达当前五档负载
和当前资源配额下的离散选择，不拟合连续最优参数，也不外推真实 IRQ 性能。

## README 结构与结论边界

README 使用总分总结构：

1. 开头给出实验目的、80+5 点范围、资源配置、gate 和 CPU 口径。
2. 用汇总表记录每档负载的完整点、合格点、最大 achieved QPS、P99 和 CPU 范围。
3. 依次展示两张图，解释怎么读、直接观察和确定性的选择规则。
4. 附录保留全部规范化数据，并链接派生表、候选表和边界表。
5. 结尾只陈述本 envelope 的事实：idle 参数的 CPU–P99 取舍随负载变化；Socket 是系统级
   锚点；当前数据不能证明 GQM IRQ 已经满足需求，也不能给出 IRQ 固定开销规格。

报告不会：

- 与 Netpoll 进行横向对比；
- 把 Socket 当作 GQM IRQ 的等价实现；
- 把单次或四次重复的 min–max 当作统计置信区间；
- 从配置 Sleep 直接推断实际 sleep、empty-pop 或 wakeup latency；
- 忽略未通过 99.5% gate 的原始结果。

## 验证

- 解析：80 个唯一 Ubmem 配置键、5 个唯一 Socket target QPS，字段逐点抽查。
- 派生：验证 Total CPU、used cores、quota occupancy、QPS/core 和 reported Sustain% gate。
- 重复：验证每档 4 个 idle-disabled 点的均值、min–max 和 all-repeats gate。
- 边界：验证每档仅使用合格候选，80K Socket 不进入边界，所有区间满足定义。
- 图形：检查两张 SVG 的 `fbthrift`、`Server 8 vCPU`、`Client 4 vCPU`、`12 vCPU total`、
  `Total CPU`、`P99 budget` 和 Socket 锚点文本；禁止出现 `Netpoll`。
- 视觉：人工检查两张 PNG 的标题、图例、误差棒、直接标注和双横轴是否重叠。
- 仓库：运行 focused pytest、SVG 文本检查、`git diff --check` 和 EDR validator。
