// =============================================================================
// 5_2_schoolriscv_cache
// =============================================================================
//
// requires:
//   seven_segment >= 1

module design_top
`include "design_top_interface.svh"


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

    wire  [31:0] imAddr;        // instruction memory address
    wire  [31:0] imData;        // instruction memory data

    logic [ 4:0] regAddr;       // debug access reg address
    wire  [31:0] regData;       // debug access reg data

    wire  [31:0] cycleCntPerf;  // clk counter for evaluation program time execution

    sr_soc # (
        .CACHE_EN (0)           // 1 - enable cache, 0 - disable (block cl_hit signal in the sr_icache module)
    )
    soc
    (
        .clk        ( clk          ),
        .rst        ( rst          ),

        .soc_addr_o ( imAddr       ),
        .soc_data_i ( imData       ),

        .regAddr    ( regAddr      ),
        .regData    ( regData      ),

        .cycleCnt_o ( cycleCntPerf )
    );

    instruction_rom # (
        .SIZE (1024)
    )
    rom
    (
        .a       ( imAddr  ),
        .rd      ( imData  )
    );

    assign regAddr = 5'd10;  // a0 address line of the cpu register file

    //------------------------------------------------------------------------

    localparam w_number = w_digit * 4;

    wire [w_number - 1:0] number
        = ( cycleCntPerf );

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

