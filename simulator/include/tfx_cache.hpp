// L1 data-cache model for the TFX-X cycle simulator (simulation only).
//
// Tag-only, set-associative, blocking (no MSHRs), write-through with
// no-write-allocate. Because every store is written through and data is always
// read from the backing array, the cache can never change architectural
// results; it changes only timing. This is checked in the regression by
// comparing the cached timing model against the functional model.
#ifndef TFX_CACHE_HPP
#define TFX_CACHE_HPP
#include "tfx_config.hpp"
#include <cstdint>
#include <string>
#include <vector>

namespace tfx {

struct L1Stats {
    uint64_t load_hits = 0, load_misses = 0, store_hits = 0, store_misses = 0;
    uint64_t evictions = 0, atomic_bypasses = 0, atomic_invalidations = 0, fills = 0;
    uint64_t accesses() const { return load_hits + load_misses + store_hits + store_misses; }
};

class L1Cache {
public:
    explicit L1Cache(const Config& c)
        : line_(uint32_t(c.l1_line_bytes)), ways_(c.l1_assoc),
          sets_(c.l1_size_bytes / (c.l1_line_bytes * c.l1_assoc)), policy_(c.l1_policy),
          rng_(uint32_t(c.l1_seed) * 2654435761u + 1u), hit_lat_(c.l1_hit_latency), miss_lat_(c.l1_miss_latency) {
        lines_.assign(size_t(sets_) * size_t(ways_), Line{});
    }
    uint32_t line_of(uint32_t byte_addr) const { return byte_addr / line_; }

    // Load lookup: allocates on miss. Returns the transaction latency.
    int load(uint32_t line_addr) {
        if (Line* l = find(line_addr)) { touch(*l); ++st.load_hits; return hit_lat_; }
        ++st.load_misses; ++st.fills; allocate(line_addr); return miss_lat_;
    }
    // Store lookup (write-through, no-write-allocate). Stores are posted to a
    // write buffer, so both hits and misses cost the hit latency.
    int store(uint32_t line_addr) {
        if (Line* l = find(line_addr)) { touch(*l); ++st.store_hits; } else ++st.store_misses;
        return hit_lat_;
    }
    // Atomics execute at the memory side: bypass, invalidate any cached copy.
    int atomic(uint32_t line_addr) {
        ++st.atomic_bypasses;
        if (Line* l = find(line_addr)) { l->valid = false; ++st.atomic_invalidations; }
        return miss_lat_;
    }
    L1Stats st;
    int sets() const { return sets_; }

private:
    struct Line { bool valid = false; uint32_t tag = 0; uint64_t used = 0, filled = 0; };
    Line* find(uint32_t line_addr) {
        const int set = int(line_addr & uint32_t(sets_ - 1));
        const uint32_t tag = line_addr / uint32_t(sets_);
        for (int w = 0; w < ways_; ++w) {
            Line& l = lines_[size_t(set) * size_t(ways_) + size_t(w)];
            if (l.valid && l.tag == tag) return &l;
        }
        return nullptr;
    }
    void touch(Line& l) { l.used = ++clock_; }
    void allocate(uint32_t line_addr) {
        const int set = int(line_addr & uint32_t(sets_ - 1));
        Line* base = &lines_[size_t(set) * size_t(ways_)];
        int victim = -1;
        for (int w = 0; w < ways_ && victim < 0; ++w) if (!base[w].valid) victim = w;
        if (victim < 0) {
            ++st.evictions;
            if (policy_ == "random") {
                rng_ ^= rng_ << 13; rng_ ^= rng_ >> 17; rng_ ^= rng_ << 5;   // xorshift32
                victim = int(rng_ % uint32_t(ways_));
            } else {
                victim = 0;
                for (int w = 1; w < ways_; ++w) {
                    const uint64_t a = policy_ == "fifo" ? base[w].filled : base[w].used;
                    const uint64_t b = policy_ == "fifo" ? base[victim].filled : base[victim].used;
                    if (a < b) victim = w;
                }
            }
        }
        Line& l = base[victim];
        l.valid = true; l.tag = line_addr / uint32_t(sets_); l.used = ++clock_; l.filled = clock_;
    }
    uint32_t line_;
    int ways_, sets_;
    std::string policy_;
    uint32_t rng_;
    int hit_lat_, miss_lat_;
    uint64_t clock_ = 0;
    std::vector<Line> lines_;
};

} // namespace tfx
#endif
