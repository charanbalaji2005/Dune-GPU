// Cluster configuration + a minimal flat-JSON reader (objects of numbers/strings).
// Full schema validation lives in tools/configuration/validate_config.py.
#ifndef TFX_CONFIG_HPP
#define TFX_CONFIG_HPP
#include <cctype>
#include <fstream>
#include <map>
#include <sstream>
#include <stdexcept>
#include <string>

namespace tfx {

struct Config {
    std::string name = "default";
    int num_warps = 4;      // resident warp slots (hardware)
    int num_lanes = 8;      // SIMT width
    int imem_depth = 256;   // instruction words
    int spm_words = 2048;   // scratchpad words (32-bit)
    int spm_banks = 8;      // power of two
    int mul_latency = 3;    // cycles from dispatch to register write (>= 2)
    int stack_depth = 8;    // divergence (mask) stack entries per warp
    std::string sched_policy = "lrr";
    // ---- L1 data-cache model (C++ cycle simulator only; there is no RTL for it).
    // Disabled by default: the timing model is then cycle-exact with the RTL.
    int l1_enable = 0;          // 1: data memory is "global memory" behind a blocking L1
    int l1_size_bytes = 1024;   // capacity
    int l1_line_bytes = 32;     // line size (power of two, >= 4)
    int l1_assoc = 2;           // ways per set
    std::string l1_policy = "lru";  // lru | fifo | random
    int l1_hit_latency = 1;     // LSU cycles per line transaction on a hit
    int l1_miss_latency = 20;   // LSU cycles per line transaction on a load miss / atomic
    int l1_seed = 1;            // random-replacement seed (deterministic)
};

inline std::map<std::string, std::string> parse_flat_json(const std::string& text) {
    std::map<std::string, std::string> kv;
    size_t i = 0;
    auto ws = [&]() { while (i < text.size() && std::isspace(static_cast<unsigned char>(text[i]))) ++i; };
    auto str = [&]() {
        if (text[i] != '"') throw std::runtime_error("config: expected string");
        std::string s; ++i;
        while (i < text.size() && text[i] != '"') { if (text[i] == '\\') ++i; s += text[i++]; }
        ++i; return s;
    };
    ws(); if (i >= text.size() || text[i] != '{') throw std::runtime_error("config: expected '{'");
    ++i;
    for (;;) {
        ws(); if (text[i] == '}') break;
        std::string k = str(); ws();
        if (text[i] != ':') throw std::runtime_error("config: expected ':'");
        ++i; ws();
        std::string v;
        if (text[i] == '"') v = str();
        else { while (i < text.size() && text[i] != ',' && text[i] != '}' && !std::isspace(static_cast<unsigned char>(text[i]))) v += text[i++]; }
        kv[k] = v; ws();
        if (text[i] == ',') { ++i; continue; }
        if (text[i] == '}') break;
        throw std::runtime_error("config: malformed object");
    }
    return kv;
}

inline Config load_config(const std::string& path) {
    std::ifstream f(path);
    if (!f) throw std::runtime_error("cannot open config " + path);
    std::stringstream ss; ss << f.rdbuf();
    auto kv = parse_flat_json(ss.str());
    Config c;
    auto geti = [&](const char* k, int& dst) { auto it = kv.find(k); if (it != kv.end()) dst = std::stoi(it->second); };
    if (kv.count("name")) c.name = kv["name"];
    if (kv.count("sched_policy")) c.sched_policy = kv["sched_policy"];
    geti("num_warps", c.num_warps); geti("num_lanes", c.num_lanes); geti("imem_depth", c.imem_depth);
    geti("spm_words", c.spm_words); geti("spm_banks", c.spm_banks); geti("mul_latency", c.mul_latency);
    geti("stack_depth", c.stack_depth);
    geti("l1_enable", c.l1_enable); geti("l1_size_bytes", c.l1_size_bytes); geti("l1_line_bytes", c.l1_line_bytes);
    geti("l1_assoc", c.l1_assoc); geti("l1_hit_latency", c.l1_hit_latency); geti("l1_miss_latency", c.l1_miss_latency);
    geti("l1_seed", c.l1_seed);
    if (kv.count("l1_policy")) c.l1_policy = kv["l1_policy"];
    auto pow2 = [](int x) { return x > 0 && (x & (x - 1)) == 0; };
    if (c.num_warps < 1 || c.num_warps > 8) throw std::runtime_error("num_warps must be 1..8");
    if (c.num_lanes < 1 || c.num_lanes > 32) throw std::runtime_error("num_lanes must be 1..32");
    if (!pow2(c.spm_banks) || c.spm_banks > 32) throw std::runtime_error("spm_banks must be a power of two <= 32");
    if (!pow2(c.spm_words) || c.spm_words < c.spm_banks) throw std::runtime_error("spm_words must be a power of two >= banks");
    if (!pow2(c.imem_depth) || c.imem_depth > 65536) throw std::runtime_error("imem_depth must be a power of two <= 65536");
    if (c.mul_latency < 2 || c.mul_latency > 8) throw std::runtime_error("mul_latency must be 2..8");
    if (c.stack_depth < 1 || c.stack_depth > 16) throw std::runtime_error("stack_depth must be 1..16");
    if (c.l1_enable) {
        if (!pow2(c.l1_line_bytes) || c.l1_line_bytes < 4) throw std::runtime_error("l1_line_bytes must be a power of two >= 4");
        if (c.l1_assoc < 1) throw std::runtime_error("l1_assoc must be >= 1");
        if (c.l1_size_bytes % (c.l1_line_bytes * c.l1_assoc) != 0 || !pow2(c.l1_size_bytes / (c.l1_line_bytes * c.l1_assoc)))
            throw std::runtime_error("l1_size_bytes / (l1_line_bytes * l1_assoc) must be a power of two (number of sets)");
        if (c.l1_policy != "lru" && c.l1_policy != "fifo" && c.l1_policy != "random")
            throw std::runtime_error("l1_policy must be lru, fifo or random");
        if (c.l1_hit_latency < 1 || c.l1_miss_latency < c.l1_hit_latency)
            throw std::runtime_error("need 1 <= l1_hit_latency <= l1_miss_latency");
    }
    return c;
}

} // namespace tfx
#endif
