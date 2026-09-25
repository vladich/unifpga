// The first bytes of an SD card's block on the LEDs and the 7-segment display:
// the switches choose the block (their value; block 0, the boot sector, with
// none), which is read again whenever they change and every second.
//
//   led [0]        a card is present
//   led [1]        the last read failed
//   led [2..]      the block's first byte, its low bits (byte 510 / 511 of the
//                  boot sector are 0x55 0xAA)
//   the 7-segment display: bytes 0 .. of the block, two hex digits each
//
// requires:
//   storage
//   leds >= 2

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

    // ---- Storage (optional) -------------------------------------------------
    parameter int st_blocks     = 0,     // 512-byte blocks, 0 = unknown

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
    output logic [          7 : 0]   abcdefgh,
    output logic [w_digit  - 1 : 0]  digit,

    // ---- RGB LEDs (active-high) ---------------------------------------------
    output logic [w_rgb_led- 1 : 0]  rgb_r,
    output logic [w_rgb_led- 1 : 0]  rgb_g,
    output logic [w_rgb_led- 1 : 0]  rgb_b,

    // ---- Pixel display ------------------------------------------------------
    input        [w_x      - 1 : 0]  x,
    input        [w_y      - 1 : 0]  y,
    output logic [w_red    - 1 : 0]  red,
    output logic [w_green  - 1 : 0]  green,
    output logic [w_blue   - 1 : 0]  blue,

    // ---- Audio in / out -----------------------------------------------------
    input        [         23 : 0]   mic_sample,
    input                            mic_valid,
    output logic [         15 : 0]   sound,

    // ---- Serial console (raw 2-wire UART) -----------------------------------
    input                            uart_rx,
    output logic                     uart_tx,

    // ---- General-purpose I/O ------------------------------------------------
    inout        [w_gpio   - 1 : 0]  gpio,

    // ---- Storage (optional): an SD card, block by block ----------------------
    output logic                     st_req,
    output logic [         31 : 0]   st_block,
    input                            st_ready,
    input        [          7 : 0]   st_data,
    input                            st_valid,
    input                            st_done,
    input                            st_error,
    input                            st_present
);

    assign rgb_r    = '0;
    assign rgb_g    = '0;
    assign rgb_b    = '0;
    assign red      = '0;
    assign green    = '0;
    assign blue     = '0;
    assign sound    = '0;
    assign uart_tx  = 1'b1;

    // ---- the block: the switches' value, read on a change and every second -------

    localparam int W_SEL = (w_sw > 0) ? w_sw : 1;
    localparam int N_SHOWN = (w_digit / 2 > 0) ? w_digit / 2 : 1;     // bytes on the display

    wire [31:0] wanted = (w_sw > 0) ? 32' (sw) : 32'd0;

    logic [31:0] shown_block;
    logic [31:0] second;
    logic        busy;
    logic        failed;
    logic [9:0]  n;
    logic [7:0]  bytes [0:N_SHOWN-1];
    logic [7:0]  first;

    always_ff @ (posedge clk or posedge rst)
        if (rst)
        begin
            st_req      <= 1'b0;
            st_block    <= '0;
            shown_block <= '1;
            second      <= '0;
            busy        <= 1'b0;
            failed      <= 1'b0;
            n           <= '0;
            first       <= '0;
            for (int i = 0; i < N_SHOWN; i++) bytes [i] <= '0;
        end
        else
        begin
            st_req <= 1'b0;
            second <= (second == 32' (clk_mhz) * 32'd1_000_000 - 1) ? '0 : second + 1'b1;

            if (! busy && st_ready && (wanted != shown_block || second == 0))
            begin                                    // ask for the block
                st_req      <= 1'b1;
                st_block    <= wanted;
                shown_block <= wanted;
                busy        <= 1'b1;
                n           <= '0;
            end
            if (busy && st_valid)
            begin
                if (n == 0)
                    first <= st_data;
                if (n < 10' (N_SHOWN))
                    bytes [n [$clog2 (N_SHOWN + 1)-1:0]] <= st_data;
                n <= n + 1'b1;
            end
            if (st_done)
            begin
                busy   <= 1'b0;
                failed <= 1'b0;
            end
            if (st_error)
            begin
                busy   <= 1'b0;
                failed <= 1'b1;
            end
        end

    // ---- the LEDs -------------------------------------------------------------------

    always_comb
    begin
        led = '0;
        if (w_led > 0) led [0] = st_present;
        if (w_led > 1) led [1] = failed;
        for (int i = 2; i < w_led; i++)
            led [i] = first [i - 2];
    end

    // ---- the 7-segment display: the bytes, two digits each, scanned -------------------

    localparam int W_DIG = (w_digit > 1) ? $clog2 (w_digit) : 1;

    logic [15:0]      scan;
    logic [W_DIG-1:0] cur;
    wire  [7:0]       cur_byte = bytes [(w_digit > 0) ? (int' (cur) / 2) % N_SHOWN : 0];
    wire  [3:0]       nibble   = cur [0] ? cur_byte [3:0] : cur_byte [7:4];

    always_ff @ (posedge clk or posedge rst)
        if (rst)
        begin
            scan <= '0;
            cur  <= '0;
        end
        else
        begin
            scan <= scan + 1'b1;
            if (scan == '0)
                cur <= (w_digit > 1 && cur == W_DIG' (w_digit - 1)) ? '0 : cur + 1'b1;
        end

    always_comb
    begin
        case (nibble)
        4'h0: abcdefgh = 8'b1111_1100; 4'h1: abcdefgh = 8'b0110_0000; 4'h2: abcdefgh = 8'b1101_1010; 4'h3: abcdefgh = 8'b1111_0010;
        4'h4: abcdefgh = 8'b0110_0110; 4'h5: abcdefgh = 8'b1011_0110; 4'h6: abcdefgh = 8'b1011_1110; 4'h7: abcdefgh = 8'b1110_0000;
        4'h8: abcdefgh = 8'b1111_1110; 4'h9: abcdefgh = 8'b1111_0110; 4'hA: abcdefgh = 8'b1110_1110; 4'hB: abcdefgh = 8'b0011_1110;
        4'hC: abcdefgh = 8'b1001_1100; 4'hD: abcdefgh = 8'b0111_1010; 4'hE: abcdefgh = 8'b1001_1110; default: abcdefgh = 8'b1000_1110;
        endcase
        digit = '0;
        if (w_digit > 0)
            digit [cur] = 1'b1;
    end

endmodule
