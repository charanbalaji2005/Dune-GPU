// Stand-alone copies of the per-lane multiplier stages (single lane) used only to
// attribute logic depth per pipeline stage. Must be kept in sync with
// rtl/core/tfx_mul_pipe.sv; `stage_b_chained` is the rejected first design.
module stage_a(input [31:0] a, b, output [31:0] ll, lh, hl, hh, corr);
  assign ll = {16'd0,a[15:0]}*{16'd0,b[15:0]};   assign lh = {16'd0,a[15:0]}*{16'd0,b[31:16]};
  assign hl = {16'd0,a[31:16]}*{16'd0,b[15:0]};  assign hh = {16'd0,a[31:16]}*{16'd0,b[31:16]};
  assign corr = (a[31] ? b : 32'd0) + (b[31] ? a : 32'd0);
endmodule
module stage_b_chained(input [31:0] ll, lh, hl, hh, corr, input [3:0] op, output [31:0] r);
  wire [32:0] mid = {1'b0,lh} + {1'b0,hl};
  wire [63:0] p = {32'd0,ll} + {15'd0,mid,16'd0} + {hh,32'd0};
  assign r = (op == 4'h2) ? p[31:0] : (op == 4'hE) ? p[63:32] : (p[63:32] - corr);
endmodule
module stage_b_csa(input [31:0] ll, lh, hl, hh, corr, input [3:0] op, output [31:0] r);
  wire sgn = (op == 4'hF);
  wire [63:0] x0 = {32'd0, ll}, x1 = {16'd0, lh, 16'd0}, x2 = {16'd0, hl, 16'd0}, x3 = {hh, 32'd0};
  wire [63:0] x4 = sgn ? {~corr, 32'hFFFFFFFF} : 64'd0, x5 = {63'd0, sgn};
  wire [63:0] s1 = x0 ^ x1 ^ x2, c1 = ((x0 & x1) | (x0 & x2) | (x1 & x2)) << 1;
  wire [63:0] s2 = x3 ^ x4 ^ x5, c2 = ((x3 & x4) | (x3 & x5) | (x4 & x5)) << 1;
  wire [63:0] s3 = s1 ^ c1 ^ s2, c3 = ((s1 & c1) | (s1 & s2) | (c1 & s2)) << 1;
  wire [63:0] s4 = s3 ^ c3 ^ c2, c4 = ((s3 & c3) | (s3 & c2) | (c3 & c2)) << 1;
  wire [63:0] p = s4 + c4;
  assign r = (op == 4'h2) ? p[31:0] : p[63:32];
endmodule
module monolithic_32x32(input [31:0] a, b, output [63:0] p);
  assign p = {32'd0, a} * {32'd0, b};
endmodule
