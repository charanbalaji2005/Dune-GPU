// TFX-ISA v1.0 integer ALU (combinational).
// Opcodes 0x0-0x6 are unchanged from v0.1. 0x7 is reserved -> illegal_opcode.
// Semantics are defined by tfx::execute() in software/reference/tfx_isa.hpp.
`default_nettype none
module tfx_alu #(
    parameter int DATA_W  = 32,
    // HAS_MUL = 0 removes the combinational multiplier (MUL/MULHU/MULH then return 0);
    // the SIMT cluster uses this and executes multiplies in tfx_mul_pipe instead.
    parameter bit HAS_MUL = 1'b1
) (
    input  logic [3:0]          opcode,
    input  logic [DATA_W-1:0]   operand_a,
    input  logic [DATA_W-1:0]   operand_b,
    output logic [DATA_W-1:0]   result,
    output logic                zero,
    output logic                illegal_opcode
);
    localparam logic [3:0] OP_ADD = 4'h0, OP_SUB = 4'h1, OP_MUL = 4'h2, OP_AND = 4'h3,
                           OP_OR  = 4'h4, OP_XOR = 4'h5, OP_MOV = 4'h6,
                           OP_SLL = 4'h8, OP_SRL = 4'h9, OP_SRA = 4'hA, OP_SLT = 4'hB,
                           OP_SLTU = 4'hC, OP_SEQ = 4'hD, OP_MULHU = 4'hE, OP_MULH = 4'hF;
    localparam int SH_W = $clog2(DATA_W);

    // Products as continuous assigns (keeps part-selects out of always_comb).
    wire [2*DATA_W-1:0] prod_u;
    wire signed [2*DATA_W-1:0] prod_s;
    if (HAS_MUL) begin : g_mul
        assign prod_u = {{DATA_W{1'b0}}, operand_a} * {{DATA_W{1'b0}}, operand_b};
        assign prod_s = $signed({{DATA_W{operand_a[DATA_W-1]}}, operand_a}) *
                        $signed({{DATA_W{operand_b[DATA_W-1]}}, operand_b});
    end else begin : g_nomul
        assign prod_u = '0;
        assign prod_s = '0;
    end
    wire [DATA_W-1:0] prod_lo = prod_u[DATA_W-1:0];
    wire [DATA_W-1:0] prod_hu = prod_u[2*DATA_W-1:DATA_W];
    wire [DATA_W-1:0] prod_hs = prod_s[2*DATA_W-1:DATA_W];
    wire [SH_W-1:0]   shamt   = operand_b[SH_W-1:0];
    wire [DATA_W-1:0] sra_res = $unsigned($signed(operand_a) >>> shamt);
    wire              lt_s    = $signed(operand_a) < $signed(operand_b);

    always_comb begin
        result = '0;
        illegal_opcode = 1'b0;
        case (opcode)
            OP_ADD:   result = operand_a + operand_b;
            OP_SUB:   result = operand_a - operand_b;
            OP_MUL:   result = prod_lo;
            OP_AND:   result = operand_a & operand_b;
            OP_OR:    result = operand_a | operand_b;
            OP_XOR:   result = operand_a ^ operand_b;
            OP_MOV:   result = operand_a;
            OP_SLL:   result = operand_a << shamt;
            OP_SRL:   result = operand_a >> shamt;
            OP_SRA:   result = sra_res;
            OP_SLT:   result = {{(DATA_W-1){1'b0}}, lt_s};
            OP_SLTU:  result = {{(DATA_W-1){1'b0}}, operand_a < operand_b};
            OP_SEQ:   result = {{(DATA_W-1){1'b0}}, operand_a == operand_b};
            OP_MULHU: result = prod_hu;
            OP_MULH:  result = prod_hs;
            default: begin result = '0; illegal_opcode = 1'b1; end
        endcase
    end
    assign zero = (result == '0);
endmodule
`default_nettype wire
