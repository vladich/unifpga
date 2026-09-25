// =============================================================================
// ir_nec_receiver — decodes the NEC infrared remote-control protocol from a
// demodulating receiver (a VS1838B / HS0038 / TSOP: the 38 kHz carrier removed,
// its output low during a burst).
//
// A frame: a 9 ms burst, a 4.5 ms space, then 32 bits, each a 562.5 us burst
// followed by a 562.5 us space for 0 or a 1.6875 ms space for 1, then a closing
// burst: address, inverted address (or a 16-bit address in extended NEC),
// command, inverted command, each least significant bit first. A held key
// repeats every 108 ms as a 9 ms burst, a 2.25 ms space and a closing burst.
// A frame whose command and inverted command disagree is dropped. Durations
// are measured in microseconds with a tolerance of about a quarter.
//
// `valid` is one clock with `address` and `command` of a new frame; `repeat_`
// one clock for each repeat frame (the last `command` stays).
// =============================================================================

module ir_nec_receiver
# (
    parameter CLK_MHZ    = 50,
    parameter ACTIVE_LOW = 1          // the receiver's output is low during a burst
)
(
    input               clk,
    input               rst,
    input               ir,
    output logic        valid,
    output logic [15:0] address,
    output logic [7:0]  command,
    output logic        repeat_
);

    // ---- microsecond tick and the level ---------------------------------------

    localparam int W_DIV = (CLK_MHZ > 1) ? $clog2(CLK_MHZ) : 1;
    localparam [31:0]       DIV_LAST_32 = CLK_MHZ - 1;
    localparam [W_DIV-1:0]  DIV_LAST    = DIV_LAST_32 [W_DIV - 1:0];

    logic [W_DIV-1:0] div;
    wire              us = (div == DIV_LAST);

    always_ff @ (posedge clk or posedge rst)
        if (rst)
            div <= '0;
        else
            div <= us ? '0 : div + 1'b1;

    logic [2:0] ir_s;

    always_ff @ (posedge clk)
        ir_s <= { ir_s [1:0], ir };

    wire mark = ACTIVE_LOW ? ~ ir_s [2] : ir_s [2];   // 1 during a burst
    logic mark_q;

    always_ff @ (posedge clk)
        mark_q <= mark;

    wire mark_start = mark & ~ mark_q;
    wire mark_end   = ~ mark & mark_q;

    // ---- durations, in microseconds ---------------------------------------------

    localparam [15:0] LEAD_MIN = 16'd7000,  LEAD_MAX  = 16'd11000;    // 9 ms burst
    localparam [15:0] HEAD_MIN = 16'd3400,  HEAD_MAX  = 16'd5600;     // 4.5 ms space
    localparam [15:0] RPT_MIN  = 16'd1700,  RPT_MAX   = 16'd2800;     // 2.25 ms space
    localparam [15:0] BIT_MIN  = 16'd300,   BIT_MAX   = 16'd900;      // 562.5 us burst
    localparam [15:0] ONE_MIN  = 16'd1200,  ONE_MAX   = 16'd2200;     // 1.6875 ms space
    localparam [15:0] TIMEOUT  = 16'd12000;                          // nothing this long is a frame

    typedef enum logic [2:0] { S_IDLE, S_LEAD, S_HEAD, S_BIT_MARK, S_BIT_SPACE, S_TAIL, S_RPT_TAIL } state_t;

    state_t      state;
    logic [15:0] t;              // microseconds in the current burst or space
    logic [4:0]  nbit;
    logic [31:0] sh;             // the frame's bits, the first received at bit 0

    always_ff @ (posedge clk or posedge rst)
        if (rst)
        begin
            state   <= S_IDLE;
            t       <= '0;
            nbit    <= '0;
            sh      <= '0;
            valid   <= 1'b0;
            repeat_ <= 1'b0;
            address <= '0;
            command <= '0;
        end
        else
        begin
            valid   <= 1'b0;
            repeat_ <= 1'b0;

            if (us && t != 16'hFFFF)
                t <= t + 1'b1;

            if (mark_start || mark_end)
                t <= '0;

            case (state)

            S_IDLE:
                if (mark_start)
                    state <= S_LEAD;

            S_LEAD:                                 // the 9 ms burst
                if (mark_end)
                    state <= (t >= LEAD_MIN && t <= LEAD_MAX) ? S_HEAD : S_IDLE;

            S_HEAD:                                 // 4.5 ms: a frame; 2.25 ms: a repeat
                if (mark_start)
                begin
                    if (t >= HEAD_MIN && t <= HEAD_MAX)
                    begin
                        nbit  <= '0;
                        state <= S_BIT_MARK;
                    end
                    else if (t >= RPT_MIN && t <= RPT_MAX)
                        state <= S_RPT_TAIL;
                    else
                        state <= S_LEAD;            // another leading burst
                end
                else if (t > TIMEOUT)
                    state <= S_IDLE;

            S_BIT_MARK:                             // a bit's burst
                if (mark_end)
                    state <= (t >= BIT_MIN && t <= BIT_MAX) ? S_BIT_SPACE : S_IDLE;

            S_BIT_SPACE:                            // its space: short 0, long 1
                if (mark_start)
                begin
                    if (t >= BIT_MIN && t <= BIT_MAX)
                        sh <= { 1'b0, sh [31:1] };
                    else if (t >= ONE_MIN && t <= ONE_MAX)
                        sh <= { 1'b1, sh [31:1] };
                    else
                        state <= S_LEAD;
                    if ((t >= BIT_MIN && t <= BIT_MAX) || (t >= ONE_MIN && t <= ONE_MAX))
                    begin
                        nbit  <= nbit + 1'b1;
                        state <= (nbit == 5'd31) ? S_TAIL : S_BIT_MARK;
                    end
                end
                else if (t > TIMEOUT)
                    state <= S_IDLE;

            S_TAIL:                                 // the closing burst: the frame is complete
                if (mark_end)
                begin
                    state <= S_IDLE;
                    if (t >= BIT_MIN && t <= BIT_MAX && sh [31:24] == ~ sh [23:16])
                    begin
                        address <= sh [15:0];
                        command <= sh [23:16];
                        valid   <= 1'b1;
                    end
                end

            S_RPT_TAIL:                             // the repeat's closing burst
                if (mark_end)
                begin
                    state <= S_IDLE;
                    if (t >= BIT_MIN && t <= BIT_MAX)
                        repeat_ <= 1'b1;
                end

            default:
                state <= S_IDLE;

            endcase
        end

endmodule
