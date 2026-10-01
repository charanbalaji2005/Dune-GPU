`default_nettype none
module tfx_fifo #(
    parameter int DATA_W = 32,
    parameter int DEPTH = 4,
    parameter int PTR_W = (DEPTH <= 2) ? 1 : $clog2(DEPTH),
    parameter int COUNT_W = $clog2(DEPTH + 1)
) (
    input logic clk,
    input logic rst_n,
    input logic push,
    input logic [DATA_W-1:0] push_data,
    output logic full,
    input logic pop,
    output logic [DATA_W-1:0] pop_data,
    output logic empty,
    output logic [COUNT_W-1:0] count
);
    logic [DATA_W-1:0] mem [0:DEPTH-1];
    logic [PTR_W-1:0] write_ptr, read_ptr;
    logic do_push, do_pop;

    assign full = (count == COUNT_W'(DEPTH));
    assign empty = (count == 0);
    assign do_push = push && !full;
    assign do_pop = pop && !empty;
    assign pop_data = empty ? '0 : mem[read_ptr];

    function automatic [PTR_W-1:0] next_ptr(input [PTR_W-1:0] ptr);
        if (ptr == PTR_W'(DEPTH-1)) next_ptr = '0;
        else next_ptr = ptr + 1'b1;
    endfunction

    always_ff @(posedge clk) begin
        if (!rst_n) begin
            write_ptr <= '0;
            read_ptr <= '0;
            count <= '0;
        end else begin
            if (do_push) begin
                mem[write_ptr] <= push_data;
                write_ptr <= next_ptr(write_ptr);
            end
            if (do_pop) read_ptr <= next_ptr(read_ptr);
            case ({do_push, do_pop})
                2'b10: count <= count + 1'b1;
                2'b01: count <= count - 1'b1;
                default: count <= count;
            endcase
        end
    end
endmodule
`default_nettype wire
