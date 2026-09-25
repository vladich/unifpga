// Self-checking testbench for rtl/peripherals/sdram_sdr.sv against a model of
// an SDR SDRAM (commands decoded at the chip's clock edges; a bank must be
// open for a column command; the power-up sequence — precharge all, eight
// refreshes, the mode register — must come first; read data CAS clocks after
// the READ; DQM masks a write; the refresh rate is watched): a 4M x 16 chip
// (12 row / 8 column bits, a PiSwords6) and a 32M x 16 one (13 / 10, a
// DE10-Lite), both at 50 MHz — words across banks and rows, byte writes, back
// to back requests, refreshes counted over a millisecond.

`timescale 1ns / 1ps

module sdram_model
# (
    parameter int ROW_BITS = 12, COL_BITS = 8, BANK_BITS = 2, DATA_BITS = 16, CAS = 2
)
(
    input                      clk,
    input                      cke,
    input                      cs_n, ras_n, cas_n, we_n,
    input  [BANK_BITS-1:0]     ba,
    input  [ROW_BITS-1:0]      a,
    inout  [DATA_BITS-1:0]     dq,
    input  [DATA_BITS/8-1:0]   dqm
);
    localparam int BANKS = 1 << BANK_BITS;
    localparam int SLOTS = 1024;                        // a sparse memory: the words written so far

    logic [31:0]          keys [0:SLOTS-1];
    logic [DATA_BITS-1:0] vals [0:SLOTS-1];
    int                   used = 0;
    logic                 open_ [0:BANKS-1];
    logic [ROW_BITS-1:0]  row   [0:BANKS-1];
    int                   errors = 0, refreshes = 0, init_refreshes = 0;
    logic                 precharged = 0, mode_set = 0;
    logic [DATA_BITS-1:0] pipe_d [0:7];
    logic                 pipe_v [0:7];
    logic [DATA_BITS-1:0] out_d = '0;
    logic                 out_v = 0;
    assign dq = out_v ? out_d : 'z;

    wire [3:0] cmd = { cs_n, ras_n, cas_n, we_n };
    logic [31:0] word;
    int          slot;

    function automatic int find (input [31:0] w);
        find = -1;
        for (int k = 0; k < used; k++)
            if (keys [k] == w)
                find = k;
    endfunction

    initial for (int b = 0; b < BANKS; b++) begin open_ [b] = 0; row [b] = '0; end
    initial for (int k = 0; k < 8; k++) pipe_v [k] = 0;

    always @ (posedge clk)
        if (cke)
        begin
            // the read pipeline: data CAS clocks after the command, for one clock
            out_v <= pipe_v [0]; out_d <= pipe_d [0];
            for (int k = 0; k < 7; k++) begin pipe_v [k] <= pipe_v [k + 1]; pipe_d [k] <= pipe_d [k + 1]; end
            pipe_v [7] <= 0;
            case (cmd)
            4'b0011:                                     // ACTIVE
            begin
                if (! mode_set) begin $display ("MODEL: ACTIVE before the mode register"); errors++; end
                if (open_ [ba]) begin $display ("MODEL: ACTIVE on an open bank"); errors++; end
                open_ [ba] <= 1; row [ba] <= a;
            end
            4'b0101, 4'b0100:                            // READ / WRITE
            begin
                if (! open_ [ba]) begin $display ("MODEL: column command on a closed bank %0d at %0t", ba, $realtime); errors++; end
                if (! a [10]) begin $display ("MODEL: a column command without auto precharge"); errors++; end
                word = (32' (ba) << (ROW_BITS + COL_BITS)) | (32' (row [ba]) << COL_BITS) | 32' (a [COL_BITS-1:0]);
                slot = find (word);
                if (cmd == 4'b0101)
                begin
                    pipe_v [CAS - 1] <= 1;
                    pipe_d [CAS - 1] <= (slot >= 0) ? vals [slot] : DATA_BITS' (~ word);
                end
                else
                begin
                    if (slot < 0)
                    begin
                        slot = used; used++;
                        keys [slot] = word; vals [slot] = '0;
                    end
                    for (int k = 0; k < DATA_BITS / 8; k++)
                        if (! dqm [k]) vals [slot][8 * k +: 8] = dq [8 * k +: 8];
                end
                open_ [ba] <= 0;                         // auto precharge (the model does not time it)
            end
            4'b0010:                                     // PRECHARGE
            begin
                if (a [10]) for (int b = 0; b < BANKS; b++) open_ [b] <= 0; else open_ [ba] <= 0;
                precharged = 1;
            end
            4'b0001:                                     // AUTO REFRESH
            begin
                for (int b = 0; b < BANKS; b++) if (open_ [b]) begin $display ("MODEL: refresh with a bank open"); errors++; end
                if (! precharged) begin $display ("MODEL: refresh before precharge all"); errors++; end
                if (! mode_set) init_refreshes++; else refreshes++;
            end
            4'b0000:                                     // LOAD MODE REGISTER
            begin
                if (init_refreshes < 8) begin $display ("MODEL: mode register after %0d refreshes", init_refreshes); errors++; end
                if (a [2:0] != 3'b000 || a [6:4] != 3' (CAS)) begin $display ("MODEL: mode register %h", a); errors++; end
                mode_set = 1;
            end
            4'b0111: ;                                   // NOP
            default: begin $display ("MODEL: command %b", cmd); errors++; end
            endcase
        end
endmodule


module sdram_sdr_tb;

    int fails = 0;

    `define SDRAM_RIG(NAME, MHZ, ROWS, COLS, DBITS)                                                     \
        logic NAME``_clk = 1'b0, NAME``_rst = 1'b1;                                                     \
        always # (500.0 / MHZ) NAME``_clk = ~ NAME``_clk;                                               \
        localparam int NAME``_ABITS = 2 + ROWS + COLS;                                                  \
        logic NAME``_req = 0, NAME``_we = 0; logic [NAME``_ABITS-1:0] NAME``_addr = '0;                 \
        logic [DBITS-1:0] NAME``_wdata = '0; logic [DBITS/8-1:0] NAME``_be = '1;                        \
        wire NAME``_ready, NAME``_ack; wire [DBITS-1:0] NAME``_rdata;                                   \
        wire NAME``_sclk, NAME``_cke, NAME``_cs_n, NAME``_ras_n, NAME``_cas_n, NAME``_we_n;             \
        wire [1:0] NAME``_ba; wire [ROWS-1:0] NAME``_a; wire [DBITS-1:0] NAME``_dq; wire [DBITS/8-1:0] NAME``_dqm; \
        sdram_sdr # (.CLK_MHZ (MHZ), .ROW_BITS (ROWS), .COL_BITS (COLS), .DATA_BITS (DBITS)) NAME``_dut \
            (.clk (NAME``_clk), .rst (NAME``_rst), .req (NAME``_req), .we (NAME``_we), .addr (NAME``_addr), \
             .wdata (NAME``_wdata), .be (NAME``_be), .ready (NAME``_ready), .ack (NAME``_ack), .rdata (NAME``_rdata), \
             .sdram_clk (NAME``_sclk), .sdram_cke (NAME``_cke), .sdram_cs_n (NAME``_cs_n), .sdram_ras_n (NAME``_ras_n), \
             .sdram_cas_n (NAME``_cas_n), .sdram_we_n (NAME``_we_n), .sdram_ba (NAME``_ba), .sdram_a (NAME``_a), \
             .sdram_dq (NAME``_dq), .sdram_dqm (NAME``_dqm));                                           \
        sdram_model # (.ROW_BITS (ROWS), .COL_BITS (COLS), .DATA_BITS (DBITS)) NAME``_chip              \
            (.clk (NAME``_sclk), .cke (NAME``_cke), .cs_n (NAME``_cs_n), .ras_n (NAME``_ras_n), .cas_n (NAME``_cas_n), \
             .we_n (NAME``_we_n), .ba (NAME``_ba), .a (NAME``_a), .dq (NAME``_dq), .dqm (NAME``_dqm));

    `SDRAM_RIG (small, 50, 12, 8, 16)      // 4M x 16 (a PiSwords6)
    `SDRAM_RIG (big, 50, 13, 10, 16)       // 32M x 16 (a DE10-Lite)

    // one access: the request set at a falling edge, taken at the rising one
    `define ACCESS(NAME, WE, ADDR, WDATA, BE, RDATA)                                                    \
        begin                                                                                           \
            @ (negedge NAME``_clk);                                                                     \
            while (! NAME``_ready) @ (negedge NAME``_clk);                                              \
            NAME``_req = 1; NAME``_we = WE; NAME``_addr = ADDR; NAME``_wdata = WDATA; NAME``_be = BE;   \
            @ (negedge NAME``_clk); NAME``_req = 0;                                                     \
            while (! NAME``_ack) @ (negedge NAME``_clk);                                                \
            RDATA = NAME``_rdata;                                                                       \
        end

    logic [15:0] got;
    int          r0, r1;

    task automatic check (input [15:0] have, want, input string what);
        if (have !== want) begin $display ("FAIL %s: %h, expected %h", what, have, want); fails++; end
    endtask

    initial
    begin
        repeat (3) @ (posedge small_clk); small_rst = 0; big_rst = 0;

        // the power-up sequence takes 200 us; nothing is ready before
        # (100_000);
        if (small_ready) begin $display ("FAIL ready during the power-up wait"); fails++; end
        while (! small_ready) @ (posedge small_clk);
        if ($realtime < 200_000) begin $display ("FAIL ready after %0t", $realtime); fails++; end

        // words across banks and rows
        for (int i = 0; i < 24; i++) `ACCESS (small, 1, 22' (i * 22'h0A3E7 + 22'h3), 16' (i * 16'h2468 + 16'h11), 2'b11, got)
        for (int i = 23; i >= 0; i--)
        begin
            `ACCESS (small, 0, 22' (i * 22'h0A3E7 + 22'h3), 16'h0000, 2'b11, got)
            check (got, 16' (i * 16'h2468 + 16'h11), $sformatf ("small word %0d", i));
        end
        // the last word; a byte write
        `ACCESS (small, 1, '1, 16'hCAFE, 2'b11, got)
        `ACCESS (small, 1, '1, 16'h5500, 2'b10, got)
        `ACCESS (small, 0, '1, 16'h0000, 2'b11, got)
        check (got, 16'h55FE, "small byte write");
        // back to back on one row and across rows
        `ACCESS (small, 1, 22'd1, 16'h1111, 2'b11, got)
        `ACCESS (small, 1, 22'd2, 16'h2222, 2'b11, got)
        `ACCESS (small, 0, 22'd1, 16'h0000, 2'b11, got)
        check (got, 16'h1111, "small back to back 1");
        `ACCESS (small, 0, 22'd2, 16'h0000, 2'b11, got)
        check (got, 16'h2222, "small back to back 2");
        `ACCESS (small, 0, 22'd3, 16'h0000, 2'b11, got)
        check (got, 16'h0011, "small word 0 again");

        // the big chip: the ends of its address space and a middle bank
        `ACCESS (big, 1, 25'd0, 16'hA5A5, 2'b11, got)
        `ACCESS (big, 1, '1, 16'h5A5A, 2'b11, got)
        `ACCESS (big, 1, 25'h1_2345_6, 16'h0F0F, 2'b11, got)
        `ACCESS (big, 0, 25'd0, 16'h0000, 2'b11, got)
        check (got, 16'hA5A5, "big first word");
        `ACCESS (big, 0, '1, 16'h0000, 2'b11, got)
        check (got, 16'h5A5A, "big last word");
        `ACCESS (big, 0, 25'h1_2345_6, 16'h0000, 2'b11, got)
        check (got, 16'h0F0F, "big middle word");

        // refreshes: a millisecond of idling must see about 64 per 4096 rows
        // (the small chip) and 128 per 8192 (the big one), a tenth early
        r0 = small_chip.refreshes; r1 = big_chip.refreshes;
        # (1_000_000);
        if (small_chip.refreshes - r0 < 64 || small_chip.refreshes - r0 > 80)
        begin $display ("FAIL small: %0d refreshes in a ms", small_chip.refreshes - r0); fails++; end
        if (big_chip.refreshes - r1 < 128 || big_chip.refreshes - r1 > 160)
        begin $display ("FAIL big: %0d refreshes in a ms", big_chip.refreshes - r1); fails++; end
        // and a word survives them
        `ACCESS (small, 0, 22'd2, 16'h0000, 2'b11, got)
        check (got, 16'h2222, "small word after refreshes");

        if (small_chip.errors != 0 || big_chip.errors != 0)
        begin $display ("FAIL the chips saw %0d + %0d protocol errors", small_chip.errors, big_chip.errors); fails++; end
        if (fails == 0)
            $display ("PASS sdram_sdr");
        $finish;
    end

    initial begin #20_000_000; $display ("FAIL timeout"); $finish; end

endmodule
