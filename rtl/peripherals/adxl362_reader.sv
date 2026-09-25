// =============================================================================
// adxl362_reader — reads the three axes of an Analog Devices ADXL362
// accelerometer over SPI (mode 0: SCLK idles low, both sides sample on its
// rising edge) and gives them in milli-g.
//
// After reset (and the part's 5 ms start-up): write POWER_CTL (0x2D) = 0x02,
// measurement mode. Then every 1000 / POLL_HZ ms: read the six data registers
// from XDATA_L (0x0E) in one burst: X, Y, Z, each least significant byte first,
// 12 bits sign-extended to 16. At the power-on range (+-2 g) one count is 1 mg,
// so the readings are milli-g as they are.
//
// Commands (datasheet, "SPI commands"): 0x0A write register, 0x0B read.
// =============================================================================

module adxl362_reader
# (
    parameter CLK_MHZ   = 50,
    parameter SCLK_KHZ  = 1000,
    parameter POLL_HZ   = 100,
    parameter START_MS  = 6
)
(
    input                      clk,
    input                      rst,
    output logic               sclk,
    output logic               mosi,
    input                      miso,
    output logic               cs_n,
    output logic               valid,     // one clock: new readings
    output logic signed [15:0] x,         // milli-g
    output logic signed [15:0] y,
    output logic signed [15:0] z
);

    // ---- half-period tick ------------------------------------------------------

    localparam int HALF   = (CLK_MHZ * 1000 / (SCLK_KHZ * 2) > 1) ? CLK_MHZ * 1000 / (SCLK_KHZ * 2) : 1;
    localparam int W_DIV  = (HALF > 1) ? $clog2(HALF) : 1;
    localparam int POLL   = SCLK_KHZ * 2000 / POLL_HZ;        // ticks between readings
    localparam int START  = START_MS * SCLK_KHZ * 2;          // ticks before the first command
    localparam int LONG   = (POLL > START) ? POLL : START;
    localparam int W_WAIT = $clog2(LONG + 1);
    localparam int GAP    = 4;                                // ticks of CS high between transactions

    localparam [31:0]         DIV_LAST_32 = HALF - 1;
    localparam [W_DIV - 1:0]  DIV_LAST    = DIV_LAST_32 [W_DIV - 1:0];
    localparam [31:0]         POLL_32     = POLL;
    localparam [31:0]         START_32    = START;
    localparam [31:0]         GAP_32      = GAP;
    localparam [W_WAIT - 1:0] POLL_TICKS  = POLL_32  [W_WAIT - 1:0];
    localparam [W_WAIT - 1:0] START_TICKS = START_32 [W_WAIT - 1:0];
    localparam [W_WAIT - 1:0] GAP_TICKS   = GAP_32   [W_WAIT - 1:0];

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

    // ---- transactions ------------------------------------------------------------
    //
    //   setup:  0x0A 0x2D 0x02                     (3 bytes out)
    //   read:   0x0B 0x0E  xl xh yl yh zl zh       (2 out, 6 in)

    typedef enum logic [1:0] { S_WAIT, S_SELECT, S_BITS, S_RELEASE } state_t;

    state_t              state;
    logic                ready;       // measurement mode is set
    logic                high;        // SCLK is high (second half of the bit)
    logic [2:0]          bit_n;
    logic [3:0]          byte_n;
    logic [7:0]          rx;
    logic [47:0]         data;        // xl xh yl yh zl zh, first byte in the top
    logic [W_WAIT - 1:0] wait_n;

    wire [3:0] n_bytes = ready ? 4'd8 : 4'd3;

    // byte i of the transaction out (the setup one before measurement mode is set)
    function automatic [7:0] out_byte (input setup_done, input [3:0] i);
        case ({ setup_done, i })
        { 1'b0, 4'd0 }: out_byte = 8'h0A;        // write
        { 1'b0, 4'd1 }: out_byte = 8'h2D;        // POWER_CTL
        { 1'b0, 4'd2 }: out_byte = 8'h02;        // measurement mode
        { 1'b1, 4'd0 }: out_byte = 8'h0B;        // read
        { 1'b1, 4'd1 }: out_byte = 8'h0E;        // from XDATA_L
        default:        out_byte = 8'h00;        // while the data comes in
        endcase
    endfunction

    wire [7:0] tx      = out_byte (ready, byte_n);
    wire [7:0] tx_next = out_byte (ready, byte_n + 1'b1);

    always_ff @ (posedge clk or posedge rst)
        if (rst)
        begin
            state  <= S_WAIT;
            ready  <= 1'b0;
            high   <= 1'b0;
            bit_n  <= '0;
            byte_n <= '0;
            rx     <= '0;
            data   <= '0;
            wait_n <= START_TICKS;
            sclk   <= 1'b0;
            mosi   <= 1'b0;
            cs_n   <= 1'b1;
            valid  <= 1'b0;
            x      <= '0;
            y      <= '0;
            z      <= '0;
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
                    state  <= S_SELECT;
                end
                else
                    wait_n <= wait_n - 1'b1;

            S_SELECT:                               // CS low for a half bit, the first bit out
            begin
                mosi  <= tx [7];
                high  <= 1'b0;
                state <= S_BITS;
            end

            S_BITS:
                if (! high)
                begin                               // rising edge: both sides sample
                    sclk <= 1'b1;
                    high <= 1'b1;
                    rx   <= { rx [6:0], miso_s [1] };
                end
                else
                begin                               // falling edge: the next bit out
                    sclk  <= 1'b0;
                    high  <= 1'b0;
                    bit_n <= bit_n + 1'b1;
                    if (bit_n == 3'd7)
                    begin
                        if (byte_n >= 4'd2)
                            data <= { data [39:0], rx };
                        if (byte_n == n_bytes - 1'b1)
                            state <= S_RELEASE;
                        else
                            byte_n <= byte_n + 1'b1;
                    end
                    mosi <= (bit_n == 3'd7) ? tx_next [7] : tx [3'd6 - bit_n];
                end

            S_RELEASE:
            begin
                cs_n   <= 1'b1;
                mosi   <= 1'b0;
                state  <= S_WAIT;
                wait_n <= GAP_TICKS;
                if (! ready)
                    ready <= 1'b1;
                else
                begin
                    valid  <= 1'b1;
                    x      <= { data [39:32], data [47:40] };
                    y      <= { data [23:16], data [31:24] };
                    z      <= { data [7:0],   data [15:8]  };
                    wait_n <= POLL_TICKS;
                end
            end

            default:
                state <= S_WAIT;

            endcase
        end

endmodule
