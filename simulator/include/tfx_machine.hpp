// Architectural state shared by the functional and timing models, plus the
// functional SIMT interpreter (the correctness oracle for TFX-ISA v1.0).
#ifndef TFX_MACHINE_HPP
#define TFX_MACHINE_HPP
#include "tfx_config.hpp"
#include "tfx_isa.hpp"
#include <array>
#include <cstdint>
#include <stdexcept>
#include <vector>

namespace tfx {

inline int popcount32(uint32_t x) { int n = 0; while (x) { x &= x - 1; ++n; } return n; }

struct StackEntry { uint32_t saved = 0, taken = 0; };

struct WarpState {
    uint16_t pc = 0;
    uint32_t mask = 0;
    std::vector<StackEntry> stack;   // size == sp
    bool launched = false, exited = false, faulted = false, at_bar = false;
    uint8_t fcode = F_NONE;
    uint16_t fpc = 0;
    bool alive() const { return launched && !exited && !faulted; }
};

// Architectural state visible to the host after a kernel completes.
struct ArchState {
    Config cfg;
    int launch_warps = 0;
    std::vector<uint32_t> imem, spm, regs;   // regs[(w*L + l)*32 + r]
    std::array<uint32_t, 8> args{};
    std::vector<WarpState> warps;

    explicit ArchState(const Config& c) : cfg(c), imem(c.imem_depth, 0), spm(c.spm_words, 0),
        regs(size_t(c.num_warps) * c.num_lanes * 32, 0), warps(c.num_warps) {}

    uint32_t& reg(int w, int l, int r) { return regs[(size_t(w) * cfg.num_lanes + l) * 32 + r]; }
    uint32_t full_mask() const { return cfg.num_lanes == 32 ? 0xFFFFFFFFu : ((1u << cfg.num_lanes) - 1); }

    // Launch: every resident warp slot below n starts at pc 0 with all lanes active.
    // All registers are zeroed (defined, deterministic start state).
    void launch(int n) {
        if (n < 1 || n > cfg.num_warps) throw std::runtime_error("launch: bad warp count");
        launch_warps = n;
        std::fill(regs.begin(), regs.end(), 0u);
        for (int w = 0; w < cfg.num_warps; ++w) {
            warps[w] = WarpState{};
            warps[w].launched = (w < n);
            warps[w].mask = full_mask();
        }
    }
    uint32_t sreg(int w, int l, uint8_t idx) const {
        switch (idx) {
            case SR_LANE: return uint32_t(l);
            case SR_WARP: return uint32_t(w);
            case SR_GTID: return uint32_t(w * cfg.num_lanes + l);
            case SR_NLANES: return uint32_t(cfg.num_lanes);
            case SR_NWARPS: return uint32_t(launch_warps);
            default: return (idx >= 8 && idx <= 15) ? args[idx - 8] : 0u;
        }
    }
    bool all_finished() const {
        for (auto& w : warps) if (w.alive()) return false;
        return true;
    }
};

// Shared control-flow resolution, used verbatim by both models (and mirrored in RTL).
// cond_any: some lane in the warp's issue mask has rs1 != 0. cond_mask: those lanes.
inline void resolve_ctrl(WarpState& W, const Decoded& d, uint16_t pc, uint32_t issue_mask,
                         uint32_t cond_mask, int stack_depth, bool pc_oob, bool* divergent_if) {
    auto fault = [&](uint8_t code) { W.faulted = true; W.fcode = code; W.fpc = pc; };
    const uint16_t target = uint16_t(pc + d.imm);
    const uint16_t next = uint16_t(pc + 1);
    if (divergent_if) *divergent_if = false;
    if (pc_oob) { fault(F_PC_RANGE); return; }
    // Structured-branch check (TFX-ISA v1.1): a non-zero depth field asserts the
    // mask-stack depth at which this branch was assembled.
    const bool is_branch = d.ctrl_op == CT_JMP || d.ctrl_op == CT_BANY || d.ctrl_op == CT_BNONE;
    if (is_branch && d.bdepth != 0 && W.stack.size() != size_t(d.bdepth - 1)) { fault(F_BRANCH_DEPTH); return; }
    switch (d.ctrl_op) {
        case CT_JMP:   W.pc = target; break;
        case CT_BANY:  W.pc = cond_mask ? target : next; break;
        case CT_BNONE: W.pc = cond_mask ? next : target; break;
        case CT_IF: {
            if (int(W.stack.size()) >= stack_depth) { fault(F_STACK_OVF); return; }
            const uint32_t taken = issue_mask & cond_mask;
            W.stack.push_back({issue_mask, taken});
            W.mask = taken;
            W.pc = taken ? next : target;
            if (divergent_if) *divergent_if = (taken != 0 && taken != issue_mask);
            break;
        }
        case CT_ELSE: {
            if (W.stack.empty()) { fault(F_STACK_UNF); return; }
            const uint32_t m = W.stack.back().saved & ~W.stack.back().taken;
            W.mask = m;
            W.pc = m ? next : target;
            break;
        }
        case CT_ENDIF:
            if (W.stack.empty()) { fault(F_STACK_UNF); return; }
            W.mask = W.stack.back().saved; W.stack.pop_back(); W.pc = next; break;
        case CT_BAR:  W.at_bar = true; W.pc = next; break;
        case CT_EXIT:
            if (!W.stack.empty()) { fault(F_EXIT_DIVERGENT); return; }
            W.exited = true; break;
        default: fault(F_ILLEGAL); return;
    }
}

// Memory-fault check over active lanes: misalignment has priority over bounds.
inline uint8_t mem_fault(const std::vector<uint32_t>& byte_addr, uint32_t mask, int spm_words) {
    bool mis = false, oob = false;
    for (size_t l = 0; l < byte_addr.size(); ++l) {
        if (!(mask >> l & 1u)) continue;
        if (byte_addr[l] & 3u) mis = true;
        else if (byte_addr[l] >= uint32_t(spm_words) * 4u) oob = true;
    }
    return mis ? F_MISALIGNED : (oob ? F_OOB : F_NONE);
}

// ---------------------------------------------------------------- functional model
// Executes one whole warp-instruction at a time, warps interleaved round-robin.
// Defines *what* a program computes; it makes no timing claims.
class FunctionalModel {
public:
    explicit FunctionalModel(ArchState& s) : S(s) {}
    uint64_t instructions = 0, thread_instructions = 0;

    // Returns false on step-limit timeout.
    bool run(uint64_t max_steps) {
        const int NW = S.cfg.num_warps;
        while (!S.all_finished()) {
            bool progressed = false;
            for (int w = 0; w < NW; ++w) {
                if (!S.warps[w].alive() || S.warps[w].at_bar) continue;
                step(w); progressed = true;
                if (++instructions > max_steps) return false;
            }
            if (!progressed) {           // every live warp is waiting at a barrier
                for (auto& W : S.warps) W.at_bar = false;
            }
        }
        return true;
    }

private:
    ArchState& S;
    void step(int w) {
        WarpState& W = S.warps[w];
        const int L = S.cfg.num_lanes;
        const bool oob = W.pc >= S.cfg.imem_depth;
        const Decoded d = oob ? Decoded{} : decode(S.imem[W.pc]);
        const uint16_t pc = W.pc;
        const uint32_t mask = W.mask;
        std::vector<uint32_t> A(L), B(L);
        for (int l = 0; l < L; ++l) {
            A[l] = d.a_imm ? d.imm : d.is_s2r ? S.sreg(w, l, d.sreg) : S.reg(w, l, d.ra);
            B[l] = d.b_imm ? d.imm : S.reg(w, l, d.rb);
        }
        if (oob || d.cls == CLS_CTRL) {
            uint32_t cond = 0;
            for (int l = 0; l < L; ++l) if ((mask >> l & 1u) && A[l] != 0) cond |= 1u << l;
            resolve_ctrl(W, d, pc, mask, cond, S.cfg.stack_depth, oob, nullptr);
            return;
        }
        thread_instructions += uint64_t(popcount32(mask));
        W.pc = uint16_t(pc + 1);
        if (d.cls == CLS_ALU || d.cls == CLS_MUL) {
            for (int l = 0; l < L; ++l)
                if ((mask >> l & 1u) && d.writes_rd) S.reg(w, l, d.rd) = execute(d.alu_op, A[l], B[l]).value;
            return;
        }
        // LSU
        std::vector<uint32_t> addr(L);
        for (int l = 0; l < L; ++l) addr[l] = A[l] + d.imm;
        const uint8_t f = mem_fault(addr, mask, S.cfg.spm_words);
        if (f != F_NONE) { W.faulted = true; W.fcode = f; W.fpc = pc; return; }
        for (int l = 0; l < L; ++l) {
            if (!(mask >> l & 1u)) continue;
            uint32_t& m = S.spm[addr[l] >> 2];
            if (d.mem_op == MEM_LW) { if (d.writes_rd) S.reg(w, l, d.rd) = m; }
            else if (d.mem_op == MEM_SW) { m = B[l]; }
            else { const uint32_t old = m; m = old + B[l]; if (d.writes_rd) S.reg(w, l, d.rd) = old; }
        }
    }
};

} // namespace tfx
#endif
