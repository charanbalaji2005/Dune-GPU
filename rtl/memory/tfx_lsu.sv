// Load/store unit for the SIMT cluster. Mirrors the LSU section of
// simulator/include/tfx_timing.hpp.
//
//   LW / SW : each cycle, every bank serves the lowest-numbered pending lane
//             that maps to it. Loads additionally broadcast to all pending
//             lanes reading the same word. Bank conflicts cost extra rounds.
//   ATOM    : lanes serialized in ascending order; read cycle + write cycle each.
// States: IDLE -> MEM -> (DRAIN for loads) -> IDLE ; IDLE -> AT_RD <-> AT_WR -> WB -> IDLE
// Addresses are validated (alignment/bounds) by the cluster before start.
`default_nettype none
module tfx_lsu #(
    parameter int NW = 4,
    parameter int NL = 8,
    parameter int BANKS = 8,
    parameter int SPM_AW = 11,
    parameter int WW = (NW > 1) ? $clog2(NW) : 1,
    parameter int LOG2B = $clog2(BANKS),
    parameter int ROW_W = SPM_AW - LOG2B
) (
    input  logic                    clk,
    input  logic                    rst_n,
    // command (accepted only when idle)
    input  logic                    start,
    input  logic [1:0]              op,          // 1 LW, 2 SW, 3 ATOM_ADD
    input  logic [WW-1:0]           warp,
    input  logic [4:0]              rd,
    input  logic                    wr,
    input  logic [NL-1:0]           mask,
    input  logic [NL*SPM_AW-1:0]    waddr_flat,
    input  logic [NL*32-1:0]        sdata_flat,
    output logic                    idle,
    // scratchpad bank ports
    output logic [BANKS-1:0]        bank_en,
    output logic [BANKS-1:0]        bank_we,
    output logic [BANKS*ROW_W-1:0]  bank_row,
    output logic [BANKS*32-1:0]     bank_wdata,
    input  logic [BANKS*32-1:0]     bank_rdata,
    // register writeback (loads, atomics)
    output logic                    wb_valid,
    output logic [WW-1:0]           wb_warp,
    output logic [4:0]              wb_rd,
    output logic                    wb_wr,
    output logic [NL-1:0]           wb_mask,
    output logic [NL*32-1:0]        wb_data,
    // completion (decrements the warp's in-flight count)
    output logic                    done_valid,
    output logic [WW-1:0]           done_warp,
    // performance events
    output logic                    ev_busy,
    output logic                    ev_round,
    output logic [6:0]              ev_lane_acc
);
    typedef enum logic [2:0] { S_IDLE = 3'd0, S_MEM = 3'd1, S_DRAIN = 3'd2, S_AT_RD = 3'd3,
                               S_AT_WR = 3'd4, S_WB = 3'd5 } state_t;
    localparam int LIW = (NL > 1) ? $clog2(NL) : 1;

    state_t st;
    logic [WW-1:0] q_warp; logic [4:0] q_rd; logic q_wr; logic [1:0] q_op;
    logic [NL-1:0] q_mask, pend, cap;
    logic [NL-1:0][SPM_AW-1:0] waddr;
    logic [NL-1:0][31:0] sdata, ldata, ldata_cap;
    logic [LIW-1:0] at_lane;

    // bank = word_addr % BANKS (BANKS is a power of two). Written as an integer
    // mask rather than a function call: Icarus 12 hangs when a function is
    // applied to a variable assigned earlier in the same always_comb.
    localparam int BMASK = BANKS - 1;

    // ---- per-bank selection of the lowest pending lane, plus broadcast set.
    // All selections are written as constant-index loops (portable across
    // Icarus/Verilator/Yosys; no variable-index part selects).
    logic [BANKS-1:0] bank_hit;
    logic [BANKS-1:0][LIW-1:0] chosen;
    logic [BANKS-1:0][SPM_AW-1:0] bsel_addr;
    logic [BANKS-1:0][31:0] bsel_sdata;
    logic [NL-1:0] served;
    logic [LIW-1:0] at_first;
    logic [SPM_AW-1:0] af_addr, al_addr;
    logic [31:0] al_sdata, al_rdata;
    logic [NL-1:0][31:0] lane_rdata;
    localparam int LBW = (LOG2B > 0) ? LOG2B : 1;
    logic [NL-1:0][LBW-1:0] lbank;
    always_comb begin
        bank_hit = '0; chosen = '0; served = '0; bsel_addr = '0; bsel_sdata = '0;
        for (int l = 0; l < NL; l++) lbank[l] = LBW'(int'(waddr[l]) & BMASK);
        for (int b = 0; b < BANKS; b++) begin
            for (int l = NL - 1; l >= 0; l--) begin
                if (pend[l] && int'(lbank[l]) == b) begin
                    bank_hit[b] = 1'b1; chosen[b] = LIW'(l);
                    bsel_addr[b] = waddr[l]; bsel_sdata[b] = sdata[l];
                end
            end
        end
        for (int l = 0; l < NL; l++) begin
            if (pend[l]) begin
                for (int b = 0; b < BANKS; b++) begin
                    if (int'(lbank[l]) == b) begin
                        if (int'(chosen[b]) == l) served[l] = 1'b1;
                        else if (q_op == 2'd1 && bsel_addr[b] == waddr[l]) served[l] = 1'b1;
                    end
                end
            end
        end
        at_first = '0;
        for (int l = NL - 1; l >= 0; l--) if (pend[l]) at_first = LIW'(l);
        for (int l = 0; l < NL; l++) lane_rdata[l] = bank_rdata[int'(lbank[l])*32 +: 32];
        for (int l = 0; l < NL; l++) ldata_cap[l] = cap[l] ? lane_rdata[l] : ldata[l];
        af_addr = '0; al_addr = '0; al_sdata = '0; al_rdata = '0;
        for (int l = 0; l < NL; l++) begin
            if (int'(at_first) == l) af_addr = waddr[l];
            if (int'(at_lane) == l) begin al_addr = waddr[l]; al_sdata = sdata[l]; al_rdata = lane_rdata[l]; end
        end
    end

    // ---- bank port drive
    logic [SPM_AW-1:0] ta;
    always_comb begin
        bank_en = '0; bank_we = '0; bank_row = '0; bank_wdata = '0; ta = '0;
        if (st == S_MEM) begin
            for (int b = 0; b < BANKS; b++) begin
                if (bank_hit[b]) begin
                    ta = bsel_addr[b];
                    bank_en[b] = 1'b1;
                    bank_we[b] = (q_op == 2'd2);
                    bank_row[b*ROW_W +: ROW_W] = ta[SPM_AW-1:LOG2B];
                    bank_wdata[b*32 +: 32] = bsel_sdata[b];
                end
            end
        end else if (st == S_AT_RD || st == S_AT_WR) begin
            ta = (st == S_AT_RD) ? af_addr : al_addr;
            for (int b = 0; b < BANKS; b++) begin
                if ((int'(ta) & BMASK) == b) begin
                    bank_en[b] = 1'b1;
                    bank_we[b] = (st == S_AT_WR);
                    bank_row[b*ROW_W +: ROW_W] = ta[SPM_AW-1:LOG2B];
                    bank_wdata[b*32 +: 32] = al_rdata + al_sdata;
                end
            end
        end
    end

    assign idle      = (st == S_IDLE);
    assign wb_valid  = (st == S_DRAIN) || (st == S_WB);
    assign wb_warp   = q_warp;
    assign wb_rd     = q_rd;
    assign wb_wr     = q_wr;
    assign wb_mask   = q_mask;
    assign done_warp = q_warp;
    assign done_valid = wb_valid || (st == S_MEM && q_op == 2'd2 && (pend & ~served) == '0);
    assign ev_busy   = (st != S_IDLE);
    assign ev_round  = (st == S_MEM) || (st == S_AT_RD) || (st == S_AT_WR);
    always_comb begin
        ev_lane_acc = '0;
        if (st == S_MEM) for (int l = 0; l < NL; l++) ev_lane_acc = ev_lane_acc + {6'd0, served[l]};
        else if (st == S_AT_RD) ev_lane_acc = 7'd1;
    end
    for (genvar l = 0; l < NL; l++) begin : g_wb
        assign wb_data[l*32 +: 32] = (st == S_DRAIN) ? ldata_cap[l] : ldata[l];
    end

    always_ff @(posedge clk) begin
        if (!rst_n) begin
            st <= S_IDLE; pend <= '0; cap <= '0; q_mask <= '0; q_op <= '0; q_warp <= '0; q_rd <= '0; q_wr <= 1'b0;
            at_lane <= '0; ldata <= '0; sdata <= '0; waddr <= '0;
        end else begin
            case (st)
                S_IDLE: if (start) begin
                    st <= (op == 2'd3) ? S_AT_RD : S_MEM;
                    q_op <= op; q_warp <= warp; q_rd <= rd; q_wr <= wr; q_mask <= mask; pend <= mask; cap <= '0;
                    for (int l = 0; l < NL; l++) begin
                        waddr[l] <= waddr_flat[l*SPM_AW +: SPM_AW];
                        sdata[l] <= sdata_flat[l*32 +: 32];
                    end
                end
                S_MEM: begin
                    ldata <= ldata_cap;
                    pend  <= pend & ~served;
                    cap   <= (q_op == 2'd1) ? served : '0;
                    if ((pend & ~served) == '0) st <= (q_op == 2'd1) ? S_DRAIN : S_IDLE;
                end
                S_DRAIN: begin ldata <= ldata_cap; cap <= '0; st <= S_IDLE; end
                S_AT_RD: begin at_lane <= at_first; st <= S_AT_WR; end
                S_AT_WR: begin
                    for (int l = 0; l < NL; l++) if (int'(at_lane) == l) begin
                        ldata[l] <= lane_rdata[l];
                        pend[l]  <= 1'b0;
                    end
                    st <= ((pend & ~(NL'(1) << at_lane)) != '0) ? S_AT_RD : S_WB;
                end
                S_WB: st <= S_IDLE;
                default: st <= S_IDLE;
            endcase
        end
    end
endmodule
`default_nettype wire
