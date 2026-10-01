# ADR 0007: L1 data cache as a simulation-only, results-preserving model
Status: accepted (milestone 3)

**Decision.** The L1 exists only in the C++ cycle simulator, disabled by default. It is
tag-only, blocking, write-through/no-write-allocate, atomics at memory with invalidation.

**Why.** Milestone 3 had to finish correctness before expanding hardware. A tag-only
write-through model cannot change architectural results, which is testable against the
functional model. Leaving it off by default keeps the C++ model cycle-exact with RTL.
The harness refuses to co-simulate an L1 configuration against RTL rather than
silently comparing different machines.

**Consequences.** L1 benchmark numbers describe a hypothetical memory system and must
not be presented as RTL or hardware results. An RTL cache, with non-blocking misses and
a real backing-memory model, is future work and would need the same exact co-simulation.
