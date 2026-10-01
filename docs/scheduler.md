# Warp scheduler

Mechanism (who *may* issue) is computed by the cluster; policy (who *does*
issue) lives in `rtl/scheduler/tfx_warp_scheduler.sv`, mirrored by
`tfx::select_warp()` in `software/reference/tfx_sched.hpp`. The RTL scheduler is
exhaustively compared with the C++ function for 4 warps (4,096 vectors), and
indirectly in every co-simulated kernel. The policy is chosen per launch.

| Id | Name | Rule |
|---:|---|---|
| 0 | LRR | loose round-robin starting after the last issued warp |
| 1 | GTO | greedy-then-oldest: keep issuing the last warp while it is eligible, else the lowest-id eligible warp |
| 2 | PRIO | fixed priority, lowest eligible warp id |
| 3 | MEMFIRST | round-robin among eligible warps whose next instruction is a memory op; otherwise LRR |

## Observed behaviour (simulated, tfx_base; full tables in `results/benchmarks/benchmarks.md`)

* MEMFIRST was the fastest policy on 7 of the 13 workloads and never more than 2.5 %
  slower than the best one on tfx_base (3.8 % worst case over all 52 config × workload
  pairs). Worst-case slowdowns vs. the best policy on tfx_base: LRR 10.5 %, GTO 28.6 %,
  PRIO 36.3 %. MEMFIRST helps most when loads gate later work (`matmul`: 5254 cycles vs
  7162 for PRIO).
* GTO wins slightly on short streaming kernels (`vector_add` 238 vs 263 LRR) but loses
  badly when one warp monopolises issue and then all warps hit the LSU together
  (`raster` 40469 vs 33981, `matmul` 6758 vs 5266).
* PRIO is never the best and starves high-id warps until low-id warps stall.

These are properties of this specific single-issue, single-LSU design and these
kernels, not general claims.
