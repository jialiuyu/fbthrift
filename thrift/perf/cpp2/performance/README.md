# 性能实验知识库

本目录是通过 `fbthrift/thrift/perf/cpp2` benchmark 验证的跨 `folly` / `fbthrift`
性能实验的唯一 source of truth。它保存当前性能认知、完整实验决策记录和维护方法，
用于防止新 agent 重复探索已经验证过的方向。

## 快速入口

- AI agent：开始性能分析或优化前，必须完整读取 [AGENT_PROTOCOL.md](AGENT_PROTOCOL.md)。
- 人工维护者：创建、审核或修复记录时，读取
  [MAINTENANCE_GUIDE.zh.md](MAINTENANCE_GUIDE.zh.md)。
- 当前状态：读取 [FRONTIER.md](FRONTIER.md)。
- 新实验：复制 [EDR_TEMPLATE.md](templates/EDR_TEMPLATE.md)。
- 系统设计：[EDR_SYSTEM_DESIGN.md](EDR_SYSTEM_DESIGN.md)。

## 文件职责

| 文件 | 职责 |
| --- | --- |
| `FRONTIER.md` | 当前认知快照、活跃假设、已关闭方向和 EDR 路由 |
| `edr/EDR-NNNN-<slug>.md` | 一次完整实验或实验矩阵的长期记录 |
| `AGENT_PROTOCOL.md` | agent 必须遵守的开工、建档、收尾和校验规则 |
| `MAINTENANCE_GUIDE.zh.md` | 人工操作、审核和故障修复指导书 |
| `templates/EDR_TEMPLATE.md` | 新 EDR 的统一模板 |
| `validate_edr.py` | schema、链接、Git commit 和路由机械校验 |
| `tests/test_validate_edr.py` | 校验器自动测试 |

## 状态枚举

- `PROPOSED`：已登记，尚未执行。
- `RUNNING`：正在执行，证据未闭合。
- `SUPPORTED`：证据支持假设。
- `REJECTED_IN_ENVELOPE`：在明确实验范围内未观察到预期收益。
- `CONTEXT_DEPENDENT`：结论随环境或 workload 改变。
- `INCONCLUSIVE`：证据不足或实验受到污染。
- `SUPERSEDED`：已有新 EDR 替代当前结论。

## 校验

从 paired worktree 根目录运行：

```bash
python3 fbthrift/thrift/perf/cpp2/performance/validate_edr.py \
  --performance-dir fbthrift/thrift/perf/cpp2/performance \
  --folly folly \
  --fbthrift fbthrift
```

或者从 `fbthrift` 仓库根目录运行：

```bash
python3 thrift/perf/cpp2/performance/validate_edr.py \
  --performance-dir thrift/perf/cpp2/performance \
  --folly ../folly \
  --fbthrift .
```

校验失败时，不得提交 Frontier 或 EDR 变更，也不得声称实验记录已经闭环。

## 所有权边界

通过 `fbthrift/thrift/perf/cpp2` 得出结论的实验统一记录在这里，即使主要实现发生在
`folly`。EDR 使用 branch、full commit SHA 和文件路径引用 `folly`，不在 `folly`
复制 Frontier 或 EDR。
