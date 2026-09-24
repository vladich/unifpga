// Self-checking testbench for rtl/peripherals/ps2_keyboard.sv: PS/2 frames at
// 12.5 kHz from a model keyboard; the key events against the expected list.
`timescale 1ns / 1ps

module ps2_keyboard_tb;
    logic clk = 0, rst = 1;
    always #10 clk = ~ clk;                       // 50 MHz

    logic ps2_clk = 1, ps2_data = 1;
    wire valid, down;
    wire [7:0] key;

    ps2_keyboard # (.clk_mhz (50)) dut (.clk, .rst, .ps2_clk, .ps2_data, .valid, .key, .down);

    // one frame: start, 8 data bits LSB first, parity, stop; data changes while
    // the clock is high, the host reads on the falling edge
    task automatic send (input logic [7:0] b, input logic bad_parity = 0, input int bits = 11);
        logic [10:0] f;
        f = { 1'b1, ~ (^ b) ^ bad_parity, b, 1'b0 };
        for (int i = 0; i < bits; i ++)
        begin
            ps2_data = f [i];
            #20_000 ps2_clk = 0;
            #40_000 ps2_clk = 1;
            #20_000;
        end
        ps2_data = 1;
        #200_000;                                  // gap between bytes (> 100 us)
    endtask

    logic [8:0] got [$];                           // { down, key }
    always @ (posedge clk) if (valid) got.push_back ({ down, key });

    logic [8:0] want [$];
    int errors = 0;
    initial begin
        repeat (5) @ (posedge clk);
        rst = 0;
        #100_000;
        send (8'h1C);                              // A down
        send (8'hF0); send (8'h1C);                // A up
        send (8'hE0); send (8'h75);                // Up down
        send (8'hE0); send (8'hF0); send (8'h75);  // Up up
        send (8'hE1); send (8'h14); send (8'h77); send (8'hE1);                  // Pause: nothing
        send (8'hF0); send (8'h14); send (8'hF0); send (8'h77);
        send (8'h12);                              // LShift down
        send (8'h1C, 1);                           // bad parity: dropped
        send (8'h1C, 0, 5);                        // cut off after 5 bits: dropped
        send (8'h1C);                              // A down
        want = '{ {1'b1, 8'h04}, {1'b0, 8'h04}, {1'b1, 8'h52}, {1'b0, 8'h52}, {1'b1, 8'hE1}, {1'b1, 8'h04} };
        if (got.size () != want.size ())
        begin
            $display ("FAIL %0d events, want %0d", got.size (), want.size ()); errors ++;
        end
        for (int i = 0; i < got.size () && i < want.size (); i ++)
            if (got [i] !== want [i])
            begin
                $display ("FAIL event %0d is %h, want %h", i, got [i], want [i]); errors ++;
            end
        if (errors == 0) $display ("PASS ps2_keyboard (%0d events)", got.size ());
        $finish;
    end
endmodule
