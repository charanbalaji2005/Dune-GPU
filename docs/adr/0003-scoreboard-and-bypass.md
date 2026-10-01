# ADR 0003: Per-warp register scoreboard with writeback bypass, one LSU op in flight
Status: accepted

**Decision.** One pending-write bit per (warp, register); issue checks RAW on both
sources and WAW on the destination, and treats registers being written back in the
same cycle as already available. Only one LSU operation is in flight cluster-wide.

**Why.** Allows ALU → dependent ALU back-to-back issue, hides MUL latency through
other warps (measured: dep_chain IPC 0.67 with 1 warp → 0.99 with 4 warps at
MUL latency 4) and keeps memory ordering trivial.

**Cost.** Memory-bound kernels serialize on the LSU (structural stalls dominate
histogram and conflicted-stride runs). A multi-entry LSU queue is the next step.
