// Self-checking testbench for rtl/peripherals/ir_nec_receiver.sv: NEC frames
// as a demodulating receiver gives them (low during a burst): a frame with
// address 0x00FF / command 0x45, an extended-address frame 0x1234 / 0xA6, two
// repeats, then a frame whose inverted command is wrong (dropped) and a frame
// with a too-short leading burst (dropped).

`timescale 1ns / 1ps

module ir_nec_receiver_tb;

    logic clk = 1'b0;
    logic rst = 1'b1;
    always #10 clk = ~ clk;                      // 50 MHz

    logic ir = 1'b1;                             // idle high
    wire         valid, repeat_;
    wire  [15:0] address;
    wire  [7:0]  command;

    ir_nec_receiver # (.CLK_MHZ (50)) dut
        (.clk (clk), .rst (rst), .ir (ir), .valid (valid), .address (address), .command (command), .repeat_ (repeat_));

    task automatic burst (input int us);   ir = 1'b0; #(us * 1000); ir = 1'b1; endtask
    task automatic space (input int us);   #(us * 1000); endtask

    task automatic send_bits (input [31:0] bits);
        for (int i = 0; i < 32; i++)
        begin
            burst (562);
            space (bits [i] ? 1687 : 562);
        end
    endtask

    task automatic send_frame (input [15:0] addr, input [7:0] cmd, input [7:0] cmd_inv);
        burst (9000); space (4500);
        send_bits ({ cmd_inv, cmd, addr });
        burst (562);
        space (40000);
    endtask

    task automatic send_repeat;
        burst (9000); space (2250); burst (562); space (96000);
    endtask

    int frames = 0, repeats = 0, fails = 0;
    logic [15:0] last_addr;
    logic [7:0]  last_cmd;

    always @ (posedge clk)
    begin
        if (valid)   begin frames++;  last_addr = address; last_cmd = command; end
        if (repeat_) repeats++;
    end

    initial
    begin
        repeat (4) @ (posedge clk);
        rst = 1'b0;
        space (1000);

        send_frame (16'h00FF, 8'h45, ~8'h45);
        if (frames != 1 || last_addr !== 16'h00FF || last_cmd !== 8'h45)
        begin $display ("FAIL frame 1: %0d frames, address %h command %h", frames, last_addr, last_cmd); fails++; end

        send_frame (16'h1234, 8'hA6, ~8'hA6);
        if (frames != 2 || last_addr !== 16'h1234 || last_cmd !== 8'hA6)
        begin $display ("FAIL frame 2: %0d frames, address %h command %h", frames, last_addr, last_cmd); fails++; end

        send_repeat; send_repeat;
        if (repeats != 2 || frames != 2)
        begin $display ("FAIL repeats: %0d repeats, %0d frames", repeats, frames); fails++; end

        send_frame (16'h00FF, 8'h45, 8'h45);       // a wrong inverted command
        burst (3000); space (4500); send_bits (32'h0); burst (562); space (40000);   // a short lead
        if (frames != 2 || repeats != 2)
        begin $display ("FAIL bad frames accepted: %0d frames, %0d repeats", frames, repeats); fails++; end

        if (fails == 0)
            $display ("PASS ir_nec_receiver");
        $finish;
    end

    initial begin #2_000_000_000; $display ("FAIL timeout"); $finish; end

endmodule
