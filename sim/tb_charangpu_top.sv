`timescale 1ns/1ps
`default_nettype none
// System-level testbench for charangpu_tfx_top.
//
// Drives a host command stream (+CMDS=file, one 64-bit hex command per line)
// through the ready/valid command port with randomised valid gaps, applies
// random backpressure on the response port, and logs every accepted response
// (+RESP=file). Optional +TRACE=file writes the cluster issue/stall trace in the
// exact format produced by simulator/include/tfx_timing.hpp so the Python
// harness can diff RTL against the C++ cycle model line by line.
//   +SEED=n  +GAP=pct (cmd_valid idle probability)  +BP=pct (resp_ready low)  +MAXCYC=n
module tb_charangpu_top;
    parameter int NW = 4, NL = 8, IMEM_DEPTH = 256, SPM_WORDS = 2048, SPM_BANKS = 8,
                  MUL_LAT = 3, STACK_DEPTH = 8;
    localparam int MAXC = 1 << 18;

    logic clk = 1'b0, rst_n = 1'b0;
    logic cmd_valid = 1'b0, cmd_ready, resp_valid, resp_ready = 1'b0, busy;
    logic [63:0] cmd_data = '0, resp_data;
    always #5 clk = ~clk;

    charangpu_tfx_top #(.NW(NW), .NL(NL), .IMEM_DEPTH(IMEM_DEPTH), .SPM_WORDS(SPM_WORDS), .SPM_BANKS(SPM_BANKS),
                        .MUL_LAT(MUL_LAT), .STACK_DEPTH(STACK_DEPTH)) dut (
        .clk(clk), .rst_n(rst_n), .cmd_valid(cmd_valid), .cmd_ready(cmd_ready), .cmd_data(cmd_data),
        .resp_valid(resp_valid), .resp_ready(resp_ready), .resp_data(resp_data), .busy(busy));

    reg [63:0] cmds [0:MAXC-1];
    reg [8*1024-1:0] cpath, rpath, tpath;
    integer ncmd, idx, idx_n, fd, rfd, tfd, rc, seed, gap, bp, maxcyc, cyc, nresp, idle, r;
    reg [63:0] tmp;
    logic have_trace;

    initial begin
        if (!$value$plusargs("CMDS=%s", cpath)) begin $display("ERROR: +CMDS=file required"); $fatal(1); end
        if (!$value$plusargs("RESP=%s", rpath)) begin $display("ERROR: +RESP=file required"); $fatal(1); end
        have_trace = $value$plusargs("TRACE=%s", tpath);
        if (!$value$plusargs("SEED=%d", seed)) seed = 1;
        if (!$value$plusargs("GAP=%d", gap)) gap = 0;
        if (!$value$plusargs("BP=%d", bp)) bp = 0;
        if (!$value$plusargs("MAXCYC=%d", maxcyc)) maxcyc = 5000000;
        fd = $fopen(cpath, "r"); if (fd == 0) begin $display("ERROR: cannot open commands"); $fatal(1); end
        ncmd = 0;
        while (!$feof(fd)) begin
            rc = $fscanf(fd, "%h\n", tmp);
            if (rc == 1) begin cmds[ncmd] = tmp; ncmd = ncmd + 1; end
        end
        $fclose(fd);
        rfd = $fopen(rpath, "w");
        tfd = have_trace ? $fopen(tpath, "w") : 0;
        idx = 0; cyc = 0; nresp = 0; idle = 0;
        repeat (4) @(posedge clk);
        rst_n <= 1'b1;
    end

    // ---- command driver: valid stays asserted with stable data until accepted
    always @(posedge clk) begin
        if (rst_n) begin
            cyc = cyc + 1;
            idx_n = (cmd_valid && cmd_ready) ? idx + 1 : idx;
            idx = idx_n;
            if (cmd_valid && !cmd_ready) begin
                // hold
            end else begin
                r = $random(seed);
                if (idx_n < ncmd && ($unsigned(r) % 100) >= gap) begin cmd_valid <= 1'b1; cmd_data <= cmds[idx_n]; end
                else cmd_valid <= 1'b0;
            end
            r = $random(seed);
            resp_ready <= ($unsigned(r) % 100) >= bp;
            if (resp_valid && resp_ready) begin
                $fwrite(rfd, "%h\n", resp_data);
                nresp = nresp + 1;
            end
            if (idx >= ncmd && !cmd_valid && !busy) idle = idle + 1; else idle = 0;
            if (idle > 8) begin
                $fclose(rfd); if (tfd != 0) $fclose(tfd);
                $display("TB_DONE commands=%0d responses=%0d tb_cycles=%0d", ncmd, nresp, cyc);
                $finish;
            end
            if (cyc > maxcyc) begin
                $display("ERROR: TB timeout after %0d cycles (idx=%0d/%0d)", cyc, idx, ncmd);
                $fclose(rfd); $fatal(1);
            end
        end
    end

    // ---- issue trace (sampled mid-cycle, after all state has settled)
    always @(negedge clk) begin
        if (rst_n && tfd != 0 && dut.u_cluster.running) begin
            if (dut.u_cluster.sel_v)
                $fwrite(tfd, "%0d issue w=%0d pc=%0d word=%h mask=%0x elig=%0x\n", dut.u_cluster.tr_cycle,
                        dut.u_cluster.sel_w, dut.u_cluster.tr_pc, dut.u_cluster.tr_word, dut.u_cluster.tr_mask,
                        dut.u_cluster.elig);
            else if (dut.u_cluster.any_haz)    $fwrite(tfd, "%0d stall stall_scoreboard\n", dut.u_cluster.tr_cycle);
            else if (dut.u_cluster.any_struct) $fwrite(tfd, "%0d stall stall_structural\n", dut.u_cluster.tr_cycle);
            else if (dut.u_cluster.any_bar)    $fwrite(tfd, "%0d stall stall_barrier\n", dut.u_cluster.tr_cycle);
            else                               $fwrite(tfd, "%0d stall stall_other\n", dut.u_cluster.tr_cycle);
        end
    end
endmodule
`default_nettype wire
