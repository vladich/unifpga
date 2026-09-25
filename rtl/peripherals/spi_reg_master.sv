// =============================================================================
// spi_reg_master — polls a register block of one SPI device: after START_MS
// it sends the N_INIT configuration frames of INIT (INIT_LEN bytes each, the
// first frame in the top bits), then every 1000 / POLL_HZ ms one read frame:
// the CMD_LEN command bytes of CMD followed by N_BYTES bytes clocked in while
// zeros go out, given in `data` with the first byte read in the top bits.
//
// CPOL is the clock's idle level, CPHA which edge samples: 0 = both sides
// sample on the edge away from idle (the leading edge) and shift on the way
// back, 1 = shift on the leading edge and sample on the trailing one; MOSI's
// first bit is set as CS falls (CPHA 0) or on the first leading edge (CPHA 1).
// CS is low for a whole frame. The accelerometers are read through it: the
// ADXL362 (mode 0, two-byte commands) and the ADXL345 (mode 3, one byte).
// =============================================================================

module spi_reg_master
# (
    parameter                 CLK_MHZ  = 50,
                              SCLK_KHZ = 1000,
                              CPOL     = 0,
                              CPHA     = 0,
                              START_MS = 6,
                              POLL_HZ  = 100,
                              N_INIT   = 1,
                              INIT_LEN = 2,
    parameter [(N_INIT > 0 ? N_INIT : 1) * INIT_LEN * 8 - 1:0] INIT = '0,
    parameter                 CMD_LEN  = 1,
    parameter [CMD_LEN * 8 - 1:0] CMD  = '0,
    parameter                 N_BYTES  = 6
)
(
    input                            clk,
    input                            rst,
    output logic                     sclk,
    output logic                     mosi,
    input                            miso,
    output logic                     cs_n,
    output logic                     valid,     // one clock: `data` is a new reading
    output logic [N_BYTES * 8 - 1:0] data
);

    // ---- half-period tick ------------------------------------------------------

    localparam int HALF   = (CLK_MHZ * 1000 / (SCLK_KHZ * 2) > 1) ? CLK_MHZ * 1000 / (SCLK_KHZ * 2) : 1;
    localparam int W_DIV  = (HALF > 1) ? $clog2(HALF) : 1;
    localparam int POLL   = SCLK_KHZ * 2000 / POLL_HZ;        // ticks between readings
    localparam int START  = START_MS * SCLK_KHZ * 2;          // ticks before the first frame
    localparam int LONG   = (POLL > START) ? POLL : START;
    localparam int W_WAIT = $clog2(LONG + 1);
    localparam int GAP    = 4;                                // ticks of CS high between frames
    localparam int READ_LEN = CMD_LEN + N_BYTES;
    localparam int MAX_LEN  = (INIT_LEN > READ_LEN) ? INIT_LEN : READ_LEN;
    localparam int W_LEN  = (MAX_LEN > 1) ? $clog2(MAX_LEN + 1) : 1;
    localparam int W_INIT = (N_INIT > 1) ? $clog2(N_INIT) : 1;

    localparam [31:0]         DIV_LAST_32 = HALF - 1;
    localparam [W_DIV - 1:0]  DIV_LAST    = DIV_LAST_32 [W_DIV - 1:0];
    localparam [31:0]         POLL_32     = POLL;
    localparam [31:0]         START_32    = START;
    localparam [31:0]         GAP_32      = GAP;
    localparam [W_WAIT - 1:0] POLL_TICKS  = POLL_32  [W_WAIT - 1:0];
    localparam [W_WAIT - 1:0] START_TICKS = START_32 [W_WAIT - 1:0];
    localparam [W_WAIT - 1:0] GAP_TICKS   = GAP_32   [W_WAIT - 1:0];
    localparam [31:0]         INIT_LAST_32 = (N_INIT > 0) ? N_INIT - 1 : 0;
    localparam [W_INIT - 1:0] INIT_LAST   = INIT_LAST_32 [W_INIT - 1:0];

    logic [W_DIV - 1:0] div;
    wire                tick = (div == DIV_LAST);

    always_ff @ (posedge clk or posedge rst)
        if (rst)
            div <= '0;
        else
            div <= tick ? '0 : div + 1'b1;

    logic [1:0] miso_s;

    always_ff @ (posedge clk)
        miso_s <= { miso_s [0], miso };

    // ---- frames ------------------------------------------------------------------

    typedef enum logic [1:0] { S_WAIT, S_SELECT, S_BITS, S_RELEASE } state_t;

    state_t              state;
    logic                configured;  // the init frames are sent
    logic [W_INIT - 1:0] init_i;
    logic                lead;        // the leading edge is next (else the trailing one)
    logic [2:0]          bit_n;
    logic [W_LEN - 1:0]  byte_n;
    logic [7:0]          rx;
    logic [N_BYTES * 8 - 1:0] shift;
    logic [W_WAIT - 1:0] wait_n;

    wire [W_LEN - 1:0] n_bytes = configured ? W_LEN' (READ_LEN) : W_LEN' (INIT_LEN);

    // byte i of the frame out (zeros while the data comes in)
    function automatic [7:0] out_byte (input logic conf, input [W_INIT - 1:0] ii, input [W_LEN - 1:0] i);
        if (! conf)
            out_byte = INIT [((N_INIT > 0 ? N_INIT : 1) * INIT_LEN - 1 - (ii * INIT_LEN + i)) * 8 +: 8];
        else if (i < CMD_LEN)
            out_byte = CMD [(CMD_LEN - 1 - i) * 8 +: 8];
        else
            out_byte = 8'h00;
    endfunction

    wire [7:0] tx      = out_byte (configured, init_i, byte_n);
    wire [7:0] tx_next = out_byte (configured, init_i, byte_n + 1'b1);
    wire       tx_bit  = tx [3'd7 - bit_n];
    wire       tx_bit_next = (bit_n == 3'd7) ? tx_next [7] : tx [3'd6 - bit_n];

    always_ff @ (posedge clk or posedge rst)
        if (rst)
        begin
            state      <= S_WAIT;
            configured <= (N_INIT == 0);
            init_i     <= '0;
            lead       <= 1'b1;
            bit_n      <= '0;
            byte_n     <= '0;
            rx         <= '0;
            shift      <= '0;
            wait_n     <= START_TICKS;
            sclk       <= CPOL [0];
            mosi       <= 1'b0;
            cs_n       <= 1'b1;
            valid      <= 1'b0;
            data       <= '0;
        end
        else
        begin
            valid <= 1'b0;

            if (tick)
            case (state)

            S_WAIT:
                if (wait_n == '0)
                begin
                    cs_n   <= 1'b0;
                    byte_n <= '0;
                    bit_n  <= '0;
                    lead   <= 1'b1;
                    state  <= S_SELECT;
                end
                else
                    wait_n <= wait_n - 1'b1;

            S_SELECT:                               // CS low for a half bit; CPHA 0: the first bit out
            begin
                if (CPHA == 0)
                    mosi <= tx_bit;
                state <= S_BITS;
            end

            S_BITS:
                if (lead)
                begin                               // the leading edge
                    sclk <= ~ CPOL [0];
                    lead <= 1'b0;
                    if (CPHA == 0)
                        rx <= { rx [6:0], miso_s [1] };
                    else
                        mosi <= tx_bit;
                end
                else
                begin                               // the trailing edge: the bit is done
                    sclk <= CPOL [0];
                    lead <= 1'b1;
                    if (CPHA == 0)
                        mosi <= tx_bit_next;
                    bit_n <= bit_n + 1'b1;
                    if (bit_n == 3'd7)
                    begin
                        if (configured && byte_n >= W_LEN' (CMD_LEN))
                            shift <= (N_BYTES > 1) ? { shift [N_BYTES * 8 - 9:0], (CPHA == 0) ? rx : { rx [6:0], miso_s [1] } }
                                                   : ((CPHA == 0) ? rx : { rx [6:0], miso_s [1] });
                        if (byte_n == n_bytes - 1'b1)
                            state <= S_RELEASE;
                        else
                            byte_n <= byte_n + 1'b1;
                    end
                    if (CPHA == 1)
                        rx <= { rx [6:0], miso_s [1] };
                end

            S_RELEASE:
            begin
                cs_n   <= 1'b1;
                mosi   <= 1'b0;
                state  <= S_WAIT;
                wait_n <= GAP_TICKS;
                if (! configured)
                begin
                    if (init_i == INIT_LAST)
                        configured <= 1'b1;
                    else
                        init_i <= init_i + 1'b1;
                end
                else
                begin
                    valid  <= 1'b1;
                    data   <= shift;
                    wait_n <= POLL_TICKS;
                end
            end

            default:
                state <= S_WAIT;

            endcase
        end

endmodule
