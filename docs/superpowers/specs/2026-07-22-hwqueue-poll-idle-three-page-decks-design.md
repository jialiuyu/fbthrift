# HWQueue 与 Poll-idle 三页学术报告设计

## 目标

面向硬件队列、RPC transport 与性能工程人员，将两份已有详细报告压缩为最多三张纯内容页，同时完整保留实验矩阵、主要中间结果和关键数据。演示用于陈述实验事实，不承担完整机制证明或硬件方案决策。

交付文件沿用现有名称：

- `outputs/gqm_rpc_sensitivity_sweep_3page_zh.pptx`
- `outputs/poll_idle_poisson_350qps_3page_zh.pptx`

原详细版保持不变。

## 内容原则

- 不设置封面、目录、结束页或独立总结页；每张均为实验内容页。
- 三页是上限，不是必须凑齐的页数。当前两份材料各需三页才能保留全部结果图。
- 标题只写实验或结果视图名称，不把结论写入标题。
- 每个子实验都展示实验条件、图表或关键数值；中间结果不强行附加结论。
- 仅对重要结果在正文中给出一至两句克制结论，并紧邻支持它的图表。
- 不写深度机制推断，不把 effective exposure 解释为已测得的 GQM operation 次数。
- 正文最小字号 16 pt，页标题至少 35 pt；优先缩短文案，不以降低字号解决拥挤。
- 使用现有报告图，不重新计算或改写数据；横轴继续使用 `0x–200x` 倍率表达。

## GQM / HWQueue 演示

### 第 1 页：RPC Baseline QPS Sweep 与 35K Open-loop Delay Sweep

用途：说明为什么选择 35K 作为压力锚点，并完整展示 open-loop delay sweep 的 latency 与 capacity 响应。

内容：

- 顶部条件条：`Unary64`、Poisson open loop、35K target QPS、单 client/connection、server 1 IO + 1 CPU、每点单次运行。
- 图 `01_rpc_saturation_knee.png`：350–50K baseline QPS sweep。
- 图 `02_gqm_delay_sensitivity.png`：0x–200x 下的 p50、p99、p99.9。
- 图 `03_gqm_delay_capacity.png`：completed QPS 与 client shed。
- 紧凑数据条：0x、40x、100x、200x 的 completed QPS、p50 与 p99。
- 正文结论只陈述：当前 35K envelope 中，100x 出现明显 latency 跳升，200x 同时出现 completed QPS 下降。

### 第 2 页：cpp2 与 VA FLAT Fixed-outstanding Delay Sweep

用途：并列展示两个 `K=100` fixed-outstanding 路径的完整敏感度，而不是只给横向结论图。

内容：

- 左侧图 `05_cpp2_closed_loop_sensitivity.png`。
- 右侧图 `06_va_flat_compressed_polling_sensitivity.png`。
- 条件说明分别标注 workload、server CPU 配置与 active window，避免被理解为严格 transport A/B。
- 每条路径附 0x、40x、100x、200x 的 QPS retention 与 p99 multiplier 小表。
- 正文只说明两条路径对相同注入倍率呈现不同响应；不在标题中写“更敏感”或“路径差异”。

### 第 3 页：GQM Wrapper 注入校准

用途：展示注入旋钮确实作用于成功 wrapper operation，并将校准结果与端到端结果放在同一页理解。

内容：

- 主图 `04_microbenchmark_calibration.png`。
- 校准表：push-only、pop-only、push+pop、fill+drain 在 100 ns 与 1000 ns 配置下的单操作增量。
- 次图 `07_fixed_outstanding_comparison.png`，作为端到端结果对照，不作为新的独立实验。
- 正文结论：1 us 配置约增加 1 us/successful wrapper operation；路径级 effective exposure 只能作为敏感度指标。
- 页面底部仅保留必要边界：每点单次运行，缺少每端、每方向 operation counter。

## Poll-idle 演示

### 第 1 页：Poll-idle Poisson 350 QPS：CPU–p99 Pareto

用途：完整交代 sweep 矩阵，并展示 client/server CPU 与 p99 的主取舍。

内容：

- 条件条：Poisson 350 QPS、64 B echo、15 秒、5285 requests。
- 参数矩阵：`BUD={0,16,128,1024}`，`SLEEP={1,10,100,1000,10000} us`，并列 HOT POLL 与 TCP Socket 参考点。
- 主图 `01_cpu_latency_pareto.png`。
- 代表点表：HOT、TCP、BUD=0/SLEEP=10 us、BUD=1024/SLEEP=10 us、BUD=0/SLEEP=1 ms。
- 正文结论只写：在本次 350 QPS 单次 sweep 中，BUD=0/SLEEP=10 us 的 p99 接近 HOT，同时 client/server CPU 明显降低。

### 第 2 页：BUD × SLEEP Latency Sweep

用途：展示 p50、p99、p99.9 随 SLEEP 和 BUD 变化的全部中间结果。

内容：

- 主图 `02_latency_by_sleep.png`，尽可能占满内容区域。
- 图旁列出各 SLEEP 档位的代表 p99 范围或代表配置数据。
- 标明 p99.9 约由 5 个尾部样本支撑，仅用于趋势观察。
- 不设置结论条，不把曲线形状总结成标题。

### 第 3 页：BUD × SLEEP CPU Sweep 与参数矩阵

用途：同时展示 CPU 曲线和完整参数热力图，并提供可复述的代表配置。

内容：

- 左侧图 `03_cpu_by_sleep.png`。
- 右侧图 `04_parameter_heatmaps.png`。
- 底部代表配置表列出 p99、client CPU、server CPU。
- 正文浅结论：本次 sweep 中，SLEEP 档位对应主要 latency 区间，BUD 改变相同 SLEEP 下的 CPU 占用；结论不外推为跨机器默认值。

## 视觉与版式

- 延续现有白底、黑字、蓝色强调色和浅灰分隔线，不引入新的视觉主题。
- 采用学术报告式平面布局，不使用封面式大标题、营销式数字卡片或独立结论页。
- 每页以一张主图加一至两张辅图或小表为主体；说明文字控制在图旁或图下的窄区域。
- 重要结论使用浅蓝正文条，但标题保持中性。
- 所有图保留原始坐标轴、图例和注释；不裁掉实验条件或参考线。

## 数据边界与验证

- GQM 结论状态沿用 `EDR-0002`、`EDR-0003` 的 `INCONCLUSIVE`，不升级置信度。
- Poll-idle 仅代表一次 15 秒、350 QPS sweep；不展示运行状态字段。
- 交付前逐页全尺寸渲染，检查标题换行、图例可读性、对象重叠、页面越界和脚注一致性。
- 运行 `slides_test.py`、PPTX 压缩结构校验与实际页数检查；输出目录只保留最终 PPTX。
