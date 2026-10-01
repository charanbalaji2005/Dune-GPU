# Benchmark methodology

* **What is measured**: simulated clock cycles of the TFX-X microarchitecture, from
  the C++ cycle model (`benchmarks/run_benchmarks.py`). That model is cycle-exact
  against the RTL for every case co-simulated in the regression, and the
  benchmark script can re-run a subset on RTL (`--rtl-check`; 20/20 identical in
  the recorded run).
* **What is not measured**: wall-clock speed, frequency, area, power or energy.
  No silicon or FPGA exists; synthesis is technology-independent (see
  `results/synthesis/`). Cycle counts must not be read as performance of a
  real chip or compared with commercial GPUs.
* **Correctness gate**: every benchmark run is first checked against its Python
  golden model and the functional model; a wrong answer aborts the sweep.
* **Inputs** are deterministic (fixed seeds) and scale with the configuration
  (`benchmarks/workloads.py`), so cross-config comparisons are only meaningful for
  fixed-work-per-thread kernels (dep_chain, ilp, divergence, stride).
* **Metrics**: IPC = warp instructions issued / cycles (upper bound 1.0, single
  issue); thread IPC = active-lane instructions / cycle; SIMD efficiency =
  active lanes / (non-control issued × lanes); stall breakdown by priority.
* **Reproduce**: `python3 benchmarks/run_benchmarks.py --rtl-check` (≈ 20 s).
  Output: `results/benchmarks/benchmarks.{json,md}`.
