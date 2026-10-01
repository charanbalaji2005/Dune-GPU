`timescale 1ns/1ps
`default_nettype none
module tb_token_channel;
    logic clk=0, rst_n=0, in_valid=0, in_ready, out_valid, out_ready=0;
    logic [37:0] in_token, out_token;
    always #5 clk=~clk;
    tfx_token_channel #(.DATA_W(32),.TAG_W(6)) dut(.*);
    initial begin
        in_token=0; repeat(2) @(posedge clk); rst_n<=1;
        @(negedge clk); in_valid=1; in_token={6'd7,32'hcafe1234};
        @(posedge clk); #1; if (!out_valid || out_token !== {6'd7,32'hcafe1234}) $fatal(1,"token not captured");
        @(negedge clk); in_valid=0; out_ready=0;
        repeat(3) begin @(posedge clk); #1; if (!out_valid || out_token !== {6'd7,32'hcafe1234}) $fatal(1,"token changed while stalled"); end
        @(negedge clk); out_ready=1; in_valid=1; in_token={6'd8,32'h12345678};
        @(posedge clk); #1; if (!out_valid || out_token !== {6'd8,32'h12345678}) $fatal(1,"simultaneous consume/refill failed");
        @(negedge clk); in_valid=0; out_ready=1;
        @(posedge clk); #1; if (out_valid) $fatal(1,"channel should be empty");
        $display("PASS: token channel"); $finish;
    end
endmodule
`default_nettype wire
