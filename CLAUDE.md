# Claude 工作入口

执行仓库任务时遵循 `AGENTS.md`。

如果任务涉及 `thrift/perf/cpp2`、性能分析、CXL/SHM、busy-poll、Rocket transport
或 paired `folly` / `fbthrift` 优化，必须在分析或修改前完整读取
`thrift/perf/cpp2/performance/AGENT_PROTOCOL.md`，并执行其中的 Frontier、EDR 和
提交前校验流程。
