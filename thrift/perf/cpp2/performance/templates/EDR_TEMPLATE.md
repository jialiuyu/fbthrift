---
id: EDR-0000
title: 替换为实验标题
status: PROPOSED
created: 2026-07-13
updated: 2026-07-13
folly_branch: 替换为分支名
folly_commit: 0000000000000000000000000000000000000000
fbthrift_branch: 替换为分支名
fbthrift_commit: 0000000000000000000000000000000000000000
components:
  - 替换为组件名
files:
  - 替换为仓库相对路径
mechanisms:
  - 替换为机制
metrics:
  - qps
workloads:
  - 替换为 workload
evidence:
  - 替换为稳定 artifact 路径或 URI
reopen_if:
  - 替换为允许重开或要求重新验证的明确条件
supersedes: []
superseded_by: []
---

# EDR-0000：替换为实验标题

## 摘要

用一段话说明实验状态、主要结果和适用范围。`PROPOSED` 阶段写预期验证目标。

## Related Experiments

- 列出相关 EDR ID、status、重叠范围和本轮改变的条件。
- 没有命中时，记录实际使用的搜索词。

## 假设与机制

- 假设：说明为什么这个改动可能影响目标指标。
- 主要变量：每轮只保留一个主要变量。
- 预测：列出支持假设时应该看到的现象。
- Falsifier：列出什么证据会否定假设。

## 代码和版本范围

- `folly` branch/commit：与 frontmatter 一致。
- `fbthrift` branch/commit：与 frontmatter 一致。
- 变更文件：列出文件和关键符号。
- Provenance：说明 commit 是精确 build snapshot 还是历史迁移推断。

## 实验 Envelope

- backend 和 transport route：
- client/server 机器：
- workload 和 operation mix：
- concurrency/pressure：
- warmup/measured duration：
- CPU/NUMA placement：
- 其他固定参数：

## Baseline 和实验矩阵

说明 baseline、唯一变量、run 数量以及完整 sweep。数据较大时链接 CSV，不要只摘最佳点。

## 结果

同时报告 QPS、p99/p999、CPU/resource cost、错误率和目标机制指标。列出无效 run 及原因。

## 结论

结论必须限制在实验 envelope 内，区分事实、推断和未知项。

## 限制与置信度

- 置信度：high / medium / low。
- 混杂变量：
- 缺失证据：
- 不能外推的范围：

## Reopen 条件

逐项解释 frontmatter 中的 `reopen_if`。终态记录不得为空。

## 原始证据

记录 artifact 路径或 URI、采集命令和 checksum。不得只引用无法恢复的 `/tmp` 路径。

## 完成检查

- [ ] 两仓库 full commit SHA 可解析。
- [ ] Related Experiments 和唯一主要变量已写清。
- [ ] baseline、envelope、结果和无效 run 完整。
- [ ] 终态 status 与非空 `reopen_if` 一致。
- [ ] Frontier 已更新。
- [ ] `validate_edr.py` 通过。
