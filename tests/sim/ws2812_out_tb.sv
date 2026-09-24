// Self-checking testbench for rtl/peripherals/ws2812_out.sv: two chained LEDs,
// every bit's high time and period against the WS2812B datasheet, the frame's
// GRB bits against the inputs, and the reset gap between frames.
`timescale 1ns / 1ps

module ws2812_out_tb;
    localparam clk_mhz = 50, count = 2;
    localparam logic [7:0] brightness = 8'b1010_0101;

    logic clk = 0, rst = 1;
    always #10 clk = ~ clk;                       // 50 MHz

    logic [count - 1:0] r = 2'b01, g = 2'b10, b = 2'b11;
    wire dout;

    ws2812_out # (.clk_mhz (clk_mhz), .count (count), .brightness (brightness))
        dut (.clk, .rst, .r, .g, .b, .dout);

    // the frame expected: LED 0 then 1, G R B, MSB first
    logic [count * 24 - 1:0] want;
    initial begin
        for (int led = 0; led < count; led ++)
        begin
            want [(count - 1 - led) * 24 + 16 +: 8] = g [led] ? brightness : 8'h00;
            want [(count - 1 - led) * 24 +  8 +: 8] = r [led] ? brightness : 8'h00;
            want [(count - 1 - led) * 24 +  0 +: 8] = b [led] ? brightness : 8'h00;
        end
    end

    int errors = 0, nbit = 0, frames = 0;
    bit seen_rise = 0;               // X -> 0 at start-up is a negedge too: ignored
    realtime rise = 0, last_rise = 0, fall = 0;

    always @ (posedge dout)
    begin
        // a gap of 280 us or more ends a frame
        if (nbit > 0 && $realtime - fall >= 280_000)
        begin
            if (nbit != count * 24) begin $display("FAIL frame of %0d bits", nbit); errors ++; end
            frames ++;
            nbit = 0;
        end
        else if (nbit > 0 && ($realtime - last_rise < 1100 || $realtime - last_rise > 1400))
        begin
            $display("FAIL bit period %0t ns", $realtime - last_rise); errors ++;
        end
        last_rise = $realtime;
        rise = $realtime;
        seen_rise = 1;
    end

    always @ (negedge dout)
    begin : fall_edge
        realtime high;
        logic one;
        if (!seen_rise) disable fall_edge;
        high = $realtime - rise;
        fall = $realtime;
        if (high >= 250 && high <= 550)      one = 0;
        else if (high >= 650 && high <= 950) one = 1;
        else begin $display("FAIL high time %0t ns", high); errors ++; one = 0; end
        if (nbit < count * 24 && one !== want [count * 24 - 1 - nbit])
        begin
            $display("FAIL bit %0d is %0d, want %0d", nbit, one, want [count * 24 - 1 - nbit]); errors ++;
        end
        nbit ++;
    end

    initial begin
        repeat (5) @ (posedge clk);
        rst = 0;
        #1_000_000;                               // 1 ms: at least two whole frames
        if (frames < 1) begin $display("FAIL no complete frame"); errors ++; end
        if (errors == 0) $display("PASS ws2812_out (%0d frames)", frames);
        $finish;
    end
endmodule
