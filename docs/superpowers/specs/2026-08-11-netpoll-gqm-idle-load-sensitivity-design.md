# Netpoll GQM Idle 负载敏感性第一版呈现设计

## 目标

把用户提供的 `QPS × BUD × Sleep` 结果整理为一份可复现的性能分析目录，回答不同负载下
分级 idle 参数如何影响吞吐维持率、尾延迟和 CPU，并使用同 workload/resource envelope
的 Netpoll Socket 结果作为系统级事件驱动锚点。Socket 不是 GQM IRQ 的替代测量，不能
据此直接宣布 GQM IRQ 必要或不必要。

## 数据范围与显示约定

- 固定条件：`Concurrency=128`、`Payload=1024 B`、`Cost=0`。
- QPS：`7700, 77000, 192500, 385000, 731500`。
- BUD：`0, 1, 16, 256, 1024`。
- Sleep：`1, 10, 100, 1000, 10000 us`。
- 用户确认 `BUD=0` 与 `BUD=256` 因配置失误实际执行的是相同策略。125 行 GQM 原始数据
  仍全部保留；用于 Pareto 和策略边界时，同一 `QPS × Sleep` 下的这两行合并为一个
  `BUD=0/256 duplicate` 两次重复候选。
- Socket 锚点：相同五档 target QPS，固定 `Concurrency=128`、`Payload=1024 B`。
- 原始 tuple 在本批数据中使用相同值；图中压缩显示为单个 `BUD` 和 `Sleep (us)`，原始
  文本仍逐字保留。
- 理论矩阵为 `5 × 5 × 5 = 125` 点。附件前 124 点字段完整；最后一个
  `731500 / BUD=1024 / Sleep=10000us` 只保留到 TPS，缺少 latency、CPU、RSS、Retry
  和 Status。缺失字段记为 `N/A`，不得推测或插值。

## 输出目录

新增：

```text
thrift/perf/cpp2/performance/analysis/netpoll_gqm_idle_load_sensitivity/
  README.md
  data/raw/netpoll_gqm_idle_load_sensitivity.txt
  data/netpoll_gqm_idle_load_sensitivity.csv
  data/netpoll_gqm_idle_load_sensitivity_summary.csv
  data/netpoll_socket_qps_baseline.csv
  data/netpoll_idle_tradeoff_candidates.csv
  data/raw/netpoll_socket_qps_baseline.txt
  data/netpoll_gqm_idle_policy_boundaries.csv
  figures/01_sc_symmetric_cpu_p99_tradeoff.{png,svg}
  figures/02_p99_budget_minimum_cpu_boundary.{png,svg}
  src/netpoll_gqm_idle_analysis.py
  tests/test_netpoll_gqm_idle_analysis.py
  pyproject.toml
  uv.lock
```

## 派生指标

每个完整点计算：

```text
attainment_pct = TPS / TargetQPS × 100
total_cpu_pct  = ClientCPU + ServerCPU
used_cores     = total_cpu_pct / 100
qps_per_core   = TPS / used_cores
```

`attainment_pct >= 99.5%` 的点标记为 `target sustained`。`731500 QPS` 档所有完整点均低于
该阈值，因此整档按 near-capacity stress 展示，不从中选择“满足目标”的最佳配置。

Pareto 只在同一 QPS 档内计算。对能够维持目标的档位，候选点必须先满足 attainment gate，
再以 `total_cpu_pct` 和 `p99` 两个越低越好的指标判定支配关系。`BUD=0/256 duplicate`
候选以两次测量均值作为中心，以 min-max 显示两次重复范围；只有两次都通过 gate 才算
合格。Socket 按同一 gate 进入候选。near-capacity 档保留所有点，但不与达到目标的档位
使用相同的合格语义。

## 容器与 CPU 口径

- 策略在 server/client 两端对称配置，同一数据点使用相同 BUD 和 Sleep。
- Client 容器配额为 `8 vCPU`，Server 容器配额为 `8 vCPU`，两端合计 `16 vCPU`。
- `Total CPU cores = (Client process CPU% + Server process CPU%) / 100`。该数值是两端
  所有线程累计 CPU 时间折算出的 vCPU-equivalents，不表示单线程占用，也不区分物理核、
  SMT sibling 或线程间分布。
- 图 1 下方横轴显示 Total CPU cores，上方横轴显示相对 `16 vCPU` 合计容器配额的占用率。

## 策略选择边界

对每个 target QPS 和给定的 P99 SLO，使用以下确定性规则：

```text
BestPolicy(QPS, P99_budget)
= argmin TotalCPU(policy)
  subject to attainment >= 99.5%
             p99 <= P99_budget
```

边界只由满足 attainment gate 的实测点组成。每个策略区间采用左闭右开语义；最后一个区间
延伸到无穷。边界使用候选中心点；`BUD=0/256 duplicate` 的 min-max 只表示两次重复范围，
不是置信区间。其他 GQM 候选和 Socket 均为单次结果。`731.5K QPS` 没有满足 gate 的策略，
不生成选择区间。

## 图表

### 图 1：S/C 对称策略的 CPU–P99 取舍

- 五个 QPS 子图，分别自适应 CPU 横轴，避免低负载优势被统一大坐标压扁；P99 使用对数轴。
- 满足 gate 的 GQM 点按策略着色、按 Sleep 使用点形；未满足 gate 的点使用浅灰标记。
- `BUD=0/256 duplicate` 使用均值中心和横纵 min-max whisker，避免把重复波动误画成策略切换。
- Socket 使用五角星并直接标注 CPU、P99 和 attainment；水平/垂直投影虚线突出其绝对值。
- 黑色虚线连接同一负载内 GQM 与合格 Socket 的联合 Pareto frontier。
- 下方横轴是两端 Total CPU cores；上方横轴是 `TotalCPU / 16 vCPU`。
- `731.5K QPS` 子图只显示 near-capacity observations，并明确不存在合格 frontier。

### 图 2：给定 P99 SLO 时的最低 CPU 边界

- 对能够维持目标的四档负载绘制阶梯曲线；横轴为允许的 P99 budget，纵轴为满足该 SLO
  所需的最小 Total CPU cores。
- 每个边界变化点直接标注策略、P99 起点和 CPU 数值，不使用“主观 knee”或综合评分。
- 第五档 `731.5K QPS` 明确标记为没有满足 attainment gate 的可选策略。
- Socket 作为同一选择规则下的候选策略；若通过 gate 且赢得某个 SLO 区间，边界在该
  区间切换到 Socket，否则只保留为系统级参考点。

两张图使用既有 Netpoll 分析目录的论文风格：白底、克制配色、细网格、图内只保留读图
所需信息，总标题位于顶部，方法性图注位于底部。

## README 结构

1. 首段给出实验矩阵、固定配置、数据完整性和三个派生指标。
2. 用一张紧凑表保留每档 QPS 的完整点数、最大 TPS、最大 attainment，以及满足 gate 的
   cell 数量。
3. 依次展示两张图；每张图先陈述读图方法，再列直接观察和可执行选择规则。
4. 附录保留全部 125 行规范化数据，其中缺失字段保持空值。
5. 结尾只给第一阶段事实：低负载主要体现 CPU–tail trade-off；负载上升后，小 BUD/长
   Sleep 逐步失去 capacity；大 BUD 保持 capacity 的同时接近 polling CPU 成本。
6. 明确证据边界：重复覆盖不完整、Socket 不是同 transport IRQ A/B、无实际 stage
   residency/empty-pop/wakeup 计数，因此不直接给出 IRQ 结论。

## 验证

- 解析测试：应得到 125 个配置键、124 个完整点和 1 个部分点，且配置组合不得重复。
- 数值测试：随机抽取原始表首、中、末完整点核对 TPS、p99 和两端 CPU。
- 派生测试：验证 attainment、total CPU、used cores 和 Pareto 判定。
- 重复测试：核对 25 对 `BUD=0/256` 分类一致，合并中心及 min-max 范围正确。
- Socket 测试：核对 5 个锚点和前三点通过、后两点不通过 99.5% gate。
- 边界测试：核对四档负载的联合策略区间和 `731.5K` 空边界。
- 图形测试：检查两张 SVG 的标题、两端 `8 vCPU`、`16 vCPU`、S/C 对称、CPU 口径、
  P99 budget、Payload、Concurrency 和 near-capacity 文本。
- 运行现有 EDR 校验器，保证新增分析目录不破坏性能知识库。
- 对两张 PNG 做人工视觉检查，确认无标题、图例、标注和坐标轴重叠。
