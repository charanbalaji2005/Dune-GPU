"""Fault-injection programs. Warp 1 executes a faulting sequence at label
``fault_here``; every other warp writes a marker (77 + tid) and exits normally,
proving that a fault is contained to its warp. Programs are generated per
configuration because some limits (stack depth, imem/scratchpad size) vary."""
from __future__ import annotations

PROLOGUE = """        s2r   r1, warp_id
        seqi  r2, r1, 1
        bany  r2, bad
        s2r   r3, global_tid
        addi  r4, r3, 77
        slli  r3, r3, 2
        sw    r4, 0(r3)
        exit
bad:
"""

FAULT_CODES = {"ILLEGAL": 1, "MISALIGNED": 2, "OOB": 3, "PC_RANGE": 4, "STACK_OVF": 5, "STACK_UNF": 6,
               "EXIT_DIVERGENT": 7, "BRANCH_DEPTH": 8}


def fault_programs(cfg: dict) -> dict[str, tuple[str, int, str]]:
    """name -> (source, expected fault code, label whose address is the fault pc, or 'abs:N')."""
    spm_bytes = cfg["spm_words"] * 4
    depth = cfg["stack_depth"]
    nest_open = "".join("        if    r5\n" for _ in range(depth))
    nest_close = "".join("        endif\n" for _ in range(depth + 1))
    p = {
        "illegal_opcode": ("fault_here:\n        .word 0xFC000000   ; opcode 0x3F unassigned\n        exit\n", "ILLEGAL"),
        "illegal_reserved_alu": ("fault_here:\n        .word 0x1C000000   ; R-type ALU op 7 is reserved\n        exit\n", "ILLEGAL"),
        "illegal_nonzero_pad": ("fault_here:\n        .word 0x00000001   ; ADD with non-zero bits[10:0]\n        exit\n", "ILLEGAL"),
        "misaligned_load": ("        movi  r5, 6\nfault_here:\n        lw    r6, 0(r5)\n        exit\n", "MISALIGNED"),
        "misaligned_store": ("        s2r   r5, lane_id\n        slli  r5, r5, 2\n        ori   r5, r5, 1\n"
                             "fault_here:\n        sw    r5, 0(r5)\n        exit\n", "MISALIGNED"),
        "oob_load": (f"        li    r5, {spm_bytes}\nfault_here:\n        lw    r6, 0(r5)\n        exit\n", "OOB"),
        "oob_atomic": (f"        li    r5, {spm_bytes - 4}\n        s2r   r6, lane_id\n        slli  r6, r6, 2\n"
                       "        add   r5, r5, r6\nfault_here:\n        atom.add r7, r5, r6\n        exit\n", "OOB"),
        "pc_range": (f"fault_here:\n        jmp   {cfg['imem_depth'] + 3}\n        exit\n", "PC_RANGE"),
        "stack_overflow": ("        movi  r5, 1\n" + nest_open + "fault_here:\n        if    r5\n" + nest_close + "        exit\n",
                           "STACK_OVF"),
        "stack_underflow": ("fault_here:\n        .word 0xC8000000   ; ENDIF with an empty mask stack\n        exit\n", "STACK_UNF"),
        "else_underflow": ("fault_here:\n        .word 0xC4000000   ; ELSE with an empty mask stack\n        exit\n", "STACK_UNF"),
        "branch_depth": ("        s2r   r5, lane_id\nloop:   if    r5\nfault_here:\n"
                         "          .word 0xA445FFFF   ; bany r5, loop with depth field 1+1 (raw, bypasses tfxasm check)\n"
                         "        endif\n        exit\n", "BRANCH_DEPTH"),
        "exit_divergent": ("        s2r   r5, lane_id\n        andi  r5, r5, 1\n        if    r5\nfault_here:\n"
                           "          exit\n        endif\n        exit\n", "EXIT_DIVERGENT"),
    }
    out = {}
    for name, (body, code) in p.items():
        out[name] = (PROLOGUE + body, FAULT_CODES[code])
    return out


def expected_fault_pc(name: str, cfg: dict, symbols: dict) -> int:
    if name == "pc_range":
        return cfg["imem_depth"] + 3
    return symbols["fault_here"]
