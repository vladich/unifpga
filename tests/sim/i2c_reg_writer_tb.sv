// Self-checking testbench for rtl/peripherals/i2c_reg_writer.sv (Icarus:
// iverilog -g2012 -o tb tests/sim/i2c_reg_writer_tb.sv rtl/peripherals/i2c_reg_writer.sv).
// A device model decodes the bus into transactions, acknowledges every byte
// except the first address byte to 0x72 (so that entry must be retried), and
// the log is compared with the table, including a restart from entry 1.

`timescale 1 ns / 1 ps

module i2c_reg_writer_tb;

    localparam int QUARTER = 10;                      // CLK_MHZ 1, SCL_KHZ 25

    logic clk = 0, rst = 1, restart = 0;
    wire  scl, done;
    tri1  sda;                                        // bus pull-up

    always #500 clk = ~ clk;                          // 1 MHz

    i2c_reg_writer
    # (
        .CLK_MHZ    ( 1  ),
        .SCL_KHZ    ( 25 ),
        .N          ( 3  ),
        .RESTART_AT ( 1  ),
        .TABLE      ( { 24'h341E00, 24'h729803, 24'h72AF16 } )
    )
    dut (.clk, .rst, .restart, .scl, .sda, .done);

    // ---- device model --------------------------------------------------------

    logic       drive_low = 0;
    assign sda = drive_low ? 1'b0 : 1'bz;

    bit         in_tx, refused_once;
    int         bitcnt;
    logic [7:0] sh;
    logic [23:0] bytes;
    int          nbytes;
    logic [31:0] log_ [0:15];                         // {byte count, bytes left-aligned}
    int          nlog = 0;

    always @ (negedge sda) if (scl === 1'b1) begin in_tx = 1; bitcnt = 0; nbytes = 0; bytes = '0; end

    always @ (posedge sda)
        if (scl === 1'b1 && in_tx)
        begin
            in_tx = 0;
            log_ [nlog] = { 8'(nbytes), bytes };
            nlog ++;
        end

    always @ (posedge scl) if (in_tx && bitcnt < 8) begin sh = { sh [6:0], sda === 1'b0 ? 1'b0 : 1'b1 }; bitcnt ++; end

    always @ (negedge scl)
        if (in_tx)
        begin
            if (bitcnt == 8)
            begin
                bytes [23 - nbytes * 8 -: 8] = sh;
                nbytes ++;
                if (nbytes == 1 && sh == 8'h72 && ! refused_once)
                    refused_once = 1;                 // NACK: no drive
                else
                    drive_low = 1;
                bitcnt = 9;
            end
            else if (bitcnt == 9)
            begin
                drive_low = 0;
                bitcnt    = 0;
            end
        end

    // ---- SCL period while a byte is sent ----------------------------------------

    realtime last_rise = 0;
    int      bad_period = 0;
    always @ (posedge scl)
    begin
        if (in_tx && last_rise > 0 && $realtime - last_rise < 2 * 4 * QUARTER * 1000
                                   && $realtime - last_rise != 4 * QUARTER * 1000)
            bad_period ++;
        last_rise = $realtime;
    end

    // ---- stimulus and checks ------------------------------------------------------

    logic [31:0] expected [0:5];

    initial
    begin
        expected [0] = 32'h03_341E00;                 // codec reset
        expected [1] = 32'h01_720000;                 // refused at the address byte ...
        expected [2] = 32'h03_729803;                 // ... and written again
        expected [3] = 32'h03_72AF16;
        expected [4] = 32'h03_729803;                 // restart from entry 1
        expected [5] = 32'h03_72AF16;
    end

    initial
    begin
        repeat (5) @ (posedge clk);
        rst = 0;
        wait (done === 1'b1);
        repeat (100) @ (posedge clk);
        if (nlog != 4) $fatal (1, "FAIL: %0d transactions before restart", nlog);
        restart = 1;
        wait (done === 1'b0);
        restart = 0;
        wait (done === 1'b1);
        repeat (100) @ (posedge clk);
        if (nlog != 6) $fatal (1, "FAIL: %0d transactions", nlog);
        for (int i = 0; i < 6; i ++)
            if (log_ [i] !== expected [i])
                $fatal (1, "FAIL: transaction %0d is %h, expected %h", i, log_ [i], expected [i]);
        if (bad_period != 0) $fatal (1, "FAIL: %0d SCL periods not %0d clocks", bad_period, 4 * QUARTER);
        if (dut.scl !== 1'b1 || sda !== 1'b1) $fatal (1, "FAIL: bus not idle after the table");
        $display ("PASS i2c_reg_writer: %0d transactions", nlog);
        $finish;
    end

    initial begin #20_000_000; $fatal (1, "FAIL: timeout"); end

endmodule
