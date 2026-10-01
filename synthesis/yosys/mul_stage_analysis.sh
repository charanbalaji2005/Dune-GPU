#!/usr/bin/env bash
# Per-stage generic-cell count and unit-delay depth of the multiplier (one lane).
# Logic synthesis only: no PDK, no timing, no retiming.
set -euo pipefail
D="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"; OUT="$D/../../results/synthesis"; mkdir -p "$OUT"
for t in stage_a stage_b_chained stage_b_csa monolithic_32x32; do
  yosys -q -p "read_verilog $D/mul_stages.v; synth -flatten -top $t; tee -q -o /tmp/$t.s stat; tee -q -o /tmp/$t.l ltp -noff" >/dev/null
  echo "$t: $(grep -m1 'Number of cells' /tmp/$t.s | awk '{print $NF}') generic cells, depth $(grep -m1 'Longest' /tmp/$t.l | sed -E 's/.*length=([0-9]+).*/\1/')"
done | tee "$OUT/mul_stage_analysis.txt"
