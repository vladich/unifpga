// =============================================================================
// sdram_sdr — a single-data-rate SDRAM of four banks, x16 or x32 (the
// HY57V641620 / IS42S16400 of the Cyclone IV boards, the IS42S16320D of the
// DE10-Lite and DE0-CV, the two chips of the DE2-115) as the `memory`
// capability: one word per request, the word address being {bank, row, column}.
//
// The chip's clock is the design clock inverted: the chip samples a command
// half a period after this module sets it and this module samples the chip's
// data half a period after the chip drives them (a design clock of up to
// about 66 MHz). Burst length 1, CAS latency CAS, every access with auto
// precharge (ACTIVATE, then READ or WRITE tRCD later, then tRP, and no
// ACTIVATE within tRC of the last); an AUTO REFRESH whenever one is due and
// the bus is idle (all rows within 64 ms, with a tenth to spare). At power-up
// INIT_US of NOPs with CKE high, a PRECHARGE ALL, eight refreshes and the
// mode register. `ready` while idle and no refresh is due; `ack` one clock
// when the access is done (with `rdata` after a read). Timings in ns round up
// to clocks. Byte enables drive DQM on a write (a chip without DQM pins
// writes whole words).
// =============================================================================

module sdram_sdr
# (
    parameter int CLK_MHZ   = 50,
    parameter int ROW_BITS  = 12,       // address pins, and the row address
    parameter int COL_BITS  = 8,        // at most 10 (A10 is the auto-precharge flag)
    parameter int BANK_BITS = 2,
    parameter int DATA_BITS = 16,
    parameter int CAS       = 2,
    parameter int T_RCD_NS  = 20,       // ACTIVATE to READ / WRITE
    parameter int T_RP_NS   = 20,       // PRECHARGE to the next command
    parameter int T_RC_NS   = 66,       // ACTIVATE to ACTIVATE
    parameter int T_RFC_NS  = 70,       // AUTO REFRESH to the next command
    parameter int T_WR_CLK  = 2,        // the last written data to PRECHARGE, clocks
    parameter int INIT_US   = 200,
    parameter int ADDR_BITS = BANK_BITS + ROW_BITS + COL_BITS
)
(
    input                            clk,
    input                            rst,

    input                            req,
    input                            we,
    input  [ADDR_BITS-1:0]           addr,
    input  [DATA_BITS-1:0]           wdata,
    input  [DATA_BITS/8-1:0]         be,
    output                           ready,
    output logic                     ack,
    output logic [DATA_BITS-1:0]     rdata,

    output                           sdram_clk,
    output                           sdram_cke,
    output logic                     sdram_cs_n,
    output logic                     sdram_ras_n,
    output logic                     sdram_cas_n,
    output logic                     sdram_we_n,
    output logic [BANK_BITS-1:0]     sdram_ba,
    output logic [ROW_BITS-1:0]      sdram_a,
    inout        [DATA_BITS-1:0]     sdram_dq,
    output logic [DATA_BITS/8-1:0]   sdram_dqm
);

    // ---- timings, in clocks ---------------------------------------------------------

    localparam int C_RCD  = (T_RCD_NS * CLK_MHZ + 999) / 1000 > 0 ? (T_RCD_NS * CLK_MHZ + 999) / 1000 : 1;
    localparam int C_RP   = (T_RP_NS  * CLK_MHZ + 999) / 1000 > 0 ? (T_RP_NS  * CLK_MHZ + 999) / 1000 : 1;
    localparam int C_RC   = (T_RC_NS  * CLK_MHZ + 999) / 1000;
    localparam int C_RFC  = (T_RFC_NS * CLK_MHZ + 999) / 1000 > 0 ? (T_RFC_NS * CLK_MHZ + 999) / 1000 : 1;
    localparam int C_INIT = INIT_US * CLK_MHZ;
    localparam int C_REF  = (64_000 * CLK_MHZ * 9 / 10) / (1 << ROW_BITS);   // a row's turn, 64 ms over all rows

    // the mode register: burst length 1, sequential, CAS latency CAS
    localparam [ROW_BITS-1:0] MODE = ROW_BITS' ({ 3' (CAS), 4'b0000 });

    // ---- commands: {cs_n, ras_n, cas_n, we_n} ----------------------------------------

    localparam [3:0] CMD_NOP = 4'b0111, CMD_ACTIVE = 4'b0011, CMD_READ = 4'b0101, CMD_WRITE = 4'b0100,
                     CMD_PRECHARGE = 4'b0010, CMD_REFRESH = 4'b0001, CMD_MODE = 4'b0000;

    logic [3:0] cmd;
    assign { sdram_cs_n, sdram_ras_n, sdram_cas_n, sdram_we_n } = cmd;
    assign sdram_clk = ~ clk;
    assign sdram_cke = 1'b1;

    // ---- the sequence -----------------------------------------------------------------

    typedef enum logic [3:0] { S_INIT_WAIT, S_INIT_PRE, S_INIT_REF, S_INIT_MODE,
                               S_IDLE, S_ACT, S_RW, S_PRE, S_REFRESH } state_t;

    state_t                    state;
    logic [19:0]               t;              // clocks left in the current step (a step of N ends N + 1 clocks on)
    logic [3:0]                ref_left;       // the power-up refreshes
    logic [19:0]               ref_cnt;        // clocks since the last refresh
    logic                      refresh_due;
    logic [7:0]                since_act;      // clocks since ACTIVATE, saturating
    logic                      writing;
    logic [COL_BITS-1:0]       col;
    logic [DATA_BITS-1:0]      dq_out;
    logic                      dq_oe;

    assign sdram_dq = dq_oe ? dq_out : 'z;
    assign ready    = (state == S_IDLE) && ! refresh_due;

    // the column command's address: the column on A0.., auto precharge on A10
    function automatic [ROW_BITS-1:0] col_address (input [COL_BITS-1:0] c);
        col_address = '0;
        col_address [COL_BITS-1:0] = c;
        col_address [10] = 1'b1;
    endfunction

    always_ff @ (posedge clk or posedge rst)
        if (rst)
        begin
            state       <= S_INIT_WAIT;
            t           <= 20' (C_INIT);
            ref_left    <= '0;
            ref_cnt     <= '0;
            refresh_due <= 1'b0;
            since_act   <= '1;
            writing     <= 1'b0;
            col         <= '0;
            dq_out      <= '0;
            dq_oe       <= 1'b0;
            ack         <= 1'b0;
            rdata       <= '0;
            cmd         <= CMD_NOP;
            sdram_ba    <= '0;
            sdram_a     <= '0;
            sdram_dqm   <= '0;
        end
        else
        begin
            cmd   <= CMD_NOP;
            dq_oe <= 1'b0;
            ack   <= 1'b0;
            if (t != 0)
                t <= t - 1'b1;
            if (since_act != '1)
                since_act <= since_act + 1'b1;
            if (ref_cnt == 20' (C_REF - 1))
                refresh_due <= 1'b1;
            else
                ref_cnt <= ref_cnt + 1'b1;

            case (state)

            S_INIT_WAIT:                             // INIT_US of NOPs, then precharge all
                if (t == 0)
                begin
                    cmd     <= CMD_PRECHARGE;
                    sdram_a <= '0;
                    sdram_a [10] <= 1'b1;
                    t       <= 20' (C_RP);
                    state   <= S_INIT_PRE;
                end

            S_INIT_PRE:
                if (t == 0)
                begin
                    cmd      <= CMD_REFRESH;
                    ref_left <= 4'd7;
                    t        <= 20' (C_RFC);
                    state    <= S_INIT_REF;
                end

            S_INIT_REF:                              // eight refreshes, then the mode register
                if (t == 0)
                begin
                    if (ref_left != 0)
                    begin
                        cmd      <= CMD_REFRESH;
                        ref_left <= ref_left - 1'b1;
                        t        <= 20' (C_RFC);
                    end
                    else
                    begin
                        cmd      <= CMD_MODE;
                        sdram_ba <= '0;
                        sdram_a  <= MODE;
                        t        <= 20'd2;
                        state    <= S_INIT_MODE;
                    end
                end

            S_INIT_MODE:
                if (t == 0)
                begin
                    ref_cnt     <= '0;
                    refresh_due <= 1'b0;
                    state       <= S_IDLE;
                end

            S_IDLE:
                if (refresh_due)
                begin
                    cmd         <= CMD_REFRESH;
                    ref_cnt     <= '0;
                    refresh_due <= 1'b0;
                    t           <= 20' (C_RFC);
                    state       <= S_REFRESH;
                end
                else if (req)
                begin                                // open the row
                    cmd       <= CMD_ACTIVE;
                    sdram_ba  <= addr [ADDR_BITS-1 -: BANK_BITS];
                    sdram_a   <= addr [COL_BITS +: ROW_BITS];
                    col       <= addr [COL_BITS-1:0];
                    writing   <= we;
                    dq_out    <= wdata;
                    sdram_dqm <= we ? ~ be : '0;
                    since_act <= '0;
                    t         <= 20' (C_RCD);
                    state     <= S_ACT;
                end

            S_ACT:                                   // tRCD later: the column command
                if (t == 0)
                begin
                    cmd     <= writing ? CMD_WRITE : CMD_READ;
                    sdram_a <= col_address (col);
                    dq_oe   <= writing;
                    t       <= writing ? 20' (T_WR_CLK) : 20' (CAS);   // a step of N ends N + 1 clocks on
                    state   <= S_RW;
                end

            S_RW:                                    // a read's data CAS + 1 clocks after the command; a write's recovery
                if (t == 0)
                begin
                    if (! writing)
                        rdata <= sdram_dq;
                    ack       <= 1'b1;
                    sdram_dqm <= '0;
                    t         <= 20' (C_RP);
                    state     <= S_PRE;
                end

            S_PRE:                                   // the auto precharge, and tRC since the ACTIVATE
                if (t == 0 && since_act >= 8' (C_RC))
                    state <= S_IDLE;

            S_REFRESH:
                if (t == 0)
                    state <= S_IDLE;

            default:
                state <= S_IDLE;

            endcase
        end

endmodule
