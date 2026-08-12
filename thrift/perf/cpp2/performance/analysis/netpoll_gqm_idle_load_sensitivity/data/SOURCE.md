# 数据来源与规范化说明

## 来源

- 接收日期：2026-08-11。
- 用户提供附件：
  `/Users/jialiuyu/.codex/attachments/147ec760-894f-4950-94b0-ba563c2439bc/pasted-text.txt`。
- 仓库内逐字归档：
  `data/raw/netpoll_gqm_idle_load_sensitivity.txt`。
- 归档 SHA-256：
  `377203bace911b6259826f12c0e7d23633ed9586cc7374539e3dba3215e379c3`。
- 2026-08-12 用户在聊天中补充同 envelope 的 Netpoll Socket QPS baseline；仓库内归档为
  `data/raw/netpoll_socket_qps_baseline.txt`，SHA-256 为
  `c96da8cc7694955fec7ae53cad65371b5b8b091efdf4c9e1d65af8e0cc672464`。

附件由用户标识为 Netpoll 的 GQM idle 测试结果。当前来源没有保存运行命令、binary SHA、
firmware 版本、CPU/NUMA placement、到达生成器实现、单点运行时长或重复次数。因此报告只
使用表中直接提供的 target QPS、TPS、TP99、TP999、CPU 和 RSS 字段，不把它进一步命名为
strict open-loop 或 closed-loop 结果。

## 字段映射

原始 `Value` 形式为：

```text
target QPS × client/server 相同 BUD tuple × 相同 sleep tuple
```

用户确认图表可将重复 tuple 压缩为：

```text
BUD       = 0, 1, 16, 256, 1024
Sleep(us) = 1, 10, 100, 1000, 10000
```

固定字段为：

```text
Concurrency = 128
Payload     = 1024 B
Cost        = 0
```

用户后续补充本组图表的资源与配置口径：Server/Client 对称使用相同 BUD 与 Sleep；Client
容器和 Server 容器各分配 `8 vCPU`，两端合计 `16 vCPU`。这一补充用于 CPU 归一化和图表
标注，不改变原始 125 个测量点。

用户同时确认，`BUD=0` 与 `BUD=256` 因配置失误实际执行的是相同有效策略。因此原始
125 行不删除、不改名；用于联合 Pareto 与策略边界时，同一 `QPS × Sleep` 的两行合并为
两次重复。中心取算术均值，范围取两次 min–max，只有两次都达到 99.5% gate 才标记为
合格。25 对重复的 gate 分类完全一致，其中 15 对共同通过、10 对共同未通过。

`TPS` 中的 `K` 按十进制 `1000` 展开。`Client CPU` 与 `Server CPU` 原样保留为进程多核
累计百分比；派生 `used_cores=(Client CPU + Server CPU)/100`，表示所有进程线程累计 CPU
时间折算的 vCPU-equivalents，不把它解释为物理核、SMT sibling 或单线程占用。

## 完整性

理论矩阵为：

```text
5 QPS × 5 BUD × 5 Sleep = 125 cells
```

前 124 行完整。第 125 行在附件末尾被截断，只保留以下字段：

```text
Value = 731500 × 1024x1024 × 10000x10000x10000x10000
Concurrency = 128
Payload = 1024 B
Target QPS = 731500
Cost = 0
TPS = 704.9K
```

该行缺失的 TP99、TP999、CPU、RSS、Retry 和 Status 全部保持空值，并标记
`partial=true`；没有进行插值或从相邻点推断。

## 文件

- `netpoll_gqm_idle_load_sensitivity.csv`：125 个配置键的规范化输入。
- `netpoll_gqm_idle_load_sensitivity_derived.csv`：逐点 attainment、total CPU、used cores、
  QPS/used-core、gate 和 Pareto 标记。
- `netpoll_gqm_idle_load_sensitivity_summary.csv`：五档负载的完整性和范围汇总。
- `netpoll_socket_qps_baseline.csv`：五档 Socket 系统级事件驱动锚点。
- `netpoll_idle_tradeoff_candidates.csv`：合并重复后的 100 个 GQM 有效候选与 5 个 Socket
  候选，包含均值、min–max、repeat count 和 gate。
- `netpoll_gqm_idle_policy_boundaries.csv`：在 99.5% attainment gate 内，给定 P99 budget
  时 Total CPU 最低的 17 个联合离散策略区间。

Socket 结果包含 transport/software stack 的整体差异，只作为系统级事件驱动 reference；
它不是 GQM IRQ 测量，也不能用于把差异归因于 IRQ。
