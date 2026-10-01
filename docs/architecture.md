# CharanGPU TFX-X architecture (milestone 3)

```
 host ── 64-bit cmd (ready/valid) ──► tfx_fifo ──► tfx_command_processor ──► tfx_token_channel ──► 64-bit resp
                                                         │ imem / spm / args / launch / debug reads
                                                         ▼
 ┌──────────────────────────── tfx_simt_cluster (NW warps × NL lanes) ────────────────────────────┐
 │ imem ─► per-warp fetch+decode ─► eligibility (scoreboard, structural, barrier) ─► tfx_warp_scheduler │
 │                                                                     │ one warp / cycle            │
 │                                                       ISSUE register (pc, word, mask)            │
 │                                                                     ▼                             │
 │  DISPATCH: decode ─ register read (NL lane register files) ─┬─ ALU ×NL ── writeback (same cycle) │
 │                                                             ├─ MUL pipe (MUL_LAT-1 stages) ── wb │
 │                                                             ├─ tfx_lsu ⇄ tfx_scratchpad (banks) │
 │                                                             └─ control: pc / mask stack / bar / exit │
 └──────────────────────────────────────────────────────────────────────────────────────────────────┘
```

| Block | File | Role |
|---|---|---|
| ALU | `rtl/core/tfx_alu.sv` | 16 integer ops incl. MULH/MULHU, flags kept from milestone 1 |
| Multiplier | `rtl/core/tfx_mul_pipe.sv` | genuinely pipelined MUL/MULHU/MULH for all lanes (partial products, then carry-save + final add) |
| Decoder | `rtl/core/tfx_decoder.sv` | TFX-ISA v1.0 decode, legality, operand/immediate selection |
| Scheduler | `rtl/scheduler/tfx_warp_scheduler.sv` | LRR / GTO / PRIO / MEMFIRST policy over an eligibility mask |
| Cluster | `rtl/compute/tfx_simt_cluster.sv` | warps, scoreboard, issue/dispatch, mask stacks, barriers, faults, 16 perf counters |
| LSU | `rtl/memory/tfx_lsu.sv` | per-bank arbitration, load broadcast, serialized atomics |
| Scratchpad | `rtl/memory/tfx_scratchpad.sv` | `SPM_BANKS` single-port banks, registered read |
| Command processor | `rtl/command_processor/tfx_command_processor.sv` | host command decode, response generation with backpressure |
| Top | `rtl/top/charangpu_tfx_top.sv` | FIFO → CP → cluster, CP → token channel |
| Milestone-1 blocks | `tfx_register_file.sv`, `tfx_compute_lane.sv`, `tfx_fifo.sv`, `tfx_token_channel.sv` | kept and still tested; FIFO and token channel are reused by the top level |

All sizes are parameters, and the same values drive the C++ simulator through a
JSON configuration (`architecture/configuration/*.json`, schema in
`architecture/schemas/`). Four configurations are shipped and verified:
`tfx_small` (2×4, 2 banks), `tfx_base` (4×8, 8 banks), `tfx_onebank` (4×8, 1 bank)
and `tfx_wide` (8×16, 16 banks).

The C++ cycle simulator additionally offers a simulation-only L1 data-cache model
(see [memory system](memory-system.md)); it has no RTL counterpart.

Further reading: [ISA](isa.md), [execution model](execution-model.md),
[scheduler](scheduler.md), [memory system](memory-system.md),
[host interface](host-interface.md), [verification](verification.md),
[benchmarks](benchmark-methodology.md), [limitations](known-limitations.md),
[ASIC roadmap](asic-roadmap.md), [design decisions](adr/).
