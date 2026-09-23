// =============================================================================
// 5_2_schoolriscv_cache
// =============================================================================
//
// requires:
//   seven_segment >= 1

module design_top
# (
    parameter int clk_mhz       = 50,
                  w_sw          = 0,
                  w_btn         = 0,
                  w_led         = 0,
                  w_digit       = 0,
                  w_rgb_led     = 0,
                  screen_width  = 0,
                  screen_height = 0,
                  w_red         = 0,
                  w_green       = 0,
                  w_blue        = 0,
                  w_gpio        = 0,
                  w_x = (screen_width  > 0) ? $clog2(screen_width ) : 1,
                  w_y = (screen_height > 0) ? $clog2(screen_height) : 1
)
(
    input                            clk,
    input                            rst,
    input        [w_sw     - 1 : 0]  sw,
    input        [w_btn    - 1 : 0]  btn,
    output logic [w_led    - 1 : 0]  led,
    output logic [          7 : 0]   abcdefgh,
    output logic [w_digit  - 1 : 0]  digit,
    output logic [w_rgb_led- 1 : 0]  rgb_r,
    output logic [w_rgb_led- 1 : 0]  rgb_g,
    output logic [w_rgb_led- 1 : 0]  rgb_b,
    input        [w_x      - 1 : 0]  x,
    input        [w_y      - 1 : 0]  y,
    output logic [w_red    - 1 : 0]  red,
    output logic [w_green  - 1 : 0]  green,
    output logic [w_blue   - 1 : 0]  blue,
    input        [         23 : 0]   mic_sample,
    input                            mic_valid,
    output logic [         15 : 0]   sound,
    input                            uart_rx,
    output logic                     uart_tx,
    inout        [w_gpio   - 1 : 0]  gpio
);


    //------------------------------------------------------------------------

       assign led        = '0;
    // assign abcdefgh   = '0;
    // assign digit      = '0;
       assign red        = '0;
       assign green      = '0;
       assign blue       = '0;
       assign sound      = '0;
       assign uart_tx    = '1;

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

