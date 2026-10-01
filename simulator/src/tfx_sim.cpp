// tfx_sim: CharanGPU TFX-X architectural simulator.
//
//   tfx_sim --config CFG.json --program PROG.hex [options]
//     --mem FILE          initial scratchpad image: lines "WORD_ADDR VALUE" (hex)
//     --arg I=V           kernel argument register I (0..7), V decimal or 0x-hex
//     --warps N           warps to launch (default: config num_warps)
//     --policy P          lrr | gto | prio | memfirst (default: config)
//     --mode M            functional | timing | both (default both)
//     --dump-mem FILE     final scratchpad, one hex word per line
//     --dump-regs FILE    final registers "w l r value" for every warp/lane/reg
//     --stats FILE        JSON run report
//     --trace FILE        per-cycle issue/stall trace (timing model)
//     --compare-regs      in "both" mode also require identical registers
//     --max-cycles N      watchdog (default 2,000,000)
// Exit codes: 0 ok, 1 usage/io error, 3 functional/timing mismatch, 4 timeout.
#include "tfx_config.hpp"
#include "tfx_machine.hpp"
#include "tfx_timing.hpp"
#include <cstdio>
#include <fstream>
#include <iostream>
#include <sstream>
#include <string>
#include <vector>

#ifndef TFX_SIM_VERSION
#define TFX_SIM_VERSION "1.0.0"
#endif

using namespace tfx;

static std::vector<uint32_t> read_hex_words(const std::string& path) {
    std::ifstream f(path);
    if (!f) throw std::runtime_error("cannot open " + path);
    std::vector<uint32_t> v; std::string line;
    while (std::getline(f, line)) {
        auto c = line.find("//"); if (c != std::string::npos) line = line.substr(0, c);
        std::stringstream ss(line); std::string tok;
        while (ss >> tok) v.push_back(static_cast<uint32_t>(std::stoul(tok, nullptr, 16)));
    }
    return v;
}

static void load_mem(ArchState& s, const std::string& path) {
    std::ifstream f(path);
    if (!f) throw std::runtime_error("cannot open " + path);
    std::string line;
    while (std::getline(f, line)) {
        if (line.empty() || line[0] == '#') continue;
        std::stringstream ss(line); std::string a, v;
        if (!(ss >> a >> v)) continue;
        const unsigned long addr = std::stoul(a, nullptr, 16);
        if (addr >= s.spm.size()) throw std::runtime_error("memory image address out of range");
        s.spm[addr] = static_cast<uint32_t>(std::stoul(v, nullptr, 16));
    }
}

static void dump_mem(const ArchState& s, const std::string& path) {
    std::ofstream f(path); char b[16];
    for (uint32_t v : s.spm) { std::snprintf(b, sizeof b, "%08x\n", v); f << b; }
}
static void dump_regs(ArchState& s, const std::string& path) {
    std::ofstream f(path); char b[64];
    for (int w = 0; w < s.cfg.num_warps; ++w)
        for (int l = 0; l < s.cfg.num_lanes; ++l)
            for (int r = 0; r < 32; ++r) { std::snprintf(b, sizeof b, "%d %d %d %08x\n", w, l, r, s.reg(w, l, r)); f << b; }
}

static std::string faults_json(const ArchState& s) {
    std::string j = "[";
    for (int w = 0; w < s.cfg.num_warps; ++w) {
        char b[96];
        std::snprintf(b, sizeof b, "%s{\"warp\":%d,\"code\":%u,\"pc\":%u}", w ? "," : "", w,
                      unsigned(s.warps[w].faulted ? s.warps[w].fcode : 0), unsigned(s.warps[w].faulted ? s.warps[w].fpc : 0));
        j += b;
    }
    return j + "]";
}

int main(int argc, char** argv) {
    std::string cfg_path, prog, mem, mode = "both", dmem, dregs, stats, trace, policy_s;
    int warps = -1; bool cmp_regs = false; uint64_t max_cycles = 2000000;
    std::vector<std::pair<int, uint32_t>> args;
    for (int i = 1; i < argc; ++i) {
        std::string a = argv[i];
        auto next = [&]() -> std::string { if (i + 1 >= argc) { std::cerr << "missing value for " << a << "\n"; std::exit(1); } return argv[++i]; };
        if (a == "--config") cfg_path = next();
        else if (a == "--program") prog = next();
        else if (a == "--mem") mem = next();
        else if (a == "--warps") warps = std::stoi(next());
        else if (a == "--policy") policy_s = next();
        else if (a == "--mode") mode = next();
        else if (a == "--dump-mem") dmem = next();
        else if (a == "--dump-regs") dregs = next();
        else if (a == "--stats") stats = next();
        else if (a == "--trace") trace = next();
        else if (a == "--compare-regs") cmp_regs = true;
        else if (a == "--max-cycles") max_cycles = std::stoull(next());
        else if (a == "--arg") {
            std::string kv = next(); auto eq = kv.find('=');
            if (eq == std::string::npos) { std::cerr << "--arg expects I=V\n"; return 1; }
            args.push_back({std::stoi(kv.substr(0, eq)), static_cast<uint32_t>(std::stoul(kv.substr(eq + 1), nullptr, 0))});
        } else { std::cerr << "unknown option " << a << "\n"; return 1; }
    }
    if (cfg_path.empty() || prog.empty()) { std::cerr << "usage: tfx_sim --config CFG --program PROG.hex [...]\n"; return 1; }
    try {
        Config cfg = load_config(cfg_path);
        if (policy_s.empty()) policy_s = cfg.sched_policy;
        const int policy = policy_from_name(policy_s);
        if (policy < 0) { std::cerr << "unknown policy " << policy_s << "\n"; return 1; }
        if (warps < 0) warps = cfg.num_warps;

        auto init = [&](ArchState& s) {
            auto words = read_hex_words(prog);
            if (words.size() > s.imem.size()) throw std::runtime_error("program larger than imem_depth");
            std::copy(words.begin(), words.end(), s.imem.begin());
            if (!mem.empty()) load_mem(s, mem);
            for (auto& kv : args) { if (kv.first < 0 || kv.first > 7) throw std::runtime_error("arg index 0..7"); s.args[kv.first] = kv.second; }
            s.launch(warps);
        };

        ArchState fs(cfg), ts(cfg);
        bool fun_ok = true, tim_ok = true;
        uint64_t fun_instr = 0, fun_thread = 0;
        TimingModel* tm = nullptr;
        FILE* tf = nullptr;
        if (mode == "functional" || mode == "both") {
            init(fs);
            FunctionalModel fm(fs);
            fun_ok = fm.run(max_cycles);
            fun_instr = fm.instructions; fun_thread = fm.thread_instructions;
        }
        if (mode == "timing" || mode == "both") {
            init(ts);
            if (!trace.empty()) tf = std::fopen(trace.c_str(), "w");
            tm = new TimingModel(ts, policy, tf);
            uint64_t c = 0;
            while (tm->step()) if (++c > max_cycles) { tim_ok = false; break; }
            if (tf) std::fclose(tf);
        }
        if (mode != "functional" && mode != "timing" && mode != "both") { std::cerr << "bad mode\n"; return 1; }
        ArchState& out = (mode == "functional") ? fs : ts;

        bool match = true; std::string why;
        if (mode == "both") {
            if (fs.spm != ts.spm) { match = false; why = "scratchpad"; }
            for (int w = 0; w < cfg.num_warps; ++w)
                if (fs.warps[w].faulted != ts.warps[w].faulted || (fs.warps[w].faulted &&
                    (fs.warps[w].fcode != ts.warps[w].fcode || fs.warps[w].fpc != ts.warps[w].fpc))) { match = false; why = "faults"; }
            if (cmp_regs && fs.regs != ts.regs) { match = false; why = "registers"; }
        }
        if (!dmem.empty()) dump_mem(out, dmem);
        if (!dregs.empty()) dump_regs(out, dregs);
        if (!stats.empty()) {
            std::ofstream f(stats);
            f << "{\n  \"simulator\": \"tfx_sim\", \"version\": \"" << TFX_SIM_VERSION << "\",\n"
              << "  \"config\": {\"name\": \"" << cfg.name << "\", \"num_warps\": " << cfg.num_warps
              << ", \"num_lanes\": " << cfg.num_lanes << ", \"imem_depth\": " << cfg.imem_depth
              << ", \"spm_words\": " << cfg.spm_words << ", \"spm_banks\": " << cfg.spm_banks
              << ", \"mul_latency\": " << cfg.mul_latency << ", \"stack_depth\": " << cfg.stack_depth
              << ", \"l1_enable\": " << cfg.l1_enable << ", \"l1_size_bytes\": " << cfg.l1_size_bytes
              << ", \"l1_line_bytes\": " << cfg.l1_line_bytes << ", \"l1_assoc\": " << cfg.l1_assoc
              << ", \"l1_policy\": \"" << cfg.l1_policy << "\", \"l1_hit_latency\": " << cfg.l1_hit_latency
              << ", \"l1_miss_latency\": " << cfg.l1_miss_latency << "},\n"
              << "  \"program\": \"" << prog << "\", \"launch_warps\": " << warps
              << ", \"policy\": \"" << policy_name(policy) << "\", \"mode\": \"" << mode << "\",\n"
              << "  \"functional\": {\"completed\": " << (fun_ok ? "true" : "false")
              << ", \"warp_instructions\": " << fun_instr << ", \"thread_instructions\": " << fun_thread << "},\n"
              << "  \"timing_completed\": " << (tim_ok ? "true" : "false") << ",\n"
              << "  \"models_agree\": " << (match ? "true" : "false") << ",\n"
              << "  \"faults\": " << faults_json(out) << ",\n  \"perf\": {";
            if (tm) for (int i = 0; i < PC_COUNT; ++i) f << (i ? ", " : "") << "\"" << perf_name(i) << "\": " << tm->perf[i];
            f << "}";
            if (tm && tm->l1()) {   // simulation-only L1 counters (no hardware equivalent)
                const L1Stats& c = tm->l1()->st;
                f << ",\n  \"l1\": {\"model\": \"simulation-only\", \"sets\": " << tm->l1()->sets()
                  << ", \"accesses\": " << c.accesses() << ", \"load_hits\": " << c.load_hits
                  << ", \"load_misses\": " << c.load_misses << ", \"store_hits\": " << c.store_hits
                  << ", \"store_misses\": " << c.store_misses << ", \"fills\": " << c.fills
                  << ", \"evictions\": " << c.evictions << ", \"atomic_bypasses\": " << c.atomic_bypasses
                  << ", \"atomic_invalidations\": " << c.atomic_invalidations << "}";
            }
            f << "\n}\n";
        }
        if (tm) {
            std::printf("timing: %u cycles, %u warp-instrs, %u thread-instrs, policy=%s\n",
                        tm->perf[PC_CYCLES], tm->perf[PC_ISSUED], tm->perf[PC_THREAD_INSTRS], policy_name(policy));
        }
        delete tm;
        if (!fun_ok || !tim_ok) { std::fprintf(stderr, "TIMEOUT (functional ok=%d timing ok=%d)\n", fun_ok, tim_ok); return 4; }
        if (!match) { std::fprintf(stderr, "MISMATCH between functional and timing models: %s\n", why.c_str()); return 3; }
        return 0;
    } catch (const std::exception& e) {
        std::cerr << "error: " << e.what() << "\n";
        return 1;
    }
}
