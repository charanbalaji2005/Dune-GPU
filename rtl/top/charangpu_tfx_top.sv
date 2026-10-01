// CharanGPU TFX-X top level (milestone 2):
//   host cmd (ready/valid) -> tfx_fifo -> tfx_command_processor -> tfx_simt_cluster
//   tfx_command_processor -> tfx_token_channel -> host resp (ready/valid)
`default_nettype none
module charangpu_tfx_top #(
    parameter int NW          = 4,
    parameter int NL          = 8,
    parameter int IMEM_DEPTH  = 256,
    parameter int SPM_WORDS   = 2048,
    parameter int SPM_BANKS   = 8,
    parameter int MUL_LAT     = 3,
    parameter int STACK_DEPTH = 8,
    parameter int CMD_DEPTH   = 8
) (
    input  logic        clk,
    input  logic        rst_n,
    input  logic        cmd_valid,
    output logic        cmd_ready,
    input  logic [63:0] cmd_data,
    output logic        resp_valid,
    input  logic        resp_ready,
    output logic [63:0] resp_data,
    output logic        busy
);
    localparam int CW = $clog2(CMD_DEPTH + 1);
    logic fifo_full, fifo_empty, fifo_pop; logic [63:0] fifo_q; logic [CW-1:0] fifo_count;
    assign cmd_ready = !fifo_full;
    tfx_fifo #(.DATA_W(64), .DEPTH(CMD_DEPTH)) u_cmd_fifo (
        .clk(clk), .rst_n(rst_n), .push(cmd_valid), .push_data(cmd_data), .full(fifo_full),
        .pop(fifo_pop), .pop_data(fifo_q), .empty(fifo_empty), .count(fifo_count));

    logic cp_resp_valid, cp_resp_ready; logic [63:0] cp_resp_data;
    tfx_token_channel #(.DATA_W(60), .TAG_W(4)) u_resp_chan (
        .clk(clk), .rst_n(rst_n), .in_valid(cp_resp_valid), .in_ready(cp_resp_ready), .in_token(cp_resp_data),
        .out_valid(resp_valid), .out_ready(resp_ready), .out_token(resp_data));

    logic imem_we, spm_en, spm_we, arg_we, launch, running, done_pulse, cp_busy;
    logic [15:0] imem_addr, dbg_fault_pc; logic [31:0] imem_wdata, spm_addr, spm_wdata, spm_rdata, arg_wdata;
    logic [2:0] arg_idx, dbg_warp; logic [3:0] dbg_fault_code; logic [3:0] launch_warps, perf_idx; logic [1:0] launch_policy;
    logic [31:0] perf_value, dbg_reg_value; logic [4:0] dbg_lane, dbg_reg;

    tfx_command_processor u_cp (
        .clk(clk), .rst_n(rst_n), .cmd_data(fifo_q), .cmd_empty(fifo_empty), .cmd_pop(fifo_pop),
        .resp_valid(cp_resp_valid), .resp_ready(cp_resp_ready), .resp_data(cp_resp_data),
        .imem_we(imem_we), .imem_addr(imem_addr), .imem_wdata(imem_wdata), .spm_en(spm_en), .spm_we(spm_we),
        .spm_addr(spm_addr), .spm_wdata(spm_wdata), .spm_rdata(spm_rdata), .arg_we(arg_we), .arg_idx(arg_idx),
        .arg_wdata(arg_wdata), .launch(launch), .launch_warps(launch_warps), .launch_policy(launch_policy),
        .running(running), .done_pulse(done_pulse), .perf_idx(perf_idx), .perf_value(perf_value),
        .dbg_warp(dbg_warp), .dbg_lane(dbg_lane), .dbg_reg(dbg_reg), .dbg_reg_value(dbg_reg_value),
        .dbg_fault_code(dbg_fault_code), .dbg_fault_pc(dbg_fault_pc), .busy(cp_busy));

    tfx_simt_cluster #(.NW(NW), .NL(NL), .IMEM_DEPTH(IMEM_DEPTH), .SPM_WORDS(SPM_WORDS), .SPM_BANKS(SPM_BANKS),
                       .MUL_LAT(MUL_LAT), .STACK_DEPTH(STACK_DEPTH)) u_cluster (
        .clk(clk), .rst_n(rst_n), .imem_we(imem_we), .imem_addr(imem_addr), .imem_wdata(imem_wdata),
        .spm_en(spm_en), .spm_we(spm_we), .spm_addr(spm_addr), .spm_wdata(spm_wdata), .spm_rdata(spm_rdata),
        .arg_we(arg_we), .arg_idx(arg_idx), .arg_wdata(arg_wdata), .launch(launch), .launch_warps(launch_warps),
        .launch_policy(launch_policy), .running(running), .done_pulse(done_pulse), .perf_idx(perf_idx),
        .perf_value(perf_value), .dbg_warp(dbg_warp), .dbg_lane(dbg_lane), .dbg_reg(dbg_reg),
        .dbg_reg_value(dbg_reg_value), .dbg_fault_code(dbg_fault_code), .dbg_fault_pc(dbg_fault_pc));

    assign busy = cp_busy || running || resp_valid;
endmodule
`default_nettype wire
