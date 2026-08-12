# Netpoll GQM Idle 负载敏感性第一版呈现设计

## 目标

把用户提供的 `QPS × BUD × Sleep` 结果整理为一份可复现的性能分析目录，回答不同负载下
分级 idle 参数如何影响吞吐维持率、尾延迟和 CPU。第一版只呈现实测关系，不把 Socket
当作本批数据的隐含基线，也不据此直接宣布 GQM IRQ 必要或不必要。

## 数据范围与显示约定

- 固定条件：`Concurrency=128`、`Payload=1024 B`、`Cost=0`。
- QPS：`7700, 77000, 192500, 385000, 731500`。
- BUD：`0, 1, 16, 256, 1024`。
- Sleep：`1, 10, 100, 1000, 10000 us`。
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
  figures/01_throughput_attainment_heatmap.{png,svg}
  figures/02_p99_latency_heatmap.{png,svg}
  figures/03_cpu_p99_pareto.{png,svg}
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
再以 `total_cpu_pct` 和 `p99` 两个越低越好的指标判定支配关系。near-capacity 档保留所有点，
但不与达到目标的档位使用相同的合格语义。

## 图表

### 图 1：Throughput Attainment

- 五个 QPS 子图，共享坐标和色标。
- 横轴为 Sleep，使用离散对数档位；纵轴为 BUD。
- 单元格标注 `TPS / Target` 百分比。
- `>=99.5%` 与 `<99.5%` 使用直观但色盲友好的连续色带，并用边框区分 gate。
- 最后一处缺失 cell 显示 `N/A` 和斜线填充。

### 图 2：P99 Latency

- 与图 1 使用完全相同的五子图布局，降低读图转换成本。
- 单元格标注绝对 p99，并自动切换 `us/ms`。
- 色彩使用对数归一化，避免 `20us–20ms` 的跨度遮蔽低延迟区域。
- 未维持目标吞吐的 cell 降低饱和度并加斜线，防止把低 TPS 下的 p99 当作有效优势。

### 图 3：CPU–P99 Pareto

- 五个 QPS 子图；横轴为 client + server CPU 对应的 used cores，纵轴为 p99，纵轴采用
  对数尺度。
- BUD 使用颜色，Sleep 使用点形或简短点旁标注。
- 达到目标的点使用实心标记；未达到目标的点使用空心标记。
- 仅连接并标注 Pareto frontier，其他点降低透明度，避免 25 个图例造成心智负担。
- `731500 QPS` 子图标题明确写为 near-capacity stress。

所有图使用既有 Netpoll 分析目录的论文风格：白底、克制配色、细网格、图内无大段结论，
总标题在顶部，简短图注由 README 放在图片下方。

## README 结构

1. 首段给出实验矩阵、固定配置、数据完整性和三个派生指标。
2. 用一张紧凑表保留每档 QPS 的完整点数、最大 TPS、最大 attainment，以及满足 gate 的
   cell 数量。
3. 依次展示三张图；每张图先陈述读图方法，再列 2–4 条直接观察。
4. 附录保留全部 125 行规范化数据，其中缺失字段保持空值。
5. 结尾只给第一阶段事实：低负载主要体现 CPU–tail trade-off；负载上升后，小 BUD/长
   Sleep 逐步失去 capacity；大 BUD 保持 capacity 的同时接近 polling CPU 成本。
6. 明确证据边界：单次 run、无 Socket 对照、无实际 stage residency/empty-pop/wakeup
   计数，因此不直接给出 IRQ 结论。

## 验证

- 解析测试：应得到 125 个配置键、124 个完整点和 1 个部分点，且配置组合不得重复。
- 数值测试：随机抽取原始表首、中、末完整点核对 TPS、p99 和两端 CPU。
- 派生测试：验证 attainment、total CPU、used cores 和 Pareto 判定。
- 图形测试：检查三张 SVG 的标题、轴标签、五个负载标题、Payload 文本和 `N/A` 标记。
- 运行现有 EDR 校验器，保证新增分析目录不破坏性能知识库。
- 对三张 PNG 做人工视觉检查，确认无标题、图例、标注和色条重叠。
