// TFX reference CLI.
//   tfx_ref OPCODE A B                       evaluate one ALU op (v0.1 interface, unchanged)
//   tfx_ref --vectors FILE [COUNT] [SEED]    ALU differential vectors (all 16 ALU codes)
//   tfx_ref --decode-vectors FILE COUNT SEED decoder differential vectors
//   tfx_ref --sched-vectors FILE NUM_WARPS   exhaustive scheduler-policy vectors
//   tfx_ref --opcode-table                   decode of every 6-bit major opcode
#include "tfx_isa.hpp"
#include "tfx_sched.hpp"
#include <cstdint>
#include <fstream>
#include <iomanip>
#include <iostream>
#include <random>
#include <string>

static int gen_alu(const std::string& path, unsigned count, uint32_t seed) {
    std::ofstream out(path);
    if (!out) { std::cerr << "Cannot open vector output: " << path << "\n"; return 2; }
    std::mt19937 rng(seed);
    std::uniform_int_distribution<uint32_t> word(0, 0xffffffffu);
    std::uniform_int_distribution<unsigned> opcode(0, 15);   // includes reserved opcode 7
    std::uniform_int_distribution<unsigned> small(0, 40);
    for (unsigned i = 0; i < count; ++i) {
        uint8_t op = static_cast<uint8_t>(opcode(rng));
        uint32_t a = word(rng), b = word(rng);
        if (i % 7 == 3) b = small(rng);                          // exercise shift amounts / compares
        if (i % 11 == 5) b = a;                                  // equality
        if (i == 0) { op = 0; a = 0xffffffffu; b = 1; }          // wraparound
        if (i == 1) { op = 1; a = 0; b = 1; }
        if (i == 2) { op = 2; a = 0xffffffffu; b = 0xffffffffu; }
        if (i == 3) { op = 0xF; a = 0x80000000u; b = 0x80000000u; } // MULH most-negative squared
        if (i == 4) { op = 0xA; a = 0x80000000u; b = 31; }
        if (i == 5) { op = 0xB; a = 0xffffffffu; b = 0; }        // -1 < 0 signed
        if (i == 6) { op = 0xC; a = 0xffffffffu; b = 0; }        // not < unsigned
        const auto r = tfx::execute(op, a, b);
        out << std::hex << std::setfill('0') << std::setw(1) << unsigned(op) << " "
            << std::setw(8) << a << " " << std::setw(8) << b << " "
            << std::setw(8) << r.value << " " << unsigned(r.zero) << " " << unsigned(r.illegal) << "\n";
    }
    std::cout << "Generated " << count << " ALU vectors at " << path << " (seed=" << seed << ")\n";
    return 0;
}

static int gen_decode(const std::string& path, unsigned count, uint32_t seed) {
    std::ofstream out(path);
    if (!out) { std::cerr << "Cannot open " << path << "\n"; return 2; }
    std::mt19937 rng(seed);
    std::uniform_int_distribution<uint32_t> word(0, 0xffffffffu);
    unsigned n = 0;
    // Every opcode with zero fields, then with all-ones fields, then random words.
    for (uint32_t opc = 0; opc < 64; ++opc) {
        for (uint32_t body : {0u, 0x03FFFFFFu, 0x0000000Au, 0x00008000u}) {
            uint32_t w = (opc << 26) | body;
            out << std::hex << std::setw(8) << std::setfill('0') << w << " " << tfx::decoded_fields_hex(tfx::decode(w)) << "\n";
            ++n;
        }
    }
    for (unsigned i = 0; i < count; ++i) {
        uint32_t w = word(rng);
        if (i & 1) w &= ~0x7FFu;                                 // make R-forms legal half the time
        out << std::hex << std::setw(8) << std::setfill('0') << w << " " << tfx::decoded_fields_hex(tfx::decode(w)) << "\n";
        ++n;
    }
    std::cout << "Generated " << std::dec << n << " decoder vectors at " << path << "\n";
    return 0;
}

static int gen_sched(const std::string& path, int nw) {
    std::ofstream out(path);
    if (!out || nw < 1 || nw > 8) { std::cerr << "bad sched args\n"; return 2; }
    unsigned n = 0;
    const uint32_t lim = 1u << nw;
    for (int pol = 0; pol < 4; ++pol)
        for (int last = 0; last < nw; ++last)
            for (uint32_t el = 0; el < lim; ++el)
                for (uint32_t mem = 0; mem < lim; ++mem) {
                    int s = tfx::select_warp(pol, nw, el, mem, last);
                    out << std::hex << pol << " " << last << " " << el << " " << mem << " "
                        << (s >= 0 ? 1 : 0) << " " << (s >= 0 ? s : 0) << "\n";
                    ++n;
                }
    std::cout << "Generated " << std::dec << n << " scheduler vectors at " << path << "\n";
    return 0;
}

int main(int argc, char** argv) {
    const std::string a1 = argc >= 2 ? argv[1] : "";
    if (a1 == "--vectors") {
        if (argc < 3) { std::cerr << "Usage: tfx_ref --vectors FILE [COUNT] [SEED]\n"; return 2; }
        unsigned count = argc >= 4 ? static_cast<unsigned>(std::stoul(argv[3])) : 500;
        uint32_t seed = argc >= 5 ? static_cast<uint32_t>(std::stoul(argv[4])) : 20261001u;
        return gen_alu(argv[2], count, seed);
    }
    if (a1 == "--decode-vectors" && argc == 5)
        return gen_decode(argv[2], static_cast<unsigned>(std::stoul(argv[3])), static_cast<uint32_t>(std::stoul(argv[4])));
    if (a1 == "--sched-vectors" && argc == 4) return gen_sched(argv[2], std::stoi(argv[3]));
    if (a1 == "--opcode-table") {
        for (uint32_t opc = 0; opc < 64; ++opc) {
            auto d = tfx::decode(opc << 26);
            std::cout << opc << " " << d.legal << " " << unsigned(d.cls) << " " << unsigned(d.mem_op)
                      << " " << unsigned(d.ctrl_op) << "\n";
        }
        return 0;
    }
    if (argc == 4) {
        unsigned op = static_cast<unsigned>(std::stoul(argv[1], nullptr, 0));
        uint32_t a = static_cast<uint32_t>(std::stoul(argv[2], nullptr, 0));
        uint32_t b = static_cast<uint32_t>(std::stoul(argv[3], nullptr, 0));
        auto r = tfx::execute(static_cast<uint8_t>(op), a, b);
        std::cout << "opcode=" << tfx::opcode_name(static_cast<uint8_t>(op))
                  << " result=0x" << std::hex << std::setw(8) << std::setfill('0') << r.value
                  << " zero=" << std::dec << r.zero << " illegal=" << r.illegal << "\n";
        return 0;
    }
    std::cerr << "Usage: tfx_ref OPCODE A B | --vectors FILE [COUNT] [SEED] | --decode-vectors FILE COUNT SEED\n"
                 "       | --sched-vectors FILE NUM_WARPS | --opcode-table\n";
    return 2;
}
