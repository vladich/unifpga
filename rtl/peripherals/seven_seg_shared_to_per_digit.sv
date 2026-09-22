// =============================================================================
// seven_seg_shared_to_per_digit
//
// Adapter for boards whose 7-segment display has independent per-digit segment
// lines (Terasic DE0 / DE1 / DE2 / DE2-115 / DE0-CV / DE10-Lite / DE1-SoC,
// Cyclone V GX starter: `HEX0[7:0]`, `HEX1[7:0]`, ...). The capability presents
// the shared-segments + digit-select model to user code; this adapter fans the
// currently selected digit's segment pattern out to that digit's own pins.
//
// Bit order (matches basics-graphics-music and config/capabilities/seven_segment.yml):
//   abcdefgh[7] = a, [6] = b, ..., [1] = g, [0] = h (decimal point)
// Board HEX buses are wired the other way round, `HEXn[0] = a ... HEXn[6] = g,
// HEXn[7] = dp` (Terasic), so the pattern is bit-reversed here, exactly like
// BGM's `hgfedcba` loop in de10_lite/board_specific_top.sv.
//
// Parameters
//   digits      number of digit positions (HEX0..HEX<digits-1>)
//   segs        pins per digit: 8 (with decimal point) or 7 (DE0-CV has no dp)
//   active      "low" for common-anode displays (all the Terasic boards),
//               "high" otherwise; same vocabulary as the peripheral YAML
//   latched     0: combinational fan-out, only the selected digit is lit at any
//                  instant (BGM default);
//               1: each digit keeps the last pattern written while it was
//                  selected (BGM's "EMULATE_DYNAMIC_7SEG_ON_STATIC_WITHOUT_STICKY
//                  _FLOPS" variant), so all digits appear lit at once.
//
// hex_o is flat: bits [d*segs +: segs] belong to digit d, LSB = segment a.
// =============================================================================

module seven_seg_shared_to_per_digit
# (
    parameter int    digits  = 6,
    parameter int    segs    = 8,
    parameter        active  = "low",     // "low" / "high" (untyped: yosys 0.41 rejects `parameter string`)
    parameter bit    latched = 1'b0
)
(
    input                              clk,
    input                              rst,
    input        [7:0]                 abcdefgh,
    input        [digits - 1:0]        digit,
    output logic [digits * segs - 1:0] hex_o
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

    generate
        if (latched) begin : g_latched

            always_ff @ (posedge clk)
                if (rst)
                    hex_o <= {digits {blank}};
                else
                    for (int d = 0; d < digits; d ++)
                        if (digit [d])
                            hex_o [d * segs +: segs] <= lit;

        end else begin : g_combinational

            always_comb
                for (int d = 0; d < digits; d ++)
                    hex_o [d * segs +: segs] = digit [d] ? lit : blank;

        end
    endgenerate

endmodule
