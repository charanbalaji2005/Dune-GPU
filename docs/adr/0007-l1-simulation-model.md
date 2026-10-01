# ADR 0007: L1 data cache as a simulation-only, tag-only model
Status: accepted (milestone 3)

**Decision.**
- Add the L1 to the C++ cycle simulator only, disabled by default.
- Make it tag-only and write-through, so it cannot affect architectural results.
- Make it blocking, with the hit/miss latencies, replacement policy and
  geometry configurable.

**Why.** The task was to finish correctness before expanding the hardware, while
still enabling memory-hierarchy exploration. A disabled L1 is bit-identical to the
milestone-2 model, which is checked by a test, so RTL equivalence is untouched.

**Consequences.**
- L1 results are design-space estimates of a hypothetical memory system. They are
  not properties of the RTL.
- An RTL L1 would need a non-blocking design (outstanding-miss tracking) and its own
  co-simulation before any of these numbers could be claimed for hardware.
