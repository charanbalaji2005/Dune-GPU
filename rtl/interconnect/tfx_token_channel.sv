`default_nettype none
module tfx_token_channel #(
    parameter int DATA_W = 32,
    parameter int TAG_W = 6
) (
    input logic clk,
    input logic rst_n,
    input logic in_valid,
    output logic in_ready,
    input logic [DATA_W+TAG_W-1:0] in_token,
    output logic out_valid,
    input logic out_ready,
    output logic [DATA_W+TAG_W-1:0] out_token
);
    logic occupied;
    logic [DATA_W+TAG_W-1:0] token_reg;
    assign in_ready = ~occupied | out_ready;
    assign out_valid = occupied;
    assign out_token = token_reg;
    always_ff @(posedge clk) begin
        if (!rst_n) begin
            occupied <= 1'b0;
            token_reg <= '0;
        end else if (in_ready) begin
            occupied <= in_valid;
            if (in_valid) token_reg <= in_token;
        end
    end
endmodule
`default_nettype wire
