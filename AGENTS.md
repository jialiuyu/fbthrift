# Agent 工作入口

默认遵循仓库现有开发和测试约定。

当任务涉及以下任一范围时，开始分析、提出优化或修改代码前，必须完整读取
`thrift/perf/cpp2/performance/AGENT_PROTOCOL.md`：

- `thrift/perf/cpp2` benchmark 或性能结论；
- CXL、共享内存、busy-poll、Rocket transport 性能优化；
- paired `folly` / `fbthrift` worktree 的跨仓库优化；
- 查找新的性能优化点或判断历史方向是否值得重开。

协议要求先读 `FRONTIER.md`，再按 component、file、mechanism、metric 和 workload
下钻相关 EDR。命中相关记录时不得跳过。提交实验记录前必须运行协议指定的校验命令。

本文件只负责入口路由。Frontier、EDR 和维护方法的唯一 source of truth 位于
`thrift/perf/cpp2/performance/`。
