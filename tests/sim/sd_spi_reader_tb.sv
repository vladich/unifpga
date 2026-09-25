// Self-checking testbench for rtl/peripherals/sd_spi_reader.sv against a model
// of an SD card in SPI mode (byte-aligned from chip select; CMD0 with the
// right CRC first; CMD8 answered by a version 2 card; ACMD41 busy a few times;
// CMD58's OCR; CMD17 answered with the data token and 512 bytes a function of
// the block; a card of byte addresses wants CMD16 first): an SDHC card and an
// SDSC one at 50 MHz — the power-up dialogue, blocks read and compared, back to
// back reads, then a card that stops answering (error, power-up again) and no
// card at all.

`timescale 1ns / 1ps

module sd_card_model
# (
    parameter bit SDHC = 1,
    parameter int BUSY_TRIES = 3                     // ACMD41 answers idle this many times
)
(
    input  sclk,
    input  mosi,
    output miso,
    input  cs_n,
    input  answer                                    // 0: the card is gone
);
    int    errors = 0, reads = 0;
    logic  idle = 1, v2_seen = 0, app = 0, ready = 0, blocklen_set = 0;
    int    tries = 0;

    // bits in, MSB first, byte aligned from chip select
    logic [7:0] in_sh = '0;
    int         nbit = 0;
    logic [7:0] cmd [0:5];
    int         cmd_n = -1;                          // bytes of a command collected, -1: none

    // bytes out: a queue the card drains one byte per 8 clocks
    logic [7:0] out_q [$];
    logic [7:0] out_sh = 8'hFF;
    int         out_n = 0;
    logic       miso_r = 1'b1;
    assign miso = miso_r;

    function automatic [7:0] block_byte (input [31:0] blk, input int i);
        block_byte = 8' ((blk * 7 + i) ^ (i >> 3) ^ 8'h5A);
    endfunction

    task automatic respond (input [31:0] arg);
        int index; logic [31:0] a;
        index = cmd [0][5:0];
        a = { cmd [1], cmd [2], cmd [3], cmd [4] };
        out_q.push_back (8'hFF);                     // NCR: one byte
        case (index)
        0:  begin
                if (cmd [5] != 8'h95) begin $display ("MODEL: CMD0 CRC %h", cmd [5]); errors++; end
                idle = 1; ready = 0; out_q.push_back (8'h01);
            end
        8:  begin
                if (cmd [5] != 8'h87) begin $display ("MODEL: CMD8 CRC %h", cmd [5]); errors++; end
                v2_seen = 1;
                out_q.push_back (8'h01); out_q.push_back (8'h00); out_q.push_back (8'h00); out_q.push_back (8'h01); out_q.push_back (8'hAA);
            end
        55: begin app = 1; out_q.push_back (ready ? 8'h00 : 8'h01); end
        41: begin
                if (! app) begin $display ("MODEL: CMD41 without CMD55"); errors++; end
                if (SDHC && ! a [30]) begin $display ("MODEL: ACMD41 without HCS"); errors++; end
                app = 0;
                if (tries < BUSY_TRIES) begin tries++; out_q.push_back (8'h01); end
                else begin ready = 1; idle = 0; out_q.push_back (8'h00); end
            end
        58: begin
                out_q.push_back (ready ? 8'h00 : 8'h01);
                out_q.push_back (SDHC ? 8'hC0 : 8'h80); out_q.push_back (8'hFF); out_q.push_back (8'h80); out_q.push_back (8'h00);
            end
        16: begin
                if (a != 512) begin $display ("MODEL: CMD16 %0d", a); errors++; end
                blocklen_set = 1; out_q.push_back (8'h00);
            end
        17: begin
                logic [31:0] blk;
                if (! ready) begin $display ("MODEL: CMD17 before ready"); errors++; out_q.push_back (8'h05); end
                else if (! SDHC && ! blocklen_set) begin $display ("MODEL: CMD17 without CMD16 on a byte-addressed card"); errors++; out_q.push_back (8'h00); end
                else
                begin
                    blk = SDHC ? a : (a >> 9);
                    if (! SDHC && a [8:0] != 0) begin $display ("MODEL: byte address %h not a block", a); errors++; end
                    reads++;
                    out_q.push_back (8'h00);
                    out_q.push_back (8'hFF); out_q.push_back (8'hFF);          // some access time
                    out_q.push_back (8'hFE);
                    for (int i = 0; i < 512; i++) out_q.push_back (block_byte (blk, i));
                    out_q.push_back (8'h12); out_q.push_back (8'h34);          // the CRC
                end
            end
        default: begin $display ("MODEL: command %0d", index); errors++; out_q.push_back (8'h04); end
        endcase
        if (! app && index != 55) app = 0;
    endtask

    // a byte's first bit is presented as the card is selected and at the
    // falling edge that ends the byte before; the other bits at the falling
    // edges after (mode 0: the master takes them at the rising edges)
    task automatic present_next;
        if (out_n == 0)
        begin
            out_sh = (out_q.size () > 0) ? out_q.pop_front () : 8'hFF;
            out_n  = 8;
        end
        miso_r = out_sh [7];
        out_sh = { out_sh [6:0], 1'b1 };
        out_n--;
    endtask

    always @ (posedge cs_n) begin nbit = 0; cmd_n = -1; out_q.delete (); out_n = 0; miso_r = 1'b1; end
    always @ (negedge cs_n) begin out_n = 0; if (answer) present_next (); end

    always @ (posedge sclk)
        if (! cs_n && answer)
        begin
            in_sh = { in_sh [6:0], mosi };
            nbit++;
            if (nbit == 8)
            begin
                nbit = 0;
                if (cmd_n < 0)
                begin
                    if (in_sh [7:6] == 2'b01) begin cmd [0] = in_sh; cmd_n = 1; end
                end
                else
                begin
                    cmd [cmd_n] = in_sh; cmd_n++;
                    if (cmd_n == 6) begin respond (0); cmd_n = -1; end
                end
            end
        end

    always @ (negedge sclk)
        if (! cs_n && answer)
            present_next ();
        else
            miso_r = 1'b1;

endmodule


module sd_spi_reader_tb;

    int fails = 0;

    `define SD_RIG(NAME, HC)                                                                     \
        logic NAME``_clk = 1'b0, NAME``_rst = 1'b1, NAME``_answer = 1'b1;                          \
        always #10 NAME``_clk = ~ NAME``_clk;                                                      \
        logic NAME``_req = 0; logic [31:0] NAME``_block = '0;                                      \
        wire NAME``_ready, NAME``_valid, NAME``_done, NAME``_error, NAME``_present;                \
        wire [7:0] NAME``_data; wire NAME``_sclk, NAME``_mosi, NAME``_miso, NAME``_cs_n;           \
        sd_spi_reader # (.CLK_MHZ (50), .TIMEOUT_MS (2), .RETRY_MS (1)) NAME``_dut                 \
            (.clk (NAME``_clk), .rst (NAME``_rst), .req (NAME``_req), .block (NAME``_block),       \
             .ready (NAME``_ready), .data (NAME``_data), .valid (NAME``_valid), .done (NAME``_done), \
             .error (NAME``_error), .present (NAME``_present),                                     \
             .sclk (NAME``_sclk), .mosi (NAME``_mosi), .miso (NAME``_miso), .cs_n (NAME``_cs_n));  \
        sd_card_model # (.SDHC (HC)) NAME``_card                                                 \
            (.sclk (NAME``_sclk), .mosi (NAME``_mosi), .miso (NAME``_miso), .cs_n (NAME``_cs_n), .answer (NAME``_answer));

    `SD_RIG (hc, 1)
    `SD_RIG (sc, 0)

    // read one block and check its 512 bytes
    `define READ_BLOCK(NAME, BLK, WHAT)                                                            \
        begin                                                                                      \
            n = 0; bad = 0; saw_done = 0; saw_error = 0;                                           \
            @ (negedge NAME``_clk);                                                                \
            while (! NAME``_ready) @ (negedge NAME``_clk);                                         \
            NAME``_req = 1; NAME``_block = BLK;                                                    \
            @ (negedge NAME``_clk); NAME``_req = 0;                                                \
            while (! saw_done && ! saw_error)                                                      \
            begin                                                                                  \
                @ (posedge NAME``_clk); #1;                                                        \
                if (NAME``_valid) begin if (NAME``_data !== NAME``_card.block_byte (BLK, n)) bad++; n++; end \
                if (NAME``_done) saw_done = 1;                                                     \
                if (NAME``_error) saw_error = 1;                                                   \
            end                                                                                    \
            if (! saw_done || n != 512 || bad != 0)                                                \
            begin $display ("FAIL %s: done %0d, %0d bytes, %0d wrong", WHAT, saw_done, n, bad); fails++; end \
        end

    int   n, bad;
    logic saw_done, saw_error;

    initial
    begin
        repeat (3) @ (posedge hc_clk); hc_rst = 0; sc_rst = 0;

        // the power-up dialogue
        wait (hc_present); wait (sc_present);
        if (! hc_dut.sdhc || sc_dut.sdhc) begin $display ("FAIL address mode: hc %0d sc %0d", hc_dut.sdhc, sc_dut.sdhc); fails++; end
        if (! hc_dut.v2) begin $display ("FAIL the card's version"); fails++; end
        if (hc_dut.fast !== 1'b1) begin $display ("FAIL not at the data clock"); fails++; end

        `READ_BLOCK (hc, 32'd0, "hc block 0")
        `READ_BLOCK (hc, 32'd1234, "hc block 1234")
        `READ_BLOCK (hc, 32'hFFFF_FFFF, "hc the last block")
        `READ_BLOCK (sc, 32'd5, "sc block 5")
        `READ_BLOCK (sc, 32'd0, "sc block 0")
        if (hc_card.reads != 3 || sc_card.reads != 2) begin $display ("FAIL reads seen: %0d, %0d", hc_card.reads, sc_card.reads); fails++; end

        // the card stops answering: an error, then it is back and the power-up runs again
        hc_answer = 0;
        begin
            saw_error = 0;
            @ (negedge hc_clk); hc_req = 1; hc_block = 7; @ (negedge hc_clk); hc_req = 0;
            fork
                begin wait (hc_error); saw_error = 1; end
                # (10_000_000);
            join_any
            disable fork;
            if (! saw_error || hc_present) begin $display ("FAIL no error when the card went away"); fails++; end
        end
        hc_answer = 1;
        wait (hc_present);
        `READ_BLOCK (hc, 32'd8, "hc block 8 after the card came back")

        if (hc_card.errors != 0 || sc_card.errors != 0)
        begin $display ("FAIL the cards saw %0d + %0d protocol errors", hc_card.errors, sc_card.errors); fails++; end
        if (fails == 0)
            $display ("PASS sd_spi_reader");
        $finish;
    end

    initial begin #200_000_000; $display ("FAIL timeout"); $finish; end

endmodule
