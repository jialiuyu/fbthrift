# 数据来源与规范化说明

## 来源

- 接收日期：2026-08-12。
- 用户提供的 fbthrift idle BUD 测试附件已逐字归档为
  `data/raw/fbthrift_idle_budget_load_sensitivity.txt`。
- Ubmem 原始归档 SHA-256：
  `4a037aae94af1577f2ff0be9dcb274a44d08c66dea9c5da8a2c5f570b4930da4`。
- 用户在聊天中补充五档 Socket 结果，逐字归档为
  `data/raw/fbthrift_socket_qps_baseline.txt`。
- Socket 原始归档 SHA-256：
  `e3caa2f79e42fa99a0b50c6e366ec19ed93fa6ec2ffd5b90db88632b2a2825f8`。

当前开源 worktree snapshot 为：

```text
folly    1108d3b98255f6ae08dbc98f265011664133ecc6
fbthrift eb77b990ef2fcc9ec6bc04bf358981289c0d03f7
branch   agent/codex/cxl-mem-rocket-benchmark
```

这些 SHA 只描述整理报告时的开源 worktree，不是内部测试 binary 的精确 build provenance。
来源没有重复保存机器型号、NUMA placement、运行顺序、单点运行时长、payload、binary
checksum 或 firmware revision，因此报告不补写这些字段。

## 矩阵与字段映射

原始 `Value` 形式为：

```text
target QPS × idle_enabled/sleep tuple × BUD tuple
```

规范化为：

```text
Target QPS = 1.6K, 16K, 40K, 80K, 152K
Idle       = disabled, or enabled with Sleep=1us, 10us, 100us
BUD        = 0, 1, 16, 256
```

理论矩阵和实际归档均为 `5 × 4 × 4 = 80` 个完整 Ubmem cell。五个 Socket 锚点使用同一
target QPS 集合。原始表中的吞吐、Sustain%、P50/P99/P99.9/Max、SendLagP99、
`Scheduled→Dispatched→Completed`、shed、两端 CPU、RSS、Retry 和 Status 全部进入 CSV。

`K` 按十进制 `1000` 展开；逗号仅作为千位分隔符移除。CPU 原始格式为
`total (user/system)`，规范化 CSV 将三者拆成独立列。

## Gate、CPU 与重复语义

- `target_sustained = reported_sustain_pct >= 99.5%`。这里使用原始报告的 Sustain%，不从
  已按 `KQPS` 四舍五入的 Achieved 字段反推。
- `Total CPU = Client process CPU% + Server process CPU%`。
- `used_cores = Total CPU / 100`，表示两端全部线程累计 CPU 时间折算的
  vCPU-equivalents，不表示单线程、物理核或 SMT sibling 占用。
- Server 容器为 `8 vCPU`、Client 容器为 `4 vCPU`，因此
  `quota_occupancy = used_cores / 12 × 100%`。

idle disabled 时 BUD 不参与实际退避决策。同一 target QPS 下的四行仍全部保留，但在候选
表、Pareto 和策略边界中聚合成 `Polling baseline` 的四次重复：中心取算术均值，误差棒
取 min–max，只有四次都通过 gate 才标记为合格。该范围不是置信区间。

Socket 结果包含 transport 和 software stack 的整体差异，只作为系统级事件驱动锚点；
它不是 GQM IRQ 测量，也不能用于把差异归因于 IRQ。

## 文件

- `fbthrift_idle_budget_load_sensitivity.csv`：80 个规范化 Ubmem 原始点。
- `fbthrift_socket_qps_baseline.csv`：5 个规范化 Socket 锚点。
- `fbthrift_idle_budget_load_sensitivity_derived.csv`：逐点 CPU、QPS/core、quota 和 gate。
- `fbthrift_idle_budget_load_sensitivity_summary.csv`：五档负载的范围汇总。
- `fbthrift_idle_tradeoff_candidates.csv`：65 个 Ubmem 有效候选与 5 个 Socket 候选。
- `fbthrift_idle_policy_boundaries.csv`：99.5% gate 内的 23 个离散 P99-SLO 选择区间。
