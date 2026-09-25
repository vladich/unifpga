// Self-checking testbench for rtl/peripherals/ds18b20_reader.sv: a DS18B20
// model on an open-drain line — presence after a reset, SKIP ROM / CONVERT T /
// READ SCRATCHPAD decoded from write slots, busy read slots that answer 0 for
// a while then 1, the nine scratchpad bytes with their CRC-8. Readings +25.0 C,
// then -10.5 C, then a corrupted CRC (dropped), then +85 C (the power-on value).

`timescale 1ns / 1ps

module ds18b20_reader_tb;

    logic clk = 1'b0;
    logic rst = 1'b1;
    always #10 clk = ~ clk;                      // 50 MHz

    wire  dq;
    logic dev_low = 1'b0;
    pullup (dq);
    assign dq = dev_low ? 1'b0 : 1'bz;

    wire               valid, found;
    wire signed [15:0] temp;

    ds18b20_reader # (.CLK_MHZ (50), .POLL_MS (40)) dut
        (.clk (clk), .rst (rst), .dq (dq), .valid (valid), .temp (temp), .found (found));

    // ---- the sensor --------------------------------------------------------------

    logic [15:0] reading = 16'h0190;             // 25.0 C
    logic        corrupt = 1'b0;
    logic [7:0]  sp [0:8];
    logic [7:0]  cmd;
    int          nbit, busy_reads, conv_pending = 0, writes = 0;
    logic        reading_scratch = 1'b0;
    int          sp_bit;
    realtime     fell;

    function automatic [7:0] crc8 (input [7:0] c, input b);
        logic x;
        x = c [0] ^ b;
        crc8 = { x, c [7], c [6], c [5] ^ x, c [4] ^ x, c [3], c [2], c [1] };
    endfunction

    task automatic fill_scratchpad;
        logic [7:0] c;
        sp [0] = reading [7:0]; sp [1] = reading [15:8];
        sp [2] = 8'h4B; sp [3] = 8'h46; sp [4] = 8'h7F; sp [5] = 8'hFF; sp [6] = 8'h0C; sp [7] = 8'h10;
        c = 8'h00;
        for (int i = 0; i < 8; i++)
            for (int b = 0; b < 8; b++)
                c = crc8 (c, sp [i][b]);
        sp [8] = corrupt ? c ^ 8'h5A : c;
    endtask

    // every falling edge is a slot (or a reset): a written 1 is high again by
    // 30 us, a written 0 by 65 us, a reset (480 us) is still low then
    always @ (negedge dq)
    if (! dev_low)                               // (not the edges the sensor makes itself)
    begin
        fell = $realtime;
        fork
            begin : slot
                logic s30;
                #(30 * 1000); s30 = (dq !== 1'b0);
                #(35 * 1000);
                if (s30)
                    got_bit (1'b1);
                else if (dq !== 1'b0)
                    got_bit (1'b0);
                else
                begin                            // a reset: answer presence after the release
                    @ (posedge dq);
                    #(30 * 1000); dev_low = 1'b1; #(100 * 1000); dev_low = 1'b0;
                    nbit = 0; cmd = 8'h00; reading_scratch = 1'b0; busy_reads = 0;
                end
            end
        join_none
    end

    task automatic got_bit (input b);
        if (! reading_scratch && busy_reads == 0)
        begin
            cmd = { b, cmd [7:1] };
            nbit++;
            if (nbit == 8)
            begin
                nbit = 0;
                writes++;
                if (cmd == 8'h44) begin conv_pending = 4; busy_reads = 1; end
                if (cmd == 8'hBE) begin fill_scratchpad; reading_scratch = 1'b1; sp_bit = 0; end
            end
        end
    endtask

    // read slots: the master pulls low 2 us and releases; a 0 is held low ~30 us
    always @ (negedge dq)
        if (! dev_low && (busy_reads || reading_scratch))
        begin
            logic b;
            if (busy_reads)
            begin
                b = (conv_pending == 0);
                if (conv_pending > 0) conv_pending--;
                if (b) busy_reads = 0;           // the master saw "done"
            end
            else
            begin
                b = sp [sp_bit / 8][sp_bit % 8];
                sp_bit++;
                if (sp_bit == 72) reading_scratch = 1'b0;
            end
            if (! b)
            begin
                #(1 * 1000); dev_low = 1'b1; #(28 * 1000); dev_low = 1'b0;
            end
        end

    // ---- the checks ----------------------------------------------------------------

    int fails = 0, seen = 0;

    task automatic expect_reading (input signed [15:0] want, input string what);
        @ (posedge valid);
        @ (negedge clk);
        if (temp !== want)
        begin
            $display ("FAIL %s: temp %0d, expected %0d", what, temp, want);
            fails++;
        end
    endtask

    initial
    begin
        repeat (4) @ (posedge clk);
        rst = 1'b0;

        expect_reading (16'sd400, "+25.0 C");
        if (! found) begin $display ("FAIL not found after a reading"); fails++; end
        reading = 16'hFF58;                      // -10.5 C
        expect_reading (-16'sd168, "-10.5 C");
        corrupt = 1'b1;                          // one corrupted scratchpad: no reading
        seen = 0;
        fork
            begin @ (posedge valid); seen = 1; end
            begin #(60_000 * 1000); end
        join_any
        disable fork;
        if (seen) begin $display ("FAIL a reading came through a bad CRC"); fails++; end
        corrupt = 1'b0;
        reading = 16'h0550;                      // +85 C
        expect_reading (16'sd1360, "+85 C");
        if (writes < 6) begin $display ("FAIL only %0d command bytes seen", writes); fails++; end

        if (fails == 0)
            $display ("PASS ds18b20_reader");
        $finish;
    end

    initial begin #2_000_000_000; $display ("FAIL timeout"); $finish; end

endmodule
