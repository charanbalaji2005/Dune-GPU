# TFX-ISA v1.1

The machine-readable specification is `architecture/isa/tfx_isa_v1.json`. The
assembler (`compiler/tfxasm.py`) reads it directly, `tests/test_isa.py` checks the
C++ decoder against it, and the RTL decoder is differentially tested against the
C++ decoder (`sim/tb_decoder_diff.sv`, 20,256 vectors). This page is generated
from the JSON plus hand-written notes. v1.1 (milestone 3) adds the structured-branch
depth field and fault 8; every v1.0 binary still runs unchanged.

## Encoding

All instructions are 32 bits:

```
 31      26 25   21 20   16 15   11 10            0
+----------+-------+-------+-------+---------------+
|  opcode  |  rd   |  rs1  |  rs2  |   must be 0   |   R / R1 / A formats
+----------+-------+-------+-------+---------------+
|  opcode  |  rd   |  rs1  |        imm16          |   I / I1 / U / M / MS / S / B / B0 formats
+----------+-------+-------+-----------------------+
```

* 32 general registers per thread, `r0` reads as zero and ignores writes.
* R-type opcodes `0x00-0x0F` and I-type `0x10-0x1F` share one 4-bit ALU op code
  (`opcode & 0xF`): 0 ADD, 1 SUB, 2 MUL, 3 AND, 4 OR, 5 XOR, 6 MOV, 7 reserved
  (illegal), 8 SLL, 9 SRL, A SRA, B SLT, C SLTU, D SEQ, E MULHU, F MULH. Opcode
  `0x16` is `movi` (I1 format, `rd = sext(imm)`), `0x17` is reserved.
* The ALU op encoding is backward compatible with milestone 1 (ops 0-6 unchanged).
* PC is 16 bits, counted in instruction words. Branch targets are `pc + sext(imm16)`.
* `jmp`/`bany`/`bnone` use `rd` (bits 25:21) as the structured-branch depth field (v1.1, see below).
* Memory addresses are byte addresses into the scratchpad and must be 4-byte aligned.
* Shift amounts use the low 5 bits; MUL/MULH/MULHU and their immediate forms run on
  the multiplier pipeline; everything else in the ALU class completes in one cycle.

## Special registers (S2R)

`lane_id`=0, `warp_id`=1, `global_tid`=2, `num_lanes`=3, `num_warps`=4, `arg0`=8, `arg1`=9, `arg2`=10, `arg3`=11, `arg4`=12, `arg5`=13, `arg6`=14, `arg7`=15. `global_tid = warp_id * num_lanes + lane_id`; `num_warps` is the number of
warps *launched*, not the hardware maximum. `arg0-7` are written by the host.

## Instructions (43 legal opcodes)

| Mnemonic | Opcode | Format | Class | Semantics |
|---|---:|---|---|---|
| `add` | 0x00 | R | alu | rd = rs1 + rs2 (mod 2^32) |
| `sub` | 0x01 | R | alu | rd = rs1 - rs2 (mod 2^32) |
| `mul` | 0x02 | R | mul | rd = low32(rs1 * rs2) |
| `and` | 0x03 | R | alu | rd = rs1 & rs2 |
| `or` | 0x04 | R | alu | rd = rs1 \| rs2 |
| `xor` | 0x05 | R | alu | rd = rs1 ^ rs2 |
| `mov` | 0x06 | R1 | alu | rd = rs1 |
| `sll` | 0x08 | R | alu | rd = rs1 << rs2[4:0] |
| `srl` | 0x09 | R | alu | rd = rs1 >> rs2[4:0] (logical) |
| `sra` | 0x0A | R | alu | rd = rs1 >>> rs2[4:0] (arithmetic) |
| `slt` | 0x0B | R | alu | rd = (signed rs1 < signed rs2) ? 1 : 0 |
| `sltu` | 0x0C | R | alu | rd = (rs1 < rs2) ? 1 : 0 (unsigned) |
| `seq` | 0x0D | R | alu | rd = (rs1 == rs2) ? 1 : 0 |
| `mulhu` | 0x0E | R | mul | rd = high32(unsigned rs1 * unsigned rs2) |
| `mulh` | 0x0F | R | mul | rd = high32(signed rs1 * signed rs2) |
| `addi` | 0x10 | I | alu | rd = rs1 + sext(imm16) |
| `subi` | 0x11 | I | alu | rd = rs1 - sext(imm16) |
| `muli` | 0x12 | I | mul | rd = low32(rs1 * sext(imm16)) |
| `andi` | 0x13 | I | alu | rd = rs1 & sext(imm16) |
| `ori` | 0x14 | I | alu | rd = rs1 \| sext(imm16) |
| `xori` | 0x15 | I | alu | rd = rs1 ^ sext(imm16) |
| `movi` | 0x16 | I1 | alu | rd = sext(imm16) |
| `slli` | 0x18 | I | alu | rd = rs1 << imm16[4:0] |
| `srli` | 0x19 | I | alu | rd = rs1 >> imm16[4:0] (logical) |
| `srai` | 0x1A | I | alu | rd = rs1 >>> imm16[4:0] (arithmetic) |
| `slti` | 0x1B | I | alu | rd = (signed rs1 < sext(imm16)) ? 1 : 0 |
| `sltui` | 0x1C | I | alu | rd = (rs1 < sext(imm16)) ? 1 : 0 (unsigned compare) |
| `seqi` | 0x1D | I | alu | rd = (rs1 == sext(imm16)) ? 1 : 0 |
| `mulhui` | 0x1E | I | mul | rd = high32(unsigned rs1 * unsigned sext(imm16)) |
| `mulhi` | 0x1F | I | mul | rd = high32(signed rs1 * signed sext(imm16)) |
| `lui` | 0x20 | U | alu | rd = imm16 << 16 |
| `lw` | 0x21 | M | lsu | rd = SPM[rs1 + sext(imm16)] (byte address, must be 4-byte aligned) |
| `sw` | 0x22 | MS | lsu | SPM[rs1 + sext(imm16)] = R[rd field]; lanes applied in ascending order (highest lane wins on same address) |
| `atom.add` | 0x23 | A | lsu | rd = SPM[rs1]; SPM[rs1] += rs2; lanes serialized in ascending lane order |
| `s2r` | 0x24 | S | alu | rd = special_register[imm16] |
| `jmp` | 0x28 | B0 | ctrl | pc = pc + sext(imm16); if rd != 0 and stack depth != rd-1 then fault BRANCH_DEPTH |
| `bany` | 0x29 | B | ctrl | if any active lane has rs1 != 0 then pc = pc + sext(imm16) else pc = pc + 1; if rd != 0 and stack depth != rd-1 then fault BRANCH_DEPTH |
| `bnone` | 0x2A | B | ctrl | if no active lane has rs1 != 0 then pc = pc + sext(imm16) else pc = pc + 1; if rd != 0 and stack depth != rd-1 then fault BRANCH_DEPTH |
| `if` | 0x30 | B | ctrl | taken = mask & (rs1 != 0); push(mask, taken); mask = taken; if taken == 0 then pc = pc + sext(imm16) |
| `else` | 0x31 | B0 | ctrl | mask = top.saved & ~top.taken; if mask == 0 then pc = pc + sext(imm16) |
| `endif` | 0x32 | N | ctrl | mask = top.saved; pop |
| `bar` | 0x38 | N | ctrl | after this warp's outstanding ops drain, wait until every live warp waits at a barrier |
| `exit` | 0x39 | N | ctrl | after this warp's outstanding ops drain, terminate the warp; EXIT_DIVERGENT fault if mask stack is non-empty |

## SIMT control flow

`if rs1, off` pushes `{saved=mask, taken=mask & (rs1 != 0)}` on the per-warp mask
stack and continues with `mask = taken`; if no lane is taken it branches to
`pc + off` (normally the matching `else`/`endif`). `else` switches to
`saved & ~taken` (branching over the else-body if empty); `endif` restores `saved`
and pops. The assembler fills in the targets for structured code automatically.
`bany`/`bnone` are warp-uniform branches decided over the *active* lanes, which is
how per-lane loops terminate (`slt / bnone exit / if ... endif / jmp loop`).
`bar` and `exit` are warp-level and first wait for the warp's in-flight
operations to drain. Stack depth is a configuration parameter (default 8).

### Structured-branch rule (v1.1)

A warp-uniform branch that leaves or enters an `if`/`else` body breaks the
mask-stack discipline: jumping out leaves stale entries (later overflow or a
divergent `exit`, with the wrong lanes active in between); jumping from a then-body
past its `else` silently skips the else-body for the lanes that needed it. v1.0
did not detect either. v1.1 closes this in two layers:

* **Static (tfxasm):** every instruction belongs to a region = the chain of
  enclosing `(if, then|else)` bodies (`if` itself is in the outer region, `else`
  ends the then-side, `endif` ends the innermost body). `jmp`/`bany`/`bnone` must
  target an instruction in *the same region*; otherwise assembly fails with
  "structured branch rule violated". Loops inside a body, loops around a whole
  `if/endif`, and jumping to a body's own `else` remain legal.
* **Dynamic (hardware and both C++ models):** the assembler writes `D+1` into the
  otherwise unused `rd` field, where `D` is the static nesting depth. When it
  executes, the branch checks that the warp's mask-stack depth equals `D` and
  otherwise raises fault 8 `BRANCH_DEPTH` (no branch taken). `rd = 0` means
  unchecked, so v1.0 binaries behave exactly as before. The dynamic check catches
  hand-encoded or corrupted control flow; it cannot distinguish then- from
  else-bodies (same depth), which the static rule covers.

## Structured-branch rule (new in v1.1)

**Bug fixed in milestone 3.** In v1.0, `jmp`, `bany` and `bnone` could leave or
enter an `if`/`else` body. The hardware had no way to detect this, and it either
leaked mask-stack entries (eventually `STACK_OVF`, or `EXIT_DIVERGENT`) or
silently skipped the else-body for lanes that were masked off. v1.1 fixes it in
two layers:

1. **Assembler (static).** `tfxasm` assigns every instruction to a structured
   region: the chain of enclosing then/else bodies. `if` belongs to the outer
   region, `else` ends the then-side, and `endif` ends the innermost body. Any
   branch whose target lies in a different region is rejected with
   "structured branch rule violated". Loops inside a body, branches to the
   body's own `else`, and loops around a whole `if … endif` are all legal.
2. **Hardware (dynamic).** The previously unused `rd` field (bits 25:21) of
   `jmp`/`bany`/`bnone` now carries the static depth + 1. The assembler fills it
   in. If the warp's mask-stack depth differs when the branch executes, the warp
   faults with `BRANCH_DEPTH` (8) and the branch is not taken. A field value of 0
   means unchecked, so v1.0 binaries still run unchanged.

The dynamic check compares depth only, so it cannot tell a then-body from an
else-body at the same depth. The static rule covers that case.

## Faults

| Code | Name | Raised when |
|---:|---|---|
| 1 | ILLEGAL | unassigned opcode, reserved ALU op 7, or non-zero bits[10:0] in R/R1/A formats |
| 2 | MISALIGNED | any active lane's address is not 4-byte aligned (checked before OOB) |
| 3 | OOB | any active lane's address is outside the scratchpad |
| 4 | PC_RANGE | the warp fetches from `pc >= imem_depth` |
| 5 | STACK_OVF | `if` with a full mask stack |
| 6 | STACK_UNF | `else`/`endif` with an empty mask stack |
| 7 | EXIT_DIVERGENT | `exit` with a non-empty mask stack |
| 8 | BRANCH_DEPTH | (v1.1) `jmp`/`bany`/`bnone` with a non-zero depth field executed at a different mask-stack depth |
| 8 | BRANCH_DEPTH | `jmp`/`bany`/`bnone` with depth field `D+1` executed at mask-stack depth ≠ `D` (v1.1) |

A faulting warp stops; its code and PC are readable by the host (`READ_FAULT`,
code in bits [19:16] since v1.1; codes 1-7 read identically to v1.0).
Other warps continue, and faulted or exited warps never block a barrier. A
faulting memory instruction performs no memory access for any lane.

## Memory ordering

Within a warp, instructions issue in program order and only one LSU operation is
in flight cluster-wide, so a warp always observes its own earlier stores. For
`sw` with several lanes on the same word, the highest lane wins. `atom.add`
serializes lanes in ascending order. Between warps there is no ordering except
`bar`; races between warps are program bugs (the cycle model and RTL agree on
the outcome, but the functional model may legitimately differ).
