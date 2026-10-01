// CharanGPU TFX-X SIMT compute cluster.
//
// NW resident warps x NL lanes, single-issue, in-order per warp, out-of-order
// completion across units, per-warp register scoreboard.
//
//   ISSUE    scheduler (tfx_warp_scheduler) picks one eligible warp
//   DISPATCH register read; ALU executes + writes back; MUL enters a
//            (MUL_LAT-1)-stage pipe; LSU op starts tfx_lsu; control resolves
//
// Cycle-for-cycle reference: simulator/include/tfx_timing.hpp. Any change
// here must be mirrored there (tests compare cycles, counters and traces).
`default_nettype none
module tfx_simt_cluster #(
    parameter int NW          = 4,
    parameter int NL          = 8,
    parameter int IMEM_DEPTH  = 256,
    parameter int SPM_WORDS   = 2048,
    parameter int SPM_BANKS   = 8,
    parameter int MUL_LAT     = 3,
    parameter int STACK_DEPTH = 8
) (
    input  logic        clk,
    input  logic        rst_n,
    // host access (honoured only while !running)
    input  logic        imem_we,
    input  logic [15:0] imem_addr,
    input  logic [31:0] imem_wdata,
    input  logic        spm_en,
    input  logic        spm_we,
    input  logic [31:0] spm_addr,       // word address
    input  logic [31:0] spm_wdata,
    output logic [31:0] spm_rdata,      // valid the cycle after a read
    input  logic        arg_we,
    input  logic [2:0]  arg_idx,
    input  logic [31:0] arg_wdata,
    input  logic        launch,
    input  logic [3:0]  launch_warps,
    input  logic [1:0]  launch_policy,
    output logic        running,
    output logic        done_pulse,
    // status / debug (combinational)
    input  logic [3:0]  perf_idx,
    output logic [31:0] perf_value,
    input  logic [2:0]  dbg_warp,
    input  logic [4:0]  dbg_lane,
    input  logic [4:0]  dbg_reg,
    output logic [31:0] dbg_reg_value,
    output logic [3:0]  dbg_fault_code,     // v1.1: widened to 4 bits (BRANCH_DEPTH = 8)
    output logic [15:0] dbg_fault_pc
);
    // ------------------------------------------------------------ parameters
    localparam int PCW    = 16;
    localparam int WW     = (NW > 1) ? $clog2(NW) : 1;
    localparam int SPW    = $clog2(STACK_DEPTH + 1);
    localparam int SPM_AW = $clog2(SPM_WORDS);
    localparam int LOG2B  = $clog2(SPM_BANKS);
    localparam int ROW_W  = SPM_AW - LOG2B;
    localparam int MD     = MUL_LAT - 1;          // multiplier pipeline stages
    localparam int IFW    = 5;
    localparam int IAW    = $clog2(IMEM_DEPTH);
    localparam logic [NL-1:0] FULL = {NL{1'b1}};

    localparam logic [1:0] C_ALU = 2'd0, C_MUL = 2'd1, C_LSU = 2'd2, C_CTRL = 2'd3;
    localparam logic [3:0] CT_JMP = 4'd1, CT_BANY = 4'd2, CT_BNONE = 4'd3, CT_IF = 4'd4, CT_ELSE = 4'd5,
                           CT_ENDIF = 4'd6, CT_BAR = 4'd7, CT_EXIT = 4'd8;
    localparam logic [3:0] F_ILLEGAL = 4'd1, F_MISALIGNED = 4'd2, F_OOB = 4'd3, F_PC_RANGE = 4'd4,
                           F_STACK_OVF = 4'd5, F_STACK_UNF = 4'd6, F_EXIT_DIV = 4'd7, F_BR_DEPTH = 4'd8;
    localparam int P_CYCLES = 0, P_ISSUED = 1, P_THREAD = 2, P_ALU = 3, P_MUL = 4, P_MEM = 5, P_CTRL = 6,
                   P_ST_SB = 7, P_ST_STRUCT = 8, P_ST_BAR = 9, P_ST_OTHER = 10, P_LSU_ROUNDS = 11,
                   P_LSU_LANES = 12, P_DIV_IFS = 13, P_BAR_REL = 14, P_LSU_BUSY = 15;

    initial begin
        if (MUL_LAT < 2) $error("tfx_simt_cluster: MUL_LAT must be >= 2");
        if (NL > 32 || NW > 8) $error("tfx_simt_cluster: NL <= 32 and NW <= 8");
        if (SPM_WORDS <= SPM_BANKS) $error("tfx_simt_cluster: SPM_WORDS must exceed SPM_BANKS");
    end

    // ------------------------------------------------------------ state
    logic [1:0]                  policy_q;
    logic [3:0]                  nwarps_q;
    logic [WW-1:0]               last_w;
    logic [NW-1:0]               launched, exited, faulted, at_bar, ctrl_wait;
    logic [NW-1:0][PCW-1:0]      pc;
    logic [NW-1:0][NL-1:0]       mask;
    logic [NW-1:0][SPW-1:0]      sp;
    logic [NW*STACK_DEPTH-1:0][NL-1:0] stk_saved, stk_taken;
    logic [NW-1:0][3:0]          fcode;
    logic [NW-1:0][PCW-1:0]      fpc;
    logic [NW-1:0][31:0]         sb;
    logic [NW-1:0][IFW-1:0]      inflight;
    logic [7:0][31:0]            args;
    logic [15:0][31:0]           perf;
    // issue register
    logic                        iss_v, iss_oob;
    logic [WW-1:0]               iss_w;
    logic [PCW-1:0]              iss_pc;
    logic [31:0]                 iss_word;
    logic [NL-1:0]               iss_mask;
    // multiplier pipeline
    logic [MD-1:0]               mul_v, mul_wr;
    logic [MD-1:0][WW-1:0]       mul_w;
    logic [MD-1:0][4:0]          mul_rd;
    logic [MD-1:0][NL-1:0]       mul_mask;

    wire [NW-1:0] alive = launched & ~exited & ~faulted;

    // ------------------------------------------------------------ instruction memory + fetch
    logic [31:0] imem [IMEM_DEPTH];
`ifndef SYNTHESIS
    initial for (int i = 0; i < IMEM_DEPTH; i++) imem[i] = 32'd0;  // matches C++ zero-filled imem
`endif
    always_ff @(posedge clk) if (imem_we && !running && imem_addr < 16'(IMEM_DEPTH)) imem[imem_addr[IAW-1:0]] <= imem_wdata;

    logic [NW-1:0][31:0] fword;
    logic [NW-1:0]       foob;
    logic [NW-1:0]       f_legal, f_use_a, f_use_b, f_wr, f_ai, f_bi, f_s2r;
    logic [NW-1:0][1:0]  f_cls, f_mem;
    logic [NW-1:0][3:0]  f_alu, f_ctrl, f_sreg;
    logic [NW-1:0][4:0]  f_rd, f_ra, f_rb;
    logic [NW-1:0][31:0] f_imm;
    for (genvar g = 0; g < NW; g++) begin : g_fetch
        assign foob[g]  = {1'b0, pc[g]} >= 17'(IMEM_DEPTH);
        assign fword[g] = imem[pc[g][IAW-1:0]];
        tfx_decoder u_dec (
            .instr(fword[g]), .legal(f_legal[g]), .cls(f_cls[g]), .alu_op(f_alu[g]), .mem_op(f_mem[g]),
            .ctrl_op(f_ctrl[g]), .rd(f_rd[g]), .ra(f_ra[g]), .rb(f_rb[g]), .use_a(f_use_a[g]), .use_b(f_use_b[g]),
            .writes_rd(f_wr[g]), .a_imm(f_ai[g]), .b_imm(f_bi[g]), .is_s2r(f_s2r[g]), .sreg(f_sreg[g]), .imm(f_imm[g]));
    end

    // ------------------------------------------------------------ dispatch-stage decode
    logic d_legal, d_use_a, d_use_b, d_wr, d_ai, d_bi, d_s2r;
    logic [1:0] d_cls_raw, d_mem; logic [3:0] d_alu, d_ctrl_raw, d_sreg; logic [4:0] d_rd, d_ra, d_rb; logic [31:0] d_imm;
    tfx_decoder u_dec_d (
        .instr(iss_word), .legal(d_legal), .cls(d_cls_raw), .alu_op(d_alu), .mem_op(d_mem), .ctrl_op(d_ctrl_raw),
        .rd(d_rd), .ra(d_ra), .rb(d_rb), .use_a(d_use_a), .use_b(d_use_b), .writes_rd(d_wr), .a_imm(d_ai),
        .b_imm(d_bi), .is_s2r(d_s2r), .sreg(d_sreg), .imm(d_imm));
    wire [1:0] d_cls  = iss_oob ? C_CTRL : d_cls_raw;
    wire       d_is_alu = iss_v && !iss_oob && d_cls == C_ALU;
    wire       d_is_mul = iss_v && !iss_oob && d_cls == C_MUL;
    wire       d_is_lsu = iss_v && !iss_oob && d_cls == C_LSU;
    wire       d_is_ctl = iss_v && d_cls == C_CTRL;

    // ------------------------------------------------------------ register files (one per lane)
    logic [NL-1:0][31:0] rfa, rfb, opa, opb, alu_res;
    logic [NL-1:0][31:0] dbg_lane_val;
    logic [NL-1:0]       alu_illegal_unused, alu_zero_unused;
    // writeback ports
    wire                 wa_en = d_is_alu && d_wr;
    wire                 wm_en = mul_v[MD-1] && mul_wr[MD-1];
    logic                lsu_wb_valid, lsu_wb_wr; logic [WW-1:0] lsu_wb_w; logic [4:0] lsu_wb_rd;
    logic [NL-1:0]       lsu_wb_mask; logic [NL*32-1:0] lsu_wb_data;
    wire                 wl_en = lsu_wb_valid && lsu_wb_wr;
    wire                 launch_go = launch && !running;

    for (genvar l = 0; l < NL; l++) begin : g_lane
        // Register storage has no reset (RAM-inferable). "Launch zeroes all
        // registers" is implemented with a per-entry valid bit cleared in one
        // cycle; an invalid entry reads as zero.
        logic [31:0]        rf [NW*32];
        logic [NW*32-1:0]   rvld;
        wire [31:0] ra_raw = rf[32 * int'(iss_w) + int'(d_ra)];
        wire [31:0] rb_raw = rf[32 * int'(iss_w) + int'(d_rb)];
        wire [31:0] rd_raw = rf[32 * int'(dbg_warp[WW-1:0]) + int'(dbg_reg)];
        assign rfa[l] = rvld[32 * int'(iss_w) + int'(d_ra)] ? ra_raw : 32'd0;
        assign rfb[l] = rvld[32 * int'(iss_w) + int'(d_rb)] ? rb_raw : 32'd0;
        assign dbg_lane_val[l] = (int'(dbg_warp) < NW && rvld[32 * int'(dbg_warp[WW-1:0]) + int'(dbg_reg)]) ? rd_raw : 32'd0;
        always_ff @(posedge clk) begin
            if (wa_en && iss_mask[l])
                rf[32 * int'(iss_w) + int'(d_rd)] <= alu_res[l];
            if (wm_en && mul_mask[MD-1][l])
                rf[32 * int'(mul_w[MD-1]) + int'(mul_rd[MD-1])] <= mul_res[l*32 +: 32];
            if (wl_en && lsu_wb_mask[l])
                rf[32 * int'(lsu_wb_w) + int'(lsu_wb_rd)] <= lsu_wb_data[l*32 +: 32];
        end
        always_ff @(posedge clk) begin
            if (!rst_n || launch_go) rvld <= '0;
            else begin
                if (wa_en && iss_mask[l]) rvld[32 * int'(iss_w) + int'(d_rd)] <= 1'b1;
                if (wm_en && mul_mask[MD-1][l]) rvld[32 * int'(mul_w[MD-1]) + int'(mul_rd[MD-1])] <= 1'b1;
                if (wl_en && lsu_wb_mask[l]) rvld[32 * int'(lsu_wb_w) + int'(lsu_wb_rd)] <= 1'b1;
            end
        end
        // operand select
        always_comb begin
            if (d_ai) opa[l] = d_imm;
            else if (d_s2r) begin
                case (d_sreg)
                    4'd0: opa[l] = 32'(l);
                    4'd1: opa[l] = 32'(iss_w);
                    4'd2: opa[l] = 32'(int'(iss_w) * NL + l);
                    4'd3: opa[l] = 32'(NL);
                    4'd4: opa[l] = {28'd0, nwarps_q};
                    default: opa[l] = args[d_sreg[2:0]];
                endcase
            end else opa[l] = rfa[l];
            opb[l] = d_bi ? d_imm : rfb[l];
        end
        tfx_alu #(.DATA_W(32), .HAS_MUL(1'b0)) u_alu (.opcode(d_alu), .operand_a(opa[l]), .operand_b(opb[l]),
                                      .result(alu_res[l]), .zero(alu_zero_unused[l]), .illegal_opcode(alu_illegal_unused[l]));
    end
    // ---- pipelined multiplier (all lanes); control/valid travel in mul_v/mul_w/... above
    logic [NL*32-1:0] mul_res;
    tfx_mul_pipe #(.NL(NL), .MD(MD)) u_mul (.clk(clk), .rst_n(rst_n), .op(d_alu), .a_flat(opa), .b_flat(opb),
                                         .res_flat(mul_res));
    assign dbg_reg_value = (int'(dbg_lane) < NL) ? dbg_lane_val[dbg_lane[$clog2(NL > 1 ? NL : 2)-1:0]] : 32'd0;
    assign dbg_fault_code = (int'(dbg_warp) < NW) ? (faulted[dbg_warp[WW-1:0]] ? fcode[dbg_warp[WW-1:0]] : 4'd0) : 4'd0;
    assign dbg_fault_pc   = (int'(dbg_warp) < NW) ? (faulted[dbg_warp[WW-1:0]] ? fpc[dbg_warp[WW-1:0]] : 16'd0) : 16'd0;
    assign perf_value     = perf[perf_idx];

    // ------------------------------------------------------------ memory address + fault check
    logic [31:0]                maddr_t;
    logic [NL*SPM_AW-1:0]       lsu_waddr_flat;
    logic [NL*32-1:0]           lsu_sdata_flat;
    logic                       mf_mis, mf_oob;
    always_comb begin
        mf_mis = 1'b0; mf_oob = 1'b0;
        for (int l = 0; l < NL; l++) begin
            maddr_t = opa[l] + d_imm;
            lsu_waddr_flat[l*SPM_AW +: SPM_AW] = maddr_t[SPM_AW+1:2];
            lsu_sdata_flat[l*32 +: 32] = opb[l];
            if (iss_mask[l]) begin
                if (maddr_t[1:0] != 2'b00) mf_mis = 1'b1;
                else if (maddr_t >= 32'(SPM_WORDS * 4)) mf_oob = 1'b1;
            end
        end
    end
    wire       mem_fault = mf_mis || mf_oob;
    wire [3:0] mem_fcode = mf_mis ? F_MISALIGNED : F_OOB;
    wire       lsu_start = d_is_lsu && !mem_fault;

    // ------------------------------------------------------------ LSU + scratchpad
    logic                    lsu_idle, lsu_done_v, ev_busy, ev_round; logic [WW-1:0] lsu_done_w; logic [6:0] ev_lanes;
    logic [SPM_BANKS-1:0]       l_en, l_we, b_en, b_we;
    logic [SPM_BANKS*ROW_W-1:0] l_row, b_row;
    logic [SPM_BANKS*32-1:0]    l_wd, b_wd, b_rd;
    tfx_lsu #(.NW(NW), .NL(NL), .BANKS(SPM_BANKS), .SPM_AW(SPM_AW)) u_lsu (
        .clk(clk), .rst_n(rst_n), .start(lsu_start), .op(d_mem), .warp(iss_w), .rd(d_rd), .wr(d_wr), .mask(iss_mask),
        .waddr_flat(lsu_waddr_flat), .sdata_flat(lsu_sdata_flat), .idle(lsu_idle),
        .bank_en(l_en), .bank_we(l_we), .bank_row(l_row), .bank_wdata(l_wd), .bank_rdata(b_rd),
        .wb_valid(lsu_wb_valid), .wb_warp(lsu_wb_w), .wb_rd(lsu_wb_rd), .wb_wr(lsu_wb_wr), .wb_mask(lsu_wb_mask),
        .wb_data(lsu_wb_data), .done_valid(lsu_done_v), .done_warp(lsu_done_w),
        .ev_busy(ev_busy), .ev_round(ev_round), .ev_lane_acc(ev_lanes));

    // Host port shares the bank ports while the cluster is not running.
    logic [LOG2B > 0 ? LOG2B-1 : 0:0] host_bank, host_bank_q;
    wire host_ok = spm_en && !running && spm_addr < 32'(SPM_WORDS);
    always_comb begin
        host_bank = (LOG2B > 0) ? spm_addr[LOG2B > 0 ? LOG2B-1 : 0:0] : '0;
        if (running) begin
            b_en = l_en; b_we = l_we; b_row = l_row; b_wd = l_wd;
        end else begin
            b_en = '0; b_we = '0; b_row = '0; b_wd = '0;
            if (host_ok) begin
                b_en[host_bank] = 1'b1;
                b_we[host_bank] = spm_we;
                b_row[int'(host_bank)*ROW_W +: ROW_W] = spm_addr[SPM_AW-1:LOG2B];
                b_wd[int'(host_bank)*32 +: 32] = spm_wdata;
            end
        end
    end
    always_ff @(posedge clk) host_bank_q <= host_bank;
    assign spm_rdata = b_rd[int'(host_bank_q)*32 +: 32];

    tfx_scratchpad #(.BANKS(SPM_BANKS), .ROWS(SPM_WORDS / SPM_BANKS), .ROW_W(ROW_W)) u_spm (
        .clk(clk), .en(b_en), .we(b_we), .row(b_row), .wdata(b_wd), .rdata(b_rd));

    // ------------------------------------------------------------ scoreboard clears this cycle
    logic [NW*32-1:0] clear;   // flat: bit (32*warp + reg)
    always_comb begin
        clear = '0;
        if (wa_en) clear[32 * int'(iss_w) + int'(d_rd)] = 1'b1;
        if (wm_en) clear[32 * int'(mul_w[MD-1]) + int'(mul_rd[MD-1])] = 1'b1;
        if (wl_en) clear[32 * int'(lsu_wb_w) + int'(lsu_wb_rd)] = 1'b1;
    end

    // ------------------------------------------------------------ issue eligibility
    logic [NW-1:0] elig, is_mem;
    logic          any_haz, any_struct, any_bar, lsu_free;
    always_comb begin
        lsu_free = lsu_idle && !d_is_lsu;
        elig = '0; is_mem = '0; any_haz = 1'b0; any_struct = 1'b0; any_bar = 1'b0;
        for (int w = 0; w < NW; w++) begin
            logic        cand, haz, sok, lsu_i, drain;
            logic [31:0] sbe;
            if (alive[w] && at_bar[w]) any_bar = 1'b1;
            cand  = running && alive[w] && !ctrl_wait[w] && !at_bar[w];
            sbe   = sb[w] & ~clear[w*32 +: 32];
            haz   = !foob[w] && ((f_use_a[w] && sbe[f_ra[w]]) || (f_use_b[w] && sbe[f_rb[w]]) || (f_wr[w] && sbe[f_rd[w]]));
            lsu_i = !foob[w] && f_cls[w] == C_LSU;
            drain = !foob[w] && f_cls[w] == C_CTRL && (f_ctrl[w] == CT_BAR || f_ctrl[w] == CT_EXIT);
            sok   = lsu_i ? lsu_free : (drain ? (inflight[w] == '0) : 1'b1);
            if (cand) begin
                if (haz) any_haz = 1'b1; else if (!sok) any_struct = 1'b1;
                if (!haz && sok) elig[w] = 1'b1;
                if (lsu_i) is_mem[w] = 1'b1;
            end
        end
    end
    logic          sel_v;
    logic [WW-1:0] sel_w;
    tfx_warp_scheduler #(.NW(NW), .WW(WW)) u_sched (
        .policy(policy_q), .eligible(elig), .is_mem(is_mem), .last(last_w), .valid(sel_v), .sel(sel_w));
    wire sel_ctrl = foob[sel_w] || f_cls[sel_w] == C_CTRL;
    // Observation points for issue traces (testbench / debug only; no logic depends on them).
    wire [PCW-1:0] tr_pc   = pc[sel_w];
    wire [31:0]    tr_word = foob[sel_w] ? 32'd0 : fword[sel_w];
    wire [NL-1:0]  tr_mask = mask[sel_w];
    wire [31:0]    tr_cycle = perf[P_CYCLES];

    // ------------------------------------------------------------ control resolution (dispatch)
    logic [NL-1:0] cond;
    logic          ct_fault, ct_pc_we, ct_mask_we, ct_push, ct_pop, ct_bar, ct_exit, ct_div;
    logic [3:0]    ct_code;
    logic [PCW-1:0] ct_pc;
    logic [NL-1:0] ct_mask, top_saved, top_taken;
    wire  [PCW-1:0] br_target = iss_pc + d_imm[PCW-1:0];
    wire  [PCW-1:0] br_next   = iss_pc + 16'd1;
    wire  [4:0]     br_depth  = iss_word[25:21];
    always_comb begin
        for (int l = 0; l < NL; l++) cond[l] = iss_mask[l] && (opa[l] != 32'd0);
        top_saved = (sp[iss_w] != '0) ? stk_saved[int'(iss_w) * STACK_DEPTH + int'(sp[iss_w]) - 1] : '0;
        top_taken = (sp[iss_w] != '0) ? stk_taken[int'(iss_w) * STACK_DEPTH + int'(sp[iss_w]) - 1] : '0;
        ct_fault = 1'b0; ct_code = 4'd0; ct_pc_we = 1'b0; ct_pc = '0; ct_mask_we = 1'b0; ct_mask = '0;
        ct_push = 1'b0; ct_pop = 1'b0; ct_bar = 1'b0; ct_exit = 1'b0; ct_div = 1'b0;
        if (iss_oob) begin ct_fault = 1'b1; ct_code = F_PC_RANGE; end
        else case (d_ctrl_raw)
            CT_JMP, CT_BANY, CT_BNONE: begin
                // structured-branch check: rd field = expected mask-stack depth + 1 (0 = unchecked)
                if (br_depth != 5'd0 && {{(8-SPW){1'b0}}, sp[iss_w]} != {3'd0, br_depth} - 8'd1) begin
                    ct_fault = 1'b1; ct_code = F_BR_DEPTH;
                end else begin
                    ct_pc_we = 1'b1;
                    if (d_ctrl_raw == CT_JMP)       ct_pc = br_target;
                    else if (d_ctrl_raw == CT_BANY) ct_pc = (|cond) ? br_target : br_next;
                    else                            ct_pc = (|cond) ? br_next : br_target;
                end
            end
            CT_IF: begin
                if (int'(sp[iss_w]) >= STACK_DEPTH) begin ct_fault = 1'b1; ct_code = F_STACK_OVF; end
                else begin
                    ct_push = 1'b1; ct_mask_we = 1'b1; ct_mask = cond; ct_pc_we = 1'b1;
                    ct_pc = (|cond) ? br_next : br_target;
                    ct_div = (|cond) && (cond != iss_mask);
                end
            end
            CT_ELSE: begin
                if (sp[iss_w] == '0) begin ct_fault = 1'b1; ct_code = F_STACK_UNF; end
                else begin
                    ct_mask_we = 1'b1; ct_mask = top_saved & ~top_taken; ct_pc_we = 1'b1;
                    ct_pc = ((top_saved & ~top_taken) != '0) ? br_next : br_target;
                end
            end
            CT_ENDIF: begin
                if (sp[iss_w] == '0) begin ct_fault = 1'b1; ct_code = F_STACK_UNF; end
                else begin ct_mask_we = 1'b1; ct_mask = top_saved; ct_pop = 1'b1; ct_pc_we = 1'b1; ct_pc = br_next; end
            end
            CT_BAR:  begin ct_bar = 1'b1; ct_pc_we = 1'b1; ct_pc = br_next; end
            CT_EXIT: begin
                if (sp[iss_w] != '0) begin ct_fault = 1'b1; ct_code = F_EXIT_DIV; end
                else ct_exit = 1'b1;
            end
            default: begin ct_fault = 1'b1; ct_code = F_ILLEGAL; end
        endcase
    end

    // ------------------------------------------------------------ per-warp next-state helpers
    logic [NW*32-1:0]       sb_set;   // flat: bit (32*warp + reg)
    logic [NL-1:0]          msel;
    logic [NW-1:0][IFW-1:0] inflight_next;
    logic                   all_wait, bar_seen, done_now;
    logic [5:0]             sel_pop;
    always_comb begin
        sb_set = '0;
        if (sel_v && !sel_ctrl && f_wr[sel_w]) sb_set[32 * int'(sel_w) + int'(f_rd[sel_w])] = 1'b1;
        for (int w = 0; w < NW; w++) begin
            logic [IFW-1:0] inc, dec;
            inc = (sel_v && !sel_ctrl && int'(sel_w) == w) ? IFW'(1) : '0;
            dec = '0;
            if (d_is_alu && int'(iss_w) == w) dec = dec + IFW'(1);
            if (d_is_lsu && mem_fault && int'(iss_w) == w) dec = dec + IFW'(1);
            if (mul_v[MD-1] && int'(mul_w[MD-1]) == w) dec = dec + IFW'(1);
            if (lsu_done_v && int'(lsu_done_w) == w) dec = dec + IFW'(1);
            inflight_next[w] = inflight[w] + inc - dec;
        end
        all_wait = 1'b1; bar_seen = 1'b0;
        for (int w = 0; w < NW; w++) begin
            if (alive[w] && !at_bar[w]) all_wait = 1'b0;
            if (alive[w] && at_bar[w]) bar_seen = 1'b1;
        end
        done_now = (alive == '0) && !iss_v && (mul_v == '0) && lsu_idle;
        sel_pop = '0; msel = mask[sel_w];
        for (int l = 0; l < NL; l++) sel_pop = sel_pop + {5'd0, msel[l]};
    end

    // ------------------------------------------------------------ sequential update
    always_ff @(posedge clk) begin
        if (!rst_n) begin
            running <= 1'b0; done_pulse <= 1'b0; policy_q <= '0; nwarps_q <= '0; last_w <= WW'(NW - 1);
            launched <= '0; exited <= '0; faulted <= '0; at_bar <= '0; ctrl_wait <= '0;
            pc <= '0; mask <= '0; sp <= '0; stk_saved <= '0; stk_taken <= '0; fcode <= '0; fpc <= '0;
            sb <= '0; inflight <= '0; args <= '0; perf <= '0;
            iss_v <= 1'b0; iss_oob <= 1'b0; iss_w <= '0; iss_pc <= '0; iss_word <= '0; iss_mask <= '0;
            mul_v <= '0; mul_wr <= '0; mul_w <= '0; mul_rd <= '0; mul_mask <= '0;
        end else begin
            done_pulse <= 1'b0;
            if (arg_we && !running) args[arg_idx] <= arg_wdata;
            if (!running) begin
                if (launch) begin
                    running <= 1'b1; policy_q <= launch_policy; nwarps_q <= launch_warps; last_w <= WW'(NW - 1);
                    for (int w = 0; w < NW; w++) launched[w] <= (w < int'(launch_warps));
                    exited <= '0; faulted <= '0; at_bar <= '0; ctrl_wait <= '0;
                    for (int w = 0; w < NW; w++) mask[w] <= FULL;
                    pc <= '0; sp <= '0; fcode <= '0; fpc <= '0; sb <= '0; inflight <= '0; perf <= '0;
                    iss_v <= 1'b0; mul_v <= '0;
                end
            end else begin
                // ---- dispatch: control resolution
                if (d_is_ctl) begin
                    ctrl_wait[iss_w] <= 1'b0;
                    if (ct_fault) begin
                        faulted[iss_w] <= 1'b1; fcode[iss_w] <= ct_code; fpc[iss_w] <= iss_pc;
                    end else begin
                        if (ct_pc_we) pc[iss_w] <= ct_pc;
                        if (ct_mask_we) mask[iss_w] <= ct_mask;
                        if (ct_push) begin
                            stk_saved[int'(iss_w) * STACK_DEPTH + int'(sp[iss_w])] <= iss_mask;
                            stk_taken[int'(iss_w) * STACK_DEPTH + int'(sp[iss_w])] <= cond;
                            sp[iss_w] <= sp[iss_w] + SPW'(1);
                        end
                        if (ct_pop) sp[iss_w] <= sp[iss_w] - SPW'(1);
                        if (ct_exit) exited[iss_w] <= 1'b1;
                    end
                    if (ct_div) perf[P_DIV_IFS] <= perf[P_DIV_IFS] + 32'd1;
                end
                // ---- dispatch: LSU address fault
                if (d_is_lsu && mem_fault) begin
                    faulted[iss_w] <= 1'b1; fcode[iss_w] <= mem_fcode; fpc[iss_w] <= iss_pc;
                end
                // ---- multiplier pipeline
                mul_v[0] <= d_is_mul; mul_w[0] <= iss_w; mul_rd[0] <= d_rd; mul_wr[0] <= d_wr;
                mul_mask[0] <= iss_mask;
                for (int s = 1; s < MD; s++) begin
                    mul_v[s] <= mul_v[s-1]; mul_w[s] <= mul_w[s-1]; mul_rd[s] <= mul_rd[s-1];
                    mul_wr[s] <= mul_wr[s-1]; mul_mask[s] <= mul_mask[s-1];
                end
                // ---- barrier release
                if (all_wait && bar_seen) begin
                    at_bar <= '0;
                    perf[P_BAR_REL] <= perf[P_BAR_REL] + 32'd1;
                end
                if (d_is_ctl && !ct_fault && ct_bar) at_bar[iss_w] <= 1'b1;
                // ---- issue
                perf[P_CYCLES] <= perf[P_CYCLES] + 32'd1;
                iss_v <= sel_v;
                if (sel_v) begin
                    iss_w <= sel_w; iss_pc <= pc[sel_w]; iss_word <= foob[sel_w] ? 32'd0 : fword[sel_w];
                    iss_mask <= mask[sel_w]; iss_oob <= foob[sel_w]; last_w <= sel_w;
                    perf[P_ISSUED] <= perf[P_ISSUED] + 32'd1;
                    if (sel_ctrl) begin
                        ctrl_wait[sel_w] <= 1'b1;
                        perf[P_CTRL] <= perf[P_CTRL] + 32'd1;
                    end else begin
                        pc[sel_w] <= pc[sel_w] + 16'd1;
                        perf[P_THREAD] <= perf[P_THREAD] + {26'd0, sel_pop};
                        case (f_cls[sel_w])
                            C_ALU:   perf[P_ALU] <= perf[P_ALU] + 32'd1;
                            C_MUL:   perf[P_MUL] <= perf[P_MUL] + 32'd1;
                            default: perf[P_MEM] <= perf[P_MEM] + 32'd1;
                        endcase
                    end
                end else if (any_haz)    perf[P_ST_SB]     <= perf[P_ST_SB] + 32'd1;
                else if (any_struct)     perf[P_ST_STRUCT] <= perf[P_ST_STRUCT] + 32'd1;
                else if (any_bar)        perf[P_ST_BAR]    <= perf[P_ST_BAR] + 32'd1;
                else                     perf[P_ST_OTHER]  <= perf[P_ST_OTHER] + 32'd1;
                // ---- LSU events
                if (ev_busy)  perf[P_LSU_BUSY]   <= perf[P_LSU_BUSY] + 32'd1;
                if (ev_round) perf[P_LSU_ROUNDS] <= perf[P_LSU_ROUNDS] + 32'd1;
                perf[P_LSU_LANES] <= perf[P_LSU_LANES] + {25'd0, ev_lanes};
                // ---- scoreboard and in-flight counts
                for (int w = 0; w < NW; w++) begin
                    sb[w] <= (sb[w] & ~clear[w*32 +: 32]) | sb_set[w*32 +: 32];
                    inflight[w] <= inflight_next[w];
                end
                // ---- completion
                if (done_now) begin running <= 1'b0; done_pulse <= 1'b1; end
            end
        end
    end

`ifndef SYNTHESIS
    // ------------------------------------------------------------ assertions (simulation)
    always @(posedge clk) if (rst_n && running) begin
        for (int w = 0; w < NW; w++)
            if (inflight[w] > IFW'(MUL_LAT + 4)) $error("inflight overflow warp %0d", w);
        if (sel_v && !elig[sel_w]) $error("scheduler selected an ineligible warp");
        if (wa_en && wm_en && iss_w == mul_w[MD-1] && d_rd == mul_rd[MD-1]) $error("WAW writeback collision ALU/MUL");
        if (wa_en && wl_en && iss_w == lsu_wb_w && d_rd == lsu_wb_rd) $error("WAW writeback collision ALU/LSU");
        if (wm_en && wl_en && mul_w[MD-1] == lsu_wb_w && mul_rd[MD-1] == lsu_wb_rd) $error("WAW writeback collision MUL/LSU");
        if (lsu_start && !lsu_idle) $error("LSU started while busy");
    end
`endif
endmodule
`default_nettype wire
