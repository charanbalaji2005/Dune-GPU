# ADR 0004: Cycle-exact C++ model as the RTL's reference
Status: accepted

**Decision.** `simulator/include/tfx_timing.hpp` is a two-phase (evaluate, commit)
transliteration of the RTL microarchitecture, and the regression demands identical
cycle counts, counters and per-cycle issue traces, in addition to the
architectural comparison against a separate functional model and Python golden code.

**Why.** Trace equality localises a bug to the first divergent cycle, and it makes
the fast C++ model trustworthy for benchmarks (~1000x faster than Icarus here).

**Cost.** Every micro-architectural change must be made twice; the C++ model is not an
independent specification of *timing*, only of the implementation intent.
