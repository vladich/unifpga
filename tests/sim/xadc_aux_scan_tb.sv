// Self-checking testbench for rtl/io/xadc_aux_scan.sv over the XADC stand-in
// of rtl/sim/vendor_stubs.sv (a pin at 1 reads 0xFFF, its negative pin alone
// at 1 reads 0x800, else 0): a Basys 3's table {6, 14, 7, 15} — the sequencer
// converts 6, 7, 14, 15 (pairs 0, 2, 1, 3) and the calibration channel, each
// result must come back on its pair's channel; then the inputs change.

`timescale 1ns / 1ps

module xadc_aux_scan_tb;

    logic clk = 1'b0;
    logic rst = 1'b1;
    always #5 clk = ~ clk;                       // 100 MHz

    logic [3:0] vp = 4'b1010, vn = 4'b0100;
    wire        valid;
    wire [3:0]  channel;
    wire [11:0] value;

    xadc_aux_scan # (.CLK_MHZ (100), .CHANNELS ({ 8'd15, 8'd7, 8'd14, 8'd6 })) dut
        (.clk (clk), .rst (rst), .vaux_p (vp), .vaux_n (vn),
         .valid (valid), .channel (channel), .value (value));

    int          seen [4];
    logic [11:0] got  [4];
    int          fails = 0;

    always @ (posedge clk)
        if (valid)
        begin
            if (channel > 4'd3)
            begin
                $display ("FAIL a conversion on channel %0d", channel);
                fails++;
            end
            else
            begin
                seen [channel]++;
                got  [channel] = value;
            end
        end

    task automatic expect_values (input logic [11:0] v0, v1, v2, v3, input string what);
        if (got [0] !== v0 || got [1] !== v1 || got [2] !== v2 || got [3] !== v3)
        begin
            $display ("FAIL %s: %h %h %h %h, expected %h %h %h %h", what,
                      got [0], got [1], got [2], got [3], v0, v1, v2, v3);
            fails++;
        end
    endtask

    initial
    begin
        if (dut.MASK !== 16'hC0C0) begin $display ("FAIL mask %h", dut.MASK); fails++; end
        if (dut.DIV != 4)          begin $display ("FAIL divider %0d", dut.DIV); fails++; end
        if (dut.i_xadc.INIT_49 !== 16'hC0C0 || dut.i_xadc.INIT_42 !== 16'h0400)
        begin $display ("FAIL XADC configuration"); fails++; end

        repeat (4) @ (posedge clk);
        rst = 1'b0;
        repeat (2000) @ (posedge clk);           // several rounds of 5 conversions x 26 x 4 clocks
        for (int i = 0; i < 4; i++)
            if (seen [i] < 2) begin $display ("FAIL channel %0d converted %0d times", i, seen [i]); fails++; end
        expect_values (12'h000, 12'hFFF, 12'h800, 12'hFFF, "first inputs");

        vp = 4'b0001; vn = 4'b0000;
        repeat (1200) @ (posedge clk);
        expect_values (12'hFFF, 12'h000, 12'h000, 12'h000, "changed inputs");

        if (fails == 0)
            $display ("PASS xadc_aux_scan");
        $finish;
    end

    initial begin #10_000_000; $display ("FAIL timeout"); $finish; end

endmodule
