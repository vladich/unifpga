// Self-checking testbench for rtl/peripherals/adxl345_spi_reader.sv: an ADXL345
// model on 4-wire SPI mode 3 (idle high; MOSI sampled on the rising edge, MISO
// changed on the falling one; command byte = R/W, multiple-byte, 6-bit address)
// with a register file whose data registers read 0 until POWER_CTL is 0x08.
// Readings +1 g / -0.5 g / +3.9 mg, then -2 g / +2 g / 0.

`timescale 1ns / 1ps

module adxl345_spi_reader_tb;

    logic clk = 1'b0;
    logic rst = 1'b1;
    always #10 clk = ~ clk;                      // 50 MHz

    wire               sclk, mosi, cs_n;
    logic              miso = 1'b0;
    wire               valid;
    wire signed [15:0] x, y, z;

    adxl345_spi_reader # (.CLK_MHZ (50), .SCLK_KHZ (1000), .POLL_HZ (1000), .START_MS (1)) dut
        (.clk (clk), .rst (rst), .sclk (sclk), .mosi (mosi), .miso (miso), .cs_n (cs_n),
         .valid (valid), .x (x), .y (y), .z (z));

    // ---- the device --------------------------------------------------------------

    logic [7:0] regs [0:63];
    logic [7:0] in, cmd, out;
    logic [5:0] addr;
    int         bitc, bad = 0, reads_before_measure = 0;

    task automatic set_axes (input [15:0] ax, input [15:0] ay, input [15:0] az);
        regs [8'h32] = ax [7:0]; regs [8'h33] = ax [15:8];
        regs [8'h34] = ay [7:0]; regs [8'h35] = ay [15:8];
        regs [8'h36] = az [7:0]; regs [8'h37] = az [15:8];
    endtask

    function automatic [7:0] read_reg (input [5:0] a);
        if (a >= 6'h32 && a <= 6'h37 && regs [8'h2D] != 8'h08)
            read_reg = 8'h00;                    // standby
        else
            read_reg = regs [a];
    endfunction

    initial
    begin
        for (int i = 0; i < 64; i++) regs [i] = 8'h00;
        regs [8'h00] = 8'hE5;
        set_axes (16'd256, -16'sd128, 16'd1);
    end

    always @ (negedge cs_n)
    begin
        if (sclk !== 1'b1) bad++;                // mode 3: the clock idles high
        bitc = 0;
    end

    always @ (posedge sclk) if (! cs_n)          // the device samples MOSI
    begin
        in = { in [6:0], mosi };
        bitc++;
        if (bitc == 8)
        begin
            cmd  = in;
            addr = in [5:0];
        end
        else if (bitc % 8 == 0 && ! cmd [7])    // a write: data bytes
        begin
            regs [addr] = in;
            if (cmd [6]) addr++;
        end
    end

    always @ (negedge sclk) if (! cs_n && cmd [7] && bitc >= 8)   // the device drives MISO
    begin
        if (bitc % 8 == 0)
        begin
            if (addr == 6'h32 && regs [8'h2D] != 8'h08) reads_before_measure++;
            out = read_reg (addr);
            if (cmd [6]) addr++;
        end
        miso = out [7 - bitc % 8];
    end

    // ---- the checks ----------------------------------------------------------------

    int fails = 0;

    task automatic expect_axes (input signed [15:0] ex, input signed [15:0] ey, input signed [15:0] ez);
        @ (posedge valid);
        @ (negedge clk);
        if (x !== ex || y !== ey || z !== ez)
        begin
            $display ("FAIL axes %0d %0d %0d, expected %0d %0d %0d", x, y, z, ex, ey, ez);
            fails++;
        end
    endtask

    initial
    begin
        repeat (4) @ (posedge clk);
        rst = 1'b0;

        expect_axes (16'sd1000, -16'sd500, 16'sd3);
        if (reads_before_measure || regs [8'h2D] !== 8'h08)
        begin
            $display ("FAIL POWER_CTL %h, %0d reads before it was set", regs [8'h2D], reads_before_measure);
            fails++;
        end
        set_axes (-16'sd512, 16'sd512, 16'sd0);
        expect_axes (-16'sd2000, 16'sd2000, 16'sd0);
        if (bad)
        begin
            $display ("FAIL %0d frames started with the clock low", bad);
            fails++;
        end
        if (fails == 0)
            $display ("PASS adxl345_spi_reader");
        $finish;
    end

    initial begin #100_000_000; $display ("FAIL timeout"); $finish; end

endmodule
