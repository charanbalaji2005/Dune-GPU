# ADR 0002: Explicit structured divergence (IF/ELSE/ENDIF mask stack)
Status: accepted

**Decision.** Divergence is expressed with explicit `if/else/endif` instructions that
push/pop `{saved, taken}` masks, plus warp-uniform `bany/bnone` branches decided over
active lanes. No hardware reconvergence detection (no immediate post-dominator stack).

**Why.** Deterministic, easy to model identically in C++ and RTL, and easy for an
assembler/compiler to emit for structured code. Stack depth is a parameter and
overflow/underflow/divergent-exit are architectural faults instead of silent corruption.

**Cost.** Unstructured control flow needs compiler restructuring; every IF is a control
instruction (one-cycle bubble for that warp).
