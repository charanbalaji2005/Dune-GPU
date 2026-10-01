#!/usr/bin/env bash
# Full regression: C++ builds, unit/differential RTL TBs, pytest (models + RTL co-simulation).
#   scripts/run_all.sh          fast regression (skips the long RTL kernels)
#   FULL=1 scripts/run_all.sh   also runs the slow RTL kernels, fuzzing and mutation testing
set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"
mkdir -p build
CXX="${CXX:-g++}"
FLAGS="-std=c++17 -O2 -Wall -Wextra -Wpedantic -Werror"
$CXX $FLAGS -Isoftware/reference software/reference/tfx_ref.cpp -o build/tfx_ref
$CXX $FLAGS -Isoftware/reference -Isimulator/include simulator/src/tfx_sim.cpp -o build/tfx_sim
python3 tools/configuration/validate_config.py
python3 tests/run_rtl.py
if command -v verilator >/dev/null; then
  verilator --lint-only -Wall -Wno-UNUSEDSIGNAL --top-module charangpu_tfx_top \
    rtl/core/tfx_alu.sv rtl/core/tfx_mul_pipe.sv rtl/core/tfx_decoder.sv rtl/scheduler/tfx_warp_scheduler.sv rtl/memory/tfx_scratchpad.sv \
    rtl/memory/tfx_lsu.sv rtl/compute/tfx_simt_cluster.sv rtl/command_processor/tfx_command_processor.sv \
    rtl/interconnect/tfx_fifo.sv rtl/interconnect/tfx_token_channel.sv rtl/top/charangpu_tfx_top.sv
  echo "verilator lint: clean"
fi
if [[ "${FULL:-0}" == "1" ]]; then
  python3 -m pytest -q
  for c in tfx_base tfx_small tfx_onebank; do
    python3 tools/cosim/fuzz.py --config architecture/configuration/$c.json --count 40 --seed 2026
  done
  python3 tools/cosim/mutation_test.py
else
  python3 -m pytest -q -m "not slow"
fi
