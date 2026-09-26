// A small display, a character display and actuators together: the
// character LCD greets and counts seconds in hex, the OLED draws a frame and
// a sweeping bar, switch i turns actuator i on and the actuators' levels
// (servo positions) sweep back and forth.
//
// requires:
//   small_display
//   text_display >= 16x2
//   actuators >= 1

module design_top
`include "design_top_interface.svh"

    // ---- a second counter ---------------------------------------------------
    localparam int CYC_S = clk_mhz * 1000 * 1000;
    logic [$clog2(CYC_S + 1) - 1:0] cyc;
    logic [31:0] seconds;
    logic [23:0] fast;          // ~ 6 Hz .. 12 Hz steps for the animations

    always_ff @ (posedge clk)
        if (rst)
        begin
            cyc     <= '0;
            seconds <= '0;
            fast    <= '0;
        end
        else
        begin
            fast <= fast + 1'd1;
            if (cyc == CYC_S - 1)
            begin
                cyc     <= '0;
                seconds <= seconds + 1'd1;
            end
            else
                cyc <= cyc + 1'd1;
        end

    // ---- character display: a greeting and the seconds in hex ---------------
    function automatic [7:0] hex (input [3:0] v);
        hex = v < 10 ? 8'h30 + v : 8'h41 + v - 10;
    endfunction

    // a string literal is packed MSB first: character c at bits [8 * (n-1-c) +: 8]
    localparam           n_greeting = 16;
    localparam [8 * n_greeting - 1:0] greeting = "unifpga  hello! ";

    always_comb
        if (txt_row == 0)
            txt_char = txt_col < n_greeting ? greeting [8 * (n_greeting - 1 - txt_col) +: 8] : 8'h20;
        else if (txt_col < 8)
            txt_char = hex (seconds [4 * (7 - txt_col) +: 4]);
        else
            txt_char = 8'h20;

    // ---- small display: a frame and a bar that sweeps across ----------------
    wire [w_sd_x - 1:0] bar = w_sd_x' (fast [23 -: 8] % (sd_width > 0 ? sd_width : 1));

    assign sd_pixel = w_sd_pixel' (   sd_x == 0 || sd_x == sd_width  - 1
                                   || sd_y == 0 || sd_y == sd_height - 1
                                   || sd_x == bar);

    // ---- actuators: switch i turns actuator i on, the levels sweep ----------
    wire [7:0] sweep = fast [23] ? ~ fast [22 -: 8] : fast [22 -: 8];

    for (genvar i = 0; i < w_act; i++)
    begin : g_act
        if (i < w_sw)
            assign act_on [i] = sw [i];
        else
            assign act_on [i] = 1'b1;
        assign act_level [8 * i +: 8] = sweep + 8' (i * 64);
    end

    // ---- the rest -----------------------------------------------------------
    assign led      = act_on;   // extended or cut to the rig's LEDs
    assign abcdefgh = '0;
    assign digit    = '0;
    assign rgb_r    = '0;
    assign rgb_g    = '0;
    assign rgb_b    = '0;
    assign red      = '0;
    assign green    = '0;
    assign blue     = '0;
    assign sound    = '0;
    assign uart_tx  = 1'b1;

endmodule
