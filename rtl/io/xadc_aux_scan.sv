// =============================================================================
// xadc_aux_scan — the XADC of a Xilinx 7-series FPGA converting a board's
// auxiliary analog inputs in turn (the Basys 3's and Nexys A7's JXADC pairs,
// the Arty A7's shield analog pins), in the `adc` capability's terms: one
// clock of `valid` per conversion with `channel`, the pair's position in
// CHANNELS, and `value`, the 12-bit code (0 .. 4095 for 0 .. 1 V between the
// pair's pins; a board that divides its input says so in full_scale_mv).
//
// CHANNELS is the table of VAUX numbers the pairs are wired to, 8 bits each,
// pair i at [8 * i +: 8] ({8'd15, 8'd7, 8'd14, 8'd6}: XA1..XA4 of a Basys 3
// are VAUX 6, 14, 7, 15). The sequencer converts those channels continuously
// (unipolar, no averaging, extended acquisition time for high-impedance
// sources); at each end of conversion the result register is read over the
// DRP. Everything derived from the table is a plain constant expression
// (no constant functions: Icarus does not evaluate them in generate blocks).
//
// Vendor layer: it instantiates the XADC primitive, so it lives in rtl/io and
// is collected only where a peripheral asks for it.
// =============================================================================

module xadc_aux_scan
# (
    parameter CLK_MHZ  = 100,
    parameter CHANNELS = { 8'd15, 8'd7, 8'd14, 8'd6 }
)
(
    input                                clk,
    input                                rst,
    input  [$bits (CHANNELS) / 8 - 1:0]  vaux_p,
    input  [$bits (CHANNELS) / 8 - 1:0]  vaux_n,
    output logic                         valid,
    output logic [3:0]                   channel,   // the pair's position in CHANNELS
    output logic [11:0]                  value
);

    localparam int N = $bits (CHANNELS) / 8;

    // ---- the table: pair i is on VAUX TABLE [8 * i +: 4] ---------------------------

    localparam [8 * 16 - 1:0] TABLE = CHANNELS;               // room for 16 pairs

    `define XA_ENABLED(i) (((i) < N) ? 16'd1 << TABLE [8 * (i) +: 4] : 16'd0)
    localparam [15:0] MASK = `XA_ENABLED (0)  | `XA_ENABLED (1)  | `XA_ENABLED (2)  | `XA_ENABLED (3)
                           | `XA_ENABLED (4)  | `XA_ENABLED (5)  | `XA_ENABLED (6)  | `XA_ENABLED (7)
                           | `XA_ENABLED (8)  | `XA_ENABLED (9)  | `XA_ENABLED (10) | `XA_ENABLED (11)
                           | `XA_ENABLED (12) | `XA_ENABLED (13) | `XA_ENABLED (14) | `XA_ENABLED (15);
    `undef XA_ENABLED

    // ADCCLK = DCLK / DIV, at most 26 MHz
    localparam int DIV = ((CLK_MHZ + 25) / 26 < 2) ? 2 : (CLK_MHZ + 25) / 26;

    // ---- the analog pins, by VAUX number -------------------------------------------

    wire [15:0] vauxp, vauxn;

    genvar c, i;
    generate
        for (c = 0; c < 16; c++)
        begin : g_vaux
            if (! MASK [c])
            begin : g_open
                assign vauxp [c] = 1'b0;
                assign vauxn [c] = 1'b0;
            end
            for (i = 0; i < N; i++)
            begin : g_pair
                if (TABLE [8 * i +: 4] == c)
                begin : g_wired
                    assign vauxp [c] = vaux_p [i];
                    assign vauxn [c] = vaux_n [i];
                end
            end
        end
    endgenerate

    // ---- the converter ---------------------------------------------------------------

    logic [6:0]  daddr;
    logic        den;
    wire  [15:0] drp_do;
    wire         drdy, eoc;
    wire  [4:0]  chan;

    XADC
    # (
        .INIT_40 (16'h0000),                       // no averaging
        .INIT_41 (16'h2FFF),                       // continuous sequence, alarms off, calibration on
        .INIT_42 (16' (DIV << 8)),                 // ADCCLK divider
        .INIT_48 (16'h0001),                       // sequence: the calibration channel
        .INIT_49 (MASK),                           //   and these auxiliary channels
        .INIT_4A (16'h0000), .INIT_4B (16'h0000),  // no averaging
        .INIT_4C (16'h0000), .INIT_4D (16'h0000),  // unipolar
        .INIT_4E (16'h0000), .INIT_4F (MASK)       // extended acquisition on the auxiliary channels
    )
    i_xadc
    (
        .DCLK (clk), .RESET (rst),
        .DADDR (daddr), .DEN (den), .DWE (1'b0), .DI (16'h0000), .DO (drp_do), .DRDY (drdy),
        .CONVST (1'b0), .CONVSTCLK (1'b0),
        .VP (1'b0), .VN (1'b0), .VAUXP (vauxp), .VAUXN (vauxn),
        .EOC (eoc), .EOS (), .BUSY (), .CHANNEL (chan), .ALM (), .OT (),
        .JTAGBUSY (), .JTAGLOCKED (), .JTAGMODIFIED (), .MUXADDR ()
    );

    // the pair on the VAUX channel just converted (CHANNEL 16 + n), if any
    logic [3:0] pair;
    logic       ours;

    always_comb
    begin
        pair = '0;
        ours = 1'b0;
        for (int k = 0; k < N; k++)
            if (chan [4] && TABLE [8 * k +: 4] == chan [3:0])
            begin
                pair = 4' (k);
                ours = 1'b1;
            end
    end

    // at each end of conversion of one of our channels: read its result
    // register (0x10 + n) and hand it over as that pair's value
    logic [3:0] pending;

    always_ff @ (posedge clk or posedge rst)
        if (rst)
        begin
            daddr   <= '0;
            den     <= 1'b0;
            pending <= '0;
            valid   <= 1'b0;
            channel <= '0;
            value   <= '0;
        end
        else
        begin
            den   <= 1'b0;
            valid <= 1'b0;
            if (eoc && ours)
            begin
                daddr   <= { 2'b00, chan };
                den     <= 1'b1;
                pending <= pair;
            end
            if (drdy)
            begin
                channel <= pending;
                value   <= drp_do [15:4];
                valid   <= 1'b1;
            end
        end

endmodule
