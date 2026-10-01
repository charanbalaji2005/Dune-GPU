// Cycle-level model of the tfx_simt_cluster microarchitecture.
//
// It is written as a two-phase (evaluate current state -> commit next state)
// transliteration of rtl/compute/tfx_simt_cluster.sv so that cycle counts,
// performance counters and issue traces can be compared exactly against RTL.
//
// Pipeline (single issue):
//   ISSUE    : scheduler picks one eligible warp; scoreboard set; pc advanced
//   DISPATCH : register read; ALU executes and writes back in this cycle;
//              MUL enters an (mul_latency-1)-stage pipe; LSU op loads the LSU;
//              control ops resolve (pc / mask stack / barrier / exit / fault).
//   LSU      : banked scratchpad, one access per bank per cycle, broadcast for
//              same-word loads, atomics serialized (read cycle + write cycle).
#ifndef TFX_TIMING_HPP
#define TFX_TIMING_HPP
#include "tfx_machine.hpp"
#include "tfx_sched.hpp"
#include "tfx_cache.hpp"
#include <memory>
#include <cstdio>
#include <string>
#include <vector>

namespace tfx {

enum PerfIdx {
    PC_CYCLES = 0, PC_ISSUED, PC_THREAD_INSTRS, PC_ALU, PC_MUL, PC_MEM, PC_CTRL,
    PC_STALL_SB, PC_STALL_STRUCT, PC_STALL_BAR, PC_STALL_OTHER,
    PC_LSU_ROUNDS, PC_LSU_LANE_ACC, PC_DIV_IFS, PC_BAR_RELEASES, PC_LSU_BUSY, PC_COUNT
};
inline const char* perf_name(int i) {
    static const char* n[PC_COUNT] = {"cycles","issued","thread_instrs","alu_instrs","mul_instrs",
        "mem_instrs","ctrl_instrs","stall_scoreboard","stall_structural","stall_barrier",
        "stall_other","lsu_rounds","lsu_lane_accesses","divergent_ifs","barrier_releases","lsu_busy_cycles"};
    return n[i];
}

class TimingModel {
public:
    TimingModel(ArchState& s, int policy, FILE* trace = nullptr) : S(s), policy_(policy), trace_(trace) {
        const int NW = S.cfg.num_warps, L = S.cfg.num_lanes;
        sb_.assign(NW, 0); inflight_.assign(NW, 0); ctrl_wait_.assign(NW, false);
        mul_.assign(S.cfg.mul_latency - 1, MulStage{});
        for (auto& m : mul_) m.data.assign(L, 0);
        lsu_.waddr.assign(L, 0); lsu_.sdata.assign(L, 0); lsu_.ldata.assign(L, 0);
        bank_rdata_.assign(S.cfg.spm_banks, 0);
        lg_banks_ = 0; while ((1 << lg_banks_) < S.cfg.spm_banks) ++lg_banks_;
        last_ = NW - 1;
        running_ = true;
        if (S.cfg.l1_enable) l1_ = std::make_unique<L1Cache>(S.cfg);
    }
    // Non-null when the (simulation-only) L1 data-cache model is enabled.
    const L1Cache* l1() const { return l1_.get(); }
    uint32_t perf[PC_COUNT] = {};
    bool running() const { return running_; }

    // Advance one clock. Returns false once the kernel has completed.
    bool step() {
        if (!running_) return false;
        const int NW = S.cfg.num_warps, L = S.cfg.num_lanes;
        // ---------- writebacks occurring this cycle (scoreboard-clear bypass)
        std::vector<uint32_t> clear(NW, 0);
        Decoded dd{}; bool dd_valid = iss_.v;
        if (iss_.v) dd = iss_.oob ? Decoded{} : decode(iss_.word);
        if (dd_valid && !iss_.oob && dd.cls == CLS_ALU && dd.writes_rd) clear[iss_.w] |= 1u << dd.rd;
        MulStage& mlast = mul_.back();
        if (mlast.v && mlast.wr) clear[mlast.w] |= 1u << mlast.rd;
        const bool lsu_wb = (lsu_.st == LS_DRAIN || lsu_.st == LS_WB);
        if (lsu_wb && lsu_.wr) clear[lsu_.w] |= 1u << lsu_.rd;

        // ---------- issue eligibility
        const bool lsu_free = lsu_.st == LS_IDLE && !(iss_.v && !iss_.oob && dd.cls == CLS_LSU);
        uint32_t elig = 0, is_mem = 0;
        bool any_haz = false, any_struct = false, any_bar = false;
        std::vector<Decoded> dec(NW);
        std::vector<bool> oobv(NW, false);
        for (int w = 0; w < NW; ++w) {
            const WarpState& W = S.warps[w];
            if (W.alive() && W.at_bar) any_bar = true;
            const bool cand = W.alive() && !ctrl_wait_[w] && !W.at_bar;
            if (!cand) continue;
            oobv[w] = W.pc >= S.cfg.imem_depth;
            dec[w] = oobv[w] ? Decoded{} : decode(S.imem[W.pc]);
            const Decoded& d = dec[w];
            const uint32_t sbe = sb_[w] & ~clear[w];
            const bool haz = (d.use_a && (sbe >> d.ra & 1u)) || (d.use_b && (sbe >> d.rb & 1u)) ||
                             (d.writes_rd && (sbe >> d.rd & 1u));
            const bool is_lsu = !oobv[w] && d.cls == CLS_LSU;
            const bool drain = !oobv[w] && d.cls == CLS_CTRL && (d.ctrl_op == CT_BAR || d.ctrl_op == CT_EXIT);
            const bool sok = is_lsu ? lsu_free : (drain ? inflight_[w] == 0 : true);
            if (haz) any_haz = true; else if (!sok) any_struct = true;
            if (!haz && sok) elig |= 1u << w;
            if (is_lsu) is_mem |= 1u << w;
        }
        const int sel = select_warp(policy_, NW, elig, is_mem, last_);

        // ---------- next-state containers
        std::vector<int> inflight_dec(NW, 0);
        struct RegWrite { int w, l, r; uint32_t v; };
        std::vector<RegWrite> rw;
        std::vector<std::pair<uint32_t, uint32_t>> bank_writes;   // (word addr, value)
        std::vector<uint32_t> next_bank_rdata = bank_rdata_;
        IssueReg next_iss{};
        std::vector<MulStage> next_mul = mul_;
        LsuState nl = lsu_;
        std::vector<bool> next_ctrl_wait = ctrl_wait_;
        std::vector<WarpState> next_w = S.warps;

        // ---------- dispatch
        if (iss_.v) {
            const int w = iss_.w;
            std::vector<uint32_t> A(L), B(L);
            for (int l = 0; l < L; ++l) {
                A[l] = dd.a_imm ? dd.imm : dd.is_s2r ? S.sreg(w, l, dd.sreg) : S.reg(w, l, dd.ra);
                B[l] = dd.b_imm ? dd.imm : S.reg(w, l, dd.rb);
            }
            if (iss_.oob || dd.cls == CLS_CTRL) {
                uint32_t cond = 0;
                for (int l = 0; l < L; ++l) if ((iss_.mask >> l & 1u) && A[l] != 0) cond |= 1u << l;
                bool div = false;
                resolve_ctrl(next_w[w], dd, iss_.pc, iss_.mask, cond, S.cfg.stack_depth, iss_.oob, &div);
                if (div) ++perf[PC_DIV_IFS];
                next_ctrl_wait[w] = false;
            } else if (dd.cls == CLS_ALU) {
                for (int l = 0; l < L; ++l)
                    if ((iss_.mask >> l & 1u) && dd.writes_rd) rw.push_back({w, l, dd.rd, execute(dd.alu_op, A[l], B[l]).value});
                inflight_dec[w]++;
            } else if (dd.cls == CLS_MUL) {
                MulStage m; m.v = true; m.w = w; m.rd = dd.rd; m.wr = dd.writes_rd; m.mask = iss_.mask; m.data.assign(L, 0);
                for (int l = 0; l < L; ++l) m.data[l] = execute(dd.alu_op, A[l], B[l]).value;
                next_mul[0] = m;   // overwritten below by shift unless stage 0
            } else {   // LSU
                std::vector<uint32_t> addr(L);
                for (int l = 0; l < L; ++l) addr[l] = A[l] + dd.imm;
                const uint8_t f = mem_fault(addr, iss_.mask, S.cfg.spm_words);
                if (f != F_NONE) {
                    next_w[w].faulted = true; next_w[w].fcode = f; next_w[w].fpc = iss_.pc;
                    inflight_dec[w]++;
                } else {
                    nl.st = dd.mem_op == MEM_ATOM_ADD ? LS_AT_RD : LS_MEM;
                    nl.w = w; nl.rd = dd.rd; nl.wr = dd.writes_rd; nl.op = dd.mem_op;
                    nl.mask = iss_.mask; nl.pend = iss_.mask; nl.cap = 0;
                    for (int l = 0; l < L; ++l) { nl.waddr[l] = addr[l] >> 2; nl.sdata[l] = B[l]; }
                    if (l1_) start_cached(nl);
                }
            }
        }

        // ---------- MUL pipeline (writeback from the last stage, then shift)
        if (mlast.v) {
            for (int l = 0; l < L; ++l)
                if ((mlast.mask >> l & 1u) && mlast.wr) rw.push_back({mlast.w, l, mlast.rd, mlast.data[l]});
            inflight_dec[mlast.w]++;
        }
        {
            MulStage in = (iss_.v && !iss_.oob && dd.cls == CLS_MUL) ? next_mul[0] : MulStage{};
            if (!in.v) in.data.assign(L, 0);
            for (size_t i = mul_.size() - 1; i > 0; --i) next_mul[i] = mul_[i - 1];
            next_mul[0] = in;
        }

        // ---------- LSU
        if (lsu_.st != LS_IDLE) ++perf[PC_LSU_BUSY];
        auto bank_of = [&](uint32_t wa) { return int(wa & uint32_t(S.cfg.spm_banks - 1)); };
        auto capture = [&](LsuState& dst) {
            for (int l = 0; l < L; ++l) if (lsu_.cap >> l & 1u) dst.ldata[l] = bank_rdata_[bank_of(lsu_.waddr[l])];
        };
        switch (lsu_.st) {
            case LS_MEM: {
                capture(nl);
                std::vector<int> chosen(S.cfg.spm_banks, -1);
                for (int l = 0; l < L; ++l)
                    if ((lsu_.pend >> l & 1u) && chosen[bank_of(lsu_.waddr[l])] < 0) chosen[bank_of(lsu_.waddr[l])] = l;
                uint32_t served = 0;
                for (int l = 0; l < L; ++l) {
                    if (!(lsu_.pend >> l & 1u)) continue;
                    const int c = chosen[bank_of(lsu_.waddr[l])];
                    if (c == l || (lsu_.op == MEM_LW && lsu_.waddr[c] == lsu_.waddr[l])) served |= 1u << l;
                }
                for (int b = 0; b < S.cfg.spm_banks; ++b) {
                    if (chosen[b] < 0) continue;
                    const uint32_t wa = lsu_.waddr[chosen[b]];
                    if (lsu_.op == MEM_LW) next_bank_rdata[b] = S.spm[wa];
                    else bank_writes.push_back({wa, lsu_.sdata[chosen[b]]});
                }
                ++perf[PC_LSU_ROUNDS]; perf[PC_LSU_LANE_ACC] += uint32_t(popcount32(served));
                nl.pend = lsu_.pend & ~served;
                nl.cap = lsu_.op == MEM_LW ? served : 0;
                if (nl.pend == 0) {
                    if (lsu_.op == MEM_LW) nl.st = LS_DRAIN;
                    else { nl.st = LS_IDLE; inflight_dec[lsu_.w]++; }
                }
                break;
            }
            case LS_DRAIN: {
                capture(nl);
                for (int l = 0; l < L; ++l)
                    if ((lsu_.mask >> l & 1u) && lsu_.wr) rw.push_back({lsu_.w, l, lsu_.rd, nl.ldata[l]});
                inflight_dec[lsu_.w]++;
                nl.st = LS_IDLE; nl.cap = 0;
                break;
            }
            case LS_AT_RD: {
                int l = 0; while (!(lsu_.pend >> l & 1u)) ++l;
                next_bank_rdata[bank_of(lsu_.waddr[l])] = S.spm[lsu_.waddr[l]];
                nl.at_lane = l; nl.st = LS_AT_WR;
                ++perf[PC_LSU_ROUNDS]; ++perf[PC_LSU_LANE_ACC];
                break;
            }
            case LS_AT_WR: {
                const int l = lsu_.at_lane;
                const uint32_t old = bank_rdata_[bank_of(lsu_.waddr[l])];
                bank_writes.push_back({lsu_.waddr[l], old + lsu_.sdata[l]});
                nl.ldata[l] = old; nl.pend = lsu_.pend & ~(1u << l);
                nl.st = nl.pend ? LS_AT_RD : LS_WB;
                ++perf[PC_LSU_ROUNDS];
                break;
            }
            case LS_CACHE: {   // L1 model: one line (or one atomic lane) transaction at a time
                nl.remaining = lsu_.remaining - 1;
                if (nl.remaining > 0) break;
                const uint32_t m = lsu_.tx_mask[size_t(lsu_.tx_idx)];
                for (int l = 0; l < L; ++l) {
                    if (!(m >> l & 1u)) continue;
                    const uint32_t wa = lsu_.waddr[l];
                    if (lsu_.op == MEM_LW) nl.ldata[l] = S.spm[wa];
                    else if (lsu_.op == MEM_SW) bank_writes.push_back({wa, lsu_.sdata[l]});
                    else { nl.ldata[l] = S.spm[wa]; bank_writes.push_back({wa, S.spm[wa] + lsu_.sdata[l]}); }
                }
                ++perf[PC_LSU_ROUNDS]; perf[PC_LSU_LANE_ACC] += uint32_t(popcount32(m));
                nl.tx_idx = lsu_.tx_idx + 1;
                if (size_t(nl.tx_idx) < lsu_.tx_mask.size()) nl.remaining = lookup(nl, nl.tx_idx);
                else if (lsu_.op == MEM_SW) { nl.st = LS_IDLE; inflight_dec[lsu_.w]++; }
                else nl.st = LS_WB;
                break;
            }
            case LS_WB: {
                for (int l = 0; l < L; ++l)
                    if ((lsu_.mask >> l & 1u) && lsu_.wr) rw.push_back({lsu_.w, l, lsu_.rd, lsu_.ldata[l]});
                inflight_dec[lsu_.w]++;
                nl.st = LS_IDLE;
                break;
            }
            default: break;
        }

        // ---------- barrier release (from current state)
        bool all_wait = true, bar_seen = false;
        for (auto& W : S.warps) { if (W.alive() && !W.at_bar) all_wait = false; if (W.alive() && W.at_bar) bar_seen = true; }
        if (all_wait && bar_seen) { for (auto& W : next_w) W.at_bar = false; ++perf[PC_BAR_RELEASES]; }

        // ---------- issue commit + stall accounting
        ++perf[PC_CYCLES];
        std::vector<uint32_t> set(NW, 0);
        std::vector<int> inflight_inc(NW, 0);
        if (sel >= 0) {
            const Decoded& d = dec[sel];
            next_iss = {true, sel, S.warps[sel].pc, oobv[sel] ? 0u : S.imem[S.warps[sel].pc], S.warps[sel].mask, oobv[sel]};
            ++perf[PC_ISSUED];
            if (oobv[sel] || d.cls == CLS_CTRL) { next_ctrl_wait[sel] = true; ++perf[PC_CTRL]; }
            else {
                next_w[sel].pc = uint16_t(S.warps[sel].pc + 1);
                inflight_inc[sel]++;
                if (d.writes_rd) set[sel] |= 1u << d.rd;
                perf[PC_THREAD_INSTRS] += uint32_t(popcount32(S.warps[sel].mask));
                ++perf[d.cls == CLS_ALU ? PC_ALU : d.cls == CLS_MUL ? PC_MUL : PC_MEM];
            }
            if (trace_) std::fprintf(trace_, "%u issue w=%d pc=%u word=%08x mask=%x elig=%x\n",
                                     perf[PC_CYCLES] - 1, sel, unsigned(S.warps[sel].pc), next_iss.word, next_iss.mask, elig);
            last_ = sel;
        } else {
            const int reason = any_haz ? PC_STALL_SB : any_struct ? PC_STALL_STRUCT : any_bar ? PC_STALL_BAR : PC_STALL_OTHER;
            ++perf[reason];
            if (trace_) std::fprintf(trace_, "%u stall %s\n", perf[PC_CYCLES] - 1, perf_name(reason));
        }

        // ---------- completion detection (from current state)
        bool mul_busy = false; for (auto& m : mul_) mul_busy |= m.v;
        const bool done = S.all_finished() && !iss_.v && !mul_busy && lsu_.st == LS_IDLE;

        // ---------- commit
        for (auto& r : rw) if (r.r != 0) S.reg(r.w, r.l, r.r) = r.v;
        for (auto& bw : bank_writes) S.spm[bw.first] = bw.second;
        bank_rdata_ = next_bank_rdata;
        for (int w = 0; w < NW; ++w) {
            sb_[w] = (sb_[w] & ~clear[w]) | set[w];
            inflight_[w] += inflight_inc[w] - inflight_dec[w];
        }
        S.warps = next_w; ctrl_wait_ = next_ctrl_wait; iss_ = next_iss; mul_ = next_mul; lsu_ = nl;
        if (done) running_ = false;
        return running_;
    }

private:
    enum LsSt { LS_IDLE = 0, LS_MEM = 1, LS_DRAIN = 2, LS_AT_RD = 3, LS_AT_WR = 4, LS_WB = 5, LS_CACHE = 6 };
    struct IssueReg { bool v = false; int w = 0; uint16_t pc = 0; uint32_t word = 0, mask = 0; bool oob = false; };
    struct MulStage { bool v = false; int w = 0; uint8_t rd = 0; bool wr = false; uint32_t mask = 0; std::vector<uint32_t> data; };
    struct LsuState {
        int st = LS_IDLE, w = 0, at_lane = 0; uint8_t rd = 0, op = 0; bool wr = false;
        uint32_t mask = 0, pend = 0, cap = 0;
        std::vector<uint32_t> waddr, sdata, ldata;
        // L1 model: per-transaction lane masks and line addresses, progress
        std::vector<uint32_t> tx_mask, tx_line;
        int tx_idx = 0, remaining = 0;
    };
    // Group active lanes into L1 transactions: one per distinct line for LW/SW
    // (ordered by the lowest lane touching the line), one per lane for ATOM.
    void start_cached(LsuState& nl) {
        nl.st = LS_CACHE; nl.tx_mask.clear(); nl.tx_line.clear(); nl.tx_idx = 0;
        for (int l = 0; l < S.cfg.num_lanes; ++l) {
            if (!(nl.mask >> l & 1u)) continue;
            const uint32_t line = l1_->line_of(nl.waddr[l] * 4u);
            size_t k = 0;
            if (nl.op != MEM_ATOM_ADD) while (k < nl.tx_line.size() && nl.tx_line[k] != line) ++k;
            else k = nl.tx_line.size();
            if (k == nl.tx_line.size()) { nl.tx_line.push_back(line); nl.tx_mask.push_back(0); }
            nl.tx_mask[k] |= 1u << l;
        }
        nl.remaining = lookup(nl, 0);
    }
    int lookup(const LsuState& nl, int i) {
        const uint32_t line = nl.tx_line[size_t(i)];
        return nl.op == MEM_LW ? l1_->load(line) : nl.op == MEM_SW ? l1_->store(line) : l1_->atomic(line);
    }
    std::unique_ptr<L1Cache> l1_;
    ArchState& S;
    int policy_;
    FILE* trace_;
    bool running_ = false;
    int last_ = 0, lg_banks_ = 0;
    std::vector<uint32_t> sb_;
    std::vector<int> inflight_;
    std::vector<bool> ctrl_wait_;
    IssueReg iss_;
    std::vector<MulStage> mul_;
    LsuState lsu_;
    std::vector<uint32_t> bank_rdata_;
};

} // namespace tfx
#endif
