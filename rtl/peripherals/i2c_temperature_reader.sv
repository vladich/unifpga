// =============================================================================
// i2c_temperature_reader — polls an LM75-family I2C temperature sensor (LM75,
// LM75A, ADT7420, TMP75 ...) and gives its reading in 1/16 degrees Celsius.
//
// Every POLL_MS: set the register pointer to 0 (the temperature register),
// then read its two bytes, most significant first. The reading is two's
// complement, left-aligned in the 16 bits: SHIFT is where its 1/16 C bit sits
// (the ADT7420's 13-bit reading has 1/16 C at bit 3: SHIFT 3; the LM75's
// 9 bits (1/2 C) and the LM75A's 11 bits (1/8 C) end at bit 7 / bit 5, so
// 1/16 C is bit 4: SHIFT 4). `temp` = reading >>> SHIFT, exact for all.
//
// The sensor's address (its A0..A2 straps) need not be known: the reader
// tries ADDR_FIRST..ADDR_LAST (the family's 0x48..0x4F) until one answers,
// keeps that one, and looks again if it stops answering.
//
// SCL is driven push-pull (single master; these sensors do not stretch the
// clock); SDA is open drain (driven low or released, the board pulls it up).
// =============================================================================

module i2c_temperature_reader
# (
    parameter       CLK_MHZ    = 50,
    parameter       SCL_KHZ    = 100,
    parameter       SHIFT      = 4,
    parameter       POLL_MS    = 250,
    parameter [6:0] ADDR_FIRST = 7'h48,
    parameter [6:0] ADDR_LAST  = 7'h4F
)
(
    input                      clk,
    input                      rst,
    output logic               scl,
    inout                      sda,
    output logic               valid,     // one clock: a new reading
    output logic signed [15:0] temp       // 1/16 degrees Celsius
);

    // ---- bit tick: four per SCL period ---------------------------------------

    localparam int QUARTER = (CLK_MHZ * 1000 / (SCL_KHZ * 4) > 1) ? CLK_MHZ * 1000 / (SCL_KHZ * 4) : 1;
    localparam int W_DIV   = (QUARTER > 1) ? $clog2(QUARTER) : 1;
    localparam int POLL    = POLL_MS * SCL_KHZ * 4;             // ticks between readings
    localparam int W_WAIT  = $clog2(POLL + 1);
    localparam int GAP     = 16;                                // ticks of idle bus between transactions

    localparam [31:0]         DIV_LAST_32  = QUARTER - 1;
    localparam [W_DIV - 1:0]  DIV_LAST     = DIV_LAST_32 [W_DIV - 1:0];
    localparam [31:0]         POLL_32      = POLL;
    localparam [31:0]         GAP_32       = GAP;
    localparam [W_WAIT - 1:0] POLL_TICKS   = POLL_32 [W_WAIT - 1:0];
    localparam [W_WAIT - 1:0] GAP_TICKS    = GAP_32  [W_WAIT - 1:0];

    logic [W_DIV - 1:0] div;
    wire                tick = (div == DIV_LAST);

    always_ff @ (posedge clk or posedge rst)
        if (rst)
            div <= '0;
        else
            div <= tick ? '0 : div + 1'b1;

    // SDA as the bus sees it, synchronised
    logic [1:0] sda_s;

    always_ff @ (posedge clk)
        sda_s <= { sda_s [0], sda };

    wire sda_in = sda_s [1];

    // ---- transactions ---------------------------------------------------------
    //
    //   pointer:  START  addr+W  0x00                      STOP
    //   read:     START  addr+R  <msb> ack  <lsb> no-ack   STOP

    typedef enum logic [2:0] { S_WAIT, S_START, S_TX, S_RX, S_STOP } state_t;

    state_t              state;
    logic [1:0]          phase;       // quarter of the current SCL period
    logic [3:0]          pos;         // bit of the byte, 8 = acknowledge slot
    logic                reading;     // the read transaction (else the pointer one)
    logic                second;      // the transaction's second byte (after the address)
    logic [7:0]          tx;
    logic [7:0]          rx;
    logic [7:0]          msb;
    logic                sda_low;
    logic                nack;
    logic                found;
    logic [6:0]          addr;
    logic [W_WAIT - 1:0] wait_n;

    assign sda = sda_low ? 1'b0 : 1'bz;

    always_ff @ (posedge clk or posedge rst)
        if (rst)
        begin
            state   <= S_WAIT;
            phase   <= '0;
            pos     <= '0;
            reading <= 1'b0;
            second  <= 1'b0;
            tx      <= '0;
            rx      <= '0;
            msb     <= '0;
            sda_low <= 1'b0;
            nack    <= 1'b0;
            found   <= 1'b0;
            addr    <= ADDR_FIRST;
            wait_n  <= GAP_TICKS;
            scl     <= 1'b1;
            valid   <= 1'b0;
            temp    <= '0;
        end
        else
        begin
            valid <= 1'b0;

            if (tick)
            case (state)

            S_WAIT:                                 // bus idle: SCL high, SDA released
                if (wait_n == '0)
                begin
                    phase <= '0;
                    state <= S_START;
                end
                else
                    wait_n <= wait_n - 1'b1;

            S_START:                                // SDA falls while SCL is high
            begin
                phase <= phase + 1'b1;
                if (phase == 2'd0)
                    sda_low <= 1'b1;
                else
                begin
                    scl    <= 1'b0;
                    phase  <= '0;
                    pos    <= '0;
                    second <= 1'b0;
                    nack   <= 1'b0;
                    tx     <= { addr, reading };
                    state  <= S_TX;
                end
            end

            S_TX:                                   // a byte out, the device acknowledges
            begin
                phase <= phase + 1'b1;
                case (phase)
                2'd0: sda_low <= (pos == 4'd8) ? 1'b0 : ~ tx [3'd7 - pos [2:0]];
                2'd1: scl     <= 1'b1;
                2'd2: if (pos == 4'd8 && sda_in) nack <= 1'b1;
                2'd3:
                begin
                    scl <= 1'b0;
                    pos <= (pos == 4'd8) ? 4'd0 : pos + 1'b1;
                    if (pos == 4'd8)
                    begin
                        if (nack || second)
                            state <= S_STOP;        // no answer, or the pointer is set
                        else if (reading)
                            state <= S_RX;
                        else
                        begin
                            tx     <= 8'h00;        // the temperature register
                            second <= 1'b1;
                        end
                    end
                end
                endcase
            end

            S_RX:                                   // a byte in, acknowledged unless the last
            begin
                phase <= phase + 1'b1;
                case (phase)
                2'd0: sda_low <= (pos == 4'd8) ? ~ second : 1'b0;
                2'd1: scl     <= 1'b1;
                2'd2: if (pos != 4'd8) rx <= { rx [6:0], sda_in };
                2'd3:
                begin
                    scl <= 1'b0;
                    pos <= (pos == 4'd8) ? 4'd0 : pos + 1'b1;
                    if (pos == 4'd8)
                    begin
                        if (second)
                            state <= S_STOP;
                        else
                        begin
                            msb    <= rx;
                            second <= 1'b1;
                        end
                    end
                end
                endcase
            end

            S_STOP:                                 // SDA rises while SCL is high
            begin
                phase <= phase + 1'b1;
                case (phase)
                2'd0: sda_low <= 1'b1;
                2'd1: scl     <= 1'b1;
                2'd2: sda_low <= 1'b0;
                2'd3:
                begin
                    state  <= S_WAIT;
                    wait_n <= GAP_TICKS;
                    if (nack)
                    begin                           // nobody there: the next address
                        found   <= 1'b0;
                        reading <= 1'b0;
                        if (! found)
                            addr <= (addr == ADDR_LAST) ? ADDR_FIRST : addr + 1'b1;
                    end
                    else if (! reading)
                        reading <= 1'b1;
                    else
                    begin
                        found   <= 1'b1;
                        reading <= 1'b0;
                        valid   <= 1'b1;
                        temp    <= $signed ({ msb, rx }) >>> SHIFT;
                        wait_n  <= POLL_TICKS;
                    end
                end
                endcase
            end

            default:
                state <= S_WAIT;

            endcase
        end

endmodule
