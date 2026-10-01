# Memory system

## Scratchpad

`SPM_WORDS` 32-bit words in `SPM_BANKS` single-ported banks (power of two). Word
address *a* lives in bank `a % SPM_BANKS`, row `a / SPM_BANKS`. Reads are
registered (data the cycle after the request). The host can read/write the
scratchpad only while no kernel runs; the host port shares the bank ports.

Simulation-only zero initialisation (`ifndef SYNTHESIS`) keeps RTL and C++ in
lock-step; real SRAM powers up undefined, so the harness explicitly writes every
input and output word a kernel depends on.

## LSU

One LSU operation is in flight in the whole cluster (structural hazard for other
warps). States: IDLE → MEM → (DRAIN for loads) → IDLE, and IDLE → AT_RD ⇄ AT_WR →
WB → IDLE for atomics.

* **Loads/stores**: every cycle each bank serves the lowest-numbered pending lane
  mapped to it ("round"). A load additionally completes every pending lane that
  reads the *same word* (broadcast). Conflicting lanes wait for later rounds.
* **Atomics** (`atom.add`): lanes are serialized in ascending order, two cycles
  each (read, then write of old + operand), so contended histograms are
  LSU-bound (histogram: 234 structural-stall cycles of 611 on tfx_base).
* **Faults** are detected for all active lanes before any access is made.

## Measured bank behaviour (simulated, stride microbenchmark, 16 loads/lane)

On tfx_base (8 lanes, 8 banks) unit stride and odd strides (3) need one round per
load (8 lanes/round), stride 2 halves throughput, stride 4 quarters it, and
strides 8 and 16 serialize completely (≈1 lane/round). Stride 0 (all lanes read
the same word) is a full-speed broadcast. With a single bank every non-broadcast
pattern serializes (544 rounds vs 68). Table: `results/benchmarks/benchmarks.md` §3.

## L1 data-cache model (milestone 3, C++ cycle simulator only)

`simulator/include/tfx_cache.hpp`, enabled per configuration (`l1_*` fields,
example `architecture/configuration/tfx_base_l1.json`). **There is no RTL
implementation**; the RTL and the default C++ configuration still use the banked
scratchpad, and remain cycle-exact with each other.

| Parameter | Field | Notes |
|---|---|---|
| capacity | `l1_size_bytes` | sets = size / (line x assoc), must be a power of two |
| line size | `l1_line_bytes` | power of two, >= 4 |
| associativity | `l1_assoc` | 1 = direct mapped |
| replacement | `l1_policy` | `lru`, `fifo`, `random` (xorshift32, `l1_seed`) |
| hit latency | `l1_hit_latency` | LSU cycles per line transaction on a hit (>= 1) |
| miss latency | `l1_miss_latency` | LSU cycles per load miss and per atomic lane |

Behaviour:
* In L1 mode the data array is treated as global memory behind the cache. Bank
  conflicts no longer apply; the L1 has one tag port and processes one line
  transaction at a time (blocking, no MSHRs, no hit-under-miss).
* Loads: active lanes are grouped by cache line (ordered by the lowest lane), one
  transaction per line; a miss allocates (fill). Stores: write-through,
  no-write-allocate, posted to an unbounded write buffer (cost = hit latency).
  Atomics: executed at memory per lane (cost = miss latency), invalidating a cached
  copy of the line.
* The model is tag-only. Because stores write through and loads read the backing
  array, the cache cannot change results; `tests/test_l1_cache.py` checks this for
  13 workloads x 6 cache variants plus 60 random programs.
* Hardware counters keep their meaning where possible (lsu_rounds = transactions,
  lsu_lane_accesses = lanes served). L1-specific counters are reported separately
  under `"l1"` in the `tfx_sim --stats` JSON: load hits/misses, store hits/misses,
  fills, evictions, atomic bypasses, atomic invalidations.

## L1 data-cache model (SIMULATION ONLY, new in milestone 3)

`simulator/include/tfx_cache.hpp`, enabled with `"l1_enable": 1` in a
configuration (example: `architecture/configuration/tfx_base_l1.json`). **There is
no RTL for it.** The co-simulation harness refuses to compare such configurations
against the RTL. With the L1 disabled (the default), the C++ cycle model stays
cycle-exact with the RTL.

When enabled, the data memory is treated as global memory behind a single L1:

| Parameter | Values |
|---|---|
| `l1_size_bytes`, `l1_line_bytes`, `l1_assoc` | sets = size / (line × assoc), must be a power of two |
| `l1_policy` | `lru`, `fifo`, `random` (xorshift, seeded by `l1_seed`, reproducible) |
| `l1_hit_latency`, `l1_miss_latency` | LSU cycles per transaction |

**Behaviour:**
- **Tag-only, blocking**, one transaction at a time.
- **Loads:** one transaction per distinct line among the active lanes, ordered by
  the lowest lane touching each line. A load allocates on miss.
- **Stores:** write-through with no write-allocate, posted to the write buffer, so
  a store costs the hit latency whether it hits or misses.
- **Atomics:** each lane goes to memory (miss latency), and any cached copy is
  invalidated.

Because data always comes from the backing array, the cache can change only
timing, never results. The regression checks this for 13 workloads × 6 cache
variants and for 260 random programs.

Bank conflicts do not apply in L1 mode: the L1 is one tag port, one line per cycle.

**Counters** are reported in the stats JSON under `l1`: accesses, load and store
hits and misses, fills, evictions, atomic bypasses and invalidations. These are
separate from the 16 hardware counters. In L1 mode, `lsu_rounds` counts
transactions.

## Not implemented

No RTL cache, no external DRAM model beyond the fixed L1 miss latency, no
non-blocking misses, no coherence (single cluster), no coalescing across warps, no
address translation.
See [known limitations](known-limitations.md).
