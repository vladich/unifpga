// Self-checking testbench for rtl/peripherals/adxl362_reader.sv: an ADXL362
// model (SPI mode 0; 0x0A write, 0x0B read with address auto-increment) whose
// data registers read 0 until POWER_CTL is 0x02. Readings +1000 / -500 / +1 mg,
// then -2000 / +2047 / -1 mg.

`timescale 1ns / 1ps

module adxl362_reader_tb;

    logic clk = 1'b0;
    logic rst = 1'b1;
    always #125 clk = ~ clk;                     // 4 MHz

    wire               sclk, mosi, cs_n;
    logic              miso = 1'b0;
    wire               valid;
    wire signed [15:0] x, y, z;

    adxl362_reader # (.CLK_MHZ (4), .SCLK_KHZ (500), .POLL_HZ (1000), .START_MS (1)) dut
        (.clk (clk), .rst (rst), .sclk (sclk), .mosi (mosi), .miso (miso), .cs_n (cs_n),
         .valid (valid), .x (x), .y (y), .z (z));

    // ---- the device --------------------------------------------------------------

    logic [7:0] regs [0:63];
    logic [7:0] in, cmd, addr, out;
    int         bitc;
    int         writes_before_reads = 0;
    logic       any_read = 1'b0;

    task automatic set_axes (input [15:0] ax, input [15:0] ay, input [15:0] az);
        regs [8'h0E] = ax [7:0]; regs [8'h0F] = ax [15:8];
        regs [8'h10] = ay [7:0]; regs [8'h11] = ay [15:8];
        regs [8'h12] = az [7:0]; regs [8'h13] = az [15:8];
    endtask

    initial
    begin
        for (int i = 0; i < 64; i++) regs [i] = 8'h00;
        set_axes (16'd1000, -16'sd500, 16'd1);
    end

    function automatic [7:0] read_reg (input [7:0] a);
        if (a >= 8'h0E && a <= 8'h13 && regs [8'h2D] != 8'h02)
            read_reg = 8'h00;                    // standby: no measurements
        else
            read_reg = regs [a];
    endfunction

    always @ (negedge cs_n) bitc = 0;

    always @ (posedge sclk)
        if (! cs_n)
        begin
            in = { in [6:0], mosi };
            bitc++;
            if (bitc % 8 == 0)
                case (bitc / 8)
                1: cmd = in;
                2: addr = in;
                default:
                    if (cmd == 8'h0A)
                    begin
                        regs [addr] = in;
                        if (! any_read) writes_before_reads++;
                        addr++;
                    end
                endcase
        end

    always @ (negedge sclk)
        if (! cs_n && cmd == 8'h0B && bitc >= 16)
        begin
            if (bitc % 8 == 0)
            begin
                out = read_reg (addr);
                addr++;
                any_read = 1'b1;
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

        expect_axes (16'sd1000, -16'sd500, 16'sd1);
        if (writes_before_reads != 1 || regs [8'h2D] !== 8'h02)
        begin
            $display ("FAIL POWER_CTL %h after %0d writes", regs [8'h2D], writes_before_reads);
            fails++;
        end

        set_axes (-16'sd2000, 16'sd2047, -16'sd1);
        expect_axes (-16'sd2000, 16'sd2047, -16'sd1);

        if (fails == 0)
            $display ("PASS adxl362_reader");
        $finish;
    end

    initial
    begin
        #100_000_000;
        $display ("FAIL timeout");
        $finish;
    end

endmodule
