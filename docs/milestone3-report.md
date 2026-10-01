# Milestone 3 report: correctness first, then a simulated L1

Only results from commands actually executed are reported. The raw records are
in `results/regressions/`, `results/benchmarks/` and `results/synthesis/`.
**Simulation vs. RTL:** everything labelled RTL is synthesizable SystemVerilog,
verified on Icarus Verilog 12. The L1 cache is a C++ simulation model only.

## 1. Plan (file by file) and what was done

| Step | Files | Status |
|---|---|---|
| Audit RTL, C++ model, ISA, tests and build scripts | all of `rtl/`, `simulator/`, `software/reference/`, `compiler/`, `tests/`, `scripts/` | done. Found the divergence bug and the fake-pipelined multiplier. Scoreboard, bypass, barriers, atomics and reset showed no bug |
| Regression tests **before** fixes | `tests/test_regressions_m3.py` (new) and a raw-command RTL runner in `tools/cosim/cosim.py` | done: 6 failed / 25 passed (`m3_before_fixes.txt`) |
| Divergence fix (ISA v1.1, `BRANCH_DEPTH` = 8) | `architecture/isa/tfx_isa_v1.json`, `software/reference/tfx_isa.hpp`, `simulator/include/tfx_machine.hpp`, `rtl/compute/tfx_simt_cluster.sv`, `rtl/command_processor/tfx_command_processor.sv`, `rtl/top/charangpu_tfx_top.sv`, `compiler/tfxasm.py`, `tools/cosim/faults.py` | done |
| Pipelined multiplier | `rtl/core/tfx_mul_pipe.sv` (new), `rtl/core/tfx_alu.sv` (`HAS_MUL`, default 1), cluster integration, source lists in `scripts/run_all.sh`, `Makefile`, `synthesis/yosys/run_synth.sh`, `tools/cosim/cosim.py` | done |
| Pipeline-interaction verification | regression tests, fuzzing on 4 configs, mutation test extended to 7 mutants run on isolated copies (`tools/cosim/mutation_test.py`) | done |
| L1 data-cache model (C++ only) | `simulator/include/tfx_cache.hpp` (new), `tfx_timing.hpp`, `tfx_config.hpp`, `simulator/src/tfx_sim.cpp`, `architecture/schemas/cluster_config.schema.json`, `tools/configuration/validate_config.py`, `architecture/configuration/tfx_base_l1.json` (new), `tests/test_l1_cache.py` (new) | done |
| Benchmarks | `benchmarks/run_benchmarks.py` (baseline comparison and L1 sweep), `results/benchmarks/milestone2_baseline.json` | done |
| Synthesis | `synthesis/yosys/run_synth.sh` (4 new jobs), `mul_stages.v` and `mul_stage_analysis.sh` (new), `results/synthesis/milestone2_summary.txt` | done |
| Docs | `docs/isa.md`, `execution-model.md`, `memory-system.md`, `architecture.md`, `host-interface.md`, `verification.md`, `known-limitations.md`, ADRs 0005-0007, this report, `README.md` | done |

**Interfaces preserved.** The only visible change is that the `READ_FAULT` code
widened from 3 to 4 bits (bits 19:16); codes 1-7 are unchanged. Branch encodings
with a 0 depth field behave as in v1.0. `tfx_alu` keeps its ports.

## 2. Correctness changes

**SIMT divergence.**
- **The bug:** `jmp`/`bany`/`bnone` could leave or enter `if`/`else` bodies. That
  leaked mask-stack entries, and could skip else-bodies for lanes that were masked
  off, with no fault.
- **The fix:** a static structured-region rule in the assembler plus a dynamic depth
  check in both C++ and RTL. See `docs/isa.md` and ADR 0005.

**Multiplier.**
- **Before:** a full 32×32 product was computed in one combinational cycle and then
  only delayed by the latency stages.
- **Now:** stage A computes 16×16 partial products; stage B does a carry-save
  reduction and one 64-bit adder.
- **Latency:** unchanged, so cycle-exact equivalence with the C++ model holds.

## 3. Regression results (final sources, clean build)

| Check | Result |
|---|---|
| Fast regression | `scripts/run_all.sh` exit code 0; 7/7 unit/differential TBs; Verilator lint clean; **246 pytest passed** |
| Slow RTL kernels | 4/4 passed |
| RTL fuzzing | 145/145 identical across all three models (base 40, small 40, one-bank 40, wide 25) |
| L1 fuzzing (C++) | 200/200 agree with the functional model |
| Mutation test | 7/7 mutants detected |
| Benchmark RTL cross-check | 20/20 identical |
| Images | regenerated; golden match and RTL-identical |

**Incident.** An interrupted, in-place mutation run left a mutant in the tree and
made unrelated fuzz runs fail. The same seed passed 80/80 on clean RTL. I fixed the
tool to mutate isolated copies, and all records above were regenerated afterwards
(see `docs/verification.md`).

## 4. Benchmark comparison (simulated cycles; not hardware)

**Milestone 2 → 3:** all 208 runs (13 workloads × 4 policies × 4 configs) have
**identical** cycle counts.

**L1 sweep on tfx_base** (`results/benchmarks/benchmarks.md` §7). Cells are cycles,
with the load hit rate in brackets. The first row is the RTL's 1-cycle banked
scratchpad, which is a different memory system shown for reference.

| | vector_add | matmul | blur | reduction | histogram | raster |
|---|---:|---:|---:|---:|---:|---:|
| scratchpad (RTL design) | 263 | 5266 | 4933 | 652 | 611 | 33981 |
| L1 1 KiB, 2-way, 32 B, LRU, miss 20 | 720 (0.00) | 7823 (0.86) | 6089 (0.93) | 1178 (0.30) | 4523 (0.00) | 34080 (1.00) |
| 4 KiB | 720 | 6320 (0.94) | 6089 | 1178 | 4523 | 34080 |
| 4-way | 720 | 9313 (0.78) | 6089 | 1178 | 4523 | 34080 |
| 64 B lines | 492 (0.46) | 6762 (0.92) | 5465 (0.95) | 830 (0.61) | 4295 (0.50) | 34040 |
| FIFO | 720 | 7298 (0.89) | 6089 | 1178 | 4523 | 34080 |
| miss latency 40 | 1240 | 10763 | 7369 | 1918 | 8843 | 34180 |

**Observations** (these hold for this model and these kernels only):
- **Streaming kernels see no reuse:** vector_add has a 0% hit rate with 32 B lines.
- **Line size matters more than associativity here.**
- **matmul is unusual:** 4-way is worse than 2-way at 1 KiB (fewer sets for its
  column stride), and FIFO beats LRU.
- **Histogram is bound by atomics,** which go to memory, so its cycles track the
  miss latency.
- **Raster is compute-bound,** so the cache barely changes it.

## 5. Synthesis (Yosys generic cells, unit delay; no PDK, no timing)

| Block | Milestone 2 | Milestone 3 |
|---|---|---|
| ALU in the cluster | 14,450 cells, depth 53 (with MUL) | 1,553 cells, depth 28 (`HAS_MUL=0`) |
| Multiplier, worst stage (1 lane) | 53 (inside the ALU) | 34 (stage A); stage B 28 |
| Multiplier, all 8 lanes, `MD=1` | – | 64,879 cells, depth 34 |
| Tiny cluster 2w × 4l | 130,030 cells, depth 70 | 122,484 cells, depth 69 |

For one lane, a monolithic 32×32 multiplier on its own is 44 deep. The first
stage-B design with chained adders was 46 deep, so it was rejected.

## 6. Limitations
See `docs/known-limitations.md`. The main ones:
- The cluster's critical path is not yet identified and not yet reduced.
- The L1 exists only in C++.
- The dynamic branch check is depth-based.
- No formal verification, no gate-level simulation and no FPGA/silicon data.

## 7. Next milestone (correctness and timing before new features)
1. Identify the cluster's critical path with named-net `ltp` reports, and pipeline
   or split it, likely eligibility → scheduler → issue.
2. Add formal properties with SymbiYosys (scoreboard never under-flows, mask
   stack bounds, at most one LSU operation in flight). Since milestone 4 also
   adds a non-blocking L1, the properties should cover its miss tracking too.
3. Add an RTL L1 with outstanding-miss tracking, co-simulated against an extended
   C++ model that tracks outstanding misses (the current blocking model would no
   longer match).
4. Bring up an FPGA build to get the first measured Fmax and resource numbers.
