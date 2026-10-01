# Execution model and pipeline timing

The cycle behaviour below is implemented identically in
`rtl/compute/tfx_simt_cluster.sv` and `simulator/include/tfx_timing.hpp`; the
regression requires both to produce the same cycle count, counters and per-cycle
issue trace.

## Launch

`LAUNCH(warps, policy)` resets every warp's PC to 0 and mask to all lanes,
clears scoreboards, mask stacks, barrier and fault state and all performance
counters, and makes every register read as zero (per-entry valid bits, one
cycle). Warps `>= warps` stay idle. The kernel finishes when no warp is alive
and nothing is in flight; the `LAUNCH` response then returns the cycle count.

## Per-cycle behaviour

Single issue, two stages (ISSUE → DISPATCH), out-of-order completion across units.

**Eligibility** of warp *w*: alive, not waiting for a control instruction to
resolve, not parked at a barrier, no scoreboard hazard (RAW on rs1/rs2 or WAW on
rd against pending writes, with a same-cycle bypass of registers being written
back this cycle), and no structural hazard (LSU instructions need the LSU idle
and no LSU instruction in dispatch; `bar`/`exit` need zero in-flight operations).

**Issue**: the scheduler picks one eligible warp. Non-control instructions
advance the PC and set the destination's scoreboard bit; control instructions
mark the warp as waiting (it is ineligible until the next cycle's resolution), so
a branch costs one bubble *for that warp only* — other warps fill the slot.

**Dispatch**: operands are read from the lane register files. ALU results write
back at the end of the dispatch cycle (dependent instruction can issue the next
cycle via the bypass). MUL-class results write back `MUL_LAT` cycles after issue.
LSU instructions check alignment/bounds for all active lanes, then start the LSU.
Control instructions resolve PC, mask stack, barrier arrival, exit or fault.

**Multiplier** (`rtl/core/tfx_mul_pipe.sv`, new in milestone 3) is genuinely
pipelined:
- **Stage A** runs in the dispatch cycle. It computes four 16×16 partial products
  and the MULH sign-correction term.
- **Stage B** is a carry-save reduction of six 64-bit terms followed by one 64-bit
  adder. With `MUL_LAT = 2`, stage B feeds the register write port directly.
- **Further stages** are pure delay.

Issue-to-writeback latency is still `MUL_LAT`, so cycle counts are unchanged from
milestone 2. All 208 benchmark runs were identical. The cluster's ALU instances
are built with `HAS_MUL = 0`.

**Barrier release** happens when every alive warp is parked at a barrier.

**Stall accounting**: a cycle with no issue is charged to exactly one reason,
by priority: scoreboard > structural > barrier > other ("other" covers control
bubbles and pipeline drain). Hence `cycles = issued + Σ stalls`, which
`tests/test_models.py` checks for every workload.

## Performance counters (READ_PERF index)

0 cycles, 1 issued, 2 thread_instrs (active lanes summed), 3 alu, 4 mul, 5 mem,
6 ctrl, 7 stall_scoreboard, 8 stall_structural, 9 stall_barrier, 10 stall_other,
11 lsu_rounds, 12 lsu_lane_accesses, 13 divergent_ifs, 14 barrier_releases,
15 lsu_busy_cycles.

## Issue trace

With `--trace` (C++) or `+TRACE=` (RTL testbench) one line is written per cycle:
`<cycle> issue w=<warp> pc=<pc> word=<hex> mask=<hex> elig=<hex>` or
`<cycle> stall <reason>`. The two traces are compared line by line.

## Simulation-only L1 data-cache mode

With `l1_enable = 1` the C++ cycle model replaces the banked-scratchpad LSU
timing with a blocking L1 in front of the same data array (see
[memory-system.md](memory-system.md)). Architectural results are unchanged; only
LSU timing differs. There is no RTL for this mode, and the co-simulation harness
refuses to compare it against RTL.
