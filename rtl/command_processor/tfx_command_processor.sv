// Command processor: consumes 64-bit host commands from a FIFO and drives the
// cluster's host interface. Read commands produce 64-bit responses, which are
// held stable until the response channel accepts them (backpressure-safe).
//
// Command  [63:60] op  [59:32] arg (28b)  [31:0] data
//   0 NOP | 1 WRITE_IMEM arg=addr | 2 WRITE_SPM arg=word addr | 3 READ_SPM arg=word addr
//   4 SET_ARG arg=index | 5 LAUNCH data[3:0]=warps data[5:4]=policy (response when done, data=cycles)
//   6 READ_PERF arg=index | 7 READ_FAULT arg=warp (data={code[19:16],pc[15:0]}; codes 1-7 unchanged from v1.0)
//   8 READ_REG arg={warp[12:10],lane[9:5],reg[4:0]} | other -> ERROR response (op 0xF, arg=bad op)
// Response [63:60] op echo (or 0xF)  [59:32] arg echo  [31:0] data
`default_nettype none
module tfx_command_processor (
    input  logic        clk,
    input  logic        rst_n,
    // command FIFO side
    input  logic [63:0] cmd_data,
    input  logic        cmd_empty,
    output logic        cmd_pop,
    // response channel side
    output logic        resp_valid,
    input  logic        resp_ready,
    output logic [63:0] resp_data,
    // cluster host interface
    output logic        imem_we,
    output logic [15:0] imem_addr,
    output logic [31:0] imem_wdata,
    output logic        spm_en,
    output logic        spm_we,
    output logic [31:0] spm_addr,
    output logic [31:0] spm_wdata,
    input  logic [31:0] spm_rdata,
    output logic        arg_we,
    output logic [2:0]  arg_idx,
    output logic [31:0] arg_wdata,
    output logic        launch,
    output logic [3:0]  launch_warps,
    output logic [1:0]  launch_policy,
    input  logic        running,
    input  logic        done_pulse,
    output logic [3:0]  perf_idx,
    input  logic [31:0] perf_value,
    output logic [2:0]  dbg_warp,
    output logic [4:0]  dbg_lane,
    output logic [4:0]  dbg_reg,
    input  logic [31:0] dbg_reg_value,
    input  logic [3:0]  dbg_fault_code,
    input  logic [15:0] dbg_fault_pc,
    output logic        busy
);
    typedef enum logic [2:0] { S_IDLE, S_EXEC, S_SPM_WAIT, S_RESP, S_RUN } state_t;
    state_t st;
    logic [63:0] cmd_q, resp_q;
    wire [3:0]  op  = cmd_q[63:60];
    wire [27:0] arg = cmd_q[59:32];
    wire [31:0] dat = cmd_q[31:0];

    assign cmd_pop    = (st == S_IDLE) && !cmd_empty;
    assign resp_valid = (st == S_RESP);
    assign resp_data  = resp_q;
    assign busy       = (st != S_IDLE) || !cmd_empty;

    // Host-interface drives are decoded combinationally from the latched command in S_EXEC.
    wire exec = (st == S_EXEC);
    assign imem_we       = exec && op == 4'd1;
    assign imem_addr     = arg[15:0];
    assign imem_wdata    = dat;
    assign spm_en        = exec && (op == 4'd2 || op == 4'd3);
    assign spm_we        = exec && op == 4'd2;
    assign spm_addr      = {4'd0, arg};
    assign spm_wdata     = dat;
    assign arg_we        = exec && op == 4'd4;
    assign arg_idx       = arg[2:0];
    assign arg_wdata     = dat;
    assign launch        = exec && op == 4'd5;
    assign launch_warps  = dat[3:0];
    assign launch_policy = dat[5:4];
    assign perf_idx      = (st == S_RUN) ? 4'd0 : arg[3:0];
    assign dbg_warp      = (op == 4'd8) ? arg[12:10] : arg[2:0];
    assign dbg_lane      = arg[9:5];
    assign dbg_reg       = arg[4:0];

    always_ff @(posedge clk) begin
        if (!rst_n) begin
            st <= S_IDLE; cmd_q <= '0; resp_q <= '0;
        end else begin
            case (st)
                S_IDLE: if (!cmd_empty) begin cmd_q <= cmd_data; st <= S_EXEC; end
                S_EXEC: begin
                    case (op)
                        4'd0, 4'd1, 4'd2, 4'd4: st <= S_IDLE;
                        4'd3: st <= S_SPM_WAIT;
                        4'd5: st <= S_RUN;
                        4'd6: begin resp_q <= {op, arg, perf_value}; st <= S_RESP; end
                        4'd7: begin resp_q <= {op, arg, 12'd0, dbg_fault_code, dbg_fault_pc}; st <= S_RESP; end
                        4'd8: begin resp_q <= {op, arg, dbg_reg_value}; st <= S_RESP; end
                        default: begin resp_q <= {4'hF, 24'd0, op, 32'd0}; st <= S_RESP; end
                    endcase
                end
                S_SPM_WAIT: begin resp_q <= {op, arg, spm_rdata}; st <= S_RESP; end
                S_RUN: if (done_pulse) begin resp_q <= {op, arg, perf_value}; st <= S_RESP; end
                S_RESP: if (resp_ready) st <= S_IDLE;
                default: st <= S_IDLE;
            endcase
        end
    end
`ifndef SYNTHESIS
    // Interface contract: a presented response must stay stable until accepted.
    logic [63:0] resp_prev; logic resp_pend;
    always @(posedge clk) begin
        if (!rst_n) resp_pend <= 1'b0;
        else begin
            if (resp_pend && resp_data !== resp_prev) $error("CP response changed while stalled");
            resp_pend <= resp_valid && !resp_ready; resp_prev <= resp_data;
        end
    end
`endif
endmodule
`default_nettype wire
