// =============================================================================
// 5_1_schoolriscv
// =============================================================================
//
// requires:
//   buttons >= 1
//   seven_segment >= 1

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

       assign led        = '0;
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

    wire [ 4:0] regAddr;  // debug access reg address
    wire [31:0] regData;  // debug access reg data
    wire [31:0] imAddr;   // instruction memory address
    wire [31:0] imData;   // instruction memory data

    sr_cpu cpu
    (
        .clk     ( slow_clk ),
        .rst     ( rst      ),
        .regAddr ( regAddr  ),
        .regData ( regData  ),
        .imAddr  ( imAddr   ),
        .imData  ( imData   )
    );

    instruction_rom # (.SIZE (64)) rom
    (
        .a       ( imAddr   ),
        .rd      ( imData   )
    );

    //------------------------------------------------------------------------

    assign regAddr = 5'd10;  // a0

    localparam w_number = w_digit * 4;

    wire [w_number - 1:0] number
        = ( btn [0] ? regData : imAddr );

    seven_segment_display
    # (
        .w_digit  ( w_digit  ),
        .clk_mhz  ( clk_mhz  )
    )
    display
    (
        .clk      ( clk      ),
        .rst      ( rst      ),

        .number   ( number   ),
        .dots     ( '0       ),

        .abcdefgh ( abcdefgh ),
        .digit    ( digit    )
    );
endmodule

