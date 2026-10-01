`timescale 1ns/1ps
`default_nettype none
// Exhaustive comparison of tfx_warp_scheduler against tfx::select_warp() (C++).
module tb_scheduler_diff;
    localparam int NW = 4;
    logic [1:0] policy; logic [NW-1:0] eligible, is_mem; logic [1:0] last, sel; logic valid;
    tfx_warp_scheduler #(.NW(NW)) dut(.*);
    integer fd, n, rc, errors, p, l, el, me, ev, es;
    reg [8*1024-1:0] path;
    initial begin
        if (!$value$plusargs("VECTORS=%s", path)) begin $display("ERROR: pass +VECTORS=path"); $fatal(1); end
        fd = $fopen(path, "r"); if (fd == 0) begin $display("ERROR: cannot open vectors"); $fatal(1); end
        n = 0; errors = 0;
        while (!$feof(fd)) begin
            rc = $fscanf(fd, "%h %h %h %h %h %h\n", p, l, el, me, ev, es);
            if (rc == 6) begin
                policy = p[1:0]; last = l[1:0]; eligible = el[NW-1:0]; is_mem = me[NW-1:0]; #1;
                if (valid !== ev[0] || (ev[0] && sel !== es[1:0])) begin
                    if (errors < 10) $display("FAIL pol=%0d last=%0d elig=%b mem=%b got v=%b sel=%0d exp v=%0d sel=%0d",
                                              p, l, eligible, is_mem, valid, sel, ev, es);
                    errors = errors + 1;
                end
                n = n + 1;
            end
        end
        if (errors != 0) begin $display("FAIL: scheduler differential: %0d / %0d", errors, n); $fatal(1); end
        $display("PASS: scheduler differential test (%0d vectors, exhaustive for %0d warps)", n, NW);
        $finish;
    end
endmodule
`default_nettype wire
