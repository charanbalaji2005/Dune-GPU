`default_nettype none
module tfx_compute_lane #(
    parameter int DATA_W = 32
) (
    input logic clk,
    input logic rst_n,
    input logic cmd_valid,
    output logic cmd_ready,
    input logic [3:0] cmd_opcode,
    input logic [DATA_W-1:0] cmd_operand_a,
    input logic [DATA_W-1:0] cmd_operand_b,
    output logic result_valid,
    input logic result_ready,
    output logic [DATA_W-1:0] result_data,
    output logic result_zero,
    output logic result_illegal
);
    logic [DATA_W-1:0] alu_result;
    logic alu_zero, alu_illegal;
    logic occupied;
    logic [DATA_W-1:0] result_reg;
    logic zero_reg, illegal_reg;

    tfx_alu #(.DATA_W(DATA_W)) u_alu (
        .opcode(cmd_opcode), .operand_a(cmd_operand_a), .operand_b(cmd_operand_b),
        .result(alu_result), .zero(alu_zero), .illegal_opcode(alu_illegal)
    );

    assign cmd_ready = ~occupied | result_ready;
    assign result_valid = occupied;
    assign result_data = result_reg;
    assign result_zero = zero_reg;
    assign result_illegal = illegal_reg;

    always_ff @(posedge clk) begin
        if (!rst_n) begin
            occupied <= 1'b0;
            result_reg <= '0;
            zero_reg <= 1'b1;
            illegal_reg <= 1'b0;
        end else if (cmd_ready) begin
            occupied <= cmd_valid;
            if (cmd_valid) begin
                result_reg <= alu_result;
                zero_reg <= alu_zero;
                illegal_reg <= alu_illegal;
            end
        end
    end
endmodule
`default_nettype wire
