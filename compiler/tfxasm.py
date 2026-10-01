#!/usr/bin/env python3
"""TFX-ISA v1.0 assembler and disassembler.

Opcodes and formats are read from architecture/isa/tfx_isa_v1.json, the single
source of truth shared with the C++ reference model and the RTL decoder tests.

Syntax
    ; comment   # comment   // comment
    label:
    .equ NAME expr
    add r1, r2, r3          addi r1, r2, -4        lui r1, 0x1234
    lw r1, 8(r2)            sw r1, 8(r2)           atom.add r1, r2, r3   ; r1=[r2]; [r2]+=r3
    s2r r1, global_tid      movi r1, -3            mov r1, r2
    jmp label  bany r1, label  bnone r1, label
    if r1 [, label]   else [label]   endif        ; targets auto-matched when omitted
    bar   exit
    .word expr              ; raw 32-bit word (e.g. deliberately illegal encodings)
Pseudo-instructions
    nop            -> add r0, r0, r0
    li rd, imm32   -> movi | lui | lui+addi
    not rd, rs     -> xori rd, rs, -1
    neg rd, rs     -> sub rd, r0, rs
    subi/... are real instructions (see ISA JSON)
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
ISA = json.loads((ROOT / "architecture/isa/tfx_isa_v1.json").read_text())
INSTR = {i["mnemonic"]: i for i in ISA["instructions"]}
BY_OPCODE = {i["opcode"]: i for i in ISA["instructions"]}
SREGS = ISA["special_registers"]


class AsmError(Exception):
    def __init__(self, line_no: int, msg: str):
        super().__init__(f"line {line_no}: {msg}")
        self.line_no = line_no


def parse_reg(tok: str, ln: int) -> int:
    t = tok.strip().lower()
    if t == "zero":
        return 0
    m = re.fullmatch(r"r(\d+)", t)
    if not m or int(m.group(1)) > 31:
        raise AsmError(ln, f"bad register '{tok}'")
    return int(m.group(1))


_EXPR_OK = re.compile(r"^[\w\s+\-*()<>&|^~]+$")


def eval_expr(expr: str, syms: dict, ln: int, allow_undef=False):
    e = expr.strip()
    if not e or not _EXPR_OK.match(e):
        raise AsmError(ln, f"bad expression '{expr}'")
    names = set(re.findall(r"\b[A-Za-z_]\w*", e))
    env = {}
    for n in names:
        if n in syms:
            env[n] = syms[n]
        elif allow_undef:
            return None
        else:
            raise AsmError(ln, f"undefined symbol '{n}'")
    try:
        return int(eval(e, {"__builtins__": {}}, env))  # restricted: names/ints/operators only
    except Exception as exc:  # noqa: BLE001
        raise AsmError(ln, f"cannot evaluate '{expr}': {exc}") from None


def split_operands(s: str) -> list[str]:
    return [p.strip() for p in s.split(",")] if s.strip() else []


def enc(opc: int, rd=0, rs1=0, rs2=0, imm=None) -> int:
    w = (opc & 63) << 26 | (rd & 31) << 21 | (rs1 & 31) << 16
    if imm is None:
        w |= (rs2 & 31) << 11
    else:
        w |= imm & 0xFFFF
    return w


def check_s16(v: int, ln: int) -> int:
    if not -32768 <= v <= 32767:
        raise AsmError(ln, f"immediate {v} does not fit signed 16 bits")
    return v


def li_parts(v: int):
    v &= 0xFFFFFFFF
    sv = v - (1 << 32) if v & 0x80000000 else v
    if -32768 <= sv <= 32767:
        return [("movi", sv)]
    lo = v & 0xFFFF
    lo_s = lo - 0x10000 if lo & 0x8000 else lo
    hi = ((v - lo_s) >> 16) & 0xFFFF
    return [("lui", hi)] + ([("addi", lo_s)] if lo_s else [])


def _clean(line: str) -> str:
    for c in (";", "#", "//"):
        i = line.find(c)
        if i >= 0:
            line = line[:i]
    return line.strip()


def assemble(text: str):
    """Returns (words, listing, symbols). listing: list of (pc, word, source_line)."""
    lines = text.splitlines()
    syms: dict[str, int] = {}
    items = []  # (ln, pc, mnemonic, operands_str, size, src)
    pc = 0
    # ---- pass 1: labels, sizes, .equ
    for ln, raw in enumerate(lines, 1):
        line = _clean(raw)
        while True:
            m = re.match(r"^([A-Za-z_]\w*)\s*:(.*)$", line)
            if not m:
                break
            name = m.group(1)
            if name in syms:
                raise AsmError(ln, f"duplicate symbol '{name}'")
            syms[name] = pc
            line = m.group(2).strip()
        if not line:
            continue
        parts = line.split(None, 1)
        mn = parts[0].lower()
        ops = parts[1] if len(parts) > 1 else ""
        if mn == ".equ":
            o = ops.split(None, 1)
            if len(o) != 2:
                raise AsmError(ln, ".equ NAME expr")
            syms[o[0]] = eval_expr(o[1], syms, ln)
            continue
        size = 1
        if mn == "li":
            o = split_operands(ops)
            if len(o) != 2:
                raise AsmError(ln, "li rd, imm32")
            v = eval_expr(o[1], syms, ln, allow_undef=True)
            size = len(li_parts(v)) if v is not None else 2
        elif mn not in INSTR and mn not in ("nop", "not", "neg", ".word"):
            raise AsmError(ln, f"unknown mnemonic '{mn}'")
        items.append([ln, pc, mn, ops, size, raw.rstrip()])
        pc += size

    # ---- structured control-flow matching for if/else/endif without explicit targets,
    # plus the structured region of every instruction (TFX-ISA v1.1 branch rule).
    # Region = tuple of (if_pc, side) for the enclosing if/else bodies. An IF belongs to
    # the outer region; ELSE ends the then-side; ENDIF ends the innermost body.
    auto_target = {}
    stack = []
    region_of = {}
    for it in items:
        ln, ipc, mn, ops, size = it[0], it[1], it[2], it[3], it[4]
        here = tuple((s_[0], "else" if s_[1] is not None else "then") for s_ in stack)
        for k in range(size):
            region_of[ipc + k] = here
        if mn == "if":
            stack.append([ipc, None])
        elif mn == "else":
            if not stack:
                raise AsmError(ln, "else without if")
            if stack[-1][1] is not None:
                raise AsmError(ln, "duplicate else")
            auto_target[stack[-1][0]] = ipc  # if jumps to else
            stack[-1][1] = ipc
        elif mn == "endif":
            if not stack:
                raise AsmError(ln, "endif without if")
            ifpc, elsepc = stack.pop()
            if elsepc is None:
                auto_target[ifpc] = ipc
            else:
                auto_target[elsepc] = ipc
    if stack:
        raise AsmError(items[-1][0] if items else 0, "unterminated if")

    def check_branch(ln, ipc, target):
        """jmp/bany/bnone must stay inside their structured region (same if/else body)."""
        src_r = region_of[ipc]
        dst_r = region_of.get(target, ())
        if dst_r != src_r:
            raise AsmError(ln, f"structured branch rule violated: branch at pc {ipc} (region depth "
                               f"{len(src_r)}) targets pc {target} in a different if/else region; "
                               "branches may not enter or leave an if/else body")
        return len(src_r) + 1   # encoded depth field (0 is reserved for 'unchecked')

    # ---- pass 2: encode
    words, listing = [], []
    for ln, ipc, mn, ops, size, src in items:
        o = split_operands(ops)
        out = []

        def need(n):
            if len(o) != n:
                raise AsmError(ln, f"'{mn}' expects {n} operand(s), got {len(o)}")

        def rel(expr):
            return check_s16(eval_expr(expr, syms, ln) - ipc, ln)

        if mn == "nop":
            need(0); out = [enc(0)]
        elif mn == ".word":   # raw 32-bit word (used by fault tests to emit illegal encodings)
            need(1); out = [eval_expr(o[0], syms, ln) & 0xFFFFFFFF]
        elif mn == "li":
            need(2)
            rd = parse_reg(o[0], ln)
            v = eval_expr(o[1], syms, ln)
            parts = li_parts(v)
            if len(parts) > size:
                raise AsmError(ln, "li value changed size between passes")
            if len(parts) < size:  # forward reference: two words were reserved in pass 1
                lo = v & 0xFFFF
                lo_s = lo - 0x10000 if lo & 0x8000 else lo
                parts = [("lui", (((v & 0xFFFFFFFF) - lo_s) >> 16) & 0xFFFF), ("addi", lo_s)]
            for kind, val in parts:
                if kind == "movi":
                    out.append(enc(INSTR["movi"]["opcode"], rd, 0, imm=val))
                elif kind == "lui":
                    out.append(enc(INSTR["lui"]["opcode"], rd, 0, imm=val))
                else:
                    out.append(enc(INSTR["addi"]["opcode"], rd, rd, imm=val))
        elif mn == "not":
            need(2); out = [enc(INSTR["xori"]["opcode"], parse_reg(o[0], ln), parse_reg(o[1], ln), imm=-1)]
        elif mn == "neg":
            need(2); out = [enc(INSTR["sub"]["opcode"], parse_reg(o[0], ln), 0, parse_reg(o[1], ln))]
        else:
            spec = INSTR[mn]
            fmt, opc = spec["format"], spec["opcode"]
            if fmt == "R":
                need(3); out = [enc(opc, parse_reg(o[0], ln), parse_reg(o[1], ln), parse_reg(o[2], ln))]
            elif fmt == "R1":
                need(2); out = [enc(opc, parse_reg(o[0], ln), parse_reg(o[1], ln), 0)]
            elif fmt == "I":
                need(3); out = [enc(opc, parse_reg(o[0], ln), parse_reg(o[1], ln), imm=check_s16(eval_expr(o[2], syms, ln), ln))]
            elif fmt == "I1":
                need(2); out = [enc(opc, parse_reg(o[0], ln), 0, imm=check_s16(eval_expr(o[1], syms, ln), ln))]
            elif fmt == "U":
                need(2)
                v = eval_expr(o[1], syms, ln)
                if not 0 <= v <= 0xFFFF:
                    raise AsmError(ln, "lui immediate must be 0..0xFFFF")
                out = [enc(opc, parse_reg(o[0], ln), 0, imm=v)]
            elif fmt in ("M", "MS"):
                need(2)
                m = re.fullmatch(r"(.*)\(\s*(\w+)\s*\)", o[1].strip())
                if not m:
                    raise AsmError(ln, "memory operand must be imm(reg)")
                off = check_s16(eval_expr(m.group(1), syms, ln), ln) if m.group(1).strip() else 0
                out = [enc(opc, parse_reg(o[0], ln), parse_reg(m.group(2), ln), imm=off)]
            elif fmt == "A":
                need(3); out = [enc(opc, parse_reg(o[0], ln), parse_reg(o[1], ln), parse_reg(o[2], ln))]
            elif fmt == "S":
                need(2)
                key = o[1].strip().lower()
                idx = SREGS[key] if key in SREGS else eval_expr(o[1], syms, ln)
                if idx not in SREGS.values():
                    raise AsmError(ln, f"unknown special register '{o[1]}'")
                out = [enc(opc, parse_reg(o[0], ln), 0, imm=idx)]
            elif fmt == "B":   # bany/bnone/if: reg, target
                if mn == "if" and len(o) == 1:
                    out = [enc(opc, 0, parse_reg(o[0], ln), imm=check_s16(auto_target[ipc] - ipc, ln))]
                else:
                    need(2)
                    depth = check_branch(ln, ipc, eval_expr(o[1], syms, ln)) if mn != "if" else 0
                    out = [enc(opc, depth, parse_reg(o[0], ln), imm=rel(o[1]))]
            elif fmt == "B0":  # jmp/else: target
                if mn == "else" and not o:
                    out = [enc(opc, imm=check_s16(auto_target[ipc] - ipc, ln))]
                else:
                    need(1)
                    depth = check_branch(ln, ipc, eval_expr(o[0], syms, ln)) if mn == "jmp" else 0
                    out = [enc(opc, depth, imm=rel(o[0]))]
            elif fmt == "N":
                need(0); out = [enc(opc)]
            else:
                raise AsmError(ln, f"internal: unknown format {fmt}")
        if len(out) != size:
            raise AsmError(ln, f"internal size mismatch for {mn}")
        for k, w in enumerate(out):
            words.append(w & 0xFFFFFFFF)
            listing.append((ipc + k, w & 0xFFFFFFFF, src if k == 0 else ""))
    return words, listing, syms


REV_SREG = {v: k for k, v in SREGS.items()}


def disassemble(w: int, pc: int | None = None) -> str:
    opc, rd, rs1, rs2, imm = w >> 26, (w >> 21) & 31, (w >> 16) & 31, (w >> 11) & 31, w & 0xFFFF
    simm = imm - 0x10000 if imm & 0x8000 else imm
    spec = BY_OPCODE.get(opc)
    if spec is None:
        return f".word 0x{w:08x}  ; illegal"
    mn, fmt = spec["mnemonic"], spec["format"]
    tgt = lambda: (f"{(pc + simm) & 0xFFFF}" if pc is not None else f"{simm:+d}")  # noqa: E731
    if fmt in ("R", "R1", "A") and (w & 0x7FF):
        return f".word 0x{w:08x}  ; illegal (reserved bits)"
    if w == 0:
        return "nop"
    return {
        "R": lambda: f"{mn} r{rd}, r{rs1}, r{rs2}",
        "R1": lambda: f"{mn} r{rd}, r{rs1}",
        "I": lambda: f"{mn} r{rd}, r{rs1}, {simm}",
        "I1": lambda: f"{mn} r{rd}, {simm}",
        "U": lambda: f"{mn} r{rd}, 0x{imm:x}",
        "M": lambda: f"{mn} r{rd}, {simm}(r{rs1})",
        "MS": lambda: f"{mn} r{rd}, {simm}(r{rs1})",
        "A": lambda: f"{mn} r{rd}, r{rs1}, r{rs2}",
        "S": lambda: f"{mn} r{rd}, {REV_SREG.get(imm, imm)}" if imm in REV_SREG else f".word 0x{w:08x}  ; illegal sreg",
        "B": lambda: f"{mn} r{rs1}, {tgt()}",
        "B0": lambda: f"{mn} {tgt()}",
        "N": lambda: mn,
    }[fmt]()


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="TFX-ISA v1.0 assembler")
    ap.add_argument("source")
    ap.add_argument("-o", "--output", help="hex output (one word per line)")
    ap.add_argument("-l", "--listing", help="listing output")
    ap.add_argument("-d", "--disassemble", action="store_true", help="treat source as hex and disassemble")
    a = ap.parse_args(argv)
    text = Path(a.source).read_text()
    if a.disassemble:
        for pc, tok in enumerate(t for t in text.split() if t):
            w = int(tok, 16)
            print(f"{pc:5d}: {w:08x}  {disassemble(w, pc)}")
        return 0
    try:
        words, listing, _ = assemble(text)
    except AsmError as e:
        print(f"{a.source}: {e}", file=sys.stderr)
        return 1
    hexs = "".join(f"{w:08x}\n" for w in words)
    if a.output:
        Path(a.output).write_text(hexs)
    else:
        sys.stdout.write(hexs)
    if a.listing:
        Path(a.listing).write_text("".join(f"{pc:5d}: {w:08x}  {disassemble(w, pc):28s} {src}\n" for pc, w, src in listing))
    return 0


if __name__ == "__main__":
    sys.exit(main())
