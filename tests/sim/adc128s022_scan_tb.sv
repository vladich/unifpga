// Self-checking testbench for rtl/peripherals/adc128s022_scan.sv: an
// ADC128S022 model as its datasheet describes the serial frame (falling edges
// 1-4 leading zeros, 5-16 the result MSB first; the address for the next
// conversion on DIN's bits 5..3 over the first 8 rising edges; IN0 first).
// Channel c reads 12'h100 * c + 12'h0A5 + c; two full scans are checked.

`timescale 1ns / 1ps

module adc128s022_scan_tb;

    logic clk = 1'b0;
    logic rst = 1'b1;
    always #10 clk = ~ clk;                      // 50 MHz

    wire        cs_n, sclk, din;
    logic       dout = 1'b0;
    wire        valid;
    wire [3:0]  channel;
    wire [11:0] value;

    adc128s022_scan # (.CLK_MHZ (50), .SCLK_KHZ (2000)) dut
        (.clk (clk), .rst (rst), .cs_n (cs_n), .sclk (sclk), .din (din), .dout (dout),
         .valid (valid), .channel (channel), .value (value));

    function automatic [11:0] sample (input [2:0] c);
        sample = 12'h100 * c + 12'h0A5 + c;
    endfunction

    // ---- the device --------------------------------------------------------------

    logic [2:0]  conv_addr = 3'd0;               // IN0 first after power-up
    logic [7:0]  ctrl;
    logic [15:0] word;
    int          falls, rises, bad_frames = 0;
    logic        in_frame = 1'b0;                // (cs_n going x -> 1 at the start is no frame end)

    always @ (negedge cs_n)
    begin
        if (sclk !== 1'b1) bad_frames++;         // a frame starts with SCLK high
        falls = 0; rises = 0; in_frame = 1'b1;
        word  = { 4'b0000, sample (conv_addr) };
        dout  = 1'b0;
    end

    always @ (posedge cs_n) if (in_frame)
    begin
        if (rises != 16) bad_frames++;
        conv_addr = ctrl [5:3];
        in_frame  = 1'b0;
    end

    always @ (negedge sclk) if (! cs_n)
    begin
        falls++;
        dout = word [16 - falls];
    end

    always @ (posedge sclk) if (! cs_n)
    begin
        rises++;
        if (rises <= 8) ctrl = { ctrl [6:0], din };
    end

    // ---- the checks ----------------------------------------------------------------

    int fails = 0;

    initial
    begin
        repeat (4) @ (posedge clk);
        rst = 1'b0;
        for (int n = 0; n < 16; n++)
        begin
            @ (posedge valid);
            @ (negedge clk);
            if (channel !== n % 8 || value !== sample (n % 8))
            begin
                $display ("FAIL conversion %0d: channel %0d value %h, expected %0d %h",
                          n, channel, value, n % 8, sample (n % 8));
                fails++;
            end
        end
        if (bad_frames)
        begin
            $display ("FAIL %0d malformed frames", bad_frames);
            fails++;
        end
        if (fails == 0)
            $display ("PASS adc128s022_scan");
        $finish;
    end

    initial begin #50_000_000; $display ("FAIL timeout"); $finish; end

endmodule
