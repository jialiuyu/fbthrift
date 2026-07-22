# 数据来源和规范化说明

本分析的初始 open-loop 与微基准数据来自 Codex 会话附件：

```text
attachment cd826092-8792-4d4c-bdf9-442bf5ac1bb4 / pasted-text.txt
```

附件 SHA-256：

```text
b5ddb599787475bb5e0d44f2b146afcb9f92db3cf131e2ea4f801c6c36e94c05
```

两个 fixed-outstanding sweep 来自补充附件：

```text
attachment e72a2100-7e3a-4b7e-9448-f882b63bc350 / pasted-text.txt
```

补充附件 SHA-256：

```text
c268fed6a438569971b425925033e44f82822f17c910cf9fc0d29206c8d6ba2b
```

附件没有复制到仓库；五份 CSV 是从附件中的 benchmark 输出和 JSON 手工规范化得到的
耐久分析输入。测试会检查关键计数、派生比例和标签修正，降低转录错误风险。

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
- fixed-outstanding 两组结果中的 `Avg QPS` 都使用 10 秒 active window；CSV 同时保留
  `total_requests`、`active_time_s` 和原报告的 `avg_qps`，可检查三者是否一致。
- 对 saturated fixed-outstanding 结果，使用 `K × 1e6 / avg_qps` 推导
  `implied_cycle_us`。该值是容量守恒关系，不把 p50 当作 mean。
- `effective_exposure = Δ(implied_cycle_us) / injected_delay_us`。它是路径级敏感度，
  只有在 operation scope、串行关系和 counter 都明确时才能近似解释为操作次数。
- VA FLAT 原报告把 `670ns` 写成 `1x`。以 `335ns` 为 1x，应为 `2x`；CSV 的
  `source_label` 保留了该修正。
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

## `cpp2` fixed-outstanding closed-loop 命令

Client baseline：

```bash
numactl --cpunodebind=0 --membind=0 \
./client_ub --shm_mode=ubmem \
  --host=192.168.0.2 --port=7777 \
  --noop_weight=1 --warmup_sec=20 --terminate_sec=30 \
  --sla_p99_us=20000000 --num_clients=1 \
  --num_connections_per_thread=1 --max_outstanding_ops=100 \
  --gqm_inject_cost_ns=0
```

Server baseline：

```bash
numactl --cpunodebind=0 --membind=0 \
./server_ub --shm_mode=ubmem --port=7777 \
  --io_threads=1 --cpu_threads=1 --gqm_inject_cost_ns=0
```

附件列出了 `0, 335, 670, 1340, 3350, 6700, 13400, 33500, 67000ns` 的结果，
但没有逐点重复完整命令，因此不能确认每个非零点在哪些进程上设置了 knob。

## VA FLAT Compressed Polling 命令

Server baseline：

```bash
numactl --cpunodebind=0 --membind=0 \
./server_va --host=192.168.0.1 \
  --io_threads=1 --cpu_threads=0 --num_connections_per_thread=1 \
  --terminate_sec=15 --memid_s_req_notify=2 --memid_c_resp_notify=6 \
  --memid_c_data=7 --memid_s_data=3 --gqm_inject_cost_ns=0
```

Client baseline：

```bash
numactl --cpunodebind=0 --membind=0 \
./client_va --host=192.168.0.2 \
  --num_clients=1 --num_connections_per_thread=1 \
  --max_outstanding_ops=100 --echo_weight=1 --terminate_sec=15 \
  --memid_s_req_notify=2 --memid_c_resp_notify=6 \
  --memid_c_data=7 --memid_s_data=3 --gqm_inject_cost_ns=0
```

附件同样只按 delay 标题列出非零点，没有保存逐点完整命令、CPU 数据或 GQM counter。
