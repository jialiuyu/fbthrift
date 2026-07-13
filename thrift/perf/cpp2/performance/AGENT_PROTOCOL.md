# 性能实验 Agent 协议

本文是跨 `folly` / `fbthrift` 性能工作的强制协议。仓库根目录的 `AGENTS.md` 和
`CLAUDE.md` 只负责把相关任务路由到这里。

## 1. 适用范围

出现以下任一情况时必须执行本协议：

- 查找新的性能优化点；
- 修改 CXL/SHM、busy-poll、Rocket transport 或 `thrift/perf/cpp2`；
- 比较 QPS、latency、CPU、syscall、cache、NUMA 或扩展性；
- 判断历史优化是否无效、是否值得重开；
- 在 paired `folly` / `fbthrift` worktree 中进行跨仓库优化。

## 2. 开工前强制步骤

1. 完整读取 [FRONTIER.md](FRONTIER.md)。
2. 从任务中提取检索指纹：
   - component；
   - file/path；
   - mechanism；
   - metric；
   - workload。
3. 搜索 Frontier 和 EDR：

```bash
rg -n -i "<component|file|mechanism|metric|workload>" \
  thrift/perf/cpp2/performance/FRONTIER.md \
  thrift/perf/cpp2/performance/edr
```

4. 命中任一相同维度时，必须完整读取关联 EDR。不得只读标题或 Frontier 摘要。
5. 修改前输出 `Related Experiments`，至少说明：
   - 命中的 EDR ID 和 status；
   - 当前任务与历史实验重叠的机制和代码范围；
   - 本轮改变的实验条件或唯一主要变量；
   - 如果重开已关闭方向，满足了哪条 `reopen_if`。

没有命中记录时，必须写明实际执行过的搜索词。不得直接声称“这是一个全新方向”。

## 3. 建立新实验

开始实现性能优化前：

1. 查看 `edr/` 中最大编号，顺序分配下一个 EDR ID。
2. 复制 [EDR_TEMPLATE.md](templates/EDR_TEMPLATE.md)，文件名使用
   `EDR-NNNN-<slug>.md`。
3. 初始 status 使用 `PROPOSED`；开始跑实验后改为 `RUNNING`。
4. 使用以下命令记录两个仓库的 full SHA：

```bash
git -C ../folly rev-parse HEAD
git -C . rev-parse HEAD
```

5. 记录 branch、涉及文件、机制、指标、workload、baseline 和预期 falsifier。
6. 同一实验的完整 concurrency sweep 属于一个 EDR，不得按数据点拆分记录。

如果实验只是历史迁移且精确 build SHA 不可恢复，必须在正文中明确写出 provenance 限制；
不得把推断 commit 描述成已确认的 build snapshot。

## 4. 实验执行规则

- 一轮实验只改变一个主要变量；无法做到时必须列出混杂变量。
- warmup 与 measured window 分离。
- 记录 client/server 机器、CPU/NUMA placement、branch、commit、完整命令和 artifact
  位置。
- QPS 结论同时保留 latency 和 CPU/resource cost；不得只摘 absolute peak。
- 无效 run 也要记录无效原因，不得静默删除不利结果。
- 原始大文件可以不进入 Git，但 EDR 必须提供稳定位置、采集方式和必要 checksum。

## 5. 状态转换

允许的转换：

```text
PROPOSED -> RUNNING
RUNNING -> SUPPORTED
RUNNING -> REJECTED_IN_ENVELOPE
RUNNING -> CONTEXT_DEPENDENT
RUNNING -> INCONCLUSIVE
终态 -> SUPERSEDED
```

终态记录必须包含非空 `reopen_if`：

- `SUPPORTED`：列出需要重新验证或会使结论失效的条件。
- `REJECTED_IN_ENVELOPE`：列出允许重新开启该方向的实质条件变化。
- `CONTEXT_DEPENDENT`：列出需要单独重测的环境边界。
- `INCONCLUSIVE`：列出补齐证据的条件。
- `SUPERSEDED`：指向替代记录并说明关系。

禁止写“这个优化无效”之类没有 envelope 的全局结论。

## 6. 收尾步骤

实验结束后必须同时完成：

1. 更新 EDR 结果、限制、status、`updated` 和 `reopen_if`。
2. 更新 [FRONTIER.md](FRONTIER.md)：
   - 活跃假设状态；
   - 已关闭方向摘要；
   - component/file/mechanism/metric/workload 路由；
   - 推荐下一步。
3. 如果新 EDR 替代旧结论，双向更新 `supersedes` 和 `superseded_by`。
4. 运行校验：

```bash
python3 thrift/perf/cpp2/performance/validate_edr.py \
  --performance-dir thrift/perf/cpp2/performance \
  --folly ../folly \
  --fbthrift .
```

5. 阅读 `git diff`，确认没有把 build、raw profiles 或本地机器私有路径提交进仓库。
6. 相关 `folly` 和 `fbthrift` commit message 使用同一个 EDR ID。

## 7. 故障和阻塞处理

- paired `folly` 不存在：报告阻塞，不得验证 `folly_commit`。
- Frontier 引用的 EDR 不存在：报告知识库损坏，先按人工指导书修复。
- EDR commit 无法解析：把结论标记为 provenance 不完整，不得当作当前代码事实。
- 校验器失败：不得提交；逐项修复，不得使用跳过校验参数。
- agent 接手时发现前一轮只有代码没有 EDR：先补建 `INCONCLUSIVE` EDR，再继续实验。

人工修复方法见 [MAINTENANCE_GUIDE.zh.md](MAINTENANCE_GUIDE.zh.md)。
