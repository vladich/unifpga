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
# (
    // ---- Clock & reset (always present) -------------------------------------
    parameter int clk_mhz       = 50,    // Advertised frequency of `clk` in MHz

    // ---- User input banks (concatenated across providers) -------------------
    parameter int w_sw          = 0,     // Width of slide-switch bus
    parameter int w_btn         = 0,     // Width of push-button bus

    // ---- User output banks --------------------------------------------------
    parameter int w_led         = 0,     // Width of single-colour LED bus
    parameter int w_digit       = 0,     // Number of 7-segment digit positions
    parameter int w_rgb_led     = 0,     // Number of RGB LEDs

    // ---- Screen (raster-scan pixel display; 0×0 = no screen) ---------------
    parameter int screen_width  = 0,
    parameter int screen_height = 0,
    parameter int w_red         = 0,     // Bits per RED channel
    parameter int w_green       = 0,     // Bits per GREEN channel
    parameter int w_blue        = 0,     // Bits per BLUE channel

    // ---- GPIO ---------------------------------------------------------------
    parameter int w_gpio        = 0,     // Generic bidirectional pin bank

    // ---- Optional capabilities: declare them only when the design uses
    // them (the generated top connects what the design declares) --------------
    parameter int sd_width      = 0,     // Small display (SSD1306 OLED ...), pixels
    parameter int sd_height     = 0,
    parameter int w_sd_pixel    = 1,     // Bits per small-display pixel
    parameter int txt_columns   = 0,     // Character display (HD44780 LCD ...)
    parameter int txt_rows      = 0,
    parameter int w_act         = 0,     // Actuators (relays, servos, PWM outputs)

    // ---- Derived widths (do not override) -----------------------------------
    parameter int w_x = (screen_width  > 0) ? $clog2(screen_width ) : 1,
    parameter int w_y = (screen_height > 0) ? $clog2(screen_height) : 1,
    parameter int w_sd_x = (sd_width  > 1) ? $clog2(sd_width ) : 1,
    parameter int w_sd_y = (sd_height > 1) ? $clog2(sd_height) : 1,
    parameter int w_txt_col = (txt_columns > 1) ? $clog2(txt_columns) : 1,
    parameter int w_txt_row = (txt_rows    > 1) ? $clog2(txt_rows   ) : 1,
    parameter int w_act_level = w_act * 8
)
(
    // ---- Clock & reset ------------------------------------------------------
    input                            clk,
    input                            rst,         // active-high

    // ---- Switches / buttons (active-high — codegen normalizes polarity) ----
    input        [w_sw     - 1 : 0]  sw,
    input        [w_btn    - 1 : 0]  btn,

    // ---- LEDs (active-high) -------------------------------------------------
    output logic [w_led    - 1 : 0]  led,

    // ---- 7-segment display (shared segment lines + digit-select) -----------
    // 8 = 7 segments (a..g) + decimal point. User multiplexes across digits.
    output logic [          7 : 0]   abcdefgh,
    output logic [w_digit  - 1 : 0]  digit,

    // ---- RGB LEDs (active-high) ---------------------------------------------
    output logic [w_rgb_led- 1 : 0]  rgb_r,
    output logic [w_rgb_led- 1 : 0]  rgb_g,
    output logic [w_rgb_led- 1 : 0]  rgb_b,

    // ---- Pixel display ------------------------------------------------------
    // (x, y) drives in from the screen provider; the user computes the pixel
    // colour at that coordinate and drives red/green/blue back out.
    input        [w_x      - 1 : 0]  x,
    input        [w_y      - 1 : 0]  y,
    output logic [w_red    - 1 : 0]  red,
    output logic [w_green  - 1 : 0]  green,
    output logic [w_blue   - 1 : 0]  blue,

    // ---- Audio in (24-bit signed mono samples; valid pulses on new sample) -
    input        [         23 : 0]   mic_sample,
    input                            mic_valid,

    // ---- Audio out (16-bit signed mono samples; one per cycle) -------------
    output logic [         15 : 0]   sound,

    // ---- Serial console (raw 2-wire UART) -----------------------------------
    input                            uart_rx,
    output logic                     uart_tx,

    // ---- General-purpose I/O ------------------------------------------------
    inout        [w_gpio   - 1 : 0]  gpio,

    // ---- Small display (optional): the driver fetches the pixel at
    // (sd_x, sd_y) one clock after presenting it ------------------------------
    input        [w_sd_x   - 1 : 0]  sd_x,
    input        [w_sd_y   - 1 : 0]  sd_y,
    output logic [w_sd_pixel - 1 : 0] sd_pixel,

    // ---- Character display (optional): the character code at
    // (txt_col, txt_row), fetched one clock after the position -----------------
    input        [w_txt_col - 1 : 0] txt_col,
    input        [w_txt_row - 1 : 0] txt_row,
    output logic [          7 : 0]   txt_char,

    // ---- Actuators (optional): an on/off bit and an 8-bit level each -------
    output logic [w_act    - 1 : 0]  act_on,
    output logic [w_act_level - 1 : 0] act_level
);

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

    localparam string GREETING = "unifpga  hello! ";

    always_comb
        if (txt_row == 0)
            txt_char = txt_col < GREETING.len () ? GREETING [txt_col] : 8'h20;
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
    assign led      = w_led' (act_on);
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
