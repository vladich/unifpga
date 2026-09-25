// =============================================================================
// ds18b20_reader — polls a Maxim DS18B20 1-Wire temperature sensor and gives
// its reading in 1/16 degrees Celsius (its native resolution at 12 bits).
//
// Every POLL_MS (datasheet timings, standard speed): a reset pulse (480 us low)
// and its presence answer; SKIP ROM (0xCC) and CONVERT T (0x44); read slots
// until the sensor answers 1 (conversion done, up to 750 ms; a sensor powered
// from the line cannot be polled so and simply gets 800 ms); a reset again,
// SKIP ROM and READ SCRATCHPAD (0xBE): temperature LSB, MSB, then six more
// bytes and the CRC-8 of the eight, checked before `valid`. Bits are least
// significant first; a write slot is 60 us plus recovery, the line low for
// 6 us (1) or 60 us (0); a read slot pulls low 2 us and samples at 12 us.
// DQ is open drain (driven low or released; the board pulls it up).
// =============================================================================

module ds18b20_reader
# (
    parameter CLK_MHZ = 50,
    parameter POLL_MS = 1000
)
(
    input                      clk,
    input                      rst,
    inout                      dq,
    output logic               valid,     // one clock: a new reading
    output logic signed [15:0] temp,      // 1/16 degrees Celsius
    output logic               found      // a sensor answered the last reset
);

    // ---- microsecond tick and the line ------------------------------------------

    localparam int W_DIV = (CLK_MHZ > 1) ? $clog2(CLK_MHZ) : 1;
    localparam [31:0]      DIV_LAST_32 = CLK_MHZ - 1;
    localparam [W_DIV-1:0] DIV_LAST    = DIV_LAST_32 [W_DIV - 1:0];

    logic [W_DIV-1:0] div;
    wire              us = (div == DIV_LAST);

    always_ff @ (posedge clk or posedge rst)
        if (rst)
            div <= '0;
        else
            div <= us ? '0 : div + 1'b1;

    logic       dq_low;
    logic [1:0] dq_s;

    assign dq = dq_low ? 1'b0 : 1'bz;

    always_ff @ (posedge clk)
        dq_s <= { dq_s [0], dq };

    wire dq_in = dq_s [1];

    // ---- the sequence -----------------------------------------------------------

    localparam int POLL_US   = POLL_MS * 1000;
    localparam int W_T       = $clog2(POLL_US + 1);

    typedef enum logic [3:0] { S_WAIT, S_RESET_LOW, S_PRESENCE, S_RESET_END,
                               S_WRITE, S_READ, S_BUSY, S_DONE } state_t;

    state_t          state;
    logic [W_T-1:0]  t;              // microseconds in the current step
    logic            phase;          // 0: first reset (convert), 1: second reset (read)
    logic [7:0]      out;            // the byte being written
    logic [3:0]      nbyte;          // bytes of the command / scratchpad so far
    logic [2:0]      nbit;
    logic [7:0]      in;
    logic [71:0]     scratch;        // the 9 scratchpad bytes, the first in the low byte
    logic [7:0]      crc;
    logic            bit_val;
    logic [6:0]      polls;          // 10 ms read slots while it converts

    // the byte to write at position n of each phase's command
    function automatic [7:0] cmd_byte (input logic ph, input [3:0] n);
        cmd_byte = (n == 4'd0) ? 8'hCC : (ph ? 8'hBE : 8'h44);
    endfunction

    // CRC-8 x^8 + x^5 + x^4 + 1, least significant bit first (the 1-Wire CRC)
    function automatic [7:0] crc8_bit (input [7:0] c, input b);
        logic x;
        x = c [0] ^ b;
        crc8_bit = { x, c [7], c [6], c [5] ^ x, c [4] ^ x, c [3], c [2], c [1] };
    endfunction

    always_ff @ (posedge clk or posedge rst)
        if (rst)
        begin
            state      <= S_WAIT;
            t          <= '0;
            phase      <= 1'b0;
            out        <= '0;
            nbyte      <= '0;
            nbit       <= '0;
            in         <= '0;
            scratch    <= '0;
            crc        <= '0;
            bit_val    <= 1'b0;
            polls      <= '0;
            dq_low     <= 1'b0;
            valid      <= 1'b0;
            temp       <= '0;
            found      <= 1'b0;
        end
        else
        begin
            valid <= 1'b0;
            if (us)
                t <= t + 1'b1;

            case (state)

            S_WAIT:                                 // idle between polls
                if (t >= W_T' (POLL_US) || (! found && t >= W_T' (POLL_US / 4)))
                begin
                    t      <= '0;
                    phase  <= 1'b0;
                    dq_low <= 1'b1;
                    state  <= S_RESET_LOW;
                end

            S_RESET_LOW:                            // 480 us low
                if (t >= W_T' (480))
                begin
                    dq_low <= 1'b0;
                    t      <= '0;
                    state  <= S_PRESENCE;
                end

            S_PRESENCE:                             // the sensor pulls low 15..60 us after the release
                if (t >= W_T' (70))
                begin
                    found <= ~ dq_in;
                    t     <= '0;
                    state <= S_RESET_END;
                end

            S_RESET_END:                            // the rest of the 480 us presence window
                if (t >= W_T' (420))
                begin
                    t <= '0;
                    if (! found)
                        state <= S_WAIT;
                    else
                    begin
                        nbyte      <= '0;
                        nbit       <= '0;
                        out        <= cmd_byte (phase, 4'd0);
                        dq_low     <= 1'b1;
                        state      <= S_WRITE;
                    end
                end

            S_WRITE:                                // a write slot: low 6 us (1) or 60 us (0), 70 us in all
            begin
                if (t >= (out [0] ? W_T' (6) : W_T' (60)))
                    dq_low <= 1'b0;
                if (t >= W_T' (70))
                begin
                    t    <= '0;
                    nbit <= nbit + 1'b1;
                    out  <= { 1'b0, out [7:1] };
                    if (nbit == 3'd7)
                    begin
                        nbyte <= nbyte + 1'b1;
                        if (nbyte == 4'd1)          // both command bytes are out
                        begin
                            nbyte <= '0;
                            if (phase == 1'b0)
                            begin                   // converting: poll read slots until 1
                                polls <= '0;
                                state <= S_BUSY;
                            end
                            else
                            begin                   // read the scratchpad
                                crc     <= '0;
                                scratch <= '0;
                                dq_low  <= 1'b1;
                                state   <= S_READ;
                            end
                        end
                        else
                        begin
                            out    <= cmd_byte (phase, nbyte + 1'b1);
                            dq_low <= 1'b1;
                        end
                    end
                    else
                        dq_low <= 1'b1;
                end
            end

            S_BUSY:                                 // a read slot every 10 ms: 0 while it converts
            begin
                if (t == W_T' (0))
                    dq_low <= 1'b1;
                if (t >= W_T' (2))
                    dq_low <= 1'b0;
                if (t == W_T' (12) && us)
                    bit_val <= dq_in;
                if (t >= W_T' (10000))
                begin
                    t     <= '0;
                    polls <= polls + 1'b1;
                    if (bit_val)
                    begin                           // done: the second reset, then the scratchpad
                        phase  <= 1'b1;
                        dq_low <= 1'b1;
                        state  <= S_RESET_LOW;
                    end
                    else if (polls == 7'd80)        // 800 ms and never done: parasite power, or gone
                        state <= S_WAIT;
                end
            end

            S_READ:                                 // a read slot: low 2 us, sample at 12 us, 70 us in all
            begin
                if (t >= W_T' (2))
                    dq_low <= 1'b0;
                if (t == W_T' (12) && us)
                begin
                    in <= { dq_in, in [7:1] };
                    if (nbyte < 4'd8)               // the CRC covers the first eight bytes
                        crc <= crc8_bit (crc, dq_in);
                end
                if (t >= W_T' (70))
                begin
                    t    <= '0;
                    nbit <= nbit + 1'b1;
                    if (nbit == 3'd7)
                    begin
                        scratch [nbyte * 8 +: 8] <= in;
                        nbyte <= nbyte + 1'b1;
                        if (nbyte == 4'd8)
                            state <= S_DONE;
                        else
                            dq_low <= 1'b1;
                    end
                    else
                        dq_low <= 1'b1;
                end
            end

            S_DONE:
            begin
                if (crc == scratch [71:64])
                begin
                    temp  <= scratch [15:0];
                    valid <= 1'b1;
                end
                t     <= '0;
                state <= S_WAIT;
            end

            default:
                state <= S_WAIT;

            endcase
        end

endmodule
