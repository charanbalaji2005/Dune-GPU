`timescale 1ns/1ps
`default_nettype none
module tb_register_file;
    logic clk=0, rst_n=0, write_enable=0;
    logic [4:0] write_address, read_address_a, read_address_b;
    logic [31:0] write_data, read_data_a, read_data_b;
    always #5 clk=~clk;
    tfx_register_file dut(.*);
    initial begin
        write_address=0; read_address_a=0; read_address_b=1; write_data=0;
        repeat(2) @(posedge clk); rst_n <= 1;
        @(negedge clk); write_enable=1; write_address=5; write_data=32'h1234abcd;
        @(posedge clk); #1;
        @(negedge clk); write_enable=0; read_address_a=5;
        #1; if (read_data_a !== 32'h1234abcd) $fatal(1,"register readback mismatch");
        if (read_data_b !== 0) $fatal(1,"reset register should be zero");
        $display("PASS: register file"); $finish;
    end
endmodule
`default_nettype wire
