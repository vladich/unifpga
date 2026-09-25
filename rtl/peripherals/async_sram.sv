// =============================================================================
// async_sram — an asynchronous SRAM (the DE1's / DE2's 256K x 16, the
// DE2-115's 1M x 16, the Karnix's and the iCE40-HX8K EVB's K6R4016V1D, the
// Nexys 4's cellular RAM in its asynchronous mode) as the `memory` capability:
// one access per request, each ACCESS_NS long.
//
// A read holds the address with CE and OE low for the access time and then
// samples the data. A write holds the address and the data with CE low,
// pulses WE low for the access time (the chip latches the data at WE's rising
// edge) and keeps both one more clock. Byte enables drive LB / UB where the
// chip has them; without them a write is the whole word. `ready` while idle,
// `ack` one clock when the access is done (with `rdata` after a read).
// =============================================================================

module async_sram
# (
    parameter int CLK_MHZ   = 50,
    parameter int ADDR_BITS = 18,
    parameter int ACCESS_NS = 10
)
(
    input                        clk,
    input                        rst,

    input                        req,
    input                        we,
    input  [ADDR_BITS-1:0]       addr,
    input  [15:0]                wdata,
    input  [1:0]                 be,
    output                       ready,
    output logic                 ack,
    output logic [15:0]          rdata,

    output logic [ADDR_BITS-1:0] sram_a,
    inout        [15:0]          sram_d,
    output logic                 sram_ce_n,
    output logic                 sram_oe_n,
    output logic                 sram_we_n,
    output logic                 sram_lb_n,
    output logic                 sram_ub_n
);

    // clocks an access is held: the access time, rounded up, plus two of margin
    // for the pins' own delays
    localparam int HOLD = (ACCESS_NS * CLK_MHZ + 999) / 1000 + 2;

    typedef enum logic [1:0] { S_IDLE, S_ACCESS, S_END } state_t;

    state_t                    state;
    logic [$clog2 (HOLD)-1:0]  t;
    logic                      writing;
    logic [15:0]               wdata_q;

    assign ready  = (state == S_IDLE);
    assign sram_d = (writing && state != S_IDLE) ? wdata_q : 'z;

    always_ff @ (posedge clk or posedge rst)
        if (rst)
        begin
            state     <= S_IDLE;
            t         <= '0;
            writing   <= 1'b0;
            wdata_q   <= '0;
            ack       <= 1'b0;
            rdata     <= '0;
            sram_a    <= '0;
            sram_ce_n <= 1'b1;
            sram_oe_n <= 1'b1;
            sram_we_n <= 1'b1;
            sram_lb_n <= 1'b1;
            sram_ub_n <= 1'b1;
        end
        else
        begin
            ack <= 1'b0;

            case (state)

            S_IDLE:
                if (req)
                begin
                    sram_a    <= addr;
                    writing   <= we;
                    wdata_q   <= wdata;
                    sram_ce_n <= 1'b0;
                    sram_oe_n <= we;                     // a read drives OE,
                    sram_we_n <= ~ we;                   // a write pulses WE
                    sram_lb_n <= we ? ~ be [0] : 1'b0;
                    sram_ub_n <= we ? ~ be [1] : 1'b0;
                    t         <= '0;
                    state     <= S_ACCESS;
                end

            S_ACCESS:
                if (t != HOLD - 1)
                    t <= t + 1'b1;
                else if (! writing)
                begin                                    // the data is there: take it
                    rdata     <= sram_d;
                    ack       <= 1'b1;
                    sram_ce_n <= 1'b1;
                    sram_oe_n <= 1'b1;
                    sram_lb_n <= 1'b1;
                    sram_ub_n <= 1'b1;
                    state     <= S_IDLE;
                end
                else
                begin                                    // WE rises: the chip latches the data
                    sram_we_n <= 1'b1;
                    state     <= S_END;
                end

            S_END:                                       // address and data held one clock after WE
            begin
                sram_ce_n <= 1'b1;
                sram_lb_n <= 1'b1;
                sram_ub_n <= 1'b1;
                ack       <= 1'b1;
                state     <= S_IDLE;
            end

            default:
                state <= S_IDLE;

            endcase
        end

endmodule
