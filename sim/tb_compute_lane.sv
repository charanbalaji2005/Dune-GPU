`timescale 1ns/1ps
`default_nettype none
module tb_compute_lane;
    logic clk=0, rst_n=0, cmd_valid=0, cmd_ready, result_valid, result_ready=0;
    logic [3:0] cmd_opcode;
    logic [31:0] cmd_operand_a, cmd_operand_b, result_data;
    logic result_zero, result_illegal;
    always #5 clk=~clk;
    tfx_compute_lane dut(.*);
    initial begin
        cmd_opcode=0; cmd_operand_a=0; cmd_operand_b=0;
        repeat(2) @(posedge clk); rst_n<=1;
        @(negedge clk); cmd_valid=1; cmd_opcode=0; cmd_operand_a=32'd19; cmd_operand_b=32'd23;
        @(posedge clk); #1; if (!result_valid || result_data != 42) $fatal(1,"compute lane result mismatch");
        @(negedge clk); cmd_valid=0; result_ready=0;
        repeat(2) begin @(posedge clk); #1; if (!result_valid || result_data != 42) $fatal(1,"result should remain stable while stalled"); end
        @(negedge clk); result_ready=1;
        @(posedge clk); #1; if (result_valid) $fatal(1,"result should be consumed");
        $display("PASS: compute lane"); $finish;
    end
endmodule
`default_nettype wire
