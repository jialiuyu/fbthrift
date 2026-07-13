# 跨仓库性能实验记录系统设计

## 1. 背景

`folly` 和 `fbthrift` 是两个独立 Git 仓库。在当前开发模式中，同一任务通过
paired worktree 同时检出两个仓库的同名分支，`folly` 承载传输基础设施，
`fbthrift/thrift/perf/cpp2` 承载 RPC 集成和主要性能验证入口。

性能优化通常跨越两个仓库。仅依赖 agent 会话上下文，会导致新会话重复探索已经
验证过的方向，或忽略只在特定实验条件下成立的负面结论。当前 worktree 根目录下的
`CONTEXT.md`、`BENCHMARK_PARAMETERS.md`、`HYPOTHESIS_LEDGER.md` 和
`RUN_PLAN.md` 属于本地工作笔记，位于外层 workspace 的忽略目录中，不能作为长期、
可共享的 source of truth。

## 2. 目标

本系统需要：

1. 为跨 `folly` / `fbthrift` 性能实验提供唯一、可追溯的记录位置。
2. 让新 agent 先获取当前性能认知，再按任务相关性下钻具体实验记录。
3. 把无收益、条件有效和证据不足的实验都作为一等工程资产保存。
4. 精确记录两个仓库的 branch、commit 和变更范围，避免把错误代码版本与实验结论关联。
5. 区分当前结论、完整实验历史和体积较大的原始数据。

## 3. 非目标

第一版不做以下事项：

- 不引入第三个 Git 仓库或数据库。
- 不在 `folly` 和 `fbthrift` 中复制同一份 Frontier 或 EDR。
- 不把大体积 profiling 数据、构建产物或完整日志直接提交到 Git。
- 不开发语义向量检索或自动判断实验是否重复的服务。
- 不替代现有 benchmark 运行指导书和构建文档。

## 4. 所有权与存放位置

凡是通过 `fbthrift/thrift/perf/cpp2` benchmark 得出结论的实验，无论主要代码修改
发生在 `folly` 还是 `fbthrift`，均由 `fbthrift` 保存实验记录。

唯一 source of truth 位于：

```text
thrift/perf/cpp2/performance/
├── README.md
├── FRONTIER.md
├── EDR_SYSTEM_DESIGN.md
├── edr/
│   └── EDR-NNNN-<slug>.md
└── templates/
    └── EDR_TEMPLATE.md
```

`folly` 不保存镜像文档。EDR 通过 commit、branch 和文件路径引用对应的 `folly`
状态。外层 workspace 的 `AGENTS.md` 只负责 paired-worktree 的 agent 启动路由，
不保存实验事实，也不作为 Git 仓库维护。

## 5. 文档职责

### 5.1 `README.md`

说明目录结构、状态枚举、创建和关闭 EDR 的流程，以及 agent 的强制阅读规则。

### 5.2 `FRONTIER.md`

Frontier 是当前认知的可变快照，不是追加式实验日志。它只保留：

- 当前优化目标和已确认瓶颈。
- 活跃假设和推荐的下一步实验。
- 已关闭方向的简短结论。
- EDR 检索路由，包括 component、file、mechanism、metric 和 workload。
- 每个关闭方向的 `reopen_if` 条件。

Frontier 的历史变化由 Git 保存。不得在其中复制完整实验过程和大表格。

### 5.3 `edr/EDR-NNNN-<slug>.md`

EDR 是一次完整实验或同一实验矩阵的长期记录。一个并发 sweep 属于一个 EDR，
而不是每个并发点各建一份记录。

EDR 在实验期间允许从 `PROPOSED` 更新到终态。进入终态后，不得改写原始结论；
新证据通过新的 EDR 表达，并用 `supersedes` / `superseded_by` 建立关系。

### 5.4 `templates/EDR_TEMPLATE.md`

模板统一必填字段、证据边界和完成检查项，避免不同 agent 生成不可比较的记录。

## 6. EDR 数据模型

每份 EDR 使用 Markdown 正文和 YAML frontmatter。frontmatter 至少包含：

```yaml
---
id: EDR-0001
title: CXL hot shard busy-poll EventBase
status: REJECTED_IN_ENVELOPE
created: 2026-07-07
updated: 2026-07-07
owners:
  benchmark: fbthrift/thrift/perf/cpp2
repositories:
  folly:
    branch: agent/codex/cxl-mem-rocket-benchmark
    commit: <full-sha>
  fbthrift:
    branch: agent/codex/cxl-mem-rocket-benchmark
    commit: <full-sha>
scope:
  components:
    - CxlMemBenchmarkHotIoShard
  files:
    - thrift/perf/cpp2/util/CxlMemBenchmarkTransport.cpp
  mechanisms:
    - BusyPollBackend
    - ManualPoll
  metrics:
    - qps
    - epoll_wait_count
  workloads:
    - sum
    - timeout
supersedes: []
superseded_by: []
---
```

正文必须包含：

1. 假设和预期机制。
2. 与既有 EDR 的关系，以及本轮改变的唯一主要变量。
3. 有效实验范围，包括 backend、机器、CPU/NUMA placement、workload 和压力参数。
4. baseline、实验矩阵和原始证据位置。
5. 结果摘要，包括 QPS、延迟、CPU 和目标机制指标。
6. 结论、置信度、限制和 `reopen_if`。
7. 失败或无效 run，以及判定无效的原因。

原始证据可以位于外部 artifact 目录，但 EDR 必须记录稳定 URI 或路径、采集命令和
必要的 checksum。只有无法恢复的本地临时路径不能作为唯一证据引用。

## 7. 状态模型

允许的状态为：

- `PROPOSED`：假设已登记，尚未执行。
- `RUNNING`：实验正在执行，证据尚未闭合。
- `SUPPORTED`：证据支持假设。
- `REJECTED_IN_ENVELOPE`：在明确实验范围内未观察到预期收益。
- `CONTEXT_DEPENDENT`：不同环境或 workload 下结论不同。
- `INCONCLUSIVE`：证据不足或实验存在污染。
- `SUPERSEDED`：已有更新实验替代当前结论。

禁止使用没有实验范围的全局表述，例如“这个优化无效”。负面结论必须写成“在指定
envelope 内未观察到收益”，并给出允许重开的条件。

## 8. Agent 阅读与路由协议

外层 workspace 的 `AGENTS.md` 对跨仓库性能任务规定：

1. 开始分析或修改前必须读取 `fbthrift/thrift/perf/cpp2/performance/FRONTIER.md`。
2. 如果任务命中相同 component、file、mechanism、metric 或 workload，必须读取
   Frontier 关联的 EDR。
3. 弱相关记录由 agent 按不确定性决定是否读取。
4. 修改前必须输出 `Related Experiments`，说明是否重复、改变了什么条件，以及是否
   满足既有记录的 `reopen_if`。
5. 新实验必须先分配 EDR ID，并在实验结束后更新 EDR 状态和 Frontier。

如果 paired `fbthrift` worktree 不存在、Frontier 引用的 EDR 不存在，或记录的 commit
无法解析，agent 不得把对应结论当作已验证事实，必须报告缺失或过期状态。

## 9. 生命周期

一次性能优化按以下顺序闭环：

```text
读取 Frontier
  -> 检索和阅读相关 EDR
  -> 建立 PROPOSED EDR
  -> 记录两仓库基线 commit
  -> 修改和执行实验
  -> 保存结果与原始证据引用
  -> 设置终态和 reopen_if
  -> 更新 Frontier
  -> 在 fbthrift 中提交 EDR 与 Frontier
```

`folly` 和 `fbthrift` 的代码仍分别提交。两个仓库的相关提交信息应包含同一个 EDR ID，
例如：

```text
EDR-0002: reduce CXL queue handoff contention
```

## 10. 初始迁移

初始化阶段只吸收已经有明确证据和结论的内容，不批量复制本地工作笔记：

1. 创建 `README.md`、`FRONTIER.md` 和 EDR 模板。
2. 将现有 CXL.mem Busy-Poll EventBase 验证结论登记为 `EDR-0001`。
3. Frontier 引用 `EDR-0001`，状态为 `REJECTED_IN_ENVELOPE`，保留其适用范围和
   `reopen_if`。
4. 从本地 `HYPOTHESIS_LEDGER.md` 中选择仍活跃的假设进入 Frontier，但不把未执行
   假设伪装成实验结论。
5. `CONTEXT.md`、`BENCHMARK_PARAMETERS.md` 和 `RUN_PLAN.md` 仍作为本地执行笔记；
   后续仅在内容稳定且具有跨任务价值时迁入受跟踪文档。

## 11. 验证和完成条件

第一版初始化完成需要满足：

- Frontier 中每个关闭方向都能解析到存在的 EDR。
- EDR 中记录的两个 commit 均能在对应仓库解析。
- `EDR-0001` 的结果和限制与现有验证结论文档一致。
- 模板不存在 `TBD`、含糊的全局负面结论或缺失的 `reopen_if`。
- Markdown 通过 whitespace 检查，目录内相对链接有效。
- `folly` 中不存在 Frontier 或 EDR 的复制版本。
