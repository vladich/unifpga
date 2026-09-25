// =============================================================================
// THE VIRTUAL DEVICE INTERFACE
//
// This file defines the canonical port list every user-written `design_top`
// targets. The interface is identical on every supported configuration. Per-
// configuration widths (number of switches, presence of a screen, etc.) come
// from `parameter` overrides set by the codegen-generated top module.
//
// Capabilities a particular board lacks are declared with width 0; SystemVerilog
// vectors of width 0 are zero-element arrays (no driver, no consumer), which
// silently optimize away. User code that references e.g. `led[3]` on a board
// with `w_led = 2` produces a synthesis error — which is the correct behaviour:
// the design requires more than the board provides, surface the mismatch.
//
// To write a new design, copy the body of this file into your project as
// `design_top.sv` and add your logic. Never rename the ports or change their
// directions — every board adapter binds to these names.
//
// To declare hard capability requirements that synthesize.py should check
// before building, add a `// requires:` block before the module keyword.
// Example:
//
//     // requires:
//     //   switches >= 4
//     //   leds     >= 4
//     //   buttons  >= 2
//     //   screen   >= 640x480
//     //   audio_in
//     //   serial_console
//
// `synthesize.py` parses that block and fails fast if the chosen configuration
// doesn't meet the requirements.
// =============================================================================

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
    parameter int adc_channels  = 0,     // Analog inputs (A/D converter channels)
    parameter int adc_mv        = 0,     // Their full scale in millivolts (code 4096)

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
    output logic [w_act_level - 1 : 0] act_level,

    // ---- Keyboard (optional): one clock of kbd_valid per key event; kbd_key is
    // the key's USB HID usage code (A = 8'h04, Enter = 8'h28), kbd_down 1 pressed --
    input                            kbd_valid,
    input        [          7 : 0]   kbd_key,
    input                            kbd_down,

    // ---- Temperature (optional): one clock of temp_valid per new reading;
    // temp is two's complement in 1/16 degrees Celsius (400 = 25.0 C) ---------
    input                            temp_valid,
    input        [         15 : 0]   temp,

    // ---- Accelerometer (optional): one clock of acc_valid per new set of
    // readings; each axis two's complement in milli-g ---------------------------
    input                            acc_valid,
    input        [         15 : 0]   acc_x,
    input        [         15 : 0]   acc_y,
    input        [         15 : 0]   acc_z,

    // ---- Analog inputs (optional): one clock of adc_valid per conversion;
    // adc_value is the 12-bit code of channel adc_channel (mV = value * adc_mv / 4096)
    input                            adc_valid,
    input        [          3 : 0]   adc_channel,
    input        [         11 : 0]   adc_value,

    // ---- Infrared remote (optional): one clock of ir_valid per key press
    // (NEC: the remote's address, the key's command byte), ir_repeat while held
    input                            ir_valid,
    input        [         15 : 0]   ir_address,
    input        [          7 : 0]   ir_command,
    input                            ir_repeat
);

    // -------------------------------------------------------------------------
    // Default tie-offs. Override below as needed.
    // -------------------------------------------------------------------------
    assign led      = '0;
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
    assign sd_pixel  = '0;
    assign txt_char  = 8'h20;    // a space
    assign act_on    = '0;
    assign act_level = '0;

    // -------------------------------------------------------------------------
    // User logic goes here.
    // -------------------------------------------------------------------------

endmodule
