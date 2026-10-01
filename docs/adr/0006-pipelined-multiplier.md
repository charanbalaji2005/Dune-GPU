# ADR 0006: Genuinely pipelined multiplier with unchanged latency
Status: accepted (milestone 3)

**Context.** Milestone 2 computed a full combinational 32×32 product, plus the
signed high word, in the dispatch cycle and then only delayed it.

**Decision.** `tfx_mul_pipe` splits the work into two stages:
- Stage A: 16×16 partial products and the sign correction.
- Stage B: a 3-level carry-save tree and one 64-bit adder.

Issue-to-writeback latency stays at `MUL_LAT`, so the cycle model, every
benchmark and every trace are unchanged. The ALU gained `HAS_MUL` (default 1),
so its interface and the milestone-1 tests are preserved.

**Evidence** (Yosys generic cells, unit delay, one lane:
`results/synthesis/mul_stage_analysis.txt`):

| Block | Depth |
|---|---:|
| Stage A | 34 |
| Stage B (carry-save) | 28 |
| Monolithic 32×32 alone | 44 |
| Previous ALU with multiplier | 53 |

A first version of stage B used three chained adders and was 46 deep, deeper than
the monolithic multiplier. That version was measured, rejected and replaced.

**Not achieved.** The tiny-cluster critical path moved only from 70 to 69, so the
multiplier was not the cluster's longest path. Stages beyond two are pure delay,
because generic Yosys does not retime them.
