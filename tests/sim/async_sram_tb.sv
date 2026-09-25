// Self-checking testbench for rtl/peripherals/async_sram.sv against a model
// of an asynchronous 16-bit SRAM (data out while CE and OE are low, a write
// latched at WE's rising edge, byte lanes by LB / UB, the address / data setup
// checked): a 10 ns chip at 50 MHz (a DE2) and a 70 ns one at 100 MHz (a
// Nexys 4's cellular RAM) — words written and read back, byte writes, back to
// back requests.

`timescale 1ns / 1ps

module sram_model
# (
    parameter int ADDR_BITS = 18,
    parameter int ACCESS_NS = 10
)
(
    input  [ADDR_BITS-1:0] a,
    inout  [15:0]          d,
    input                  ce_n,
    input                  oe_n,
    input                  we_n,
    input                  lb_n,
    input                  ub_n
);
    logic [15:0] mem [0:(1 << ADDR_BITS) - 1];
    int          errors = 0;

    // reading: the word is out while CE and OE are low; the model checks that a
    // read (CE and OE low with one address) lasted at least the access time
    // before it ended or the address moved on
    wire reading = ! ce_n && ! oe_n && we_n;
    assign d [7:0]  = (reading && ! lb_n) ? mem [a][7:0]  : 8'hzz;
    assign d [15:8] = (reading && ! ub_n) ? mem [a][15:8] : 8'hzz;
    realtime read_from = 0;
    logic    was_reading = 0;
    always @ (reading or a)
    begin
        if (was_reading && ($realtime - read_from) < ACCESS_NS)
        begin $display ("MODEL: a read of %0t at %0t, shorter than the access time", $realtime - read_from, $realtime); errors++; end
        read_from   = $realtime;
        was_reading = reading;
    end

    // writing: at WE's rising edge, with the address stable since WE fell (a
    // WE-controlled write) and the data set up for most of the access time
    realtime we_fell = 0, d_changed = 0, a_changed = 0;
    always @ (negedge we_n) we_fell = $realtime;
    always @ (d) d_changed = $realtime;
    always @ (a) a_changed = $realtime;
    always @ (posedge we_n)
        if (! ce_n)
        begin
            if ($realtime - we_fell < ACCESS_NS) begin $display ("MODEL: WE pulse of %0t too short", $realtime - we_fell); errors++; end
            if ($realtime - d_changed < ACCESS_NS * 0.6) begin $display ("MODEL: data setup %0t too short", $realtime - d_changed); errors++; end
            if (a_changed > we_fell) begin $display ("MODEL: address changed during the write"); errors++; end
            if (! lb_n) mem [a][7:0]  = d [7:0];
            if (! ub_n) mem [a][15:8] = d [15:8];
        end
endmodule


module async_sram_tb;

    int fails = 0;

    // ---- one chip and its controller ---------------------------------------------

    `define SRAM_RIG(NAME, MHZ, NS, ABITS)                                                   \
        logic NAME``_clk = 1'b0, NAME``_rst = 1'b1;                                          \
        always # (500.0 / MHZ) NAME``_clk = ~ NAME``_clk;                                    \
        logic NAME``_req = 0, NAME``_we = 0; logic [ABITS-1:0] NAME``_addr = '0;             \
        logic [15:0] NAME``_wdata = '0; logic [1:0] NAME``_be = 2'b11;                       \
        wire NAME``_ready, NAME``_ack; wire [15:0] NAME``_rdata;                             \
        wire [ABITS-1:0] NAME``_a; wire [15:0] NAME``_d;                                     \
        wire NAME``_ce_n, NAME``_oe_n, NAME``_we_n, NAME``_lb_n, NAME``_ub_n;                \
        async_sram # (.CLK_MHZ (MHZ), .ADDR_BITS (ABITS), .ACCESS_NS (NS)) NAME``_dut        \
            (.clk (NAME``_clk), .rst (NAME``_rst), .req (NAME``_req), .we (NAME``_we),       \
             .addr (NAME``_addr), .wdata (NAME``_wdata), .be (NAME``_be),                    \
             .ready (NAME``_ready), .ack (NAME``_ack), .rdata (NAME``_rdata),                \
             .sram_a (NAME``_a), .sram_d (NAME``_d), .sram_ce_n (NAME``_ce_n),               \
             .sram_oe_n (NAME``_oe_n), .sram_we_n (NAME``_we_n),                             \
             .sram_lb_n (NAME``_lb_n), .sram_ub_n (NAME``_ub_n));                            \
        sram_model # (.ADDR_BITS (ABITS), .ACCESS_NS (NS)) NAME``_chip                       \
            (.a (NAME``_a), .d (NAME``_d), .ce_n (NAME``_ce_n), .oe_n (NAME``_oe_n),         \
             .we_n (NAME``_we_n), .lb_n (NAME``_lb_n), .ub_n (NAME``_ub_n));

    `SRAM_RIG (fast, 50, 10, 18)     // a DE2: 256K x 16, 10 ns, at 50 MHz
    `SRAM_RIG (slow, 100, 70, 12)    // a cellular RAM (shrunk), 70 ns, at 100 MHz

    // ---- one access: the request set at a falling edge, taken at the rising one ----

    `define ACCESS(NAME, WE, ADDR, WDATA, BE, RDATA)                                         \
        begin                                                                                \
            @ (negedge NAME``_clk);                                                          \
            while (! NAME``_ready) @ (negedge NAME``_clk);                                   \
            NAME``_req = 1; NAME``_we = WE; NAME``_addr = ADDR; NAME``_wdata = WDATA; NAME``_be = BE; \
            @ (negedge NAME``_clk); NAME``_req = 0;                                          \
            while (! NAME``_ack) @ (negedge NAME``_clk);                                     \
            RDATA = NAME``_rdata;                                                            \
        end

    logic [15:0] got;

    task automatic check (input [15:0] have, want, input string what);
        if (have !== want) begin $display ("FAIL %s: %h, expected %h", what, have, want); fails++; end
    endtask

    initial
    begin
        repeat (3) @ (posedge fast_clk); fast_rst = 0;
        repeat (3) @ (posedge slow_clk); slow_rst = 0;

        // words, read back in another order
        for (int i = 0; i < 16; i++) `ACCESS (fast, 1, 18' (i * 4099), 16' (i * 16'h1357 + 16'h0A0A), 2'b11, got)
        for (int i = 15; i >= 0; i--)
        begin
            `ACCESS (fast, 0, 18' (i * 4099), 16'h0000, 2'b11, got)
            check (got, 16' (i * 16'h1357 + 16'h0A0A), $sformatf ("fast word %0d", i));
        end
        // the last address, and a byte write
        `ACCESS (fast, 1, '1, 16'hBEEF, 2'b11, got)
        `ACCESS (fast, 1, '1, 16'h1234, 2'b10, got)        // the high byte only
        `ACCESS (fast, 0, '1, 16'h0000, 2'b11, got)
        check (got, 16'h12EF, "fast byte write");
        // back to back: a write then a read of it, then another word
        `ACCESS (fast, 1, 18'd7, 16'h0F0F, 2'b11, got)
        `ACCESS (fast, 0, 18'd7, 16'h0000, 2'b11, got)
        check (got, 16'h0F0F, "fast back to back");
        `ACCESS (fast, 0, 18'd0, 16'h0000, 2'b11, got)
        check (got, 16'h0A0A, "fast word 0 again");
        if (fast_dut.HOLD != 3) begin $display ("FAIL fast HOLD %0d", fast_dut.HOLD); fails++; end

        // the slow chip
        for (int i = 0; i < 8; i++) `ACCESS (slow, 1, 12' (i * 511), 16' (~i), 2'b11, got)
        for (int i = 0; i < 8; i++)
        begin
            `ACCESS (slow, 0, 12' (i * 511), 16'h0000, 2'b11, got)
            check (got, 16' (~i), $sformatf ("slow word %0d", i));
        end
        `ACCESS (slow, 1, 12'd5, 16'hFFFF, 2'b11, got)
        `ACCESS (slow, 1, 12'd5, 16'h00CC, 2'b01, got)     // the low byte only
        `ACCESS (slow, 0, 12'd5, 16'h0000, 2'b11, got)
        check (got, { 8'hFF, 8'hCC }, "slow byte write");
        if (slow_dut.HOLD != 9) begin $display ("FAIL slow HOLD %0d", slow_dut.HOLD); fails++; end

        if (fast_chip.errors != 0 || slow_chip.errors != 0)
        begin $display ("FAIL the chips saw %0d + %0d timing errors", fast_chip.errors, slow_chip.errors); fails++; end
        if (fails == 0)
            $display ("PASS async_sram");
        $finish;
    end

    initial begin #2_000_000; $display ("FAIL timeout"); $finish; end

endmodule
