# Milestone 2 report

This report lists only results from commands that were actually executed.

## A. Baseline audit
The uploaded milestone-1 tree was inspected file by file. It contained 5 RTL
testbenches, the C++ ALU reference and pytest. Two problems were found and fixed:
* `sim/tb_alu_diff.sv` stored the vector path in a 256-bit register, which truncated
  long paths and caused "cannot open vectors".
* `scripts/run_all.sh` failed when pytest was missing.

## B. Files
| Status | Files |
|---|---|
| Modified | `README.md`, `docs/architecture.md`, `scripts/run_all.sh`, `tests/run_rtl.py`, `software/reference/{tfx_isa.hpp,tfx_ref.cpp}`, `rtl/core/tfx_alu.sv` (same ports, 16 ops), `rtl/interconnect/tfx_fifo.sv` (width casts only), `sim/tb_alu_diff.sv` |
| Removed | `docs/verification-plan.md` (superseded by `docs/verification.md`) |
| Created | everything else listed in the README repository map |

## C. Key decisions
See `docs/adr/0001-0004`:
1. Shared 4-bit ALU op field, so the ISA stays backward compatible.
2. Explicit `if/else/endif` mask stack for divergence.
3. Per-warp scoreboard with writeback bypass, and one LSU operation in flight.
4. A cycle-exact C++ model as the reference for the RTL.

## D. Features implemented
These are listed in the README, "What's in milestone 2".

## E. Deferred
Floating point, caches and DRAM, multiple clusters, a graphics pipeline, a compiler
above assembly, FPGA and PDK flows (see `docs/asic-roadmap.md`).

## F. Commands
* `make test`
* `make test-full`
* `python3 -m pytest -q -m slow`
* `python3 tools/cosim/fuzz.py --config <cfg> --count N --seed S`
* `python3 tools/cosim/mutation_test.py`
* `python3 benchmarks/run_benchmarks.py --rtl-check`
* `python3 tools/render_images.py --rtl`
* `synthesis/yosys/run_synth.sh`

## G. Results
* **Fast regression:** `scripts/run_all.sh` returned rc=0.
  * All 4 configurations validated.
  * All 7 unit/differential TBs passed: ALU 5,000 vectors, decoder 20,256, scheduler
    4,096 exhaustive, plus the register file, FIFO, token channel and compute lane.
  * Verilator lint was clean, with only UNUSEDSIGNAL waived.
  * pytest: 124 passed (4 slow tests deselected).
* **Slow RTL group:** 4 passed (matmul, blur, mandelbrot, raster on tfx_base).
* **RTL co-simulation:** the RTL is identical to the C++ cycle model on cycles, all
  counters, every register, all memory, faults and the full trace for:
  * 13/13 kernels on tfx_base and tfx_small.
  * 11 on tfx_onebank and 5 on tfx_wide.
  * GTO, PRIO and MEMFIRST on 6 kernels each (LRR is covered by every run above).
  * Every kernel also matched its Python golden model.
* **Faults:** 24/24 passed (12 programs × 2 configurations).
* **Fuzzing:**
  * 185 random programs on RTL: base 60, small 60, onebank 40, wide 25.
  * 300 programs on the C++ models only.
  * Zero mismatches.
* **Mutation test:** 4/4 injected bugs detected.
* **Benchmark RTL cross-check:** 20/20 identical.

## H. Limitations
See `docs/known-limitations.md`.

## I. Measurements (simulated cycles, not hardware)
See `results/benchmarks/benchmarks.md`. Selected values on tfx_base:

| Kernel | Cycles | IPC | SIMD efficiency |
|---|---:|---:|---:|
| vector_add (101 elements) | 263 | 0.859 | – |
| mandelbrot 32×24 | 33,282 | 0.947 | 0.702 |
| raster 32×32 | 33,981 | 0.959 | – |
| matmul 16×16 | 5,266 | 0.997 | – |

* Bank-stride sweep: unit stride needs 68 LSU rounds; stride 8 needs 516; a single
  bank needs 544.
* dep_chain on 1 warp: IPC 0.794 at MUL latency 2 and 0.506 at latency 8. On 4 warps
  it stays at 0.99 up to latency 4.

## J. Synthesis
Generic Yosys cells, unit-delay depth, no PDK (see `results/synthesis/summary.txt`):

| Block | Generic cells | Logic depth |
|---|---:|---:|
| ALU | 14,450 | 53 |
| Decoder | 518 | 14 |
| Scheduler (4 warps) | 76 | 14 |
| FIFO (64-bit × 8) | 1,095 | 10 |
| LSU (4w × 8l × 8 banks) | 11,448 | 53 |
| Command processor | 820 | 9 |
| Cluster (2w × 4l, tiny memories) | 130,030 | 70 |

## K. Suggested milestone 3
1. Pipeline the multiplier and give the LSU a queue with multiple requests in flight.
2. Add an L1 data cache in front of an external memory model with latency.
3. Add FP32 add/mul/fma, with a fused-path model in C++.
4. Add a tiny fixed-function front end for the rasterizer (triangle setup, bounding
   box, tile binning) feeding the compute cluster.
5. Build multiple clusters on a ready/valid interconnect.
6. Bring up an FPGA build to get the first real Fmax and resource numbers.
