# Poll-idle Poisson 350 QPS 绘图

这组图来自一次 `poisson_exp`、350 QPS、64 B payload、echo handler、15 秒的单次实验，
包含 5285 个请求。原始汇总表已经规范化为
[`data/poisson_exp_350qps_unary64.csv`](data/poisson_exp_350qps_unary64.csv)。

## 运行

本目录使用 `uv` 创建和管理本地 `.venv`：

```bash
uv sync --group dev
uv run poll-idle-plot
```

指定其他兼容 CSV 或输出目录：

```bash
uv run poll-idle-plot \
  --input data/poisson_exp_350qps_unary64.csv \
  --output-dir figures
```

运行测试：

```bash
uv run --group dev pytest -q
```

## 输出

- `01_cpu_latency_pareto`：RPC p99 主取舍图，左侧使用 client CPU，右侧使用 server
  CPU；按 BUD 着色的散点直接标注 `BUD/SLEEP`，灰线表示 Pareto 前沿。Y 轴在
  `150 us` 处断开，下段线性放大 TCP 附近的密集结果，上段保留对数尺度；TCP Socket
  使用紫色菱形标注，并绘制横、纵参考虚线。
- `02_latency_by_sleep`：不同 BUD 下，SLEEP 对 p50/p99/p99.9 的影响。每个指标使用
  独立断轴，下部线性放大 HOT 到 Socket 的延迟预算并用淡紫色背景标识，上部使用
  对数尺度压缩高延迟数据作为趋势上下文。
- `03_cpu_by_sleep`：不同 BUD 下，SLEEP 对 client CPU 和 server CPU 的影响。
- `04_parameter_heatmaps`：`BUD x SLEEP` 参数矩阵，直观看 CPU 和延迟的交互。

每张图同时生成 PNG 和 SVG。CSV 保留 drain 状态，但绘图不区分这些点，所有配置都按
普通测量结果展示。

## 数据限制

- 只有一次 15 秒运行，没有重复实验和误差棒。
- 5285 个请求只能给 p99.9 提供大约 5 个尾部样本，因此 p99.9 只能用于观察趋势。
- 表中没有 `time_to_first_idle`、实际等待时间和 `publish-to-detect`，无法绘制这三个指标。
- 当前 `BUD` 仍是次数，尚未换算为可跨机器比较的微秒时间。
- CPU 主取舍图并列展示 `client_cpu_pct` 和 `server_cpu_pct`。
