// Self-checking testbench for rtl/peripherals/i2c_temperature_reader.sv:
// an ADT7420-like device at 0x4B (the reader must find it past 0x48..0x4A,
// which nobody answers), a pointer write to register 0, then two-byte reads.
// Readings: +25.0 C, then -10.5 C (13-bit, 1/16 C at bit 3). The device then
// stops answering and comes back: the reader finds it again.

`timescale 1ns / 1ps

module i2c_temperature_reader_tb;

    logic clk = 1'b0;
    logic rst = 1'b1;
    always #125 clk = ~ clk;                     // 4 MHz

    wire  scl;
    wire  sda;
    logic dev_low = 1'b0;
    logic present = 1'b1;

    pullup (sda);
    assign sda = dev_low ? 1'b0 : 1'bz;

    wire               valid;
    wire signed [15:0] temp;

    i2c_temperature_reader # (.CLK_MHZ (4), .SCL_KHZ (100), .SHIFT (3), .POLL_MS (1)) dut
        (.clk (clk), .rst (rst), .scl (scl), .sda (sda), .valid (valid), .temp (temp));

    // ---- the device ------------------------------------------------------------

    localparam [6:0] ADDR = 7'h4B;

    logic [15:0] reading = 16'h0C80;             // 25.0 C: 400 sixteenths << 3
    logic [7:0]  pointer = 8'hFF;
    logic [7:0]  sh, out;
    logic [3:0]  n;
    logic        rw, mine, second, master_ack;
    int          st;                             // 0 idle, 1 address, 2 write, 3 read
    int          pointer_writes = 0;

    initial st = 0;

    always @ (negedge sda) if (scl === 1'b1) begin st = 1; n = 0; dev_low = 0; end   // START
    always @ (posedge sda) if (scl === 1'b1) begin st = 0;        dev_low = 0; end   // STOP

    always @ (posedge scl)
    begin
        if ((st == 1 || st == 2) && n < 8)
            sh = { sh [6:0], sda === 1'b0 ? 1'b0 : 1'b1 };
        if (st == 3 && n == 8)
            master_ack = (sda === 1'b0);
        n = n + 1;
    end

    always @ (negedge scl)
        case (st)
        1:
            if (n == 8)
            begin
                mine    = present && sh [7:1] == ADDR;
                rw      = sh [0];
                dev_low = mine;
            end
            else if (n == 9)
            begin
                dev_low = 0;
                n = 0;
                if (! mine)
                    st = 0;
                else if (rw)
                begin
                    if (pointer != 8'h00)
                        $display ("FAIL read before the pointer was set to 0");
                    st = 3; second = 0; out = reading [15:8]; dev_low = ~ out [7];
                end
                else
                    st = 2;
            end
        2:
            if (n == 8)
                dev_low = 1;
            else if (n == 9)
            begin
                dev_low = 0; n = 0; pointer = sh; pointer_writes++;
            end
        3:
            if (n >= 1 && n <= 7)
                dev_low = ~ out [7 - n];
            else if (n == 8)
                dev_low = 0;
            else if (n == 9)
            begin
                n = 0;
                if (master_ack && ! second)
                begin
                    second = 1; out = reading [7:0]; dev_low = ~ out [7];
                end
                else
                begin
                    if (master_ack)
                        $display ("FAIL the reader acknowledged the last byte");
                    st = 0; dev_low = 0;
                end
            end
        endcase

    // ---- the checks --------------------------------------------------------------

    int fails = 0;

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
        if (dut.i_master.addr !== ADDR)
        begin
            $display ("FAIL the reader settled on 0x%h, not 0x%h", dut.i_master.addr, ADDR);
            fails++;
        end

        reading = 16'hFAC0;                      // -10.5 C: -168 sixteenths << 3
        expect_reading (-16'sd168, "-10.5 C");

        present = 1'b0;                          // gone: the reader looks for it again
        repeat (40000) @ (posedge clk);
        if (dut.i_master.found)
        begin
            $display ("FAIL the reader still thinks the device is there");
            fails++;
        end
        present = 1'b1;
        reading = 16'h0000;
        expect_reading (16'sd0, "0.0 C after the device came back");

        if (pointer_writes < 3)
        begin
            $display ("FAIL only %0d pointer writes", pointer_writes);
            fails++;
        end

        if (fails == 0)
            $display ("PASS i2c_temperature_reader");
        $finish;
    end

    initial
    begin
        #200_000_000;
        $display ("FAIL timeout");
        $finish;
    end

endmodule
