# CharanGPU TFX-X benchmark results (simulated)

> Simulated cycle counts from the C++ cycle model (cycle-exact vs RTL in the regression). Not hardware measurements. Regenerate with `python3 benchmarks/run_benchmarks.py`.

## 1. Scheduler policies (tfx_base: 4 warps x 8 lanes, 8 banks, MUL latency 3)

Cycles per policy; best per row in **bold**. IPC = warp instructions issued per cycle (max 1.0).

| workload | lrr | gto | prio | memfirst | IPC (lrr) | SIMD eff. | stall: scoreboard / struct / barrier / other |
|---|---:|---:|---:|---:|---:|---:|---|
| vector_add | 263 | **238** | 252 | 244 | 0.859 | 0.982 | 17 / 15 / 0 / 5 |
| dep_chain | 812 | 852 | 866 | **808** | 0.99 | 1.0 | 0 / 6 / 0 / 2 |
| ilp | 1124 | **1119** | 1143 | 1120 | 0.993 | 1.0 | 0 / 6 / 0 / 2 |
| divergence | 360 | 363 | 393 | **356** | 0.978 | 0.719 | 0 / 6 / 0 / 2 |
| reduction | 652 | **635** | 662 | 647 | 0.914 | 0.95 | 15 / 21 / 17 / 3 |
| histogram | 611 | 641 | 622 | **608** | 0.589 | 1.0 | 12 / 234 / 0 / 5 |
| matmul | 5266 | 6758 | 7162 | **5254** | 0.997 | 1.0 | 3 / 12 / 0 / 3 |
| stride1 | 227 | 266 | 250 | **224** | 0.969 | 1.0 | 2 / 3 / 0 / 2 |
| stride_bank | 414 | 491 | 439 | **410** | 0.531 | 1.0 | 189 / 3 / 0 / 2 |
| broadcast | 227 | 266 | 250 | **224** | 0.969 | 1.0 | 2 / 3 / 0 / 2 |
| mandelbrot | **33282** | 33453 | 34727 | 33283 | 0.947 | 0.702 | 837 / 0 / 0 / 916 |
| raster | **33981** | 40469 | 40589 | 34461 | 0.959 | 0.986 | 809 / 569 / 0 / 11 |
| blur | 4933 | **4930** | 5859 | 4931 | 0.999 | 1.0 | 2 / 0 / 0 / 3 |

## 2. Cycles by configuration (LRR)

| workload | tfx_small (2x4, 2 banks) | tfx_base (4x8, 8) | tfx_onebank (4x8, 1) | tfx_wide (8x16, 16) |
|---|---:|---:|---:|---:|
| vector_add | 180 (IPC 0.744) | 263 (IPC 0.859) | 465 (IPC 0.486) | 475 (IPC 0.922) |
| dep_chain | 409 (IPC 0.983) | 812 (IPC 0.99) | 840 (IPC 0.957) | 1622 (IPC 0.991) |
| ilp | 565 (IPC 0.988) | 1124 (IPC 0.993) | 1152 (IPC 0.969) | 2246 (IPC 0.994) |
| divergence | 166 (IPC 0.867) | 360 (IPC 0.978) | 388 (IPC 0.907) | 718 (IPC 0.981) |
| reduction | 336 (IPC 0.821) | 652 (IPC 0.914) | 789 (IPC 0.755) | 1375 (IPC 0.935) |
| histogram | 223 (IPC 0.807) | 611 (IPC 0.589) | 779 (IPC 0.462) | 1987 (IPC 0.362) |
| matmul | 10417 (IPC 0.998) | 5266 (IPC 0.997) | 8194 (IPC 0.64) | 2751 (IPC 0.989) |
| stride1 | 119 (IPC 0.924) | 227 (IPC 0.969) | 441 (IPC 0.499) | 450 (IPC 0.978) |
| stride_bank | 150 (IPC 0.733) | 414 (IPC 0.531) | 441 (IPC 0.499) | 1336 (IPC 0.329) |
| broadcast | 118 (IPC 0.932) | 227 (IPC 0.969) | 253 (IPC 0.87) | 450 (IPC 0.978) |
| mandelbrot | 54341 (IPC 0.978) | 33282 (IPC 0.947) | 33277 (IPC 0.947) | 19610 (IPC 0.999) |
| raster | 18422 (IPC 0.879) | 33981 (IPC 0.959) | 33989 (IPC 0.959) | 17186 (IPC 0.961) |
| blur | 9767 (IPC 0.999) | 4933 (IPC 0.999) | 7310 (IPC 0.674) | 2565 (IPC 0.998) |

Note: input sizes scale with the configuration (see benchmarks/workloads.py), so compare cycles across columns only for workloads with fixed work per thread.

## 3. Scratchpad bank conflicts (stride microbenchmark, 16 loads per lane)

| stride (words) | base: LSU rounds | base: lanes/round | onebank: rounds | wide: rounds | wide: lanes/round |
|---:|---:|---:|---:|---:|---:|
| 0 | 68 | 8.0 | 96 | 136 | 16.0 |
| 1 | 68 | 8.0 | 544 | 136 | 16.0 |
| 2 | 132 | 4.121 | 544 | 264 | 8.242 |
| 3 | 68 | 8.0 | 544 | 136 | 16.0 |
| 4 | 260 | 2.092 | 544 | 520 | 4.185 |
| 8 | 516 | 1.054 | 544 | 1032 | 2.109 |
| 16 | 516 | 1.054 | 544 | 2056 | 1.058 |

## 4. Multiplier latency: dependent chain vs independent chains

| MUL latency | dep_chain 1 warp | dep_chain 4 warps | ilp 1 warp | ilp 4 warps |
|---:|---:|---:|---:|---:|
| 2 | 253 (IPC 0.794) | 812 (IPC 0.99) | 307 (IPC 0.909) | 1124 (IPC 0.993) |
| 3 | 277 (IPC 0.726) | 812 (IPC 0.99) | 307 (IPC 0.909) | 1124 (IPC 0.993) |
| 4 | 301 (IPC 0.668) | 812 (IPC 0.99) | 307 (IPC 0.909) | 1124 (IPC 0.993) |
| 6 | 349 (IPC 0.576) | 860 (IPC 0.935) | 355 (IPC 0.786) | 1124 (IPC 0.993) |
| 8 | 397 (IPC 0.506) | 908 (IPC 0.885) | 403 (IPC 0.692) | 1124 (IPC 0.993) |

## 5. Latency hiding: resident warps (16 lanes, 16 banks)

Work per thread is fixed for dep_chain; matmul/mandelbrot have a fixed total problem size.

| warps | dep_chain IPC | matmul cycles (IPC) | mandelbrot cycles (IPC, SIMD eff.) |
|---:|---:|---:|---:|
| 1 | 0.668 | 4979 (0.524) | 33892 (0.574, 0.566) |
| 2 | 0.884 | 2895 (0.906) | 23646 (0.824, 0.567) |
| 3 | 0.951 | 2804 (0.942) | 19831 (0.983, 0.567) |
| 4 | 0.99 | 2675 (0.993) | 19936 (0.979, 0.568) |
| 5 | 0.99 | 2840 (0.941) | 19559 (0.999, 0.569) |
| 6 | 0.991 | 2713 (0.991) | 19700 (0.993, 0.569) |
| 7 | 0.991 | 2770 (0.976) | 19619 (0.998, 0.57) |
| 8 | 0.991 | 2751 (0.989) | 19610 (0.999, 0.57) |

## 6. Regression vs milestone 2 (pipelined multiplier + structured-branch fields)

208 benchmark runs (13 workloads x 4 policies x 4 configs) compared with `milestone2_baseline.json`: **0 changed cycle counts**. The new multiplier keeps the issue-to-writeback latency, so cycle behaviour is unchanged by design.

## 7. L1 data-cache model (SIMULATION ONLY, no RTL) on tfx_base

Default: 1 KiB, 32 B lines, 2-way, LRU, hit 1 / miss 20 cycles, write-through no-write-allocate, blocking. Each row changes one parameter. Cells: cycles (load hit rate). The scratchpad column is the RTL-equivalent design (1-cycle banked SRAM) for reference; it is a different memory system, not a 'no-cache' version of the same one.

| variant | vector_add | matmul | blur | reduction | histogram | stride1 | stride_bank | raster |
|---|---:|---:|---:|---:|---:|---:|---:|---:|
| scratchpad (RTL design) | 263 | 5266 | 4933 | 652 | 611 | 227 | 414 | 33981 |
| default | 720 (0.0) | 7823 (0.856) | 6089 (0.926) | 1178 (0.302) | 4523 (0.0) | 246 (0.969) | 566 (0.969) | 34080 (0.999) |
| size=256 | 720 (0.0) | 10385 (0.719) | 6089 (0.926) | 1178 (0.302) | 4523 (0.0) | 246 (0.969) | 566 (0.969) | 34080 (0.999) |
| size=4096 | 720 (0.0) | 6320 (0.938) | 6089 (0.926) | 1178 (0.302) | 4523 (0.0) | 246 (0.969) | 566 (0.969) | 34080 (0.999) |
| assoc=1 | 720 (0.0) | 7778 (0.858) | 6089 (0.926) | 1178 (0.302) | 4523 (0.0) | 246 (0.969) | 566 (0.969) | 34080 (0.999) |
| assoc=4 | 720 (0.0) | 9313 (0.776) | 6089 (0.926) | 1178 (0.302) | 4523 (0.0) | 246 (0.969) | 566 (0.969) | 34080 (0.999) |
| line=16 | 1247 (0.0) | 10323 (0.825) | 7400 (0.911) | 1910 (0.215) | 5003 (0.0) | 267 (0.969) | 569 (0.969) | 34152 (0.998) |
| line=64 | 492 (0.462) | 6762 (0.915) | 5465 (0.952) | 830 (0.612) | 4295 (0.5) | 246 (0.969) | 364 (0.969) | 34040 (0.999) |
| policy=fifo | 720 (0.0) | 7298 (0.885) | 6089 (0.926) | 1178 (0.302) | 4523 (0.0) | 246 (0.969) | 566 (0.969) | 34080 (0.999) |
| policy=random | 720 (0.0) | 7659 (0.865) | 6089 (0.926) | 1178 (0.302) | 4523 (0.0) | 246 (0.969) | 566 (0.969) | 34080 (0.999) |
| miss_lat=10 | 470 (0.0) | 6362 (0.856) | 5449 (0.926) | 808 (0.302) | 2363 (0.0) | 236 (0.969) | 486 (0.969) | 34018 (0.999) |
| miss_lat=40 | 1240 (0.0) | 10763 (0.856) | 7369 (0.926) | 1918 (0.302) | 8843 (0.0) | 266 (0.969) | 726 (0.969) | 34180 (0.999) |

## RTL cross-check: 20/20 benchmark runs re-simulated on RTL were cycle- and state-identical.

_Sweep runtime 23.6 s on Linux-6.18.44-fc-v50-x86_64-with-glibc2.39._
