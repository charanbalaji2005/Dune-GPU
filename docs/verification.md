# Verification

Every check below is automated. All of them were executed for milestone 3 on the
final sources. The raw records are in `results/regressions/`. Commands are in
`scripts/run_all.sh`, the `Makefile` and `.github/workflows/ci.yml`.

## Layers

| Layer | What is compared | Where |
|---|---|---|
| Unit TBs (milestone 1) | register file, FIFO, token channel, compute lane | `sim/tb_*.sv`, `tests/run_rtl.py` |
| ALU / decoder / scheduler differential | RTL vs C++ reference: 5,000 / 20,256 / 4,096 (exhaustive) vectors | `sim/tb_*_diff.sv` |
| ISA consistency | JSON spec vs C++ opcode table vs assembler | `tests/test_isa.py`, `tests/test_assembler.py` |
| Golden models | 13 kernels × 4 configs × 4 policies, both C++ models vs independent Python code | `tests/test_models.py` |
| **Milestone-3 regressions** (written before the fixes) | structured-branch rule; MUL RAW/WAW latency for `MUL_LAT` 2-8; ALU forwarding; load-use; latency hiding; barrier ordering; barriers with exited and faulted warps; atomic lane order and contention; multiplier edge cases at latencies 2, 3 and 4; RTL relaunch/reset | `tests/test_regressions_m3.py` |
| **L1 model** (C++ only) | load-use hit/miss timing, LRU vs FIFO, random-policy determinism, associativity, line coalescing, no-write-allocate, atomic invalidation, "never changes results" (13 kernels × 6 variants), fuzzing | `tests/test_l1_cache.py` |
| System co-simulation | RTL top level, driven only through the host port with random gaps and backpressure, vs the C++ cycle model: exact cycles, 16 counters, all registers, all memory, faults, per-cycle trace | `tools/cosim/cosim.py`, `tests/test_cosim_rtl.py` |
| Fault containment | 13 programs covering all 8 fault codes | `tools/cosim/faults.py` |
| Random programs | constrained-random, race-free, all instruction classes | `tools/cosim/fuzz.py` |
| Mutation test | 7 injected RTL bugs, each applied to an isolated copy of the repository | `tools/cosim/mutation_test.py` |
| Lint / synthesizability | Verilator `-Wall`; sv2v + Yosys generic synthesis | `scripts/run_all.sh`, `synthesis/yosys/` |

## Milestone-3 results

The tests were written first and run before any fix (`m3_before_fixes.txt`):
6 failed and 25 passed. All 6 failures were the divergence bug:
- 3 out-of-region branches that the assembler failed to reject;
- the missing depth-field encoding;
- the missing depth fault, on 2 configs.

The 25 timing, barrier, atomic, multiplier and relaunch contracts already held.

After the fixes, on a clean build:

| Check | Result |
|---|---|
| `scripts/run_all.sh` (`m3_run_all.log`) | exit code 0: 5 configs validated, 7/7 unit/differential TBs pass, Verilator lint clean, **246 pytest passed** (4 slow deselected) |
| Slow RTL kernels (`m3_slow.txt`) | 4 passed (matmul, blur, mandelbrot, raster) |
| Fuzzing (`m3_fuzz.txt`, seed 2027) | 145 programs on RTL, identical across all three models: base 40, small 40, one-bank 40, wide 25. Plus 200 L1-enabled programs on the two C++ models |
| Mutation test (`m3_mutation_test.txt`) | 7/7 detected: 5 by fuzz mismatch, 1 (branch-depth check disabled) by a directed test, 1 by an LSU hang. Working tree verified untouched |
| Benchmark RTL cross-check | 20/20 identical. 208/208 benchmark cycle counts unchanged from milestone 2 |
| Images | Mandelbrot, raster and blur match the golden models and are identical on RTL |

### Incident during this milestone

An interrupted mutation run left a mutant in the working tree. While it was
there, unrelated fuzz runs failed: 37/40 programs on base and 22/40 on small at
seed 505.

I confirmed the cause by re-running the same seed on the restored RTL: 80/80
agree. I then rewrote the mutation tool to mutate a throw-away copy of the
repository, and regenerated all records from a clean build.

## Known gaps
* No formal verification and no gate-level simulation.
* The dynamic branch check compares depth only.
* Fuzz programs are structured and race-free by construction. Out-of-region
  branches are covered by directed tests.
* One multiplier mutant (a carry lost in the first design) was caught by only
  1 of 25 random programs. The directed edge-case test covers it reliably, and
  random operands alone are weak for arithmetic corner cases.
* The L1 model is verified only against its own contracts and the functional
  model. It has no hardware reference.
