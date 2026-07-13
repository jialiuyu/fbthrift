# 性能实验记录人工维护指导书

本文面向需要创建、审核或修复 Frontier/EDR 的开发者。即使 agent 没有完整遵守
[AGENT_PROTOCOL.md](AGENT_PROTOCOL.md)，也可以按本指导书恢复一致状态。

## 1. 目录和职责

- [FRONTIER.md](FRONTIER.md)：当前认知和检索入口，可以随新结论重写。
- `edr/`：追加式实验历史。终态记录不改写结论，使用新 EDR 替代。
- [EDR_TEMPLATE.md](templates/EDR_TEMPLATE.md)：创建记录的唯一模板。
- `validate_edr.py`：提交前机械检查。
- [README.md](README.md)：目录索引和快速命令。

## 2. 创建新 EDR

### 2.1 查重

先用准备修改的文件、机制、指标和 workload 搜索：

```bash
rg -n -i "BusyPollBackend|epoll_wait|CxlMemBenchmarkTransport|timeout" \
  thrift/perf/cpp2/performance/FRONTIER.md \
  thrift/perf/cpp2/performance/edr
```

如果命中终态 EDR，先检查 `reopen_if`。没有满足重开条件时，应停止重复实验或重新定义
假设，而不是换一个名字重复相同修改。

### 2.2 分配 ID

```bash
find thrift/perf/cpp2/performance/edr -maxdepth 1 \
  -name 'EDR-*.md' -print | sort
```

取最大编号加一。编号一旦提交不得复用。

### 2.3 复制模板并记录版本

```bash
cp thrift/perf/cpp2/performance/templates/EDR_TEMPLATE.md \
  thrift/perf/cpp2/performance/edr/EDR-0002-example-slug.md

git -C ../folly rev-parse --abbrev-ref HEAD
git -C ../folly rev-parse HEAD
git rev-parse --abbrev-ref HEAD
git rev-parse HEAD
```

替换模板中的所有示例值，status 设为 `PROPOSED`。在写代码前完成假设、baseline、主要
变量、预测和 falsifier。

## 3. 执行和记录实验

每个 run 至少保存：

- `run_id`、时间、client/server host；
- 两仓库 branch 和 full commit；
- 完整命令和环境变量；
- workload、concurrency sweep、持续时间和 warmup；
- CPU/NUMA placement；
- QPS、p99/p999、CPU、错误率和目标机制指标；
- raw artifact 路径、URI 或 checksum；
- run 是否有效以及无效原因。

大体积日志和 profiling 文件不提交 Git。建议使用稳定 artifact 目录：

```text
<artifact-root>/<EDR-ID>/<run-id>/
```

EDR 中不得只记录“见本机 `/tmp`”；临时路径必须补充归档位置或明确证据已经丢失。

## 4. 关闭实验

1. 将 status 从 `RUNNING` 改为合适终态。
2. 写清实验 envelope、结果、置信度和限制。
3. 填写非空 `reopen_if`。
4. 更新 Frontier 的关闭方向、关键词路由和推荐下一步。
5. 运行校验器。
6. 确认 `folly` / `fbthrift` 相关提交包含相同 EDR ID。

负面结论推荐写法：

```text
在 <backend/workload/placement/pressure> 条件内，未观察到 <预期指标> 收益。
```

禁止写法：

```text
这个优化没有用。
```

## 5. 重开或替代历史结论

### 5.1 重开

只有满足旧 EDR 的某条 `reopen_if` 才能重开。创建新 EDR，不把旧终态改回 `RUNNING`。
新记录的“相关实验”章节必须说明满足的具体条件。

### 5.2 替代

如果新证据改变旧结论：

1. 新 EDR 的 `supersedes` 加入旧 ID。
2. 旧 EDR 的 `superseded_by` 加入新 ID。
3. 旧 EDR status 改为 `SUPERSEDED`，正文保留旧实验事实。
4. Frontier 只展示当前有效结论，并保留到旧 EDR 的历史链接。

## 6. Agent 未遵从协议时的修复

### 6.1 只有代码，没有 EDR

1. 暂停继续优化。
2. 根据 `git log`、`git diff` 和现有 artifacts 补建 EDR。
3. 无法证明结果时使用 `INCONCLUSIVE`，不要补写成成功。
4. 更新 Frontier 后再继续实验。

### 6.2 有 EDR，但 Frontier 未更新

把 EDR 的 ID、status、一句话结论、路由关键词和 `reopen_if` 加入 Frontier，然后运行
校验器。校验器会报告未被索引的终态 EDR。

### 6.3 重复 ID

尚未提交时，后创建的文件改用下一个编号；已经推送时，不重写历史编号，创建新编号并
在正文说明原记录冲突。不得让两个文件共享同一个 `id`。

### 6.4 commit 错误或无法解析

使用两个仓库分别执行：

```bash
git cat-file -e <full-sha>^{commit}
```

如果实验时的精确 SHA 无法恢复，写明 `commit_provenance` 和推断依据，并降低置信度；
不得用当前 HEAD 冒充历史 build snapshot。

### 6.5 结论过度泛化

把“无效”“更快”“已经解决”等全局表述收敛到具体 envelope，并补充 baseline、delta、
样本数、资源条件和 `reopen_if`。

### 6.6 EDR 关系不对称

同时打开新旧两个 EDR，保证：

```text
new.supersedes contains old.id
old.superseded_by contains new.id
```

## 7. 校验命令

paired worktree 根目录：

```bash
python3 fbthrift/thrift/perf/cpp2/performance/validate_edr.py \
  --performance-dir fbthrift/thrift/perf/cpp2/performance \
  --folly folly \
  --fbthrift fbthrift
```

自动测试：

```bash
python3 -m unittest discover \
  -s fbthrift/thrift/perf/cpp2/performance/tests -v
```

Git 检查：

```bash
git -C folly diff --check
git -C fbthrift diff --check
git -C folly status --short --branch
git -C fbthrift status --short --branch
```

## 8. 人工 Review Checklist

- [ ] 根目录 agent 路由仍指向 `AGENT_PROTOCOL.md`。
- [ ] ID 唯一，文件名与 frontmatter ID 一致。
- [ ] 两个 full commit SHA 可以解析。
- [ ] 实验只改变一个主要变量，或明确列出混杂变量。
- [ ] baseline、workload、pressure、placement 和 duration 完整。
- [ ] QPS 结论同时报告 latency 和 CPU/resource cost。
- [ ] 无效和不利 run 没有被静默删除。
- [ ] 终态结论带 envelope、限制和非空 `reopen_if`。
- [ ] Frontier 已更新且能路由到具体 EDR。
- [ ] supersede 关系双向一致。
- [ ] 大体积 artifacts 未进入 Git，引用位置稳定。
- [ ] 自动测试、校验器和 `git diff --check` 全部通过。
