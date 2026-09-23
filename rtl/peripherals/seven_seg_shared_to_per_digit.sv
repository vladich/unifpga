// =============================================================================
// seven_seg_shared_to_per_digit
//
// Adapter for boards whose 7-segment display has independent per-digit segment
// lines (Terasic DE0 / DE1 / DE2 / DE2-115 / DE0-CV / DE10-Lite / DE1-SoC,
// Cyclone V GX starter: `HEX0[7:0]`, `HEX1[7:0]`, ...). The capability presents
// the shared-segments + digit-select model to user code; this adapter fans the
// currently selected digit's segment pattern out to that digit's own pins.
//
// Bit order (matches config/capabilities/seven_segment.yml):
//   abcdefgh[7] = a, [6] = b, ..., [1] = g, [0] = h (decimal point)
// Board HEX buses are wired the other way round, `HEXn[0] = a ... HEXn[6] = g,
// HEXn[7] = dp` (Terasic), so the pattern is bit-reversed here (`hgfedcba`).
//
// Parameters
//   digits      number of digit positions (HEX0..HEX<digits-1>)
//   segs        pins per digit: 8 (with decimal point) or 7 (DE0-CV has no dp)
//   active      "low" for common-anode displays (all the Terasic boards),
//               "high" otherwise; same vocabulary as the peripheral YAML
//   latched     0: combinational fan-out, only the selected digit is lit at any
//                  instant;
//               1: each digit keeps the last pattern written while it was
//                  selected, so all digits appear lit at once.
//
// hex_o is flat: bits [d*segs +: segs] belong to digit d, LSB = segment a.
// =============================================================================

module seven_seg_shared_to_per_digit
# (
    parameter int    digits  = 6,
    parameter int    segs    = 8,
    parameter        active    = "low",   // "low" / "high" (untyped: yosys 0.41 rejects `parameter string`)
    parameter bit    latched   = 1'b0,
    parameter        dp_active = "high"   // polarity of dp_o (the LEDs it lands on when the HEX has no dp pin)
)
(
    input                              clk,
    input                              rst,
    input        [7:0]                 abcdefgh,
    input        [digits - 1:0]        digit,
    output logic [digits * segs - 1:0] hex_o,
    // The decimal point per digit for a display whose dp segment is not
    // wired to the FPGA (Terasic DE0-CV, DE1-SoC, DE2-115, C5GX, DE23-Lite):
    // shown on the board's top w_digit LEDs. Latched / combinational
    // like hex_o, with its own polarity.
    output logic [digits - 1:0]        dp_o
);

    // abcdefgh -> hgfedcba (bit reversal), then keep the low `segs` bits:
    // hgfedcba[0] = a ... hgfedcba[6] = g, hgfedcba[7] = dp.
    logic [7:0] hgfedcba;

    always_comb
        for (int i = 0; i < 8; i ++)
            hgfedcba [i] = abcdefgh [7 - i];

    localparam bit active_low = (active == "low");

    wire [segs - 1:0] pattern = hgfedcba [segs - 1:0];
    wire [segs - 1:0] lit     = active_low ? ~ pattern : pattern;
    wire [segs - 1:0] blank   = active_low ? '1 : '0;

    localparam bit dp_low = (dp_active == "low");
    wire dp_lit   = dp_low ? ~ hgfedcba [7] : hgfedcba [7];
    wire dp_blank = dp_low;

    generate
        if (latched) begin : g_latched

            // Async reset, all off on reset
            always_ff @ (posedge clk or posedge rst)
                if (rst)
                begin
                    hex_o <= {digits {blank}};
                    dp_o  <= {digits {dp_blank}};
                end
                else
                    for (int d = 0; d < digits; d ++)
                        if (digit [d])
                        begin
                            hex_o [d * segs +: segs] <= lit;
                            dp_o  [d]                <= dp_lit;
                        end

        end else begin : g_combinational

            always_comb
                for (int d = 0; d < digits; d ++)
                begin
                    hex_o [d * segs +: segs] = digit [d] ? lit : blank;
                    dp_o  [d]                = digit [d] ? dp_lit : dp_blank;
                end

        end
    endgenerate

endmodule
