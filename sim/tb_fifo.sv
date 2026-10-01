`timescale 1ns/1ps
`default_nettype none
module tb_fifo;
    logic clk=0, rst_n=0, push=0, pop=0;
    logic [31:0] push_data, pop_data;
    logic full, empty;
    logic [2:0] count;
    always #5 clk=~clk;
    tfx_fifo #(.DATA_W(32),.DEPTH(4)) dut(.*);
    initial begin
        push_data=0; repeat(2) @(posedge clk); rst_n<=1;
        @(negedge clk); push=1; push_data=32'h11;
        @(negedge clk); push_data=32'h22;
        @(negedge clk); push_data=32'h33;
        @(negedge clk); push_data=32'h44;
        @(posedge clk); #1; if (!full || count != 4) $fatal(1,"FIFO should be full");
        @(negedge clk); push=0; pop=1; #1; if (pop_data !== 32'h11) $fatal(1,"FIFO order mismatch 1");
        @(posedge clk); @(negedge clk); #1; if (pop_data !== 32'h22) $fatal(1,"FIFO order mismatch 2");
        @(posedge clk); @(negedge clk); #1; if (pop_data !== 32'h33) $fatal(1,"FIFO order mismatch 3");
        @(posedge clk); @(negedge clk); #1; if (pop_data !== 32'h44) $fatal(1,"FIFO order mismatch 4");
        @(posedge clk); @(negedge clk); pop=0; #1; if (!empty || count != 0) $fatal(1,"FIFO should be empty");
        $display("PASS: FIFO"); $finish;
    end
endmodule
`default_nettype wire
