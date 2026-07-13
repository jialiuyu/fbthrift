# 性能 EDR 系统初始化实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 在 paired `folly` / `fbthrift` worktree 中建立可自动发现、可由 agent 和人共同维护、可机械校验的跨仓库性能实验记录系统。

**Architecture:** `fbthrift/thrift/perf/cpp2/performance/` 保存唯一的 Frontier、EDR、agent 协议和人工指导书；两个仓库根目录的 `AGENTS.md` / `CLAUDE.md` 只负责条件路由。一个无第三方依赖的 Python 校验器检查 EDR schema、Frontier 索引、跨 EDR 关系、Git commit 和入口文件，避免只靠 agent 自觉维护。

**Tech Stack:** Markdown、YAML frontmatter 的受限子集、Python 3 标准库 `unittest`、Git CLI。

---

### Task 1: 修订系统设计并建立仓库级自动发现入口

**Files:**
- Modify: `fbthrift/thrift/perf/cpp2/performance/EDR_SYSTEM_DESIGN.md`
- Create: `fbthrift/AGENTS.md`
- Create: `fbthrift/CLAUDE.md`
- Create: `folly/AGENTS.md`
- Create: `folly/CLAUDE.md`

- [x] **Step 1: 修订设计中的发现与维护层次**

将设计改为四层：仓库根路由、`AGENT_PROTOCOL.md`、`MAINTENANCE_GUIDE.zh.md`、`validate_edr.py`。明确外层 workspace 指令只是本机额外入口，不是唯一入口。

- [x] **Step 2: 创建 `fbthrift` 根路由**

路由只在任务涉及 performance、benchmark、CXL/SHM transport 或 paired `folly` / `fbthrift` 优化时生效，并强制读取：

```text
thrift/perf/cpp2/performance/AGENT_PROTOCOL.md
```

- [x] **Step 3: 创建 `folly` 根路由**

paired worktree 中强制读取：

```text
../fbthrift/thrift/perf/cpp2/performance/AGENT_PROTOCOL.md
```

路径缺失时必须报告知识库不可用，不得声称已经排除重复实验。

- [x] **Step 4: 验证路由内容**

Run:

```bash
rg -n "AGENT_PROTOCOL.md|性能|benchmark" \
  folly/{AGENTS.md,CLAUDE.md} fbthrift/{AGENTS.md,CLAUDE.md}
```

Expected: 四个文件都命中协议路径和条件路由。

### Task 2: 创建 agent 协议和人工维护指导书

**Files:**
- Create: `fbthrift/thrift/perf/cpp2/performance/README.md`
- Create: `fbthrift/thrift/perf/cpp2/performance/AGENT_PROTOCOL.md`
- Create: `fbthrift/thrift/perf/cpp2/performance/MAINTENANCE_GUIDE.zh.md`

- [x] **Step 1: 写 agent 强制协议**

协议必须覆盖：开工前读取顺序、相关实验检索、`Related Experiments` 输出、EDR ID 分配、双仓库 commit 记录、状态转换、`reopen_if`、结束时更新 Frontier，以及提交前校验命令。

- [x] **Step 2: 写人的维护指导书**

指导书必须包含完整操作步骤和故障处理：新建 EDR、关闭实验、重开实验、supersede、修复重复 ID、补 Frontier、修复错误 commit、处理不完整 agent 输出和人工 review checklist。

- [x] **Step 3: 写目录索引**

`README.md` 同时为人和 agent 提供文件职责、快速开始命令和状态枚举，但不复制协议全文。

- [x] **Step 4: 检查维护信息可发现性**

Run:

```bash
rg -n "AGENT_PROTOCOL|MAINTENANCE_GUIDE|validate_edr.py|FRONTIER" \
  fbthrift/thrift/perf/cpp2/performance/{README.md,AGENT_PROTOCOL.md,MAINTENANCE_GUIDE.zh.md}
```

Expected: 三份文档能互相路由到协议、人工指南、Frontier 和校验器。

### Task 3: 初始化 Frontier、EDR 模板和首条历史记录

**Files:**
- Create: `fbthrift/thrift/perf/cpp2/performance/FRONTIER.md`
- Create: `fbthrift/thrift/perf/cpp2/performance/templates/EDR_TEMPLATE.md`
- Create: `fbthrift/thrift/perf/cpp2/performance/edr/EDR-0001-cxl-hot-shard-busy-poll-eventbase.md`

- [x] **Step 1: 定义扁平 frontmatter schema**

模板使用无嵌套的 YAML 子集，至少包含：

```yaml
---
id: EDR-0000
title: 示例标题
status: PROPOSED
created: 2026-07-13
updated: 2026-07-13
folly_branch: branch-name
folly_commit: 0000000000000000000000000000000000000000
fbthrift_branch: branch-name
fbthrift_commit: 0000000000000000000000000000000000000000
components:
  - ComponentName
files:
  - path/to/file.cpp
mechanisms:
  - mechanism-name
metrics:
  - qps
workloads:
  - workload-name
evidence:
  - path/or/uri
reopen_if:
  - explicit-condition
supersedes: []
superseded_by: []
---
```

- [x] **Step 2: 建立 Frontier**

Frontier 记录当前目标、活跃假设、关闭方向、EDR 路由关键词和 `reopen_if`。迁入 H1-H5 时必须标记为待验证假设，不得伪装成结论。

- [x] **Step 3: 迁移 `EDR-0001`**

使用以下代码引入 commit：

```text
folly:    b0f2ac351e90ad27309ef0135745841e446a4ea8
fbthrift: ab6959f9b5ffd03c9c029d7882397193dbd26bad
```

正文必须声明：历史结论文档没有记录构建时精确 SHA；以上 commit 是引入对应代码的可解析 commit，属于历史迁移推断，而不是对当时 dirty build tree 的精确重建。

- [x] **Step 4: 检查结论一致性**

Run:

```bash
rg -n -- "-0.06%|-0.31%|-5.40%|83654|REJECTED_IN_ENVELOPE" \
  fbthrift/thrift/perf/cpp2/performance/edr/EDR-0001-cxl-hot-shard-busy-poll-eventbase.md
```

Expected: EDR 保留原结论文档的关键数值、状态和限制。

### Task 4: 以 TDD 定义校验器行为

**Files:**
- Create: `fbthrift/thrift/perf/cpp2/performance/tests/test_validate_edr.py`
- Create: `fbthrift/thrift/perf/cpp2/performance/validate_edr.py`

- [x] **Step 1: 写合法目录通过的测试**

测试使用 `tempfile.TemporaryDirectory()` 创建 performance fixture，并断言：

```python
errors = validate_repository(performance_dir, folly_repo, fbthrift_repo)
self.assertEqual([], errors)
```

- [x] **Step 2: 写缺少字段和重复 ID 的失败测试**

分别删除 `reopen_if`、创建第二个相同 `id`，断言错误包含字段名或重复 ID。

- [x] **Step 3: 写 Frontier 和 supersede 关系测试**

覆盖 broken link、未索引终态 EDR 和不对称 `supersedes` / `superseded_by`。

- [x] **Step 4: 写 Git commit 与 agent 路由测试**

使用临时 Git repo 创建真实 commit；错误 SHA 必须失败。删除根 `AGENTS.md` 或协议路径时必须报告路由缺失。

- [x] **Step 5: 运行测试并确认 RED**

Run:

```bash
python3 -m unittest discover \
  -s fbthrift/thrift/perf/cpp2/performance/tests -v
```

Expected: FAIL，因为 `validate_edr.py` 尚未提供目标 API。

### Task 5: 实现无第三方依赖的 EDR 校验器

**Files:**
- Create: `fbthrift/thrift/perf/cpp2/performance/validate_edr.py`
- Modify: `fbthrift/thrift/perf/cpp2/performance/tests/test_validate_edr.py`

- [x] **Step 1: 实现受限 frontmatter parser**

提供：

```python
def parse_frontmatter(path: pathlib.Path) -> dict[str, object]:
    """解析顶层 scalar、缩进 list 和空列表 []。"""
```

遇到缺失分隔符、重复 key 或嵌套 map 时返回可定位到文件的错误。

- [x] **Step 2: 实现目录与 schema 校验**

提供：

```python
def validate_repository(
    performance_dir: pathlib.Path,
    folly_repo: pathlib.Path,
    fbthrift_repo: pathlib.Path,
) -> list[str]:
    """返回稳定排序的全部错误；空列表表示通过。"""
```

检查必填字段、允许状态、ID/文件名、唯一 ID、终态 `reopen_if`、Frontier 索引、相对链接和 supersede 对称性。

- [x] **Step 3: 实现 Git 和路由校验**

使用：

```bash
git -C <repo> cat-file -e <sha>^{commit}
```

确认两个 commit 可解析，并检查 `folly` / `fbthrift` 根目录的 `AGENTS.md`、
`CLAUDE.md` 都包含 `AGENT_PROTOCOL.md` 路由。

- [x] **Step 4: 实现 CLI**

CLI 接受：

```text
--performance-dir PATH --folly PATH --fbthrift PATH
```

失败时逐行输出错误并返回 1；通过时输出 EDR 数量并返回 0。

- [x] **Step 5: 运行测试并确认 GREEN**

Run:

```bash
python3 -m unittest discover \
  -s fbthrift/thrift/perf/cpp2/performance/tests -v
```

Expected: 所有测试 `OK`。

### Task 6: 全量验证、提交和推送

**Files:**
- Verify: `folly/AGENTS.md`
- Verify: `folly/CLAUDE.md`
- Verify: `fbthrift/AGENTS.md`
- Verify: `fbthrift/CLAUDE.md`
- Verify: `fbthrift/thrift/perf/cpp2/performance/**`

- [x] **Step 1: 运行真实仓库校验**

Run:

```bash
python3 fbthrift/thrift/perf/cpp2/performance/validate_edr.py \
  --performance-dir fbthrift/thrift/perf/cpp2/performance \
  --folly folly \
  --fbthrift fbthrift
```

Expected: `validated 1 EDR(s)`，exit 0。

- [x] **Step 2: 运行文档与 whitespace 检查**

Run:

```bash
git -C folly diff --check
git -C fbthrift diff --check
rg -n "TBD|TODO|PLACEHOLDER" \
  fbthrift/thrift/perf/cpp2/performance \
  folly/AGENTS.md folly/CLAUDE.md fbthrift/AGENTS.md fbthrift/CLAUDE.md
```

Expected: `diff --check` 无输出；placeholder 搜索仅允许人工指导书中解释禁止项的文字。

- [x] **Step 3: 分仓库提交**

```bash
git -C folly add AGENTS.md CLAUDE.md
git -C folly commit -m "docs: route agents to cross-repository performance records"

git -C fbthrift add AGENTS.md CLAUDE.md thrift/perf/cpp2/performance
git -C fbthrift commit -m "docs: initialize performance EDR maintenance system"
```

- [x] **Step 4: 推送同名分支**

```bash
git -C folly push origin HEAD:agent/codex/cxl-mem-rocket-benchmark
git -C fbthrift push origin HEAD:agent/codex/cxl-mem-rocket-benchmark
```
