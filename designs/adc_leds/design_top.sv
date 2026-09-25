// Analog input 0 on the LEDs: the top bits of its 12-bit code (an on-board
// A/D converter, or any analog-input provider).
//
// requires:
//   adc
//   leds >= 1

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

    // ---- Analog inputs (optional) --------------------------------------------
    parameter int adc_channels  = 0,     // A/D converter channels
    parameter int adc_mv        = 0,     // Their full scale in millivolts (code 4096)


    // ---- Derived widths (do not override) -----------------------------------
    parameter int w_x = (screen_width  > 0) ? $clog2(screen_width ) : 1,
    parameter int w_y = (screen_height > 0) ? $clog2(screen_height) : 1
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


    // ---- Analog inputs (optional): one clock of adc_valid per conversion;
    // adc_value is the 12-bit code of channel adc_channel (mV = value * adc_mv / 4096)
    input                            adc_valid,
    input        [          3 : 0]   adc_channel,
    input        [         11 : 0]   adc_value
);

    // -------------------------------------------------------------------------
    // Default tie-offs. Override below as needed.
    // -------------------------------------------------------------------------
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

    // -------------------------------------------------------------------------
    // Channel 0 on the LEDs: its code's top bits (a bar graph's worth of it:
    // all LEDs dark at 0 V, the top one lit from half the full scale up).
    // -------------------------------------------------------------------------

    logic [11:0] level;

    always_ff @ (posedge clk)
        if (rst)
            level <= '0;
        else if (adc_valid && adc_channel == 4'd0)
            level <= adc_value;

    always_comb
    begin
        led = '0;
        for (int i = 0; i < w_led && i < 12; i++)
            led [w_led - 1 - i] = level [11 - i];
    end

endmodule
