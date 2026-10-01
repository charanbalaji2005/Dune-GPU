// TFX-ISA v1.0 reference semantics. This header is the correctness oracle.
// Machine-readable spec: architecture/isa/tfx_isa_v1.json (kept in sync by tests).
#ifndef TFX_ISA_HPP
#define TFX_ISA_HPP
#include <cstdint>
#include <cstdio>
#include <string>

namespace tfx {

// ---------------------------------------------------------------- ALU ops
// 4-bit ALU operation codes. 0x0-0x6 are unchanged from ISA v0.1; 0x7 stays
// reserved (illegal) so v0.1 behaviour and tests remain valid.
enum class Opcode : uint8_t {
    ADD = 0x0, SUB = 0x1, MUL = 0x2, AND = 0x3, OR = 0x4, XOR = 0x5, MOV = 0x6,
    SLL = 0x8, SRL = 0x9, SRA = 0xA, SLT = 0xB, SLTU = 0xC, SEQ = 0xD,
    MULHU = 0xE, MULH = 0xF
};
struct Instruction { Opcode opcode; uint32_t a; uint32_t b; };  // v0.1 compatibility
struct Result { uint32_t value; bool zero; bool illegal; };

inline Result execute(uint8_t opcode, uint32_t a, uint32_t b) {
    uint32_t v = 0; bool illegal = false;
    const int64_t sa = static_cast<int32_t>(a), sb = static_cast<int32_t>(b);
    switch (opcode) {
        case 0x0: v = a + b; break;
        case 0x1: v = a - b; break;
        case 0x2: v = static_cast<uint32_t>(static_cast<uint64_t>(a) * b); break;
        case 0x3: v = a & b; break;
        case 0x4: v = a | b; break;
        case 0x5: v = a ^ b; break;
        case 0x6: v = a; break;
        case 0x8: v = a << (b & 31u); break;
        case 0x9: v = a >> (b & 31u); break;
        case 0xA: v = static_cast<uint32_t>(static_cast<int32_t>(a) >> (b & 31u)); break;
        case 0xB: v = (sa < sb) ? 1u : 0u; break;
        case 0xC: v = (a < b) ? 1u : 0u; break;
        case 0xD: v = (a == b) ? 1u : 0u; break;
        case 0xE: v = static_cast<uint32_t>((static_cast<uint64_t>(a) * b) >> 32); break;
        case 0xF: v = static_cast<uint32_t>(static_cast<uint64_t>(sa * sb) >> 32); break;
        default:  v = 0; illegal = true; break;
    }
    return {v, v == 0, illegal};
}

inline const char* opcode_name(uint8_t op) {
    static const char* n[16] = {"ADD","SUB","MUL","AND","OR","XOR","MOV","ILLEGAL",
                                "SLL","SRL","SRA","SLT","SLTU","SEQ","MULHU","MULH"};
    return op < 16 ? n[op] : "ILLEGAL";
}
inline bool is_mul_op(uint8_t alu_op) { return alu_op == 0x2 || alu_op == 0xE || alu_op == 0xF; }

// ---------------------------------------------------------------- encoding
namespace op {
constexpr uint8_t R_BASE = 0x00, I_BASE = 0x10, LUI = 0x20, LW = 0x21, SW = 0x22,
    ATOM_ADD = 0x23, S2R = 0x24, JMP = 0x28, BANY = 0x29, BNONE = 0x2A,
    IF = 0x30, ELSE = 0x31, ENDIF = 0x32, BAR = 0x38, EXIT = 0x39;
}
enum Cls : uint8_t { CLS_ALU = 0, CLS_MUL = 1, CLS_LSU = 2, CLS_CTRL = 3 };
enum MemOp : uint8_t { MEM_NONE = 0, MEM_LW = 1, MEM_SW = 2, MEM_ATOM_ADD = 3 };
enum CtrlOp : uint8_t { CT_NONE = 0, CT_JMP = 1, CT_BANY = 2, CT_BNONE = 3, CT_IF = 4,
    CT_ELSE = 5, CT_ENDIF = 6, CT_BAR = 7, CT_EXIT = 8, CT_ILLEGAL = 9 };
enum Fault : uint8_t { F_NONE = 0, F_ILLEGAL = 1, F_MISALIGNED = 2, F_OOB = 3,
    F_PC_RANGE = 4, F_STACK_OVF = 5, F_STACK_UNF = 6, F_EXIT_DIVERGENT = 7,
    F_BRANCH_DEPTH = 8 };
enum SReg : uint8_t { SR_LANE = 0, SR_WARP = 1, SR_GTID = 2, SR_NLANES = 3, SR_NWARPS = 4,
    SR_ARG0 = 8 };

inline uint32_t enc_r(uint8_t opc, unsigned rd, unsigned rs1, unsigned rs2) {
    return (uint32_t(opc & 63) << 26) | ((rd & 31u) << 21) | ((rs1 & 31u) << 16) | ((rs2 & 31u) << 11);
}
inline uint32_t enc_i(uint8_t opc, unsigned rd, unsigned rs1, int32_t imm) {
    return (uint32_t(opc & 63) << 26) | ((rd & 31u) << 21) | ((rs1 & 31u) << 16) | (uint32_t(imm) & 0xFFFFu);
}

// Canonical decoded form. The SystemVerilog decoder (rtl/core/tfx_decoder.sv)
// must produce bit-identical fields; tb_decoder_diff checks this.
struct Decoded {
    bool legal = false;
    uint8_t cls = CLS_CTRL, alu_op = 0, mem_op = MEM_NONE, ctrl_op = CT_ILLEGAL;
    uint8_t rd = 0, ra = 0, rb = 0;       // ra/rb: read-port addresses (0 when unused)
    bool use_a = false, use_b = false, writes_rd = false;
    bool a_imm = false, b_imm = false, is_s2r = false;
    uint8_t sreg = 0;
    uint32_t imm = 0;                      // final immediate (sext, or <<16 for LUI); 0 if none
    uint8_t bdepth = 0;                    // jmp/bany/bnone: raw rd field (0 = unchecked, D+1 = expected stack depth D)
};

inline bool sreg_legal(uint32_t idx) { return idx <= 4 || (idx >= 8 && idx <= 15); }

inline Decoded decode(uint32_t w) {
    Decoded d;
    const uint8_t opc = uint8_t(w >> 26);
    const uint8_t rd = (w >> 21) & 31, rs1 = (w >> 16) & 31, rs2 = (w >> 11) & 31;
    const uint32_t imm16 = w & 0xFFFFu;
    const uint32_t sext = (imm16 & 0x8000u) ? (imm16 | 0xFFFF0000u) : imm16;
    const bool low11_zero = (w & 0x7FFu) == 0;
    auto illegal = [&]() { Decoded x; return x; };
    auto set_rd = [&](uint8_t r) { d.rd = r; d.writes_rd = (r != 0); };

    if (opc < 0x20) {                       // R (0x00-0x0F) and I (0x10-0x1F) ALU forms
        const uint8_t aop = opc & 15;
        const bool is_i = opc >= 0x10;
        if (aop == 7) return illegal();
        if (!is_i && !low11_zero) return illegal();
        d.legal = true; d.alu_op = aop; d.cls = is_mul_op(aop) ? CLS_MUL : CLS_ALU;
        d.ctrl_op = CT_NONE; set_rd(rd);
        if (is_i) {
            d.imm = sext;
            if (aop == 6) { d.a_imm = true; }                      // movi
            else { d.use_a = true; d.ra = rs1; d.b_imm = true; }
        } else {
            d.use_a = true; d.ra = rs1;
            if (aop != 6) { d.use_b = true; d.rb = rs2; }           // mov ignores rs2
        }
        return d;
    }
    switch (opc) {
        case op::LUI:
            d.legal = true; d.cls = CLS_ALU; d.alu_op = 6; d.ctrl_op = CT_NONE;
            set_rd(rd); d.a_imm = true; d.imm = imm16 << 16; return d;
        case op::LW:
            d.legal = true; d.cls = CLS_LSU; d.mem_op = MEM_LW; d.ctrl_op = CT_NONE;
            set_rd(rd); d.use_a = true; d.ra = rs1; d.imm = sext; return d;
        case op::SW:
            d.legal = true; d.cls = CLS_LSU; d.mem_op = MEM_SW; d.ctrl_op = CT_NONE;
            d.use_a = true; d.ra = rs1; d.use_b = true; d.rb = rd; d.imm = sext; return d;
        case op::ATOM_ADD:
            if (!low11_zero) return illegal();
            d.legal = true; d.cls = CLS_LSU; d.mem_op = MEM_ATOM_ADD; d.ctrl_op = CT_NONE;
            set_rd(rd); d.use_a = true; d.ra = rs1; d.use_b = true; d.rb = rs2; return d;
        case op::S2R:
            if (!sreg_legal(imm16)) return illegal();
            d.legal = true; d.cls = CLS_ALU; d.alu_op = 6; d.ctrl_op = CT_NONE;
            set_rd(rd); d.is_s2r = true; d.sreg = uint8_t(imm16); return d;
        case op::JMP:   d.legal = true; d.ctrl_op = CT_JMP; d.imm = sext; d.bdepth = uint8_t(rd); return d;
        case op::BANY:  d.legal = true; d.ctrl_op = CT_BANY; d.use_a = true; d.ra = rs1; d.imm = sext; d.bdepth = uint8_t(rd); return d;
        case op::BNONE: d.legal = true; d.ctrl_op = CT_BNONE; d.use_a = true; d.ra = rs1; d.imm = sext; d.bdepth = uint8_t(rd); return d;
        case op::IF:    d.legal = true; d.ctrl_op = CT_IF; d.use_a = true; d.ra = rs1; d.imm = sext; return d;
        case op::ELSE:  d.legal = true; d.ctrl_op = CT_ELSE; d.imm = sext; return d;
        case op::ENDIF: d.legal = true; d.ctrl_op = CT_ENDIF; return d;
        case op::BAR:   d.legal = true; d.ctrl_op = CT_BAR; return d;
        case op::EXIT:  d.legal = true; d.ctrl_op = CT_EXIT; return d;
        default: return illegal();
    }
}

// One-line canonical text form shared by C++ vector generator and SV testbench.
// Field order: legal cls alu_op mem_op ctrl_op rd ra rb use_a use_b writes_rd a_imm b_imm is_s2r sreg imm
inline std::string decoded_fields_hex(const Decoded& d) {
    char buf[160];
    std::snprintf(buf, sizeof buf, "%x %x %x %x %x %02x %02x %02x %x %x %x %x %x %x %x %08x",
        d.legal, d.cls, d.alu_op, d.mem_op, d.ctrl_op, d.rd, d.ra, d.rb, d.use_a, d.use_b,
        d.writes_rd, d.a_imm, d.b_imm, d.is_s2r, d.sreg, d.imm);
    return buf;
}

} // namespace tfx
#endif
