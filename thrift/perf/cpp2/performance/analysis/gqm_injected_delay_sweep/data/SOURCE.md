# 数据来源和规范化说明

本分析来自 Codex 会话附件：

```text
attachment cd826092-8792-4d4c-bdf9-442bf5ac1bb4 / pasted-text.txt
```

附件 SHA-256：

```text
b5ddb599787475bb5e0d44f2b146afcb9f92db3cf131e2ea4f801c6c36e94c05
```

原附件没有复制到仓库；三份 CSV 是从附件中的 benchmark 输出和 JSON 手工规范化得到的
耐久分析输入。测试会检查关键计数、派生比例、异常 run 和标签修正，降低转录错误风险。

## 规范化规则

- 所有微基准时间统一为 `latency_ns`；例如 `2.75us` 写为 `2750ns`。
- `successful_hw_ops_per_iteration` 仅用于校准注入：ping-pong 为 2，push-only 和
  pop-only 为 496，fill-drain 为 992。
- cross-core 和 MPMC 的操作数写为 1，但标记为 assumption，不用于结论图中的线性校准。
- RPC 的 QPS 和比例不预先写死在 CSV 中，而由原始计数和 `runtime_s` 计算。
- delay sweep 的 `0ns` 使用报告中单独给出的 35K anchor，不复用 baseline QPS
  sweep 中的 35K 点。
- 原报告把 `67000ns` 写成 `100x`。以该报告的 `335ns` 倍数基准计算，应为
  `200x`；CSV 同时保留原标签错误和修正结果。
## 原报告中的采集命令

微基准：

```bash
./io_async_gqm_queue_benchmark --bm_min_iters=100000 \
  --gqm_inject_cost_ns=<0|100|1000>
```

RPC server baseline：

```bash
numactl --cpunodebind=0 --membind=0 \
./Server --transport_mode=Ubmem --shm_mode=ubmem \
  --port=5000 --io_threads=1 --cpu_threads=1 \
  --server_runtime_s=20 --forbid_shm_fallback=true \
  --server_cpu_report_path=ubmem_baseline_qps.json \
  --gqm_inject_cost_ns=0
```

RPC client baseline：

```bash
numactl --cpunodebind=0 --membind=0 \
./OpenLoopStressTest --enable_ubmem --shm_mode=ubmem \
  --server_host=192.168.0.2 --server_port=5000 \
  --target_qps=<sweep> --open_loop_mode=poisson_exp \
  --open_loop_workload=Unary64 --warmup_s=3 --runtime_s=15 \
  --drain_timeout_s=5 --max_inflight=496 --client_threads=1 \
  --connections_per_thread=1 --result_path=baseline_qps.json \
  --gqm_inject_cost_ns=0
```

附件没有给出 delay sweep 的完整 server/client 命令，只给出了 delay 标题和结果 JSON。
因此当前无法从 artifact 确认注入是在 client、server 还是两端，也无法确认它分别作用于
push、pop 还是所有成功的 wrapper operation。
