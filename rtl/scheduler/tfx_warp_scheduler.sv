// Warp-selection policies (combinational). Mechanism (eligibility) is computed
// by the cluster; this module only implements policy. Must match
// tfx::select_warp() in software/reference/tfx_sched.hpp.
//   0 LRR      loose round-robin starting after the last issued warp
//   1 GTO      greedy-then-oldest: keep the last warp while eligible, else lowest id
//   2 PRIO     fixed priority: lowest eligible warp id
//   3 MEMFIRST round-robin among eligible memory instructions first (keeps LSU busy)
`default_nettype none
module tfx_warp_scheduler #(
    parameter int NW = 4,
    parameter int WW = (NW > 1) ? $clog2(NW) : 1
) (
    input  logic [1:0]    policy,
    input  logic [NW-1:0] eligible,
    input  logic [NW-1:0] is_mem,
    input  logic [WW-1:0] last,
    output logic          valid,
    output logic [WW-1:0] sel
);
    logic [NW-1:0] pool;
    logic          found;
    int            w;
    always_comb begin
        valid = |eligible;
        sel   = '0;
        found = 1'b0;
        w     = 0;
        pool  = ((policy == 2'd3) && |(eligible & is_mem)) ? (eligible & is_mem) : eligible;
        if (policy == 2'd1 && eligible[last]) begin
            sel = last; found = 1'b1;
        end else if (policy == 2'd1 || policy == 2'd2) begin
            for (int i = NW - 1; i >= 0; i--) if (pool[i]) begin sel = WW'(i); found = 1'b1; end
        end else begin
            for (int i = NW; i >= 1; i--) begin
                w = (int'(last) + i) % NW;
                if (pool[w]) begin sel = WW'(w); found = 1'b1; end
            end
        end
        if (!found) sel = '0;
    end
endmodule
`default_nettype wire
