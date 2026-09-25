// Self-checking testbench for rtl/peripherals/adxl345_i2c_reader.sv: an ADXL345
// model at 0x53 (nothing answers 0x1D) with a register file, pointer writes
// with auto-increment, and burst reads. Its data registers read 0 until
// POWER_CTL is 0x08. Readings +1 g / -0.5 g / +3.9 mg, then -2 g / +2 g / 0.

`timescale 1ns / 1ps

module adxl345_i2c_reader_tb;

    logic clk = 1'b0;
    logic rst = 1'b1;
    always #125 clk = ~ clk;                     // 4 MHz

    wire  scl;
    wire  sda;
    logic dev_low = 1'b0;

    pullup (sda);
    assign sda = dev_low ? 1'b0 : 1'bz;

    wire               valid;
    wire signed [15:0] x, y, z;

    adxl345_i2c_reader # (.CLK_MHZ (4), .SCL_KHZ (100), .POLL_HZ (1000)) dut
        (.clk (clk), .rst (rst), .scl (scl), .sda (sda), .valid (valid), .x (x), .y (y), .z (z));

    // ---- the device: a register file behind an I2C slave ------------------------

    localparam [6:0] ADDR = 7'h53;

    logic [7:0] regs [0:63];
    logic [7:0] pointer;
    logic [7:0] sh, out;
    logic [3:0] n;
    logic       rw, mine, first_byte, master_ack;
    int         st;                              // 0 idle, 1 address, 2 write, 3 read
    int         reads_before_measure = 0;

    task automatic set_axes (input [15:0] ax, input [15:0] ay, input [15:0] az);
        regs [8'h32] = ax [7:0]; regs [8'h33] = ax [15:8];
        regs [8'h34] = ay [7:0]; regs [8'h35] = ay [15:8];
        regs [8'h36] = az [7:0]; regs [8'h37] = az [15:8];
    endtask

    function automatic [7:0] read_reg (input [7:0] a);
        if (a >= 8'h32 && a <= 8'h37 && regs [8'h2D] != 8'h08)
        begin
            read_reg = 8'h00;                    // standby: no measurements
        end
        else
            read_reg = regs [a [5:0]];
    endfunction

    initial
    begin
        for (int i = 0; i < 64; i++) regs [i] = 8'h00;
        regs [8'h00] = 8'hE5;                    // DEVID
        set_axes (16'd256, -16'sd128, 16'd1);
        st = 0;
    end

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
                mine    = (sh [7:1] == ADDR);
                rw      = sh [0];
                dev_low = mine;
            end
            else if (n == 9)
            begin
                dev_low = 0; n = 0;
                if (! mine)
                    st = 0;
                else if (rw)
                begin
                    st = 3;
                    if (pointer == 8'h32 && regs [8'h2D] != 8'h08) reads_before_measure++;
                    out = read_reg (pointer); pointer++; dev_low = ~ out [7];
                end
                else
                begin
                    st = 2; first_byte = 1;
                end
            end
        2:
            if (n == 8)
                dev_low = 1;                     // acknowledge every byte
            else if (n == 9)
            begin
                dev_low = 0; n = 0;
                if (first_byte)
                begin
                    pointer = sh; first_byte = 0;
                end
                else
                begin
                    regs [pointer [5:0]] = sh; pointer++;
                end
            end
        3:
            if (n >= 1 && n <= 7)
                dev_low = ~ out [7 - n];
            else if (n == 8)
                dev_low = 0;
            else if (n == 9)
            begin
                n = 0;
                if (master_ack)
                begin
                    out = read_reg (pointer); pointer++; dev_low = ~ out [7];
                end
                else
                begin
                    st = 0; dev_low = 0;
                end
            end
        endcase

    // ---- the checks --------------------------------------------------------------

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

        expect_axes (16'sd1000, -16'sd500, 16'sd3);   // 256 counts = 1000 mg; 1 count = 3.9 mg
        if (reads_before_measure)
        begin
            $display ("FAIL %0d data reads before POWER_CTL was set", reads_before_measure);
            fails++;
        end
        if (dut.i_master.addr !== ADDR)
        begin
            $display ("FAIL settled on 0x%h, not 0x%h", dut.i_master.addr, ADDR);
            fails++;
        end

        set_axes (-16'sd512, 16'sd512, 16'sd0);
        expect_axes (-16'sd2000, 16'sd2000, 16'sd0);

        if (fails == 0)
            $display ("PASS adxl345_i2c_reader");
        $finish;
    end

    initial begin #200_000_000; $display ("FAIL timeout"); $finish; end

endmodule
