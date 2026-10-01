// Pipelined 32x32 integer multiplier for all lanes (MUL, MULHU, MULH and their
// immediate forms). Replaces the milestone-2 arrangement in which a full
// combinational 32x32 product was computed in the dispatch cycle and then only
// delayed by MUL_LAT-1 register stages.
//
//   stage A (dispatch cycle, combinational from the operand muxes):
//        four 16x16 unsigned partial products  ll, lh, hl, hh  and the signed
//        high-word correction  corr = (a<0 ? b : 0) + (b<0 ? a : 0)
//   register R1
//   stage B: carry-save reduction of the six 64-bit terms
//        ll, lh<<16, hl<<16, hh<<32 and, for MULH only, -(corr<<32) = {~corr,1..1} + 1
//        (three 3:2 CSA levels) followed by ONE 64-bit carry-propagate adder.
//        MUL -> P[31:0]   MULHU / MULH -> P[63:32]
//        (A first version summed with three chained adders; that stage was deeper
//        than a monolithic multiplier in Yosys unit-delay terms, see docs.)
//   MD == 1 : stage B feeds the register-file write port directly (writeback cycle)
//   MD >= 2 : stage B is registered (R2); stages R3..R_MD are latency padding
// Total latency issue -> writeback is unchanged (= MUL_LAT = MD + 1), so the
// cycle-level behaviour equals the C++ timing model exactly.
//
// Note: extra stages beyond two are pure delay; Yosys does not retime them into
// the partial-product logic. The logic depth per stage is what the pipeline buys.
`default_nettype none
module tfx_mul_pipe #(
    parameter int NL = 8,
    parameter int MD = 2            // stages after dispatch (= MUL_LAT - 1), >= 1
) (
    input  logic             clk,
    input  logic             rst_n,
    input  logic [3:0]       op,      // ALU op code: 2 MUL, 14 MULHU, 15 MULH
    input  logic [NL*32-1:0] a_flat,
    input  logic [NL*32-1:0] b_flat,
    output logic [NL*32-1:0] res_flat // valid in the writeback cycle of the op
);
    logic [NL-1:0][31:0] r1_ll, r1_lh, r1_hl, r1_hh, r1_corr;
    logic [3:0]          r1_op;
    logic [NL-1:0][31:0] stage_b;

    for (genvar l = 0; l < NL; l++) begin : g_lane
        wire [31:0] a = a_flat[l*32 +: 32];
        wire [31:0] b = b_flat[l*32 +: 32];
        wire [31:0] ll = {16'd0, a[15:0]}  * {16'd0, b[15:0]};
        wire [31:0] lh = {16'd0, a[15:0]}  * {16'd0, b[31:16]};
        wire [31:0] hl = {16'd0, a[31:16]} * {16'd0, b[15:0]};
        wire [31:0] hh = {16'd0, a[31:16]} * {16'd0, b[31:16]};
        wire [31:0] corr = (a[31] ? b : 32'd0) + (b[31] ? a : 32'd0);
        always_ff @(posedge clk) begin
            if (!rst_n) begin
                r1_ll[l] <= '0; r1_lh[l] <= '0; r1_hl[l] <= '0; r1_hh[l] <= '0; r1_corr[l] <= '0;
            end else begin
                r1_ll[l] <= ll; r1_lh[l] <= lh; r1_hl[l] <= hl; r1_hh[l] <= hh; r1_corr[l] <= corr;
            end
        end
        // stage B: 6 -> 2 carry-save reduction, one final adder
        wire        sgn = (r1_op == 4'hF);
        wire [63:0] x0 = {32'd0, r1_ll[l]};
        wire [63:0] x1 = {16'd0, r1_lh[l], 16'd0};
        wire [63:0] x2 = {16'd0, r1_hl[l], 16'd0};
        wire [63:0] x3 = {r1_hh[l], 32'd0};
        wire [63:0] x4 = sgn ? {~r1_corr[l], 32'hFFFF_FFFF} : 64'd0;
        wire [63:0] x5 = {63'd0, sgn};
        wire [63:0] s1 = x0 ^ x1 ^ x2, c1 = ((x0 & x1) | (x0 & x2) | (x1 & x2)) << 1;
        wire [63:0] s2 = x3 ^ x4 ^ x5, c2 = ((x3 & x4) | (x3 & x5) | (x4 & x5)) << 1;
        wire [63:0] s3 = s1 ^ c1 ^ s2, c3 = ((s1 & c1) | (s1 & s2) | (c1 & s2)) << 1;
        wire [63:0] s4 = s3 ^ c3 ^ c2, c4 = ((s3 & c3) | (s3 & c2) | (c3 & c2)) << 1;
        wire [63:0] p  = s4 + c4;
        assign stage_b[l] = (r1_op == 4'h2) ? p[31:0] : p[63:32];
    end
    always_ff @(posedge clk) begin
        if (!rst_n) r1_op <= 4'h0;
        else        r1_op <= op;
    end

    if (MD == 1) begin : g_md1
        assign res_flat = stage_b;
    end else begin : g_mdn
        logic [MD-2:0][NL*32-1:0] pipe;
        always_ff @(posedge clk) begin
            if (!rst_n) pipe <= '0;
            else begin
                pipe[0] <= stage_b;
                for (int s = 1; s < MD - 1; s++) pipe[s] <= pipe[s-1];
            end
        end
        assign res_flat = pipe[MD-2];
    end
endmodule
`default_nettype wire
