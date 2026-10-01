# ADR 0005: Structured-branch rule, static plus dynamic
Status: accepted (milestone 3)

**Context.** `jmp`/`bany`/`bnone` could leave or enter `if`/`else` bodies. This
corrupted the mask stack: it leaked entries and could silently skip else-bodies.
The regression tests written before the fix reproduced it. With the old encoding,
a backward `bany` out of an `if` overflowed the stack after `stack_depth`
iterations.

**Decision.**
- The assembler rejects any branch whose target is in a different structured region.
- The unused `rd` field of the three branch instructions carries the expected
  stack depth + 1, and the hardware faults with `BRANCH_DEPTH` when it doesn't match.
- A field value of 0 means unchecked, for v1.0 binary compatibility.

**Alternatives rejected.**
- Hardware reconvergence (post-dominator stack) is much more RTL and modelling work.
- Popping the stack on branch exit is wrong for lanes that were masked off.

**Consequences.**
- The interface change is a 4-bit fault code in `READ_FAULT`; codes 1-7 are unchanged.
- The check is depth-based, so then- and else-bodies at the same depth are
  distinguished only by the static rule.
