// =============================================================================
// Lab 09 — Hex counter on the 7-segment display
//
// Drives the shared-segment 7-segment display with a hex value that increments slowly
// enough to be visible. Uses a tiny inline 7-seg multiplexer/decoder so the
// design is self-contained (no external helper module).
// =============================================================================
//
// requires:
//   seven_segment >= 4

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
                  w_y = (screen_height > 0) ? $clog2(screen_height) : 1,
                  // Bits needed to divide clk down to ~1 Hz
                  w_div  = $clog2(clk_mhz * 1_000_000),
                  // 4 bits per hex digit
                  w_value = (w_digit > 0) ? w_digit * 4 : 4
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

    assign led      = '0;
    assign rgb_r    = '0;
    assign rgb_g    = '0;
    assign rgb_b    = '0;
    assign red      = '0;
    assign green    = '0;
    assign blue     = '0;
    assign sound    = '0;
    assign uart_tx  = 1'b1;

    // ---- Slow tick (~1 Hz) and the visible value counter ---------------
    logic [w_div - 1 : 0]   div_cnt;
    logic [w_value - 1 : 0] number;
    always_ff @(posedge clk or posedge rst) begin
        if (rst) begin
            div_cnt <= '0;
            number  <= '0;
        end else begin
            div_cnt <= div_cnt + 1'b1;
            if (div_cnt == '0)
                number <= number + 1'b1;
        end
    end

    // ---- Multiplex the digits at refresh rate (kHz region) --------------
    localparam int w_refresh = $clog2(clk_mhz * 1000);
    logic [w_refresh - 1 : 0] refresh_cnt;
    always_ff @(posedge clk or posedge rst)
        if (rst) refresh_cnt <= '0;
        else     refresh_cnt <= refresh_cnt + 1'b1;

    wire [$clog2(w_digit) - 1 : 0] active_digit
        = refresh_cnt[w_refresh - 1 -: $clog2(w_digit)];

    always_comb begin
        digit = '0;
        digit[active_digit] = 1'b1;
    end

    // ---- Hex-to-7-segment encoder (a..g pattern, dp = 0) ---------------
    wire [3:0] hex_value = (w_digit > 0) ? number[active_digit*4 +: 4] : 4'h0;
    always_comb begin
        case (hex_value)
            4'h0: abcdefgh = 8'b1111_1100;
            4'h1: abcdefgh = 8'b0110_0000;
            4'h2: abcdefgh = 8'b1101_1010;
            4'h3: abcdefgh = 8'b1111_0010;
            4'h4: abcdefgh = 8'b0110_0110;
            4'h5: abcdefgh = 8'b1011_0110;
            4'h6: abcdefgh = 8'b1011_1110;
            4'h7: abcdefgh = 8'b1110_0000;
            4'h8: abcdefgh = 8'b1111_1110;
            4'h9: abcdefgh = 8'b1111_0110;
            4'hA: abcdefgh = 8'b1110_1110;
            4'hB: abcdefgh = 8'b0011_1110;
            4'hC: abcdefgh = 8'b1001_1100;
            4'hD: abcdefgh = 8'b0111_1010;
            4'hE: abcdefgh = 8'b1001_1110;
            4'hF: abcdefgh = 8'b1000_1110;
            default: abcdefgh = '0;
        endcase
    end

endmodule
