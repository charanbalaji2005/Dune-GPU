`timescale 1ns/1ps
`default_nettype none
// Compares tfx_decoder against vectors produced by tfx::decode() (C++).
module tb_decoder_diff;
    logic [31:0] instr;
    logic legal, use_a, use_b, writes_rd, a_imm, b_imm, is_s2r;
    logic [1:0] cls, mem_op; logic [3:0] alu_op, ctrl_op, sreg; logic [4:0] rd, ra, rb; logic [31:0] imm;
    tfx_decoder dut(.*);
    integer fd, n, rc, errors;
    reg [8*1024-1:0] path;
    reg [31:0] w, e_imm;
    integer e_legal, e_cls, e_alu, e_mem, e_ctrl, e_rd, e_ra, e_rb, e_ua, e_ub, e_wr, e_ai, e_bi, e_s2r, e_sreg;
    initial begin
        if (!$value$plusargs("VECTORS=%s", path)) begin $display("ERROR: pass +VECTORS=path"); $fatal(1); end
        fd = $fopen(path, "r"); if (fd == 0) begin $display("ERROR: cannot open vectors"); $fatal(1); end
        n = 0; errors = 0;
        while (!$feof(fd)) begin
            rc = $fscanf(fd, "%h %h %h %h %h %h %h %h %h %h %h %h %h %h %h %h %h\n", w, e_legal, e_cls, e_alu, e_mem, e_ctrl,
                         e_rd, e_ra, e_rb, e_ua, e_ub, e_wr, e_ai, e_bi, e_s2r, e_sreg, e_imm);
            if (rc == 17) begin
                instr = w; #1;
                if (legal !== e_legal[0] || cls !== e_cls[1:0] || alu_op !== e_alu[3:0] || mem_op !== e_mem[1:0] ||
                    ctrl_op !== e_ctrl[3:0] || rd !== e_rd[4:0] || ra !== e_ra[4:0] || rb !== e_rb[4:0] ||
                    use_a !== e_ua[0] || use_b !== e_ub[0] || writes_rd !== e_wr[0] || a_imm !== e_ai[0] ||
                    b_imm !== e_bi[0] || is_s2r !== e_s2r[0] || sreg !== e_sreg[3:0] || imm !== e_imm) begin
                    if (errors < 10) $display("FAIL word=%08x got legal=%b cls=%0d alu=%0h mem=%0d ctrl=%0d rd=%0d ra=%0d rb=%0d imm=%08x",
                                              w, legal, cls, alu_op, mem_op, ctrl_op, rd, ra, rb, imm);
                    errors = errors + 1;
                end
                n = n + 1;
            end
        end
        $fclose(fd);
        if (errors != 0) begin $display("FAIL: decoder differential: %0d / %0d mismatches", errors, n); $fatal(1); end
        $display("PASS: decoder differential test (%0d vectors)", n);
        $finish;
    end
endmodule
`default_nettype wire
