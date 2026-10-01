# ADR 0001: Fixed 32-bit encoding with a shared 4-bit ALU op field
Status: accepted (milestone 2)

**Context.** Milestone 1 had a 7-op ALU with a 4-bit opcode. Milestone 2 needed
immediates, memory, special registers and SIMT control without breaking it.

**Decision.** 6-bit major opcode; R-type 0x00-0x0F and I-type 0x10-0x1F reuse the
same low 4 bits as the ALU op, so the decoder passes `opcode[3:0]` to the ALU unchanged
and milestone-1 op numbers keep their meaning. Unused low bits of register formats
must be zero (illegal otherwise) to keep encoding space for later extensions.

**Consequences.** Simple decoder (518 generic cells), 16-bit immediates only (`li` uses
LUI+ADDI), 21 free major opcodes for future FP/texture/etc.
