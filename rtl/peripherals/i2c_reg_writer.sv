// =============================================================================
// i2c_reg_writer — programs a table of 8-bit device registers over I2C after
// reset. Each entry is one write transaction: START, the device address (write
// form), the register address, the value, each byte acknowledged by the
// device, then STOP. An entry the device does not acknowledge is written
// again after a short idle gap. Once the table is done, `restart` (level,
// sampled on the bit tick) writes it again from entry RESTART_AT — the
// ADV7513's hot-plug interrupt uses it.
//
// TABLE holds N entries of 24 bits, {device address, register, value}, the
// first entry in the most significant bits. SCL is driven push-pull (single
// master, no clock stretching); SDA is open drain (driven low or released).
// =============================================================================

module i2c_reg_writer
# (
    parameter              CLK_MHZ    = 50,
    parameter              SCL_KHZ    = 20,
    parameter              N          = 1,
    parameter              RESTART_AT = 0,
    parameter [N * 24 - 1:0] TABLE    = '0
)
(
    input        clk,
    input        rst,
    input        restart,
    output logic scl,
    inout        sda,
    output logic done
);

    // ---- bit tick: four per SCL period ---------------------------------------

    localparam int QUARTER = (CLK_MHZ * 1000 / (SCL_KHZ * 4) > 1) ? CLK_MHZ * 1000 / (SCL_KHZ * 4) : 1;
    localparam int W_DIV   = (QUARTER > 1) ? $clog2(QUARTER) : 1;
    localparam int W_IDX   = (N > 1) ? $clog2(N) : 1;
    localparam int GAP     = 16;                   // idle ticks before every transaction

    localparam [31:0]        DIV_LAST_32    = QUARTER - 1;
    localparam [31:0]        IDX_LAST_32    = N - 1;
    localparam [31:0]        IDX_RESTART_32 = RESTART_AT;
    localparam [31:0]        GAP_LAST_32    = GAP - 1;
    localparam [W_DIV - 1:0] DIV_LAST       = DIV_LAST_32    [W_DIV - 1:0];
    localparam [W_IDX - 1:0] IDX_LAST       = IDX_LAST_32    [W_IDX - 1:0];
    localparam [W_IDX - 1:0] IDX_RESTART    = IDX_RESTART_32 [W_IDX - 1:0];
    localparam [4:0]         GAP_LAST       = GAP_LAST_32    [4:0];

    logic [W_DIV - 1:0] div;
    wire                tick = (div == DIV_LAST);

    always_ff @ (posedge clk or posedge rst)
        if (rst)
            div <= '0;
        else
            div <= tick ? '0 : div + 1'b1;

    // ---- transaction sequencer ----------------------------------------------

    typedef enum logic [2:0] { S_GAP, S_START, S_BITS, S_STOP, S_DONE } state_t;

    state_t             state;
    logic [1:0]         phase;       // quarter of the current SCL period
    logic [1:0]         byte_n;      // 0 address, 1 register, 2 value
    logic [3:0]         pos;         // bit of the byte, 8 = acknowledge slot
    logic [4:0]         gap;
    logic [W_IDX - 1:0] idx;
    logic               sda_low;
    logic               nack;

    wire [31:0] idx_w    = { { (32 - W_IDX) { 1'b0 } }, idx };
    wire [23:0] entry    = TABLE[(N - 1 - idx_w) * 24 +: 24];
    wire [7:0]  byte_out = entry[23 - byte_n * 8 -: 8];
    wire        ack      = (sda == 1'b0);

    assign sda = sda_low ? 1'b0 : 1'bz;

    always_ff @ (posedge clk or posedge rst)
        if (rst)
        begin
            state   <= S_GAP;
            phase   <= '0;
            byte_n  <= '0;
            pos     <= '0;
            gap     <= '0;
            idx     <= '0;
            scl     <= 1'b1;
            sda_low <= 1'b0;
            nack    <= 1'b0;
            done    <= 1'b0;
        end
        else if (tick)
        begin
            case (state)

            S_GAP:                                  // bus idle: SCL high, SDA released
                if (gap == GAP_LAST)
                begin
                    gap   <= '0;
                    phase <= '0;
                    state <= S_START;
                end
                else
                    gap <= gap + 1'b1;

            S_START:                                // SDA falls while SCL is high
            begin
                phase <= phase + 1'b1;
                if (phase == 2'd0)
                    sda_low <= 1'b1;
                else
                begin
                    scl    <= 1'b0;
                    phase  <= '0;
                    byte_n <= '0;
                    pos    <= '0;
                    nack   <= 1'b0;
                    state  <= S_BITS;
                end
            end

            S_BITS:
            begin
                phase <= phase + 1'b1;
                case (phase)
                2'd0: sda_low <= (pos == 4'd8) ? 1'b0 : ~ byte_out[3'd7 - pos[2:0]];
                2'd1: scl     <= 1'b1;
                2'd2: if (pos == 4'd8 && ! ack) nack <= 1'b1;
                2'd3:
                begin
                    scl <= 1'b0;
                    if (pos != 4'd8)
                        pos <= pos + 1'b1;
                    else if (nack || byte_n == 2'd2)
                        state <= S_STOP;
                    else
                    begin
                        pos    <= '0;
                        byte_n <= byte_n + 1'b1;
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
                    if (nack)
                        state <= S_GAP;             // the same entry again
                    else if (idx == IDX_LAST)
                    begin
                        done  <= 1'b1;
                        state <= S_DONE;
                    end
                    else
                    begin
                        idx   <= idx + 1'b1;
                        state <= S_GAP;
                    end
                endcase
            end

            S_DONE:
                if (restart)
                begin
                    done  <= 1'b0;
                    idx   <= IDX_RESTART;
                    state <= S_GAP;
                end

            default:
                state <= S_GAP;

            endcase
        end

endmodule
