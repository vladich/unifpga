// =============================================================================
// adc128s022_scan — scans the channels of a TI ADC128S022 (8 channels, 12 bits;
// the DE0-Nano's A/D converter) in turn: one conversion per 16-clock frame.
//
// Datasheet (SNAS298, "Serial Interface"): a frame starts with CS falling while
// SCLK is high; SCLK falling edges 1-4 clock out leading zeros, 5-16 the result,
// MSB first; DIN is sampled on the first 8 rising edges, its bits 5..3 (rising
// edges 3-5) the channel of the NEXT conversion. The first conversion after
// power-up is of IN0. SCLK must be 0.8 - 3.2 MHz.
// =============================================================================

module adc128s022_scan
# (
    parameter CLK_MHZ  = 50,
    parameter SCLK_KHZ = 2000,
    parameter CHANNELS = 8
)
(
    input               clk,
    input               rst,
    output logic        cs_n,
    output logic        sclk,
    output logic        din,
    input               dout,
    output logic        valid,       // one clock: a conversion
    output logic [3:0]  channel,
    output logic [11:0] value
);

    localparam int HALF  = (CLK_MHZ * 1000 / (SCLK_KHZ * 2) > 1) ? CLK_MHZ * 1000 / (SCLK_KHZ * 2) : 1;
    localparam int W_DIV = (HALF > 1) ? $clog2(HALF) : 1;

    localparam [31:0]        DIV_LAST_32 = HALF - 1;
    localparam [W_DIV - 1:0] DIV_LAST    = DIV_LAST_32 [W_DIV - 1:0];
    localparam [31:0]        LAST_CH_32  = CHANNELS - 1;
    localparam [2:0]         LAST_CH     = LAST_CH_32 [2:0];

    logic [W_DIV - 1:0] div;
    wire                tick = (div == DIV_LAST);

    always_ff @ (posedge clk or posedge rst)
        if (rst)
            div <= '0;
        else
            div <= tick ? '0 : div + 1'b1;

    logic [1:0] dout_s;

    always_ff @ (posedge clk)
        dout_s <= { dout_s [0], dout };

    // ---- frames ----------------------------------------------------------------

    typedef enum logic [1:0] { S_IDLE, S_SELECT, S_BITS, S_END } state_t;

    state_t      state;
    logic        low;         // SCLK is low (first half of the bit)
    logic [4:0]  k;           // SCLK cycles done in this frame
    logic [15:0] rx;
    logic [2:0]  conv_ch;     // the channel this frame converts
    logic [2:0]  next_ch;     // the channel this frame asks for next

    wire [7:0] control = { 2'b00, next_ch, 3'b000 };

    always_ff @ (posedge clk or posedge rst)
        if (rst)
        begin
            state   <= S_IDLE;
            low     <= 1'b0;
            k       <= '0;
            rx      <= '0;
            conv_ch <= 3'd0;                        // the first conversion is of IN0
            next_ch <= (LAST_CH == 3'd0) ? 3'd0 : 3'd1;
            cs_n    <= 1'b1;
            sclk    <= 1'b1;
            din     <= 1'b0;
            valid   <= 1'b0;
            channel <= '0;
            value   <= '0;
        end
        else
        begin
            valid <= 1'b0;

            if (tick)
            case (state)

            S_IDLE:                                 // CS high for a half bit, then low with SCLK high
            begin
                cs_n  <= 1'b0;
                k     <= '0;
                state <= S_SELECT;
            end

            S_SELECT:                               // CS set-up before the first edge
                state <= S_BITS;

            S_BITS:
                if (! low)
                begin                               // falling edge k + 1: the ADC's next bit, our DIN bit
                    sclk <= 1'b0;
                    low  <= 1'b1;
                    din  <= (k < 5'd8) ? control [3'd7 - k [2:0]] : 1'b0;
                end
                else
                begin                               // rising edge k + 1: both sides sample
                    sclk <= 1'b1;
                    low  <= 1'b0;
                    rx   <= { rx [14:0], dout_s [1] };
                    k    <= k + 1'b1;
                    if (k == 5'd15)
                        state <= S_END;
                end

            S_END:                                  // the frame's result; the next channels
            begin
                cs_n    <= 1'b1;
                din     <= 1'b0;
                valid   <= 1'b1;
                channel <= { 1'b0, conv_ch };
                value   <= rx [11:0];
                conv_ch <= next_ch;
                next_ch <= (next_ch == LAST_CH) ? 3'd0 : next_ch + 1'b1;
                state   <= S_IDLE;
            end

            default:
                state <= S_IDLE;

            endcase
        end

endmodule
