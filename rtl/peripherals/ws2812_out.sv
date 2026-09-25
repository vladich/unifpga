// WS2812 / WS2812B smart RGB LEDs on one data line (a chain of `count`).
//
// The design's RGB LED bits (rgb_leds: one bit per channel and LED) become
// 24-bit GRB frames, MSB first: a channel that is on is `brightness`, off is 0.
// Bit timing from the WS2812B datasheet: a bit lasts 1.25 us, a 0 is high for
// 0.4 us, a 1 for 0.8 us (each within +-150 ns); a frame ends with the line
// low for at least 50 us on WS2812 and 280 us on the newer WS2812B, so 300 us.
// LED 0 is the first in the chain.

module ws2812_out
# (
    parameter clk_mhz    = 50,
              count      = 1,
              brightness = 8'h20
)
(
    input                      clk,
    input                      rst,

    input        [count - 1:0] r,
    input        [count - 1:0] g,
    input        [count - 1:0] b,

    output logic               dout
);

    localparam t_bit   = (clk_mhz * 1250 + 999) / 1000;   // cycles per bit
    localparam t_high0 = (clk_mhz *  400 + 999) / 1000;
    localparam t_high1 = (clk_mhz *  800 + 999) / 1000;
    localparam t_reset = clk_mhz * 300;
    localparam n_bits  = count * 24;

    localparam w_cnt = $clog2 (t_reset + 1);
    localparam w_bit = $clog2 (n_bits + 1);

    logic [w_cnt - 1:0] cnt;       // cycles within the current bit or the reset gap
    logic [w_bit - 1:0] bit_idx;   // bits of the frame sent so far
    logic               in_reset;

    // bit k of the frame: LED k / 24, channels G R B, each MSB first
    function automatic logic frame_bit (input int k);
        int led, pos;
        logic on;
        led = k / 24;
        pos = k % 24;
        on  = pos < 8 ? g [led] : pos < 16 ? r [led] : b [led];
        frame_bit = on & brightness [7 - pos % 8];
    endfunction

    wire cur = frame_bit (bit_idx);

    always_ff @ (posedge clk)
        if (rst)
        begin
            cnt      <= '0;
            bit_idx  <= '0;
            in_reset <= 1'b1;
            dout     <= 1'b0;
        end
        else if (in_reset)
        begin
            dout <= 1'b0;
            if (cnt == w_cnt' (t_reset - 1))
            begin
                cnt      <= '0;
                in_reset <= 1'b0;
                bit_idx  <= '0;
            end
            else
                cnt <= cnt + 1'b1;
        end
        else
        begin
            dout <= cnt < w_cnt' (cur ? t_high1 : t_high0);
            if (cnt == w_cnt' (t_bit - 1))
            begin
                cnt <= '0;
                if (bit_idx == w_bit' (n_bits - 1))
                    in_reset <= 1'b1;
                else
                    bit_idx <= bit_idx + 1'b1;
            end
            else
                cnt <= cnt + 1'b1;
        end

endmodule
