`timescale 1ns/1ps
`default_nettype none
module tb_alu_diff;
    logic [3:0] opcode;
    logic [31:0] a, b, result;
    logic zero, illegal_opcode;
    integer fd, scan_result, vector_count;
    reg [8*1024-1:0] vector_file;
    reg [31:0] expected_result;
    integer expected_zero, expected_illegal;
    integer op_int;

    tfx_alu dut(.opcode(opcode), .operand_a(a), .operand_b(b), .result(result), .zero(zero), .illegal_opcode(illegal_opcode));

    initial begin
        if (!$value$plusargs("VECTORS=%s", vector_file)) begin
            $display("ERROR: pass +VECTORS=path"); $fatal(1);
        end
        fd = $fopen(vector_file, "r");
        if (fd == 0) begin $display("ERROR: cannot open vectors"); $fatal(1); end
        vector_count = 0;
        while (!$feof(fd)) begin
            scan_result = $fscanf(fd, "%h %h %h %h %d %d\n", op_int, a, b, expected_result, expected_zero, expected_illegal);
            if (scan_result == 6) begin
                opcode = op_int[3:0]; #1;
                if (result !== expected_result || zero !== expected_zero[0] || illegal_opcode !== expected_illegal[0]) begin
                    $display("FAIL vector %0d op=%h a=%h b=%h got=%h zero=%b illegal=%b expected=%h zero=%0d illegal=%0d", vector_count, opcode, a, b, result, zero, illegal_opcode, expected_result, expected_zero, expected_illegal);
                    $fatal(1);
                end
                vector_count = vector_count + 1;
            end
        end
        $fclose(fd);
        $display("PASS: ALU differential test (%0d vectors)", vector_count);
        $finish;
    end
endmodule
`default_nettype wire
