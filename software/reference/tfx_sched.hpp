// Warp-scheduler policies (mechanism/policy separation).
// The SV module rtl/scheduler/tfx_warp_scheduler.sv must match select_warp()
// bit-exactly; tb_scheduler_diff checks every input combination for 4 warps.
#ifndef TFX_SCHED_HPP
#define TFX_SCHED_HPP
#include <cstdint>
#include <string>

namespace tfx {
enum Policy : uint8_t { POL_LRR = 0, POL_GTO = 1, POL_PRIO = 2, POL_MEMFIRST = 3 };

inline int policy_from_name(const std::string& s) {
    if (s == "lrr") return POL_LRR;
    if (s == "gto") return POL_GTO;
    if (s == "prio") return POL_PRIO;
    if (s == "memfirst") return POL_MEMFIRST;
    return -1;
}
inline const char* policy_name(int p) {
    switch (p) { case 0: return "lrr"; case 1: return "gto"; case 2: return "prio"; case 3: return "memfirst"; }
    return "?";
}

// eligible/is_mem: bit w set for warp w. last: warp issued most recently.
// Returns selected warp or -1 when nothing is eligible.
inline int rr_from(uint32_t mask, int nw, int last) {
    for (int i = 1; i <= nw; ++i) { int w = (last + i) % nw; if (mask >> w & 1u) return w; }
    return -1;
}
inline int lowest(uint32_t mask, int nw) {
    for (int w = 0; w < nw; ++w) if (mask >> w & 1u) return w;
    return -1;
}
inline int select_warp(int policy, int nw, uint32_t eligible, uint32_t is_mem, int last) {
    if (!eligible) return -1;
    switch (policy) {
        case POL_GTO:  return (eligible >> last & 1u) ? last : lowest(eligible, nw);
        case POL_PRIO: return lowest(eligible, nw);
        case POL_MEMFIRST: {
            uint32_t m = eligible & is_mem;
            return m ? rr_from(m, nw, last) : rr_from(eligible, nw, last);
        }
        default: return rr_from(eligible, nw, last);
    }
}
} // namespace tfx
#endif
