// =============================================================================
// 7_1_serial_adder_using_logic_operations_only
// =============================================================================
//
// requires:
//   buttons >= 2
//   seven_segment >= 1

`ifndef SIMULATION

module design_top
`include "design_top_interface.svh"

    // ---- slow_clk derivation --------------------------------------------------
    // design_top has no `slow_clk` port, so derive a ~1 Hz tick from
    // the system clock.
    // clk_mhz <= 1 is the testbench setting (tb.sv passes clk as slow_clk):
    // a two-bit divider keeps the simulation short.
    localparam int W_SLOW_CLK_DIV = (clk_mhz > 1) ? $clog2(clk_mhz * 1_000_000) : 2;
    logic [W_SLOW_CLK_DIV - 1 : 0] slow_clk_div;
    logic                          slow_clk;
    always_ff @(posedge clk or posedge rst)
        if (rst) slow_clk_div <= '0;
        else     slow_clk_div <= slow_clk_div + 1'b1;
    assign slow_clk = slow_clk_div[W_SLOW_CLK_DIV - 1];



    //------------------------------------------------------------------------

    // assign led        = '0;
    // assign abcdefgh   = '0;
    // assign digit      = '0;
       assign red        = '0;
       assign green      = '0;
       assign blue       = '0;
       assign sound      = '0;
       assign uart_tx    = '1;
       assign rgb_r      = '0;
       assign rgb_g      = '0;
       assign rgb_b      = '0;

    //------------------------------------------------------------------------

    wire a = btn [0];
    wire b = btn [1];
    wire sum;

    serial_adder adder
    (
        .clk  ( slow_clk ),
        .rst  ( rst      ),
        .a    ( a        ),
        .b    ( b        ),
        .sum  ( sum      )
    );

    assign led = ({ sum, b, a });

    //------------------------------------------------------------------------

    wire [3:0] a4;

    shift_reg # (4) shift_a
    (
        .clk     ( slow_clk ),
        .rst     ( rst      ),
        .en      ( 1'b1     ),
        .seq_in  ( a        ),
        .seq_out (          ),
        .par_out ( a4       )
    );

    wire [3:0] b4;

    shift_reg # (4) shift_b
    (
        .clk     ( slow_clk ),
        .rst     ( rst      ),
        .en      ( 1'b1     ),
        .seq_in  ( b        ),
        .seq_out (          ),
        .par_out ( b4       )
    );

    wire [3:0] sum4;

    shift_reg # (4) shift_sum
    (
        .clk     ( slow_clk ),
        .rst     ( rst      ),
        .en      ( 1'b1     ),
        .seq_in  ( sum      ),
        .seq_out (          ),
        .par_out ( sum4     )
    );

    //------------------------------------------------------------------------

    localparam w_number = w_digit * 4;
    wire [w_number - 1:0] number = ({ sum4, b4, a4 });

    wire [7:0] abcdefgh_pre;

    seven_segment_display # (w_digit) i_display
    (
        .clk      ( clk          ),
        .number   ( number       ),
        .dots     ( '0           ),
        .abcdefgh ( abcdefgh_pre ),
        .digit    ( digit        ),
        .*
    );

    always_comb
        if (digit & 3'b111)
            abcdefgh = abcdefgh_pre;
        else
            abcdefgh = '0;
endmodule


`endif
