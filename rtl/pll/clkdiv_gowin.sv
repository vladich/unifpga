// =============================================================================
// clkdiv_gowin — divide a PLL clock with the Gowin CLKDIV / CLKDIV2 clock
// dividers (no fabric logic on the clock path). Codegen instantiates it for a
// peripheral clock declared as `{name: pixel, from: serial, divide: N}` on a
// Gowin board, exactly as BGM's tang_primer_20k_dock_hdmi_tm1638_yosys does:
//
//     CLKDIV2 div_2 (.HCLKIN(serial_clk), .CLKOUT(pixel_clk_div2), .RESETN(pll_lock));
//     CLKDIV #(.DIV_MODE("5")) div_5 (.HCLKIN(pixel_clk_div2), .CLKOUT(pixel_clk), .RESETN(pll_lock));
//
// Supported DIV: 2 (CLKDIV2), 4, 5, 8 (CLKDIV), 10 (CLKDIV2 + CLKDIV 5).
// DIV_MODE "8" exists on GW2A/GW5 only; codegen never asks for it on GW1N.
// =============================================================================

module clkdiv_gowin
# (
    parameter int DIV = 10
)
(
    input  clk_in,
    input  resetn,       // PLL lock
    output clk_out
);

    generate
        if (DIV == 2) begin : g_div2

            CLKDIV2 u_div2 (.HCLKIN (clk_in), .CLKOUT (clk_out), .RESETN (resetn));

        end else if (DIV == 4 || DIV == 5 || DIV == 8) begin : g_div

            localparam MODE = (DIV == 4) ? "4" : (DIV == 5) ? "5" : "8";

            CLKDIV # (.DIV_MODE (MODE), .GSREN ("false"))
                u_div (.HCLKIN (clk_in), .CLKOUT (clk_out), .RESETN (resetn), .CALIB (1'b0));

        end else if (DIV == 10) begin : g_div10

            wire clk_div2;

            CLKDIV2 u_div2 (.HCLKIN (clk_in), .CLKOUT (clk_div2), .RESETN (resetn));

            CLKDIV # (.DIV_MODE ("5"), .GSREN ("false"))
                u_div5 (.HCLKIN (clk_div2), .CLKOUT (clk_out), .RESETN (resetn), .CALIB (1'b0));

        end else begin : g_unsupported

            initial $error ("clkdiv_gowin: DIV=%0d is not a CLKDIV/CLKDIV2 combination", DIV);

        end
    endgenerate

endmodule
