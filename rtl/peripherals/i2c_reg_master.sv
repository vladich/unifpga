// =============================================================================
// i2c_reg_master — polls a register block of one I2C device: every POLL_MS it
// sets the device's register pointer to REG and reads N_BYTES bytes in one
// burst (the device auto-increments), giving them in `data`, the first byte in
// the top bits. Before the first read it writes the N_INIT {register, value}
// pairs of INIT (a power-up configuration: an ADXL345's POWER_CTL).
//
// The device's address need not be known: the master tries the N_ADDR 7-bit
// addresses of ADDRS (the first in the top bits) in turn until one answers,
// keeps it, and, should it stop answering, looks again (the configuration is
// written again too). Only the chip's own possible addresses belong in ADDRS:
// a write to another device at a probed address would change that device.
//
// SCL is driven push-pull (single master; the devices this serves do not
// stretch the clock); SDA is open drain (driven low or released, the board
// pulls it up). The temperature sensors and the ADXL345 are read through it.
// =============================================================================

module i2c_reg_master
# (
    parameter                    CLK_MHZ = 50,
                                 SCL_KHZ = 100,
                                 POLL_MS = 250,
                                 N_ADDR  = 1,
    parameter [N_ADDR * 7 - 1:0] ADDRS   = 7'h48,
    parameter                    N_INIT  = 0,
    parameter [(N_INIT > 0 ? N_INIT : 1) * 16 - 1:0] INIT = '0,
    parameter [7:0]              REG     = 8'h00,
    parameter                    N_BYTES = 2
)
(
    input                          clk,
    input                          rst,
    output logic                   scl,
    inout                          sda,
    output logic                   valid,     // one clock: `data` is a new reading
    output logic [N_BYTES * 8 - 1:0] data,
    output logic                   found      // a device answers
);

    // ---- bit tick: four per SCL period ---------------------------------------

    localparam int QUARTER = (CLK_MHZ * 1000 / (SCL_KHZ * 4) > 1) ? CLK_MHZ * 1000 / (SCL_KHZ * 4) : 1;
    localparam int W_DIV   = (QUARTER > 1) ? $clog2(QUARTER) : 1;
    localparam int POLL    = POLL_MS * SCL_KHZ * 4;             // ticks between readings
    localparam int W_WAIT  = $clog2(POLL + 1);
    localparam int GAP     = 16;                                // ticks of idle bus between transactions
    localparam int W_ADDR  = (N_ADDR > 1) ? $clog2(N_ADDR) : 1;
    localparam int W_INIT  = (N_INIT > 1) ? $clog2(N_INIT) : 1;
    localparam int W_BYTE  = (N_BYTES > 1) ? $clog2(N_BYTES) : 1;

    localparam [31:0]         DIV_LAST_32  = QUARTER - 1;
    localparam [W_DIV - 1:0]  DIV_LAST     = DIV_LAST_32 [W_DIV - 1:0];
    localparam [31:0]         POLL_32      = POLL;
    localparam [31:0]         GAP_32       = GAP;
    localparam [W_WAIT - 1:0] POLL_TICKS   = POLL_32 [W_WAIT - 1:0];
    localparam [W_WAIT - 1:0] GAP_TICKS    = GAP_32  [W_WAIT - 1:0];
    localparam [31:0]         ADDR_LAST_32 = N_ADDR - 1;
    localparam [W_ADDR - 1:0] ADDR_LAST    = ADDR_LAST_32 [W_ADDR - 1:0];
    localparam [31:0]         INIT_LAST_32 = (N_INIT > 0) ? N_INIT - 1 : 0;
    localparam [W_INIT - 1:0] INIT_LAST    = INIT_LAST_32 [W_INIT - 1:0];
    localparam [31:0]         BYTE_LAST_32 = N_BYTES - 1;
    localparam [W_BYTE - 1:0] BYTE_LAST    = BYTE_LAST_32 [W_BYTE - 1:0];

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
    //   init k:   START  addr+W  INIT[k].reg  INIT[k].value        STOP   (until configured)
    //   pointer:  START  addr+W  REG                               STOP
    //   read:     START  addr+R  byte ack ... byte ack  byte no-ack STOP

    typedef enum logic [2:0] { S_WAIT, S_START, S_TX, S_RX, S_STOP } state_t;
    typedef enum logic [1:0] { T_INIT, T_POINTER, T_READ } txn_t;

    state_t              state;
    txn_t                txn;
    logic [1:0]          phase;       // quarter of the current SCL period
    logic [3:0]          pos;         // bit of the byte, 8 = acknowledge slot
    logic [1:0]          nbyte;       // bytes sent so far in this transaction (the address is 0)
    logic [W_BYTE - 1:0] rbyte;       // bytes received so far
    logic [W_INIT - 1:0] init_i;
    logic [W_ADDR - 1:0] addr_i;
    logic                configured;
    logic [7:0]          tx;
    logic [7:0]          rx;
    logic [N_BYTES * 8 - 1:0] shift;
    logic                sda_low;
    logic                nack;
    logic [W_WAIT - 1:0] wait_n;

    wire  [6:0]  addr      = ADDRS [(N_ADDR - 1 - addr_i) * 7 +: 7];
    wire  [15:0] init_pair = INIT  [((N_INIT > 0 ? N_INIT : 1) - 1 - init_i) * 16 +: 16];

    // the byte to send after the one out now (nbyte bytes are done: the address is 0)
    wire  [7:0]  next_tx   = (txn == T_POINTER) ? REG
                           : (nbyte == 2'd0)   ? init_pair [15:8] : init_pair [7:0];

    assign sda = sda_low ? 1'b0 : 1'bz;

    always_ff @ (posedge clk or posedge rst)
        if (rst)
        begin
            state      <= S_WAIT;
            txn        <= (N_INIT > 0) ? T_INIT : T_POINTER;
            phase      <= '0;
            pos        <= '0;
            nbyte      <= '0;
            rbyte      <= '0;
            init_i     <= '0;
            addr_i     <= '0;
            configured <= (N_INIT == 0);
            tx         <= '0;
            rx         <= '0;
            shift      <= '0;
            sda_low    <= 1'b0;
            nack       <= 1'b0;
            wait_n     <= GAP_TICKS;
            scl        <= 1'b1;
            valid      <= 1'b0;
            data       <= '0;
            found      <= 1'b0;
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
                    scl   <= 1'b0;
                    phase <= '0;
                    pos   <= '0;
                    nbyte <= '0;
                    rbyte <= '0;
                    nack  <= 1'b0;
                    tx    <= { addr, txn == T_READ };
                    state <= S_TX;
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
                        if (nack)
                            state <= S_STOP;        // nobody answers
                        else if (txn == T_READ)
                            state <= S_RX;
                        else if (nbyte == ((txn == T_POINTER) ? 2'd1 : 2'd2))
                            state <= S_STOP;        // the transaction's last byte is out
                        else
                        begin
                            tx    <= next_tx;
                            nbyte <= nbyte + 1'b1;
                        end
                    end
                end
                endcase
            end

            S_RX:                                   // a byte in, acknowledged unless the last
            begin
                phase <= phase + 1'b1;
                case (phase)
                2'd0: sda_low <= (pos == 4'd8) ? (rbyte != BYTE_LAST) : 1'b0;
                2'd1: scl     <= 1'b1;
                2'd2: if (pos != 4'd8) rx <= { rx [6:0], sda_in };
                2'd3:
                begin
                    scl <= 1'b0;
                    pos <= (pos == 4'd8) ? 4'd0 : pos + 1'b1;
                    if (pos == 4'd8)
                    begin
                        shift <= (N_BYTES > 1) ? { shift [N_BYTES * 8 - 9:0], rx } : rx;
                        if (rbyte == BYTE_LAST)
                            state <= S_STOP;
                        else
                            rbyte <= rbyte + 1'b1;
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
                    begin                           // nobody there: the next address, configure again
                        found      <= 1'b0;
                        configured <= (N_INIT == 0);
                        init_i     <= '0;
                        txn        <= (N_INIT > 0) ? T_INIT : T_POINTER;
                        if (! found)
                            addr_i <= (addr_i == ADDR_LAST) ? '0 : addr_i + 1'b1;
                    end
                    else if (txn == T_INIT)
                    begin
                        if (init_i == INIT_LAST)
                        begin
                            configured <= 1'b1;
                            txn        <= T_POINTER;
                        end
                        else
                            init_i <= init_i + 1'b1;
                    end
                    else if (txn == T_POINTER)
                        txn <= T_READ;
                    else
                    begin
                        found  <= 1'b1;
                        valid  <= 1'b1;
                        data   <= shift;
                        txn    <= T_POINTER;
                        wait_n <= POLL_TICKS;
                    end
                end
                endcase
            end

            default:
                state <= S_WAIT;

            endcase
        end

endmodule
