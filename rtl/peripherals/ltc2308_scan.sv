// =============================================================================
// ltc2308_scan — scans the channels of a Linear / Analog Devices LTC2308
// (8 channels, 12 bits, internal 4.096 V reference; the A/D converter of the
// DE0-Nano-SoC, DE10-Nano and Cyclone V GX Starter Kit) in turn, single-ended
// and unipolar: one conversion per frame, a code of 1 mV.
//
// Datasheet (LTC2308 2308fc, "Timing and Control"): a CONVST rising edge
// starts the conversion (at most 1.6 us); CONVST is high at least 20 ns and,
// for best performance, low again within 40 ns: a pulse of ceil(20 ns * clock)
// clock cycles. With CONVST low, SDO shows the result's MSB and SCK's falling
// edges shift out the rest, 12 bits. SDI is latched on SCK's rising edges: the 6-bit word
// { S/D, O/S, S1, S0, UNI, SLP } configures the NEXT conversion. Single-ended
// channel c is S/D = 1, O/S = c[0], S1 = c[2], S0 = c[1]; UNI = 1, SLP = 0.
// The first frame's result (its configuration unknown) is not given out.
// =============================================================================

module ltc2308_scan
# (
    parameter CLK_MHZ  = 50,
    parameter SCK_KHZ  = 2000,
    parameter CHANNELS = 8,
    parameter CONV_NS  = 1600
)
(
    input               clk,
    input               rst,
    output logic        convst,
    output logic        sck,
    output logic        sdi,
    input               sdo,
    output logic        valid,       // one clock: a conversion
    output logic [3:0]  channel,
    output logic [11:0] value
);

    localparam int HALF    = (CLK_MHZ * 1000 / (SCK_KHZ * 2) > 1) ? CLK_MHZ * 1000 / (SCK_KHZ * 2) : 1;
    localparam int W_DIV   = (HALF > 1) ? $clog2(HALF) : 1;
    localparam int TICK_NS = 1000000 / (SCK_KHZ * 2);
    localparam int CONV    = CONV_NS / TICK_NS + 2;           // ticks from CONVST to the data, with margin
    localparam int W_WAIT  = $clog2(CONV + 1);
    localparam int PULSE   = (20 * CLK_MHZ + 999) / 1000 > 1 ? (20 * CLK_MHZ + 999) / 1000 : 1;
    localparam int W_PULSE = $clog2(PULSE + 1);

    localparam [31:0]         DIV_LAST_32 = HALF - 1;
    localparam [W_DIV - 1:0]  DIV_LAST    = DIV_LAST_32 [W_DIV - 1:0];
    localparam [31:0]         CONV_32     = CONV;
    localparam [W_WAIT - 1:0] CONV_TICKS  = CONV_32 [W_WAIT - 1:0];
    localparam [31:0]         LAST_CH_32  = CHANNELS - 1;
    localparam [2:0]          LAST_CH     = LAST_CH_32 [2:0];
    localparam [31:0]         PULSE_32    = PULSE - 1;
    localparam [W_PULSE-1:0]  PULSE_LAST  = PULSE_32 [W_PULSE - 1:0];

    logic [W_DIV - 1:0] div;
    wire                tick = (div == DIV_LAST);

    always_ff @ (posedge clk or posedge rst)
        if (rst)
            div <= '0;
        else
            div <= tick ? '0 : div + 1'b1;

    logic [1:0] sdo_s;

    always_ff @ (posedge clk)
        sdo_s <= { sdo_s [0], sdo };

    // ---- frames ----------------------------------------------------------------

    typedef enum logic [2:0] { S_START, S_CONV, S_BITS, S_END } state_t;

    state_t              state;
    logic                high;        // SCK is high (second half of the bit)
    logic [3:0]          k;           // SCK cycles done in this frame
    logic [11:0]         rx;
    logic                primed;      // a frame has configured the conversion
    logic [2:0]          conv_ch;     // the channel this frame converts
    logic [2:0]          next_ch;     // the channel this frame configures
    logic [W_WAIT - 1:0] wait_n;
    logic [W_PULSE-1:0]  pulse_n;     // clock cycles of CONVST high left

    wire [5:0] config_word = { 1'b1, next_ch [0], next_ch [2], next_ch [1], 1'b1, 1'b0 };

    always_ff @ (posedge clk or posedge rst)
        if (rst)
        begin
            state   <= S_START;
            high    <= 1'b0;
            k       <= '0;
            rx      <= '0;
            primed  <= 1'b0;
            conv_ch <= '0;
            next_ch <= '0;
            wait_n  <= '0;
            pulse_n <= '0;
            convst  <= 1'b0;
            sck     <= 1'b0;
            sdi     <= 1'b0;
            valid   <= 1'b0;
            channel <= '0;
            value   <= '0;
        end
        else
        begin
            valid <= 1'b0;

            if (convst)                             // the pulse ends after PULSE clock cycles
            begin
                if (pulse_n == '0)
                    convst <= 1'b0;
                else
                    pulse_n <= pulse_n - 1'b1;
            end

            if (tick)
            case (state)

            S_START:                                // the CONVST pulse: the conversion starts
            begin
                convst  <= 1'b1;
                pulse_n <= PULSE_LAST;
                wait_n  <= CONV_TICKS;
                state   <= S_CONV;
            end

            S_CONV:                                 // it converts
            begin
                if (wait_n == '0)
                begin
                    k     <= '0;
                    high  <= 1'b0;
                    sdi   <= config_word [5];
                    state <= S_BITS;
                end
                else
                    wait_n <= wait_n - 1'b1;
            end

            S_BITS:
                if (! high)
                begin                               // rising edge k + 1: we take SDO, it takes SDI
                    sck  <= 1'b1;
                    high <= 1'b1;
                    rx   <= { rx [10:0], sdo_s [1] };
                end
                else
                begin                               // falling edge: its next bit, our next SDI bit
                    sck  <= 1'b0;
                    high <= 1'b0;
                    k    <= k + 1'b1;
                    sdi  <= (k < 4'd5) ? config_word [3'd4 - k [2:0]] : 1'b0;
                    if (k == 4'd11)
                        state <= S_END;
                end

            S_END:
            begin
                sdi     <= 1'b0;
                valid   <= primed;
                channel <= { 1'b0, conv_ch };
                value   <= rx;
                primed  <= 1'b1;
                conv_ch <= next_ch;
                next_ch <= (next_ch == LAST_CH) ? 3'd0 : next_ch + 1'b1;
                state   <= S_START;
            end

            default:
                state <= S_START;

            endcase
        end

endmodule
