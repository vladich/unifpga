// =============================================================================
// sd_spi_reader — an SD / microSD card in SPI mode, read block by block for
// the `storage` capability.
//
// Power-up, at 400 kHz: 80 clocks with the card deselected, then CMD0 (idle;
// its response R1 = 0x01), CMD8 (0x1AA: a version 2 card echoes it, a version
// 1 card calls it illegal), ACMD41 (CMD55 + CMD41 with HCS, repeated until the
// card leaves idle), CMD58 (the OCR: CCS says the card takes block addresses),
// and CMD16 (512-byte blocks) for a card of byte addresses. Then `present` and
// `ready`, at SCLK_KHZ. A request (`req` with `block` while ready): CMD17 with
// the block's address, the data token 0xFE, 512 bytes each one clock of
// `valid`, two CRC bytes ignored, then `done`. A card that stops answering
// (a response overdue) gives `error` and the power-up starts again; with no
// card the power-up retries every RETRY_MS.
//
// SPI mode 0: MOSI changes on SCLK's falling edge, MISO is taken on the rising
// one; the card is deselected between commands, with eight extra clocks.
// =============================================================================

module sd_spi_reader
# (
    parameter int CLK_MHZ     = 50,
    parameter int SCLK_KHZ    = 12500,    // the data clock (the cards take up to 25 MHz)
    parameter int TIMEOUT_MS  = 500,      // a response overdue
    parameter int RETRY_MS    = 250       // between power-up attempts without a card
)
(
    input               clk,
    input               rst,

    input               req,
    input        [31:0] block,
    output              ready,
    output logic [7:0]  data,
    output logic        valid,
    output logic        done,
    output logic        error,
    output logic        present,

    output logic        sclk,
    output              mosi,
    input               miso,
    output logic        cs_n
);

    // ---- the SPI byte engine: one byte per `start`, mode 0 -----------------------------

    // half periods, in clocks: 400 kHz for the power-up, SCLK_KHZ after
    localparam int SLOW_HALF = (CLK_MHZ * 1000 + 799) / 800;
    localparam int FAST_HALF = (CLK_MHZ * 1000 + 2 * SCLK_KHZ - 1) / (2 * SCLK_KHZ);
    localparam int MS_CLOCKS = CLK_MHZ * 1000;

    logic        fast;
    logic        start;                  // one clock: shift `tx` out
    logic [7:0]  tx, rx, sh;
    logic        busy;
    logic        byte_done;              // one clock: rx holds the byte
    logic [15:0] half_cnt;
    logic [2:0]  nbit;
    wire  [15:0] half = fast ? 16' (FAST_HALF > 0 ? FAST_HALF : 1) : 16' (SLOW_HALF > 0 ? SLOW_HALF : 1);

    assign mosi = busy ? sh [7] : 1'b1;

    always_ff @ (posedge clk or posedge rst)
        if (rst)
        begin
            sclk      <= 1'b0;
            busy      <= 1'b0;
            sh        <= 8'hFF;
            rx        <= '0;
            half_cnt  <= '0;
            nbit      <= '0;
            byte_done <= 1'b0;
        end
        else
        begin
            byte_done <= 1'b0;
            if (start)
            begin
                busy     <= 1'b1;
                sh       <= tx;
                half_cnt <= '0;
                nbit     <= '0;
                sclk     <= 1'b0;
            end
            else if (busy)
            begin
                if (half_cnt != half - 1)
                    half_cnt <= half_cnt + 1'b1;
                else
                begin
                    half_cnt <= '0;
                    if (! sclk)
                    begin                            // the rising edge: take a bit
                        sclk <= 1'b1;
                        rx   <= { rx [6:0], miso };
                    end
                    else
                    begin                            // the falling edge: the next bit out
                        sclk <= 1'b0;
                        sh   <= { sh [6:0], 1'b1 };
                        nbit <= nbit + 1'b1;
                        if (nbit == 3'd7)
                        begin
                            busy      <= 1'b0;
                            byte_done <= 1'b1;
                        end
                    end
                end
            end
        end

    // ---- the sequence -------------------------------------------------------------------

    typedef enum logic [3:0] { S_POWER, S_DUMMY, S_CMD, S_RESP, S_GAP, S_IDLE,
                               S_TOKEN, S_DATA, S_CRC, S_FAIL, S_WAIT } state_t;

    state_t      state, after_gap;
    logic [5:0]  cmd_index;
    logic [31:0] cmd_arg;
    logic [7:0]  cmd_crc;
    logic [3:0]  cmd_more;               // response bytes after R1
    logic [3:0]  phase;                  // of the command: 0..5 its bytes out, 6 waiting for R1, 7 the rest
    logic [4:0]  ncr;                    // bytes waited for R1
    logic [7:0]  r1;
    logic [31:0] r_arg;
    logic        v2, sdhc, reading;
    logic [9:0]  nbyte;
    logic [11:0] acmd_tries;
    logic [31:0] timer;                  // clocks since the last byte or step

    assign ready = (state == S_IDLE) && present;

    task automatic start_byte (input [7:0] b);
        tx    <= b;
        start <= 1'b1;
    endtask

    // the next command, after a gap with the card deselected
    task automatic command (input [5:0] index, input [31:0] arg, input [7:0] crc, input [3:0] more);
        cmd_index <= index;
        cmd_arg   <= arg;
        cmd_crc   <= crc;
        cmd_more  <= more;
        after_gap <= S_CMD;
        state     <= S_GAP;
    endtask

    always_ff @ (posedge clk or posedge rst)
        if (rst)
        begin
            state      <= S_POWER;
            after_gap  <= S_POWER;
            fast       <= 1'b0;
            start      <= 1'b0;
            tx         <= 8'hFF;
            cs_n       <= 1'b1;
            cmd_index  <= '0;
            cmd_arg    <= '0;
            cmd_crc    <= 8'hFF;
            cmd_more   <= '0;
            phase      <= '0;
            ncr        <= '0;
            r1         <= 8'hFF;
            r_arg      <= '0;
            v2         <= 1'b0;
            sdhc       <= 1'b0;
            reading    <= 1'b0;
            nbyte      <= '0;
            acmd_tries <= '0;
            timer      <= '0;
            data       <= '0;
            valid      <= 1'b0;
            done       <= 1'b0;
            error      <= 1'b0;
            present    <= 1'b0;
        end
        else
        begin
            start <= 1'b0;
            valid <= 1'b0;
            done  <= 1'b0;
            error <= 1'b0;
            timer <= byte_done ? '0 : timer + 1'b1;

            case (state)

            S_POWER:                                 // 80 clocks with the card deselected, slowly
            begin
                fast    <= 1'b0;
                cs_n    <= 1'b1;
                present <= 1'b0;
                reading <= 1'b0;
                nbyte   <= '0;
                timer   <= '0;
                start_byte (8'hFF);
                state   <= S_DUMMY;
            end

            S_DUMMY:
                if (byte_done)
                begin
                    nbyte <= nbyte + 1'b1;
                    if (nbyte == 10'd9)
                    begin
                        acmd_tries <= '0;
                        command (6'd0, 32'h0000_0000, 8'h95, 4'd0);
                    end
                    else
                        start_byte (8'hFF);
                end

            S_GAP:                                   // eight clocks with the card deselected
            begin
                cs_n <= 1'b1;
                if (! busy && ! start && ! byte_done)
                    start_byte (8'hFF);
                else if (byte_done)
                begin
                    state <= after_gap;
                    if (after_gap == S_CMD)
                    begin
                        cs_n  <= 1'b0;
                        phase <= '0;
                        ncr   <= '0;
                        r1    <= 8'hFF;
                        start_byte ({ 2'b01, cmd_index });
                    end
                end
            end

            S_CMD:                                   // the six bytes, R1 within 16 bytes, then more
                if (byte_done)
                begin
                    case (phase)
                    4'd0: start_byte (cmd_arg [31:24]);
                    4'd1: start_byte (cmd_arg [23:16]);
                    4'd2: start_byte (cmd_arg [15:8]);
                    4'd3: start_byte (cmd_arg [7:0]);
                    4'd4: start_byte (cmd_crc);
                    4'd5: start_byte (8'hFF);
                    4'd6:
                        if (! rx [7])
                        begin
                            r1    <= rx;
                            r_arg <= '0;
                            if (cmd_more == 0)
                                state <= S_RESP;
                            else
                            begin
                                nbyte <= '0;
                                start_byte (8'hFF);
                            end
                        end
                        else if (ncr == 5'd16)
                            state <= S_FAIL;
                        else
                        begin
                            ncr <= ncr + 1'b1;
                            start_byte (8'hFF);
                        end
                    default:
                    begin
                        r_arg <= { r_arg [23:0], rx };
                        nbyte <= nbyte + 1'b1;
                        if (nbyte == 10' (cmd_more) - 1)
                            state <= S_RESP;
                        else
                            start_byte (8'hFF);
                    end
                    endcase
                    if (phase != 4'd7)
                        phase <= (phase == 4'd6 && rx [7]) ? 4'd6 : phase + 1'b1;
                end

            S_RESP:                                  // what the card said
                case (cmd_index)
                6'd0:                                // idle
                    if (r1 == 8'h01) command (6'd8, 32'h0000_01AA, 8'h87, 4'd4);
                    else state <= S_FAIL;
                6'd8:                                // a version 2 card echoes 0x1AA; version 1: illegal command
                begin
                    v2 <= (r1 == 8'h01) && (r_arg [11:0] == 12'h1AA);
                    if (r1 == 8'h01 || r1 [2]) command (6'd55, 32'h0000_0000, 8'h01, 4'd0);
                    else state <= S_FAIL;
                end
                6'd55:                               // the next command is application specific
                    if (r1 [7:2] == 6'b0) command (6'd41, v2 ? 32'h4000_0000 : 32'h0000_0000, 8'h01, 4'd0);
                    else state <= S_FAIL;
                6'd41:                               // initialised, or still idle
                    if (r1 == 8'h00) command (6'd58, 32'h0000_0000, 8'h01, 4'd4);
                    else if (r1 == 8'h01 && acmd_tries != '1)
                    begin
                        acmd_tries <= acmd_tries + 1'b1;
                        command (6'd55, 32'h0000_0000, 8'h01, 4'd0);
                    end
                    else state <= S_FAIL;
                6'd58:                               // the OCR: block or byte addresses
                begin
                    sdhc <= r_arg [30];
                    fast <= 1'b1;
                    if (r1 != 8'h00) state <= S_FAIL;
                    else if (r_arg [30]) begin present <= 1'b1; after_gap <= S_IDLE; state <= S_GAP; end
                    else command (6'd16, 32'h0000_0200, 8'h01, 4'd0);
                end
                6'd16:                               // 512-byte blocks
                    if (r1 == 8'h00) begin present <= 1'b1; after_gap <= S_IDLE; state <= S_GAP; end
                    else state <= S_FAIL;
                6'd17:                               // the block follows
                    if (r1 == 8'h00) begin reading <= 1'b1; start_byte (8'hFF); state <= S_TOKEN; end
                    else state <= S_FAIL;
                default:
                    state <= S_FAIL;
                endcase

            S_IDLE:
            begin
                cs_n <= 1'b1;
                if (req)
                begin
                    cmd_index <= 6'd17;
                    cmd_arg   <= sdhc ? block : (block << 9);
                    cmd_crc   <= 8'h01;
                    cmd_more  <= '0;
                    cs_n      <= 1'b0;
                    phase     <= '0;
                    ncr       <= '0;
                    r1        <= 8'hFF;
                    timer     <= '0;
                    start_byte (8'h51);
                    state     <= S_CMD;
                end
            end

            S_TOKEN:                                 // the data token 0xFE
                if (byte_done)
                begin
                    if (rx == 8'hFE)
                    begin
                        nbyte <= '0;
                        state <= S_DATA;
                    end
                    else if (rx [7:5] == 3'b000 && rx != 8'h00)
                        state <= S_FAIL;             // an error token
                    if (rx == 8'hFE || rx [7:5] != 3'b000 || rx == 8'h00)
                        start_byte (8'hFF);
                end

            S_DATA:                                  // 512 bytes
                if (byte_done)
                begin
                    data  <= rx;
                    valid <= 1'b1;
                    nbyte <= nbyte + 1'b1;
                    start_byte (8'hFF);
                    if (nbyte == 10'd511)
                    begin
                        nbyte <= '0;
                        state <= S_CRC;
                    end
                end

            S_CRC:                                   // two CRC bytes, then done
                if (byte_done)
                begin
                    nbyte <= nbyte + 1'b1;
                    if (nbyte == 10'd1)
                    begin
                        done      <= 1'b1;
                        reading   <= 1'b0;
                        after_gap <= S_IDLE;
                        state     <= S_GAP;
                    end
                    else
                        start_byte (8'hFF);
                end

            S_FAIL:                                  // the card is gone: say so, start over later
            begin
                cs_n    <= 1'b1;
                error   <= present | reading;
                present <= 1'b0;
                reading <= 1'b0;
                timer   <= '0;
                state   <= S_WAIT;
            end

            S_WAIT:
                if (timer >= 32' (RETRY_MS) * 32' (MS_CLOCKS))
                    state <= S_POWER;

            default:
                state <= S_POWER;

            endcase

            // a response overdue: the card is not answering
            if (state != S_IDLE && state != S_WAIT && state != S_FAIL && state != S_POWER
                    && timer >= 32' (TIMEOUT_MS) * 32' (MS_CLOCKS))
                state <= S_FAIL;
        end

endmodule
