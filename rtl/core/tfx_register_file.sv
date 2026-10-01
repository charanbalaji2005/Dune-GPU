`default_nettype none
module tfx_register_file #(
    parameter int DATA_W = 32,
    parameter int ADDR_W = 5,
    parameter int NUM_REGS = (1 << ADDR_W)
) (
    input logic clk,
    input logic rst_n,
    input logic write_enable,
    input logic [ADDR_W-1:0] write_address,
    input logic [DATA_W-1:0] write_data,
    input logic [ADDR_W-1:0] read_address_a,
    input logic [ADDR_W-1:0] read_address_b,
    output logic [DATA_W-1:0] read_data_a,
    output logic [DATA_W-1:0] read_data_b
);
    logic [DATA_W-1:0] registers [0:NUM_REGS-1];
    integer i;
    always_ff @(posedge clk) begin
        if (!rst_n) begin
            for (i = 0; i < NUM_REGS; i = i + 1) registers[i] <= '0;
        end else if (write_enable && (write_address < NUM_REGS)) begin
            registers[write_address] <= write_data;
        end
    end
    always_comb begin
        read_data_a = (read_address_a < NUM_REGS) ? registers[read_address_a] : '0;
        read_data_b = (read_address_b < NUM_REGS) ? registers[read_address_b] : '0;
    end
endmodule
`default_nettype wire
