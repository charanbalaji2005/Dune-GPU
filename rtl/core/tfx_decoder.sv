// TFX-ISA v1.0 instruction decoder (combinational).
// Must match tfx::decode() in software/reference/tfx_isa.hpp bit-for-bit
// (checked exhaustively per opcode + randomly by sim/tb_decoder_diff.sv).
`default_nettype none
module tfx_decoder (
    input  logic [31:0] instr,
    output logic        legal,
    output logic [1:0]  cls,        // 0 ALU, 1 MUL, 2 LSU, 3 CTRL
    output logic [3:0]  alu_op,
    output logic [1:0]  mem_op,     // 0 none, 1 LW, 2 SW, 3 ATOM_ADD
    output logic [3:0]  ctrl_op,    // 0 none, 1 JMP, 2 BANY, 3 BNONE, 4 IF, 5 ELSE, 6 ENDIF, 7 BAR, 8 EXIT, 9 ILLEGAL
    output logic [4:0]  rd,
    output logic [4:0]  ra,
    output logic [4:0]  rb,
    output logic        use_a,
    output logic        use_b,
    output logic        writes_rd,
    output logic        a_imm,
    output logic        b_imm,
    output logic        is_s2r,
    output logic [3:0]  sreg,
    output logic [31:0] imm
);
    wire [5:0]  opc   = instr[31:26];
    wire [4:0]  f_rd  = instr[25:21];
    wire [4:0]  f_rs1 = instr[20:16];
    wire [4:0]  f_rs2 = instr[15:11];
    wire [15:0] imm16 = instr[15:0];
    wire [31:0] sext  = {{16{imm16[15]}}, imm16};
    wire        low11_zero = (instr[10:0] == 11'd0);
    wire [3:0]  aop   = opc[3:0];
    wire        is_i  = opc[4];
    wire        aop_is_mul = (aop == 4'h2) || (aop == 4'hE) || (aop == 4'hF);
    wire        sreg_ok = (imm16 <= 16'd4) || ((imm16 >= 16'd8) && (imm16 <= 16'd15));

    always_comb begin
        legal = 1'b0; cls = 2'd3; alu_op = 4'd0; mem_op = 2'd0; ctrl_op = 4'd9;
        rd = 5'd0; ra = 5'd0; rb = 5'd0;
        use_a = 1'b0; use_b = 1'b0; writes_rd = 1'b0; a_imm = 1'b0; b_imm = 1'b0;
        is_s2r = 1'b0; sreg = 4'd0; imm = 32'd0;
        if (opc < 6'h20) begin
            if (aop != 4'h7 && (is_i || low11_zero)) begin
                legal = 1'b1; alu_op = aop; cls = aop_is_mul ? 2'd1 : 2'd0; ctrl_op = 4'd0;
                rd = f_rd; writes_rd = (f_rd != 5'd0);
                if (is_i) begin
                    imm = sext;
                    if (aop == 4'h6) a_imm = 1'b1;
                    else begin use_a = 1'b1; ra = f_rs1; b_imm = 1'b1; end
                end else begin
                    use_a = 1'b1; ra = f_rs1;
                    if (aop != 4'h6) begin use_b = 1'b1; rb = f_rs2; end
                end
            end
        end else begin
            case (opc)
                6'h20: begin legal = 1'b1; cls = 2'd0; alu_op = 4'h6; ctrl_op = 4'd0;           // LUI
                             rd = f_rd; writes_rd = (f_rd != 5'd0); a_imm = 1'b1; imm = {imm16, 16'd0}; end
                6'h21: begin legal = 1'b1; cls = 2'd2; mem_op = 2'd1; ctrl_op = 4'd0;           // LW
                             rd = f_rd; writes_rd = (f_rd != 5'd0); use_a = 1'b1; ra = f_rs1; imm = sext; end
                6'h22: begin legal = 1'b1; cls = 2'd2; mem_op = 2'd2; ctrl_op = 4'd0;           // SW
                             use_a = 1'b1; ra = f_rs1; use_b = 1'b1; rb = f_rd; imm = sext; end
                6'h23: if (low11_zero) begin legal = 1'b1; cls = 2'd2; mem_op = 2'd3; ctrl_op = 4'd0; // ATOM.ADD
                             rd = f_rd; writes_rd = (f_rd != 5'd0); use_a = 1'b1; ra = f_rs1; use_b = 1'b1; rb = f_rs2; end
                6'h24: if (sreg_ok) begin legal = 1'b1; cls = 2'd0; alu_op = 4'h6; ctrl_op = 4'd0;  // S2R
                             rd = f_rd; writes_rd = (f_rd != 5'd0); is_s2r = 1'b1; sreg = imm16[3:0]; end
                6'h28: begin legal = 1'b1; ctrl_op = 4'd1; imm = sext; end                        // JMP
                6'h29: begin legal = 1'b1; ctrl_op = 4'd2; use_a = 1'b1; ra = f_rs1; imm = sext; end // BANY
                6'h2A: begin legal = 1'b1; ctrl_op = 4'd3; use_a = 1'b1; ra = f_rs1; imm = sext; end // BNONE
                6'h30: begin legal = 1'b1; ctrl_op = 4'd4; use_a = 1'b1; ra = f_rs1; imm = sext; end // IF
                6'h31: begin legal = 1'b1; ctrl_op = 4'd5; imm = sext; end                        // ELSE
                6'h32: begin legal = 1'b1; ctrl_op = 4'd6; end                                    // ENDIF
                6'h38: begin legal = 1'b1; ctrl_op = 4'd7; end                                    // BAR
                6'h39: begin legal = 1'b1; ctrl_op = 4'd8; end                                    // EXIT
                default: ;
            endcase
        end
    end
endmodule
`default_nettype wire
