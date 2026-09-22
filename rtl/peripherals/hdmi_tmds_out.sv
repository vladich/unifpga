// =============================================================================
// hdmi_tmds_out — DVI/HDMI output over three TMDS data pairs plus the clock
// pair: BGM's `dvi_top` (sync + TMDS encoders + 10:1 SDR serializers on a
// 10x pixel clock, rtl/peripherals/dvi.sv) followed by one vendor differential
// buffer per pair (rtl/io/diff_obuf.sv). This is the open implementation BGM
// itself ships for the boards where it does not use Gowin's encrypted
// DVI_TX IP (a7_lite_35t, tang_primer_20k_dock_hdmi_tm1638_yosys), with the
// buffers moved out of board_specific_top.sv so the top stays generated.
//
// Clocks (declared by config/peripherals/hdmi_tmds.yml, built by codegen):
//   serial_clk_i  10 x pixel (252 MHz for 640x480 at 25.2 MHz)
//   pixel_clk_i   serial / 10 (Gowin CLKDIV, Xilinx MMCM output, or the board
//                 clock itself when the frequencies coincide — Tang Nano 4K)
//
// Channel order follows the DVI spec and BGM: d[0] = blue, d[1] = green,
// d[2] = red.
// =============================================================================

module hdmi_tmds_out
# (
    parameter DIFF_BUF = "generic"
)
(
    input               serial_clk_i,
    input               pixel_clk_i,
    input               rst_i,

    input        [7:0]  red_i,
    input        [7:0]  green_i,
    input        [7:0]  blue_i,

    output       [9:0]  x_o,
    output       [8:0]  y_o,

    output              tmds_clk_p,
    output              tmds_clk_n,
    output       [2:0]  tmds_d_p,
    output       [2:0]  tmds_d_n
);

    wire red_serial, green_serial, blue_serial;

    dvi_top i_dvi_top
    (
        .serial_clk_i   ( serial_clk_i ),
        .pixel_clk_i    ( pixel_clk_i  ),
        .rst_i          ( rst_i        ),
        .red_i          ( red_i        ),
        .green_i        ( green_i      ),
        .blue_i         ( blue_i       ),
        .x_o            ( x_o          ),
        .y_o            ( y_o          ),
        .red_serial_o   ( red_serial   ),
        .green_serial_o ( green_serial ),
        .blue_serial_o  ( blue_serial  )
    );

    diff_obuf # (.VENDOR (DIFF_BUF)) i_buf_blue  (.i (blue_serial ), .o (tmds_d_p [0]), .ob (tmds_d_n [0]));
    diff_obuf # (.VENDOR (DIFF_BUF)) i_buf_green (.i (green_serial), .o (tmds_d_p [1]), .ob (tmds_d_n [1]));
    diff_obuf # (.VENDOR (DIFF_BUF)) i_buf_red   (.i (red_serial  ), .o (tmds_d_p [2]), .ob (tmds_d_n [2]));
    diff_obuf # (.VENDOR (DIFF_BUF)) i_buf_clk   (.i (pixel_clk_i ), .o (tmds_clk_p  ), .ob (tmds_clk_n  ));

endmodule
