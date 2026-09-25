// =============================================================================
// 5_5_aps
// =============================================================================
//
// requires:
//   seven_segment >= 1
//   screen >= 320x240

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

    assign sound      = '0;

    assign rgb_r      = '0;

    assign rgb_g      = '0;

    assign rgb_b      = '0;

    /*
    =====================================================
    CLK/RST adapter
    =====================================================
    */
    localparam APS_SYS_CLK_MHZ = 10;
    localparam APS_VGA_CLK_MHZ = 25;

    logic clk10MHz, clk10MHz_raw;
    logic clk25MHz, clk25MHz_raw;
    logic sync_rst;
    clock_divider #(
        .FAST_CLK_FREQ(clk_mhz          ),
        .SLOW_CLK_FREQ(APS_SYS_CLK_MHZ  )
    ) sys_clk_div (
        .clk_i(clk),
        .aresetn_i(!rst),
        .clk_o(clk10MHz_raw),
        .rst_o(sync_rst)
    );
    clock_divider #(
        .FAST_CLK_FREQ(clk_mhz          ),
        .SLOW_CLK_FREQ(APS_VGA_CLK_MHZ  )
    ) vga_clk_div (
        .clk_i(clk),
        .aresetn_i(!rst),
        .clk_o(clk25MHz_raw),
        .rst_o()
    );
    // the divided clocks onto the clock network (the generated top defines
    // global_clock_buffer for the board; in simulation it is a wire)
    global_clock_buffer i_clk10MHz (.in (clk10MHz_raw), .out (clk10MHz));
    global_clock_buffer i_clk25MHz (.in (clk25MHz_raw), .out (clk25MHz));
    //===================================================

    /*
    =====================================================
    LED adapter
    =====================================================
    */
    logic [15:0] aps_led;

generate
    if(w_led > 16) begin
        assign led[w_led-1:16] = '0;
        assign led[15:0] = aps_led;
    end
    else begin
        assign led = aps_led[0+:w_led];
    end
endgenerate
    //===================================================



    /*
    =====================================================
    SW adapter
    =====================================================
    */
    logic [15:0] aps_sw;
generate
    if(w_sw > 16) begin
        assign aps_sw=sw[15:0];
    end
    else begin
        assign aps_sw[0+:w_sw] = sw;
    end
endgenerate
    //===================================================



    /*
    =====================================================
    7seg adapter
    =====================================================
    */
    logic [6:0] hex_led;
    logic [7:0] hex_sel;
    assign abcdefgh   = ~{hex_led, 1'b0};

generate
    if(w_digit > 8) begin
        assign digit[w_digit-1:8] = '0;
        assign digit[7:0] = ~hex_sel;
    end else begin
        assign digit = ~hex_sel[0+:w_digit];
    end
endgenerate
    //===================================================



    /*
    =====================================================
    VGA adapter
    =====================================================
    */
    logic [3:0] vga_r;
    logic [3:0] vga_g;
    logic [3:0] vga_b;
    logic vga_hs;
    logic vga_vs;
generate
    if(w_red > 4) begin
        assign red      = vga_r << (w_red - 4);
    end else begin
        assign red      = vga_r >> (4 - w_red);
    end
    if(w_green > 4) begin
        assign green    = vga_g << (w_green - 4);
    end else begin
        assign green    = vga_g >> (4 - w_green);
    end
    if(w_blue > 4) begin
        assign blue     = vga_b << (w_blue - 4);
    end else begin
        assign blue     = vga_b >> (4 - w_blue);
    end
endgenerate
    //===================================================



    /*
    =====================================================
    PS/2 adapter
    =====================================================
    */
    logic kclk;
    logic kdata;
    //===================================================


    processor_system system(
        .clk10mhz_i     (clk10MHz       ),
        .clk25175khz_i  (clk25MHz       ),
        .rst_i          (sync_rst       ),

        .sw_i           (aps_sw         ),
        .led_o          (aps_led        ),

        .kclk_i         (kclk           ),
        .kdata_i        (kdata          ),

        .hex_led_o      (hex_led        ),
        .hex_sel_o      (hex_sel        ),

        .rx_i           (uart_rx        ),
        .tx_o           (uart_tx        ),

        .vga_r_o        (vga_r          ),
        .vga_g_o        (vga_g          ),
        .vga_b_o        (vga_b          ),
`ifdef INSTANTIATE_GRAPHICS_INTERFACE_MODULE
        .vga_x_i        (x              ),
        .vga_y_i        (y              ),
`else
        .vga_hs_o       (vga_hs         ),
        .vga_vs_o       (vga_vs         ),
`endif
        .tck_i          (),
        .tms_i          (),
        .tdi_i          (),
        .tdo_o          (),
        .tdo_en_o       ()
    );
endmodule

