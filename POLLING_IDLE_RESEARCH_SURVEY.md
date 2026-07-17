# 从忙轮询到自适应等待：用户态低延迟 I/O 的 CPU 与延迟取舍

日期：2026-07-16

> **文章简介**：DPDK、RDMA、CXL 共享内存 RPC、SPDK 等用户态或内核旁路系统，
> 普遍用持续轮询换取微秒级响应，却也因此在低负载时长期占用 CPU。本文整理相关论文、
> Linux/DPDK 官方文档和生产实践，说明业界如何从纯轮询演进到间歇轮询、忙轮询与中断混合、
> 硬件浅等待和动态 core 调度，并总结这些工作如何评价 CPU、功耗、吞吐和尾延迟之间的取舍。

本文只是一篇公开资料综述，不包含任何具体项目的 poller 设计、参数选择、代码实现、
benchmark 方案或实验结果。

## 1. 为什么这是用户态传输层的共同问题

低延迟 I/O 的核心矛盾很简单：

- **持续轮询**绕过中断、调度和线程唤醒，数据到达后能立即被发现；代价是即使没有工作，
  poller 也会持续执行并占用 CPU。
- **中断或事件等待**可以在空闲时释放 CPU；代价是下一次事件要经过设备通知、内核处理、
  调度、上下文切换和 cache 恢复，微秒级设备的优势可能被唤醒路径抵消。

传统设备延迟较高时，几微秒的唤醒成本并不突出；当 NIC、RDMA、NVMe 和 CXL 访问本身
进入微秒甚至亚微秒量级后，host wake-up 反而成为主要成本。FAST'12 的
[When Poll Is Better than Interrupt](https://www.usenix.org/conference/fast12/when-poll-better-interrupt)
已经用低延迟存储说明：当设备完成时间很短时，interrupt、scheduler 和 C-state 的额外成本
可能高于直接等待完成的成本。FastWake 则在 RDMA 上把同一问题称为需要重新审视的 host
network stack 问题。

公开系统没有形成一个适用于所有负载的固定答案。主流工作大致沿着四条路线演进：

1. 保留轮询，但让轮询强度随负载变化。
2. 先轮询一个有限窗口，再进入事件或中断等待。
3. 优化中断、线程唤醒和 CPU locality，降低 event path 本身的成本。
4. 不只控制一个 poller，而是在系统层动态重分配 core。

## 2. DPDK：从持续轮询到负载感知的间歇工作

### 2.1 PMD 与 `l3fwd-power`

DPDK PMD 的基础模型是在专用 lcore 上持续读取 RX/TX descriptor。其优点是路径短、批处理
效率高；缺点是 CPU 在没有流量时仍可能显示为 100% busy。DPDK 的
[Power Management](https://doc.dpdk.org/guides/prog_guide/power_man.html) 和
[`l3fwd-power`](https://doc.dpdk.org/guides/sample_app_ug/l3_forward_power_man.html)
展示了三类缓解方式：

- `monitor`：监控 RX descriptor 对应地址，在数据变化时恢复执行，依赖平台能力；
- `pause`：使用 power-aware pause 或普通 pause，降低空转强度；
- `scale`：按流量调节 CPU frequency，反应速度通常慢于 monitor 和 pause。

这些机制表达了一个重要区别：**降低功耗不等于释放 CPU**。调频可以减少能耗，但轮询线程
仍占据 core；monitor、sleep 或 interrupt 才可能让执行资源真正交给其他任务。DPDK 文档也
把 baseline、monitor、pause、scale 作为不同模式分别评估，而不是宣称某一种模式普遍最优。

### 2.2 Metronome

[Metronome](https://arxiv.org/abs/2103.13263) 直接把 DPDK continuous polling 改为
intermittent sleep-and-wake。它的目标不是单纯降低频率，而是让 I/O 线程的 CPU 使用随实际
负载变化，同时把额外 buffering delay 控制在目标范围内。

Metronome 使用多个 retrieval thread 维持服务连续性：拿到队列所有权的 primary thread
处理队列后短暂休眠，未拿到所有权的 backup thread 休眠更久。系统根据流量动态调整等待
参数。论文同时报告 CPU cycles、功耗、延迟和与 CPU-intensive 应用共存时的表现，说明其
评价对象是“资源是否可共享”，而不只是 packet latency 是否下降。

这项工作的代表性结论是：间歇轮询增加的主要成本是 buffering delay；如果流量可预测且
等待精度足够高，CPU 使用可以更接近负载比例。但这种方法依赖 workload 和目标延迟，不能
从论文中抽取一个通用 sleep 值。

## 3. Linux NAPI：中断、内核轮询和应用 busy poll 的混合体系

[Linux NAPI 文档](https://docs.kernel.org/networking/napi.html) 展示了成熟的 hybrid
模型。通常由设备 interrupt 调度 NAPI，NAPI 在预算内批量处理 packet；高负载时，中断被
抑制或延后，polling 负责吸收 burst；低负载时则回到 interrupt。

Linux 又把 busy polling 暴露给应用：

- `SO_BUSY_POLL` 和 `busy_read` 允许 socket read 前在内核中忙轮询；
- `busy_poll` 或 per-epoll 参数允许 `poll`/`epoll_wait` 主动触发 NAPI processing；
- `SO_PREFER_BUSY_POLL` 可以让高 request-rate 应用承诺周期性 busy poll，从而暂缓 IRQ；
- `gro_flush_timeout` 与 `napi_defer_hard_irqs` 决定连续空轮询后何时重新回到硬中断。

这不是简单的“打开 busy poll 开关”，而是 hardirq、softirq/timer 和 application-driven
polling 三条控制路径之间的转换。较新的 per-epoll 配置还包含 busy-poll 时长、budget 和
prefer-busy-poll 等参数。

[Net DIM](https://docs.kernel.org/next/networking/net_dim.html) 则从另一个方向自适应调节
interrupt moderation。它根据 bytes、packets、events 和采样间隔调整事件间隔与每事件最大
packet 数，体现的是吞吐、事件率和延迟之间的动态平衡。

生产侧资料也强调系统性调优。Cloudflare 的
[10GbE 低延迟实践](https://blog.cloudflare.com/how-to-achieve-low-latency/) 比较了普通
阻塞、`SO_BUSY_POLL` 和用户态非阻塞 busy loop：busy polling 降低了最小值、平均值和
jitter，但增加 CPU 使用；CPU pinning、RSS、flow steering 和 interrupt coalescing 同样会
影响结果。Netdev 0x18 的
[epoll busy-poll 实战](https://netdevconf.info/0x18/sessions/tutorial/real-world-tips-tricks-and-notes-of-using-epoll-based-busy-polling-to-reduce-latency.html)
进一步把多 NIC、RSS context、per-queue coalescing 和生产指标纳入讨论。

## 4. RDMA：应用显式选择 poll CQ 或 completion event

### 4.1 Verbs 的两条完成路径

RDMA verbs 将 polling 与 event 两种模式都暴露给应用：

- [`ibv_poll_cq()`](https://man7.org/linux/man-pages/man3/ibv_poll_cq.3.html) 直接消费 CQE，
  适合追求最低延迟的热路径；
- [`ibv_req_notify_cq()`](https://man7.org/linux/man-pages/man3/ibv_req_notify_cq.3.html)
  为 CQ 注册一次性通知；
- [`ibv_get_cq_event()`](https://man7.org/linux/man-pages/man3/ibv_get_cq_event.3.html)
  等待 completion channel event，并要求应用 ack 和重新 arm。

官方示例特别强调 three-step protocol：收到 event 后 ack，重新注册下一次通知，再 drain CQ。
通知是 one-shot；event 与 poll 之间存在 race；event ack 需要 mutex，适合批量摊销。这说明
event mode 并不是“把 poll 换成一次阻塞调用”，而是一个需要正确 rearm、recheck 和 drain
的协议。

[linux-rdma/perftest](https://github.com/linux-rdma/perftest) 为这种比较提供了标准工具：
latency 与 bandwidth benchmark 默认 polling，`--events` 可切换 CQ event；它还提供 message
size、QP 数、queue depth、CQ moderation、duration、histogram 等维度。官方 README 也明确
提醒：这些 synthetic microbenchmark 不等价于真实应用流量，latency 工具以 RTT 的一半近似
one-way latency，非对称配置下可能失真。

### 4.2 RDMAbox：event 唤醒后保留有限 polling

[RDMAbox](https://arxiv.org/abs/2104.12197) 提出 Adaptive Polling：先由 completion event
唤醒处理线程，随后 drain CQ；CQ 暂时为空时不立即重新休眠，而是保留一个有限的 polling
hook，以吸收同一 burst 中紧随其后的 completion。hook 用尽后才重新 arm event。

它解决的是两类空闲的区分：burst 内部的短 gap 适合继续 poll，burst 之间的长 gap 才值得
重新进入 event wait。论文同时比较 throughput、CPU utilization、interrupt 和 context switch，
并指出随着 peer/CQ 数增加，大量 dedicated polling thread 会与应用争抢 CPU；纯 event 又
可能产生过多上下文切换。

### 4.3 FastWake：优化的对象是整个唤醒路径

[FastWake](https://conferences.sigcomm.org/events/apnet2023/papers/sec1-fakewake.pdf)
把 RDMA interrupt latency 拆成 NIC event、interrupt、tasklet、scheduler、wait queue、IPI 和
context switch 等阶段，并给出两种模式：

1. per-core dispatcher 持续轮询同一 core 上多个线程的 CQ，发现 completion 后走快速
   context switch；延迟接近 dedicated polling，但 CPU 仍保持 100% active。
2. 保留 interrupt，通过动态 CQ/EQ 映射改善 interrupt affinity，并缩短内核 wake-up 路径；
   功耗更低，但延迟仍高于 dispatcher。

论文在 x86 和 ARM 上同时评估 latency 与 power。其报告中，FastWake 将传统 interrupt-mode
RDMA latency 分别降低约 80% 和 77%；在强调省电的 interrupt 方案中，改善约为 59% 和 52%。
这项工作说明：如果必须进入 sleep，唤醒在哪个 core、是否跨 core IPI、调度路径有多长，
都与 interrupt 本身同样重要。

## 5. 从 poller 退让到系统级 core 管理

### 5.1 Shenango

[Shenango](https://www.usenix.org/conference/nsdi19/presentation/ousterhout) 观察到，
kernel-bypass 应用为了满足 peak load 和微秒级 tail latency，往往为 spin polling 预留过多
core。它保留一个专用 IOKernel core 负责收包和资源控制，并以约 5 微秒粒度在 latency-sensitive
与 batch workload 之间重新分配 core。

Shenango 的评价重点不是 idle thread 的 CPU 百分比，而是 tail latency、throughput 和机器上
“未被浪费的 cycles”能否同时改善。实验使用 latency-sensitive 服务与 batch workload 共置，
比较两类业务的吞吐交换关系。这把问题从“poller 要不要 sleep”提升为“闲置 core 能否快速
变成其他工作负载的有效算力”。

### 5.2 Caladan

[Caladan](https://www.usenix.org/conference/osdi20/presentation/fried) 延续了微秒级动态资源
管理，但控制信号不限于 runnable task。它还关注 queueing delay、memory bandwidth、cache
和 hyperthread interference。论文说明静态资源分区既可能浪费资源，也无法及时应对 burst 和
phased behavior；快速 core allocation 与 interference control 才能同时维持 tail latency 和
较高利用率。

Shenango 与 Caladan 的共同结论是：当机器上存在多个 workload 时，单看 poller 自身 CPU
并不足以描述资源效率。被“省下”的 core 是否能被其他任务利用、是否引入共享 cache 或
memory bandwidth 干扰，必须进入系统级评价。

## 6. CXL 共享内存 RPC：轮询问题进入 load/store 通信

### 6.1 HydraRPC

[HydraRPC](https://www.usenix.org/conference/atc24/presentation/ma) 在真实 CXL 硬件上构建
共享内存 RPC。由于 CXL HDM 的 request/response entry 仍需要 arrival notification，论文讨论
了 optimized polling：以 `MONITOR/MWAIT` 及其用户态对应指令监控共享内存 cache line，
数据变化或超时后恢复检查，减少无条件 tight spin。

HydraRPC 的意义在于证明 CXL RPC 并不会自动消除 polling 问题：数据传输从 packet network
变成 load/store 后，接收端仍需要发现状态变化。论文主要报告 RPC latency、throughput 和 CPU
随负载变化的结果；低负载时 optimized polling 能减少 CPU footprint，接近峰值时则自然回到
高利用率。其公开评估并未系统展开“不同 idle gap 后第一条请求”的 cold-start tail 曲线。

### 6.2 RPCool

[RPCool](https://arxiv.org/abs/2408.11325) 通过 CXL shared memory 直接传递 pointer-rich data，
并处理权限隔离、内存管理和 RDMA fallback。它的 busy-wait 路径使用 adaptive sleep，评价则
覆盖 no-op RPC microbenchmark、Memcached、MongoDB、DeathStarBench 和自建 document store。

RPCool 的 CXL 环境使用 dual-socket remote NUMA memory 模拟，因为 CXL 3.0 多主机共享设备
尚不可得；因此论文中的 latency 和 sleep 策略应理解为该平台上的证据，而不是通用 CXL
硬件参数。它的重要贡献是同时观察通信延迟和应用 CPU 竞争：不休眠可以降低单次 RPC 延迟，
较长休眠则可能把 CPU 让给 application，从而在部分高负载点改善整体 throughput。

HydraRPC 和 RPCool 都证明了共享内存 RPC 的低延迟潜力，也共同暴露出一个研究空白：公开
结果通常以 end-to-end latency、throughput 和总 CPU 为主，对 idle duration、publish-to-detect
cold wake 和极端尾部的联合分布报告较少。

## 7. SPDK、io_uring 与 CPUIdle：相同取舍的其他实现

### 7.1 SPDK

SPDK reactor 默认不断执行 poller，以换取最低 I/O latency。
[Interrupt Mode](https://spdk.io/doc/interrupt_mode.html) 要求 subsystem/poller 提供可等待的
fd，由 reactor 通过 epoll 阻塞；跨 reactor 和跨 thread 消息通过 eventfd 唤醒。不是所有
subsystem 都天然支持这种模式，没有有效事件源的 active poller 也无法真正释放 CPU。

[SPDK Scheduler](https://spdk.io/doc/scheduler.html) 进一步统计 thread/reactor 的 busy 和
idle ticks，把 idle thread 合并到更少的 reactor，并让没有 thread 的 reactor 进入 interrupt
mode。这与 Shenango 的方向相似：资源节省的单位从一次 poll 变成整个 reactor/core。

### 7.2 io_uring SQPOLL

[`io_uring_sqpoll(7)`](https://man7.org/linux/man-pages/man7/io_uring_sqpoll.7.html) 使用 kernel
thread 轮询 submission queue。`sq_thread_idle` 决定无提交后继续 poll 多久；线程睡眠后，
producer 通过 `IORING_SQ_NEED_WAKEUP` 判断是否需要用 `IORING_ENTER_SQ_WAKEUP` 显式唤醒。
这一接口把“热路径不做 syscall、睡眠后才 sideband wake”做成了清晰协议。

官方文档强调 SQPOLL 的收益取决于 workload、hardware 和 kernel version，应在真实条件下
分别测试开启和关闭；如果应用的大部分时间花在处理 completion，而不是 submission syscall，
SQPOLL 未必有明显收益。

### 7.3 CPUIdle 与硬件用户态等待

[Linux CPUIdle](https://www.kernel.org/doc/html/latest/admin-guide/pm/cpuidle.html) 用两个概念
判断是否进入更深 C-state：`target residency` 表示至少空闲多久才能抵消进入/退出成本，
`exit latency` 表示恢复执行需要的最长时间；PM QoS 再限制可接受的 wake latency。

Intel 的
[User Wait Instructions 指南](https://cdrdv2-public.intel.com/751859/Power_Mgmt_User_Wait_Instructions_Power_Saving_TechGuide_751859v4.pdf)
讨论了 `UMONITOR`、`UMWAIT` 和 `TPAUSE` 在 polling workload 中的功耗价值。不过这些指令
不是无条件可用的“免费睡眠”：CPU 型号、BIOS、microcode 和 OS policy 都可能影响实际等待
状态。Intel 后续的
[MONITOR/UMONITOR guidance](https://www.intel.com/content/www/us/en/developer/articles/technical/software-security-guidance/technical-documentation/monitor-umonitor-performance-guidance.html)
还说明，某些平台即使枚举 WAITPKG 能力，也可能禁用 UMONITOR address monitoring。

## 8. 这些工作是如何评估轮询与等待的

不同领域的实验设计不完全相同，但指标有明显共性。

| 资料 | 主要 workload / baseline | 主要指标 | 结论边界 |
|---|---|---|---|
| DPDK `l3fwd-power` | baseline、monitor、pause、scale；不同流量 | packet rate、core frequency、功耗、CPU | power saving 不一定等于 core 可复用 |
| Metronome | 不同 offered load、CPU 共置干扰 | latency、CPU cycles、power、service continuity | 目标主要是 average buffering delay，不是所有 tail SLO |
| NAPI / busy poll | IRQ、NAPI poll、socket/epoll busy poll | latency、pps、CPU、IRQ、budget | RSS、NIC queue、coalescing 和 affinity 会改变结果 |
| Cloudflare | blocking、`SO_BUSY_POLL`、userspace polling、pinning | min/avg latency、jitter、pps | 生产网络调优不是单参数实验 |
| RDMA perftest | polling 与 `--events`；message size、QP、depth | min/median/max、histogram、bandwidth | synthetic stream 不代表真实应用，one-way 由 RTT/2 近似 |
| RDMAbox | busy poll、event、adaptive polling；不同 peer/burst | throughput、CPU、interrupt、context switch、tail latency | peer/CQ 规模会改变 polling 的竞争成本 |
| FastWake | x86/ARM；dispatcher、传统/优化 interrupt | completion latency、power、core affinity | dispatcher 低延迟但仍占满 CPU |
| Shenango / Caladan | open-loop 服务与 batch/干扰 workload 共置 | p99/p99.9、throughput、queueing delay、useful cycles | 关注整机效率，而非单 poller CPU |
| HydraRPC / RPCool | no-op RPC、payload、并发、应用 benchmark | RTT、throughput、CPU、应用性能 | CXL 硬件或模拟环境不同，参数不可直接横向复制 |
| SPDK / SQPOLL | poll mode 与 interrupt/wakeup mode | reactor busy/idle、I/O latency、CPU | 需要真实可等待事件源，subsystem 支持程度不同 |
| CPUIdle | 不同 predicted idle duration 与 latency QoS | residency、exit latency、energy | 依赖具体处理器和平台策略 |

从这些公开评价可以归纳出四点：

1. **CPU 与 latency 必须同时报告。** 只看 p50 或 throughput，会掩盖 dedicated core 和功耗；
   只看 CPU，又会漏掉 cold wake 对 tail 的影响。
2. **负载强度不是唯一变量。** burst、queue 数、peer 数、message size、batch、affinity 和
   共置干扰都会改变 polling 的收益。
3. **event mode 需要测完整路径。** interrupt 数量只是表象，IRQ affinity、wake-to-run、
   context switch 和 cache locality 往往决定最终延迟。
4. **公开工作对 cold-start 的刻画仍不统一。** 很多论文报告 steady-state 平均延迟或若干
   percentile，但较少按 idle gap 分桶展示第一条请求的 detection/wakeup tail。

## 9. 文献形成的共同认识

综合网络、RDMA、存储、runtime 和 CXL RPC 的公开结果，可以得到以下非项目化结论：

- 持续轮询仍是低延迟上限的基准，但不是低负载资源效率的答案。
- 简单固定 sleep 只是在 CPU 和 buffering/wakeup delay 之间静态换算；流量变化时容易失效。
- 最常见的折中是 bounded polling：事件到达后保留一段轮询以吸收 burst，长时间无工作再
  回到 event、interrupt 或硬件等待。
- 如果 event path 过慢，优化重点不只是缩短 sleep，而是缩短 arm、IRQ、scheduler、IPI、
  context switch 和 cache recovery 的完整链路。
- 当多个应用共享机器时，poller-local 优化的上限有限；Shenango、Caladan 和 SPDK scheduler
  说明动态 core allocation 可能比单线程 sleep 更能提高整机效率。
- 没有可跨系统复制的“最佳轮询次数”或“最佳休眠微秒数”。这些值与设备延迟、CPU、
  workload burstiness、SLO 和资源竞争共同相关。

## 10. 推荐阅读顺序

如果只想快速建立完整认识，可以按以下顺序阅读：

1. [Metronome](https://arxiv.org/abs/2103.13263)：理解间歇轮询如何显式交换 CPU 与 buffering delay。
2. [Linux NAPI](https://docs.kernel.org/networking/napi.html)：理解成熟 hybrid 如何在 IRQ、timer 和 busy poll 之间切换。
3. [`ibv_get_cq_event()`](https://man7.org/linux/man-pages/man3/ibv_get_cq_event.3.html)：理解 one-shot event、rearm、ack 和 drain。
4. [FastWake](https://conferences.sigcomm.org/events/apnet2023/papers/sec1-fakewake.pdf)：理解 RDMA wake-up 路径为何昂贵。
5. [Shenango](https://www.usenix.org/conference/nsdi19/presentation/ousterhout)：理解为什么问题最终会上升到 core allocation。
6. [HydraRPC](https://www.usenix.org/conference/atc24/presentation/ma)：理解 CXL load/store RPC 为什么仍需要 notification/polling。
7. [When Poll Is Better than Interrupt](https://www.usenix.org/conference/fast12/when-poll-better-interrupt)：理解跨领域的 break-even 思路。

## 11. 参考资料目录

### 11.1 DPDK 与间歇轮询

- [DPDK Power Management](https://doc.dpdk.org/guides/prog_guide/power_man.html)
- [DPDK `l3fwd-power`](https://doc.dpdk.org/guides/sample_app_ug/l3_forward_power_man.html)
- [DPDK `l3fwd-power` source example](https://doc.dpdk.org/api/examples_2l3fwd-power_2main_8c-example.html)
- [DPDK DTS Power PMD test plan](https://doc.dpdk.org/dts/test_plans/power_pmd_test_plan.html)
- [DPDK DTS Power Telemetry test plan](https://doc.dpdk.org/dts/test_plans/power_telemetry_test_plan.html)
- [Metronome paper](https://arxiv.org/abs/2103.13263)
- [Metronome implementation](https://github.com/marcofaltelli/Metronome)

### 11.2 Linux 网络与生产 busy polling

- [Linux NAPI documentation](https://docs.kernel.org/networking/napi.html)
- [Linux NAPI 中文文档](https://docs.kernel.org/translations/zh_CN/networking/napi.html)
- [Linux network sysctl](https://docs.kernel.org/admin-guide/sysctl/net.html)
- [epoll busy-poll ioctl](https://www.man7.org/linux/man-pages/man2/ioctl_eventpoll.2.html)
- [Net DIM](https://docs.kernel.org/next/networking/net_dim.html)
- [Busy Polling: Past, Present, Future](https://netdevconf.info/2.1/papers/BusyPollingNextGen.pdf)
- [Netdev 0x18 epoll busy-poll tutorial](https://netdevconf.info/0x18/sessions/tutorial/real-world-tips-tricks-and-notes-of-using-epoll-based-busy-polling-to-reduce-latency.html)
- [Cloudflare: How to achieve low latency with 10Gbps Ethernet](https://blog.cloudflare.com/how-to-achieve-low-latency/)

### 11.3 RDMA completion 与自适应轮询

- [`ibv_poll_cq(3)`](https://man7.org/linux/man-pages/man3/ibv_poll_cq.3.html)
- [`ibv_req_notify_cq(3)`](https://man7.org/linux/man-pages/man3/ibv_req_notify_cq.3.html)
- [`ibv_get_cq_event(3)`](https://man7.org/linux/man-pages/man3/ibv_get_cq_event.3.html)
- [linux-rdma/rdma-core](https://github.com/linux-rdma/rdma-core)
- [linux-rdma/perftest](https://github.com/linux-rdma/perftest)
- [RDMAbox](https://arxiv.org/abs/2104.12197)
- [FastWake paper](https://conferences.sigcomm.org/events/apnet2023/papers/sec1-fakewake.pdf)
- [FastWake project page](https://01.me/projects/FastWake/)

### 11.4 Runtime 与 core allocation

- [Shenango paper page](https://www.usenix.org/conference/nsdi19/presentation/ousterhout)
- [Shenango source](https://github.com/shenango/shenango)
- [Caladan paper page](https://www.usenix.org/conference/osdi20/presentation/fried)
- [Caladan artifact](https://github.com/shenango/caladan-all)
- [IX](https://www.usenix.org/conference/osdi14/technical-sessions/presentation/belay)
- [eRPC](https://www.usenix.org/conference/nsdi19/presentation/kalia)

### 11.5 CXL 共享内存 RPC

- [HydraRPC paper page](https://www.usenix.org/conference/atc24/presentation/ma)
- [HydraRPC PDF](https://www.usenix.org/system/files/atc24-ma.pdf)
- [RPCool abstract](https://arxiv.org/abs/2408.11325)
- [RPCool PDF](https://arxiv.org/pdf/2408.11325)

### 11.6 SPDK、io_uring 与硬件等待

- [SPDK Interrupt Mode](https://spdk.io/doc/interrupt_mode.html)
- [SPDK NVMe Interrupt Mode](https://spdk.io/doc/nvme_interrupt_mode.html)
- [SPDK Scheduler](https://spdk.io/doc/scheduler.html)
- [`spdk_top`](https://spdk.io/doc/spdk_top.html)
- [`io_uring_sqpoll(7)`](https://man7.org/linux/man-pages/man7/io_uring_sqpoll.7.html)
- [`io_uring_setup(2)`](https://www.man7.org/linux/man-pages/man2/io_uring_setup.2.html)
- [Linux CPUIdle](https://www.kernel.org/doc/html/latest/admin-guide/pm/cpuidle.html)
- [Linux PM QoS](https://docs.kernel.org/power/pm_qos_interface.html)
- [Intel User Wait Instructions guide](https://cdrdv2-public.intel.com/751859/Power_Mgmt_User_Wait_Instructions_Power_Saving_TechGuide_751859v4.pdf)
- [Intel MONITOR/UMONITOR guidance](https://www.intel.com/content/www/us/en/developer/articles/technical/software-security-guidance/technical-documentation/monitor-umonitor-performance-guidance.html)
- [`futex(2)`](https://www.man7.org/linux/man-pages/man2/futex.2.html)
- [`eventfd(2)`](https://www.man7.org/linux/man-pages/man2/eventfd2.2.html)
- [When Poll Is Better than Interrupt](https://www.usenix.org/conference/fast12/when-poll-better-interrupt)
