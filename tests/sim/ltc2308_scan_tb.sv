// Self-checking testbench for rtl/peripherals/ltc2308_scan.sv: an LTC2308
// model (a CONVST rising edge converts with the configuration latched in the
// previous frame; after 1.6 us with CONVST low SDO shows the MSB, SCK falling
// edges shift out the rest; SDI's 6-bit word latched on the first 6 rising
// edges; the CONVST pulse 20 - 40 ns, as datasheet 2308fc asks). Single-ended unipolar channel c = { S1, S0, O/S } reads
// 12'h100 * c + 12'h05A + c; two full scans are checked.

`timescale 1ns / 1ps

module ltc2308_scan_tb;

    logic clk = 1'b0;
    logic rst = 1'b1;
    always #10 clk = ~ clk;                      // 50 MHz

    wire        convst, sck, sdi;
    logic       sdo = 1'b0;
    wire        valid;
    wire [3:0]  channel;
    wire [11:0] value;

    ltc2308_scan # (.CLK_MHZ (50), .SCK_KHZ (2000)) dut
        (.clk (clk), .rst (rst), .convst (convst), .sck (sck), .sdi (sdi), .sdo (sdo),
         .valid (valid), .channel (channel), .value (value));

    function automatic [11:0] sample (input [2:0] c);
        sample = 12'h100 * c + 12'h05A + c;
    endfunction

    // ---- the device --------------------------------------------------------------

    logic [5:0]  latched = 6'b000000;            // power-up: not a single-ended channel
    logic [5:0]  in;
    logic [11:0] result;
    logic        ready = 1'b0;
    int          k, bad = 0;
    realtime     started;
    logic        pulsed = 1'b0;                  // (convst going x -> 0 at reset is no pulse)

    always @ (negedge convst)                    // 20 ns high at least, low again within 40 ns
        if (pulsed && ($realtime - started < 20 || $realtime - started > 40)) bad++;

    always @ (posedge convst)
    begin
        // { S/D, O/S, S1, S0, UNI, SLP }
        if (latched [5] && latched [1] && ! latched [0])
            result = sample ({ latched [3], latched [2], latched [4] });
        else
            result = 12'hFFF;                    // an unexpected configuration
        ready = 1'b0; k = 0; started = $realtime; pulsed = 1'b1;
        #1600;
        if (convst) bad++;                       // CONVST must be low to read
        ready = 1'b1;
        sdo = result [11];
    end

    always @ (posedge sck)
    begin
        if (! ready) bad++;                      // clocked before the conversion ended
        if (k < 6) in = { in [4:0], sdi };
        k++;
        if (k == 6) latched = in;
    end

    always @ (negedge sck)
        if (k < 12) sdo = result [11 - k];

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
        if (bad)
        begin
            $display ("FAIL %0d timing violations", bad);
            fails++;
        end
        if (fails == 0)
            $display ("PASS ltc2308_scan");
        $finish;
    end

    initial begin #50_000_000; $display ("FAIL timeout"); $finish; end

endmodule
