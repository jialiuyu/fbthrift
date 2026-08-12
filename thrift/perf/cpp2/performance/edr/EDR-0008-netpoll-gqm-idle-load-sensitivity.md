---
id: EDR-0008
title: Netpoll GQM BUD 与 Sleep 的负载敏感性
status: RUNNING
created: 2026-08-11
updated: 2026-08-11
folly_branch: agent/codex/cxl-mem-rocket-benchmark
folly_commit: 1108d3b98255f6ae08dbc98f265011664133ecc6
fbthrift_branch: agent/codex/cxl-mem-rocket-benchmark
fbthrift_commit: 8f5dd1f5173be5637017afdc7f17739ce2936350
components:
  - Netpoll RPC benchmark
  - GQM idle policy
  - Netpoll Socket reference
files:
  - thrift/perf/cpp2/performance/analysis/netpoll_gqm_idle_load_sensitivity
mechanisms:
  - empty-poll budget
  - configured sleep interval
  - load-dependent polling and idle transition
metrics:
  - target_qps
  - achieved_tps
  - attainment_pct
  - tp99
  - tp999
  - client_cpu_pct
  - server_cpu_pct
  - qps_per_used_core
workloads:
  - Netpoll RPC payload 1KiB at concurrency 128
evidence:
  - ../analysis/netpoll_gqm_idle_load_sensitivity/README.md
  - ../analysis/netpoll_gqm_idle_load_sensitivity/data/SOURCE.md
  - ../analysis/netpoll_gqm_idle_load_sensitivity/data/raw/netpoll_gqm_idle_load_sensitivity.txt
  - ../analysis/netpoll_gqm_idle_load_sensitivity/data/netpoll_gqm_idle_load_sensitivity.csv
  - ../analysis/netpoll_gqm_idle_load_sensitivity/data/netpoll_gqm_idle_load_sensitivity_derived.csv
  - ../analysis/netpoll_gqm_idle_load_sensitivity/data/netpoll_gqm_idle_load_sensitivity_summary.csv
  - ../analysis/netpoll_gqm_idle_load_sensitivity/data/netpoll_socket_qps_baseline.csv
  - ../analysis/netpoll_gqm_idle_load_sensitivity/data/netpoll_idle_tradeoff_candidates.csv
  - ../analysis/netpoll_gqm_idle_load_sensitivity/data/netpoll_gqm_idle_policy_boundaries.csv
reopen_if:
  - 获得每个条件至少三次交错重复和完整运行 provenance
  - 对代表性 GQM 与 Socket frontier 点增加交错重复
  - 增加同一 GQM transport 上的真实 IRQ 与 polling A/B
  - 补齐各 idle stage residency、empty-pop 和 wakeup 时序
supersedes: []
superseded_by: []
---

# EDR-0008：Netpoll GQM BUD 与 Sleep 的负载敏感性

## 摘要

本实验固定 `Concurrency=128` 和 `Payload=1 KiB`，扫描五档 target QPS、五档 BUD 和五档
Sleep，共形成 125 个配置键，并加入相同 payload/concurrency 下五档 Socket reference。
附件包含 124 个完整点和 1 个被截断的部分点。达到 99.5% target attainment 的 GQM cell
数量随负载从 `25` 依次变为 `20、10、10、0`；Socket 则依次为通过、通过、通过、不通过、
不通过。软件 idle 的 CPU–p99 取舍区间随负载上升而收缩。

用户确认 `BUD=0/256` 是相同有效配置的偶然重复；两组原始数据全部保留，联合候选按均值和
两次 min–max 处理。status 保持 `RUNNING`：除这 25 对偶然重复外，其余点均为单次结果，
且没有真实 GQM IRQ A/B、stage residency 或完整 provenance。Socket 是系统级参考，不能
单独形成 IRQ 必要性或 IRQ 性能规格结论。

## Related Experiments

- `EDR-0005`，status `RUNNING`：在 fbthrift Ubmem Poisson open-loop 路径中扫描单层
  empty-poll backoff，并加入 TCP Socket 系统级对照。本轮对象改为 Netpoll 的 BUD 与
  Sleep 二维策略，并补充同 envelope Socket 点；但 Socket 与 GQM transport 不同，仍不
  能替代同 transport IRQ A/B。
- `EDR-0007`，status `RUNNING`：研究 Netpoll 下 GQM successful-operation latency 与
  Ubmem write latency。本轮不注入 successful-operation latency，主要变量改为
  empty-poll BUD、Sleep 和 target QPS。
- 本轮检索词：`Netpoll`、`GQM`、`BUD`、`idle`、`sleep`、`target QPS`、`CPU`、`p99`、
  `IRQ`。

## 假设与判别

### 假设

BUD 和 Sleep 共同控制 idle 路径在空队列后多久降低轮询强度。低负载下，较早进入较深
idle 可以降低 CPU，但会增加请求发现延迟；负载上升后，相同策略会先失去 target
attainment。若只有保持较高轮询强度的策略能够维持高负载，则软件定时 idle 无法用一个
静态参数同时覆盖低负载 CPU 和高负载 capacity。

### 支持现象

- 低 QPS 下存在宽广的 CPU–p99 Pareto frontier，且所有点维持目标；
- QPS 上升后，小 BUD 或长 Sleep 的 attainment 下降；
- 高负载合格 frontier 向高 CPU 区域收缩。

### Falsifier

- 在交错重复后，CPU、p99 和 attainment 不随 BUD/Sleep 出现可重复变化；
- 证明配置没有作用于实际 GQM empty-poll 路径；
- 当前差异完全由到达生成器无法维持 target 或其他固定资源瓶颈造成。

## 实验 Envelope

```text
target QPS = 7,700; 77,000; 192,500; 385,000; 731,500
BUD        = 0; 1; 16; 256; 1024
Sleep      = 1us; 10us; 100us; 1ms; 10ms
Concurrency= 128
Payload    = 1024 B
Cost       = 0
```

用户确认原始重复 tuple 可以压缩显示为单值 BUD 和 Sleep。报告没有保存 tuple 各位置的
具体代码角色，因此这里只把它解释为同一运行中配置了相同值，不进一步推断内部 stage 或
client/server 字段映射。

用户后续确认 `BUD=0` 与 `BUD=256` 实际执行同一个有效配置。报告将这 25 对结果作为偶然
重复：中心为 TPS/P99/CPU 的算术均值，范围为两次 min–max，且两次都达到 gate 才纳入
合格候选。两次范围不是置信区间。

当前来源也没有保存 QPS limiter/arrival generator 实现，因此本 EDR 不把该测试命名为
strict open-loop。`target sustained` 仅定义为完整点中 `TPS/Target >= 99.5%`。

策略在 Server/Client 两端对称配置；Client 和 Server 容器各分配 `8 vCPU`，合计
`16 vCPU`。本文的 `Total CPU cores` 定义为
`(Client process CPU% + Server process CPU%) / 100`，表示两端所有线程累计 CPU 时间
折算出的 vCPU-equivalents，不表示单线程占用，也不区分物理核、SMT sibling 或线程分布。

## 结果

| Target QPS | 完整点 | 维持目标 | 最大 TPS | 最大 Attainment | p99 范围 | Total CPU 范围 |
|---:|---:|---:|---:|---:|---:|---:|
| 7.7K | 25 | 25 | 7.7K | 100.00% | 20us–19.45ms | 0.112–3.993 cores |
| 77K | 25 | 20 | 77.0K | 100.00% | 30us–20.59ms | 0.268–8.809 cores |
| 192.5K | 25 | 10 | 192.5K | 100.00% | 50us–11.31ms | 0.268–8.696 cores |
| 385K | 25 | 10 | 384.3K | 99.82% | 330us–11.07ms | 0.266–11.726 cores |
| 731.5K | 24+1 partial | 0 | 707.5K | 96.72% | 410us–10.58ms | 0.269–14.820 cores |

Socket reference 为：`7.7K: 0.239 cores/120us/100%`、
`77K: 2.117 cores/250us/100%`、`192.5K: 5.136 cores/340us/100%`、
`385K: 9.724 cores/610us/98.94%`、`731.5K: 9.563 cores/620us/52.17%`。

阶段性事实：

- `7.7K` 下所有配置都维持目标，主要差异是 CPU 与 p99；
- `192.5K` 下 `BUD=1/16` 的最高 attainment 为 `88.9%/90.3%`；
- `385K` 下只有 10 个原始点达到 gate；纠正重复语义后，合格联合 frontier 为
  `B1024/S1us` 与 `B0/256 duplicate/S10us`，Socket 未通过 gate；
- `731.5K` 下没有点达到 gate，最高 TPS 为 `707.5K`，因此只按 near-capacity stress
  解释，不从该档计算合格 frontier。

在满足 attainment gate 的点内，以给定 P99 budget 下 Total CPU 最小为选择规则，四档
可维持负载共形成 17 个离散策略区间：`7.7K/77K/192.5K/385K` 分别为
`5/6/4/2` 段。Socket 分别在前三档的 `[120us,1.15ms)`、`[250us,1.32ms)` 和
`>=340us` 区间成为最低 CPU 候选；`731.5K` 不生成伪边界。

完整 125 点、派生表、策略边界表、两张图和第一阶段解释见 evidence 中的 README/CSV。

## 结论

当前 envelope 支持“软件 idle 策略具有显著负载敏感性”：低负载下可以用更深 idle 换取
CPU，但到中高负载时同类配置会失去 capacity，合格配置逐渐向高 CPU 区域收缩。

本阶段不作以下结论：

1. 不宣布 GQM IRQ 已经必要或不必要；
2. 不把 Socket 等同于 GQM IRQ，也不把 Socket/GQM 的整体差异归因于 IRQ；
3. 不把单次 Pareto 点直接设为生产默认参数；
4. 不从配置 Sleep 推断 actual sleep 或 publish-to-detect latency；
5. 不把线程级进程 CPU 折算值解释为物理核或单线程占用。

## 限制与后续

- `BUD=0/256` 有 25 对偶然重复；其他 GQM cell 和 Socket 均只有单次结果。重复范围是
  min–max，不是置信区间；第 125 行只保留 TPS。
- 缺少运行时间、执行顺序、client/server 机器、CPU/NUMA placement、binary SHA 和
  firmware revision。
- 缺少每个 stage 的进入次数、驻留时间、actual sleep、empty-pop 和 wakeup 时序。
- 下一步先补代表性 frontier/边界点的三次交错重复和 stage counter；真实 GQM IRQ 可用后，
  再在同一 transport 内做 polling/IRQ A/B。

## 完成检查

- [x] 125 个配置键和唯一 partial cell 已归档。
- [x] attainment、CPU、QPS/core 和 Pareto 派生方式已固定。
- [x] 五个 Socket reference 已归档并按统一 gate 纳入联合候选。
- [x] `BUD=0/256` 已按偶然重复聚合，25 对 gate 分类一致。
- [x] 两张 PNG/SVG 决策图、17 段策略边界和完整结果表已生成。
- [x] 原始来源、SHA-256 和缺失字段已记录。
- [ ] 每个 cell 至少三次交错重复。
- [ ] stage residency、empty-pop 和 wakeup 时序已采集。
- [x] Socket system-level reference 已完成。
- [ ] 真实 GQM IRQ A/B 已完成。
