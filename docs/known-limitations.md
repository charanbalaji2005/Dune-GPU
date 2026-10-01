# Known limitations

## Architecture
* Integer only: no floating point, no division, no texture/sampler units and no
  fixed-function graphics. The "rasterizer" and "image filter" are compute kernels.
* Single cluster and single issue, with one LSU operation in flight. The RTL has no
  caches, no DRAM, no virtual memory and no interrupts.
* Divergence is structured only. Since v1.1, out-of-region branches are rejected by
  the assembler. At runtime they fault with `BRANCH_DEPTH` only when the stack
  *depth* differs. Hand-written binaries can still branch between a then-body and
  an else-body at the same depth without a fault.
* `atom.add` is the only atomic. The old values returned to registers depend on
  warp order.

## Implementation (synthesizable RTL)
* **Multiplier.** Since milestone 3 it is genuinely pipelined, in two stages:
  stage A is 34 cells deep and stage B is 28 (one lane). Stages beyond two are pure
  delay, because generic Yosys does not retime.
* **Cluster critical path.** The tiny cluster's longest path barely moved (70 → 69),
  so the multiplier was not the cluster's critical path. That path has not been
  identified yet; candidates are the eligibility → scheduler → issue logic and the
  LSU (53).
* **Register files** use asynchronous read, so they are not SRAM-macro friendly.
* **Zero-initialised memories.** Scratchpad and imem zero-init exist in simulation only.
* **Icarus workarounds.** Function calls inside `always_comb` hang Icarus 12, and it
  rejects variable-index part-selects of packed 2-D arrays. The RTL is written
  around both.
* **Synthesis front end.** Yosys 0.33 cannot parse the SystemVerilog directly, so
  synthesis goes through sv2v.

## Simulation-only features
* **The L1 data cache exists only in the C++ cycle simulator.** It is tag-only and
  blocking, with no outstanding-miss tracking. It is write-through with
  no-write-allocate, and assumes an unlimited write buffer. Atomics are performed at
  memory. Its numbers are design-space estimates, not properties of the RTL.

## Verification
* No formal proofs, no gate-level simulation, no FPGA bring-up and no timing closure.
* Synthesis numbers are generic cell counts and unit-delay depths. They are not
  area, frequency or power for any process.
* Benchmarks are simulated cycles of this design running these kernels.
