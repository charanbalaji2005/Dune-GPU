#!/usr/bin/env bash
# Technology-independent LOGIC synthesis of CharanGPU TFX-X blocks with Yosys.
# Flow: sv2v (SystemVerilog -> Verilog-2005) -> yosys synth (generic gates) -> stat + ltp.
# This reports generic cell counts and unit-delay logic depth only. It is NOT a
# timing, area or power result for any process/PDK (no liberty file, no P&R).
set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
OUT="$ROOT/results/synthesis"
mkdir -p "$OUT" "$ROOT/build/synth"
command -v sv2v >/dev/null || { echo "sv2v not found (https://github.com/zachjs/sv2v)"; exit 1; }
command -v yosys >/dev/null || { echo "yosys not found"; exit 1; }
R="$ROOT/rtl"
ALL="$R/core/tfx_alu.sv $R/core/tfx_mul_pipe.sv $R/core/tfx_decoder.sv $R/scheduler/tfx_warp_scheduler.sv $R/memory/tfx_scratchpad.sv \
     $R/memory/tfx_lsu.sv $R/compute/tfx_simt_cluster.sv $R/command_processor/tfx_command_processor.sv \
     $R/interconnect/tfx_fifo.sv $R/interconnect/tfx_token_channel.sv $R/top/charangpu_tfx_top.sv"
# name | top module | parameter overrides (yosys chparam)
JOBS=(
  "alu|tfx_alu|"
  "alu_nomul|tfx_alu|-set HAS_MUL 0"
  "mul_pipe_8l_md1|tfx_mul_pipe|-set NL 8 -set MD 1"
  "mul_pipe_8l_md2|tfx_mul_pipe|-set NL 8 -set MD 2"
  "decoder|tfx_decoder|"
  "scheduler_4w|tfx_warp_scheduler|-set NW 4"
  "fifo_64x8|tfx_fifo|-set DATA_W 64 -set DEPTH 8"
  "lsu_4w8l8b|tfx_lsu|-set NW 4 -set NL 8 -set BANKS 8 -set SPM_AW 11"
  "command_processor|tfx_command_processor|"
  "cluster_2w4l_tiny|tfx_simt_cluster|-set NW 2 -set NL 4 -set IMEM_DEPTH 32 -set SPM_WORDS 64 -set SPM_BANKS 2 -set MUL_LAT 2 -set STACK_DEPTH 2"
  "cluster_2w4l_tiny_mul3|tfx_simt_cluster|-set NW 2 -set NL 4 -set IMEM_DEPTH 32 -set SPM_WORDS 64 -set SPM_BANKS 2 -set MUL_LAT 3 -set STACK_DEPTH 2"
)
sv2v -DSYNTHESIS $ALL > "$ROOT/build/synth/charangpu.v"
for j in "${JOBS[@]}"; do
  IFS='|' read -r name top params <<< "$j"
  chp=""; [[ -n "$params" ]] && chp="chparam $params $top;"
  log="$OUT/$name.log"
  yosys -l "$log" -p "read_verilog $ROOT/build/synth/charangpu.v; $chp hierarchy -top $top; synth -flatten -top $top; \
        tee -o $OUT/$name.stat.txt stat; tee -o $OUT/$name.ltp.txt ltp -noff" > /dev/null
  cells=$(grep -m1 "Number of cells" "$OUT/$name.stat.txt" | awk '{print $NF}')
  depth=$(grep -m1 "Longest topological path" "$OUT/$name.ltp.txt" | sed -E 's/.*length=([0-9]+).*/\1/')
  echo "$name: $cells generic cells, longest combinational path $depth cells (unit delay)"
done | tee "$OUT/summary.txt"
