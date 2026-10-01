// Banked scratchpad SRAM model. One port per bank: each cycle a bank performs
// either one read or one write. Read data is registered (available the cycle
// after en && !we) and holds its value until the next read of that bank.
// Word address mapping (done by the client): bank = waddr % BANKS, row = waddr / BANKS.
`default_nettype none
module tfx_scratchpad #(
    parameter int BANKS = 8,
    parameter int ROWS  = 256,
    parameter int ROW_W = (ROWS > 1) ? $clog2(ROWS) : 1
) (
    input  logic                   clk,
    input  logic [BANKS-1:0]       en,
    input  logic [BANKS-1:0]       we,
    input  logic [BANKS*ROW_W-1:0] row,
    input  logic [BANKS*32-1:0]    wdata,
    output logic [BANKS*32-1:0]    rdata
);
    for (genvar b = 0; b < BANKS; b++) begin : g_bank
        logic [31:0] mem [ROWS];
        logic [31:0] q;
        wire  [ROW_W-1:0] r = row[b*ROW_W +: ROW_W];
`ifndef SYNTHESIS
        // Simulation-only power-up value so RTL and the C++ models start from
        // identical state. Real SRAM contents are undefined: software must
        // initialise every word it reads (see docs/known-limitations.md).
        initial begin
            for (int i = 0; i < ROWS; i++) mem[i] = 32'd0;
            q = 32'd0;
        end
`endif
        always_ff @(posedge clk) begin
            if (en[b]) begin
                if (we[b]) mem[r] <= wdata[b*32 +: 32];
                else       q <= mem[r];
            end
        end
        assign rdata[b*32 +: 32] = q;
    end
endmodule
`default_nettype wire
